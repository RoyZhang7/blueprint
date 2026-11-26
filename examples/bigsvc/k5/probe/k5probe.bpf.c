// SPDX-License-Identifier: GPL-2.0
// Phase K5 (RESULTS-K 5, item A1): the lean libbpf probe of the K1 addendum (k1/libbpf/k1lean.bpf.c), generalised from one
// service cgroup to a list of configured cgroups and extended with optional programs, each behind its own flag. The
// default flags (F_RQ only, switch program only) do the lean probe's work: holder occupancy of the victims' CPUs and the
// run-queue wait of their threads split by holder.
//   F_RQ       run-queue wait of the victims' threads split by holder: time-share split after a preemption on the same
//              CPU, last-holder credit otherwise; after a wake-up the wait comes from task->sched_info.run_delay at the
//              thread's next switch-out (exact start with F_WAKE)
//   F_SIB      phi_sibling: a victim thread's on-CPU time split by what holds the SMT sibling. The sibling CPUs' switches
//              are traced for their holder only; each pair interval is charged once, by the CPU whose switch ends it
//   F_WAKE     sched_wakeup / sched_wakeup_new: the waker class of the sleep that ended, and the wait's exact start
//   F_SOFTIRQ  softirq_entry / softirq_exit on the victims' CPUs: ns by vector, and the share while a victim's task was
//              current (the softirq ran on its time)
//   F_TIMER    a 100 ms bpf_timer (armed by the k5_arm syscall program on the agent's CPU, pinned there) folds the
//              per-CPU maps into one record per victim cgroup and pushes it to a ring buffer
// Holder classes: configured cgroup slots 0..NCG-1 (victims first), root (kernel threads), other (any cgroup not
// configured), idle. Every counter is cumulative; readers take differences, so nothing is reset under a writer.
// Every program returns before any map access on a CPU it does not watch.
#include <linux/types.h>
#include <linux/bpf.h>
#include <bpf/bpf_helpers.h>
#include <bpf/bpf_tracing.h>
#include <bpf/bpf_core_read.h>

typedef __u64 u64;
typedef __u32 u32;
typedef _Bool bool;

#include "k5probe.h"
#ifndef BPF_F_TIMER_CPU_PIN
#define BPF_F_TIMER_CPU_PIN (1ULL << 1)
#endif

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

const volatile struct cfg cfg SEC(".rodata.cfg") = {};   // set by the agent before load

struct tmv { struct bpf_timer t; };

struct { __uint(type, BPF_MAP_TYPE_HASH); __uint(max_entries, 4096); __type(key, u32); __type(value, struct ts); } tst SEC(".maps");
struct { __uint(type, BPF_MAP_TYPE_ARRAY); __uint(max_entries, MAXCPU); __type(key, u32); __type(value, struct cs); } cpus SEC(".maps");
struct { __uint(type, BPF_MAP_TYPE_ARRAY); __uint(max_entries, MAXCPU); __type(key, u32); __type(value, u32); } sibs SEC(".maps");
struct { __uint(type, BPF_MAP_TYPE_PERCPU_ARRAY); __uint(max_entries, 1); __type(key, u32); __type(value, struct win); } winm SEC(".maps");
struct { __uint(type, BPF_MAP_TYPE_ARRAY); __uint(max_entries, 1); __type(key, u32); __type(value, struct tmv); } tmr SEC(".maps");
struct { __uint(type, BPF_MAP_TYPE_RINGBUF); __uint(max_entries, 256 * 1024); } rb SEC(".maps");
struct { __uint(type, BPF_MAP_TYPE_ARRAY); __uint(max_entries, NV); __type(key, u32); __type(value, struct rec); } prevr SEC(".maps");
struct { __uint(type, BPF_MAP_TYPE_PERCPU_ARRAY); __uint(max_entries, 1); __type(key, u32); __type(value, struct rec); } scr SEC(".maps");
struct { __uint(type, BPF_MAP_TYPE_PERCPU_ARRAY); __uint(max_entries, 1); __type(key, u32); __type(value, struct pcs); } pcsm SEC(".maps");   // F_CGF: per-CPU on-CPU / wait / softirq

// F_RD: each victim thread's sched_info.run_delay when it last left a CPU
struct rdv { u64 rd; u64 seen; };
struct { __uint(type, BPF_MAP_TYPE_TASK_STORAGE); __uint(map_flags, BPF_F_NO_PREALLOC); __type(key, int); __type(value, struct rdv); } rdst SEC(".maps");

struct gstate gs SEC(".data.gs") = {};                    // tick sequence, ring-buffer misses, timer armed: read by the agent

#ifdef USE_CORE_READ
#define TASK_CG(t) BPF_CORE_READ((t), cgroups, dfl_cgrp, kn, id)
#define RUN_DELAY(t) BPF_CORE_READ((t), sched_info.run_delay)
#else
#define TASK_CG(t) ((t)->cgroups->dfl_cgrp->kn->id)
#define RUN_DELAY(t) ((t)->sched_info.run_delay)
#endif

static __always_inline u32 cls(u64 cg)
{
	if (cg == cfg.root) return H_ROOT;
#pragma unroll
	for (int i = 0; i < NID; i++) {
		if (i >= cfg.nid) break;
		if (cg == cfg.cgid[i]) return cfg.cgslot[i] < NCG ? cfg.cgslot[i] : H_OTHER;
	}
	return H_OTHER;
}

// Phase J1 (F_CGF): the switch program for unpinned victims. Every CPU is traced; the two cgroup reads come first and the
// program returns before any map access unless prev or next is a victim (row 1). With F_HOLD (row 2) every switch also
// updates the CPU's holder state (occupancy by holder, the sibling's holder for phi_sibling) as the pinned program does.
// The wait before a run is sched_info.run_delay's growth since the thread last left a CPU, credited to the class of the task
// that left when the thread arrived ("the task running on the CPU while we were runnable there"); on-CPU time per CPU and
// the wait per CPU go to pcsm, which the agent reads each tick.
static __always_inline int cgf_switch(u32 cpu, bool preempt, struct task_struct *prev, struct task_struct *next, unsigned int prev_state)
{
	u32 pp = prev->pid, np = next->pid;
	u32 pc = pp ? cls(bpf_get_current_cgroup_id()) : H_IDLE;
	u32 nc = np ? cls(TASK_CG(next)) : H_IDLE;
	bool vp = pc < cfg.nv && pc < NV, vn = nc < cfg.nv && nc < NV;
	if (!vp && !vn && !(cfg.flags & F_HOLD)) return 0;             // nothing of ours on this switch: no map access
	u32 z = 0;
	struct cs *c = bpf_map_lookup_elem(&cpus, &cpu);
	struct win *w = bpf_map_lookup_elem(&winm, &z);
	struct pcs *q = bpf_map_lookup_elem(&pcsm, &z);
	if (!c || !w || !q) return 0;
	u64 now = bpf_ktime_get_ns();
	if (cfg.flags & F_HOLD) {
		u64 last = c->lastsw;
		if (cfg.flags & F_SIB) {
			u32 *sp = bpf_map_lookup_elem(&sibs, &cpu);
			if (sp && *sp < MAXCPU) {
				u32 s = *sp;
				struct cs *o = bpf_map_lookup_elem(&cpus, &s);
				if (o && last && o->lastsw) {
					u64 ol = o->lastsw, t0 = last > ol ? last : ol;
					u32 oc = o->cur;
					if (now > t0 && oc < NH && pc < NH) {
						u64 ov = now - t0;
						if (pc < cfg.nv && pc < NV) w->sibm[pc][oc] += ov;   // our thread here, the sibling held by oc
						if (oc < cfg.nv && oc < NV) w->sibo[oc][pc] += ov;   // our thread on the sibling, this CPU held by pc
					}
				}
			}
		}
		if (last && pc < NH) { u64 dt = now - last; c->occ[pc] += dt; w->occ[pc] += dt; }
		c->lastsw = now;
		c->cur = nc;
	}
	if (vp) {                                                      // a victim thread leaves this CPU
		w->sw[pc] += 1;
		bool rb = preempt || prev_state == 0;
		if (rb) w->pre[pc] += 1;
		struct ts *p = bpf_map_lookup_elem(&tst, &pp);
		if (!p) {
			struct ts zp = {};
			bpf_map_update_elem(&tst, &pp, &zp, BPF_NOEXIST);
			p = bpf_map_lookup_elem(&tst, &pp);
		}
		if (p) {
			u64 rd = RUN_DELAY(prev);
			if (p->seen && rd >= p->rd) {                          // the wait before this run, credited to the last holder
				u64 d = rd - p->rd; u32 h = p->hin;
				if (h < NH) w->R[pc][h] += d;
				w->Rn[pc] += 1; w->Rrd[pc] += d; w->Rrdn[pc] += 1;
				if (pc < NV) q->wait[pc] += d;
			}
			p->rd = rd; p->seen = 1;
			if (p->tin && now > p->tin && pc < NV) q->on[pc] += now - p->tin;
			p->tin = 0;
			if (rb) { p->runnable = now; p->rcpu = cpu + 1; p->sleep = 0; } else { p->sleep = now; p->runnable = 0; }
		}
	}
	if (vn) {                                                      // a victim thread gets this CPU
		struct ts *n = bpf_map_lookup_elem(&tst, &np);
		if (!n) {
			struct ts zn = {};
			bpf_map_update_elem(&tst, &np, &zn, BPF_NOEXIST);
			n = bpf_map_lookup_elem(&tst, &np);
		}
		if (n) {
			if (n->rcpu && n->rcpu != cpu + 1) w->mig[nc] += 1;
			n->tin = now; n->hin = pc; n->runnable = 0; n->rcpu = 0; n->pend = 0;
		}
	}
	return 0;
}

SEC("tp_btf/sched_switch")
int BPF_PROG(k5_switch, bool preempt, struct task_struct *prev, struct task_struct *next, unsigned int prev_state)
{
	u32 cpu = bpf_get_smp_processor_id();
	if (cpu >= MAXCPU) return 0;
	if (cfg.flags & F_CGF) return cgf_switch(cpu, preempt, prev, next, prev_state);
	bool inm = BIT_TEST(cfg.mask, cpu);
	bool ins = (cfg.flags & F_SIB) && BIT_TEST(cfg.sibmask, cpu);
	if (!inm && !ins) return 0;                                  // before any map access
	u32 z = 0;
	struct cs *c = bpf_map_lookup_elem(&cpus, &cpu);
	struct win *w = bpf_map_lookup_elem(&winm, &z);
	if (!c || !w) return 0;
	u64 now = bpf_ktime_get_ns();
	u32 pp = prev->pid, np = next->pid;
	u32 pc = pp ? cls(bpf_get_current_cgroup_id()) : H_IDLE;   // current == prev here
	u32 nc = H_IDLE;
	if (np && (cfg.flags & (F_RQ | F_SIB | F_WAKE))) nc = cls(TASK_CG(next));
	u64 last = c->lastsw;
	if (cfg.flags & F_SIB) {
		u32 *sp = bpf_map_lookup_elem(&sibs, &cpu);
		if (sp && *sp < MAXCPU) {
			u32 s = *sp;
			struct cs *o = bpf_map_lookup_elem(&cpus, &s);
			if (o && last && o->lastsw) {
				u64 ol = o->lastsw, t0 = last > ol ? last : ol;
				u32 oc = o->cur;
				if (now > t0 && oc < NH && pc < NH) {
					u64 ov = now - t0;
					if (pc < cfg.nv && pc < NV) w->sibm[pc][oc] += ov;   // our thread here, the sibling held by oc
					if (oc < cfg.nv && oc < NV) w->sibo[oc][pc] += ov;   // our thread on the sibling, this CPU held by pc
				}
			}
		}
	}
	if (inm && last && pc < NH) {
		u64 dt = now - last;
		c->occ[pc] += dt; w->occ[pc] += dt;
	}
	c->lastsw = now;
	c->cur = nc;
	if (!inm) return 0;                                          // a sibling CPU: its holder is all we keep
	if (pp && pc < cfg.nv && pc < NV) {
		w->sw[pc] += 1;
		if (preempt || prev_state == 0) w->pre[pc] += 1;
	}
	if ((cfg.flags & F_RD) && pp && pc < cfg.nv && pc < NV) {
		// F_RD: the run_delay the thread accumulated since it last left a CPU (the kernel adds each wait when the thread
		// arrives), from its own task storage: no per-thread hash map, no holder split
		struct rdv *v = bpf_task_storage_get(&rdst, prev, 0, BPF_LOCAL_STORAGE_GET_F_CREATE);
		if (v) {
			u64 rd = RUN_DELAY(prev);
			if (v->seen && rd >= v->rd) {
				w->Rrd[pc] += rd - v->rd;
				w->Rrdn[pc] += 1;
			}
			v->rd = rd;
			v->seen = 1;
		}
	}
	if (!(cfg.flags & F_RQ)) return 0;
	if (np && nc < cfg.nv && nc < NV) {                          // a victim thread gets the CPU
		struct ts *n = bpf_map_lookup_elem(&tst, &np);
		if (n) {
			if (n->runnable) {
				u64 d = now - n->runnable;
				if (n->rcpu == cpu + 1) {                        // waited here: split by the holders' time share
					long f = 0;
#pragma unroll
					for (int h = 0; h < NH; h++) {
						if (h == nc) continue;
						long x = (long)(c->occ[h] - n->snap[h]);
						if (x < 0) x = 0;
						w->R[nc][h] += x; f += x;
					}
					long s = (long)d - f;
					if (s < 0) s = 0;
					w->R[nc][nc] += s;
				} else {
					if (pc < NH) w->R[nc][pc] += d;
					w->mig[nc] += 1;
				}
				w->Rn[nc] += 1;
				n->runnable = 0; n->rcpu = 0; n->pend = 0;
			} else {                                             // woken without the wake-up program: at switch-out
				n->pend = 1; n->hin = pc;
			}
		}
	}
	if (pp && pc < cfg.nv && pc < NV) {                          // a victim thread leaves the CPU
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
				u32 h = p->hin;
				if (h < NH) w->R[pc][h] += d;
				w->Rn[pc] += 1;
				if (!(cfg.flags & F_RD)) { w->Rrd[pc] += d; w->Rrdn[pc] += 1; }
			}
			p->pend = 0; p->rd = rd; p->seen = 1;
			if (preempt || prev_state == 0) {
				p->runnable = now; p->rcpu = cpu + 1;
#pragma unroll
				for (int h = 0; h < NH; h++) p->snap[h] = c->occ[h];
			} else p->sleep = now;
		}
	}
	return 0;
}

static __always_inline int do_wakeup(struct task_struct *t)
{
	u32 tc = t->thread_info.cpu;
	if (tc >= MAXCPU || !BIT_TEST(cfg.mask, tc)) return 0;      // before any map access
	u32 v = cls(TASK_CG(t));
	if (v >= cfg.nv || v >= NV) return 0;
	u32 pid = t->pid, z = 0;
	struct ts *s = bpf_map_lookup_elem(&tst, &pid);
	if (!s || !s->sleep) return 0;
	struct win *w = bpf_map_lookup_elem(&winm, &z);
	struct cs *c = bpf_map_lookup_elem(&cpus, &tc);
	if (!w || !c) return 0;
	u64 now = bpf_ktime_get_ns(), d = now - s->sleep;
	u32 wk = ((bpf_get_current_pid_tgid() & 0xffffffff) == 0) ? H_IDLE : cls(bpf_get_current_cgroup_id());
	if (wk < NH) w->Wo[v][wk] += d;
	w->wk[v] += 1;
	s->sleep = 0;
	u64 el = c->lastsw ? now - c->lastsw : 0;
	u32 cc = c->cur;
	s->runnable = now; s->rcpu = tc + 1;
#pragma unroll
	for (int h = 0; h < NH; h++) s->snap[h] = c->occ[h] + (cc == h ? el : 0);
	return 0;
}

SEC("tp_btf/sched_wakeup")
int BPF_PROG(k5_wakeup, struct task_struct *p) { return do_wakeup(p); }

SEC("tp_btf/sched_wakeup_new")
int BPF_PROG(k5_wakeup_new, struct task_struct *p) { return do_wakeup(p); }

SEC("tp_btf/softirq_entry")
int BPF_PROG(k5_sirq_entry, unsigned int vec)
{
	u32 cpu = bpf_get_smp_processor_id();
	if (cpu >= MAXCPU || !BIT_TEST(cfg.mask, cpu)) return 0;   // before any map access
	struct cs *c = bpf_map_lookup_elem(&cpus, &cpu);
	if (!c) return 0;
	c->sirq_t0 = bpf_ktime_get_ns(); c->sirq_vec = vec;
	return 0;
}

SEC("tp_btf/softirq_exit")
int BPF_PROG(k5_sirq_exit, unsigned int vec)
{
	u32 cpu = bpf_get_smp_processor_id();
	if (cpu >= MAXCPU || !BIT_TEST(cfg.mask, cpu)) return 0;   // before any map access
	u32 z = 0;
	struct cs *c = bpf_map_lookup_elem(&cpus, &cpu);
	if (!c || !c->sirq_t0) return 0;
	struct win *w = bpf_map_lookup_elem(&winm, &z);
	if (!w) return 0;
	u64 d = bpf_ktime_get_ns() - c->sirq_t0;
	c->sirq_t0 = 0;
	w->sirq += d; w->sirqn += 1;
	if (vec < NVEC) w->vec[vec] += d;
	struct pcs *q = (cfg.flags & F_CGF) ? bpf_map_lookup_elem(&pcsm, &z) : NULL;   // F_CGF: per CPU for the weighted phi_irq
	if (q) q->sirq += d;
	if (bpf_get_current_pid_tgid() & 0xffffffff) {               // the softirq ran on a task's time
		u32 k = cls(bpf_get_current_cgroup_id());
		if (k < cfg.nv && k < NV) { w->sirqc[k] += d; if (q) q->sirqc[k] += d; }
	}
	return 0;
}

struct fctx { u32 v; };

static long fold_cpu(u32 i, void *ctx)
{
	struct fctx *f = ctx;
	u32 v = f->v, z = 0;
	if (v >= NV || i >= MAXCPU) return 1;
	struct rec *s = bpf_map_lookup_elem(&scr, &z);
	if (!s) return 1;
	struct win *w = bpf_map_lookup_percpu_elem(&winm, &z, i);
	if (!w) return 0;
	bool mine = BIT_TEST(cfg.vmask[v], i);
	u32 *sp = bpf_map_lookup_elem(&sibs, &i);
	u32 si = sp ? *sp : 0xffffffff;
	bool sibof = si < MAXCPU && BIT_TEST(cfg.vmask[v], si);       // i is the SMT sibling of one of v's CPUs
#pragma unroll
	for (int h = 0; h < NH; h++) { s->R[h] += w->R[v][h]; s->Wo[h] += w->Wo[v][h]; }
	s->Rn += w->Rn[v]; s->Rrd += w->Rrd[v]; s->Rrdn += w->Rrdn[v]; s->mig += w->mig[v];
	s->sw += w->sw[v]; s->pre += w->pre[v]; s->wk += w->wk[v];
	if (mine) {
#pragma unroll
		for (int h = 0; h < NH; h++) { s->occ[h] += w->occ[h]; s->sib[h] += w->sibm[v][h]; }
		s->sirq += w->sirq; s->sirqn += w->sirqn; s->sirqc += w->sirqc[v];
#pragma unroll
		for (int k = 0; k < NVEC; k++) s->vec[k] += w->vec[k];
	}
	if (sibof) {
#pragma unroll
		for (int h = 0; h < NH; h++) s->sib[h] += w->sibo[v][h];
	}
#pragma unroll
	for (int j = 0; j < MAXVC; j++) {
		if (j >= cfg.nvcpu[v]) break;
		u32 cj = cfg.vcpu[v][j];
		if (cj == i) {
#pragma unroll
			for (int h = 0; h < NH; h++) s->csib[j][h] += w->sibm[v][h];
			s->csirq[j][0] += w->sirq; s->csirq[j][1] += w->sirqc[v];
		}
		if (sibof && cj == si) {
#pragma unroll
			for (int h = 0; h < NH; h++) s->csib[j][h] += w->sibo[v][h];
		}
	}
	return 0;
}

static int tick(void *map, int *key, struct tmv *val)
{
	u32 z = 0;
	for (u32 v = 0; v < NV; v++) {
		if (v >= cfg.nv) break;
		struct rec *s = bpf_map_lookup_elem(&scr, &z);
		struct rec *p = bpf_map_lookup_elem(&prevr, &v);
		if (!s || !p) break;
		volatile u64 *a = (volatile u64 *)s;                     // volatile: no memset idiom
		for (int i = 0; i < RECW; i++) a[i] = 0;
		struct fctx f = { .v = v };
		bpf_loop(cfg.ncpu, fold_cpu, &f, 0);
		struct rec *o = bpf_ringbuf_reserve(&rb, sizeof(struct rec), 0);
		if (!o) { gs.lost += 1; continue; }
		volatile u64 *b = (volatile u64 *)p, *d = (volatile u64 *)o;
		for (int i = 2; i < RECW; i++) { d[i] = a[i] - b[i]; b[i] = a[i]; }
		o->t = bpf_ktime_get_ns(); o->v = v; o->seq = gs.seqno;
		bpf_ringbuf_submit(o, 0);
	}
	gs.seqno += 1;
	bpf_timer_start(&val->t, cfg.tick_ns, BPF_F_TIMER_CPU_PIN);
	return 0;
}

SEC("syscall")
int k5_arm(void *ctx)
{
	u32 z = 0;
	struct tmv *t = bpf_map_lookup_elem(&tmr, &z);
	if (!t) return 1;
	if (bpf_timer_init(&t->t, &tmr, 1 /* CLOCK_MONOTONIC */)) return 2;
	if (bpf_timer_set_callback(&t->t, tick)) return 3;
	if (bpf_timer_start(&t->t, cfg.tick_ns, BPF_F_TIMER_CPU_PIN)) return 4;
	gs.armed = 1;
	return 0;
}

char LICENSE[] SEC("license") = "GPL";
