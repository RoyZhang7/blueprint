# Gordion scheduler probe (Phase G, §2.2)

Host-side eBPF probe that measures, per 100 ms window, the kernel run-queue delay of the service's threads split by the
cgroup that held the CPU in between, the service threads' sleep time split by futex (lock) vs other, and the pod
cgroup's `cpu.stat`, keyed by the same unix-ms `t` as the recorder's windows. It runs on the service node outside the
pod, as root, filtered to the CPUs of the service's cpuset.

## Files

- `gordion_sched.bt.tmpl` — the bpftrace program; `probe.py` substitutes the CPU filter and the cgroup ids.
- `probe.py` — wrapper: generates the program, runs `bpftrace -f json`, reads the pod's `cpu.stat` at every record,
  writes `<out>.jsonl` (one JSON line per 100 ms: `t`, `Rself`, `Raggr`, `Rroot`, `Rother`, `Runk`, their counts,
  `Qlock`, `Qidle`, `preempt`, `multi`, `usage_usec`, `nr_periods`, `nr_throttled`, `throttled_usec`), `<out>-hist.jsonl`
  (per second: the log2 histogram of runnable gaps and the per-thread R map) and `self` records with the probe's own
  CPU (bpftrace + wrapper, from `/proc`).
- `probe-ctl.sh` — `start <container-name-fragment> <cpuset> <out> [aggr-cgroup]` finds the pod's cgroup from its
  container id and starts the wrapper detached (`<out>.pid`); `stop <out>` sends SIGINT; `aggr-cgroup` creates
  `/sys/fs/cgroup/gordion-aggr` for stress-ng so the aggressor has a cgroup id of its own.
- `../analysis/g2_probe.py` — joins `probe.jsonl` with the recorder's windows into `<run>/g2/g2_windows.csv`
  (`U`, `R_foreign_ms`, `R_self_ms`, `R_other_ms`, `Q_futex_ms`, `Q_idle_ms`, `Q_mutex_ms`, `G_ms`, `sched_p50/p90_ms`,
  `preempt`, `multi`, `nr_throttled`, `probe_self_cores`) and prints the per-step summary.

## Kernel, BTF, cgroup layout (clnode170, Nov 2026)

- Kernel `6.12.116-harvest` (Ubuntu 22.04), `CONFIG_DEBUG_INFO_BTF=y`, `/sys/kernel/btf/vmlinux` present, cgroup v2,
  kubelet under `kubepods.slice`, docker with the systemd cgroup driver (`docker-<id>.scope`).
- bpftrace **0.14.0** (apt) with **libbpf 0.5**: it reports `BTF data not available` for this kernel (libbpf 0.5 cannot
  parse the 6.12 BTF), so `kfunc:finish_task_switch` and BTF struct walks are **not usable**; the BEGIN/END probes do
  not resolve either (stripped binary). The probe therefore uses only tracepoints and builtins:
  `tracepoint:sched:sched_switch` (current == prev: prev's cgroup from the `cgroup` builtin, `prev_state` from the
  arguments; next's cgroup from a pid → cgroup map learned whenever a task is switched out), `tracepoint:sched:sched_wakeup`,
  `tracepoint:syscalls:sys_enter/exit_futex`, `interval:ms:100`. A newer bpftrace (≥ 0.17 with libbpf ≥ 1.0, built from
  source or a newer distro) would allow the kfunc form; nothing in the measurement depends on it.
- Holder classes: the service's cgroup (R_self), the aggressor's cgroup (R_foreign; stress-ng must run inside
  `/sys/fs/cgroup/gordion-aggr`), the root cgroup = kernel threads and host processes outside any slice (R_root), any
  other cgroup (R_other), not yet known (R_unk; a thread's first switch-in). The last holder before the resume is
  credited; `multi` counts resumes with more than one holder in between.
- Go services: futex sleeps are mostly parked idle Ms, so the probe's `Qlock` is the C++ (Thrift) Q only; the Go Q
  is the recorder's `rt` mutex-wait and `sched` fields (runtime/metrics), and G is the recorder's `rt` GC fields.

## Attribution (v2): credit by time share

v1 credited a thread's whole runnable gap to the last task that ran on the CPU; under CFS a woken service thread
preempts a CPU hog within microseconds and then waits behind its own threads, so the hog received almost nothing.
v2 keeps per-CPU occupancy counters per holder class (`@occ0..3`, charged to `prev` at every switch), snapshots them
when a service thread becomes runnable (on preemption, on that CPU; on `sched_wakeup`, on the target CPU, adding the
running task's partial slice from `@lastsw` / `@curcls`), and on resume splits the gap by the counters' deltas
(self = gap − foreign − root − other, clamped). Threads resuming on another CPU get the last-holder credit (`mig`).
The last-holder figures stay in the record (`Lself`, `Laggr`, `Lroot`, `Lother`). Unit test on node-3 (CPU-bound victim
in `/sys/fs/cgroup/gordion-victim` sharing CPU 1 with one hog in `gordion-aggr`, 118 windows): R_foreign 50.0 ms per
100 ms window, R_self 0.0, 12.3 preemptions per window; `cpu.stat` of the two cgroups 50 / 50.

bpftrace 0.14 grammar notes: no cast on a bare variable or after `+=` (use `$x = (int64)(expr)` and temporaries); no
BEGIN/END; the ternary works; `args->prev_state & 0xff == 0` marks a runnable switch-out.

## v3: occupancy of the cpuset by holder class

The per-CPU occupancy counters of v2 are also summed per 100 ms window and exported as `Oself`, `Oaggr`, `Oroot`,
`Oother` (ns of CPU time on the cpuset held by each class). φ = `Oaggr` ÷ (k × window) is the aggressor cgroups'
share of the service's cores: a hog that occupies a core without preempting the service's threads (the service's
wake-ups avoid the busy core and its threads crowd the others) shows up here and not in R_foreign. Addendum C.1 of
the handoff makes φ the trigger of row 1. `zero()` replaces `clear()` on every map (bpftrace 0.14's `clear()` deletes
keys and races with the program; it aborted the probe once with "Could not clear map").

## Overhead

RESULTS-G.md §2.3 (clnode170, node-3 CPUs 1,3,5,7, uniform sweep 40–900 req/s, probe off vs on): service CPU +0.1 to +0.6 % (the in-kernel BPF time is charged to the interrupted service threads), CPU per request +0.01 to +0.10 ms, per-endpoint floors on/off median 1.000 (99 % within ±5 %), knee unchanged, p50 within ±2 %; the probe process itself (bpftrace + wrapper) 0.0046 cores, flat with load, at 1 500–3 700 service-thread resumes per second on four CPUs.

## v3.1 (Nov 11, after (v) run 2): per-thread maps on thread-per-connection servers

bpftrace maps hold 4096 keys by default. The pid-keyed maps (`@cg`, `@runnable`, `@snap*`, `@sleep`, `@infutex`,
`@Rthr`) fill up on a server that creates a thread per connection (SocialNetwork post-storage: ~30 000 threads per
sweep, ~60 new per second): after ~90 s every new thread is unclassified, its run-queue delay and futex sleep are
lost (R and Q read 0 while the occupancy O* — read from the `cgroup` builtin — stays right). (v) runs 1 and 2 carry
this (R only on the first two steps). Fix: `probe.py` sets `BPFTRACE_MAP_KEYS_MAX=1048576` for bpftrace, and the
program deletes a thread's entries on `tracepoint:sched:sched_process_exit` (so `@Rthr`, which `zero()` keeps the
keys of, holds live threads only). Go services (a handful of threads) were never affected.
