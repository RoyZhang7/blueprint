// SPDX-License-Identifier: GPL-2.0
// Phase K1 addendum: the lean libbpf probe (user spec, Nov 13).
//   - sched_switch is the only program by default; the wake-up programs are in this object but loaded only with
//     `k1lean --progs switch,wakeup`.
//   - Every program returns on a CPU outside the victim's cpuset before any map access (the wake-up programs test the
//     woken task's target CPU, then its cgroup).
//   - Default map sizes: per-thread state only for the service's threads, in a 4096-entry hash; CPU-indexed arrays.
//   - No pid -> cgroup map: the next task's cgroup id is read from task->cgroups->dfl_cgrp->kn->id (BTF, CO-RE).
//   - No SMT-sibling occupancy (those CPUs are outside the cpuset).
// Run-queue delay of a service thread, split by holder: after a preemption, by the holders' time share on the CPU
// (snapshots at switch-out, as k1probe.bpf.c); after a wake-up, with the wake-up program on, the same from snapshots
// at the wake-up; without it, the kernel's own task->sched_info.run_delay is read at the thread's next switch-out and
// the wait is credited to the class that held the CPU when the thread got it (last-holder credit; Rrd/Rrdn count it).
#include <linux/types.h>
#include <linux/bpf.h>
#include <bpf/bpf_helpers.h>
#include <bpf/bpf_tracing.h>
#include <bpf/bpf_core_read.h>

typedef __u64 u64;
typedef __u32 u32;
typedef _Bool bool;

struct kernfs_node { u64 id; } __attribute__((preserve_access_index));
struct cgroup { struct kernfs_node *kn; } __attribute__((preserve_access_index));
struct css_set { struct cgroup *dfl_cgrp; } __attribute__((preserve_access_index));
struct sched_info { unsigned long long run_delay; } __attribute__((preserve_access_index));
struct thread_info { u32 cpu; } __attribute__((preserve_access_index));
struct task_struct {
	struct thread_info thread_info;
	int pid;
	struct css_set *cgroups;
	struct sched_info sched_info;
} __attribute__((preserve_access_index));

struct cfg { u64 svc, aggr, root, cpumask; };
const volatile struct cfg cfg = {};

struct ts { u64 runnable, snap1, snap2, snap3, sleep, rd, rthr; u32 rcpu, hin, pend, seen; };
struct cs { u64 lastsw; u64 occ[4]; u32 holder, nholders, curcls, pad; };
struct win { u64 R[4], RL[4], Rn[4], occw[5], Wo[5], Won[5], Qidle, Qidlen, preempt, multi, mig, Rrd, Rrdn; };

struct { __uint(type, BPF_MAP_TYPE_HASH); __uint(max_entries, 4096); __type(key, u32); __type(value, struct ts); } tst SEC(".maps");
struct { __uint(type, BPF_MAP_TYPE_ARRAY); __uint(max_entries, 64); __type(key, u32); __type(value, struct cs); } cpus SEC(".maps");
struct { __uint(type, BPF_MAP_TYPE_PERCPU_ARRAY); __uint(max_entries, 1); __type(key, u32); __type(value, struct win); } winm SEC(".maps");
struct { __uint(type, BPF_MAP_TYPE_PERCPU_ARRAY); __uint(max_entries, 64); __type(key, u32); __type(value, u64); } hist SEC(".maps");

#ifdef USE_CORE_READ
#define TASK_CG(t) BPF_CORE_READ((t), cgroups, dfl_cgrp, kn, id)
#define RUN_DELAY(t) BPF_CORE_READ((t), sched_info.run_delay)
#else
#define TASK_CG(t) ((t)->cgroups->dfl_cgrp->kn->id)
#define RUN_DELAY(t) ((t)->sched_info.run_delay)
#endif

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

static __always_inline void add_hist(u64 d)
{
	u32 b = log2u(d) & 63;
	u64 *hb = bpf_map_lookup_elem(&hist, &b);
	if (hb) *hb += 1;
}

SEC("tp_btf/sched_switch")
int BPF_PROG(lean_switch, bool preempt, struct task_struct *prev, struct task_struct *next, unsigned int prev_state)
{
	u32 cpu = bpf_get_smp_processor_id();
	if (cpu >= 64 || !(cfg.cpumask & (1ULL << cpu))) return 0;   // before any map access
	u32 z = 0;
	struct cs *c = bpf_map_lookup_elem(&cpus, &cpu);
	struct win *w = bpf_map_lookup_elem(&winm, &z);
	if (!c || !w) return 0;
	u64 now = bpf_ktime_get_ns();
	u32 pp = prev->pid, np = next->pid;
	u64 pcg = bpf_get_current_cgroup_id();                       // current == prev here
	if (c->lastsw) {
		u64 dt = now - c->lastsw;
		if (pp == 0) w->occw[4] += dt;
		else { u32 k = cls(pcg) & 3; c->occ[k] += dt; w->occw[k] += dt; }
	}
	c->lastsw = now;
	u64 ncg = np ? TASK_CG(next) : 0;
	if (np && ncg == cfg.svc) {                                  // a service thread gets the CPU
		struct ts *n = bpf_map_lookup_elem(&tst, &np);
		u32 h = c->holder & 3;
		if (n) {
			if (n->runnable) {                                   // start of the wait seen: time-share split
				u64 d = now - n->runnable;
				if (n->rcpu == cpu + 1) {
					long a = (long)(c->occ[1] - n->snap1), r = (long)(c->occ[2] - n->snap2), o = (long)(c->occ[3] - n->snap3);
					if (a < 0) a = 0; if (r < 0) r = 0; if (o < 0) o = 0;
					long s = (long)d - a - r - o; if (s < 0) s = 0;
					w->R[0] += s; w->R[1] += a; w->R[2] += r; w->R[3] += o;
				} else { w->R[h] += d; w->mig += 1; }
				w->RL[h] += d; w->Rn[h] += 1;
				n->rthr += d;
				add_hist(d);
				if (c->nholders > 1) w->multi += 1;
				n->runnable = 0; n->rcpu = 0; n->pend = 0;
			} else {                                             // woken without the wake-up program: resolve at switch-out
				n->pend = 1; n->hin = h;
			}
		}
		c->holder = 0; c->nholders = 0; c->curcls = 0;
	} else {
		u32 k = (np && ncg == cfg.aggr) ? 1 : (np && ncg == cfg.root) ? 2 : 3;
		c->holder = k; c->curcls = k; c->nholders += 1;
	}
	if (pp && pcg == cfg.svc) {                                  // a service thread leaves the CPU
		struct ts *p = bpf_map_lookup_elem(&tst, &pp);
		if (!p) {
			struct ts zp = {};
			bpf_map_update_elem(&tst, &pp, &zp, BPF_NOEXIST);
			p = bpf_map_lookup_elem(&tst, &pp);
		}
		if (p) {
			u64 rd = RUN_DELAY(prev);
			if (p->pend && p->seen && rd >= p->rd) {             // the wait before this run, from sched_info
				u64 d = rd - p->rd;
				u32 h = p->hin & 3;
				w->R[h] += d; w->RL[h] += d; w->Rn[h] += 1; w->Rrd += d; w->Rrdn += 1;
				p->rthr += d;
				add_hist(d);
			}
			p->pend = 0; p->rd = rd; p->seen = 1;
			if (preempt || prev_state == 0) {
				p->runnable = now; p->rcpu = cpu + 1; p->snap1 = c->occ[1]; p->snap2 = c->occ[2]; p->snap3 = c->occ[3];
				w->preempt += 1;
			} else p->sleep = now;
		}
	}
	return 0;
}

static __always_inline int do_wakeup(struct task_struct *t)
{
	u32 tc = t->thread_info.cpu;
	if (tc >= 64 || !(cfg.cpumask & (1ULL << tc))) return 0;      // before any map access
	if (TASK_CG(t) != cfg.svc) return 0;
	u32 pid = t->pid, z = 0;
	struct ts *s = bpf_map_lookup_elem(&tst, &pid);
	if (!s || !s->sleep) return 0;
	struct win *w = bpf_map_lookup_elem(&winm, &z);
	struct cs *c = bpf_map_lookup_elem(&cpus, &tc);
	if (!w || !c) return 0;
	u64 now = bpf_ktime_get_ns(), d = now - s->sleep;
	u32 wk = ((bpf_get_current_pid_tgid() & 0xffffffff) == 0) ? 4 : cls(bpf_get_current_cgroup_id());
	if (wk > 4) wk = 4;
	w->Qidle += d; w->Qidlen += 1; w->Wo[wk] += d; w->Won[wk] += 1;
	s->sleep = 0;
	u64 el = c->lastsw ? now - c->lastsw : 0;
	s->runnable = now; s->rcpu = tc + 1;
	s->snap1 = c->occ[1] + (c->curcls == 1 ? el : 0);
	s->snap2 = c->occ[2] + (c->curcls == 2 ? el : 0);
	s->snap3 = c->occ[3] + (c->curcls == 3 ? el : 0);
	return 0;
}

SEC("tp_btf/sched_wakeup")
int BPF_PROG(lean_wakeup, struct task_struct *p) { return do_wakeup(p); }

SEC("tp_btf/sched_wakeup_new")
int BPF_PROG(lean_wakeup_new, struct task_struct *p) { return do_wakeup(p); }

char LICENSE[] SEC("license") = "GPL";
