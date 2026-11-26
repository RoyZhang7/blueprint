// SPDX-License-Identifier: GPL-2.0
// Phase K1: libbpf (BTF-typed tracepoint) version of the probe's "full" program (k1/progs/full_both.bt.tmpl): the same
// accounting -- per-CPU occupancy by holder class on the cpuset and on its SMT siblings, run-queue delay of the
// service's threads split by holder time share (last-holder for migrated resumes), the waker's class and the
// sleep time per class, pid -> cgroup learned at switch-out, per-thread R, log2 histogram of runnable gaps, entries
// dropped at process exit. Per-window counters live in a per-CPU array that userspace (k1probe.c) reads and zeroes
// every 100 ms. No vmlinux.h: minimal CO-RE struct definitions, relocated against the kernel's BTF at load time.
// Differences from the bpftrace program, by construction of libbpf: one per-pid struct instead of eight pid-keyed maps,
// a CPU-indexed array instead of CPU-keyed hashes, per-CPU window counters instead of shared ones, cpuset and sibling
// filters in one program (bit masks) instead of two.
#include <linux/types.h>
#include <linux/bpf.h>
#include <bpf/bpf_helpers.h>
#include <bpf/bpf_tracing.h>
#include <bpf/bpf_core_read.h>

typedef __u64 u64;
typedef __u32 u32;
typedef _Bool bool;

struct thread_info { u32 cpu; } __attribute__((preserve_access_index));
struct task_struct { struct thread_info thread_info; int pid; } __attribute__((preserve_access_index));

struct cfg { u64 svc, aggr, root, cpumask, sibmask; };
const volatile struct cfg cfg = {};

struct ps { u64 cg, runnable, snap1, snap2, snap3, sleep, rthr; u32 rcpu, pad; };
struct cs { u64 lastsw, lastsws; u64 occ[4]; u32 holder, nholders, curcls, pad; };
struct win { u64 R[4], RL[4], Rn[4], occw[5], occs[5], Wo[5], Won[5], Qidle, Qidlen, preempt, multi, mig; };

struct { __uint(type, BPF_MAP_TYPE_HASH); __uint(max_entries, 1 << 20); __uint(map_flags, BPF_F_NO_PREALLOC); __type(key, u32); __type(value, struct ps); } pst SEC(".maps");
struct { __uint(type, BPF_MAP_TYPE_ARRAY); __uint(max_entries, 64); __type(key, u32); __type(value, struct cs); } cpus SEC(".maps");
struct { __uint(type, BPF_MAP_TYPE_PERCPU_ARRAY); __uint(max_entries, 1); __type(key, u32); __type(value, struct win); } winm SEC(".maps");
struct { __uint(type, BPF_MAP_TYPE_PERCPU_ARRAY); __uint(max_entries, 64); __type(key, u32); __type(value, u64); } hist SEC(".maps");

static __always_inline u32 cls(u64 cg)
{
	if (cg == cfg.svc) return 0;
	if (cg == cfg.aggr) return 1;
	if (cg == cfg.root) return 2;
	return 3;
}

static __always_inline u32 log2u(u64 v)
{
	u32 r = 0;
#pragma unroll
	for (int i = 32; i > 0; i >>= 1) if (v >= (1ULL << i)) { v >>= i; r += i; }
	return r;
}

SEC("tp_btf/sched_switch")
int BPF_PROG(on_switch, bool preempt, struct task_struct *prev, struct task_struct *next, unsigned int prev_state)
{
	u32 cpu = bpf_get_smp_processor_id(), z = 0;
	if (cpu >= 64) return 0;
	u64 bit = 1ULL << cpu;
	if (!((cfg.cpumask | cfg.sibmask) & bit)) return 0;
	u64 now = bpf_ktime_get_ns();
	struct cs *c = bpf_map_lookup_elem(&cpus, &cpu);
	struct win *w = bpf_map_lookup_elem(&winm, &z);
	if (!c || !w) return 0;
	u32 pp = prev->pid, np = next->pid;
	u64 pcg = bpf_get_current_cgroup_id();            // current == prev here
	if (cfg.sibmask & bit) {                           // SMT sibling of the cpuset: occupancy only
		if (c->lastsws) {
			u64 dt = now - c->lastsws;
			if (pp == 0) w->occs[4] += dt; else w->occs[cls(pcg) & 3] += dt;
		}
		c->lastsws = now;
		return 0;
	}
	if (c->lastsw) {
		u64 dt = now - c->lastsw;
		if (pp == 0) w->occw[4] += dt;
		else { u32 k = cls(pcg) & 3; c->occ[k] += dt; w->occw[k] += dt; }
	}
	c->lastsw = now;
	struct ps *p = bpf_map_lookup_elem(&pst, &pp);
	if (!p) {
		struct ps zp = {}; zp.cg = pcg;
		bpf_map_update_elem(&pst, &pp, &zp, BPF_ANY);
		p = bpf_map_lookup_elem(&pst, &pp);
	} else p->cg = pcg;
	struct ps *n = bpf_map_lookup_elem(&pst, &np);
	u64 ncg = n ? n->cg : 0;
	if (n && ncg == cfg.svc) {
		if (n->runnable) {
			u64 d = now - n->runnable;
			u32 h = c->holder & 3;
			if (n->rcpu == cpu + 1) {
				long a = (long)(c->occ[1] - n->snap1), r = (long)(c->occ[2] - n->snap2), o = (long)(c->occ[3] - n->snap3);
				if (a < 0) a = 0; if (r < 0) r = 0; if (o < 0) o = 0;
				long s = (long)d - a - r - o; if (s < 0) s = 0;
				w->R[0] += s; w->R[1] += a; w->R[2] += r; w->R[3] += o;
			} else { w->R[h] += d; w->mig += 1; }
			w->RL[h] += d; w->Rn[h] += 1;
			n->rthr += d;
			u32 b = log2u(d) & 63; u64 *hb = bpf_map_lookup_elem(&hist, &b); if (hb) *hb += 1;
			if (c->nholders > 1) w->multi += 1;
			n->runnable = 0; n->rcpu = 0;
		}
		c->holder = 0; c->nholders = 0; c->curcls = 0;
	} else {
		u32 k = (ncg == cfg.aggr) ? 1 : (ncg == cfg.root) ? 2 : 3;
		c->holder = k; c->curcls = k; c->nholders += 1;
	}
	if (p && pcg == cfg.svc) {
		if (preempt || prev_state == 0) {
			p->runnable = now; p->rcpu = cpu + 1; p->snap1 = c->occ[1]; p->snap2 = c->occ[2]; p->snap3 = c->occ[3]; w->preempt += 1;
		} else p->sleep = now;
	}
	return 0;
}

static __always_inline int do_wakeup(struct task_struct *t)
{
	u32 pid = t->pid, z = 0;
	struct ps *s = bpf_map_lookup_elem(&pst, &pid);
	if (!s || !s->sleep) return 0;
	struct win *w = bpf_map_lookup_elem(&winm, &z);
	if (!w) return 0;
	u64 now = bpf_ktime_get_ns(), d = now - s->sleep;
	u32 wk = ((bpf_get_current_pid_tgid() & 0xffffffff) == 0) ? 4 : cls(bpf_get_current_cgroup_id());
	if (wk > 4) wk = 4;
	w->Qidle += d; w->Qidlen += 1; w->Wo[wk] += d; w->Won[wk] += 1;
	s->sleep = 0;
	u32 tc = t->thread_info.cpu;
	if (tc >= 64) return 0;
	struct cs *c = bpf_map_lookup_elem(&cpus, &tc);
	if (!c) return 0;
	u64 el = c->lastsw ? now - c->lastsw : 0;
	s->runnable = now; s->rcpu = tc + 1;
	s->snap1 = c->occ[1] + (c->curcls == 1 ? el : 0);
	s->snap2 = c->occ[2] + (c->curcls == 2 ? el : 0);
	s->snap3 = c->occ[3] + (c->curcls == 3 ? el : 0);
	return 0;
}

SEC("tp_btf/sched_wakeup")
int BPF_PROG(on_wakeup, struct task_struct *p) { return do_wakeup(p); }

SEC("tp_btf/sched_wakeup_new")
int BPF_PROG(on_wakeup_new, struct task_struct *p) { return do_wakeup(p); }

SEC("tp_btf/sched_process_exit")
int BPF_PROG(on_exit, struct task_struct *p)
{
	u32 pid = p->pid;
	bpf_map_delete_elem(&pst, &pid);
	return 0;
}

char LICENSE[] SEC("license") = "GPL";
