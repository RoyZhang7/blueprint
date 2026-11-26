// SPDX-License-Identifier: GPL-2.0
// Phase K5: the layout shared by k5probe.bpf.c and k5agent.c (configuration, per-CPU state, per-CPU windows, records).
#ifndef K5PROBE_H
#define K5PROBE_H

#include <linux/types.h>

#define MAXCPU 256            // CPU bitmaps of NW 64-bit words (Phase I: the r650 has 144 logical CPUs; was one u64)
#define NW (MAXCPU / 64)
// bit c of a bitmap m (c < MAXCPU checked by the caller); the word index is masked so the BPF verifier sees it bounded
#define BIT_TEST(m, c) ((((m)[((c) >> 6) & (NW - 1)]) >> ((c) & 63)) & 1)
#define BIT_SET(m, c) ((m)[((c) >> 6) & (NW - 1)] |= 1ULL << ((c) & 63))
#define NCG 8                 // configured cgroup slots (holders); victims are slots 0..nv-1
#define NV 4                  // victims at most
#define NID 16                // configured cgroup ids (several container scopes may map to one slot)
#define H_ROOT NCG
#define H_OTHER (NCG + 1)
#define H_IDLE (NCG + 2)
#define NH (NCG + 3)
#define MAXVC 8               // CPUs per victim in the record's per-CPU section
#define NVEC 10
#define F_RQ 1
#define F_SIB 2
#define F_WAKE 4
#define F_SOFTIRQ 8
#define F_TIMER 16
#define F_RD 32               // total run_delay of the victims' threads, no holder split (task storage, read at switch-out)
#define F_CGF 64              // Phase J1: cgroup-filtered mode on every CPU (unpinned victims): the switch program returns before
                              // any map access unless prev or next is a victim; on-CPU time and wait per CPU in pcs; mask = all
#define F_HOLD 128            // J1 row 2, with F_CGF: the holder state of every CPU on every switch (occupancy by holder, the
                              // sibling's holder for phi_sibling); without it the wait is credited to the task that left when we arrived

struct cfg {
	__u64 cgid[NID];          // configured cgroup ids (kernfs ids of the cgroup directories)
	__u32 cgslot[NID];        // slot of each id
	__u32 nid, nv;            // ids configured; victims = slots 0..nv-1
	__u64 vmask[NV][NW];      // each victim's CPUs
	__u64 mask[NW];           // union of vmask: where the switch accounting runs
	__u64 sibmask[NW];        // SMT siblings of mask's CPUs that are not in mask (traced for their holder with F_SIB)
	__u64 root;               // the root cgroup's id
	__u64 tick_ns;            // F_TIMER period
	__u32 flags, ncpu;        // F_*; possible CPUs (fold bound)
	__u32 vcpu[NV][MAXVC];    // each victim's CPUs as a list (the record's per-CPU section)
	__u32 nvcpu[NV];
};

struct cs {                   // per CPU, shared (the sibling CPU reads it)
	__u64 lastsw;             // last switch on this CPU
	__u64 occ[NH];            // cumulative ns per holder (mask CPUs; snapshots for the time-share split)
	__u64 sirq_t0;            // softirq entry time
	__u32 cur;                // holder now on this CPU
	__u32 sirq_vec;
};

struct ts {                   // per victim thread
	__u64 runnable, sleep, rd;
	__u64 snap[NH];
	__u32 rcpu, hin, pend, seen;
	__u64 tin;                // F_CGF: the time this thread got its CPU
};

struct pcs {                  // F_CGF: per CPU, cumulative, read by the agent each tick
	__u64 on[NV];             // victim v's on-CPU ns on this CPU
	__u64 wait[NV];           // victim v's run-queue wait before its runs on this CPU
	__u64 sirq;               // softirq ns on this CPU (F_SOFTIRQ)
	__u64 sirqc[NV];          // of which while victim v's task was current
};

struct win {                  // per CPU, cumulative
	__u64 occ[NH];            // ns held per holder (mask CPUs)
	__u64 R[NV][NH];          // run-queue wait of victim v's threads, by holder (F_RQ)
	__u64 Rn[NV], Rrd[NV], Rrdn[NV], mig[NV];
	__u64 sw[NV], pre[NV];    // switch-outs of victim v's threads; of which still runnable (preempted)
	__u64 wk[NV];             // wake-ups of victim v's threads (F_WAKE; on the waker's CPU)
	__u64 Wo[NV][NH];         // the sleep that ended, by waker class (F_WAKE)
	__u64 sibm[NV][NH];       // F_SIB: victim v's thread on this CPU, the sibling held by h
	__u64 sibo[NV][NH];       // F_SIB: victim v's thread on this CPU's sibling, this CPU held by h
	__u64 sirq, sirqn;        // F_SOFTIRQ: ns and count on this CPU
	__u64 sirqc[NV];          // of which while victim v's task was current
	__u64 vec[NVEC];          // by vector
};

struct rec {                  // one per victim per tick (differences); also the fold's cumulative scratch
	__u64 t;                  // fold time (ns, CLOCK_MONOTONIC)
	__u32 v, seq;
	__u64 occ[NH];            // its CPUs' time by holder: phi = the foreign share, on-CPU = occ[v]
	__u64 R[NH];              // run-queue wait by holder: R_self = R[v], R_foreign = the others
	__u64 Rn, Rrd, Rrdn, mig, sw, pre, wk;
	__u64 Wo[NH];
	__u64 sib[NH];            // phi_sibling: its on-CPU time by holder of the SMT sibling
	__u64 sirq, sirqn, sirqc; // softirq on its CPUs: ns, count, ns while its task was current
	__u64 vec[NVEC];
	__u64 csib[MAXVC][NH];    // per CPU of its list: phi_sibling by holder
	__u64 csirq[MAXVC][2];    // per CPU: softirq ns, of which while current
};
#define RECW (sizeof(struct rec) / 8)

struct gstate { __u32 seqno, lost, armed, pad; };

#endif
