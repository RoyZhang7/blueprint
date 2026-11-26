# Phase J — unpinned, production-clock validation on the r650 (clnode270 node-3)

Phase I ran every cell with the victims pinned by thread affinity, `HWP_REQUEST` holding 2.4 GHz, C1E and C6 off. Phase J
repeats the chain the way a production node runs: the platform's own P-states (HWP request as found, turbo), all idle
states, the victims placed by the scheduler under a CFS limit (`cpu.max`), the probe watching every CPU and selecting the
victims by cgroup. Order: J0 → J1 → J2 → J3, stop and report after J3; J1 decides the default set and everything after it
waits for J1. Nothing committed; the stage-3 RUNBOOK §18 carries every node change with its restore. The cluster expires
2026-11-20 13:32 UTC; every cell is archived to `/proj/tracing-pythia-PG0/royno7-j-archive-clnode270-2026-11-20/j1/` as
it finishes, and all of `/local/results` (Phase I) went to `royno7-i-archive-clnode270-2026-11-20/results/` first.

## 0. J0 — node-3 at production settings

| item | before (Phase I state) | J0 | restore (EXIT trap) |
|---|---|---|---|
| `HWP_REQUEST` (0x774), CPUs 0-143 | 0x55001818 (min = max = 2.4 GHz) | the saved original 0x5500ff01 (`i2node3.sh hwp-restore`) | written back from the queue's snapshot `node3-hwp-pre.txt` |
| idle states | C1E (`state2`) and C6 (`state3`) `disable` = 1 | all enabled (`cstate-restore`) | `c1e-off`, `cstate-off` |
| uncore | min = max 2 400 000 kHz | as found: 800 000-2 400 000 (`uncore-restore`) | `uncore-pin` |
| `msr` module | loaded (ours) | unloaded (`msr-unload`; "msr retired": the clock comes from perf events) | loaded again, as before J0 |
| victims | `taskset` to CPU 1 (unique-id) and 5,7 (recommendation), no CFS limit | no `taskset`; `cpu.max` 100000/100000 (unique-id, 1 core) and 400000/100000 (recommendation, 4 cores); QoS Burstable (no memory limit), so kubelet's CPU manager (policy none) sets no cpuset | deployments replaced from the pre-prep snapshot |
| `CONFIG_IRQ_TIME_ACCOUNTING` in 6.12.118-harvest | — | **not set** (`CONFIG_TICK_CPU_ACCOUNTING=y`): `/proc/stat`'s softirq column is tick-sampled; the probe's softirq programs are the only exact source | — |
| recommendation's GOMAXPROCS | 2 (set from the cpuset) | unset: the image's Go 1.25.14 takes it from `cpu.max` (4) | — |

Expected mid (ρ 0.5 from the rho ramp, ρ = cgroup usage / (k × wall)): unique-id **≈ 30 000 req/s** (Phase I's 22 483 at
2.4 GHz scaled by a ≈ 3.4 GHz single-core turbo, service CPU ≈ 16 µs), recommendation **≈ 9 000 req/s** (k 4 against
Phase I's k 2 at 3 600).

**Measured (J0, the first ramp, 05:40-05:52 UTC): the prediction for unique-id missed by 10×.** Unpinned under `cpu.max` =
1 core, the pod's CPU per request is **≈ 190 µs against 22 µs pinned** (ρ 0.195 / 0.386 / 0.773 at 1 000 / 2 000 / 4 000
req/s; a hand test at 2 000 req/s: 0.355 cores, 1 960 served/s, client p50 0.61 ms, no throttling), the quota binds from
6 000 req/s (ρ 1.00, 255 throttled periods in 25 s) and at 8 000 req/s the pod is throttled in 97 % of its periods
(849 of 873), serving nothing to the client, with 55 % of its CPU in system time. The 64 connection threads of the
TThreadedServer, pinned to one CPU in Phase I, now wake on 64 idle cores (C6 exit 170 µs, cross-core wake-ups, cold
caches, migrations): the "production clock" costs this service most of its capacity before any neighbor arrives. **Mid for
the ladder is therefore ≈ 2 600 req/s** (the ramp's interpolation), not 30 000; the "stress case" switch rate is an
order of magnitude lower than predicted, so the probe rows' predictions below (made for 22-30 k req/s) are upper bounds
and the CPU-per-request differences will be measured against a 190 µs base. Recommendation (k 4 from `cpu.max`, Go 1.25's
GOMAXPROCS 4): ρ 0.206 / 0.324 / 0.502 / 0.636 / 0.754 / 0.962 at 1 000-10 000 req/s, **≈ 580 µs CPU per request against 278
µs pinned on 2 cores** (×2.1), throttling only from 10 000 req/s (178 periods); **mid 3 976 req/s** against the ≈ 9 000 predicted.
The ladder's twins confirm both: 192.8 µs (unique-id) and 579.4 µs (recommendation) per request. The clock column (per-CPU
cycles / ref-cycles) reads **800 MHz on-CPU-weighted for the unique-id pod** and 829-846 MHz over the 11-13 busy CPUs:
with the HWP request released, the cores the unpinned pods touch sit at the minimum P-state because no single core is
busy enough to raise it. The rest of the inflation is the thread model: each request wakes a thread on an idle core (C6
exit 170 µs, cold caches, a migration), which the probe sees as one wake-up and one switch per request (`wk` = `sw` = `Rn`,
`Rrd` 0 unless preempted).

**J0 as run (queue j1 prep, 05:48 UTC, from the queue's snapshots):** `HWP_REQUEST` 144 × 0x55001818 → 144 × 0x5500ff01;
`state2`/`state3` disable 144 × 1 → 144 × 0; uncore 2 400 000 / 2 400 000 → 800 000 / 2 400 000 on both packages; `msr`
loaded → unloaded; unique-id `cpu.max` 100000 100000 and recommendation 400000 100000, `Cpus_allowed_list` 0-143 on every
thread, cpuset.cpus.effective 0-143. **Restored from the trap at 09:49 UTC** (and again at 09:53 after the profile rerun):
HWP, idle states, uncore, msr, resctrl and bpf_stats equal to the pre-state, 46 deployments equal, 79/79 pods.

## 1. J1 — the overhead ladder, unpinned, production clock (queue j1, Nov 20 ~06:30 UTC on)

Design: unique-id (the stress case) and recommendation at mid, one cell per service with the 16 arms below, a twin arm
(`none`: interceptor off, no agent) before and after each, **3 reps × 60 s** (cut from 120 s for the cluster's expiry;
K5's cell length), the generator at mid throughout; CPU per request = the pod cgroup's `cpu.stat` usage over the arm ÷ the
client's completions, client p99 from the generator's per-second records, both as the paired difference to the two
neighbouring twins with the spread over reps. Rows 1-4 are cumulative; everything else runs alone. Before the ladder: the
J2-addendum profile (perf by symbol of the unsampled interceptor path on the g8 image, every request timed) and the row-5
before/after cell (count only on g8, the shared-counter build, against its own twins). Image g9 = the per-thread recorder
(each server thread's counters in its own padded block, summed by the flusher).

**Predictions, written before the run** (CPU per request vs the paired twin; p99 "flat" = within the twins' spread, ±3 %):

| row | arm | what runs | unique-id CPU/req (user's prediction) | rec CPU/req | p99 |
|---|---|---|---|---|---|
| 1 | `j-probe` | switch program on all 144 CPUs, cgroup-filtered: run_delay, on-CPU per CPU, wait credited to the last holder | **+0.8 %** (0.5-1 %) | +0.4 % | flat |
| 2 | `j-holder` | + the holder state of every CPU on every switch (occupancy by holder, the sibling's holder → φ_sibling) | +1.2 % | +0.6 % | flat |
| 3 | `j-wake` | + sched_wakeup programs | +1.6 % | +0.8 % | flat |
| 4 | `j-sirq` | + softirq entry/exit programs on every CPU | +1.9 % | +0.9 % | flat |
| 5 | `j-count` | interceptor: arrivals and completions only (per-thread, g9) | +0.4 % (< 1 % once per-thread); **g8 before: +0.7 %** | +0.3 % | flat |
| 6 | `j-slot` | + the slot integral | +0.6 % | +0.4 % | flat |
| 7 | `j-full` | + timers on every request (histogram add per request) | +1.2 % (< 1 %) | +0.6 % | flat |
| 8 | `j-s8` | 1-in-8 timers instead | +0.7 % | +0.4 % | flat |
| 9 | `j-perf` | perf cgroup group on all CPUs: cycles, instructions, LLC misses, 3 stalls, ref-cycles, LLC refs (8 events, 3 fixed + 5 programmable; `pmux` time_enabled = time_running) | +1.5 % (≤ 1.5 %): the cgroup event switch on 400 k switches/s | +0.8 % | flat |
| 10 | `j-rdt` | resctrl CMT/MBM armed (RMID written on the victim's switches), no reads | +0.4 % | +0.2 % | flat |
| 11 | `j-clock` | per-CPU cycles / ref-cycles events on all 144 CPUs, read each tick on CPU 53 | +0.2 % | +0.1 % | flat |
| 12 | `j-reads` | the agent's sampler (cgroup cpu.stat, `/proc/stat` of 144 CPUs) + the recorder's window reads | +0.3 % | +0.2 % | flat |
| 13 | `j-scorer` | the scorer alone, replaying recorded inputs at the tick, on CPU 53 | +0.1 % | +0.1 % | flat |
| 14 | `j-all` | everything, every request timed, scorer on-node | +4.5 % | +2.5 % | +3-5 % |
| 14′ | `j-all-s8` | everything, 1-in-8 timers | +4.0 % | +2.3 % | +3 % |
| 15 | `Bp-J` | Phase I's set B′ as it runs unpinned (mask = every CPU, no cgroup filter, no msr read) | +3.5 % (Phase I pinned: +2.6-3.0 %) | +1.5 % | +3 % |

Recommendation rule (fixed before the run): the default set is the largest union of rows whose **all-on arm reads ≤ 2 % CPU
per request on unique-id with p99 flat at mid**. Predicted outcome: the probe through softirq (rows 1-4), count + slot,
1-in-8 timers, resctrl armed, the clock events, the reads and the scorer — about 1.9 + 0.7 + 0.4 + 0.2 + 0.4 ≈ 3.5 %
summed, so the ≤ 2 % set will have to drop either the probe's holder/wake/softirq programs or the perf group; the perf
group (row 9) is the first candidate to leave the default and become escalation-only.

**Contended-line question (J2 addendum).** The g8 recorder keeps one process-wide array of atomics (arrivals, completions,
the slot accumulator, 512 histogram buckets) that every TThreadedServer thread increments; with 64 connections that is 64
threads on the same lines at 22-30 k requests/s. Prediction: the profile shows the `fetch_add` lines of `arrival` /
`countCompletion` / `slotBegin` in the recorder's symbols with cache-misses out of proportion to their instructions, and
row 5 reads +0.7 % on g8 against +0.4 % on g9.

### 1.1 Measured (unique-id cell `20261120-010826-j1-uid-ladder`, 06:09-07:57 UTC, 99 arm records, 0 errors; `results/j1/an/j1_ladder.{csv,md}`)

**Twins:** 0.4994 cores at 2 591 req/s = **192.8 µs CPU per request** (Phase I pinned at 2.4 GHz: 22 µs), client p99 1.540 ms,
the node 1.21 busy cores in all (the generator, the agent's CPU, the pod), the pod's CPUs at **800 MHz** (the on-CPU-weighted
clock from the per-CPU cycles / ref-cycles events; the busy CPUs' mean 829-846 MHz over 11-12 busy CPUs per tick): unpinned
and lightly loaded per core, HWP leaves every core the service touches at its minimum P-state. CPU per request vs the paired
twins, mean over 3 reps [min, max]; p99 = client p99 vs the same twins.

| row | arm | predicted | **measured CPU/req** | **p99** | note |
|---|---|---|---|---|---|
| 1 | probe, cgroup-filtered, all CPUs | +0.8 % (0.5-1) | **+1.55 % [1.17, 1.81]** | −0.16 % [−0.61, 0.13] | 500 ns per BPF event; agent 0.002 cores |
| 2 | + holder state on every switch | +1.2 % | **+1.52 % [1.26, 1.81]** | −0.33 % | the node's other switches cost nothing measurable (694 ns/event) |
| 3 | + wake-up programs | +1.6 % | **+2.37 % [2.11, 2.76]** | 0.00 % | +0.85 pt for the wake-up programs |
| 4 | + softirq programs | +1.9 % | **+3.48 % [3.28, 3.61]** | +0.31 % [0.19, 0.37] | +1.1 pt: softirq entry/exit on every CPU |
| 5 | count only (g9, per-thread) | +0.4 % | **+0.42 % [0.07, 0.69]** | +0.29 % | **g8 (shared counters): +0.42 % [0.04, 0.88]** — the same |
| 6 | + slot | +0.6 % | **+0.70 % [0.36, 1.04]** | +0.20 % | |
| 7 | + timers on every request | +1.2 % (< 1) | **+1.09 % [0.83, 1.25]** | −0.16 % | |
| 8 | 1-in-8 timers instead | +0.7 % | **+1.08 % [0.95, 1.22]** | +0.15 % | **sampling buys nothing**: the timer's cost is in the clock reads, not the histogram add |
| 9 | perf cgroup group, 8 events, all CPUs | +1.5 % (≤ 1.5) | **+12.41 % [12.29, 12.53]** | +0.45 % | no multiplexing (time_running = time_enabled on 100 % of ticks); the cgroup event switch on every arrival of the pod's threads on a CPU |
| 10 | resctrl CMT/MBM armed | +0.4 % | **+0.72 % [0.44, 1.01]** | −0.05 % | the RMID write on the victim's switches |
| 11 | per-CPU clock events, 144 CPUs | +0.2 % | **+0.02 % [−0.15, 0.22]** | −0.04 % | agent 0.127 cores on CPU 53 (288 reads per tick), nothing on the pod |
| 12 | sampler + window reads | +0.3 % | **+0.50 % [0.29, 0.71]** | +0.04 % | |
| 13 | scorer alone (replay) | +0.1 % | **+0.01 % [−0.12, 0.21]** | +0.08 % | scorer 0.010 cores |
| 14 | all on, every request timed | +4.5 % | **+20.46 % [20.16, 20.73]** | **+1.60 % [1.46, 1.68]** | ≈ the sum of rows 4 + 7 + 9 + 10 + 12 (18.2) plus the on-node scorer's follow reads |
| 14′ | all on, 1-in-8 | +4.0 % | **+20.24 % [20.12, 20.44]** | +1.49 % | |
| 15 | B′ (Phase I set) unpinned | +3.5 % | **+3.22 % [3.10, 3.37]** | −0.04 % | Phase I pinned: +2.6-3.0 %; 677 ns/event, scorer 0.014 cores |

- **The contended-line question is answered: no.** Count-only costs +0.42 % on both builds (g8 shared atomics +0.42 [0.04,
  0.88], g9 per-thread +0.42 [0.07, 0.69]); at 2 591 req/s over 64 threads the shared lines are not contended (one increment
  per 390 µs). The per-thread build stays (it is the right structure for the 20-30 k req/s pinned case), but it is not what
  decides row 5-8. The 15 s perf profile of the unsampled path is rerun by the follow-up chain (the first attached to a
  terminating pod's process) → `results/j1-profile/`.
- **Timers: every request costs the same as 1-in-8** (+1.09 vs +1.08 %): the cost is the two clock reads and the
  `TProcessorEventHandler` context per request, which both modes pay; the histogram add is free. Rule: keep 1-in-8 only if
  full timers exceed 1 % — they read 1.09 % [0.83, 1.25], at the line; with no gain from sampling, **full timers are the
  default** (every request timed, the per-second p50 exact).
- **perf is escalation-only.** The cgroup perf group on an unpinned service costs 12.4 %: each arrival of a pod thread on a CPU
  schedules 8 counters in and out (`perf_cgroup_switch`), about 24 µs per request here, against a 193 µs request. Pinned in
  Phase I the same group cost ≤ 1.5 % because the thread stayed on one CPU.
- **The probe's cost is in its own program, not the node's switches:** rows 1 and 2 are equal (1.55 / 1.52 %), so tracing
  every switch on 144 CPUs for the holder state is free at this node's rate; the wake-up (+0.85 pt) and softirq (+1.1 pt)
  programs are what the cumulative rows add.

**Recommendation (one line, by the rule fixed before the run).** No set that contains both the probe and the interceptor meets
≤ 2 % CPU per request on unique-id unpinned at this operating point (probe 1.55 + timers 1.09 = 2.6 % by additivity; the
measured union B′ reads 3.22 % with p99 flat); the largest set under 2 % is **the interceptor with full timers + the window
reads + the per-CPU clock + the on-node scorer (≈ 1.6 % summed, p99 flat)**, the probe (1.55 %) being the alternative when the
holder matters; perf (12.4 %) and the softirq programs (+1.1 pt) are escalation-only. Caveat: the rule's "all-on arm" was
measured only for the full union (20.5 %) and for B′ (3.2 %); the sub-unions are sums of single rows (rows 1-4 cumulative,
which were additive within 0.1 pt). Recommendation's ladder: §1.2.

### 1.2 Recommendation (cell `20261120-025716-j1-rec-ladder`, 07:58-09:49 UTC, 99 arm records, 0 errors)

**Twins:** 2.30 cores at 3 975 req/s = **579.4 µs CPU per request** (Phase I pinned on 2 CPUs: 278 µs), client p99 15.0 ms.
The generator's per-second log stopped after 5 243 s while its load continued (the pod's CPU is unchanged in the later arms),
so rows 8-15 have n = 2 (rep 2's client rates and p99 are missing for them); client p99 on this service swings ±5-10 %
between twins at mid, so its column resolves nothing finer.

| row | arm | predicted CPU/req | **measured CPU/req** | p99 | n |
|---|---|---|---|---|---|
| 1 | probe, cgroup-filtered | +0.4 % | **−0.60 % [−0.84, −0.38]** | +7.0 % [5.1, 10.4] | 3 |
| 2 | + holder state | +0.6 % | **−0.27 % [−0.61, 0.29]** | +0.5 % | 3 |
| 3 | + wake-up | +0.8 % | **−0.30 % [−0.75, 0.11]** | −0.4 % | 3 |
| 4 | + softirq | +0.9 % | **−0.08 % [−0.37, 0.18]** | +5.2 % [−2.0, 16.0] | 3 |
| 5 | count only | +0.3 % | **−0.21 % [−0.36, −0.12]** | +0.3 % | 3 |
| 6 | + slot | +0.4 % | **+0.24 % [−0.23, 0.91]** | +2.0 % | 3 |
| 7 | + timers, every request | +0.6 % | **+0.51 % [0.23, 0.76]** | +0.4 % | 3 |
| 8 | 1-in-8 timers | +0.4 % | **+0.34 % [0.14, 0.54]** | −7.7 % | 2 |
| 9 | perf cgroup group, 8 events | +0.8 % | **−1.10 % [−1.26, −0.95]** | +0.6 % | 2 |
| 10 | resctrl armed | +0.2 % | **+0.49 % [−0.31, 1.29]** | +7.3 % | 2 |
| 11 | per-CPU clock events | +0.1 % | **−0.00 % [−0.16, 0.16]** | +4.8 % | 2 |
| 12 | sampler + window reads | +0.2 % | **+0.03 % [−0.35, 0.40]** | +1.7 % | 2 |
| 13 | scorer alone | +0.1 % | **+0.01 % [−0.56, 0.58]** | +1.7 % | 2 |
| 14 | all on, every request timed | +2.5 % | **+2.27 % [2.07, 2.48]** | −0.7 % | 2 |
| 14′ | all on, 1-in-8 | +2.3 % | **+1.36 % [0.65, 2.06]** | +4.9 % | 2 |
| 15 | B′ unpinned | +1.5 % | **+0.56 % [0.17, 0.95]** | −2.8 % | 2 |

On the Go service with four Ps the single rows are within the twins' spread (−1.1 to +0.5 %; the probe and the perf group
read *negative*, which is the resolution of a 60 s arm against a 580 µs request on 2.3 cores, not a gain); the one real
reading is the interceptor's timers (+0.51 % on every request, +0.34 % at 1-in-8) and the all-on arm, **+2.27 %** with
full timers and +1.36 % at 1-in-8. perf costs nothing here: with GOMAXPROCS 4 the cgroup's threads stay on few CPUs and the
event switch happens rarely, where unique-id's 64 threads arrive on a new CPU per request.

### 1.3 The unsampled interceptor path, profiled (J2 addendum; `results/j1/profile/`, the rerun at 09:50-09:53 UTC on g8)

`perf record -e cycles -e cache-misses -c 20011 -p <pid> -- sleep 15` on the unique-id process (67 threads) at mid with every
request timed: the recorder's symbols hold **0.9 % of the process's samples** (`EventHandler::getContext` 0.41 %, `finish`
0.26 %, `postRead` 0.07 %, `freeContext` 0.06 %, `preWrite` 0.06 %, `preRead` 0.03 %, the flusher 0.01 %), the kernel 55.7 %,
libstdc++ 21.0 %, the service's own code 9.6 %. That is the full-timers row (+1.09 %) seen from inside, and it is the
clock reads and the handler's context, not the counters: the shared-counter build's atomics do not show as a cache-miss
hot spot at one increment per 390 µs. Per-thread blocks (g9) change nothing at this rate (row 5: +0.42 % on both builds) and
are kept for the pinned 20-30 k req/s case.

### 1.4 The default set (J1's decision) and what J2-J6 inherit

By the rule fixed before the run (the largest set whose all-on arm reads ≤ 2 % CPU per request on unique-id with p99 flat):
**interceptor with full timers (every request) + the window reads + the per-CPU clock events + the on-node scorer, ≈ 1.6 %
summed on unique-id (1.09 + 0.50 + 0.02 + 0.01), 0.55 % on recommendation, p99 flat on both.** The probe (1.55 % filtered,
1.52 % with the holder state, 2.37 % with wake-ups, 3.48 % with softirq) does not fit beside the interceptor under 2 % on
unique-id unpinned; it is the escalation's first stage, the perf group (12.4 %) and the softirq programs its second. Phase
I's B′ measured as one arm costs 3.22 % here (2.6-3.0 % pinned) with p99 flat, so if the user accepts 3.5 % instead of 2 %
the probe (holder state included) joins the default. The 1-in-8 sampling is dropped from the default (no saving: 1.08 vs
1.09 %). Caveats: the "all-on" criterion was measured for the full union (20.5 %) and B′ (3.2 %) only, the rest by additivity
(rows 1-4 additive within 0.1 pt); the operating point is 2 591 req/s at 193 µs per request, an order of magnitude below the
pinned case the predictions assumed; the cluster expired at 13:32 UTC before J2-J6 could run, so they stay as written in the
prompt with this set as the default.

**Files.** `results/j1/an/j1_ladder.{csv,md}` (33 rows; `results/figs/src/j1_ladder.csv` → `overhead_cells.csv` rows with
`set` = "J1 row N"), `results/j1/{queue-j1.log,cells.txt,mid-*.tsv,mid-*.txt,pins-j1.txt,node3-*.txt,queue-j1-profile.log}`,
`results/j1/profile/profile-g8-*.txt`; raw `archive/j1-raw-20261120-laptop.tgz` on the laptop (sha256 c47fc3d4…, the same
file as `/proj/tracing-pythia-PG0/royno7-j-archive-clnode270-2026-11-20/j1-raw.tgz`), not staged. The project filesystem
also holds `j1/` (with the recorder streams), `j1-try1/`, `j1-profile/`, `code/` (`~/k5`, `~/i2`, the analysis scripts, the
built agent and probe, `Gordion.h` g9, `sn-gordion-g9.tar.gz`) and, under `royno7-i-archive-clnode270-2026-11-20/results/`,
every Phase I queue; all readable from clnode107. Code: `k5/probe/*` (F_CGF, F_HOLD, `pcsm`, `--clock-cpus`, `--perf-ref`),
`k5/k5spec.py` (the `j-*` arms), `k5/k5ctl.py` (k from `cpu.max`, `sample=`), `i/queue-j1.sh`, `i/chain-j1.sh`,
`analysis/j1_ladder.py`, `socialNetwork/src/gordion/Gordion.h` (per-thread counters; image g9).

## 2. J0″ — the busy node near the knee (prepared Nov 20; waits for a cluster)

Plan (user's prompt): node-3 at defaults, nothing pinned; a background of the DSB chain under a steady compose load plus
bigsvc tenants until node CPU utilization is 60-70 %, the recipe recorded; node utilization and the on-CPU-weighted clock
logged per window for the whole queue; the victims under `cpu.max`, load stepped to the knee, then held at ρ 0.75; the J1
rows 1, 5-7, 9-10, 14 at ρ 0.75 (3 × 60 s, paired twins, µs per request and %); then J2 → J3 (sweeps on the busy node,
step-binned; stop and report) → J4 (default set at ρ 0.75 and 0.5, cells < 85 min) → J5 → J6.

**What is built (staged, untested on a node: no cluster was available):**
- `i/queue-j0b.sh`: J0 prep as queue-j1 (snapshots, defaults through `i2node3.sh`, victims unpinned with `cpu.max`, trap
  re-pin); `nodewatch` = one `k5agent` for the whole queue on CPU 53 (`--sampler 1` on every CPU → "pst" per CPU per 100 ms
  = node utilization; `--clock-cpus all` → "clk_busy" = the busy CPUs' clock); the SN chain's other services on node-3
  (`OTHER_NODES=node-3`) under `hrgen -app sn -mix compose` from node-1 at `DSB_RATE` (600 req/s); bigsvc tenants
  `bigsvc-tN` (`exp.sh` gained `NAME=`; `cpu.max` 4, memwalk 24 MB, uniform mix, `TENANT_RATE` 300 req/s each from node-1)
  added one at a time until the 15 s node utilization is in [0.60, 0.70] (`busy-steps.txt`, `recipe.json` with pods and
  rates); the knee per victim (`knee-*.tsv`: rate, ρ, served, client p50/p99, node util per 30 s step; the knee = the first
  step with served < 98 % of offered or p50 > 2 × the first step's; the hold rate = the ρ 0.75 point of the ρ-rate table);
  the ladder subset `j-probe,j-count,j-slot,j-full,j-perf,j-rdt,j-all` with a twin around each, 3 × 60 s (≈ 51 min per
  service). `analysis/j1_ladder.py` now writes `delta_us` [min, max], `arm_us`, `twin_us` beside the percentages
  (`J_LOAD`, `J_SET`, `J_NEIGHBOR` name the rows).
- J2: `Gordion.h` has `TServerSocketTS` / `TSocketTS` (SO_TIMESTAMPNS, `recvmsg` with the cmsg, the stamp in a thread-local),
  `startOf()` takes the stamp when newer than the thread's last completion, else the handler entry and a fallback count;
  the window line gains `"lath"` (handler-entry histogram) and `"lat_fb"`, `GORDION_RXTS=0` turns it off;
  `UniqueIdService.cpp` uses it unless SSL. Needs the image build (g10) on the next node-0 and the own-cost arm (B′ on g10
  vs g9, n = 3) before use.
- J3: `k5score.py --fit-rates r1,r2,... [--fit-guard 0.98]` bins the sweep by its offered-rate steps with the overload guard
  (`dropped_steps` in the dictionary), the step-binned re-fit RESULTS-I §11c left open.

**Predictions (written before any run; the node-3 state as J0 found it):**

| item | prediction |
|---|---|
| background recipe | DSB compose at 600 req/s ≈ 6-8 busy cores (11 SN services on node-3, unpinned); tenants at 300 req/s × 24 MB memwalk ≈ 2.5-3 cores each (cpu.max 4); **≈ 28-32 tenants** to reach 60-70 % of 144 CPUs (87-100 busy cores) — `TENANT_MAX` 24 may stop short at ≈ 55 %; if so the queue records the recipe as is and the rate is raised on the rerun |
| clock on the busy node | the busy CPUs' mean **2.2-2.6 GHz** (HWP raises the P-state when cores are busy; the all-core turbo of the 8360Y is 3.1 GHz, the uncore as found); the victims' on-CPU-weighted clock **1.8-2.4 GHz** against 800 MHz on the idle node |
| unique-id CPU per request (idle-node 193 µs, pinned 22 µs) | **80-130 µs**: the clock gain (×2.5-3) against colder caches and longer run-queue waits on shared cores |
| unique-id knee | served < offered from **≈ 6 000-7 000 req/s** (cpu.max 1 core ÷ ≈ 110 µs plus the quota's burst throttling); ρ 0.75 at ≈ 5 000 req/s |
| recommendation CPU per request (idle 579 µs, pinned 278) | **350-450 µs**; knee ≈ 7 000-8 000 req/s (cpu.max 4); ρ 0.75 at ≈ 6 000 |
| served vs offered at ρ 0.75 | equal within 1 % on both; client p50 unique-id 0.6-1.0 ms (idle node: 0.61 ms at 2 000 req/s), recommendation 5-8 ms |
| ladder at ρ 0.75, unique-id, µs per request (%) | probe **+3.0 µs (2.5-3.5 %)**, count +0.8 (0.7 %), slot +1.3 (1.2 %), full timers +2.1 (2.0 %), perf **+24 µs (20 %)**, rdt +1.4 (1.3 %), all-on +35 µs (30 %); p99 flat except all-on (+2-3 %) |
| ladder at ρ 0.75, recommendation | probe ≈ 0 (within ±2 µs), count/slot/full +1 / +2 / +3 µs (0.2-0.7 %), perf within noise (−5 to +5 µs), rdt +2 µs, all-on +10 µs (2.5 %) |
| what changes against the idle node | the probe's µs per request stay (its cost is per switch, ≈ 2 switches per request), its percentage rises as the request shrinks; perf's µs stay (per arrival on a CPU), so its percentage rises too |

The decision rule stays J1's: the default set is the one whose all-on arm reads ≤ 2 % on unique-id with p99 flat; by these
predictions that is still interceptor + reads + clock + scorer, and the probe (≈ 3 %) stays escalation stage 1.
