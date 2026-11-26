// SPDX-License-Identifier: GPL-2.0
// Phase K1 addendum: empty libbpf programs on the same tracepoints as k1lean.bpf.c (the libbpf floor that K1 lacked).
// `k1lean k1empty.bpf.o --progs switch` attaches e_switch only; `--progs switch,wakeup` also e_wakeup and e_wakeup_new.
#include <linux/types.h>
#include <linux/bpf.h>
#include <bpf/bpf_helpers.h>
#include <bpf/bpf_tracing.h>

typedef _Bool bool;
struct task_struct;

SEC("tp_btf/sched_switch")
int BPF_PROG(e_switch, bool preempt, struct task_struct *prev, struct task_struct *next, unsigned int prev_state) { return 0; }

SEC("tp_btf/sched_wakeup")
int BPF_PROG(e_wakeup, struct task_struct *p) { return 0; }

SEC("tp_btf/sched_wakeup_new")
int BPF_PROG(e_wakeup_new, struct task_struct *p) { return 0; }

char LICENSE[] SEC("license") = "GPL";
