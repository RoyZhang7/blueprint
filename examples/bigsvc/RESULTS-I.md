# Phase I results — Ice Lake r650 (clnode270)

Spec: the Phase I prompt of 2026-11-17 (steps 0-7, stop points 1-3). Cluster changes are in stage-3 `RUNBOOK.md` §18;
data under `results/i*/`. Every run has its expected reading written before it, the measured one beside it. Nothing
from clnode170 is assumed here until it is measured.

## Step 0. clnode170's earlier results

clnode170's node-0 `/local/results` (165 entries, 5 835 files, 26.5 GB) is copied whole to the project's Clemson
storage, `/proj/tracing-pythia-PG0/royno7-k-archive-clnode170-2026-11-17/results/` (Nov 17 21:12-21:17 UTC; an
`rsync` dry run afterwards finds 0 differing files); `/proj` outlives the experiment and is readable from clnode270.
On the laptop: the K5/K6 directories whole (`C:/coding-workspace/archive/k5k6-final-20261117-laptop.tgz`), the 76
small items that were missing (queue scripts and logs, mix files, irq snapshots, `build`, `k4restore`:
`clnode170-results-small-20261117-laptop.tgz`), and the analysed subsets under `results/`. **Not on the laptop, only
in `/proj`:** the bulk raw directories `k1` (11 GB; 0.5 GB of it local), `k4` (2.9 GB), `k42`, `k42e`, `k42f`, `k4b`,
`k45` (1.4 GB) and the 22 K1 synthetic-service run directories (`20261113-*-k1-*-syn-*`, 1.4 GB).

## 0. Platform inventory (read-only, Nov 17)

All five nodes, read with a script and a CPUID reader (no mount, no module, no write; RUNBOOK §18). node-0 to
node-3 are identical; node-4 differs in its boot settings (below).

| item | expected (RESULTS-K 7) | measured |
|---|---|---|
| CPU | 2 × Xeon Platinum 8360Y, 36 cores each | 2 × Xeon Platinum 8360Y @ 2.40 GHz, 36 cores per socket, 144 logical CPUs (node-4: 72) |
| SMT | on (node-4 of the profile `nosmt`) | node-0 to node-3: on, switchable at run time (`smt/control` = on); siblings `c ↔ c+72`; node-4: `nosmt=force` at boot (`forceoff`) |
| NUMA, socket layout | — | 2 nodes; **even CPUs on socket 0, odd on socket 1** (node 0 = 0,2,4,…,142) |
| LLC | — | 54 MB per socket (108 MB), **12 ways** (4.5 MB per way), shared by all of a socket's CPUs |
| memory | — | 16 × 16 GB DDR4-3200, A1-A8 and B1-B8: **8 channels per socket, one DIMM each**; 251 GB |
| NIC | 25 Gb ConnectX-5 | cluster LAN on a **100 Gb ConnectX-6 Dx** (`enp202s0f0np0`, NUMA node 1, 63 queues); 25 Gb ConnectX-5 on the public side (NUMA 0); two 1 Gb BCM5720 unused |
| kernel, BTF | 5.15 unless the harvest kernel is installed | **6.12.118-harvest** everywhere; `/sys/kernel/btf/vmlinux` present; `BPF_JIT_ALWAYS_ON`, `DEBUG_INFO_BTF`, `CGROUP_BPF`, `SCHED_INFO`, `PSI`, `X86_CPU_RESCTRL` set |
| BPF features the probe uses | `bpf_timer`, `bpf_loop`, ring buffer, `tp_btf` | `bpf_timer_init`, `bpf_loop`, `bpf_ringbuf_reserve`, `bpf_get_current_cgroup_id`, `bpf_get_current_ancestor_cgroup_id`, `bpf_task_storage_get` all in the kernel; `bpf_stats` 0, `unprivileged_bpf_disabled` 2, `perf_event_paranoid` 2, `sched_schedstats` 0 (as on clnode170) |
| programmable counters per thread | 4 with SMT on, 8 with it off | **8 with SMT on and 8 with it off** (CPUID 0xA: 8 × 48-bit per logical CPU, perfmon v5; the kernel's "generic registers: 8" on every node) |
| fixed counters, top-down | 4 fixed incl. `TOPDOWN.SLOTS`, `PERF_METRICS` | 4 fixed × 48-bit; `slots`, `topdown-retiring`, `-bad-spec`, `-fe-bound`, `-be-bound` exposed (level 1); PEBS fmt4, 32-deep LBR |
| uncore PMUs | — | 36 CHA, 12 IMC (+4 free-running), 6 IIO, 4 M2M per node |
| resctrl: monitoring | CMT, MBM total and local | CMT, **MBM total and local**; **288 RMIDs** per L3 domain (CPUID 0xF), upscale 73 728 bytes; resctrl detected at boot, not mounted |
| resctrl: L3 CAT | ways and CLOS from `info/L3` | **12-bit capacity mask (12 ways), 15 CLOS**, no CDP |
| resctrl: MBA | throttle steps from `info/MB` | **15 CLOS, linear, max throttle 90 %** (10 % steps) |
| cpufreq | HWP; pin with `HWP_REQUEST` min | node-0 to node-3: **no cpufreq driver** ("intel_pstate: P-states controlled by the platform", no `hwp` flag): the OS cannot set the clock. node-4: `intel_cpufreq` passive, `schedutil`, 0.8-3.5 GHz, turbo allowed |
| clock under load (a 4 s spin on two CPUs) | 3.1 GHz turbo | node-3: **3.5 GHz** on the busy cores, idle ones 0.8-2.4 GHz; node-4: 3.1 GHz |
| C-states | — | node-0 to node-3: `intel_idle` with C1, C1E and **C6 (170 µs)** enabled; node-4: `acpi_idle`, C1 only (boot `max_cstate=0`) |
| uncore clock | fixed by the platform on clnode170 | `intel_uncore_frequency` loaded everywhere: node-0 to node-3 0.8-2.4 GHz (2.1-2.2 GHz under the spin); node-4 min = max 2.4 GHz, read 2.2 GHz under the spin |
| HC's 64-CPU limit | exceeded (72 cores) | 144 CPUs with SMT on, **72 with it off: both above 64**; HarvestContainers checked out on node-4 only (`/local/HarvestContainers`), `qidlecpu` loaded nowhere |
| Kubernetes | — | v1.25.6; node-0, node-1 control plane; node-2, node-3, node-4 workers. **Correction (Nov 17 23:33):** not only system pods: the profile deployed a HotelReservation stack in `default` at 16:40 UTC (21 pods, unpinned, idle), 9 of them on **node-3** (frontend, geo, profile, user, `hr-client`, memcached-rate, three mongods) and 3 on node-4; the inventory's pod listing missed them. On clnode170 node-3 held no pods. Measured on node-3 (30 s, idle): every pod together 0.046 cores (calico 0.034, each mongod ~0.003), the victims' CPUs 1, 3, 5, 7 busy 0.1-0.3 %. Left as found (the motivation's stack; not ours to move) |

**Changes the probe needs before it runs here, on any node:** its `MAXCPU 64` and `u64` CPU masks become bitmaps
(RESULTS-K 7): 144 or 72 CPUs both exceed them.

### 0.1 Stop point 1: SMT state and frequency, recommendation

**The one number:** 8 programmable counters per thread with SMT on, so SMT costs no counters here (RESULTS-K 7
expected 4).

**Recommendation.**
- **SMT on for the evaluation, on node-3 as the victim node** (node-0 to node-3 have it on now). It is the state the
  Haswell results were taken in, it keeps φ_sibling and C1′ possible, and it costs no counters. Victims on one thread
  per core with the siblings idle, as on clnode170.
- **Frequency: try to take turbo away on node-3, otherwise measure it.** The OS has no frequency control on node-0 to
  node-3, so a busy core runs at 3.5 GHz and an idle one at 0.8-2.4. Two reversible changes would pin the uncore
  (sysfs min = max = 2.4 GHz) and ask the CPU to drop turbo (`IA32_MISC_ENABLE` bit 38 through the `msr` module).
  Whether the platform honours the second is not known until it is tried and read back with a spin. If it does, the
  cores run at the 2.4 GHz base under load. If it does not, the clock is recorded per window (APERF/MPERF, already the
  recorder's `mhz` read) and ρ carries f ÷ f_nom with f_nom 3.5 GHz.
- **node-4 stays as the profile set it** (SMT off, OS frequency control, C1 only, uncore 2.4 GHz), for the SMT-off
  control sweep and, if it is pursued, for HC.

**What each choice costs.**

| choice | what it costs |
|---|---|
| SMT on (node-3) | nothing in counters (8 per thread either way); the neighbors must avoid the victims' siblings except in C1′ |
| SMT off everywhere | no φ_sibling and no C1′; node-0 to node-3 would need `smt/control` off (run time) or a reboot with `nosmt` |
| frequency on node-3 as is | turbo varies with the number of busy cores on the socket, so I_g and the dictionary carry the neighbor's load through the clock; it is recorded, not removed |
| turbo off on node-3 (if honoured) | one `msr` module load and a write per CPU, undone at the end; the clock is 2.4 GHz rather than Phase C's 3.1, so f_nom 2.4 |
| victim on node-4 instead | a pinnable clock, but SMT off (no φ_sibling, so C1′ must still run on node-3 at another clock) and C1-only idle states differ from node-0 to node-3 |
| HC (C5′ and step 7) | HC addresses CPUs below 64 only: on node-4 (72 CPUs) CPUs 64-71 would have to go offline for its runs, or HC's masks patched to bitmaps; on an SMT-on node (144 CPUs) offlining is not practical, so patching would be needed |

**Decision (user, Nov 17):** SMT on; victim node node-3; node-4 left as the profile set it, for the SMT-off control
sweep and for HC (CPUs 64-71 offline for its runs, no mask patch); on node-3, in order: uncore pinned, turbo off through
MSR 0x1A0 bit 38, else `HWP_REQUEST` min = max = base, else record the clock; C6 and deeper off.

### 0.2 node-3 clock and idle states (Nov 17 23:12-23:15 UTC; RUNBOOK §18)

Clock read as APERF/MPERF over 3 s on CPUs held busy by a shell loop (MPERF counts at the 2.4 GHz TSC rate).

| step | expected | measured |
|---|---|---|
| before any change | 3.5 GHz busy (inventory) | 2 busy cores 3 482 MHz; all 36 cores of socket 0 busy 2 762 MHz, and that socket's uncore falls to 1.7 GHz |
| 1. uncore min = max = 2.4 GHz | uncore 2.4 GHz | **2.2 GHz** at idle and under 2 busy cores, both packages (node-4's profile pin reads the same 2.2); still 1.7 GHz with the socket full until step 3 |
| 2. MSR 0x1A0 bit 38 (turbo disengage) | busy 2.4 GHz | bit set and read back on all 144 CPUs (the register is per package), **ignored**: 3 498 MHz with 2 busy, 2 757 MHz with 36 busy. Restored to the saved value before step 3 |
| 3. `HWP_REQUEST` (0x774) min = max = 24 (2.4 GHz), EPP 0x55 kept | busy 2.4 GHz | **holds**: 2 400 MHz with 2 busy (socket 0 and socket 1) and with 36 busy; uncore 2.2 GHz on both packages even with the socket full |
| 5. C6 off (cpuidle `disable`) | — | C6 disabled on CPUs 0-143, 0 entries afterwards; still 2 400 MHz busy |

**HWP is on although the OS does not see it:** no `hwp` CPUID flag and no cpufreq driver, but `IA32_PM_ENABLE` = 1
and `HWP_CAPABILITIES` = highest 35, guaranteed 24, lowest 8 (the platform enables HWP and keeps it). The OS's
writes to `HWP_REQUEST` are honoured, min and max both (on clnode258's Ice Lake only min held). The register can be
rewritten by the platform, so it is read back before every queue. **Idle states left on node-3:** POLL (exit 0 µs),
C1 (1 µs), C1E (4 µs; target residency 4 µs); C6 (170 µs, residency 600 µs) off. node-4 has C1 only (`acpi_idle`).
**f_nom on node-3 = 2.4 GHz.**

## 2. Bring-up

### 2.0 Environment from clnode170 (Nov 17 23:05-23:20 UTC)

From the export the user ran on clnode170 (`/proj/.../env/`, three files, manifest on the laptop). **Image IDs:**

| image | clnode170 | clnode270 after `docker load` | registry digest (both) |
|---|---|---|---|
| `hr-gordion:gordion9` | `sha256:76572e3dbbf1…f24406` | `sha256:76572e3dbbf1…f24406` | `sha256:f8c183e9d474…bc5` |
| `sn-gordion:g8` | `sha256:4409b3d32e75…6c8a` | `sha256:4409b3d32e75…6c8a` | `sha256:f0b782f4ee54…bb` |
| `big_ctr:k123g4` | `sha256:a63bed141029…68d3` | `sha256:a63bed141029…68d3` | `sha256:e0f2b47ea390…bc` |

(Full IDs in the session scratchpad `k56/clnode170-image-ids.tsv`; clnode170's stock images, by node, with digests in
`k56/clnode170-docker-images-all-nodes.tsv`, to compare what Docker Hub serves here for the `:latest` tags.)

**Presence check before any cluster change** (read-only script, `i2/presence-out.txt`). Present: `ugen`
(`~/k1/ugen/ugen`, static), `hrgen` (static), bigsvc `wlgen-k123g4`, every K5 driver and helper (`queue-k5.sh`,
`k5ctl.py`, `k5score.py`, `k5slice.py`, `k5spec.py`, `k5net.sh`, `k5summary.py`, `build-k5p.sh`), `hr.sh`,
`sn.clean.sh` (with the socfb-Reed98 graph), bigsvc `exp.sh` and `stressor.sh`, the twelve mix files, the HR
Kubernetes manifests (50), the SN helm chart, the K4 scripts, `kubectl`, `helm`, `docker`, `perf`, `gcc`, the
`nfs-client` storage class. The scripts match the laptop's staged copies byte for byte (CRs stripped) except the
expected four: the bitmap probe (not yet on node-0), `kernels.go` and `exp.sh` (clnode170's tree predates
`BIGSVC_MEMWALK_STEPS`), `dictionary.py` and `b4_compare.py` (the 4.5.1 fit options). **Missing, installed in the
bring-up and logged:** clang/llc/bpftool (probe build), Go 1.25.3 and `protoc` with its Go plugins (bigsvc image with
the fixed step count), `stress-ng` on node-3 (the neighbors), numpy on node-0 (the fits), aiohttp (the SN graph load,
installed by `sn.clean.sh` itself). Not taken from clnode170: its Go module cache (fetched again) and 3.4 GB of
non-victim recorder files on its node-1 and node-2.

### 2.1 Layout and apparatus (queue `i/queue-i2.sh`, derived from `k5/queue-k5.sh`)

| role | clnode170 (RESULTS-G 2.1, RESULTS-K 5) | clnode270 |
|---|---|---|
| service node | node-3, socket 1 (odd ids), siblings +28 idle | node-3, socket 1 (odd ids; the NIC's socket), siblings +72 idle |
| victims | unique-id CPU 1; recommendation 5,7; bigsvc 1,3,5,7 | the same CPU ids |
| generator | node-3 CPUs 17-27 odd, **the victims' socket** | node-3 CPUs 16-26 even, **socket 0** (the spec's "other socket") |
| agent and scorer | CPU 53 (socket 1) | CPU 53 (socket 1) |
| neighbors | `stress-ng` in `/sys/fs/cgroup/gordion-aggr` on the victim's CPUs (switch), its siblings (sib), CPUs 9-15 (cache); UDP pods on 9/11 with RPS to the victim (net) | the same |
| datastores, other services | node-1 / node-1, node-2 | the same |
| images | `sn-gordion:g8`, `hr-gordion:gordion9` (set B′), `big_ctr:k123g4` | `g8`, `gordion9` (loaded, IDs equal), **`big_ctr:k123i2`** (memwalk steps fixed; RUNBOOK §18) |
| recommendation's admission gate | off in 5.8 | off |
| clock | `intel_cpufreq`, 2.0 GHz pinned, turbo off | HWP min = max 2.4 GHz, uncore 2.2 GHz, C6 off (§0.2) |
| probe | lean switch program, `u64` masks | the same, CPU masks as 4-word bitmaps (MAXCPU 256) |

Probe smoke test after the bitmap change (node-3, Nov 17 23:17 UTC, a temporary cgroup as the victim on CPUs 1, 3, 101,
139): `mask` words `a,2000000000,800,0` and `sibmask` `20000000,a08,0,0` (CPUs 1, 3, 101, 139 and their siblings 73,
75, 29, 67: all four words addressed correctly); 15 154 switches and 12 151 run-delay samples counted, per-CPU sections
filled for 1, 101 and 139 and empty for 3 (no load there).

### 2.2 Queue i2: expected readings (written before it runs)

| run | what | expected | measured |
|---|---|---|---|
| mid, unique-id | ρ ramp 3 000-24 000 req/s, no instrumentation; mid = the rate at ρ 0.5 | ρ 0.5 near **14 000 req/s** (clnode170 10 200 at 2.0 GHz; ×1.2 for the clock and ~×1.15 for Ice Lake's IPC on this Thrift path) || **16 568 req/s** (interpolated: ρ 0.139 at 3 000, 0.266 at 6 000, 0.371 at 9 000, 0.428 at 12 000, 0.473 at 15 000, 0.525 at 18 000, 0.580 at 21 000, 0.631 at 24 000; `ugen` delivered each rate). CPU per request falls with load (0.046 ms at 3 000, 0.026 at 24 000), so ρ is concave in λ |
| mid, recommendation | ρ ramp 1 000-8 000 req/s | ρ 0.5 near **5 000 req/s** (clnode170 3 600) || **10 482 req/s**, past the ramp: ρ 0.144 at 1 000 rising to only 0.432 at 8 000 (hrgen delivered every rate in full; CPU per request 0.29 ms at 1 000, 0.11 ms at 8 000). The queue first extrapolated through the origin (9 270), which reads low on a concave curve; replaced at 00:00 UTC, before the twins, by the slope of the last two steps (the queue in the repository now does that) |
| mid, bigsvc uniform | ρ ramp 100-700 req/s at 256 MB, fixed steps | ρ 0.5 near **440-465 req/s**: the work is defined in ms (c̄ 4.30 ms, k 4 gives 465 at ρ 0.5), less the runtime's own CPU || **431 req/s** (ρ 0.119 at 100, 0.234 at 200, 0.348 at 300, 0.468 at 400, 0.573 at 500, 0.682 at 600, 0.774 at 700: linear: slope 0.00109 ρ per req/s, i.e. 4.36 ms CPU per request against c̄ 4.30 ms (+1.4 %, the runtime)); `ns_per_iter` (the spin calibration) identical across the three pods (2.5928-2.5942) |
| K1-style cell, quiet | unique-id at mid, `none` vs set B′'s probe alone (switch; φ, φ_sibling, run_delay), 5 × 60 s | **80-110 ns per switch** averaged over all CPUs (clnode170's lean program 107-129 ns at 2.0 GHz; ×2.0/2.4 for the clock; the bitmap test adds one shift and one mask, a few ns); Δ CPU per request **+2 to +3.5 %** (clnode170's lean probe +3.5 % quiet); p99 unchanged within ±2 % || **50-51 ns per switch** (150 k switches/s, all CPUs); Δ CPU per request **+0.9 % [+0.6, +1.1]**, Δ client p99 +0.2 % [−0.1, +0.6]; BPF 0.0076 cores, agent 0.0019, 0.95 ms per tick. Twin: 0.0303 ms per request at 0.503 cores, client p99 1.91 ms. Below the expected range: the per-event cost fell by more than the clock ratio |
| K1-style cell, switch neighbor | the same under one `stress-ng --switch` on CPU 1 | ns per switch about the same, events/s ~2.5× the quiet cell (clnode170 262 k vs 109 k); Δ CPU per request **+2 to +3 %** (clnode170 +2.2 %) || **80 ns per switch** (520 k switches/s); Δ CPU per request **+1.1 % [+0.9, +1.3]**, Δ client p99 +0.5 % [+0.4, +0.6]; BPF 0.041 cores, 4.2 ms per tick. **Unexpected:** the twin under the neighbor is cheaper and faster than the quiet twin (0.0225 vs 0.0303 ms per request, 0.372 vs 0.503 cores, client p99 1.33 vs 1.91 ms at the same 16 570 req/s), the direction of clnode170's "faster under a neighbour"; to be looked at in the signatures (step 4), not explained here |
| calibration, 256 MB | quiet pod, `walk_ns_per_step` | **90-110 ns per step** (a dependent load from DRAM; 256 MB is 5× the 54 MB LLC): N ≈ 4 ms ÷ that ≈ **36 000-44 000** || **94.7 ns per step, N = 42 244**; the pod started with N fixed measured 95.4 ns on its own start (the count no longer follows it) |
| calibration, 128 MB | the same | **80-105 ns** (2.4× the LLC; a few hits): N ≈ **38 000-50 000** || **56.7 ns per step, N = 70 557**: faster than expected (the 128 MB cycle is 2.4× the LLC, but more of it hits than a uniform random chase would) |
| twins at mid (5 min) | each service alone, twin arm | unique-id p50 **9-11 µs** (clnode170 12 µs); recommendation p50 below clnode170's by the clock ratio; bigsvc uniform p50 ≈ F̄ of the mix (~4 ms) plus a queueing term || unique-id (16 568 req/s): 0.506 cores, 0.0305 ms CPU per request, client p99 1.91 ms (server windows read after the restore). Recommendation (10 482): 0.978 cores of 2 (ρ 0.49), 0.093 ms per request, **client p99 31.4 ms** (clnode170 4.82 ms at its mid): its own recorder in the sweep reads server p50 0.205 ms and p99 6.6 ms at this rate, so most of the client's tail is upstream (frontend and network on node-1/2, which here also carry the profile's idle HR stack); server p50 turns between 10 500 and 12 600 (0.205 → 0.410 ms), client p50 too (4.2 → 8.2 ms). bigsvc uniform (431): 1.96 cores, 4.56 ms CPU per request, server p50 3.62 ms, p99 19.4 ms |
| sweeps, counter column | arm `sweep` (perf cycles, instructions, LLC misses; resctrl llc_occupancy, mbm_total) per step | unique-id and recommendation: CPI flat across ρ within ±10 %, MPKI < 5, occupancy a few MB, mbm < 0.5 GB/s. bigsvc spin mixes: CPI ≈ 0.3-0.5 (a register loop), MPKI ≈ 0, occupancy and mbm ≈ 0 beyond the runtime's. memwalk30 at 128 MB: MPKI and mbm rising with λ, **~0.3-0.5 GB/s** at mid (30 % of requests × N steps × 64 B), occupancy tens of MB || see §2.3. **Not as expected in two ways:** CPI is not flat in load on the two real services (unique-id 0.60 → 0.82, recommendation 0.60 → 0.44 across the sweep) and their instructions per request fall with load (116 k → 67 k; 611 k → 416 k), so the counter column has to be read at matched ρ; unique-id's mbm reaches 0.81 GB/s at 30 k req/s (expected < 0.5). As expected: bigsvc spin mixes MPKI ≈ 0.01-0.04, memwalk30 at 128 MB mbm 0.45 GB/s at mid, occupancy 36-50 MB. bigsvc CPI is 0.62, not 0.3-0.5 |
| sweeps, fit | `dictionary.py` with a 50-window floor per ρ bin and step interpolation (RESULTS-K 4.5.1) | every step kept up to ρ 0.8; I_g ≈ 1 on the twins at mid || every sweep fitted with ≥ 50 windows per bin; bigsvc: the top step (all mixes) or the top two (B, mutex10, mutex40, memwalk30) dropped as overloaded, the gate caps ρ at ≈ 0.77 (3 slots of 4); unique-id and recommendation: 15 bins each, the K5 fit's ρ axis (λ·c̄ at the lowest tenth of the load ÷ k) runs to 1.41 and 1.91 because CPU per request falls with load (measured CPU utilisation 0.76 and 0.72 at the top steps). Twins against the dictionaries: in step 3's none cells (the scorer reads them there) |

### 2.3 The dictionaries and their counter column (queue i2, Nov 18 00:39-01:59 UTC)

Fits (`analysis/i2_dict.py`; files in `results/i2/dict/`): bigsvc per mix with `dictionary.fit_service_dict`
(50 settled windows per ρ bin, step interpolation), unique-id and recommendation with `k5score.py --fit
--fit-min-windows 50` (its ρ bins are interpolated linearly in the scorer).

| dictionary | F̄ (ms) | c̄ (ms) | K (ms) | ρ bins | ρ range | dropped steps |
|---|---|---|---|---|---|---|
| unique-id | floor p50 0.014 | 0.0464 | — | 15 | 0.15-1.41 | — |
| recommendation | floor p50 0.086 | 0.190 | — | 15 | 0.20-1.91 | — |
| bigsvc uniform | 2.270 | 4.277 | 5.082 | 17 | 0.04-0.79 | 8 |
| short | 1.893 | 1.916 | 1.240 | 15 | 0.04-0.79 | 8 |
| long | 9.617 | 10.352 | 6.168 | 17 | 0.04-0.79 | 8 |
| mid7 | 6.544 | 7.028 | 4.188 | 17 | 0.04-0.79 | 8 |
| B | 5.404 | 5.788 | 3.450 | 15 | 0.04-0.70 | 7, 8 |
| mutex10 | 2.067 | 4.095 | 4.918 | 15 | 0.04-0.70 | 7, 8 |
| mutex40 | 2.039 | 3.412 | 4.174 | 15 | 0.04-0.70 | 7, 8 |
| memwalk30, 128 MB | 5.383 | 4.240 | 4.248 | 15 | 0.04-0.70 | 7, 8 |

**Unique-id's p50 is bimodal in load:** 13.8 µs at the two lowest steps (ρ ≤ 0.29 measured), 4.9 µs from 9 900 req/s on
(clnode170: flat 12.4-13.8 µs). At low load a request mostly starts on a thread woken from C1E; above that the thread
is still running. It makes the dictionary's p50 fall with ρ.

The counter column per step (`results/i2/dict/i2_counters.csv`; the first 3 s of each step dropped; ρ here is the
recorder's process CPU ÷ step ÷ k; CPI, MPKI, instructions per request (k), llc_occupancy (MB, median tick, both L3
domains) and mbm_total (GB/s) for the victim's cgroup from the agent's 100 ms ticks):

| mix | offered | served /s | ρ | p50 ms | p99 ms | CPU/req ms | CPI | MPKI | ins/req (k) | LLC MB | mbm GB/s |
|---|---|---|---|---|---|---|---|---|---|---|---|
| uid | 3314 | 3321.2 | 0.1713 | 0.0138 | 0.026 | 0.05158 | 0.598 | 0.519 | 116 | 2.39 | 0.0705 |
| uid | 6627 | 6622.9 | 0.2938 | 0.0138 | 0.127 | 0.04436 | 0.665 | 0.662 | 89 | 3.16 | 0.1683 |
| uid | 9941 | 9823.9 | 0.3821 | 0.0049 | 0.032 | 0.03889 | 0.712 | 0.729 | 80 | 3.3 | 0.2582 |
| uid | 13254 | 13391.2 | 0.4651 | 0.0049 | 0.03 | 0.03473 | 0.744 | 0.763 | 74 | 3.52 | 0.3462 |
| uid | 16568 | 16535.9 | 0.529 | 0.0049 | 0.03 | 0.03199 | 0.768 | 0.785 | 72 | 3.45 | 0.4314 |
| uid | 19882 | 19701.4 | 0.582 | 0.0049 | 0.03 | 0.02954 | 0.787 | 0.805 | 71 | 3.38 | 0.519 |
| uid | 23195 | 23278.2 | 0.6414 | 0.0049 | 0.032 | 0.02755 | 0.804 | 0.827 | 69 | 3.66 | 0.6204 |
| uid | 26509 | 26727.6 | 0.7099 | 0.0049 | 0.03 | 0.02656 | 0.815 | 0.843 | 67 | 3.66 | 0.7077 |
| uid | 29822 | 29719.0 | 0.7585 | 0.0049 | 0.03 | 0.02552 | 0.822 | 0.857 | 67 | 4.15 | 0.8113 |
| rec | 2096 | 2118.4 | 0.2573 | 0.086 | 0.475 | 0.24293 | 0.595 | 0.025 | 611 | 14.84 | 0.0159 |
| rec | 4193 | 4191.3 | 0.3437 | 0.086 | 0.754 | 0.16401 | 0.57 | 0.019 | 518 | 15.61 | 0.0117 |
| rec | 6289 | 6295.1 | 0.4124 | 0.127 | 1.507 | 0.13101 | 0.549 | 0.017 | 479 | 15.96 | 0.0197 |
| rec | 8386 | 8394.4 | 0.462 | 0.1556 | 4.063 | 0.11007 | 0.525 | 0.013 | 454 | 16.59 | 0.0219 |
| rec | 10482 | 10494.8 | 0.5108 | 0.2048 | 6.554 | 0.09735 | 0.502 | 0.011 | 439 | 17.44 | 0.0253 |
| rec | 12578 | 12576.6 | 0.5606 | 0.4096 | 9.961 | 0.08915 | 0.482 | 0.011 | 428 | 18.07 | 0.0269 |
| rec | 14675 | 14671.4 | 0.6055 | 1.3763 | 13.107 | 0.08255 | 0.463 | 0.009 | 419 | 18.77 | 0.0338 |
| rec | 16771 | 16794.1 | 0.6595 | 2.7525 | 15.204 | 0.07854 | 0.449 | 0.009 | 415 | 19.55 | 0.0396 |
| rec | 18868 | 18836.4 | 0.724 | 5.505 | 19.923 | 0.07688 | 0.441 | 0.009 | 416 | 20.88 | 0.052 |
| uniform | 86 | 85.9 | 0.1093 | 2.2404 | 13.941 | 5.08753 | 0.61 | 0.036 | 18824 | 21.59 | 0.0439 |
| uniform | 172 | 172.5 | 0.2099 | 2.3153 | 16.676 | 4.86716 | 0.623 | 0.036 | 17920 | 21.52 | 0.0544 |
| uniform | 259 | 260.4 | 0.3084 | 2.4962 | 17.566 | 4.73671 | 0.626 | 0.036 | 17574 | 24.05 | 0.0634 |
| uniform | 345 | 339.9 | 0.3929 | 2.9083 | 18.465 | 4.62378 | 0.627 | 0.035 | 17274 | 26.51 | 0.0837 |
| uniform | 431 | 432.3 | 0.4873 | 3.4426 | 19.436 | 4.50846 | 0.627 | 0.032 | 16984 | 30.52 | 0.0944 |
| uniform | 517 | 511.9 | 0.5831 | 5.2181 | 21.351 | 4.5567 | 0.629 | 0.036 | 17225 | 29.95 | 0.1135 |
| uniform | 603 | 608.8 | 0.6905 | 10.3434 | 27.393 | 4.53723 | 0.629 | 0.036 | 17196 | 30.8 | 0.1301 |
| uniform | 690 | 687.8 | 0.7656 | 41.534 | 59.592 | 4.45281 | 0.629 | 0.034 | 16932 | 30.87 | 0.137 |
| uniform | 776 | 688.3 | 0.7734 | 45.1124 | 64.382 | 4.49472 | 0.629 | 0.033 | 17137 | 32.2 | 0.1417 |
| short | 193 | 192.6 | 0.1236 | 1.8395 | 4.211 | 2.56648 | 0.618 | 0.033 | 8926 | 17.58 | 0.0622 |
| short | 387 | 387.8 | 0.2301 | 1.8256 | 4.277 | 2.37332 | 0.627 | 0.032 | 8365 | 22.29 | 0.0939 |
| short | 580 | 575.0 | 0.3231 | 1.8494 | 4.448 | 2.24756 | 0.628 | 0.03 | 8085 | 20.95 | 0.1203 |
| short | 773 | 773.4 | 0.4226 | 1.9524 | 4.792 | 2.18581 | 0.629 | 0.03 | 8008 | 21.3 | 0.1486 |
| short | 966 | 975.2 | 0.5191 | 2.1701 | 5.507 | 2.12909 | 0.629 | 0.029 | 7915 | 22.29 | 0.1588 |
| short | 1160 | 1159.3 | 0.6091 | 2.525 | 6.583 | 2.10172 | 0.629 | 0.029 | 7890 | 25.17 | 0.2165 |
| short | 1353 | 1360.9 | 0.7027 | 3.6047 | 9.0 | 2.06541 | 0.628 | 0.028 | 7819 | 28.9 | 0.2286 |
| short | 1546 | 1539.4 | 0.7892 | 19.7216 | 24.043 | 2.0508 | 0.628 | 0.027 | 7800 | 27.63 | 0.2449 |
| short | 1739 | 1543.6 | 0.7923 | 20.1205 | 24.607 | 2.05304 | 0.629 | 0.026 | 7813 | 26.86 | 0.2496 |
| long | 36 | 36.8 | 0.1082 | 9.2689 | 16.364 | 11.7518 | 0.601 | 0.01 | 43408 | 18.49 | 0.0343 |
| long | 71 | 71.5 | 0.1998 | 9.5039 | 17.771 | 11.18469 | 0.612 | 0.009 | 42755 | 21.02 | 0.0396 |
| long | 107 | 106.3 | 0.2934 | 9.9904 | 19.041 | 11.03913 | 0.615 | 0.007 | 42294 | 19.62 | 0.0416 |
| long | 143 | 144.4 | 0.3931 | 10.3959 | 19.775 | 10.88541 | 0.617 | 0.007 | 41842 | 21.09 | 0.0426 |
| long | 179 | 180.4 | 0.4854 | 11.2859 | 21.052 | 10.7643 | 0.619 | 0.007 | 41357 | 20.18 | 0.0451 |
| long | 214 | 209.6 | 0.5593 | 12.8604 | 24.326 | 10.67466 | 0.619 | 0.007 | 41131 | 21.09 | 0.0448 |
| long | 250 | 248.1 | 0.6589 | 17.225 | 31.506 | 10.62337 | 0.619 | 0.007 | 40972 | 20.32 | 0.0467 |
| long | 286 | 285.6 | 0.7532 | 60.7931 | 78.689 | 10.54953 | 0.62 | 0.007 | 40711 | 24.05 | 0.0482 |
| long | 322 | 286.7 | 0.7639 | 110.7035 | 123.508 | 10.65925 | 0.62 | 0.007 | 41230 | 23.98 | 0.0481 |
| mid7-070-109 | 53 | 52.6 | 0.1068 | 6.4771 | 11.663 | 8.12029 | 0.601 | 0.012 | 29850 | 17.79 | 0.0329 |
| mid7-070-109 | 105 | 104.2 | 0.2009 | 6.535 | 12.651 | 7.70817 | 0.613 | 0.011 | 29249 | 17.79 | 0.0348 |
| mid7-070-109 | 158 | 162.6 | 0.308 | 6.797 | 13.31 | 7.57521 | 0.617 | 0.01 | 28807 | 18.84 | 0.0364 |
| mid7-070-109 | 210 | 209.6 | 0.3895 | 7.1473 | 13.58 | 7.43244 | 0.619 | 0.01 | 28381 | 21.59 | 0.0386 |
| mid7-070-109 | 263 | 258.0 | 0.4744 | 7.7442 | 15.125 | 7.35415 | 0.621 | 0.01 | 28139 | 19.34 | 0.0394 |
| mid7-070-109 | 316 | 315.0 | 0.5744 | 8.8632 | 17.8 | 7.29446 | 0.62 | 0.009 | 28001 | 22.43 | 0.0405 |
| mid7-070-109 | 368 | 369.1 | 0.6695 | 11.7516 | 23.342 | 7.25511 | 0.621 | 0.01 | 27916 | 20.88 | 0.0422 |
| mid7-070-109 | 421 | 415.8 | 0.756 | 67.8609 | 77.956 | 7.27335 | 0.621 | 0.009 | 28008 | 22.71 | 0.0508 |
| mid7-070-109 | 474 | 422.9 | 0.7673 | 74.8209 | 84.32 | 7.25801 | 0.621 | 0.009 | 28010 | 24.82 | 0.0496 |
| B-065-104 | 64 | 63.5 | 0.1082 | 5.402 | 9.952 | 6.82054 | 0.604 | 0.014 | 25444 | 15.68 | 0.0335 |
| B-065-104 | 128 | 126.5 | 0.2039 | 5.4595 | 10.606 | 6.44985 | 0.615 | 0.013 | 24249 | 20.74 | 0.0357 |
| B-065-104 | 192 | 193.9 | 0.3047 | 5.6459 | 11.011 | 6.28434 | 0.618 | 0.012 | 23788 | 19.34 | 0.0376 |
| B-065-104 | 255 | 255.2 | 0.3937 | 5.8307 | 11.441 | 6.17026 | 0.62 | 0.012 | 23478 | 21.09 | 0.0394 |
| B-065-104 | 319 | 315.4 | 0.4798 | 6.3094 | 12.93 | 6.0861 | 0.621 | 0.011 | 23231 | 19.69 | 0.0411 |
| B-065-104 | 383 | 383.7 | 0.5778 | 7.2876 | 15.297 | 6.02291 | 0.621 | 0.011 | 23049 | 22.92 | 0.0422 |
| B-065-104 | 447 | 442.2 | 0.6674 | 9.612 | 19.738 | 6.03745 | 0.622 | 0.011 | 23190 | 21.66 | 0.0502 |
| B-065-104 | 511 | 512.5 | 0.7709 | 61.6326 | 69.882 | 6.01729 | 0.621 | 0.011 | 23162 | 23.06 | 0.0518 |
| B-065-104 | 575 | 518.1 | 0.77 | 61.0585 | 69.278 | 5.94476 | 0.622 | 0.011 | 22915 | 21.52 | 0.0527 |
| mutex10 | 90 | 90.0 | 0.1084 | 2.1439 | 13.616 | 4.82061 | 0.607 | 0.019 | 17919 | 20.6 | 0.039 |
| mutex10 | 180 | 181.5 | 0.2125 | 2.1266 | 16.448 | 4.68387 | 0.618 | 0.017 | 17347 | 21.02 | 0.0462 |
| mutex10 | 270 | 270.1 | 0.3044 | 2.2204 | 17.203 | 4.50745 | 0.62 | 0.015 | 16924 | 22.01 | 0.0557 |
| mutex10 | 360 | 354.8 | 0.39 | 2.4036 | 18.069 | 4.39639 | 0.622 | 0.015 | 16574 | 23.55 | 0.0648 |
| mutex10 | 450 | 450.4 | 0.4869 | 3.1435 | 18.9 | 4.32494 | 0.623 | 0.015 | 16404 | 23.7 | 0.0719 |
| mutex10 | 541 | 537.4 | 0.5821 | 4.687 | 21.0 | 4.33307 | 0.623 | 0.015 | 16501 | 23.98 | 0.0706 |
| mutex10 | 631 | 635.8 | 0.6869 | 9.5765 | 27.877 | 4.32165 | 0.623 | 0.014 | 16545 | 25.52 | 0.0735 |
| mutex10 | 721 | 716.1 | 0.7716 | 43.2678 | 61.439 | 4.3103 | 0.623 | 0.014 | 16532 | 28.62 | 0.0722 |
| mutex10 | 811 | 723.6 | 0.7727 | 42.5409 | 61.687 | 4.27113 | 0.623 | 0.014 | 16407 | 27.42 | 0.0723 |
| mutex40 | 109 | 107.8 | 0.1161 | 2.0732 | 13.118 | 4.30977 | 0.609 | 0.021 | 15990 | 22.29 | 0.0345 |
| mutex40 | 217 | 220.2 | 0.217 | 2.0348 | 15.347 | 3.94192 | 0.619 | 0.02 | 14491 | 21.66 | 0.0499 |
| mutex40 | 326 | 325.4 | 0.3065 | 2.0451 | 16.296 | 3.76858 | 0.623 | 0.02 | 13982 | 23.84 | 0.055 |
| mutex40 | 435 | 431.3 | 0.3984 | 2.4356 | 17.529 | 3.69442 | 0.623 | 0.018 | 13891 | 24.68 | 0.0649 |
| mutex40 | 544 | 541.9 | 0.4976 | 3.1459 | 18.488 | 3.67337 | 0.624 | 0.018 | 13906 | 26.02 | 0.0671 |
| mutex40 | 652 | 658.8 | 0.5942 | 5.6782 | 21.027 | 3.60757 | 0.625 | 0.018 | 13722 | 25.88 | 0.0679 |
| mutex40 | 761 | 759.6 | 0.6843 | 18.5166 | 35.042 | 3.60385 | 0.625 | 0.017 | 13771 | 26.3 | 0.0702 |
| mutex40 | 870 | 798.8 | 0.7091 | 38.7881 | 54.618 | 3.5508 | 0.625 | 0.017 | 13575 | 24.54 | 0.0681 |
| mutex40 | 978 | 863.1 | 0.7568 | 37.9484 | 56.116 | 3.50766 | 0.624 | 0.016 | 12746 | 27.07 | 0.0682 |
| memwalk30-128 | 87 | 86.7 | 0.1256 | 4.8318 | 12.998 | 5.7963 | 0.883 | 1.039 | 15121 | 36.21 | 0.124 |
| memwalk30-128 | 175 | 174.9 | 0.2387 | 5.3151 | 15.517 | 5.45869 | 0.928 | 1.1 | 13591 | 43.03 | 0.212 |
| memwalk30-128 | 262 | 262.9 | 0.345 | 5.3917 | 16.93 | 5.24933 | 0.937 | 1.095 | 13101 | 47.25 | 0.2889 |
| memwalk30-128 | 349 | 343.2 | 0.4406 | 5.7635 | 17.848 | 5.13616 | 0.943 | 1.102 | 12830 | 48.52 | 0.3685 |
| memwalk30-128 | 437 | 438.3 | 0.5529 | 5.9775 | 19.528 | 5.04581 | 0.946 | 1.092 | 12647 | 49.5 | 0.452 |
| memwalk30-128 | 524 | 520.0 | 0.6537 | 7.7435 | 22.731 | 5.02853 | 0.955 | 1.123 | 12544 | 49.36 | 0.539 |
| memwalk30-128 | 611 | 617.4 | 0.7682 | 48.2101 | 63.602 | 4.97725 | 0.955 | 1.114 | 12461 | 49.99 | 0.6226 |
| memwalk30-128 | 699 | 623.3 | 0.7744 | 50.445 | 66.22 | 4.96953 | 0.948 | 1.088 | 12536 | 50.41 | 0.6167 |
| memwalk30-128 | 786 | 702.6 | 0.8619 | 44.3256 | 66.296 | 4.90664 | 0.953 | 1.102 | 11870 | 50.27 | 0.6669 |

- **The real services' counters move with load.** Unique-id: CPI 0.60 → 0.82, MPKI 0.52 → 0.86 and instructions per
  request 116 k → 67 k from 3 300 to 29 800 req/s; recommendation: CPI 0.60 → 0.44 and 611 k → 416 k instructions per
  request. Per-request fixed costs (wake-ups, polling) are amortised as load rises. A counter signature must therefore
  be compared with the dictionary at the same ρ, not with a single per-service value.
- **bigsvc is flat in its counters** (CPI 0.60-0.63, MPKI 0.01-0.04 on the spin mixes), except where the work is
  memory-bound: memwalk30 at 128 MB reads CPI 0.88-0.96, MPKI 1.04-1.12, 36-50 MB of LLC and mbm rising linearly with
  λ (0.12 → 0.67 GB/s). The spin mixes hold 16-32 MB of LLC: the pod's own 256 MB memwalk array and the runtime.
- **Every bigsvc mix tops out at ρ ≈ 0.77**: the admission gate's 3 slots of 4 cores. The last one or two steps of each
  sweep are overloaded and dropped from its fit.

## 3. Overhead on this core (set B′; queue `i/queue-i3.sh`)

Set B′ (RESULTS-K 5.8: φ, φ_sibling and run_delay in the probe, no holder split; CMT; the interceptor's sampled
histogram timers; the window reads; the sampler; the scorer on-node with the step-2 dictionary) against the twin of
the same repetition, both services at mid, × {no neighbor, switch neighbor, network neighbor}, n = 5 × 30 s; the
network neighbor at 50 000 pps × 512 B with RPS to the victim's CPUs, calibrated first (A5).

### 3.0 Expected readings (written before the queue)

| cell | expected | measured |
|---|---|---|
| A5, softirq on CPU 1 at 50 000 pps | NET_RX on the victim's CPU at the configured rate (clnode170: the stream's receive processing landed on CPU 1 with RPS); CPUs 5,7 untouched || NET_RX on CPU 1 only: 939/s at 20 000 pps, **2 344/s at 50 000**, 3 749/s at 80 000 (≈ 21 packets per softirq); CPUs 5, 7: 0. The `/proc/stat` softirq share reads 0.1 % at 20 000 and 80 000 but 25 % at 50 000: tick-sampled, not usable as an amount (the probe's softirq hook is off in set B′) |
| unique-id, none | Δ CPU per request **+2 to +4 %** (clnode170 +4.8 %; the probe part is +0.9 % here against ~3.5 % there, the recorder's timers about the same share); Δ p99 within ±2 %; agents + scorer ≤ 0.01 cores; ≤ 2 ms per tick || **+2.3 % [+1.7, +2.9]**, but **Δ client p99 +44 % [+40, +49]**; agents + scorer 0.0093 cores, 1.68 ms per tick. CPU passes, p99 fails |
| unique-id, switch | **+2 to +4 %**; BPF time up with the switch rate (~4 ms per tick as in the K1-style cell) || **+8.6 % [+8.5, +8.8]**, Δ p99 **+105 % [+97, +114]**; 4.62 ms per tick. Both fail |
| unique-id, net | **+2 to +4.5 %** (clnode170 +5.2 %) || **+10.2 % [+10.0, +10.3]**, Δ p99 **+65 % [+63, +68]**. Both fail |
| recommendation, none / switch / net | **+0.5 to +1.5 %** each (clnode170 +1.3 / +1.6 / +0.7 %); p99: the client tail at this mid is wide (31 ms, §2.2), so the p99 gate may come out undecided || **+1.6 % [−0.9, +4.1] / +0.4 % [−0.3, +1.0] / +1.0 % [+0.7, +1.3]**: CPU passes in all three; Δ p99 +0.8 / +0.5 / −1.8 % with intervals ±5-7 %: undecided |
| verdicts | row 6 (nothing to explain) in nearly every window of the none cells; row 1 under the switch neighbor on recommendation (clnode170 1 643 of 1 645) || unique-id: row 6 in every scored window of all three cells (1 494 / 1 494 / 1 494 per cell, 5 × 299), including under switch and net, where clnode170 also read row 6. Recommendation: none 1 393 row 6, 99 row 7, 3 row 3; **switch 1 495 of 1 495 row 1** (clnode170 1 643 of 1 645); net: row 7 in 1 059 of 1 494 windows (row 4 167, row 3 114, row 6 147), so most windows under net are "no verdict" here (clnode170 15 of 1 645) |

### 3.1 Result (Nov 18 02:06-02:49 UTC; `results/i3/an/k5_setb.csv`, `k5_arms.csv`)

| service | load | neighbor | twin: CPU/req ms · victim cores · client p99 ms | Δ CPU/req % [95 %] | Δ client p99 % [95 %] | agents + scorer cores | BPF cores | ms per tick | CPU ≤ 5 % | p99 ≤ 2 % |
|---|---|---|---|---|---|---|---|---|---|---|
| unique-id | mid (16 568) | none | 0.0308 · 0.51 · 1.92 | +2.3 [1.7, 2.9] | +44.3 [39.7, 48.8] | 0.0093 | 0.0075 | 1.68 | pass | **fail** |
| unique-id | mid | switch | 0.0227 · 0.38 · 1.34 | +8.6 [8.5, 8.8] | +105.5 [96.5, 114.5] | 0.0057 | 0.0404 | 4.62 | **fail** | **fail** |
| unique-id | mid | net | 0.0276 · 0.46 · 1.94 | +10.2 [10.0, 10.3] | +65.3 [62.7, 67.9] | 0.0070 | 0.0077 | 1.47 | **fail** | **fail** |
| recommendation | mid (10 482) | none | 0.0955 · 0.94 · 33.9 | +1.6 [−0.9, 4.1] | +0.8 [−3.9, 5.5] | 0.0078 | 0.0062 | 1.40 | pass | undecided |
| recommendation | mid | switch | 0.0800 · 0.84 · 53.1 | +0.4 [−0.3, 1.0] | +0.5 [−6.4, 7.3] | 0.0068 | 0.0692 | 7.59 | pass | undecided |
| recommendation | mid | net | 0.0996 · 1.04 · 36.1 | +1.0 [0.7, 1.3] | −1.8 [−6.6, 3.0] | 0.0077 | 0.0064 | 1.41 | pass | undecided |

Paired per repetition (B′ against the twin of the same rep), n = 5 × 30 s. Agents + scorer ≤ 1 % of a core per node:
**passes in all six cells** (0.0057-0.0093). BPF: 50 ns per switch quiet, 80-92 under the switch neighbor, 42-73 under
net (bpf_stats over all CPUs).

- **unique-id fails on this core, and not through the probe.** The probe alone cost +0.9 % / +1.1 % with p99 flat
  (§2.2, the same mid and neighbors); set B′ costs +2.3 % quiet, +8.6 % under switch, +10.2 % under net, and the
  client p99 rises 44-105 %. On clnode170 the same set cost +4.6-5.2 % with p99 +1.7 % / +1.9 % (none, switch) and
  +54 % (net) (RESULTS-K 5.8).
- **The added tail is outside the handler.** The recorder's in-handler p99 under B′ is 9-30 µs and its p50 4.9 µs; the
  client's p99 grows by 0.9-1.4 ms. Requests wait on unique-id's one core while something else runs there, which the
  server's timer does not see (an unstamped Lat_e carries no wait, Phase C).
- **Candidates (not measured yet), all of them on the victim's CPU:** resctrl monitoring, which here also reads MBM
  (clnode170's Haswell had CMT only) and writes the RMID (PQR_ASSOC) on every switch into and out of the monitored
  groups, the victim's and the `aggr` holder's, so it grows with the switch rate as the +8.6 % under switch does; the
  recorder's sampled timers and its `cpu`/`mhz` window reads; the sampler. A decomposition (B′ minus one part at a time
  on unique-id, as RESULTS-K 5.5's ladder) would say which.
- **Recommendation passes CPU per request in all three cells** (+0.4-1.6 %); p99 is undecided because its client tail
  at this mid is wide (34-53 ms, §2.2: most of it upstream of the service).
- **Verdicts:** recommendation under the switch neighbor reads row 1 in every window, as on clnode170. Under net, 71 %
  of its windows fall through to row 7 ("no verdict"; clnode170: 1 %), the rest rows 3, 4 and 6: the network neighbor
  is not cleanly named on this node.
- **Restore after both queues checked:** 46 deployments equal to the snapshots, pods on the same nodes, node-3 back to
  its §0.2 state, `HWP_REQUEST` unchanged at every check.

## 4. Stop point 2 decisions: B′ decomposed and corrected on unique-id; recommendation's p99 (queue `i/queue-i4.sh`)

**Found before the queue (1b):** both recorders read the clock every window from sysfs cpufreq, and node-0 to node-3
have no cpufreq driver, so they fall back to parsing **/proc/cpuinfo**: 269 KB on 144 CPUs, **3.3 ms of CPU per read**
measured on node-3 pinned to one CPU, i.e. a 3.3 ms stretch every 100 ms on the victim's own CPU (the recorder's
threads share its cpuset). Set B′ turns the read on for the C++ recorder (`reads=cpu,mhz`, unique-id) and not for Go
(`q,g`, recommendation). The step-2 sweeps (`sweep`: `cpu,mhz` / `cpu,mhz,q,g`) and **bigsvc's runtime recorder
(unconditionally, every window)** read it too, so the step-2 dictionaries carry it. clnode170 had a cpufreq driver
(`intel_cpufreq`): one sysfs file per CPU of the cpuset. **The fix:** the recorder reads `cpu` only, and the agent reads
APERF/MPERF of the victim's CPUs once per tick (`--mhz-cpus`, `/dev/cpu/N/msr`; smoke test: 2 400 MHz at 99.8 % C0 on
two busy cores, idle cores near 0 % C0).

**Moved first (decision 2):** the profile's nine HotelReservation pods off node-3 (RUNBOOK §18); node-3 now holds only
kube-system's daemon pods; the profile's stack answers through its frontend.

Arms (each paired with the twin of the same repetition; the in-handler p99 against the timers-only arm `intercept` of
the same repetition, because the twin times nothing): `defBp`; minus one part: `Bp-noRdt` (no resctrl), `Bp-noTimers`
(interceptor count + slot, no histogram timers), `Bp-noReads`, `Bp-noSampler`, `Bp-noScorer`; the fixes:
`Bp-fixedReads`, `Bp-rdtNbr` (monitor groups for the neighbors only), `Bp-fix2` (both). Every arm records the switch
rate of each victim CPU (`/proc/schedstat` pcount) and the cpusets of the agent's and the scorer's threads.

### 4.0 Expected readings (written before the queue)

| cell / arm | expected | measured |
|---|---|---|
| unique-id quiet, `defBp` | as in §3: +2.3 % CPU, client p99 +44 % || +2.7 % [2.1, 3.2] CPU, client p99 **+46.1 % [42.4, 49.7]** (rerun Nov 18 20:56-21:29 UTC): as in §3 |
| `Bp-noReads`, `Bp-fixedReads` | **p99 back to within a few % of the twin**: the 3.3 ms stretch every 100 ms is the +1 ms; CPU +0.5-2 % (a stretch batches requests, which on this core makes each cheaper, so removing it need not lower CPU per request by its full 33 ms/s) || switch cell: **+3.2 % / +3.3 % CPU, client p99 +1.0 % / +1.0 %** against `defBp`'s +9.6 % and +107 %: as expected (quiet cell: §4.1, rerun) |
| `Bp-noRdt` quiet | CPU −0.5 to −1 point against `defBp` (an RMID write on each switch of a monitored task) || +5.7 % [5.1, 6.3], p99 +46.7 %: not lower than `defBp`. With the read on, CPU per request in the quiet cell varies from +2.1 to +5.7 % between arms that differ in nothing else that matters (the 3.3 ms stretch batches requests, and how much depends on the arm's other timing); the decomposition of CPU is clean only once the read is off |
| `Bp-noRdt` switch | **CPU −3 to −6 points** against `defBp`: under the switch neighbor the victim's CPU switches between the victim and `aggr` several hundred thousand times a second, each with an RMID write || **+9.2 % against `defBp`'s +9.6 %: −0.4 points, not 3-6.** The RMID writes are cheap here; the switch cell's excess was the reads |
| `Bp-rdtNbr` | quiet: about `Bp-noRdt`; **switch: little better than `defBp`**, because a switch between the victim (default RMID) and a monitored neighbor still changes PQR_ASSOC || switch: +9.5 % (as `defBp`, as expected) |
| `Bp-noTimers`, `Bp-noSampler`, `Bp-noScorer` | each within ±1 point of `defBp`; the scorer and sampler run on CPU 53 || switch: +9.5 / +9.6 / +9.5 %, each within 0.1 points of `defBp` |
| `Bp-fix2` | quiet: CPU ≤ +2 %, p99 ≤ +5 %; switch: CPU as `Bp-rdtNbr` minus the reads || switch: **+3.3 % [3.1, 3.4], client p99 +0.9 % [0.7, 1.2]** |
| cpusets | agent (and its sampler) and scorer threads: `53` only || agent (all threads, the sampler included) and scorer: **`53`** in every arm of every cell; `k5ctl.py` now pinned to 53 too (it read /proc at arm boundaries unpinned) |
| recommendation 6 000 and mid, `defBp` / `Bp-fix2` | CPU +0.5-1.5 %; in-handler p99 within ±5 % of timers-only; client p99 at 6 000 a few ms (below the knee), at mid 30-50 ms || CPU −0.2 to +1.3 % in all ten arm-cells (pass); in-handler p99 against timers-only +7 to +28 % with intervals ±30-80 points: undecided; **client p99 at 6 000 is 16 ms**, not a few ms (§4.2) |
| re-sweeps (`sweepF`) | unique-id's CPU per request a few % below step 2's at the same rate; recommendation's unchanged within ±2 % (its sweep read /proc/cpuinfo on two cores of 2) || unique-id: CPU per request **1-12 % below** step 2 (12 % at 3 300 req/s, 1-4 % above 9 900) and **CPI 0.83-0.89 instead of 0.60-0.82**: the /proc/cpuinfo formatting was low-CPI kernel work counted in the victim's cgroup. Recommendation: CPU per request 0.1-3.8 % lower, CPI 0.46-0.66 instead of 0.44-0.60 |

### 4.1 unique-id: which part of B′ carries the +1 ms (switch cell Nov 18 03:13-03:47 UTC; quiet cell 20:56-21:29 UTC)

**The recorder's per-window clock read carries it: with that read off (or moved to the agent) B′ costs +3.2-3.3 % CPU
per request and +1.0 % client p99 under the switch neighbor, against +9.6 % and +107 % with it.** The other parts of
B′ removed one at a time change CPU per request by ≤ 0.4 points and leave the tail where it is.

| arm (switch neighbor, mid, n = 5) | Δ CPU/req % [95 %] | Δ client p99 % [95 %] | client p99 ms | in-handler p99 ms | Δ in-handler p99 vs timers-only % | victim CPU switches/s |
|---|---|---|---|---|---|---|
| twin (`none`) | — | — | 1.34 | — | — | 428 500 |
| timers only (`intercept`) | +0.9 [0.8, 1.0] | +0.2 [0.0, 0.5] | 1.33 | 0.011 | — | 426 500 |
| `defBp` | +9.6 [9.4, 9.8] | +106.9 [101.7, 112.0] | 2.75 | 0.010 | −14.6 [−20.4, −8.8] | 371 300 |
| − resctrl (`Bp-noRdt`) | +9.2 [9.0, 9.4] | +105.7 [101.9, 109.6] | 2.74 | 0.010 | −10.9 | 373 100 |
| − timers (`Bp-noTimers`) | +9.5 [9.4, 9.7] | +106.6 [105.1, 108.2] | 2.75 | — | — | 371 700 |
| **− reads (`Bp-noReads`)** | **+3.2 [3.0, 3.4]** | **+1.0 [0.8, 1.2]** | 1.34 | 0.010 | −12.7 | 385 200 |
| − sampler (`Bp-noSampler`) | +9.6 [9.3, 9.9] | +106.8 [102.0, 111.5] | 2.75 | 0.009 | −16.5 | 371 000 |
| − scorer (`Bp-noScorer`) | +9.5 [9.3, 9.6] | +107.0 [102.0, 111.9] | 2.75 | 0.009 | −16.5 | 371 200 |
| **fixed reads (`Bp-fixedReads`)** | **+3.3 [3.1, 3.4]** | **+1.0 [0.9, 1.2]** | 1.34 | 0.010 | −12.7 | 384 900 |
| resctrl neighbors only (`Bp-rdtNbr`) | +9.5 [9.4, 9.6] | +104.2 [99.3, 109.0] | 2.72 | 0.009 | −18.2 | 371 600 |
| **both fixes (`Bp-fix2`)** | **+3.3 [3.1, 3.4]** | **+0.9 [0.7, 1.2]** | 1.34 | 0.010 | −14.4 | 385 300 |

| arm (no neighbor, mid, n = 5) | Δ CPU/req % [95 %] | Δ client p99 % [95 %] | client p99 ms | in-handler p99 ms | Δ in-handler p99 vs timers-only % | victim CPU switches/s |
|---|---|---|---|---|---|---|
| twin (`none`) | — | — | 1.92 | — | — | 16 800 |
| timers only (`intercept`) | +0.2 [−0.1, 0.5] | +0.2 [−0.3, 0.7] | 1.92 | 0.029 | — | 16 800 |
| `defBp` | +2.7 [2.1, 3.2] | +46.1 [42.4, 49.7] | 2.79 | 0.029 | +0.1 | 17 000 |
| − resctrl (`Bp-noRdt`) | +5.7 [5.1, 6.3] | +46.7 [44.7, 48.7] | 2.80 | 0.030 | +3.0 | 16 900 |
| − timers (`Bp-noTimers`) | +2.1 [1.4, 2.9] | +45.6 [43.8, 47.4] | 2.78 | — | — | 17 000 |
| **− reads (`Bp-noReads`)** | **−1.6 [−2.1, −1.0]** | **−0.6 [−1.5, 0.4]** | 1.90 | 0.028 | −4.1 | 16 800 |
| − sampler (`Bp-noSampler`) | +2.8 [2.4, 3.2] | +45.2 [42.3, 48.2] | 2.78 | 0.029 | +1.5 | 17 000 |
| − scorer (`Bp-noScorer`) | +3.0 [2.6, 3.5] | +43.1 [40.2, 45.9] | 2.73 | 0.030 | +3.0 | 17 000 |
| **fixed reads (`Bp-fixedReads`)** | **−1.1 [−1.4, −0.9]** | **−0.4 [−0.9, 0.1]** | 1.90 | 0.028 | −2.8 | 16 800 |
| resctrl neighbors only (`Bp-rdtNbr`) | +5.4 [5.2, 5.6] | +46.1 [43.9, 48.3] | 2.79 | 0.030 | +3.0 | 17 000 |
| **both fixes (`Bp-fix2`)** | **+1.3 [1.0, 1.7]** | **+0.5 [0.0, 1.0]** | 1.92 | 0.029 | +1.5 | 16 800 |

- **The tail is the reads, in both cells.** Every arm with the clock read on has client p99 2.72-2.80 ms; every arm
  without it the twin's (1.34 ms under switch, 1.90-1.92 ms quiet). Quiet, the corrected set costs −1.1 % (fixed reads)
  to +1.3 % (both fixes) CPU per request; a reading below the twin is within what the batching of this one-core service
  moves between arms. The in-handler p99 is the same 9-11 µs in all of them: the 3.3 ms stretch delays requests before the
  handler, which the in-handler timer cannot see; the client can.
- **The RMID write is cheap.** `defBp` − `Bp-noRdt` = 0.4 points of CPU per request at ~371 000 switches/s on the
  victim's CPU; 0.4 % of the victim's 0.376 cores over 371 000 switches/s bounds the RMID write at **≈ 4 ns per
  switch**. Neighbors-only monitoring buys nothing measurable, as expected (a switch between the victim's default RMID
  and a monitored neighbor still writes PQR_ASSOC).
- **The corrected set** for step 4 is **B′ with fixed reads** (`Bp-fixedReads`: the recorder reads `cpu`, the agent
  reads the victim CPUs' APERF/MPERF). `Bp-fix2` measures the same, so the victim stays monitored (CMT on the victim is
  an input of C4′ and C3′). Its remaining +3.3 % under the switch neighbor is the probe (+1.1 %, §2.2), the timers
  (+0.9 %) and the rest of the set at this switch rate.
- **Pinning (1d):** the agent's threads (its sampler included) and the scorer ran on CPU 53 in every arm; `k5ctl.py`,
  which reads /proc at arm boundaries, ran unpinned until now and is pinned to 53 from the rerun on.

### 4.2 recommendation: CPU per request, and why its p99 cannot be gated at n = 5 (queue i4, Nov 18 03:47-04:56 UTC)

| load | neighbor | arm | Δ CPU/req % [95 %] | client p99 ms | in-handler p99 ms | Δ in-handler p99 vs timers-only % [95 %] |
|---|---|---|---|---|---|---|
| 6 000 | none | `defBp` | −0.1 [−1.4, 1.2] | 16.2 | 1.64 | +12.9 [−37.8, 63.6] |
| 6 000 | none | `Bp-fix2` | +0.7 [−1.6, 2.9] | 15.7 | 1.67 | +17.6 [−51.2, 86.4] |
| 6 000 | switch | `defBp` | +0.9 [0.0, 1.8] | 16.8 | 3.07 | +28.1 [−44.7, 101.0] |
| 6 000 | switch | `Bp-fix2` | +1.3 [−0.2, 2.9] | 15.8 | 2.86 | +16.8 [−29.9, 63.5] |
| mid | none | `defBp` | +0.3 [−1.9, 2.5] | 28.8 | 6.55 | +18.8 [−27.6, 65.2] |
| mid | none | `Bp-fix2` | −0.2 [−2.1, 1.8] | 28.9 | 6.45 | +17.2 [−30.7, 65.0] |
| mid | switch | `defBp` | +0.2 [−1.8, 2.3] | 41.8 | 18.7 | +7.4 [−14.7, 29.6] |
| mid | switch | `Bp-fix2` | +0.2 [−1.6, 2.0] | 42.1 | 19.1 | +10.9 [−20.9, 42.6] |
| mid | net | `defBp` | +0.9 [−0.7, 2.5] | 29.9 | 7.60 | +12.8 [−27.5, 53.1] |
| mid | net | `Bp-fix2` | +0.6 [−1.1, 2.3] | 29.6 | 7.60 | +12.7 [−27.6, 53.1] |

- **CPU per request passes in every cell** (−0.2 to +1.3 %).
- **Why client p99 at mid cannot decide a 2 % gate at n = 5.** The client's p99 at mid is 29-42 ms, of which
  recommendation's own handler is 6.5-19 ms; the rest is queueing upstream (frontend and its network path on node-1/2),
  which the overhead cannot touch but which moves 5-15 % from repetition to repetition. With n = 5 the 95 % interval of
  a paired difference is ±2.78 × sd ÷ √5 ≈ ±1.2 sd: resolving 2 % needs the repetitions' differences to have an sd
  under ~1.6 %, several times smaller than the chain gives. The gate would measure the chain's noise, not the agent.
- **The in-handler p99 does not decide it at this n either.** Its window p99s (completion-weighted median over a 30 s
  arm) move ±30-80 % between repetitions at both loads; every interval spans 0 and the gate is undecided everywhere.
  The point estimates (+7 to +28 %) are all positive; whether that is real needs a longer arm or a per-request
  statistic. **6 000 req/s is not below the chain's knee for the client**: 16 ms client p99 against 1.6 ms in the
  handler.

### 4.3 The network neighbor's softirq on this platform (decision 4)

NET_RX per second on the watched CPUs (the victim's, their siblings and CPU 53) under the network neighbor at
50 000 pps × 512 B with RPS to the victim's CPUs, at each node's mid:

| victim | clnode270 | clnode170 (RESULTS-K 5.8 cells) |
|---|---|---|
| recommendation (CPUs 5, 7) | **12 560** (8 390 on 5, 3 340 on 7) | 7 800 |
| unique-id (CPU 1) | **18 800** | 12 500 |

The same stream yields 1.5-1.6× the softirq invocations here (smaller NAPI batches at this rate, and each victim's own
traffic is larger at the higher mid). This is why the net neighbor reads differently on this node: under it 71 % of
recommendation's windows fall to row 7 (§3), the softirq case with no row in production mode, which is the motivation
for the escalation arm (step 6).

### 4.4 Check 1: what else runs per window on the victim node (Nov 18 21:30 UTC)

Read in the sources, then traced on node-3 (`strace -f -y`, `openat`/`read`/`pread64`, 0.3 s = three windows, during
a 10 s `Bp-fix2` cell; not a measurement), and for bigsvc's pod during a sweep with `/proc/<pid>/io` (no ptrace):

| process (cpus) | per window, before | found in the trace | fixed |
|---|---|---|---|
| unique-id's recorder (C++, CPU 1) | `getrusage`; **/proc/cpuinfo** (`mhz`, no cpufreq driver) | its 25-byte control file only (reads `cpu`) | yes: `mhz` off in the corrected set; the agent reads the clock |
| recommendation's recorder (Go, 5,7) | runtime/metrics `q,g` in process; **/proc/cpuinfo** only if `mhz` is asked (the step-2 sweep did) | no file reads | yes: `mhz` off in `sweepF` and the corrected set |
| bigsvc's runtime recorder (1,3,5,7) | **/proc/cpuinfo every window, unconditionally** | `big_proc` on `k123i3`: 4 bytes/s read during a sweep (the old read is ~2.7 MB/s) | yes: `GORDION_READS` opt-in, default off (`k123i3`) |
| agent and its sampler (53) | `/proc/stat` every tick (14 KB, every CPU's line), the per-CPU BPF array (144 entries), resctrl and cgroup files, APERF/MPERF of the victim's CPUs | as listed; 2 MSR reads per victim CPU per tick (an IPI each) | not on the victim's CPUs; kept |
| scorer (53) | the recorder and agent files | no reads in the 0.3 s (it polls) | — |
| controller `k5ctl.py` | `/proc/stat`, `/proc/softirqs`, `/proc/schedstat` at arm boundaries only | — | was unpinned; pinned to CPU 53 |

Nothing else reads a file proportional to the CPU count on the victim's CPUs. The first trace's bigsvc entry matched
the tracing shell itself (`pgrep -f` self-match); `big_proc` was then identified by its cpuset and checked as above.

### 4.5 The dictionaries for step 4 (re-swept Nov 18 21:35-22:52 UTC; `results/i4/dict`, `results/i5/dict`)

unique-id and recommendation re-swept with `sweepF` (queue i4), the eight bigsvc mixes on `big_ctr:k123i3` with
`sweepF` (queue i5): the same schedules, mids, memwalk step counts and fit (50 windows per ρ bin, step interpolation)
as step 2; the only difference is that no recorder reads /proc/cpuinfo, and the agent reads the victim CPUs' clock.

| dictionary | F̄ ms (step 2 → now) | c̄ ms | K ms | ρ bins | median change per step up to ρ 0.7: CPU/req · p50 · p99 · CPI | LLC MB (step 2 → now) | mbm GB/s (step 2 → now) |
|---|---|---|---|---|---|---|---|
| unique-id | floor 0.014 → 0.015 | 0.0464 → 0.0436 | — | 15 → 14 | −1 to −12 % · +7-10 % · 0 (−80 % at one step) · +8 to +41 % | — | — |
| recommendation | floor 0.086 → 0.094 | 0.190 → 0.187 | — | 15 → 17 | −0.1 to −3.8 % · 0 to +10 % · −11 to 0 % · +3 to +11 % | — | — |
| bigsvc uniform | 2.270 → 2.266 | 4.277 → 4.276 | 5.082 | 17 | −1.5 · −0.3 · −0.1 · +1.4 % | 26.5 → 21.2 | 0.084 → 0.050 |
| short | 1.893 → 1.908 | 1.916 | 1.240 | 15 | −1.2 · +1.0 · −0.2 · +1.6 % | 21.8 → 9.9 | 0.134 → 0.041 |
| long | 9.617 → 9.627 | 10.352 | 6.168 | 17 | −1.7 · +0.4 · −0.1 · +1.3 % | 20.3 → 4.7 | 0.043 → 0.004 |
| mid7 | 6.544 → 6.613 | 7.028 | 4.188 | 17 | −1.6 · −0.1 · −0.4 · +1.1 % | 19.3 → 5.5 | 0.039 → 0.019 |
| B | 5.404 → 5.507 | 5.788 | 3.450 | 15 | −1.7 · +0.3 · +0.5 · +1.1 % | 20.7 → 7.3 | 0.039 → 0.006 |
| mutex10 | 2.067 → 2.143 | 4.095 | 4.918 | 15 | −1.4 · +0.3 · +0.4 · +1.3 % | 23.6 → 9.3 | 0.065 → 0.013 |
| mutex40 | 2.039 → 2.058 | 3.412 → 3.413 | 4.174 → 4.176 | 15 | −1.4 · +0.8 · +0.1 · +1.3 % | 24.7 → 8.7 | 0.065 → 0.028 |
| memwalk30, 128 MB | 5.383 → 5.308 | 4.240 | 4.248 → 4.247 | 15 | −2.2 · −0.4 · −1.1 · +2.3 % | 47.9 → 48.8 | 0.329 → 0.284 |

- **The latency terms barely move** (F̄ within 2 %, p50 and p99 per step within ±1 % on bigsvc): at a median over a
  45 s step a 3.3 ms stretch every 100 ms on one of four cores is mostly averaged out. **The counter column moves a
  lot:** the spin mixes' LLC occupancy falls from 19-27 MB to 5-21 MB and their mbm by 2-10×, because formatting 269 KB
  of /proc/cpuinfo inside the service's cgroup touched memory under the service's RMID, and its instructions ran at a low
  CPI; on unique-id the read had pulled CPI from ~0.85 down to 0.60-0.82. Step 2's counter column (§2.3) over-states
  occupancy and bandwidth and under-states CPI for every service; **step 4 reads its amounts against the dictionaries in
  this section** (`results/i4/dict/dict-{uid,rec}.json`, `results/i5/dict/dict-syn-*.json`, counter columns in the two
  `i2_counters.csv`).
- CPU per request falls 1.2-2.2 % on bigsvc, more than the 0.8 % the stretch's own CPU would explain: the formatting's
  cost and its cache misses both landed in the service's cgroup.
- The restores of i4, i4b and i5 checked clean (46 deployments equal to the snapshots, pods on their nodes, node-3 with
  no agent, mount or cgroup left, `HWP_REQUEST` unchanged at every check). Raw data on node-0 and in
  `C:/coding-workspace/archive/i4i5-raw-20261118-laptop.tgz` (sha256 cb7995cd…d2b5, equal on both).

## 5. Ice-Lake-only signatures (step 4; queue `i/queue-i6.sh`)

One source at a time against a twin, at mid, one 300 s cell each, arm `sigF`: the corrected set's reads (the recorder
reads `cpu`, the agent the victim CPUs' APERF/MPERF), the probe with every flag (φ, R_foreign, φ_sibling, wake-ups,
softirq), the counters with the stall columns, CMT + MBM, the sampler; scorer offline. The dictionaries are §4.5's
(and a new memwalk30 at 24 MB sweep for C4′). For each source the amount check D′ = I_g·F̄ + h(I_g·ρ)·I_g·K (RESULTS-K
6.3) against the measured p50.

**The stall columns** (agent `--perf-td 1`, raw Ice Lake server events in each cgroup's group): memory-bound share =
CYCLE_ACTIVITY.STALLS_MEM_ANY ÷ cycles, DRAM-bound = .STALLS_L3_MISS ÷ cycles, core-bound = (.STALLS_TOTAL −
.STALLS_MEM_ANY) ÷ cycles: the stall-cycle proxies for top-down level 2, because the kernel's `topdown-*` events need
a `slots` group leader that the agent's per-cgroup counting groups cannot carry. Checked on node-3 (Nov 18 22:58 UTC,
temporary cgroups): a spin loop reads IPC 3.68, memory-bound 0.023, DRAM-bound 0.000; a `stress-ng --stream` reads IPC
2.25, memory-bound 0.137, DRAM-bound 0.047 (a prefetched stream stalls little even at high bandwidth).

**C5′ (harvester) is not run on node-3:** HarvestContainers addresses CPUs below 64 only and node-3 has 144 online
(SMT on); the stop-point-1 decision puts HC on node-4 with CPUs 64-71 offline, which is step 7.

### 5.0 Expected readings (written before the queue)

| cell | expected | measured |
|---|---|---|
| C6′ unique-id twin vs twin | CPU per request ±2 %, p50 ±5 %, CPI ±2 %, MPKI ±5 %, occupancy and mbm ±10 %, φ ≈ 0, I_g 0.97-1.03 | CPU +0.4 %, p50 0.0 % (5.38 µs both), CPI +0.7 %, MPKI +0.2 %, φ 0.001, I_g 1.022 / 1.026 ✓; **occupancy −17 % (4.4 → 3.7 MB) and mbm −60 % (0.42 → 0.17 GB/s) ✗**: at unique-id's size these two columns sit at their noise floor (~1 MB, ~0.3 GB/s) |
| C1′ unique-id, matrixprod on its SMT sibling (CPU 73) | **core-bound share up** (from ≤ 0.05 to 0.1-0.3), memory-bound and DRAM-bound flat, **φ_sibling ≈ 1**, φ 0, CPI +20-50 %, MPKI, occupancy and mbm flat; I_g 1.2-1.6; D′ within ±20 % of the measured p50 | core-bound 0.139 → 0.175 (+25 %; the twin is already 0.14, not ≤ 0.05) ✓, memory-bound +12 % (0.34 → 0.38) ~, DRAM-bound −6 % ✓, **φ_sibling 1.000** ✓, φ 0.001 ✓, CPI +52 % (just above +50 %), MPKI +1 % ✓, occupancy +4 % ✓, mbm −65 % (noise floor); **I_g 1.150 ✗** (below 1.2), D′ 6.2 µs against p50 7.9 µs: −22 % ✗ (one histogram bin, §5.1); **the victim core's clock 1.71 → 2.40 GHz** (§5.1) |
| C6′ bigsvc uniform twin vs twin | as unique-id's twins; I_g 0.97-1.03 | CPU −0.1 %, p50 0.0 %, CPI 0.0 %, MPKI −0.5 %, occupancy +2 %, mbm −5 %, φ 0, I_g 1.005 / 1.004 ✓; D′/p50 1.04 |
| C3′ memwalk30 at 128 MB alone | against the uniform twin: **MPKI ~1.1 (×30), own mbm ~0.45 GB/s, occupancy ~48 MB, memory- and DRAM-bound up**; the neighbor columns zero; φ flat; I_g ≈ 1 against its own dictionary | MPKI 0.035 → 1.10 (×31) ✓, own mbm 0.055 → 0.38 GB/s ✓, occupancy 26.5 → 50.3 MB ✓, memory-bound 0.021 → 0.357 and DRAM-bound 0.008 → 0.189 ✓; neighbor columns 0 ✓; φ 0 ✓; I_g 1.011 against its own dictionary ✓, D′/p50 1.03 ✓ |
| C7 memwalk30 at 128 MB, stream ×2 / ×4 / ×8 on CPUs 9-23 odd | **neighbor mbm high and rising** (~10-20, ~20-40, ~40-80 GB/s); our memory-bound and DRAM-bound shares up with N; our occupancy roughly held (≥ 70 % of C3′'s); φ flat; I_g 1.05 / 1.1 / 1.2+; D′ within ±20 % of p50. Distinct from C4′ (our occupancy taken, the neighbor's mbm modest) and from C1′ (core-bound, not memory-bound) | neighbor mbm **34.2 / 63.4 / 102.3 GB/s** (1.3-1.7× the expected ranges), neighbor occupancy 50-52 MB; our memory-bound 0.357 → 0.437 / 0.459 / 0.502 ✓ and DRAM-bound 0.189 → 0.283 / 0.307 / 0.358 ✓, rising with N; **our occupancy 50 → 1.8 / 1.2 / 0.9 MB ✗ (not held: taken as completely as under C4′)**; MPKI 1.10 → 1.78 / 1.83 / 1.87; CPI +15 / +20 / +30 %; core-bound −11 / −14 / −20 %; φ 0 ✓; clock flat ✓; I_g 1.149 / 1.200 / 1.305 (above the expected 1.05 / 1.1 / 1.2); p50 5.90 → 8.26 / 9.34 / 15.33 ms; **D′/p50 1.06 / 1.14 ✓, 1.81 ✗ at ×8** (§5.1). Distinct from C1′ ✓ (core-bound down, φ_sibling 0); **distinct from C4′ only by the neighbor's mbm** (the victim-side discriminator failed) |
| C3′ order control (memwalk 128 MB alone again) | within ±3 % of the first | within 0.0-0.7 % on every column (mbm +11 % at 0.38 → 0.42 GB/s) ✓ |
| memwalk30 at 24 MB alone, then cache neighbor ×4 on CPUs 9-15 (C4′) | twin: occupancy ~24 MB, MPKI low (fits the 54 MB LLC); **C4′: our occupancy down by half or more, our MPKI and own mbm up, the neighbor's occupancy high, its mbm modest**; I_g 1.2-2; D′ within ±20 % of p50 | twin: occupancy 29.4-30.4 MB (above the ~24 expected: the other endpoints' data), MPKI 0.039, DRAM-bound 0.006 ✓; C4′: **occupancy 5.2 MB (−83 %) ✓, MPKI 2.17 (×56) ✓, own mbm 1.00 GB/s (×18) ✓, neighbor occupancy 46 MB ✓, neighbor mbm 5.4 GB/s ✓** (2.1 cores for 4 workers, as K6); memory-bound 0.276 → 0.500, DRAM-bound 0.006 → 0.299, CPI +46 %, core-bound −29 %; I_g 1.451 ✓; p50 4.14 → 15.75 ms (×3.80); D′ 17.2 ms, **D′/p50 1.09 ✓** |
| C4′ order control | within ±3 % of the first 24 MB twin | within 0.1 % (MPKI −5 % at 0.039 → 0.038) ✓ |
| calibration at 24 MB | 30-45 ns per step (most of 24 MB in the LLC): N ≈ 90 000-130 000 | **124 419 steps at 32.15 ns** ✓ (`results/i6/memwalk-steps.txt`) |

### 5.1 Results (Nov 18 23:01 - Nov 19 00:41 UTC; `results/i6/an/i6_sig.csv`, `i6_sig.txt`; `analysis/i6_sig.py`)

**Six of the six sources measured have a signature that is unique among the seven** (C5′ not run, above). One of the
six rests on a single column: on the victim's own columns the stream neighbor (C7) and the cache neighbor (C4′)
coincide, and only the neighbor's MBM separates them (34-102 against 5.4 GB/s). Every cell ran 300 s at mid, one arm
(`sigF`), 2 999-3 000 recorder windows; the amounts are against §4.5's dictionaries and the new 24 MB one
(`results/i6/dict/dict-syn-memwalk30-24.json`: F̄ 4.125 ms, c̄ 4.244 ms, K 4.252 ms, 17 ρ bins to 0.79).

**The signature table.** Per source, each column against its twin (ratio − 1 unless an absolute value is more
readable; the twins' own spread is the last row). CPU per request carries I_g in brackets; "nb" is the neighbor's
cgroup (`gordion-aggr`); D′/p50 is the amount check.

| source (cells) | CPU/req (I_g) | in-handler p50 | CPI | MPKI | memory-bound | DRAM-bound | core-bound | own occupancy | own mbm | nb occupancy / mbm | φ / φ_sibling | softirq ms/s | clock | D′/p50 | unique? |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| **C1′ SMT sibling** (unique-id, matrixprod on CPU 73) | +12 % (1.15) | +48 % (5.4 → 7.9 µs) | **+52 %** | +1 % | +12 % | −6 % | **+25 %** | +4 % | floor | 0.3 MB / 0.01 GB/s | 0.001 / **1.000** | 138 → 174 | **1.71 → 2.40 GHz** | 0.78 | **yes:** the only source with φ_sibling = 1 and core-bound up while MPKI, DRAM-bound and occupancy stay flat |
| **C2 net neighbor** (§3's B′ cells, no counter or stall columns) | uid −10 % (0.0307 → 0.0276 ms), rec +4 % | — | — | — | — | — | — | — | — | — | — | node softirq 0.19 → 0.41 cores (uid; NET_RX 16 600 → 18 800/s), 0.27 → 0.47 (rec) | (rises, §5.1 clock) | — | **yes:** the only source that moves the softirq column; no counter column available in those arms (step 6 adds them while flagged) |
| **C3′ own 128 MB working set** (memwalk128 vs the uniform twins) | +10 % vs uniform; **I_g 1.01 on its own dictionary** | +61 % | +52 % | **×31** (1.10) | ×17 (0.36) | ×24 (0.19) | −35 % | +90 % (50 MB) | ×7 (0.38 GB/s) | 0 / 0 | 0 / 0 | +9 % | flat | 1.03 | **yes:** the only source whose own cache columns move with every neighbor column at zero, and I_g = 1: the service's own behaviour, not inflation |
| **C4′ cache neighbor** (memwalk24, `--cache 4` on 9-15) | +44 % (1.45) | ×3.80 (4.14 → 15.75 ms) | +46 % | **×56** (2.17) | +81 % (0.50) | ×50 (0.30) | −29 % | **−83 %** (5.2 MB) | ×18 (1.00 GB/s) | **46 MB / 5.4 GB/s** | 0 / 0 | 16.5 → 23.0 | flat | 1.09 | **yes, by the neighbor column:** nb occupancy high, nb mbm modest (C7's victim-side columns are the same, below) |
| C5′ harvester | not run on node-3 (HC addresses CPUs < 64; step 7 on node-4) | | | | | | | | | | | | | | — |
| **C6′ twin vs twin** (uid a/b, uniform a/b, memwalk128 a/b, memwalk24 a/b) | ±0.4 % (1.00-1.03) | ±0.1 % | ±0.7 % | ±0.7 % (±5 % below 0.04) | ±0.7 % | ±0.5 % | ±0.6 % | ±4 % (bigsvc), −17 % (uid, 1 MB) | ±11 % (bigsvc), −60 % (uid, 0.3 GB/s) | 0 / 0 | 0 / 0 | ±1 % | ±0.6 % | 1.02-1.04 | the reference: flat; the resolution of every other row |
| **C7 bandwidth neighbor** (memwalk128, `--stream` ×2 / ×4 / ×8 on 9-23 odd) | +14 / +19 / +29 % (1.15 / 1.20 / 1.31) | ×1.40 / 1.58 / 2.60 | +15 / +20 / +30 % | +62 / +66 / +69 % (1.8-1.9) | +22 / +29 / +41 % (0.44-0.50) | +49 / +62 / +89 % (0.28-0.36) | −11 / −14 / −20 % | **−96 / −98 / −98 %** (0.9-1.8 MB) | ×2.1 (0.80-0.85 GB/s) | 50-52 MB / **34 / 63 / 102 GB/s** | 0 / 0 | 17.2 → 21.5 / 24.0 / 25.5 | flat | 1.06 / 1.14 / **1.81** | **only by the neighbor's mbm** (and the graded rise with N): on the victim's own columns ×8 reads as C4′ |

**C7 against C4′: the expected victim-side discriminator failed.** §5.0 expected the stream to leave our occupancy
roughly held (≥ 70 %) while the cache neighbor takes it. It does not: two `stress-ng --stream` workers at 34 GB/s take
the victim's 50 MB down to 1.8 MB, as completely as the cache aggressor takes 30 MB down to 5 MB. From then on the
victim-side columns of the two sources are the same kind of number: at ×8 against C4′, occupancy 0.9 vs 5.2 MB, MPKI
1.87 vs 2.17, memory-bound 0.50 vs 0.50, DRAM-bound 0.36 vs 0.30, core-bound −20 vs −29 %, CPI +30 vs +46 %, own mbm
0.85 vs 1.00 GB/s, I_g 1.31 vs 1.45, φ 0 in both. What differs is the neighbor's column: its mbm 34-102 GB/s against
5.4 (×6-19) at the same occupancy (50 against 46 MB). So "occupancy taken" is the signature of any neighbor that
streams through the shared LLC, not of a cache-capacity neighbor in particular, and the rule that separates the two
is **the neighbor's MBM** (above ~10 GB/s: bandwidth neighbor; below: capacity neighbor), which production mode has for
every monitored neighbor cgroup (set B′ keeps CMT + MBM on, §4.1's `Bp-rdtNbr` monitors the neighbors only). **The rule (stop-point-3 decision 2):** under a high I_g with the victim's occupancy down, read the neighbors' MBM: **≫ 10 GB/s
is a bandwidth neighbor (mitigation: MBA, §6), ≈ 5 GB/s a capacity neighbor (CAT)**. It is the scorer's row-3 sub-rule
(`k5score.py --mbm-bw 10 --mbm-cap 1`: `sub` = "bw" / "cap" and `nb_mbm` on every row, from the largest `mbm_total` of
any monitored cgroup other than the victim); no discriminator cell is added. The other
pairs separate on the victim's own columns: C1′ is the only one with core-bound up and φ_sibling = 1 (C4′ and C7 have
core-bound down by 11-29 %); C3′ is the only one with every neighbor column at zero and I_g 1.0.

**The stall columns do what top-down level 2 was wanted for.** Memory-bound and DRAM-bound separate the memory sources
from the sibling (C1′: DRAM-bound −6 % with CPI +52 %; C4′ and C7: DRAM-bound ×1.5-50), and DRAM-bound separates an
LLC-resident walk from a DRAM walk at the same memory-bound reading (24 MB twin: memory-bound 0.28, DRAM-bound 0.006,
MPKI 0.04; 128 MB twin: 0.36, 0.19, 1.10). The softirq column also rises 25-50 % under C4′ and C7 (the victim's
NET_RX handlers run slower when the LLC is taken), a second-order mark, not a discriminator.

**The amount check** (`i6_sig.txt`): D′ = I_g·F̄ + h(I_g·ρ)·I_g·K = I_g·D(I_g·ρ) against the measured p50:

| cell | ρ | I_g | D(ρ) | D(I_g·ρ) | D′ | measured p50 | D′/p50 |
|---|---|---|---|---|---|---|---|
| twins (uniform, memwalk128 a/b, memwalk24 a/b) | 0.46 | 1.00-1.01 | 3.78 / 5.96 / 4.20 ms | | 3.83 / 6.09 / 4.28 | 3.67 / 5.90 / 4.14 | 1.03-1.04 |
| C7 ×2 | 0.463 | 1.149 | 5.96 | 7.61 | 8.75 | 8.26 | **1.06** |
| C7 ×4 | 0.463 | 1.200 | 5.96 | 8.86 | 10.63 | 9.34 | **1.14** |
| C7 ×8 | 0.463 | 1.305 | 5.97 | 21.22 | 27.70 | 15.33 | **1.81** |
| C4′ | 0.464 | 1.451 | 4.20 | 11.86 | 17.20 | 15.75 | **1.09** (K6 C4: 0.88-0.96 of ×3.04; here ×4.1 against ×3.80) |
| C1′ (unique-id) | 0.722 | 1.150 | 5.4 µs | 5.4 | 6.2 | 7.9 µs | 0.78 |

- Within ±20 % for C7 ×2 and ×4 and for C4′, as expected. **C7 ×8 misses by +81 %:** ρ·I_g = 0.605 lands on the 128 MB
  dictionary's knee, between its ρ 0.59 bin (p50 11.5 ms) and its 0.63 bin (41.4 ms; the sweep's step at ρ 0.65 reads
  45 ms), so D(I_g·ρ) alone is 21 ms and the check is a ratio of two steep numbers; the measured ×2.6 against D′'s ×4.6.
  D(ρ) without the inflation reads 0.39 of the measured, D′ 1.81: the sign is right, the knee is too sharp in the
  dictionary for the amount at this ρ·I_g.
- **C1′ at −22 %:** the recorder's histogram has 8 buckets per octave (`Gordion.h` `kHistBits` 3, ±4.5 %; the window
  p50s read 4.86, 5.38, 7.94 and 8.70 µs, mids 0.51 µs apart), so the miss is real at this resolution. The unique-id
  dictionary's p50 is flat at 5.38 µs from ρ 0.44 up and 14.85 µs below ρ 0.30 (the idle core at a lower effective
  clock, below), so D′ = I_g × floor = 6.2 µs, while the sibling moves the p50 by +48 % with CPU per request only +12 %:
  the floor was taken at the twin's 1.71 GHz and is itself a clock-dependent number. Redone after C1E is off (stop
  point 3, decision 1).

**The clock column found something (C1E).** The agent's APERF/MPERF read of the victim CPU gives **1 711-1 721 MHz on
the unique-id twins** at mid (ρ 0.72: ~30 µs of work per request, idle between), **2 400 MHz under the sibling load**,
and 2 373-2 400 on every bigsvc cell. `HWP_REQUEST` is min = max = 24 throughout (`hwp-check.txt`), so the P-state
request is 2.4 GHz; what differs is that the unique-id core enters C1E between requests (the stop-point-1 decision
disabled C6 and deeper; C1E is enabled, `cpuidle/state2`, 19.3 M entries on CPU 1) and runs much of its short
on-periods below the pinned clock, consistent with the exit-from-C1E ramp (not separately verified). Consequences:
(i) unique-id's dictionary (§4.5) and its twins were taken at this effective clock, so a source that keeps the core
busy raises the clock and masks part of its own cost: C1′'s CPI +52 % shows as CPU per request +12 % (clock +40 %),
and the net neighbor's softirq shows as CPU per request **−10 %** (§3's uid-net cells, 0.0307 → 0.0276 ms); (ii) the
bigsvc cells, whose requests run 4 ms, are not affected; (iii) the clock column is the mark that exposes it, and it
belongs in the signature table. The step 2-4 comparisons within unique-id (arms against twins at the same clock)
hold; the cross-source reading of I_g on unique-id does not, until C1E is handled. Decision at stop point 3: disable
C1E on node-3 for the remaining steps (restorable: `cpuidle/state2/disable`; ~6 min to verify on one twin cell) and
rerun the three unique-id cells and its dictionary sweep (~30 min), or keep the stop-point-1 setting and carry the
clock column beside I_g.

- Raw data `/local/results/i6-raw.tgz` and `C:/coding-workspace/archive/i6-raw-20261119-laptop.tgz` (139 MB, sha256
  30bcd784…8bd1, equal on both). The restore checked clean (RUNBOOK §18, queue i6 end state).

### 5.2 unique-id with C1E off (stop-point-3 decision 1; queue `i/queue-i7.sh`): expected readings (written before the queue)

node-3's C1E is disabled since Nov 19 01:02 UTC (RUNBOOK §18; POLL and C1 left on, exit 0 and 1 µs). The queue reruns
unique-id's ρ ramp (its mid moves with the clock), its dictionary (`sweepF`), the three signature cells (twin, C1′, twin;
`sigF`) and the overhead cells for the corrected set (`none,intercept,Bp-fixedReads`, quiet and switch, n = 5 × 30 s). The
clock per cell is the agent's APERF/MPERF column.

| cell | expected | measured |
|---|---|---|
| clock, every unique-id cell (twins included) | **2 390-2 400 MHz** (was 1 711-1 721 on the twins, 2 400 under the sibling) | **2 400 on all 13 agent-bearing cells** (twin a / b, C1′, `Bp-fixedReads` × 5 quiet, × 5 switch); met |
| ρ ramp | mid (rate at ρ 0.5) up from 16 568 to **20 000-24 000** (CPU per request ∝ 1 / clock: ×0.71-0.75) | **22 483** (×1.36; CPU per request 0.0315 → 0.0233 ms = ×0.74); met |
| dictionary | p50 **flat at ~5.4 µs over the whole ramp** (step 2's 14.85 µs below ρ 0.30 was the idle core's clock); CPU per request at mid 0.022-0.024 ms (was 0.031); CPI 0.85-0.90 unchanged; g(ρ) flatter | p50 **4.864 µs in all 13 bins, ρ 0.11-0.96** (one ±4.5 % bin below the 5.4 forecast); CPU per request 0.0231 ms at mid, 0.0229-0.0236 over the ramp; CPI 0.88; c̄ 23.3 µs (step 2's 43.6 carried the slow floor); met |
| C6′ twin vs twin | as §5.0: CPU ±2 %, p50 ±5 %, CPI ±2 %, I_g 0.97-1.03; occupancy and mbm at their noise floor again | CPU −0.5 %, p50 0.0 %, CPI −0.4 %, I_g 1.035 / 1.031 (the signature cells spend 2.5 % more CPU per request than the sweep: perf counters on); occupancy 4.3 / 4.6 MB, mbm 0.35 / 0.61 GB/s; server p99 8.7 µs (was 30 at 1.7 GHz), client p99 1.43 ms (was 1.96); met (I_g 0.5 pt over the band) |
| C1′ sibling | φ_sibling 1, core-bound +20-30 %, CPI +40-55 % as before, **clock flat (both at 2.4 GHz) so CPU per request +40-55 % and I_g 1.4-1.6** (was +12 %, 1.15); p50 +40-55 %; **D′ within ±20 % of p50** (D′ = I_g × floor with the floor now at one clock) | φ_sibling 1.000, core-bound +23.9 %, CPI +47.9 %, MPKI +5 %, clock 2 400 both; **CPU per request +47.4 %, I_g 1.522**; p50 4.86 → 7.94 µs (**+63 %**, above the band); **D′ 7.40 µs = 0.93 × p50** (was 0.78); met except p50's size |
| overhead, quiet | `Bp-fixedReads` − `none`: CPU per request +1 to +4 %, client p99 within +1 %, in-handler p99 within the twin spread (as §4.1; the per-switch costs in ns are smaller at 2.4 GHz and the request is shorter by the same factor) | **+2.9 % [2.7, 3.0]**, client p99 **+0.9 % [0.6, 1.2]**, in-handler p50 4.86 µs and p99 9.7 µs on every rep (`intercept`: 9.7-10.8); `intercept` +0.8 % / +0.3 %; agent 0.006 cores, BPF 0.009, 1.5 ms per tick; gates pass; met |
| overhead, switch | +2 to +4 % CPU per request, client p99 within +2 % | **+3.0 % [2.7, 3.2]**, client p99 **+0.8 % [0.6, 1.1]**, in-handler p99 9.7 µs; `intercept` +0.8 % / +0.1 %; BPF 0.037 cores, 4.3 ms per tick; gates pass; met |

**Result (Nov 19 01:11-03:20 UTC; `results/i7/an/i6_sig.csv`, `i6_sig.txt`, `summary.txt`, `k5_setb.csv`; dictionary
`results/i7/dict/dict-uid.json`).** With C1E off the unique-id core runs at 2 400 MHz in every cell, idle gaps included, and
the step-2 picture is restated at one clock: mid 22 483 req/s, CPU per request 0.0233 ms, the dictionary one flat floor of
4.864 µs from ρ 0.11 to 0.96 (step 2's 14.85 µs below ρ 0.30 and its 43.6 µs c̄ were the idle core's clock, not the service),
server p99 8.7 µs instead of 30. **The C1′ amount check now passes:** the SMT sibling costs +47 % CPU per request at the same
clock, I_g 1.52, and I_g × D(I_g ρ) = 7.40 µs against a measured p50 of 7.94 µs (D′/p50 0.93, inside ±20 %; §5.1's 0.78 was
the clock hidden in the twins' floor). The one miss is the size of the p50 rise, +63 % against the +40-55 % written, with the
CPU per request at +47 %: the sibling stretches the request's wall time more than its cycles (R_foreign 97 ms/s on the victim
CPU, softirq 239 vs 150 ms/s). The corrected set's overhead at 2.4 GHz is +2.9 % quiet and +3.0 % under the switch neighbor
(client p99 +0.9 / +0.8 %, in-handler p50 and p99 unchanged, n = 5, 95 % intervals above), inside the §4.1 band; `intercept`
alone is +0.8 %. Twins flat (CPU −0.5 %, p50 0.0 %). The queue aborted once before any instrumented cell (a wrong argument
count in the sweep call; RUNBOOK §18) and was resumed from the sweep with the ramp's mid; no cell was repeated. Raw data:
`/local/results/i7-raw.tgz` on node-0 and `archive/i7-raw-20261119-laptop.tgz` on the laptop (sha256 00c39307…a266), not staged.

## 6. Mitigation of inflation (step 5; queue `i/queue-i8.sh`)

The two mitigations the MBM rule (§5.1) picks between, each tried on both sources, so the table shows the rule picking
right and the cost of picking wrong: **CAT** gives the victim its own L3 ways via a resctrl ctrl group (`/sys/fs/resctrl/k5v`,
`L3:1=<mask>`; the neighbor's cgroup in `k5n` with the remaining ways; L3 domain 1 is socket 1's, where both run; 12 ways,
55 MB, min 1 way), **MBA** throttles the neighbor's ctrl group (`MB:1=<pct>`; min 10 %, steps of 10, linear). The controller
builds the groups per arm before the agent starts and removes them after it (`k5ctl.py mitig_setup/teardown`); the
agent's monitor groups sit inside them (`--rdt-parent`), so the counter, CMT and MBM columns read as in step 4. Smoke-tested
on node-3 Nov 19 01:08 UTC (RUNBOOK §18). One 300 s run per step, arm `sigF` plus the mitigation (`cat4`, `cat6`, `mba90`,
`mba50`, `mba30`, `mba10`); the unmitigated source and its twins run before and after in the same pod, so every recovery
is read against the same calibration. The neighbor's progress is its cgroup's instructions per second (the agent's `aggr`
counters now cover the neighbor's CPUs, `k5spec.py`), its bandwidth its MBM.

**Recovery** = 1 − (p50_mitigated − p50_twin) / (p50_source − p50_twin): the share of the inflation removed (1 = back at the
twin, 0 = none); the same for I_g. **Neighbor cost** = its progress under the mitigation ÷ its progress unmitigated − 1.

### 6.0 Expected readings (written before the queue)

| run | expected | measured |
|---|---|---|
| memwalk24 twins before / after | p50 4.1-4.2 ms, occupancy 29-31 MB, MPKI 0.04, I_g 1.00-1.02; within ±3 % of each other | p50 4.067 / 4.067 ms, occupancy 28.6 / 26.9 MB, MPKI 0.040, I_g 0.998 both; CPU, p50, CPI within 0.0 % (occupancy −6 %); met |
| C4′ unmitigated (cache ×4 on 9-15) | as §5.0: p50 ×3.5-4, occupancy ≤ 6 MB, MPKI ~2, I_g 1.4-1.5, nb occupancy ~46 MB, nb mbm ~5 GB/s | p50 ×3.91 (15.88 ms), occupancy 6.6 MB, MPKI 2.17, I_g 1.450, nb occupancy 44 MB, nb mbm 3.5 GB/s; met |
| **C8a CAT 4 ways under C4′** (victim 18 MB) | partial: occupancy up to 15-18 MB (capped below its 30 MB), MPKI 0.5-1.2, **recovery 0.4-0.7** on p50, I_g 1.1-1.25; neighbor capped at 8 ways (37 MB): occupancy ≤ 37 MB, progress −10 to −30 %, its mbm up (5 → 7-12 GB/s: more misses) | occupancy 14.6 MB, MPKI 1.14, **recovery 0.70** (p50 7.65 ms), I_g 1.235; neighbor occupancy 32.7 MB, progress **−9.2 %**, mbm 8.3 GB/s; met (neighbor cost at the low edge) |
| **C8a CAT 6 ways under C4′** (victim 28 MB) | occupancy 25-28 MB, MPKI 0.05-0.3, **recovery ≥ 0.8** on p50, I_g ≤ 1.08; neighbor at 6 ways: occupancy ≤ 28 MB, progress −20 to −45 %, mbm 8-15 GB/s | occupancy 21.5 MB, MPKI 0.53, **recovery 0.87** (5.62 ms), I_g 1.103; neighbor occupancy 25.6 MB, progress **−11.1 %**, mbm 11.5 GB/s; recovery met, occupancy / MPKI / I_g short of the band, the neighbor pays half of what was written |
| C8b′ MBA 50 / 10 % under C4′ | the cache neighbor's traffic is in-LLC, not DRAM (5 GB/s): **recovery ≤ 0.3** at either step, occupancy stays ≤ 10 MB, MPKI stays ×30+; neighbor progress −0 to −20 % (50 %), −20 to −50 % (10 %): the wrong mitigation costs the neighbor without freeing the victim | 50 %: **recovery 0.00**, occupancy 9.4 MB, MPKI 2.18, I_g 1.450, neighbor progress −4.8 %, mbm 6.3 GB/s; met. 10 %: **recovery 0.50**, occupancy 10.3 MB, MPKI 1.57 (×39), I_g 1.323, neighbor progress **−68 %**, mbm 1.9 GB/s; recovery above the band (the floor throttle starves the cache neighbor enough to free half the victim's loss, at 7× CAT 6's neighbor cost) |
| memwalk128 twins before / after | p50 5.9 ms, occupancy ~50 MB, MPKI 1.1, DRAM-bound 0.19, I_g 1.01; within ±3 % | p50 5.890 / 5.905 ms, occupancy 50.4 / 49.7 MB, MPKI 1.10 / 1.11, DRAM-bound 0.190 / 0.191, I_g 1.005 / 1.006; within 0.2 %; met |
| C7 ×4 unmitigated | as §5.0: p50 ×1.5-1.6, occupancy ~1 MB, DRAM-bound 0.30, I_g 1.15-1.22, nb mbm 55-70 GB/s | p50 ×1.58 (9.32 ms), occupancy 0.8 MB, DRAM-bound 0.307, I_g 1.198, nb mbm 62.3 GB/s; met |
| **C8a′ CAT 4 / 6 ways under C7 ×4** | **little recovery (≤ 0.25):** the 128 MB walk misses in any partition (its own occupancy capped at 18-28 MB instead of 1 MB, MPKI unchanged ±10 %, DRAM-bound stays ≥ 0.27), I_g stays ≥ 1.12; neighbor (streams) progress −0 to −5 %, mbm unchanged | **recovery 0.35 / 0.43** (8.13 / 7.86 ms), occupancy 16.1 / 24.5 MB, MPKI 1.57 / 1.46 (−15 / −20 %), DRAM-bound 0.285 / 0.275, I_g 1.139 / 1.121; neighbor progress −0.0 %, mbm 62.7 / 62.6 GB/s; **missed: more recovery than written** (the partition keeps part of the 128 MB walk's hot set in the LLC: MPKI −15-20 %), at no cost to the streams |
| **C8b MBA 90 / 50 / 30 / 10 % under C7 ×4** | neighbor mbm ~60 / 30-35 / 18-25 / 6-12 GB/s and progress −5 / −45 / −65 / −85 %; victim DRAM-bound 0.30 → 0.28 / 0.25 / 0.22 / 0.20, I_g 1.20 → 1.17 / 1.10 / 1.05 / ≤ 1.03, p50 recovery 0.1 / 0.4-0.6 / 0.7-0.85 / ≥ 0.9; **the victim is within the twin's band (p50 ±5 %, I_g ≤ 1.03) at 10 %, possibly at 30 %**; its occupancy comes back only partly (the stream still sweeps, slower) | neighbor mbm 61.6 / 36.7 / 18.2 / 1.6 GB/s, progress −1.3 / −41 / −71 / **−97 %**; victim DRAM-bound 0.307 / 0.288 / 0.274 / 0.236, I_g 1.196 / 1.159 / 1.129 / 1.067, **recovery 0.04 / 0.26 / 0.42 / 0.69**; occupancy 1.0 / 1.3 / 2.5 / 17.1 MB; **missed: at every step the victim recovers less than written and is within the twin's band at none** (p50 +18 %, I_g 1.07 at 10 %, with the streams at 2.6 % of their progress) |
| dense 128 MB re-sweep (decision 5: ρ 0.5-0.7 in steps of 0.05) | the knee between ρ 0.55 and 0.65 resolved into 2-3 bins; the C7 ×8 check redone: D′/p50 within ±25 % | 15 bins to ρ 0.70: 0.547 → 8.3, 0.591 → 11.6, **0.634 → 25.6** (was 41.4), 0.678 → 45.4 ms; step anchors 568 req/s → 13.6 ms, 612 → 40.9: **the knee is a cliff between two steps, not a bin gap**; C7 ×8 D′/p50 **1.61** (was 1.81), still outside ±25 %; D(I_g·ρ)/p50 1.22 |

### 6.1 Results (Nov 19 03:21-05:04 UTC; `results/i8/an/i6_sig.csv`, `i6_sig.txt`; dense dictionary `results/i8/dict/dict-syn-memwalk30-128d.json`, its tables in `an-dense/`)

**The MBM rule picks the mitigation that works, on both sources.** Under the cache neighbor (nb mbm 3.5 GB/s, sub-rule
`cap`) CAT with 6 of 12 ways gives the victim back 87 % of its p50 loss for an 11 % loss of the neighbor's progress;
MBA at its floor gives back 50 % and costs the neighbor 68 %. Under the stream neighbor (nb mbm 62 GB/s, `bw`) MBA at
10 % gives back 69 % for a 97 % loss of the streams' progress, CAT 6 ways 43 % at no cost to them. Eight cells, 17
arms, 0 errors, 300 s each, 2 999-3 001 recorder windows per arm; every arm's schemata as written (`k5v` `L3:1=00f` /
`03f`, `k5n` the complement; `MB:1=` 90 / 50 / 30 / 10 on `k5n`), 12 victim and 5 neighbor tasks in the groups, the
groups removed after each arm (`mitig-off`); clock 2 400 MHz on every arm; twins within 0.2 % before and after.

| source (nb mbm, sub-rule) | mitigation | victim p50 ms (twin 4.07 / 5.90) | **recovery p50** | I_g (recovery) | victim occupancy MB | MPKI | DRAM-bound | **neighbor progress** | neighbor mbm GB/s | neighbor occupancy MB | D′/p50 |
|---|---|---|---|---|---|---|---|---|---|---|---|
| C4′ cache ×4 (3.5 GB/s, `cap`) | none (`sigF`) | 15.88 | 0 | 1.450 (0) | 6.6 | 2.17 | 0.300 | 0 | 3.5 | 44.2 | 1.08 |
| | **CAT 4 ways** | 7.65 | **0.70** | 1.235 (0.48) | 14.6 | 1.14 | 0.188 | **−9 %** | 8.3 | 32.7 | 0.87 |
| | **CAT 6 ways** | 5.62 | **0.87** | 1.103 (0.77) | 21.5 | 0.53 | 0.097 | **−11 %** | 11.5 | 25.6 | 0.89 |
| | MBA 50 % | 15.83 | 0.00 | 1.450 (0.00) | 9.4 | 2.18 | 0.300 | −5 % | 6.3 | 44.8 | 1.07 |
| | MBA 10 % | 10.01 | 0.50 | 1.323 (0.28) | 10.3 | 1.57 | 0.240 | **−68 %** | 1.9 | 41.3 | 0.99 |
| C7 stream ×4 (62 GB/s, `bw`) | none (`sigF`) | 9.32 | 0 | 1.198 (0) | 0.8 | 1.83 | 0.307 | 0 | 62.3 | 51.2 | 1.13 |
| | CAT 4 ways | 8.13 | 0.35 | 1.139 (0.31) | 16.1 | 1.57 | 0.285 | −0 % | 62.7 | 34.4 | 1.05 |
| | CAT 6 ways | 7.86 | 0.43 | 1.121 (0.40) | 24.5 | 1.46 | 0.275 | −0 % | 62.6 | 25.6 | 1.00 |
| | MBA 90 % | 9.18 | 0.04 | 1.196 (0.01) | 1.0 | 1.84 | 0.307 | −1 % | 61.6 | 51.1 | 1.13 |
| | MBA 50 % | 8.42 | 0.26 | 1.159 (0.21) | 1.3 | 1.79 | 0.288 | −41 % | 36.7 | 50.6 | 1.08 |
| | MBA 30 % | 7.90 | 0.42 | 1.129 (0.36) | 2.5 | 1.73 | 0.274 | −71 % | 18.2 | 49.1 | 1.03 |
| | **MBA 10 %** | 6.95 | **0.69** | 1.067 (0.68) | 17.1 | 1.37 | 0.236 | **−97 %** | 1.6 | 34.2 | 0.98 |

Recovery = 1 − (p50 − p50_twin) ÷ (p50_source − p50_twin) (the same for I_g, in brackets); neighbor progress = its cgroup's
instructions per second against the unmitigated arm − 1 (the cache neighbor runs 2.1 cores, the streams 4.0, in every arm);
D′/p50 against §4.5's 128 MB and §5.1's 24 MB dictionaries.

- **Under the cache neighbor, capacity is the lever.** The victim's occupancy follows its ways (6.6 → 14.6 → 21.5 MB for
  0 / 4 / 6 own ways) and its MPKI with it (2.17 → 1.14 → 0.53); the neighbor loses 9-11 % of its progress because its
  working set no longer fits (its mbm 3.5 → 8-12 GB/s: it misses more, the misses go to DRAM). MBA at 50 % does nothing
  to either side (the neighbor's 3.5 GB/s is far under the throttle); at 10 % the neighbor is starved to a third of its
  speed and the victim recovers half, with its occupancy still at 10 MB and its MPKI ×39: the right amount of relief
  for the wrong reason, at 7× the cost of CAT 6. §6.0 expected MBA at 10 % to give ≤ 0.3; it gave 0.50.
- **Under the stream neighbor, bandwidth is the lever, but it is not the whole story.** MBA's throttle tracks the streams
  (mbm 62 → 37 → 18 → 1.6 GB/s, progress −1 / −41 / −71 / −97 %) and the victim recovers 0.04 / 0.26 / 0.42 / 0.69. At
  the floor the streams are at 2.6 % of their progress and 1.6 GB/s, yet the victim's p50 is still +18 % over the twin
  (I_g 1.07) and its occupancy 17 MB instead of 50: the throttled stream still sweeps the LLC, slowly. §6.0 expected the
  victim back in the twin's band at 10 %; it is not, at any step. **CAT under the stream recovers 0.35 / 0.43 instead of
  the ≤ 0.25 written:** a private partition keeps 16-25 MB of the 128 MB walk's hot set (MPKI −15 to −20 %), and costs
  the streams nothing (their progress and mbm unchanged). Neither mitigation alone brings this victim back; CAT 6 + MBA
  would be the next cell (not run; not in the plan).
- **The rule's reading is the right one on both sides:** `cap` → CAT recovers 0.87 at −11 % neighbor cost against MBA's
  0.50 at −68 %; `bw` → MBA recovers 0.69 against CAT's 0.43. Per point of neighbor progress lost, CAT is first under
  both (`cap`: 0.079 against MBA's 0.007 per point; `bw`: CAT's 0.43 is free, MBA's 0.69 costs 97 points), so under a
  bandwidth neighbor the operator's choice depends on what the neighbor is worth: MBA for the victim, CAT when the
  streams matter.
- **The amount check holds through the mitigations** (D′/p50 0.87-1.13 on every arm, I_g read from the agent's counters
  in each): recovery read from I_g and from p50 agree to within 0.1 at CAT and MBA 30 / 10 and differ by 0.2 under
  C4′'s CAT 4 and MBA 10, where the convex part of D amplifies a given I_g.
- **Decision 5 (analysis only, the dense 128 MB sweep):** eleven steps with ρ 0.54 / 0.59 / 0.66 / 0.70 / 0.76 between
  the old 0.55 and 0.63 (`results/i8/dict/dict-syn-memwalk30-128d.json`: F̄ 5.145, c̄ 4.243, K 4.255 ms, 15 bins to
  ρ 0.70, W_max 40 ms). The knee is a cliff: the step at 568 req/s serves at p50 13.6 ms, the next at 612 at 40.9 ms
  (ρ 0.70 → 0.76), so the bins at 0.63 and 0.68 pool windows from both sides (25.6 and 45.4 ms against 41.4 and 48.4
  before). Refit, C7 ×8 reads D′/p50 **1.61** (was 1.81) with ρ·I_g = 0.613 on the cliff's lower edge, where D moves
  by 14 ms per 0.043 of ρ; the ±4 % uncertainty of I_g alone spans ±8 ms of D there. D(I_g·ρ) without the second I_g
  factor reads 1.22, and for ×2 / ×4 0.96 / 1.02 (D′ 1.12 / 1.25): on the convex part of the curve the load term
  carries the inflation and the factor on F̄ and K double-counts it; on the flat part (C4′: D(I_g·ρ)/p50 0.74, D′ 1.08)
  it is needed. **The check at ×8 is not resolved by a finer sweep; the operating point is on the knee and the amount
  there is as good as I_g is.** The §5.1 dictionaries stay the reference (the dense one changes C7 ×4's D′/p50 from
  1.13 to 1.24, by the same mechanism).
- **Platform and restore.** HWP 144 × 0x55001818 at every check; C1E and C6 off throughout; the resctrl groups removed
  after each arm and the filesystem unmounted at the end; 46 deployments equal to the snapshot, 79 / 79 pods, node-3
  cleaned (RUNBOOK §18). Raw data: `/local/results/i8-raw.tgz` on node-0 and `archive/i8-raw-20261119-laptop.tgz` on the
  laptop (191 MB, sha256 a5e9f7bc…9e97, equal), not staged; summaries in `results/i8/`.

**The two-step policy test (user, Nov 19; queue `i/queue-i9.sh`, cells `m6-syn-memwalk128-{twin-a,stream4,twin-b}`,
arm `cat6mba30`): expected readings, written before the run.** C7 ×4 with the victim's own 6 ways and the neighbor
throttled to 30 % together, 300 s, twins before and after in the same pod. The user's expectation: recovery ≥ 0.8 of
the p50 inflation at about −71 % neighbor progress, the victim's occupancy back above 40 MB. Mine, from the two single
arms above: the capacity part (CAT 6: 0.43, at no neighbor cost) and the bandwidth part (MBA 30: 0.42, −71 %) are
largely independent, so **recovery 0.75-0.9 on p50, I_g 1.03-1.06, neighbor progress −70 to −72 %, mbm 17-19 GB/s**,
DRAM-bound 0.22-0.25, MPKI 1.3-1.5. **The occupancy cannot exceed about 27 MB:** 6 of 12 ways of a 55 MB L3 cap the
victim at 27.5 MB whatever the throttle, so I expect **24-27 MB**, not above 40; above 40 MB would need 9 or more ways.
The unmitigated arm and the twins should read as §6.1's (p50 9.3 / 5.9 ms, occupancy 0.8 / 50 MB).

**Measured (Nov 19 05:33-05:57 UTC; `results/i9/an/i6_sig.csv`): recovery 0.60 on p50 (9.28 → 7.25 ms against the twins'
5.90), 0.58 on I_g (1.198 → 1.087), neighbor progress −71.0 %, its mbm 63 → 18.3 GB/s, the victim's occupancy 0.9 →
24.8 MB, MPKI 1.83 → 1.45, DRAM-bound 0.307 → 0.251, client p99 33.6 → 26.4 ms (twin 23.4), D′/p50 0.97; schemata as
written (`k5v` `L3:1=03f`, `k5n` `L3:1=fc0` + `MB:1=30`, 12 and 5 tasks); twins within 0.2 % of each other and of step
5's.** The neighbor cost and the occupancy cap came out as written (−71 %, 25 MB); the recovery did not: 0.60 against
the ≥ 0.8 asked and my 0.75-0.9. The two single-arm recoveries (CAT 6: 0.43, MBA 30: 0.42) do not add. With 6 ways the
walk's misses still go to DRAM at the throttled stream's 18 GB/s, and with the stream at 30 % the victim's 6 ways
still hold only half of what it had alone: 6.6 % of the 128 MB working set is in the LLC either way, and the two
mitigations remove overlapping parts of the same stall time (DRAM-bound 0.307 → 0.275 with CAT 6 alone, → 0.274 with
MBA 30 alone, → 0.251 with both). The remaining +23 % of p50 at −71 % neighbor progress is the price of this victim's
working set, not of either lever: to recover more the stream must go to 10 % (−97 %, recovery 0.69 alone) or the
victim needs more ways than half the cache.

## 7. The escalation arm (step 6; queue `i/queue-i9.sh`)

The production set is the corrected B′ (`Bp-fixedReads`, §4.1 / §5.2). The escalation arm (`Bp-esc`, `k5spec.py`) runs
that set throughout and, while the on-node scorer flags a window (E ≥ 0.15 or I_g > 1.15), a second agent with the
diagnostic programs the default leaves off (RESULTS-K 5.6): the softirq programs, the perf cgroup counters with the
stall columns, and the probe's holder split (`probe=switch,softirq rq=1 perf=1 perf_read=1 perf_td=1`). The controller's
`Escalator` (`k5ctl.py`) tails the scorer's output, starts the diagnostic agent at the first flagged window, stops it 5 s
after the last one, and writes an `esc` event per start and stop; each escalation has its own agent file. Two cells, each
with the twin (`none`), B′ alone and `Bp-esc`, 3 × 60 s paired per repetition: the synthetic service's 128 MB walk at
mid under the stream neighbor ×4 (C7, where the scorer reads row 3 throughout), and unique-id at the C1E-off mid
(22 483 req/s) under the network neighbor (A5 stream at 50 000 pps with RPS to CPU 1; §3.1 read row 6 there). The
synthetic service's on-node scorer uses k5score's pooled fit of queue i5's 128 MB sweep (`dict-syn-memwalk128.json`:
c̄ 5.47 ms, 20 bins to ρ 1.23), which is self-consistent on its own ρ axis.

### 7.0 Expected readings (written before the queue)

| cell | expected | measured |
|---|---|---|
| C7 ×4, escalation | **flagged on ≥ 90 % of the windows** (E 0.3-0.4, I_g 1.2 from the first smoothed windows); **one escalation per arm**, on within 5 s of the start, off at the arm's end; the diagnostic agent runs ≥ 50 of the 60 s | **flagged on 100 % of the windows** (E 0.29, I_g 1.16, rows 3 / 7 / 2 on 52 / 38 / 10 %); **one escalation per arm, on for 62.8 of 63 s**, off at the arm's end, in all three reps; met |
| C7 ×4, cost | B′ alone − twin: **+1 to +3 %** CPU per request (the set's switch-rate part is small on this 4-core Go service: §4.2's recommendation read +0.4-1.6 %); **`Bp-esc` − B′: +0.5 to +2 points** (perf counters armed on 4 CPUs at a low switch rate, the softirq programs on little softirq); client p99 within ±3 % of B′ | B′ − twin **+0.3 % [−1.8, 2.5]** (under the band: the set's cost is inside this 4-core service's rep-to-rep spread); **`Bp-esc` − twin +0.9 % [−0.8, 2.7]**, i.e. +0.6 points over B′ (met; not distinguishable at n = 3); client p99 +4.4 % [−5.8, 14.6] against B′'s +0.2 % (undecided) |
| C7 ×4, signature in the flagged windows | from the diagnostic agent's ticks: **MPKI 1.7-1.9 (twin 1.1), DRAM-bound 0.28-0.32 (twin 0.19), own occupancy ≤ 2 MB (twin 50), neighbor mbm 55-70 GB/s, sub-rule `bw`** in ≥ 95 % of the flagged windows; φ 0, softirq while-current ≤ 30 ms/s: the inflation signature, present | diagnostic agent, every escalation: **MPKI 1.84-1.86, DRAM-bound 0.307, memory-bound 0.46**, φ 0.000, softirq 24 / 20 ms/s; the production agent beside it: occupancy 0.8-0.9 MB, neighbor mbm 62-63 GB/s, **sub-rule `bw` on 629 of 629 windows**; present in every flagged window; met |
| net, escalation | **flagged on ≤ 10 % of the windows** (E ≈ 0: the softirq steals the core before the handler, which the in-handler timer does not see; I_g 0.95-1.05); escalations, if any, short (≤ 10 s) and ≤ 3 per arm | **missed: flagged on 100 % of the windows, one escalation per arm for the whole 63 s.** The scorer read row 6 on every window (E 0.001: the in-handler p50 does not move) but **I_g read 1.28-1.33**: the stream's NET_RX runs while the victim is current and is charged to its cgroup, +28-33 % CPU per request with the same instructions per request (RESULTS-K 6.1's C2 mechanism) |
| net, cost | B′ alone − twin: **+2 to +5 %** (§5.2's +3.0 % under switch, the net stream's extra softirq on CPU 1 counted in the victim's cgroup time); **`Bp-esc` − B′ within ±1 point** (the diagnostic agent is off most of the time) | B′ − twin **+3.4 % [3.1, 3.7]** (met), client p99 +3.6 % [1.1, 6.1]; **`Bp-esc` − twin +7.5 % [7.1, 7.8]**, +4.1 points over B′ (missed, by the I_g flag above: the diagnostics ran the whole arm, and the perf counters and softirq programs cost 4 points at 18 800 NET_RX/s on the victim's CPU); client p99 +6.4 % [4.0, 8.9] |
| net, signature in the flagged windows | if any window is flagged: softirq while-current 200-400 ms/s on CPU 1 (the stream's NET_RX), **MPKI and DRAM-bound at the twin's**, occupancy at the floor: not the inflation signature (row 7 territory: the softirq case the production rule has no row for, §4.3) | diagnostic agent: **softirq 366 ms/s total, 285 while current** (quiet unique-id: 150 / 150, §5.2), MPKI 0.57, DRAM-bound 0.083, memory-bound 0.31, φ 0.028; not the inflation signature; met |
| twins | `none` CPU per request within ±1 % between repetitions; the stream's progress and mbm the same in all three arms | C7: 5.864-5.892 ms per request over the three `none` reps (±0.3 %); net: 0.02798-0.02803 ms (±0.1 %); the streams' mbm 62-63 GB/s in every arm |

### 7.1 Results (queue i9b, Nov 19 06:48-07:14 UTC; `results/i9b/an/i9_esc.csv`, `i9_esc.txt`, `summary.txt`; `analysis/i9_esc.py`)

**Under the stream neighbor the escalation does what it is for, at +0.6 points: the production set alone costs +0.3 %
[−1.8, 2.5] CPU per request and the escalation arm +0.9 % [−0.8, 2.7], and in every one of its windows the diagnostic agent
shows the inflation signature (MPKI 1.85 against the twin's 1.10, DRAM-bound 0.31 against 0.19) beside the production
agent's `bw` sub-rule (neighbor mbm 62 GB/s). Under the network neighbor the escalation never switches off: the
production scorer reads row 6 throughout (E 0.001), but I_g reads 1.28-1.33 because the stream's NET_RX is charged to
the victim's cgroup, so the I_g test flags every window, the diagnostics run for the whole arm and cost 4.1 points over
B′ (+7.5 % against +3.4 %), while what they show is the softirq signature (366 ms/s of softirq on the victim's CPU, 285
of it while the victim is current, MPKI and DRAM-bound at the twin's), not inflation.** Two cells, 3 reps × 3 arms, 18
arm records, 0 errors; one escalation per `Bp-esc` arm, on at the first flagged window (within 0.3 s of the arm's start),
off at the arm's end (62.8 s of 63); the queue's first attempt (i9) aborted on a controller bug after one `Bp-esc` arm per
cell, with the escalation itself behaving the same (RUNBOOK §18).

| cell (n = 3 × 60 s) | arm | CPU per request vs twin [95 %] | client p99 vs twin [95 %] | scorer: flagged windows, rows | escalation | diagnostic agent's columns (flagged windows) |
|---|---|---|---|---|---|---|
| memwalk128 at mid under stream ×4 (C7) | `Bp-fixedReads` | +0.3 % [−1.8, 2.5] | +0.2 % [−21.9, 22.2] | 100 %; 3: 52 %, 7: 38 %, 2: 10 %; `bw` 100 % | — | — |
| | `Bp-esc` | **+0.9 % [−0.8, 2.7]** | +4.4 % [−5.8, 14.6] | 100 %; 3: 54 %, 7: 36 %, 2: 10 % | 1 per arm, 62.8 s | MPKI 1.85, DRAM-bound 0.307, mem-bound 0.46, φ 0, softirq 24 / 20 ms/s |
| unique-id at mid (22 483) under net (A5 stream, RPS → CPU 1) | `Bp-fixedReads` | +3.4 % [3.1, 3.7] | +3.6 % [1.1, 6.1] | 100 % (I_g 1.28-1.33, E 0.001); 6: 100 % | — | — |
| | `Bp-esc` | **+7.5 % [7.1, 7.8]** | +6.4 % [4.0, 8.9] | 100 %; 6: 100 % | 1 per arm, 62.9 s | MPKI 0.57, DRAM-bound 0.083, **softirq 366 / 285 ms/s**, φ 0.028 |

- **The C7 case is the one the arm was built for, and it reads as written:** the production set names inflation with the
  bandwidth sub-rule, the escalation adds the counters and finds MPKI ×1.7 and DRAM-bound ×1.6 in every window it runs,
  for about half a point of CPU per request on this service (perf counters on 4 CPUs at a low switch rate; the softirq
  programs see 24 ms/s). The rows split 52 / 38 / 10 between 3, 7 and 2 because E sits around 0.29 while I_g (1.16) and the
  slot share cross their bands window by window; the amount is the same whichever row the window falls in.
- **The net case shows the arm's trigger is wrong for softirq.** The in-handler p50 is untouched (E 0.001, row 6 in
  every window), but the victim's cgroup CPU per request is +28-33 % with the same instructions per request, so I_g
  flags every window and the diagnostics never stop. The production columns already carry the discriminator: the
  sampler's softirq per CPU (§4.3, 18 800 NET_RX/s on CPU 1) and, once armed, the softirq-while-current split (285 of
  366 ms/s). **Fix for the rule (not applied here):** an I_g rise with E ≈ 0 and softirq on the victim's CPUs above its
  twin's is the softirq case (RESULTS-K 6.1 S9), not inflation; it should name the network neighbor and not escalate,
  or escalate once for a bounded time. With that gate the net arm's cost returns to B′'s +3.4 %.
- **Cost of the escalation when it runs:** +0.6 points on the 4-core Go service under the stream, +4.1 on the 1-core C++
  service under 18 800 softirqs/s on its own CPU, where the perf cgroup counters switch at every NET_RX and the softirq
  programs fire for each (RESULTS-K 5.5's +1.9 and +3.2 % on Haswell at a lower rate). Agents + scorer 0.007-0.014 cores,
  1.5-1.8 ms per tick, BPF 0.002-0.010 cores: inside the per-tick gate in every arm.
- **Platform and restore (RUNBOOK §18):** HWP 144 × 0x55001818 at every check, C1E and C6 off; i9b's restore clean (46
  deployments equal, 79/79 pods, node-3 cleaned, namespace `k5` gone). Raw data `/local/results/i9b-raw.tgz` on node-0
  and `archive/i9b-raw-20261119-laptop.tgz` (23 MB, sha256 a53479ea…21b3), not staged; summaries in `results/i9b/`.

## 8. HarvestContainers on Ice Lake (step 7; queue `i/queue-i10.sh`)

node-4 of this profile runs the harvest kernel (6.12.118-harvest, `nosmt=force`, `intel_idle.max_cstate=0`: idle states
POLL and C1 only), 72 cores online, HC installed as the profile's motivation experiment left it (`/local/HarvestContainers`,
units `hc-listener` / `hc-balancer` disabled, module `qidlecpu.ko` not loaded, `/etc/default/hc-harvest` with the Monitor on
CPU 15 and the Listener on 13), and three of the profile's pods (jaeger, memcached-profile, mongodb-geo, unpinned). It has
no HWP: `intel_cpufreq` in passive mode with `schedutil`, 0.8-3.5 GHz. HC addresses CPUs below 64, so the queue takes
CPUs 64-71 offline for its duration (stop-point-1 decision) and pins the experiment's CPUs (Primary 4,6,8,10, burner 12,
HC daemons 13-15 and 24, hrgen 17-27 odd, agent 30) to 2.4 GHz through cpufreq, restoring both afterwards. Everything
else follows K4.2e / K4.5: recommendation on CPUs 4,6,8,10 (`GOMAXPROCS` 4, gate off), the burner (five busy workers,
BestEffort) owning core 12, HC driven by `k4node.sh` (writes only, never a procfs read), reserve = `TIC = LOW_TIC`, 300 s
per run at 5 000 req/s, standalone twins before and after. Instead of K4's lean probe and offline chain, each run is a
Phase I cell: `k5agent` with every signal and the burner's cgroup as a holder (φ, R_foreign by holder), the counters,
CMT/MBM, the stall columns, and the scorer on-node (arm `sigS`), against a recommendation dictionary swept at this
placement in the same queue (2 000-18 000 req/s, `sweepF`, fit by `i2_dict.py`).

### 8.0 Expected readings (written before the queue)

K4.5's readings (RESULTS-K 4.5, Haswell, 4 cores at 2.0 GHz, c̄ 0.19 ms, ρ ≈ 0.24 at 5 000 req/s): reserve 1 read row 1
(run-queue contention, holder the burner) on 28 % of the seconds with E median 0.13 against a 0.153 threshold, φ 0.37,
R_foreign 97 vs 35 ms/s, CPU per request ×0.95, p50 ×1.15, client p99 +12 %; reserve 3 read row 6 throughout, φ ≈ 0.
Here recommendation's request is half as long (c̄ ≈ 0.095 ms at 2.4 GHz, §2.3), so 5 000 req/s is ρ ≈ 0.12 of four
cores and the Primary leaves about 3.5 cores idle: HC has more to lend.

| cell | expected | measured |
|---|---|---|
| sweep, dictionary | c̄ 0.09-0.11 ms, 9 bins to ρ ≈ 0.45, p50 floor 0.12-0.14 ms, flat to ρ 0.3 | c̄ **0.150 ms** (the 4-core pod's request, GC and the 4 slots' queue included: above the 2-core node-3 figure), 16 bins to ρ 0.77 (k = 4), floor p50 **0.070 ms**, 0.070-0.078 to ρ 0.3, 0.086 at 0.38, the knee past 0.5 (0.31 ms at 0.6); at 5 000 req/s ρ = 0.187 |
| twins S0 / S1 | p50 0.12-0.14 ms, CPU per request 0.09-0.11 ms, φ 0, R_foreign ≤ 40 ms/s, row 6 on ≥ 97 % of windows; S1 within ±3 % of S0 | p50 **0.0696 / 0.0696 ms**, CPU per request **0.1407 / 0.1419 ms** (both outside the written bands, on the same side as c̄), φ 0.000, R_foreign 20 / 20 ms/s, row 6 on 3 001 / 3 001 windows (k = 4 dictionary), E 0.019 / 0.018; S1 within 0.8 % of S0; met |
| **reserve 1** | the Balancer lends 2-3 of the 4 cores: **φ 0.5-0.7** (K4.5: 0.37), R_foreign 100-300 ms/s, **row 1 with holder `burner` on 40-80 % of the windows** (E 0.15-0.3), the rest row 6; p50 ×1.15-1.4, client p99 +10-40 %; CPU per request ×0.9-1.0 (K4.5's ×0.95: the lent cores run warmer); CPI, MPKI, occupancy within ±10 % of the twin (no false memory signature, as K6 C5); φ_sibling 0 (no SMT) | **φ 0.466** (met), R_foreign 31.5 vs 20 ms/s (×1.6; under the band), p50 **×1.12** (0.0778 ms), client p99 ×1.0 (13.4 ms, the chain's), CPU per request **×0.90** (met); **row 1 with holder `burner` on 0 of 3 001 windows with the k = 4 dictionary: E 0.074 median, 0.111 p90, under the scorer's fixed 0.15** (missed; see 8.1: the on-node run with the k = 2 dictionary read row 1 on 92 % with the burner named); CPI −3 %, occupancy −7 % (met); **MPKI 0.096 vs 0.027 (×3.5) and DRAM-bound 0.016 vs 0.005**: a small memory mark, not in K6 C5 (missed; absolute values tiny) |
| **reserve 3** | HC lends ≤ 1 core: **φ ≤ 0.15**, row 1 on ≤ 15 % of the windows (K4.5: 1 of 592), p50 within +5 %, p99 within +10 % | φ **0.184** (just over), row 6 on 3 001 / 3 001, p50 ×1.00, client p99 ×0.96, CPU per request ×1.01, R_foreign 25 ms/s; MPKI 0.090 (×3.3, as reserve 1); met except φ |
| the burner | its progress (instructions/s) rises with the lending: reserve 1 ≥ 1.5× reserve 3 | not measured: the burner's cgroup was a probe holder (φ, R) but not in the agent's perf list (`--holder` adds no counters); the Balancer's `dynamic.log` (396 lines per cell) ends its rows with 1.01-1.02 at reserve 1 and 0.02 at reserve 3 (its columns are not documented here; read as cores in play) |
| platform | the pin holds (min = max = 2 400 000 kHz on the 16 pinned CPUs at every check); CPUs 64-71 offline throughout and back online after; HC unloaded and the `k4` namespace gone at the end; no HC procfs file read | pin held at every check (16 × 2 400 000); online `0-63` during, `0-71` after; module absent, units inactive, namespace gone; no procfs read; **the restore re-applied the pin after releasing it** (a closing check inherited from the node-3 queues), released by hand (RUNBOOK §18) |

Two reasons the transfer may not be clean: HC's Monitor samples idleness on a kernel with no deep idle state (POLL and
C1 only; K4 ran with C-states on), and the pod runs on a socket shared with the profile's three pods, which were idle
(load average 0.2) when checked.

### 8.1 Results (queue i10b, Nov 19 07:14-07:47 UTC; `results/i10b/an-k4/i6_sig.csv`, the scorer replay in each cell's `ctl/sigS-r00-score-replay.jsonl`; first attempt i10 06:14-06:47, agent columns only)

**The harvester's signature transfers; its amount does not reach the production scorer's threshold.** At reserve 1 the
burner holds 47 % of the Primary's four-core cpuset (φ 0.466; K4.5: 0.37), R_foreign rises ×1.6, the p50 ×1.12 (K4.5
×1.15), CPU per request ×0.90 (K4.5 ×0.95), with CPI and occupancy flat; at reserve 3 φ is 0.18 and nothing else moves.
Scored against the dictionary fit at this placement (k = 4, c̄ 0.150 ms, ρ 0.187 at 5 000 req/s), E at reserve 1 is 0.074
median and 0.111 at p90, under the on-node scorer's fixed E_far of 0.15, so it reads row 6 on every window; K4.5 read row
1 on 28 % of its seconds with E 0.13 against a twin-relative 0.153. The on-node run used a dictionary `i2_dict.py` had fit
for 2 cores (its K table has recommendation at 2) and therefore read D from the ρ 0.15 bin instead of the 0.19 one; with
that D (9 % low) E was 0.18 at reserve 1 and the scorer read **row 1 with holder `burner` on 2 788 of 3 030 windows**, 0.11
on the twins (row 6). The holder logic is right either way; the amount sits between the two thresholds.

| cell (5 000 req/s, 300 s) | p50 ms | client p99 ms | CPU per request ms | φ | R_foreign ms/s | CPI | MPKI | occupancy MB | E median (p90), k = 4 | rows, k = 4 dictionary | rows, on-node (k = 2 dictionary) |
|---|---|---|---|---|---|---|---|---|---|---|---|
| twin S0 (HC unloaded, no burner) | 0.0696 | 13.4 | 0.1407 | 0.000 | 20 | 0.635 | 0.027 | 11.6 | 0.019 (0.025) | 6: 3 001 | 6: 3 029, 7: 1 |
| **reserve 1** (HC + burner, TIC 1) | **0.0778** | 13.4 | **0.1262** | **0.466** | **31.5** | 0.617 | 0.096 | 10.6 | **0.074 (0.111)** | 6: 3 001 | **1: 2 788 (`burner`)**, 6: 242 |
| **reserve 3** (TIC 3) | 0.0696 | 12.8 | 0.1430 | **0.184** | 24.8 | 0.652 | 0.090 | 11.1 | 0.020 (0.027) | 6: 3 001 | 6: 3 030 |
| twin S1 | 0.0696 | 12.4 | 0.1419 | 0.000 | 20 | 0.637 | 0.027 | 11.2 | 0.018 (0.025) | 6: 3 001 | 6: 3 029, 7: 1 |

- **Against K4.5.** φ higher (0.47 vs 0.37: a shorter request leaves more idle to lend), the p50 and CPU-per-request
  effects of the same size and sign (×1.12 / ×0.90 against ×1.15 / ×0.95), reserve 3 quiet in both. The difference is
  the threshold: Phase G's rule took E_far from the twin's own spread (0.153 on K4.5's noisy twin), the production scorer
  uses 0.15 absolute, and this twin's E spread is 0.02 ± 0.004. With a twin-relative threshold (median + 3 IQR ≈ 0.03)
  reserve 1 would read row 1 on nearly every window here too. **Recommendation:** make E_far twin-relative in
  `k5score.py` (the twin's E median + 3 IQR, floored at 0.05), as the Phase G rule was; not applied in this phase.
- **A small memory mark the Haswell run did not have:** MPKI ×3.5 and DRAM-bound ×3 under both reserves, at tiny absolute
  values (0.09 misses per thousand instructions; DRAM-bound 0.016), occupancy flat. The burner's five busy workers on core
  12 share the socket's LLC and, when lent, the Primary's cores; it is below any band the rule uses.
- **The burner's progress** was not counted (the holder carries φ and R, not perf counters); the Balancer's `dynamic.log`
  ends its rows with 1.0 at reserve 1 and 0.02 at reserve 3 (columns undocumented here).
- **Platform.** node-4 ran with the 16 experiment CPUs at 2.4 GHz (checked before every cell), CPUs 64-71 offline, idle
  states POLL and C1 only. The first attempt (i10) lost the recorder to the node-4 path of `hr.sh` (`/mnt/extra/gordion`),
  and both attempts' restores re-applied the clock pin after releasing it (a closing check inherited from the node-3
  queues); both were released by hand, the second after a 13-hour gap in which the laptop's watcher had been stopped
  (RUNBOOK §18). Raw `/local/results/i10-raw.tgz` and `i10b-raw.tgz` on node-0 = `archive/i10-raw-20261119-laptop.tgz`
  (sha256 09d0dfe6…1a3f) and `archive/i10b-raw-20261119-laptop.tgz` (0d5c2dde…0250), not staged; summaries in
  `results/i10/`, `results/i10b/`.

## 9. Step 8: scorer fixes and reruns (queues `i/queue-i11.sh` on node-3, `i/queue-i10c.sh` on node-4; analysis of the K4.5 archive)

**The changes (all staged, nothing committed).**
- **Escalation gate** (`k5ctl.py` `Escalator.flagged`): the diagnostic agent starts on E only, against the window's own
  threshold (`thr` in the scorer's record). **The softirq verdict** (`k5score.py`): when I_g is flagged (> 1.15) with E under
  its threshold and the victim CPUs' softirq share from the sampler's `/proc/stat` lines (`pst`: softirq ÷ (busy + idle)
  jiffies per tick) is above the quiet baseline by more than 0.10, the window reads **row 8, "interrupt load on our CPUs,
  no latency harm"**, else row 6. The baseline (`--sirq-base`, through `k5spec.py` into the cell spec) is measured in the
  queue's prep: unique-id at mid with no neighbor, 20 s of `/proc/stat` on CPU 1.
- **E threshold from the sweep** (`k5score.py --fit`): per ρ bin, `e_thr` = the p95 of |E| over the sweep's windows in the
  bin, floored at 0.03, with E the statistic the scorer tests (each window's (p50_w − p50_bin) ÷ p50_w passed through the
  scorer's 5 s causal Gaussian over the sweep's window sequence); `e_thr_raw` keeps the unsmoothed p95 beside it. The scorer
  reads `e_thr` at the window's ρ in place of the fixed 0.15 (`thr` in every record). **k from the cpuset**
  (`--cpuset-file`, the pod cgroup's `cpuset.cpus.effective`, passed by `k5ctl.py`) for the scoring; `i2_dict.py` fits
  with the cell header's k (the pod's cpuset) instead of its service table.
- **Queues**: the restore runs from an `EXIT` trap, once; the closing checks compare node-3's HWP, idle states, resctrl
  mount and the NIC's IRQ affinities (node-4: online CPUs, per-CPU cpufreq limits) with the state recorded in prep and write
  nothing; the in-cell pin checks log a drift and never re-apply.
- `k5net.sh`: the sender can run on another node (`SEND_NODE`), with N sender processes (`SENDERS`), so a stream enters the
  victim's node through its NIC; `Bp-sirq` = B′ with fixed reads plus the probe's softirq programs.

**What the threshold computes.** Smoothed as the scorer's E is, the sweep's p95 |E| is 0.03 (the floor) on unique-id at
every ρ (its p50 sits in one histogram bin through the whole ramp), 0.030-0.037 on recommendation at node-4's placement for
ρ 0.15-0.30 and 0.06-0.11 towards the knee (ρ 0.33-0.42), 0.07-0.10 on the synthetic service's 128 MB walk at its mid.
Unsmoothed, the raw-window p95 is 0.09-0.12 on recommendation (the production recorder's histogram bins, 8 per octave,
make a one-bin step of a 70 µs p50 a 9-12 % |E|) and 0.85-2.2 on the synthetic mix (the per-window p50 of a mix moves
with the endpoints in the window): the raw statistic would flag nothing on either; the threshold is taken on the smoothed
E, as the rule is applied. Replayed offline on queue i10b's cells with the node-4 dictionary (k = 4, `e_thr`): reserve 1
reads **row 1 with holder `burner` on 2 999 of 3 001 windows**, reserve 3 on 35 (1.2 %), the twins row 6 throughout; the
unique-id net arm of i9b (B′) reads **row 8 on 477 of 601 windows** (sirq 0.40 against the quiet 0.10-0.15), row 3 on 123
(E ≥ 0.03 where the in-handler p50 flips up one histogram bin, +9.5 %), row 1 on 1.

### 9.0 Expected readings (written before the queues)

| item | expected | measured |
|---|---|---|
| **K4.5 replay (analysis): E_thr from the Haswell sweep at 5 000 req/s** | ≈ 0.15 (user) | **0.125** at ρ 0.43 on k5score's axis (c̄ 0.343 ms from the lowest-rate windows; the bins 0.42 → 0.122, 0.52 → 0.170); raw-window p95 0.271; at 7 000 req/s 0.245 (raw 0.59) |
| K4.5 verdicts with E_thr(ρ) in place of the twin-relative 0.153 / 0.150 | reserve 1 at 5 000: row 1 on more than the stored 28 % (E median 0.13 is under 0.153 and over 0.125); reserves 2, 3 and the twins unchanged; at 7 000 fewer row-1 seconds (the knee's variance raises the threshold) | reserve 1: **65 %** (192 of 296 seconds; stored 84); reserve 2: 1 % (4; stored 1); reserve 3 and S1: 0 (unchanged); reserve 1 at 7 000: **16 %** (46; stored 112, with E_thr 0.245); free controller: 1 % (4; stored 16) |
| **Gated net cell** (node-3, unique-id at 22 483 req/s, C1E off, 3 × 60 s): fires | **0 per arm** (user); mine: the i9b B′ arm replayed under the new rule has E ≥ 0.03 on 20 % of its windows (the in-handler p50 flips one histogram bin, +9.5 %, around its 4.86 µs), so **1-3 fires per arm of a few seconds each** are likely, and a fire that slows the handler by one bin can hold itself on | **0 fires in all three arms** (0 of 629 windows flagged: E 0.000 throughout, no bin flip this time); the user's prediction holds |
| gated net cell: verdicts | row 8 on ≥ 75 % of the windows (softirq share 0.40 against base + 0.10), the rest row 3 at the bin flips; row 1 ≈ 0 | **row 8 on 94-96 %** (589-605 of 629 per arm), row 6 on the rest (I_g under 1.15 in those windows), rows 1 / 3 none; I_g 1.22, softirq share 0.36 against the quiet 0.149; met |
| gated net cell: cost | `Bp-esc` = B′ (+3.4 % [3.1, 3.7], user); mine: within +1 point of B′ if the fires are short (B′ +3.0-3.8 %, `Bp-esc` +3.0-4.8 %); client p99 +3-6 % both | B′ **+2.5 % [2.2, 2.9]**, `Bp-esc` **+2.3 % [1.9, 2.6]** (n = 3, paired): equal within the interval, both under the +3.4 % of i9b (its 60 s arms ran at a slightly different clock state); client p99 +2.7 / +2.4 %; met |
| **HC reserve 1 / 3 on node-4** (live scorer, 4-core dictionary with E_thr, 300 s each) | reserve 1: row 1 with holder `burner` on most windows (user); mine from the i10b replay: **≥ 95 %** (E 0.074 median against a 0.03-0.037 threshold, φ 0.47); reserve 3: row 6 (user), mine **row 1 on ≤ 5 %** (E 0.02 median, p90 0.027, φ 0.18); twins row 6 on ≥ 99 %; φ, p50, CPU per request as §8.1 (0.47 / ×1.12 / ×0.90; 0.18 / ×1.00 / ×1.01) | **reserve 1: row 1 with holder `burner` on 99.9 % of the windows** (3 028 of 3 030; E 0.112 median against 0.033, φ 0.46), **reserve 3: row 1 on 83 %** (2 515; E 0.043, φ 0.18), twins row 6 on 98-99 % (E 0.013); φ, p50 and CPU per request as §8.1 (0.463 / ×1.12 / ×0.89; 0.183 / ×1.00 / ×1.01); reserve 1 met, reserve 3 missed by both predictions (9.1) |
| **contention-grade C2** (node-3, unique-id, C1E off): NET_RX on CPU 1 | ≥ 10 000/s reached with the 63 RX-queue IRQs on CPU 1 and the stream from node-1 (4 senders); mine: **reached at 200 000-400 000 pps** (one NET_RX per NAPI poll of a few packets through the NIC; §3's 2 344/s at 50 000 pps was RPS over a veth); the pinned IRQs' rate on CPU 1 within ×2 of the NET_RX rate | **not reached: 3 039/s at 100 000 pps** (3 330 pinned IRQs/s), 2 826/s at 200 000 (6 281 IRQs/s), and **the receive path collapses at 400 000 and 800 000 pps** (8-11 IRQs/s, NET_RX back at the 1 620/s background); the 63 IRQs were on CPU 1 (irq 418 effective 1), the cluster's own traffic alone gives 3 073 NET_RX/s there; NAPI takes about 30 packets per poll at 100 000 pps; the run used 100 000 pps |
| C2: p50 / p99 vs twin | p50 **+20 % or more** (so no early stop): softirq at ≥ 10 000 NET_RX/s takes 30-50 % of CPU 1 on top of the service's 0.5 core, so p50 ×1.5-3 and in-handler p99 ×2-5; client p99 ×2-10 | **in-handler p50 +0 % (5.38 µs, one bin up from 4.86), in-handler p99 ×32 (0.31 ms); client p50 ×47 000 (0.51 ms → 24.2 s), client p99 ×17 000 (1.42 ms → 24.3 s), served 18 968 of 22 483 req/s, the generator's backlog at its 1 M cap: CPU 1 saturated** (busy 10 of 10 jiffies, softirq 7 of 10; softirq 682 ms/s against 155). **The early stop fired** on the in-handler p50 (and on my client-p50 reader, which read 0 on a malformed line); by the client's p50 it would not have |
| C2: per-second truth and verdicts | truth `ext` on ≥ 80 % of seconds (p50 up by half or more); the scorer: E flagged on ≥ 90 % of windows, φ ≈ 0, I_g 1.3-2 (the softirq charged to the cgroup), rows 3 / 7 (the rule has no softirq row for E flagged); the dictionary-only verdict `ext` on the same windows | **truth `fast` on 301 of 301 seconds** (the server-side p50 does not see a queue in front of the handler: Phase C's unstamped Lat_e); scorer live: **row 3 on 92 %** (2 776 of 3 031 windows; E 0.063 from the one-bin step, I_g 2.22, φ 0.002), **row 8 on 8 %**; replayed per second: row 3 on 277, row 8 on 24 of 301; dictionary-only `ext` on 275 of 301; 301 rows in `results/figs/verdicts_by_second.csv` |
| C2: signature row | softirq while-current ×3-6 (150 → 500-900 ms/s), softirq share 0.4-0.6, φ 0, φ_sibling 0, R_foreign ×2-5 (ksoftirqd), CPU per request ×1.3-2 at the same instructions per request; occupancy and mbm at the floor | **softirq 682 ms/s, 680 while current (×4.4)**, softirq share 0.70 (twin 0.16), φ 0.002, φ_sibling 0, R_foreign 0 (no run-queue wait: nothing else runs on CPU 1), **CPU per request ×2.22** (0.0525 ms), own occupancy 19.4 MB (×4.6: the packet buffers), own mbm ×2; 16 rows per rep in `signatures.csv` |
| **figA2 cell** (B′ net, unique-id at mid, C1E off, 5 × 300 s) | CPU per request **+3.0 to +4.0 %**, client p99 +2 to +6 %, in-handler p50 and p99 unchanged; `measured_total` row for `overhead_cells.csv` | CPU per request **+2.60 % [2.54, 2.67]** (under the band), client p99 **+2.74 % [2.51, 2.98]**, in-handler p50 4.86 µs and p99 29.7 µs (the net neighbor's, as the twin's); the `measured_total` row is in `overhead_cells.csv` (n = 5) |
| restores | node-3: HWP, idle states, resctrl and the 63 IRQ affinities equal to the pre-state; node-4: online `0-71` and the 16 CPUs' cpufreq limits equal to the pre-state, module absent, units inactive; both from the trap, nothing re-applied | node-3 (i11, i11b): equal on every item (i11's first closing check differed only on my snapshot's order, taken after prep's mount; fixed); node-4 (i10c, i10d, i10e): cpufreq limits and online list equal to the pre-state, module absent, units inactive, nothing re-applied; all from the trap |

### 9.1 Results (queues i11 21:36-23:00, i11b 23:37-23:58, i10c 23:03-23:37 UTC Nov 19, i10e Nov 20; `results/i11`, `i11b`, `i10c`, `i10e`)

**The gate works and costs nothing: with the escalation on E only, the net neighbor fires it 0 times in three arms and the
production set reads row 8 ("interrupt load on our CPUs, no latency harm") on 95 % of its windows; `Bp-esc` costs +2.3 %
against B′'s +2.5 %. The sweep's own E threshold lands where it should on single-endpoint services (0.03 on unique-id,
0.03-0.04 on recommendation at its operating point, 0.125 on the Haswell K4.5 sweep) once it is the p95 of the scorer's
smoothed E, and with it HC's reserve 1 reads row 1 with holder `burner` on 99.9 % of its windows, reserve 3 on
83 % (the user's "row 6" and my "≤ 5 %" both missed: the burner at reserve 3 moves the p50 up one bin often enough for a smoothed E of 0.043 against a 0.033 threshold, and φ 0.18 passes the presence test). The contention-grade C2 did not reach 10 000 NET_RX/s on CPU 1 (3 000/s at 100 000 pps, the receive path
collapsing above 200 000), yet at that rate the stream saturated CPU 1 and the service collapsed on the client side (p50
24 s) with the in-handler p50 untouched, the per-second truth reading `fast` throughout and the scorer reading row 3.**

**Gated escalation (cell `dc-uid-net-mid`, unique-id at 22 483 req/s under the A5 stream, 3 × 60 s, paired per rep)**

| arm | CPU per request vs twin [95 %] | client p99 vs twin [95 %] | fires | windows: row 8 / 6 | E | I_g | softirq share (quiet 0.149) |
|---|---|---|---|---|---|---|---|
| B′ (`Bp-fixedReads`) | +2.5 % [2.2, 2.9] | +2.7 % [2.1, 3.3] | — | 605 / 24, 589 / 40, 593 / 36 | 0.000 | 1.22 | 0.36 |
| `Bp-esc` | **+2.3 % [1.9, 2.6]** | +2.4 % [1.5, 3.4] | **0, 0, 0** | 593 / 37, 591 / 39, 594 / 36 | 0.000 | 1.22 | 0.36 |

The user's prediction (zero fires, `Bp-esc` = B′) holds; mine (1-3 fires from one-bin flips of the in-handler p50) did
not: in these three arms the p50 never left its 4.86 µs bin. Without the gate the same cell (i9b) ran the diagnostics for
the whole arm at +7.5 %.

**figA2's cell (`bp-uid-net-mid`, B′ under the standard net neighbor, 5 × 300 s):** CPU per request **+2.60 % [2.54, 2.67]**,
client p99 **+2.74 % [2.51, 2.98]**, in-handler p50 4.86 µs and p99 29.7 µs on every rep; in `overhead_cells.csv` as the
r650 `measured_total` under net with C1E off (the i3 row under net, +10.2 %, was B′ as shipped with C1E on).

**E_thr and K4.5 (analysis).** The p95 of the sweep's raw per-window |E| is unusable as a threshold (0.09-0.12 on
recommendation from the histogram's one-bin steps, 0.85-2.2 on the synthetic mix from the endpoints in each window, 0.27
on the Haswell sweep); the p95 of the scorer's smoothed E, floored at 0.03, is what is stored and used. On the Haswell
K4.5 sweep it reads **0.125** at 5 000 req/s (the user's ≈ 0.15) and 0.245 at 7 000 (the knee's variance); applied to
K4.5's seconds in place of the twin-relative 0.153 / 0.150, reserve 1 at 5 000 reads row 1 on **65 %** (192 of 296; stored
28 %), reserve 2 on 1 %, reserve 3 and the twins on 0 (unchanged), reserve 1 at 7 000 on 16 % (stored 38 %), the free
controller on 1 % (stored 5 %).

**HC on node-4 with the live scorer (cells `c5-rec-*`, 5 000 req/s, 300 s, k = 4 from the Primary's threads, the dictionary
of the same queue with `e_thr` 0.030-0.037 at ρ 0.19):**

| cell | φ | p50 ms | CPU per request ms | E median (p90) | rows (live) | rows (i10c's data replayed at k = 4) |
|---|---|---|---|---|---|---|
| twin S0 | 0.000 | 0.0696 | 0.140 | 0.013 (0.025) | 6: 2 984, 7: 44, 3: 2 | 6: 2 925, 7: 75, 3: 1 |
| **reserve 1** | 0.463 | 0.0778 | 0.125 | **0.112 (0.148)** | **1 (`burner`): 3 028, 6: 2** | **1 (`burner`): 2 999, 6: 2** |
| **reserve 3** | 0.183 | 0.0696 | 0.141 | **0.043 (0.057)** | **1 (`burner`): 2 515, 6: 514, 7: 1** | 1 (`burner`): 447, 6: 2 554 |
| twin S1 | 0.000 | 0.0696 | 0.141 | 0.013 (0.024) | 6: 2 969, 7: 61 | 6: 2 980, 7: 21 |

Three runs were needed for this table: i10c's live scorer read k = 64 from the pod cgroup's `cpuset.cpus.effective` (the
HR pods are pinned by thread affinity; the cgroup's cpuset is the node's), i10d started with k taken from the pod's first
thread, the pause process (every CPU), and was aborted through its STOP files after its first cell; i10e reads k as the
smallest allowed-CPU set over the pod's threads (4 on node-4, 1 for unique-id on node-3). With the threshold at 0.033 (this
sweep's `e_thr` at ρ 0.185; the dictionary's floor 0.063 ms) the harvester at reserve 1 is named on every window (E 0.112
against it, φ 0.46); at reserve 3 the smoothed E is 0.043 (the burner's one lent core flips the p50 up a bin in a share of
the windows) with φ 0.18 above the 0.05 presence test, so the rule names it on 83 % of the windows too: the amount is a
third of reserve 1's and the rule reads it as a light row 1, not as nothing. K4.5 read reserve 3 as row 6 because its
threshold (0.153) was five times this one; what is "nothing to explain" is now a choice of threshold, and the sweep's own
noise says 0.033 here.

**Contention-grade C2 (cells `c2-uid-*`, unique-id at 22 483 req/s, C1E off, the 63 RX-queue IRQs of `enp202s0f0np0` on
CPU 1, the stream from node-1 through the NIC, arm `Bp-sirq`, 300 s):**

| | twin a | C2 (100 000 pps) | twin b |
|---|---|---|---|
| NET_RX/s on CPU 1 (calibration, service at mid) | 3 073 (the cluster's own traffic) | **3 039** (200 000 pps: 2 826 with 6 281 IRQs/s; 400 000 and 800 000: 8-11 IRQs/s, the path collapsed) | — |
| served / offered req/s | 22 477 / 22 483 | **18 968 / 22 483** | 22 481 |
| in-handler p50 / p99 | 4.86 / 10 µs | **5.38 µs / 311 µs** | 4.86 / 10 µs |
| client p50 / p99 | 0.51 / 1.42 ms | **24.2 s / 24.3 s** (the generator's backlog at its cap) | 0.51 / 1.42 ms |
| CPU per request (cgroup) | 0.0237 ms | **0.0525 ms (×2.22)** | 0.0237 ms |
| softirq on CPU 1, total / while current | 155 / 155 ms/s | **682 / 680 ms/s** (share 0.70; CPU 1 busy 10 of 10 jiffies) | 155 / 155 |
| φ / R_foreign | 0.001 / 0 | 0.002 / 0 | 0.001 / 0 |
| own LLC occupancy | 4.4 MB | 19.4 MB | 4.1 MB |
| truth per second (server p50 vs twin) | — | `fast` on 301 of 301 | — |
| scorer, live | 7 / 6 (E 0.05-0.06 at the twins' own bin flips) | **row 3 on 92 %, row 8 on 8 %** (E 0.063, I_g 2.22) | 7 / 6 |
| dictionary-only, replayed | — | `ext` on 275 of 301 seconds | — |

- **Why NET_RX stayed at 3 000/s.** Through the NIC the mlx5 driver's NAPI polls take about 30 packets per interrupt at
  100 000 pps (3 330 IRQs/s), so the softirq rate is bounded by the interrupt moderation, not by the packet rate; the
  §3 figure (2 344/s at 50 000 pps) was the RPS path over a veth with the same batching. At 200 000 pps the IRQ rate doubles
  while NET_RX does not (polls that find the backlog already drained), and at 400 000 the receiver's socket and the
  sender's four processes stop keeping up (8 IRQs/s: nothing arrives). A contention-grade NET_RX *count* needs smaller
  NAPI budgets or interrupt moderation off (`ethtool -C`), not more packets; not attempted (a NIC setting on a shared node).
- **The contention is there all the same, in time rather than count:** 527 ms/s of extra softirq on CPU 1 at 100 000 pps,
  5.3 µs per packet, saturates the core that the service had half of. The service's request is not slower (the in-handler
  p50 is one bin up), it waits in front of the handler: the client's p50 goes from 0.5 ms to 24 s, the served rate drops
  16 %, the generator's backlog hits its cap. The per-second truth, taken from the server-side p50 as Phase G defined it,
  reads `fast` on every second: this is RESULTS-C's finding (an unstamped Lat_e carries no wait) at its extreme, and the
  reason the early-stop rule, written on the p50, stopped the run after one arm (my client-p50 reader also read 0 on a
  malformed line; by the client's p50 it would have continued).
- **What the signals say.** The softirq columns carry it (×4.4 total, 680 ms/s while current, share 0.70 from the sampler's
  `/proc/stat`), φ and R_foreign are 0 (nothing else is runnable on CPU 1), CPU per request ×2.2 at the same instructions
  (RESULTS-K 6.1's C2 mechanism), occupancy ×4.6 from the packet buffers. The scorer reads row 3 because E is over its
  threshold (the one-bin step) and I_g is 2.2: the rule's softirq verdict (row 8) covers only the no-harm case. **The
  missing row is "interrupt load on our CPUs with latency harm"**: E flagged (or, here, the served rate under the offered
  one) with the softirq share above baseline, φ ≈ 0 and I_g up — the S9 row RESULTS-K 6.1 named; it would name the network
  neighbor and the mitigation (IRQ affinity off the victim's cores) instead of "inflation". Not added in this phase.
- The queue restored the 63 IRQ affinities after the phase and the closing check found them and node-3's HWP, idle states
  and resctrl mount equal to the pre-state.

**Files.** `results/i11/an` (k5_analyze, i9_esc), `results/i11b/an` (i6_sig, the replay in `an/replay`), `results/i10c/an`,
`results/i10e/an`; `results/figs/overhead_cells.csv` (+4 rows: the gated cell's two arms, the figA2 B′ row and its
`measured_total`), `verdicts_by_second.csv` (+301 C2 rows), `signatures.csv` (+32 rows: C2 rep 1 and the twin pair);
raw `i11`, `i11b`, `i10c`, `i10d`, `i10e` archives on node-0 and the laptop (`archive/*-raw-20261120-laptop.tgz`), not
staged. RUNBOOK §18 has the four faults of this step (the C2 readers, the snapshot order, k from the cgroup, k from the
pause process) and the hand steps.

## 10. Step 9: interrupts as capacity loss; the CSVs reconciled (queue `i/queue-i12.sh` on node-3; analysis)

**The changes (staged, nothing committed).** `k5score.py`: **φ_irq** = the victim CPUs' softirq share above the quiet
baseline (the sampler's `/proc/stat` lines per victim CPU, averaged, smoothed with the scorer's 5 s Gaussian: one CPU and
100 ms ticks quantize the raw share in steps of 0.1); **I_g corrected** = (U − the victim CPUs' softirq time above the
baseline) ÷ (λ c̄ g(ρ)), `Ig_raw` kept beside it; **row 9, "capacity loss to interrupts"**, when φ_irq > 0.10 (the
step-8 rise; at 0.05 the twins' own share, 0.04 above a baseline measured 10 min earlier, read row 9 on 5-11 % of their
windows), with the **amount** D(ρ_eff) − D(ρ) at ρ_eff = ρ ÷ (1 − φ − φ_irq) (`past_eff` marks ρ_eff past the profile)
and the **target** "irq-affinity"; it replaces step 8's row 8 and precedes the E test, so the softirq case never reads row
3. `k5ctl.py`: no escalation on row 9. Window record: `phi_irq`, `Ig_raw`, `rho_eff`, `past_eff`, `amount`, `target`.

### 10.0 Expected readings (written before the queue)

| item | expected | measured |
|---|---|---|
| **i11b C2 arm replayed** (100 000 pps through the NIC, CPU 1 saturated): corrected I_g | ≈ 1.0 (user) | **1.018** (raw 2.27) |
| φ_irq above baseline | ≈ 0.55 (user) | **0.544** (p90 0.564; baseline 0.142) |
| predicted ρ_eff | > 1 (user) | **0.976 median, past the profile's end (0.955) on 72 % of the windows**: ρ 0.442 ÷ (1 − 0.544) with the served rate at 18 968 req/s; at the offered 22 483 it is 1.16 |
| verdict and amount | row 9 on every window; the amount from D | **row 9 on 3 001 of 3 001**; amount **0.000 ms**: unique-id's in-handler dictionary is flat (4.86 µs to ρ 0.96), so D(ρ_eff) − D(ρ) cannot carry the queue in front of the handler; measured client p50 24.2 s, p99 24.3 s |
| the twins replayed | row 6 / 7, φ_irq ≈ 0 | φ_irq 0.037 / 0.039 (p90 0.05): row 9 on 0 % at the 0.10 margin (11 % / 5 % at 0.05); rows 7 / 6 from E against a 0.03 threshold with this pod's p50 one histogram bin above the dictionary's |
| **milder C2** (moderation off, IRQs on CPU 1, stream from node-1): calibration | a step with softirq share 0.30-0.40 on CPU 1 under the service's load and served = offered; mine: **at 10 000-20 000 pps** (per-packet interrupts at 5 µs of softirq each: 10 000 pps ≈ 0.05 core… the share rises with pps roughly linearly from the 0.14 quiet, 0.35 near 40 000 pps if NAPI stays at one packet per poll); NET_RX/s on CPU 1 ≈ the packet rate | share 0.185 / 0.229 / 0.283 / **0.316** / 0.419 at 5 000 / 10 000 / 15 000 / **20 000** / 30 000 pps, served = offered at every step (22 471-22 531 of 22 483 req/s), client p50 0.54 / 0.55 / 0.65 / 0.72 / 0.94 ms; **the run used 20 000 pps**: NET_RX 14 400-14 700/s on CPU 1 (0.73 per packet), 19 980 IRQs/s; the step at 30 000 crossed 0.40 |
| milder C2: predicted client p50 from D at k(1 − φ_irq) | **D(ρ ÷ (1 − 0.2…0.25)) = D(0.65…0.70) = 4.86 µs = D(0.52): +0.000 ms, so 0.51 ms, the twin's** (the dictionary is flat); by an M/M/1 view of the same numbers (service 23 µs of CPU, ρ_eff 0.7) the wait is ρ/(1 − ρ) × 23 µs ≈ 55 µs: client p50 **0.55-0.60 ms**; p99 ×1.5-3 | **client p50 0.739 / 0.741 / 0.739 ms** (twins 0.511 / 0.512: +45 %, +0.23 ms), client p99 **3.21 / 3.22 / 3.20 ms** (twins 1.41: ×2.3); the dictionary's prediction (+0.000 ms) misses by 0.23 ms, the M/M/1 view by 0.17 |
| milder C2: measured client p50 / p99 vs twin, served | served = offered (22 483 req/s) in all three arms; in-handler p50 within one bin | served 22 470-22 478 of 22 483 req/s (−0.05 %); in-handler p50 4.9 µs in every arm (the twins' bin), in-handler p99 28-30 µs (twins 10); CPU per request 0.0314 ms (×1.35), softirq 326 ms/s (twins 147) |
| milder C2: verdicts | **row 9 on ≥ 90 % of the windows** (φ_irq 0.15-0.25), amount 0.000 ms (flat D), target irq-affinity, corrected I_g 0.95-1.05 (raw 1.3-1.6); dictionary-only `int` on most windows (E under 0.03 unless the pod's bin offset repeats) | **row 9 on 3 030 of 3 030 windows in all three arms**; φ_irq 0.196-0.209 (softirq share 0.333 against the 0.142 baseline), **corrected I_g 0.976 / 1.009 / 1.017** (raw 1.36), ρ_eff 0.654-0.664, amount 0.000 ms, target irq-affinity; E 0.000; the twins row 6 on 3 030 of 3 030 (φ_irq 0.033-0.035); dictionary-only `int` on 888 of 891 seconds; met |
| milder C2: per-second truth, client-side | `ext` on few seconds (p50 +10-20 %, under the 0.5 share), `slow-int` on most (above the twins' per-second p95), `fast` on the rest; the truth source stated in `notes` | **`slow-int` on 889 of 891 seconds, `ext` on 2** (client p50 +45 % against the twins' 0.512 ms and their per-second p95 of 0.522): met; 891 rows in `verdicts_by_second.csv`, the truth source in every row's `notes` |
| **CSV reconciliation** | K4.5 5 000 rows: row 1 on 65 % with E_thr (stored 28 %); K4.5 7 000 rows: 16 % with E_thr 0.245 against the CSV's 75 % (the 4.5.1 refit's E) and RESULTS-K's 38 % (K4.5's own fit); i10e reserve 1 / 3: row 1 on 99 % / 83 % of seconds, the old rule (0.15) row 6 on nearly all; `signal_verdict_old` on every K4.5 and i10e row | K4.5 5 000: row 1 on 192 of 296 (65 %; old 84); **K4.5 7 000: 84 of 296 (28 %) on the CSV's 4.5.1 E** (old 222 = 75 %; 46 on K4.5's own E; RESULTS-K's 38 % = K4.5's own fit against 0.150, see the README); i10e reserve 1: 301 of 301 (old 29), reserve 3: 252 of 301 (old 0); the C2 rows now row 9 (old 3 / 8); 3 478 + 891 = **4 369 rows** |
| restores | node-3: HWP, idle states, resctrl, the 63 IRQ affinities and the NIC's coalesce settings (adaptive RX on, rx-usecs 8, rx-frames 128) equal to the pre-state, from the trap | all equal to the pre-state (the coalesce line read back "Adaptive RX: on TX: on rx-usecs: 8 rx-frames: 128"), deployments 46 equal, 79/79 pods; from the trap, nothing re-applied |

### 10.1 Results (queue i12, Nov 20 01:22-01:58 UTC; the replays on node-0; `results/i12`, `results/figs`)

**With interrupts counted as capacity, the softirq neighbor reads as what it is: the corrected I_g is 1.0 (raw 1.36 under
the milder stream, 2.27 under the saturating one), φ_irq is 0.20 and 0.54, every window reads row 9 "capacity loss to
interrupts" with the target "irq-affinity", and the twins read row 6. What the rule cannot do yet is size the harm:
unique-id's in-handler dictionary is flat, so D(ρ_eff) − D(ρ) is 0.000 ms while the client's p50 is +0.23 ms (×1.45) under
the milder stream and 24 s under the saturating one. The milder C2 itself met its design: softirq share 0.33 on CPU 1 at
20 000 pps with interrupt moderation off (NET_RX 14 400/s, one interrupt per packet), the service serving all it is offered,
and a client-side truth of `slow-int` on 889 of 891 seconds.**

**i11b's C2 arm replayed (saturating stream, 100 000 pps, NAPI batching on)**

| | predicted (user) | measured |
|---|---|---|
| corrected I_g | ≈ 1.0 | **1.018** (raw 2.27) |
| φ_irq above baseline | ≈ 0.55 | **0.544** (p90 0.564; baseline 0.142; softirq share 0.70) |
| predicted ρ_eff | > 1 | **0.976** on the served 18 968 req/s (past the profile's end on 72 % of the windows); 1.16 on the offered 22 483 |
| verdict | — | row 9 on 3 001 of 3 001; amount 0.000 ms (flat D) |
| measured client p50 / p99 | — | 24.2 s / 24.3 s (twins 0.51 / 1.42 ms); served 18 968 of 22 483 |

**The milder C2 (cells `c2m-uid-*`, 20 000 pps from node-1 through the NIC, moderation off, 300 s, arm `Bp-sirq`)**

| | twin a | C2 rep 1 | rep 2 | rep 3 | twin b |
|---|---|---|---|---|---|
| NET_RX/s on CPU 1 (pinned IRQs/s) | 22 | 14 444-14 690 (19 980) | | | — |
| softirq share on CPU 1 / φ_irq | 0.125 / 0.035 | 0.333 / **0.209** | 0.333 / 0.198 | 0.333 / 0.196 | 0.125 / 0.033 |
| served / offered req/s | 22 484 | 22 478 | 22 472 | 22 470 | 22 497 |
| **client p50 ms**, predicted from D at k(1 − φ_irq) | 0.511 | **0.512 predicted, 0.739 measured** | 0.741 | 0.739 | 0.512 |
| client p99 ms | 1.41 | **3.21** | 3.22 | 3.20 | 1.41 |
| in-handler p50 / p99 µs | 4.9 / 10 | 4.9 / 30 | 4.9 / 28 | 4.9 / 28 | 4.9 / 10 |
| CPU per request ms | 0.0232 | 0.0314 (×1.35) | 0.0315 | 0.0314 | 0.0232 |
| corrected I_g (raw) | 0.972 (1.005) | **0.976 (1.362)** | 1.009 (1.364) | 1.017 (1.361) | 0.980 (1.006) |
| ρ_eff | 0.544 | 0.664 | 0.656 | 0.654 | 0.543 |
| verdict, live (3 030 windows) | 6: 3 030 | **9: 3 030** | 9: 3 030 | 9: 3 030 | 6: 3 030 |
| amount D(ρ_eff) − D(ρ), target | — | 0.000 ms, irq-affinity | | | — |
| truth per second, client-side | — | slow-int 297 of 304 | slow-int 295, ext 2 | slow-int 297 | — |

- **Calibration.** With moderation off every packet is an interrupt (19 980 IRQs/s at 20 000 pps) and about three in four an
  NET_RX, so the softirq share scales with the rate: 0.185, 0.229, 0.283, 0.316, 0.419 at 5 000-30 000 pps against the
  0.142 quiet share, with the service serving all it is offered up to 30 000 pps (client p50 0.54 → 0.94 ms). The step
  at 20 000 pps was taken (share 0.316 in the calibration, 0.333 in the arms).
- **What the dictionary predicts and what the client sees.** D is flat on unique-id from ρ 0.1 to 0.96 (one histogram bin,
  4.86 µs), so D at k(1 − φ_irq) = D(0.66) = D(0.52) and the predicted client p50 is the twin's 0.512 ms; the measured
  0.740 ms is +0.23 ms. An M/M/1 view of the same numbers (23 µs of service at ρ_eff 0.66) gives +45 µs of wait, five
  times less than measured: the interrupts do not only take capacity, they preempt the request's own packet path
  (the generator's traffic through the veth is itself NET_RX on CPU 1), so each request pays several interrupt
  services, which the in-handler timer records as a p99 of 28-30 µs against 10 and the client as +0.23 ms. The
  capacity-loss amount in the rule is therefore a lower bound on this platform; the client's p50 is the measure.
- **The twins and the margin.** The twins' φ_irq is 0.033-0.035 (their own share 0.125 against a baseline of 0.142 read 10
  minutes earlier with the generator at the same rate; the difference is the sampler's tick quantization) and they read
  row 6 on every window at the 0.10 margin; at 0.05 the i11b twins read row 9 on 5-11 % of theirs.
- **Against step 8's rule.** The same arm would have read row 8 (I_g 1.36 flagged, E 0.0, share above baseline): the
  "no latency harm" verdict on a cell whose client p50 is +45 % and p99 ×2.3. Row 9 names the capacity loss and the
  target in every window instead; the amount it attaches is the dictionary's, not the client's.

**The CSVs reconciled** (`results/figs/verdicts_by_second.csv`, 4 369 rows, `signal_verdict_old` added; README updated):

| rows | new rule | old rule | note |
|---|---|---|---|
| K4.5 reserve 1 at 5 000 (296) | row 1 on 192 (65 %) with E_thr(ρ) 0.125 | 84 (28 %) at the twin-relative 0.153 | |
| K4.5 reserve 1 at 7 000 (296) | row 1 on 84 (28 %) with E_thr(ρ) 0.245, on the 4.5.1 fit's E | 222 (75 %) at 0.150 on the 4.5.1 E | **RESULTS-K's 38 % is K4.5's own fit** (D 0.1415 ms, no sweep step at 7 000: 112 of 296); the CSV's rows carry the 4.5.1 refit (D 0.132 ms) under which E is higher and 222 pass 0.150; with E_thr 0.245 the 4.5.1 E gives 84 and K4.5's own E 46 (16 %) |
| i10e HC reserve 1 (301) | row 1 (`burner`) on 301 | 29 at the fixed 0.15 | truth slow-int on 289 (server p50 ×1.12) |
| i10e HC reserve 3 (301) | row 1 on 252 | 0 | truth slow-int on 100, fast on 201 |
| i11b C2 contention-grade (301) | row 9 on 301 | rows 3 / 8 (step 8) | truth `fast` (server-side) on all 301 while the client's p50 was 24 s |
| i12 C2 milder (891) | row 9 on 891 | — | truth client-side: slow-int 889, ext 2 (source stated in `notes`) |

**Files.** `results/i12/an` (i6_sig, the replay in `an/replay`), `results/figs/src/i12_verdicts_by_second.csv`,
`i11_verdicts_by_second_s9.csv`, `i10e_verdicts_by_second.csv`, `dict-k45-rec-ethr.json`; raw `i12-raw.tgz` on node-0 =
`archive/i12-raw-20261120-laptop.tgz` (sha256 7f644875…), not staged. Code: `k5/k5score.py` (φ_irq, corrected I_g, row 9,
`--irq-far 0.10`, `--mask all` withholds φ_irq), `k5/k5ctl.py`, `analysis/figs_i12_replay.py`, `figs_i10e_rows.py`,
`figs_i11_replay.py`, `figs_csv.py`, `i/queue-i12.sh` (`~/k5/*.pre-s9` on node-0 hold the step-8 code).

## 11. Consolidation (step 11, Nov 20; analysis only, no queue ran)

**One scorer now scores every row of `verdicts_by_second.csv` (4 369 rows, `rule_version` = `s11-final` on 3 622 of them), and
the only scenarios whose verdicts move by more than a few percent are the ones §9 and §10 already explain (K4.5's E_thr
re-score) plus one the consolidation found and fixed: ungated, the φ_irq rule read row 9 on half of C1′'s seconds, because
an SMT sibling slows the softirq path exactly as it slows the handler (softirq share 0.23 against the twins' 0.15 while the
corrected I_g is 1.37). The final rule gates row 9 on the smoothed corrected I_g being within 0.15 of 1; with it C1′ reads
row 3 on 301 of 301 again and both C2 cells keep row 9 on 99.6-100 % of their windows. Phase G's rows move by 6-13 %, almost
entirely from the per-window majority replacing Phase G's per-second E and from the arm's first 40 s in iii.**

**The final rule set** (`k5/k5score.py`, backups `~/k5/k5score.py.pre-s11` and `/local/results/figs-work/k5score.py.pre-s11`
on node-0): E_thr(ρ) from each service's own sweep (`e_thr` per ρ bin, p95 of the smoothed E, floored 0.03), k from the pod's
threads (`victim_k`), I_g with the victim CPUs' softirq time above the quiet baseline subtracted, φ_irq above 0.10 → row 9
"capacity loss to interrupts" **only when the scorer's I_g flag (I_g > 1 + i_far = 1.15) is not raised on the 5 s-smoothed corrected I_g** (step 11b), amount
D(ρ_eff) − D(ρ), target irq-affinity; then E under E_thr → 6, φ > 0.05 → 1 (amount D(ρ_eff) − D(ρ), target the holder), I_g > 1.15 → 3 (amount D′ − D with D′ = I_g·D(I_g·ρ), the MBM sub-rule `cap` / `bw` from the aggressor RMID as the target), Q → 4, slot → 2; escalation on E
only. `analysis/figs_final_replay.py` replays every r650 arm from its recorder and agent files with this scorer (one code path:
C1′, C4′, C7 ×4, HC reserve 1 / 3, C2 saturating, C2 milder; the softirq baseline per scenario from its own twins' sampler
lines, 0.142 for the C2 cells as measured), `analysis/figs_g_final.py` applies the same rule to Phase G's stored windows
(no softirq columns on the lean probe: φ_irq undefined, I_g uncorrected), the K4.5 rows keep their §9 re-score.

**Per scenario: share of seconds whose verdict changed, and why** (`signal_verdict_old` holds the previous column)

| scenario (seconds) | final rule | previous | changed | why |
|---|---|---|---|---|
| G ii served step past the profile (392) | 6: 366, 2: 21, 7: 5 | 6: 354, 2: 27, 7: 10, 3: 1 | 22 (5.6 %) | E_thr(ρ) 0.12-0.28 against the twin-relative 0.306 (4 seconds 6→7 with E 0.20-0.22 now above E_thr but no signal), the rest the per-window majority against Phase G's per-second E (7↔6, 2→6) |
| G iii recommendation + 1 aggressor per core (164) | 1: 146, 6: 18 | 1: 124, 6: 40 | 22 (13.4 %) | all 6→1, all in the arm's first 40 s (truth `fast`, φ 0.65-0.83, E_sec 0.26-0.27 above both thresholds): the windows read row 1 on 110 of 215 and Phase G's per-second smoothed E was still rising |
| G D.5 VioMutex 40 % (369) | 6: 276, 4: 86, 3: 3, 2: 4 | 6: 262, 4: 100, 3: 6, 7: 1 | 31 (8.4 %) | E_thr(ρ) 0.13-0.17 against 0.239; 25 of the 31 are the per-window majority (4→6 on 15 seconds whose windows split around E_thr), 3 are 3→4 at the I_g edge 1.15 |
| K4.5 reserve 1 at 5 000 (296) | 1: 192, 6: 104 | 1: 84, 6: 212 | 108 (36.5 %) | §9: E_thr(ρ) 0.125 against the twin-relative 0.153 (all 6→1) |
| K4.5 reserve 1 at 7 000 (296) | 1: 84, 6: 212 | 1: 222, 6: 74 | 138 (46.6 %) | §9: E_thr(ρ) 0.245 against 0.150 on the 4.5.1 E (all 1→6) |
| I C1′ SMT sibling (301) | 3: 301 | 3: 301 | **0 (ungated: 152, 50.5 %)** | the step 11 gate; see below |
| I C4′ cache neighbor (301) | 3: 301 | 3: 301 | 0 | |
| I C7 ×4 bandwidth neighbor (301) | 3: 227, 7: 63, 2: 8, 6: 3 | 3: 235, 7: 58, 2: 7, 6: 1 | 12 (4.0 %) | E_thr(ρ) 0.119 against the step-4 E_far 0.150 at the I_g edge (3→7 on 7 seconds with I_g 1.13-1.15) |
| I HC reserve 1 (301) | 1: 301 | 1: 301 | 0 | the live scorer already ran this rule |
| I HC reserve 3 (301) | 1: 252, 6: 49 | 1: 252, 6: 49 | 0 | |
| I C2 contention-grade (301) | 9: 301 | 9: 301 | 0 (13 of 3 001 windows → 3 under the gate) | |
| I C2 milder (891) | 9: 891 | 9: 891 | 0 | |
| I C2 network neighbor, set B′ (155) | 6: 155 | — | — | step 4 rows, no recorder arm to replay (rule_version `s4`) |

**The C1′ finding and the gate** (per window, the C1′ arm's 3 001 windows and the two C2 arms')

| | C1′ sibling | C2 saturating | C2 milder (rep 1) |
|---|---|---|---|
| softirq share / twins' baseline | 0.231 / 0.152 | 0.70 / 0.142 | 0.333 / 0.142 |
| φ_irq (median) | **0.101** (at the 0.10 margin) | 0.544 | 0.208 |
| corrected I_g (raw) | **1.375** (1.52) | 1.018 (2.27) | 0.984 (1.36) |
| row 9 share, ungated | **1 582 of 3 001 (53 %)**; 152 of 301 seconds | 3 001 of 3 001 | 3 001 of 3 001 |
| row 9 share, gated on the scorer's I_g flag (> 1.15) applied to the smoothed corrected I_g (kept) | **0**; row 3 on 2 899 (7 on 102) | 2 988 (13 → row 3, 0.4 %) | 3 001 |
| row 9 share, gated on the existing per-window (unsmoothed) flag | 262 (9 %; 1 320 of the 1 582 flagged) | 2 012 (989 lost, 33 %) | 2 587 (414 lost, 14 %; reps 2-3: 500 / 506) |

The sibling inflates every cycle on the core, the softirq handlers' included, so the softirq share rises by the same ×1.5 as
the handler's CPU per request; the capacity-loss reading needs the handler's own cycles to be intact (I_g ≈ 1), which is
exactly what distinguishes a neighbor that takes the CPU's time (C2) from one that slows it (C1′). The gate's threshold is the scorer's own I_g flag (1 + i_far = 1.15, one-sided; step 11b replaced the step 11 "within 0.15 of 1", which gave the same counts on every arm since no row-9 window had a smoothed I_g under 0.85). It is applied to the
smoothed corrected I_g, not to the per-window flag, because the per-window corrected I_g on one CPU is noisy: under the
unsmoothed flag the saturating C2 loses 33 % of its row-9 windows and the milder 14-17 %, while C1′ still keeps 9 %; smoothed, 0.4 % / 0 % / 0 %.

**Milder C2 signature** (`signatures.csv` +100 rows, `analysis/figs_i12_sig.py`; rep 1 vs the two twins' mean; reps 2-3 within 2 %)

| signal | C2 milder | twins | ratio / value |
|---|---|---|---|
| CPU per request ms | 0.0314 | 0.0232 | ×1.35 |
| I_g corrected (raw) | 0.976 (1.362) | 0.976 (1.006) | **×1.00** (raw ×1.35) |
| in-handler p50 / p99 µs | 4.9 / 29.7 | 4.9 / 9.7 | ×1.00 / ×3.05 |
| client p50 / p99 ms | 0.741 / 3.21 | 0.512 / 1.41 | **×1.45 / ×2.27** |
| served req/s | 22 475 | 22 492 | ×1.00 |
| φ / φ_sibling / R_foreign | 0.002 / 0 / 0 ms/s | 0.0005 / 0 / 0 | absolute |
| φ_irq / softirq share | **0.209** / 0.333 | 0.034 / 0.125 | ×2.66 (share) |
| softirq ms/s, while current | 327 / 314 | 147 / 147 | ×2.22 / ×2.14 |
| own LLC occupancy / MBM | 12.4 MB / 0.238 GB/s | 4.0 MB / 0.234 | ×3.1 / ×1.01 (the socket buffers) |
| aggressor (receiver pod) occupancy / MBM | 2.0 MB / 0.012 GB/s | — | absolute |
| clock MHz | 2 400 | 2 400 | ×1.00 |
| CPI, MPKI, DRAM-bound, core-bound | n/a | n/a | arm Bp-sirq carries no counters (sigF would) |

figC's C2 row can now come from the r650: I_g corrected ×1.00 with raw ×1.35, client p50 ×1.45 on an in-handler p50 ×1.00,
softirq ×2.2 with φ ≈ 0 — the interrupt-neighbor signature, distinct from C1′ (I_g corrected ×1.37, φ_sibling 1) and C4′/C7
(I_g ×1.43 / ×1.16 with the cache signals).

**Step 11b: the MBM sub-rule and the amount restored on the r650 rows** (`rule_version` `s11b-final`, 2 697 rows). The step 11 replay had carried the scorer's `sub` and `amount` in its own CSV but not into `verdicts_by_second.csv`'s notes, and the scorer emitted the amount on row 9 only. Now every row-3 verdict with an aggressor RMID carries the sub-rule and every row 1 / 3 / 9 an amount: rows 1 and 9 from D at k(1 − φ − φ_irq), row 3 from D′ = I_g·D(I_g·ρ) (§5.0's amount check). Rows 2 / 6 / 7 carry none (123 rows: C7's 63 + 8 + 3, HC reserve 3's 49).

| scenario | sub-rule (step 4) | amount, median | measured p50 − twin |
|---|---|---|---|
| C4′ cache neighbor | **`cap` on 301 of 301** (step 4: 301) | D′ − D **51.1 ms** (11c: 39.9 on smoothed inputs) | **11.5 ms** (median of the seconds; 11b quoted the first second, 8.2) — the 3.5× overshoot is the K5 dictionary's knee, step 11c |
| C7 ×4 bandwidth neighbor | **`bw` on 301 of 301** (step 4: 301) | D′ − D 43.6 ms on the 227 row-3 seconds (11c: 16.6) | **3.7 ms** (median; 11b quoted the first second, 10.0) — same cause, step 11c |
| C1′ SMT sibling | none (no aggressor RMID: the sibling is a bare process) | D′ − D 0.002 ms | in-handler +0.002-0.003 ms |
| HC reserve 1 | none | D(ρ_eff) − D **0.0082 ms** | 0.0781 − 0.0696 = **0.0085 ms** |
| HC reserve 3 | none | 0.000 ms on the 252 row-1 seconds | +0.001 ms |
| C2 saturating / milder | none | 0.000 ms (flat D) | 24 s / 0.23 ms on the client, §10 |

**Step 11c: why §5's D′ check passes and the s11b amount did not** (`rule_version` `s11c-final`; per-window table
`results/figs/src/s11c_windows_c4_c7.csv`, 6 001 rows: ρ, I_g, ρ·I_g, D(ρ), D′ per window, the 5 s-smoothed ρ_s, I_g_s and D′_s, twin and measured p50)

**The formula is the same in both**: §5 (`analysis/i6_sig.py`) computes `Dp_ms = Ig * dd.D(rho * Ig)` and the s11b scorer
`Ig * dget("p50_ms", rho * Ig)`, both D′ = I_g·D(I_g·ρ) = I_g·F̄ + h(I_g·ρ)·I_g·K. Three things differed, two of them now removed:

| | §5 (`i6_sig`) | s11b scorer | s11c scorer |
|---|---|---|---|
| dictionary | Phase E service dictionary `dict-syn-memwalk30-{24,128}.json`: D = F̄ + h(ρ)K, **cbar 4.244 / 4.240 from the configured cpu_ms**, bins = the sweep's steps at their offered rate | K5 single-endpoint dictionary `dict-syn-memwalk{24,128}-ethr.json`: pooled p50 per **per-window ρ** bin, **cbar 5.000 / 5.470 measured** | same as s11b |
| ρ at λ 437 | 0.463 | 0.546 / 0.597 (same λ; the axes differ by cbar) | same |
| evaluation | once, at the arm's λ and I_g (CPU per request over the arm) | per window, with the window's ρ (IQR 0.49-0.60) and I_g (IQR 1.31-1.57), then the median of D′ − D over the seconds | per window on the **5 s-smoothed ρ_s and I_g_s** (ρ_s·I_g_s IQR 0.771-0.810), as E and the row-9 gate |
| D(ρ) | 4.20 / 5.96 ms | 4.15 / 6.00 (agree) | same |
| ρ_eff = ρ·I_g | 0.673 / 0.556 | 0.814 / 0.697 (window medians) | 0.789 / 0.704 |
| D(ρ_eff) | **11.85 / 8.86 ms** | **30 / 14.5 ms** at the same λ-equivalent (633 / s for C4′) | same dictionary |
| D′ | 17.20 / 10.63 | 53.6 / 18.2 (medians; IQR 15.9-64.5 / 6.9-52) | 43.5 / 20.6 |
| D′ − D (the amount) | **12.6 / 3.8 ms** | **50 / 38 ms** | **39.9 / 16.6 ms** (IQR 28-54 / 10.7-22) |
| measured p50 − twin | 11.6 / 3.4 (pooled p50 15.75 − 4.14, 9.34 − 5.90) | the 11b table quoted the first second's values (8.2 / 10.0); the medians are **11.5 / 3.7 ms** | |
| D′ / p50 | **1.09 / 1.14** | 3.4 / 2.0 | 2.8 / 2.2 |

- **Aggregation** (removed in 11c): D is convex at the knee, so the median of the per-window D′ with noisy ρ and I_g sits
  above D′ at the median inputs: 50 vs 39 ms (C4′), 38 vs 15 (C7). The scorer now evaluates the amount on the 5 s-smoothed
  ρ_s and I_g_s (rows 1 / 9 too: D(ρ_s/(1 − φ − φ_irq)) − D(ρ_s)); at the arm level the same numbers come out (39.1 / 10.9).
- **The dictionary** (the remaining 3-4×, not removed): at the same λ-equivalent the K5 dictionary reads D 30 ms where the
  service dictionary reads 11.9 (C4′), 14.5 against 8.4 (C7). The K5 fit bins the sweep's windows by their own ρ = λ_w·c̄/k,
  and a window inside an overloaded step with fewer arrivals than the step's average lands in a lower bin carrying the
  step's queue: the knee is smeared toward lower ρ and steepened (memwalk24: 7.2 → 18.0 → 43.7 ms over bins 0.704, 0.763,
  0.818; the service dictionary's step bins put 11.5 → 41.4 ms at ρ 0.59 → 0.63 on its axis, i.e. λ 557 → 594 / s, where the K5
  axis already reads 37 ms at λ 557). §5's check passes on the step-binned dictionary (12.6 vs 11.5 measured, 3.8 vs 3.7) and
  fails on the per-window-binned one (39.9, 16.6); the formula is not at fault, the dictionary's knee is.
- **Error bar at ρ_eff on each dictionary** (the amount at ρ_eff ± half the IQR of the smoothed ρ_s·I_g_s, on that dictionary's axis):

| dictionary | ρ_eff | local slope dD/dρ | amount and its bar | measured |
|---|---|---|---|---|
| K5 memwalk24 (scorer), C4′ | 0.789 ± 0.020 | **460 ms per unit ρ** | 39.3 ms **[26.2, 52.4]** | 11.5 |
| service memwalk30-24 (§5), C4′ | 0.670 ± 0.017 | 132 | 12.6 ms [10.6, 21.7] | 11.5 |
| K5 memwalk128 (scorer), C7 ×4 | 0.704 ± 0.015 | **322** | 14.7 ms [9.0, 20.4] | 3.7 |
| service memwalk30-128 (§5), C7 ×4 | 0.546 ± 0.012 | 43 | 3.8 ms [3.1, 4.3] | 3.7 |

So on the scorer's dictionaries the amount carries a ±13 ms (C4′) / ±6 ms (C7) bar from the input spread alone, and a
3-4× bias from the knee's position; on §5's dictionaries ±5 / ±0.6 ms and no bias. **What the scorer needs** is the service
dictionary's binning (by the sweep step's offered rate, with its overload guard) on the K5 dictionary's format and E_thr;
that is a re-fit of `k5score.py --fit`, not done in this step (the user's call: it changes D(ρ) for every verdict, not only the
amount). Until then the §5 check stands beside the amount in the C4′ and C7 rows' `notes`, and the amount on a per-window
dictionary is an upper bound at the knee. The HC reserve 1 amount (0.0082 vs 0.0085 ms measured) and C1′'s (0.002 vs
0.003) are off the knee and unaffected.

**Not done in this step.** The unique-id recorder change (Lat from the socket receive timestamp, 8 buckets per octave below
32 µs, its own-cost arm, the re-sweep and the re-run of the milder C2 and C1′ arms) waits for the go; nothing of it is written
or deployed. No queue ran on the cluster in step 11, so no node changed and no restore was needed; the one change outside the
repo is the scorer on node-0 (`~/k5/k5score.py`, `/local/results/figs-work/k5score.py`, the gate; `.pre-s11` beside each).
Files: `results/figs/src/final_verdicts_by_second.csv` (2 697 rows), `g_final_verdicts_by_second.csv` (925),
`i12_signatures.csv` (100), `dict-syn-memwalk24-ethr.json` fitted on node-0 for C4′ (`/local/results/figs-work/s8/`).
