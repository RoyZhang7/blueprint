# Phase G results — the three-signal scorer

> **Vocabulary (Addendum E, Nov 12).** The diagnoses are named by mechanism in every file from here on: **1 run-queue
> contention** (was "extrinsic"; trigger φ, amount E = (Lat − D) ÷ Lat, target the holder cgroup), **2 own overload** (was
> "intrinsic overload"), **3 service-time inflation** (was "shared resource"), **4 lock wait** (was "runtime / lock wait"),
> **5 GC**, **6 nothing to explain**, **7 no verdict**. The cell slow × ext and the truth classes ext / slow-int / fast keep their
> names: where a second is called "ext" or "extrinsic" below, that is the cell's axis (the twin's share ≥ 0.5), not a diagnosis.
> Numbers are unchanged.

Spec: `PHASEG-HANDOFF.md`. Everything from PHASED §6, PHASEE §0, PHASEF §0 holds (D[ρ; mix] = F̄ + h(ρ)·K, F̄ the
mixture median, ρ = λc̄ ÷ (f/f_nom) ÷ k, one FIFO line, 5 s smoothing, gate = experimental apparatus, vocabulary
aggressor / c̄ / c̄² / service dictionary). Status: **§1 (G1) done, analysis only, no cluster time; stopped before §2.**
Every run analysed here is a clnode258 run of Phases C–F (f_nom 3100 MHz, `GORDION_FREQ_NORM=0`, `GORDION_LAT=unstamped`).
Code: `analysis/g1_inflation.py` (U, ρ, I per window; tables; Fig. G1-a/b), `analysis/g1_p90.py` (Fig. G1-c).
Outputs: `results/g1/` (`g1_inflation.csv`, `g1_stressed.csv`, `g1_collapse.csv`, `g1_thresholds.csv`,
`g1_p90_collapse.csv`, three figures) and `<run>/g1/g1_windows.csv` per run (new directory; no CSV overwritten).

## 1. G1 — I = U ÷ ρ from the existing recorder streams

### 1.1 What the `cpu` field is (U found)

**Finding.** `cpu` is the process's user + system CPU time consumed **since the previous flush**, in **nanoseconds**,
per interval (not cumulative), for the whole process (all threads: handlers, transport goroutines, runtime, the recorder's
own flusher). It is `getrusage(RUSAGE_SELF)`:

- Go (synthetic and HotelReservation): `runtime/plugins/gordion/cpu_linux.go:9-15` —
  `processCPUNs()` = `(Utime.Sec+Stime.Sec)*1e9 + (Utime.Usec+Stime.Usec)*1e3` of `syscall.Getrusage(RUSAGE_SELF)`;
  `runtime/plugins/gordion/recorder.go:754-760` in `flush()`: `"cpu": cpu - r.lastCPU` then `r.lastCPU = cpu`
  (the first window of a run carries no `cpu`: `lastCPU == 0`). The HotelReservation copy
  (`hotelReservation/gordion/cpu_linux.go`, `recorder.go:24`) is identical.
- C++ (SocialNetwork): `socialNetwork/src/gordion/Gordion.h:142-147` `cpuNs()` (same rusage sum) and `:324-328`
  `"cpu": cpu - _lastCPU + _idleCPU` (idle heartbeat windows accumulate their CPU into the next emitted window).

**The interval the delta covers.** The Go recorder writes the *nominal* window into `"w"` (`recorder.go:744-745`,
`r.windowNs`) and stamps `"t"` with `time.Now()` taken right after the previous flush (`recorder.go:587, 604`), so the
delta covers `t[i+1] − t[i]`, not `w`: measured Δt is 100 ms median, 81–127 ms p1–p99 on the synthetic sweep and up to
500 ms p99 on the recommendation collapse steps (the flusher goroutine is scheduled late). The C++ recorder writes the
measured flush-to-flush length into `"w"` (`Gordion.h:313-316`, image g2+). Idle windows (no arrival, no completion)
are emitted once per second only (`recorder.go:731-734`) and the loader drops them; the cpu delta of the first record
after such a gap spans the gap. Hence, per window:

```
U   = cpu ÷ Δt ÷ |cpuset|            Δt = t[i+1] − t[i] for the Go recorder (clipped to [0.5 w, 3 w], else w), Δt = w for the C++ one
ρ   = Σₑ aₑ cₑ ÷ Δt ÷ |cpuset|       the same Δt (arrivals are counted over the same interval)
I   = U ÷ ρ = cpu ÷ Σₑ aₑ cₑ          Δt cancels: I is measured CPU per unit of offered work, independent of the window length
```

U and ρ are smoothed with Algorithm 1's causal Gaussian (5 s) and I is their ratio (`I_s` in the CSVs; the raw per-window
`I` is kept beside it).

**Cross-check.** No run of Phases C–F captured the pod's `cpu.stat` or `/proc/<pid>/stat`, so the counter cannot be
checked against the cgroup from recorded data; §2.2 adds `usage_usec` to the record for that. What can be checked from
the data: the 1-core post-storage process never exceeds 1.0 core (max 0.998 cores busy over 4 603 windows), the 4-core
synthetic service peaks at 3.9 cores under the cap-3 gate, and the 2-core geo / recommendation processes at 1.5; and
Phase A already found process CPU within 5 % of Σ arrivals × floor on the synthetic service. The unit (ns, per interval)
is therefore settled from the code; the cgroup identity of the counter is not, and `cpu.stat` is the §2 fix.

**One thing the handoff's formula does not say.** `cpu` is the *process*, not the request work: it contains a per-second
base (Go runtime spinning Ms, netpoller, GC, the Thrift server's idle threads, the recorder) that does not scale with
λ. That term decides everything in 1.2–1.3 below.

### 1.2 I on the unstressed sweeps and twins (claim C1)

Costs used (ρ's cₑ): synthetic = the configured `cpu_ms` (independent of any measurement); reservation
`costs-reservation-D5-avg.json` and post-storage `costs-post-storage-k1b-avg.json` = NNLS "average" fits over the two
sweeps of that service (utilisation band 0.03/0.3–0.9); frontend `costs-frontend-D-load.json` = single-endpoint load
runs; geo / recommendation `costs-*-F-op.json` = process CPU per request at the step before the knee of the same sweep.
So on reservation and post-storage I averages 1 over the fitting band by construction, on geo / recommendation it is 1
at one step by construction; the *shape* of I across steps is not by construction anywhere, and on the synthetic
service nothing is.

Per step (5 s smoothed I; the full service × step table is `results/g1/g1_inflation.csv`, Fig. G1-a
`results/g1/figG1-I-vs-rho-twins.png`):

| service | run | k | lowest step I [p25, p75] | I at the 2nd step | I at the top served step (ρ) | per-step IQR of I (median) | base b (cores) | slope m | I_marginal median [min, max] |
|---|---|---|---|---|---|---|---|---|---|
| synthetic | uniform sweep | 4 | 1.23 [1.21, 1.26] | 1.14 | 0.93 (0.97) | 0.014 | 0.21 | 0.92 | 0.91 [0.67, 0.97] |
| synthetic | short sweep | 4 | 1.23 [1.22, 1.24] | 1.12 | 0.96 (0.90) | 0.006 | 0.38 | 0.88 | 0.89 [0.81, 0.92] |
| synthetic | long sweep | 4 | 1.09 [1.08, 1.09] | 1.04 | 1.00 (0.88) | 0.007 | 0.09 | 0.98 | 0.98 [0.97, 0.99] |
| synthetic | mid7 sweep | 4 | 1.11 [1.10, 1.11] | 1.05 | 0.99 (0.91) | 0.005 | 0.15 | 0.96 | 0.96 [0.93, 0.97] |
| reservation | default sweep | 4 | 2.91 [2.81, 2.97] | 1.94 | 0.91 (0.26) | 0.056 | 0.23 | 0.70 | 0.71 [0.39, 0.78] |
| reservation | heavy sweep | 4 | 3.09 [2.96, 3.14] | 2.06 | 0.92 (0.25) | 0.062 | 0.20 | 0.73 | 0.73 [0.64, 0.81] |
| frontend | default sweep | 4 | 3.49 [3.36, 3.67] | 2.14 | 0.79 (0.36) | 0.043 | 0.35 | 0.56 | 0.56 [0.44, 0.60] |
| frontend | heavy sweep | 4 | 3.52 [3.44, 3.58] | 2.01 | 1.07 (0.27) | 0.093 | 0.27 | 0.70 | 0.71 [0.59, 0.82] |
| geo | sweep to 9000 | 2 | 4.08 [3.98, 4.18] | 3.14 | 0.90 (0.29) | 0.047 | 0.21 | 0.59 | 0.60 [−0.17, 0.72] |
| geo | sweep to 16000 | 2 | 3.51 [3.39, 3.58] | 1.99 | 0.88 (0.32) | 0.053 | 0.28 | 0.45 | 0.45 [0.41, 0.47] |
| recommendation | sweep | 2 | 3.19 [3.05, 3.39] | 2.35 | 1.00 (0.56) | 0.023 | 0.29 | 0.73 | 0.75 [0.55, 0.80] |
| post-storage | compose-heavy k1c | 1 | 1.97 [1.93, 2.00] | 1.66 | 1.10 (0.71) | 0.050 | 0.12 | 0.96 | 0.96 [0.87, 1.02] |

`b` and `m` are the least-squares line *cores busy = b + m·(ρ k)* through the served steps above the lowest; I_marginal =
(cores busy − b) ÷ (ρ k) per step. Twin ramps (ρ bins, `g1_inflation.csv` kind = ramp): synthetic B 1.067 → 1.032 over
ρ 0.3–0.7 (IQR ≤ 0.004 per bin), mid7 1.064 → 1.017 over 0.3–0.8, reservation 1.137 [1.111, 1.157] at ρ 0.15, frontend
1.088 → 1.063 over 0.1–0.3, post-storage 1.386 → 1.161 over ρ 0.3–0.7.

**Reading.** (a) Within a step I is tight: the per-step IQR is 0.005–0.014 on the synthetic service and 0.02–0.09 on the
real ones. (b) Across steps it is not flat: I falls monotonically with load on every service, from 1.09–1.23 (synthetic),
1.97 (post-storage) and 2.9–4.1 (the HR Go services) at the lowest step to 0.9–1.0 at the top served step. The lowest
step is above 1 as expected, but so are the next three to six steps, and the top steps are below 1. (c) The whole shape
is one term: a per-second base CPU `b` of 0.09–0.38 cores per process that does not scale with λ. With it subtracted the
remaining CPU is linear in the offered work (I_marginal flat within ±0.05 on nine of twelve sweeps; the outliers are the
geo sweep's 9000 step, where the fit line runs through zero, and the reservation default sweep's 1200 step). The fitted
`b` is 0.2–0.35 cores on every Go service (runtime background: spinning Ms, netpoll, GC, the transport goroutines'
idle work) and 0.09–0.21 on the synthetic one. (d) I_marginal is not 1 on the HR services (0.45–0.75) because the "average"
and "op" cost files already include the base: they are the cost at one load. The synthetic service's configured costs
are per-request work, and there I_marginal is 0.89–0.98 (the spin loop's CPU is a little under its wall time).

**Claim C1 as written — "I ≈ 1 within the per-step IQR, lowest step excluded" — does not hold** on any service with the
average costs: the step-to-step drift (0.1 on the synthetic, 2–3 on the HR services) is 10–50× the per-step IQR. It
holds only after the base term is separated: I_marginal = (U − b/k) ÷ ρ_marginal with marginal cₑ (the NNLS-with-intercept
fit that `r3_costs.py --variant intercept` already produces, e.g. `costs-reservation-nnls.json`) is flat within
±0.05 across steps on 9 of 12 sweeps. Decision for the user (§1 stop point): define I against marginal cₑ with the
base subtracted, or keep the average definition and accept per-step thresholds that follow the 1/λ curve.

### 1.3 I under one aggressor (first sign of cache pressure?)

Stressed run against its twin, per step / ρ bin, served steps only (`results/g1/g1_stressed.csv`, Fig. G1-b
`results/g1/figG1-I-stressed.png`); ≈ / ≫ against the service's provisional band of 1.6:

| service | stressed run | steps / bins | Δ I = stressed − twin: median [min, max] | ≈ / between / ≫ | largest rise |
|---|---|---|---|---|---|
| synthetic | uniform sweep + 1 aggressor | 7 | −0.012 [−0.039, +0.000] | 2 / 4 / 1 | +0.000 at 100 req/s |
| synthetic | ramp B + 1 aggressor | 4 | −0.012 [−0.020, −0.007] | 1 / 3 / 0 | −0.007 at ρ 0.5–0.6 |
| synthetic | ramp mid7 + 1 aggressor | 4 | −0.009 [−0.013, −0.002] | 1 / 3 / 0 | −0.002 at ρ 0.5–0.6 |
| reservation | sweep + aggressors (search/geo/rate pods) | 13 | −0.108 [−0.228, −0.055] | 1 / 10 / 2 | −0.055 at 5760 req/s |
| reservation | ramp + aggressors | 1 | −0.073 | 0 / 1 / 0 | −0.073 at ρ 0.1–0.2 |
| frontend | sweep + aggressors | 13 | **+0.099 [+0.042, +0.183]** | 1 / 7 / 5 | **+0.183 at 1000 req/s** |
| frontend | ramp + aggressors | 2 | +0.092 [+0.075, +0.110] | 0 / 2 / 0 | +0.110 at ρ 0.1–0.2 |
| geo | sweep to 9000 + 1 aggressor | 10 | −0.130 [−0.438, −0.070] | 0 / 6 / 4 | −0.070 at 9000 req/s |
| geo | sweep to 16000 + 1 aggressor | 5 | −0.111 [−0.504, −0.100] | 0 / 3 / 2 | −0.100 at 7000 req/s |
| recommendation | sweep + 1 aggressor | 8 | −0.131 [−0.553, −0.089] | 0 / 0 / 8 | −0.089 at 10000 req/s |
| recommendation | sweep + 2 aggressors | 7 | −0.293 [−1.462, −0.136] | 0 / 0 / 7 | −0.136 at 10000 req/s |
| post-storage | sweep + 1 aggressor | 10 | −0.218 [−0.772, −0.061] | 0 / 2 / 8 | −0.061 at 2000 req/s |
| post-storage | rev ramp + 1 aggressor | 4 | −0.173 [−0.322, −0.108] | 0 / 1 / 3 | −0.108 at ρ 0.6–0.7 |

**Reading.** On the synthetic service I stays within 0.04 of the twin at every step (a time-slicer, as expected). On every
real service the aggressor **lowers** I, most at the lowest steps (recommendation −1.46 at 1000 req/s with two aggressors,
post-storage −0.77 at 200 req/s) and least at the top (−0.06 to −0.14): the aggressor squeezes the base term `b` (the
runtime's background CPU yields its CFS share to the neighbour), not the per-request work. That is the same base-term
problem as 1.2 seen from the other side, and another reason to define I on the marginal cost. The one rise is the HR
**frontend** with the three colocated service pods as aggressors: +0.16 to +0.18 at 1000–2600 req/s, 3–4× its band —
reported here as the first sign of cache pressure, per the handoff, and not interpreted further (its aggressors are not
stress-ng; they are the HR search / geo / rate services, which also carry the frontend's own downstream traffic).

### 1.4 The collapse windows (geo to 16 000, recommendation past the knee)

Steps with served/target < 0.95, I and cores busy of the process (k = 2: one executing slot under the gate plus the
transport core), share of windows that would fit row 2 as "I within the band and cores busy ≥ 0.9 × slots"
(`results/g1/g1_collapse.csv`):

| service | run | step (req/s) | served/target | n | I median | cores busy (of k) | I ≈ 1 | slot busy | row 2 (both) |
|---|---|---|---|---|---|---|---|---|---|
| geo | sweep to 16000 | 6–10 (12000–16000) | 0.22–0.59 | 1399 | 0.81–0.83 | 0.11–0.33 of 2 | 2–8 % | 0 % | **0 %** |
| geo | sweep to 16000 + 1 aggressor | 5–10 (11000–16000) | 0.19–0.77 | 1710 | 0.70–0.79 | 0.12–0.38 of 2 | 0–7 % | 0 % | **0 %** |
| recommendation | sweep | 8 (16000) | 0.63 | 375 | 1.10 | 0.98 of 2 | 4 % | 51 % | **3 %** |
| recommendation | sweep + 1 aggressor | 8 (16000) | 0.32 | 224 | 1.08 | 0.19 of 2 | 13 % | 13 % | 0 % |
| recommendation | sweep + 2 aggressors | 7 (14000) | 0.55 | 306 | 1.02 | 0.49 of 2 | 16 % | 26 % | 1 % |
| recommendation | sweep + 2 aggressors | 8 (16000) | 0.21 | 206 | 1.05 | 0.27 of 2 | 17 % | 0 % | 0 % |

**They do not fit row 2.** In the collapse the process is *idle*: geo burns 0.1–0.4 of its two cores and recommendation
0.2–1.0 while the generator is refused 40–80 % of its target. The executing slot is not busy; the requests that reach the
tap are served at or below their usual CPU (I 0.7–0.8 on geo, 1.0–1.1 on recommendation). The collapse is in front of
the tap (accept queue / transport core saturated by connection churn and retries), which PHASEG §0 puts out of scope and
which the process CPU counter cannot see. What would separate "slot saturated" from "transport saturated" is the probe's
R_self per thread (handler threads vs the netpoller/transport threads) plus the per-cgroup `cpu.stat`; from today's data
the row-2 share is 0–3 %, and the honest label for these windows is "served ≪ target with the slot idle" — a seventh
pattern, not row 2. (Caveat: cores busy includes the transport core, so "slot busy ≥ 0.9" is a necessary, not a
sufficient, test for a saturated slot; the share could only go down with a cleaner slot measure.)

### 1.5 p90 collapse check (Fig. G1-c)

`results/g1/figG1-p90-collapse.png`, F3 layout, pooled p50 above and pooled p90 below, the four synthetic sweeps (uniform
K 5.1 ms, short 1.2, long 6.2, mid7 4.2), W = p above the mix's own lowest-step p, served steps only
(`results/g1/g1_p90_collapse.csv`):

| ρ | W50/K uniform · short · long · mid7 | cross-mix ratio range, p50 | W90/K uniform · short · long · mid7 | cross-mix ratio range, p90 |
|---|---|---|---|---|
| 0.45 | 0.37 · 0.24 · 0.49 · 0.45 | [0.63, 1.32] | 0.75 · 0.48 · 0.83 · 0.93 | [0.64, 1.24] |
| 0.50 | 0.56 · 0.31 · 0.61 · 0.58 | [0.55, 1.09] | 1.07 · 0.65 · 1.08 · 1.24 | [0.61, 1.15] |
| 0.55 | 0.89 · 0.59 · 0.93 · 0.83 | [0.66, 1.04] | 1.70 · 1.69 · 1.90 · 1.95 | [1.00, 1.15] |
| 0.60 | 1.58 · 0.86 · 1.27 · 1.40 | [0.55, 0.89] | 3.16 · 2.73 · 2.84 · 3.64 | [0.86, 1.15] |
| 0.65 | 2.27 · 2.78 · 2.18 · 2.89 | [0.96, 1.27] | 4.61 · 7.27 · 6.18 · 8.15 | [1.34, 1.77] |
| 0.70 | 5.74 · 4.75 · 3.85 · 4.37 | [0.67, 0.83] | 7.30 · 11.92 · 9.22 · 12.67 | [1.26, 1.74] |

**Verdict: W90 ÷ K collapses across the mixes at least as well as W50 ÷ K below the knee** (cross-mix ratio within
[0.6, 1.25] at ρ 0.45–0.60 for p90 against [0.55, 1.32] for p50; at 0.55–0.60 the p90 spread is 15 % where the p50
spread is 35–45 %). Above ρ 0.65 both diverge, p90 more (the uniform mix's p90 saturates first: its W90/K is 0.6–0.75×
the others'). The short mix sits lowest in both statistics. W90 is 1.7–2.5× W50 at the same ρ, i.e. the same h(ρ) shape
scaled; a p90 dictionary D90 = F̄90 + h90(ρ)·K is a candidate on the collapse criterion. Not decided here: which floor
statistic (per-mix p90 floor, here 3.7–17.8 ms, 2–6× the p50 floor) and whether K is still the right scale for the p90
(the ratio W90/W50 is not constant across mixes: 2.0 uniform, 2.0–2.9 short, 1.7–2.2 long, 2.1–2.6 mid7 at ρ 0.5–0.6).

### 1.6 Provisional thresholds (until G2)

From the unstressed runs, served steps above the lowest: ≈ = within the per-step IQR of I (median over steps), ≫ = beyond
3× it (`results/g1/g1_thresholds.csv`). Q = the Go recorder's existing `sched` field (runtime wait, `/sched/latencies`,
p50 / p90 of the window's goroutine wake-ups, ms), carried as the provisional Q baseline; the C++ service has none.

| service | served steps above the lowest | I median over steps | per-step IQR of I | ≈ band around the step's own I | ≫ beyond | Q50 / Q90 per window (ms) |
|---|---|---|---|---|---|---|
| synthetic | 33 | 1.037 | 0.008 | ± 0.008 | > 3 × 0.008 = 0.024 | 0.000 / 0.010 |
| reservation | 25 | 1.044 | 0.061 | ± 0.061 | > 0.184 | 0.000 / 0.007 |
| frontend | 22 | 1.045 | 0.047 | ± 0.047 | > 0.140 | 0.000 / 0.029 |
| geo | 14 | 1.247 | 0.051 | ± 0.051 | > 0.154 | 0.000 / 0.078 |
| recommendation | 7 | 1.200 | 0.023 | ± 0.023 | > 0.069 | 0.000 / 0.131 |
| post-storage | 13 | 1.262 | 0.036 | ± 0.036 | > 0.109 | — |

These bands are *around the step's own unstressed I*, not around 1: with the average costs the centre moves from 3 to
0.9 across a sweep (1.2), so a band around 1 would call every low-load window "I ≫ 1" on every real service. With the
marginal definition (base subtracted) the centre is flat and the same IQRs apply around it. E (= Lat − D) thresholds
are not derived here: the twins' E is already in the Phase F `b4g/` files and its per-step IQR is the floor-noise
statistic of Phase F (`service_dict.json` `noise`); R and G have no data before the probe.

### Deliverables of §1 and what is left to the user

Delivered: the U finding (1.1), the I tables (1.2, 1.3; CSVs), Fig. G1-a (I vs ρ, twins), Fig. G1-b (stressed vs twin),
Fig. G1-c (p90 collapse), the collapse-window table (1.4) and the threshold table (1.6). Open for decision before §2:
(i) I on marginal cost with the base term subtracted (recommended; the intercept fit exists) or on the average cost with
load-dependent bands; (ii) whether row 2 keeps "slot busy ≈ 1" as the discriminator given that today's collapse windows
have an idle slot (1.4) — the probe's R_self per thread is what would measure the slot; (iii) whether the p90 dictionary
is pursued (1.5 says the collapse criterion is met).

### 1.7 Revised Nov 10 (Addendum A.1 and A.4)

**A.1 — I in the marginal form.** U₀ = the intercept of the cost regression (`r3_costs.py --variant intercept`, the
per-service D₀, new files `costs-<svc>-G-nnls.json`; the synthetic one from the uniform + mid7 sweeps with the
configured costs as the start) and ρ from the same regression's marginal cₑ, so that D₀ and cₑ come from one fit (using
the average cₑ with the intercept would count the base twice; the dictionary's ρ is untouched). I_marg = (U − U₀) ÷ ρ,
new columns `U0`, `I_marg`, `I_marg_s` in every `g1/g1_windows.csv`; bands recomputed (`g1_thresholds.csv`); Fig. G1-a′
`results/g1/figG1-Imarg-vs-rho-twins.png`.

| service | U₀ (cores) | marginal cₑ vs average | I_marg median over served steps | per-step IQR | steps within ±0.05 of 1 | twin ramps (above the lowest step) | verdict on the twins |
|---|---|---|---|---|---|---|---|
| synthetic | 0.18 of 4 | configured cₑ kept | 0.952 | 0.008 | 20 of 33 | ramp B 0.952–0.958, ramp mid7 0.950–0.958 (ρ 0.3–0.8) | **within ±0.05** |
| reservation | 0.18 of 4 | 0.115 / 0.159 vs 0.155 / 0.208 ms | 1.016 | 0.078 | 14 of 25 | 1.091 [1.057, 1.119] at ρ 0.11 | outside (+0.09) |
| frontend | 0.27 of 4 | 0.22 / 0.22 / 0.37 / 0.05 vs 0.31 / 0.30 / 0.49 / 0.10 ms | 0.996 | 0.067 | 7 of 22 | 1.16 at ρ 0.13, 1.50 at ρ 0.07 | outside (+0.16 to +0.50) |
| geo | 0.18 / 0.15 of 2 | 0.043 / 0.038 vs 0.071 / 0.052 ms | 1.115 | 0.075 | 3 of 14 | no twin ramp; sweeps 0.53–1.81 by step | outside |
| recommendation | 0.24 of 2 | 0.064 vs 0.086 ms | 1.038 | 0.028 | 4 of 7 | no twin ramp; sweep 0.96–1.20 by step | outside |
| post-storage | 0.07 of 1 | 0.249 / 0.559 vs 0.277 / 0.645 ms | 1.066 | 0.030 | 5 of 12 | 1.10–1.15 (ρ 0.3–0.6) | outside (+0.10 to +0.15) |

Reading. The marginal form moves the centre to 1 on every service (medians 0.95–1.12 against 1.04–1.26 before) and on
the synthetic service it meets the criterion: the twin ramps sit at 0.95–0.96 and the sweeps within [0.91, 0.99] from
ρ 0.3 up (the spin loop's CPU is 4–5 % under its wall time; the remaining 0.5 % per-step IQR is noise). Below ρ 0.3 and
at the lowest steps it undershoots (0.64–0.78 at the lowest step, down to 0.14 at 40 req/s) and on the HR services the
lowest step goes negative: **U₀ is not a constant**. The regression's intercept is the base at the high-load end of the
fit; at low load the process burns less than that (fewer netpoll wake-ups, fewer spinning Ms), so subtracting a constant
over-corrects there, and on the real services the twins at ρ 0.1–0.2 land 0.09–0.5 above 1 for the opposite reason
(the base there is above the intercept's extrapolation, Fig. G1-a′ frontend and reservation panels). **Fallback taken
for the real services (reservation, frontend, geo, recommendation, post-storage): the average form with per-step
bands stays the operating definition (§1.6); I_marg is kept as a column and the clean form I_req (per-request thread
CPU ÷ c̄, from the probe) is the one that removes the background by construction. The synthetic service uses I_marg.**
U₀ is elastic under an aggressor (idle-priority GC workers and spinning Ms yield their CFS share), so I_marg is biased
low under an aggressor at low load: with one aggressor I_marg drops by 0.10–0.30 on reservation / geo / post-storage
and by 0.15–0.27 on recommendation at the served steps (`g1_stressed.csv`, `delta_marg`), the synthetic service by
0.01–0.02; the frontend's rise is +0.14 to +0.28 in this form (same sign and steps as in 1.3). The collapse-window
table in the marginal form stays at row-2 share 0–2 % (`g1_collapse.csv`).

Marginal bands (around 1, per-step IQR of I_marg; the synthetic's are the operating ones): synthetic ± 0.008 (≫ beyond
1.023), reservation ± 0.078, frontend ± 0.067, geo ± 0.075, recommendation ± 0.028, post-storage ± 0.030.

**A.4 — CPU limits per pod (from the deployment code, not the cluster, which is gone).**

| pod | manifest `resources.limits.cpu` | what the driver does at deploy | effective in the Phase C–F runs |
|---|---|---|---|
| HR frontend, search, geo, rate, profile, recommendation, user, reservation | 3 (requests 1) in `hotelReservation/kubernetes/*/*-deployment.yaml` | `hr.sh deploy_svc` patches `resources` to `{}` on all eight (`hr.sh:92-93`, "no CFS quota on any service"), SUT, aggressors and the rest alike | **none** |
| HR consul, jaeger, mongodb-*, memcached-* | 3 (requests 1) | not touched by hr.sh (`cmd_place` only moves them) | **3 cores each**, on the DB node, not on the SUT's cores |
| synthetic `bigsvc` | none in the manifest `exp.sh` applies (`scripts/exp.sh:69-111`, no `resources` block) | — | **none** |
| SocialNetwork post-storage and the other SN services | chart default 100 m / 128 Mi | `sn.sh install` and `deploy_svc` strip `resources` (`sn.sh:71, 136`) | **none** |

So CFS bandwidth throttling cannot be the explanation of the geo / recommendation collapse: the SUT pods ran without a
quota. The recorder's 500 ms flushes and the idle process under collapse (1.4) are not throttling; the §2 `cpu.stat`
capture (`nr_throttled` should stay 0) will confirm it on the new node. The motivation cluster's own HR deployment
(`default` namespace on clnode170) is a different configuration (limits 8–24 cores, see §2.1) and is left as it is.

### 1.8 Addendum B (Nov 11): I against the CPU curve, g-collapse, D90 columns, the collapse rows

**B.1 — g(ρ) and I_g.** For each service, from the same profiling sweep that gives h(ρ): u(ρ) = process CPU per
served request per step and per ρ bin (the recorder's `cpu` over the step's arrivals), g(ρ) = u(ρ) ÷ c̄_sweep, stored
in the service dictionary beside h (`dictionary.py`: `steps[s]["u_ms"]`, `["g"]`, `ServiceDict.g`, `g_at()`,
`service_dict.json` keys `g`, `u_sweep_ms`). I_g = U ÷ [λ·c̄(mix)·g(ρ)] = I_avg ÷ g(ρ) per window; new columns `g_rho`,
`I_g`, `I_g_s` in every `g1/g1_windows.csv`; `I_avg` (= `I`) and `I_marg` kept. The geo2 rows use the geo2 sweep's g
and costs, the geo rows the first geo sweep's. Figure G1-a″ `results/g1/figG1-Ig-vs-rho-twins.png`.

g per step (profiling sweeps): synthetic 1.19 (40 req/s) → 1.02 at ρ 0.73 → 0.93 at 0.97; reservation 2.89 → 0.91;
frontend 3.49 → 0.79; geo 4.10 → 0.90; recommendation 3.20 → 1.00 at the step before the knee; post-storage 1.98 → 1.10.

| service | profiling sweep for g | I_g median over served steps (lowest included) | per-step IQR | steps within ±0.05 of 1 | twin ramps (ρ bins) | non-profiling sweeps of the same service (served steps) |
|---|---|---|---|---|---|---|
| synthetic | uniform | 0.983 | 0.018 | 29 of 37 | ramp B 0.98–1.02, ramp mid7 0.96–1.02 | short 0.99–1.08 (0.90 at ρ 0.9), long 0.93–0.99, mid7 0.93–0.99 |
| reservation | default | 0.999 | 0.050 | 21 of 27 | 0.991 [0.967, 1.018] | heavy 0.86–1.05 (1.42 at 400 req/s) |
| frontend | default | 0.996 | 0.048 | 17 of 24 | 0.99–1.06 | heavy 0.85–1.17 (1.3 on its unserved top steps) |
| geo | geo / geo2, each its own | 0.995 | 0.047 | 13 of 16 | — | geo2 served steps 0.88–1.10 |
| recommendation | sweep | 1.006 | 0.023 | 6 of 8 | — | — |
| post-storage | compose-heavy k1c | 1.002 | 0.026 | 13 of 14 | 1.00–1.07 | — |

**I_g ≈ 1 holds**: medians 0.98–1.01 on every service, the twin ramps within [0.96, 1.07], and the lowest step no
longer needs an exclusion (recommendation 1.008, geo2 1.10, reservation default 1.0 by construction; the heavy mixes'
lowest steps 1.25–1.42 are the one place where a different mix at the same ρ has a different per-request base). The
profiling sweep is 1 by construction at its step medians; the information is in the twins, the other mixes and the
stressed runs. Bands (per-step IQR of I_g, around 1): synthetic ± 0.018 (≫ beyond 1.054), reservation ± 0.050
(1.151), frontend ± 0.048 (1.144), geo ± 0.047 (1.141), recommendation ± 0.023 (1.068), post-storage ± 0.026
(1.077). Where I_g leaves the band on an unstressed run it is a different mix at low load (the per-request base is a
function of λ more than of ρ: the short mix at ρ 0.3 runs 2× the uniform's λ and reads 1.08) or a collapse step
(served < 0.95: recommendation 16 000 and geo2 12 000–16 000 read 0.4–0.7, the low tails in the figure; excluded
from the bands as before).

Stressed against twin, g-form (`g1_stressed.csv`, columns `I_g_twin`, `I_g_stressed`, `delta_g`, `verdict_g`; served steps):

| service | stressed run | steps | I_g twin median | I_g stressed median | Δ I_g median [min, max] | ≈ / between / ≫ |
|---|---|---|---|---|---|---|
| synthetic | uniform sweep + 1 aggressor | 7 | 1.002 | 0.992 | −0.009 [−0.035, 0.000] | 5 / 2 / 0 |
| synthetic | ramp B + 1 aggressor | 4 | 0.984 | 0.974 | −0.011 [−0.019, −0.006] | 3 / 1 / 0 |
| synthetic | ramp mid7 + 1 aggressor | 4 | 0.982 | 0.976 | −0.005 [−0.013, +0.002] | 4 / 0 / 0 |
| reservation | sweep + aggressors | 13 | 0.999 | 0.908 | −0.096 [−0.148, −0.053] | 0 / 13 / 0 |
| frontend | sweep + aggressors | 13 | 1.000 | 1.078 | **+0.096 [+0.032, +0.176]** | 3 / 7 / 3 |
| geo | sweep + 1 aggressor | 10 | 1.000 | 0.921 | −0.090 [−0.150, −0.023] | 1 / 8 / 1 |
| geo | geo2 sweep + 1 aggressor | 5 | 0.991 | 0.913 | −0.091 [−0.162, −0.072] | 0 / 4 / 1 |
| recommendation | sweep + 1 aggressor | 8 | 1.006 | 0.894 | −0.104 [−0.193, −0.056] | 0 / 1 / 7 |
| recommendation | sweep + 2 aggressors | 7 | 1.012 | 0.787 | −0.212 [−0.512, −0.129] | 0 / 0 / 7 |
| post-storage | sweep + 1 aggressor | 10 | 1.000 | 0.825 | −0.171 [−0.403, −0.056] | 0 / 2 / 8 |
| post-storage | rev ramp + 1 aggressor | 4 | 1.008 | 0.863 | −0.146 [−0.248, −0.092] | 0 / 0 / 4 |

The synthetic service is unmoved by the aggressor in every form. On the real services the g-form keeps the downward
bias of 1.3 and 1.7: with a CPU aggressor on its cores the process burns 5–20 % less CPU per request than its own
profile at that ρ (the runtime's background work yields its CFS share; on the C++ post-storage the Thrift idle
threads do the same), so **I_g under a time-slicing aggressor reads below 1, never above**: a rise of I_g is a
different signal (the frontend's +0.10 to +0.18 with the three colocated service pods stays the one candidate for
cache pressure). For the verdict rows this means "I ≈ 1" under rows 2 and 4 must tolerate I_g down to ≈ 0.8 on the
Go services when R_foreign is non-zero, and "I ≫ 1" (row 3) stays a rise above the band.

**B.2 — g collapses across mixes (`analysis/g_collapse.py`, `results/g1/g_collapse.csv`, Fig. G-g
`results/g1/figG-g-collapse.png`).** u = CPU per request of the four synthetic sweeps and g = u ÷ c̄ of each mix, served
steps:

| ρ | g uniform (c̄ 4.3 ms) | g short (1.9) | g long (10.3) | g mid7 (7.0) | cross-mix ratio to uniform |
|---|---|---|---|---|---|
| 0.2 | 1.113 | 1.171 | 1.054 | 1.076 | [0.947, 1.052] |
| 0.3 | 1.080 | 1.120 | 1.038 | 1.054 | [0.961, 1.038] |
| 0.4 | 1.062 | 1.101 | 1.036 | 1.046 | [0.976, 1.037] |
| 0.5 | 1.057 | 1.082 | 1.035 | 1.040 | [0.979, 1.024] |
| 0.6 | 1.053 | 1.067 | 1.027 | 1.027 | [0.975, 1.013] |
| 0.7 | 1.034 | 1.055 | 1.014 | 1.020 | [0.981, 1.021] |

**g is mix-independent below the knee within ±5 %** (the ordering is by λ at equal ρ: the short mix, 2.2× the
uniform's arrival rate at the same ρ, carries the most per-request overhead, the long mix the least), the same
tolerance as the h collapse of Phase E. One g(ρ) per service is enough for the verdict's I; g(ρ; mix) is not needed
on this evidence.

**B.3** — the D90 table (A.3 above) now names its decomposition columns and states that the p90 wait guard is off on
every row (14.3 ms floor noise > W90max ÷ 3), which is why D90 never abstains; no further D90 work.

**B.4 — the collapse rows, from the generator's side.** hrgen records per second the sent / done / error counts,
the in-flight count and the end-to-end latency; it does not classify errors (refused / reset / timeout) and does not
record its own CPU, so neither can be reported from the Phase F runs; the per-request timeout is 10 s and the cap
`max-inflight` 20 000 over 4096 connections. What the records show at the collapse steps: in-flight pinned at the
cap (median 13 000–18 600, max 20 000 at every collapse step of geo2 and recommendation), the caller's p50 in
seconds (geo2 sweep 1.1 s at 12 000 req/s, 4.5 s at 13 000; recommendation stress1 5.8 s at 16 000, stress2 7.0 s)
and 1 000–7 900 errors per second with served/target 0.1–0.6: an open-loop generator that has turned closed-loop
at its in-flight cap, every request either answered after seconds or dropped at the 10 s timeout. The pod's own
counters (2.2b) show the process at 0.2–0.4 of its two cores and no throttling. The paper row for these steps reads
**"saturation in front of the tap; no verdict"**; on clnode170 the recommendation explore reproduced it at 12 000
req/s (served 0.35, 732 errors per second, in-flight cap reached).

## 2. G2 — probe, overhead, re-runs (cluster clnode170, Nov 10–11)

### 2.1 Node facts and layout (read before anything was changed; changes logged in stage-3 `RUNBOOK.md` §17.1)

| item | value |
|---|---|
| experiment | `clnode170.clemson.cloudlab.us`, the stage-3 motivation cluster (`RUNBOOK.md` §17): 5× Clemson c6320, 2× Xeon E5-2683 v3 (**Haswell**, 14C/28T per socket, 2.0 GHz base), 251 GB; **not Ice Lake** |
| kernel | `6.12.116-harvest` (Ubuntu 22.04.2), `CONFIG_BPF_SYSCALL=y`, `CONFIG_DEBUG_INFO_BTF=y`, `CONFIG_SCHEDSTATS=y` (`kernel.sched_schedstats=0` at boot), `CONFIG_CFS_BANDWIDTH=y`, `CONFIG_KPROBES=y`, `CONFIG_FTRACE=y`; tracefs readable by root only |
| BTF | `/sys/kernel/btf/vmlinux` present on every node; `linux-headers-6.12.116-harvest` installed |
| cgroup | **v2** (`cgroup2fs` on `/sys/fs/cgroup`), kubelet under `kubepods.slice`, docker 20.10 with the systemd cgroup driver (`docker-<id>.scope`), k8s 1.25.6 |
| eBPF tooling | `bpftrace` not installed, apt candidate **0.14.0** (Ubuntu 22.04; old but supports BTF/CO-RE tracepoints); `libbpf0` 0.5.0 installed, `libbpf-dev` 0.5.0 candidate; no `bpftool`, no `clang`. For the probe the plan is bpftrace 0.14 first (tracepoint `sched:sched_switch`, `sched:sched_wakeup`, cgroup ids via `cgroup` builtin); fallback a libbpf CO-RE C program if 0.14 lacks what §2.2 needs |
| clocks | node-3, node-4: `intel_cpufreq` passive, governor `performance`, `no_turbo=1`, min = max = **2 000 000 kHz** (section 17); node-1, node-2: schedutil. f_nom for every clnode170 run = **2000 MHz** |
| SMT | **on** on every node (56 threads); socket 0 = even ids, sibling of N = N + 28 |
| NMI watchdog | off on node-3, node-4 (section 17 PMU fix); on on node-1, node-2 |
| occupancy at start | node-1/2: the motivation's `aggressor-sn`, `aggressor-ss` and the `default` HotelReservation (limits 8–24 cores); node-3: no pods, load 0.00; node-4: no pods, one leftover `check_idle_cpus` at 100 % on CPU 26; node-3/4 tainted `dedicated=special:NoSchedule` |
| Gordion layout | **service node = node-3, socket 1 (odd ids)**: SUT on CPUs `1,3,5,7` (synthetic, k = 4) or `1,3` (HR recommendation, k = 2), siblings idle; generator on `17,19,21,23,25,27`; tolerations added to both drivers for the taint; recorder files in `/var/tmp/gordion`; HR stack in namespace **`gordion`** (PVCs on `nfs-client`), DBs on node-1; images `big_ctr:k123g3`, `hr-gordion:gordion6` |
| toolchain added | Go 1.25.3 at `/usr/local/go1.25` beside the kit's 1.23.4; protoc 3.12.4 was present; `royno7` added to `docker` |

Nothing in the probe's requirements is missing on this kernel: BTF, both tracepoints, cgroup v2 ids and schedstats are
available. The one open question is the bpftrace version (0.14.0 from apt; `kstack`/`cgroup` builtins and
`tracepoint:sched:*` are in it, per-CPU maps too); if a construct is missing, the fallback is a small libbpf CO-RE
program compiled on node-0 (needs `clang` from apt), not a kprobe on `finish_task_switch`.

### 2.2a Base-term runs (A.5 a) — synthetic service

Runs `results/20261111-004420-g2a-syn-base-w1000` (1000 ms windows, raw histograms off) and
`20261111-004745-g2a-syn-base-w100` (the usual 100 ms, raw on): uniform mix, 100 / 300 / 500 req/s × 45 s on node-3
CPUs 1,3,5,7 (k = 4, gate cap 3, 2.0 GHz), the pod's cgroup `cpu.stat` sampled every second from the host
(`cpustat.tsv`; the pod sits in `kubepods-besteffort.slice`, `cpu.max = max 100000`, i.e. **no quota**). Analysis
`analysis/g2a_base.py` → `<run>/g2a/g2a_steps.csv`.

| run | step | served | offered cores λc̄ | cores busy (RUSAGE) | cores busy (cgroup) | cgroup ÷ process | I (average form) | nr_throttled / nr_periods |
|---|---|---|---|---|---|---|---|---|
| w1000, raw off | 100 req/s | 1.00 | 0.418 | 0.467 | 0.464 | 0.995 | 1.117 | 0 / 0 |
| | 300 | 1.00 | 1.308 | 1.411 | 1.399 | 0.992 | 1.079 | 0 / 0 |
| | 500 | 0.97 | 2.091 | 2.235 | 2.259 | 1.010 | 1.069 | 0 / 0 |
| w100, raw on | 100 | 1.00 | 0.409 | 0.457 | 0.455 | 0.995 | 1.119 | 0 / 0 |
| | 300 | 1.01 | 1.298 | 1.407 | 1.405 | 0.998 | 1.084 | 0 / 0 |
| | 500 | 0.99 | 2.110 | 2.252 | 2.255 | 1.001 | 1.067 | 0 / 0 |

Intercepts (cores busy = U₀ + m · offered cores over the three steps): **w1000/raw off: U₀ = 0.026 cores, m = 1.057**
(cgroup counter: 0.011, 1.072); **w100/raw on: U₀ = 0.029 cores, m = 1.055** (cgroup: 0.025, 1.058).

Findings. (1) **U₀ does not drop when the instrumentation is turned down**: 0.029 → 0.026 cores; the recorder's raw
histograms and 10 Hz flushes cost ≤ 0.003 cores (within the fit noise). The base is not our instrumentation, so
nothing is reported as overhead and nothing changes in the subtraction. (2) **The genuine idle base of the synthetic
process is ≈ 0.03 cores**, not the 0.18 cores of the §1 intercept. The §1 number is the intercept of a straight line
through a concave curve: on clnode258 the per-request CPU kept falling toward the knee (I 1.14 at ρ 0.1 → 1.03 at 0.74
→ 0.93 at 0.97), and a line fitted over ρ 0.1–0.97 lands with a positive intercept and a slope below 1. On this node
over ρ 0.1–0.52 the curve is linear (slope 1.057, I_marg 1.046–1.055 at every step with U₀ = 0.03). The
load-dependence of the per-request CPU, not a constant background, is what made the marginal form fail in 1.7; a
constant U₀ cannot represent it in either direction. (3) The cgroup counter agrees with the process counter within
1 % at every step (the pod is one process), **`nr_throttled` = 0 and `nr_periods` = 0 throughout**: no CFS quota, no
throttling, no stop condition. (4) Haswell at 2.0 GHz runs the configured per-request work at 1.06× its wall time
(Ice Lake: 0.92–0.98×, 1.2).

### A.3 side track — a p90 dictionary (analysis only; `analysis/g_d90.py`, `results/g1/d90_rows.csv`)

D90 = F̄90 + h90(ρ)·K built from the uniform profiling sweep (`d-a2-uniform`): F̄90 = 0.9 quantile of the window mix's
mixture of per-endpoint floor distributions (the Phase F 41-point FLₑ(q), share-weighted), h90(ρ) = (pooled p90 of the
sweep's ρ bin − F̄90 of the bin's mix) ÷ K of the bin's mix, Lat90 = pooled p90 of the second's samples, %ext90 =
(Lat90 − D90) ÷ Lat90 with the 5 s causal smoothing, truth90 = (Lat90 − Lat90 of the twin at the same second since
start) ÷ Lat90, cells and guards as in Phase F at the same quantile. The p50 pipeline through the same code is the
reference (it is a simplified scorer, not `b4_compare.py`: per-second pooling, twin alignment ±1 s, no step rule, so
its numbers are not the §1.3 / Phase F table's).

Dictionary: h over ρ 0.02–0.72 for both quantiles; W50max 24.3 ms with floor noise 6.2 ms (wait guard on), **W90max
32.9 ms with floor noise 14.3 ms (wait guard off: W90max < 3× noise)**; h(0.65) = 2.2 (p50) and 3.9 (p90).

| row | quantile | seconds scored | truth slow / ext | strict | when it speaks | abstained | decomposition: %ext error median · share with abs(error) ≤ 0.15 |
|---|---|---|---|---|---|---|---|
| ramp B + 1 aggressor (main Fig. B row) | p50 | 161 | 91 / 8 | 98.1 | 98.8 | 0.6 % | −0.007, 99 % |
| | **p90** | 161 | 59 / 8 | **92.5** | 92.5 | 0 % | +0.015, 98 % |
| ramp mid7 + 1 aggressor | p50 | 165 | 113 / 50 | 64.2 | 100.0 | 35.8 % | +0.007, 100 % |
| | **p90** | 165 | 69 / 40 | **88.5** | 88.5 | 0 % | +0.026, 82 % |
| ramp B twin (no aggressor) | p50 | 161 | 4 / 0 | 100 | 100 | 0 | −0.009, 100 % |
| | p90 | 161 | 3 / 0 | 100 | 100 | 0 | +0.006, 98 % |

(B.3 note: the p90 wait guard is **off** on every row here, because the p90 floor noise of the uniform sweep, 14.3 ms, exceeds W90max ÷ 3 = 11.0 ms; D90 therefore never abstains, and its "when it speaks" column equals its strict column.) **Verdict: D90 scores.** On the main row it is right on 92.5 % of the seconds (p50: 98.1 %) with the decomposition
within ±0.15 on 98 %; on the mid7 row it is right on 88.5 % strict against the p50 pipeline's 64.2 %, because the p90
wait guard is off (the p90 floor noise is 2.3× the p50's, 14 vs 6 ms, so W90max does not clear 3× noise) and D90
speaks on every second where the p50 dictionary abstains on 36 %; where it speaks the p50 is right 100 % and the p90
88.5 %, and the p90's decomposition error is wider (82 % within ±0.15 against 100 %). No false extrinsic on the twin
at either quantile. Open: the p90 floor noise rule (the guard switches off at exactly the row where the p50 guard
bites), and the h90 shape across mixes above ρ 0.65 (1.5 showed the p90 collapse breaks first there). Figure
`results/g1/figG-D90-ramp_B_plus_1_aggressor.png`: observed p50 / p90, D50 / D90, twin, and the two %ext series
against their truths.

### 2.2b Base-term runs (A.5 a) — HR recommendation, and the collapse step with `cpu.stat`

HR stack in namespace `gordion` (image `hr-gordion:gordion6`), recommendation pinned on node-3 CPUs 1,3 (k = 2, gate
cap 1), `single:recommend` with fixed params, the generator on node-3 CPUs 17–27. Explore first
(`results/hr/20261111-010006-g2a-rec-explore`, 15 s steps 1000 → 12 000 req/s): the caller's knee rule gives
**6000 req/s** on this hardware (caller p50 1.30 ms at 1000, 2.72 at 8000 = 2.1×), served/target 1.00 up to 10 000
and **0.35 at 12 000** (the collapse step, 16 110 errors). Base pair at 0.3 / 0.6 / 0.9 × knee = 1800 / 3600 /
5400 req/s × 45 s (`…-g2a-rec-base-w1000`: 1000 ms windows, raw off; `…-g2a-rec-base-w100`: 100 ms, raw on). No
costs exist for this CPU yet, so the regression is on the arrival rate (slope = marginal CPU per request).

| run | step | served | cores busy (RUSAGE) | cores busy (cgroup) | cgroup ÷ process | nr_throttled / nr_periods |
|---|---|---|---|---|---|---|
| w1000, raw off | 1800 req/s | 1.00 | 0.552 | 0.552 | 0.999 | 0 / 0 |
| | 3600 | 0.99 | 0.923 | 0.919 | 0.996 | 0 / 0 |
| | 5400 | 0.97 | 1.194 | 1.211 | 1.015 | 0 / 0 |
| w100, raw on | 1800 | 1.00 | 0.548 | 0.547 | 0.999 | 0 / 0 |
| | 3600 | 1.00 | 0.911 | 0.910 | 0.999 | 0 / 0 |
| | 5400 | 1.00 | 1.204 | 1.207 | 1.002 | 0 / 0 |
| explore | 1000 … 10 000 | 1.00 | 0.352 → 1.576 | 0.349 → 1.576 | 0.994–1.000 | 0 / 0 |
| explore, collapse | 12 000 | **0.35** | **0.385** | 0.263 | 0.68 | **0 / 0** |

Intercepts: **w1000/raw off U₀ = 0.248 cores, 0.178 ms per request; w100/raw on U₀ = 0.231 cores, 0.182 ms per
request** (cgroup: 0.235 / 0.228); the explore over 1000–10 000 req/s: U₀ 0.371 cores, 0.132 ms per request.

Findings. (1) Same answer as the synthetic service: **U₀ does not drop with the instrumentation off** (0.248 vs
0.231; the difference is inside the step-to-step noise), so the base is not the recorder. (2) The "base" is the
intercept of a concave curve, not an idle term: recommendation's CPU per request falls from 0.35 ms at 1000 req/s to
0.16 ms at 10 000 (the Go runtime amortises netpoll wake-ups and scheduler work over more requests per wake-up), so
the straight-line U₀ grows with the range of the fit (0.24 over 1800–5400, 0.37 over 1000–10 000) while the true
idle CPU of the process is below 0.1 cores (the two-point slope 1000 → 2000 gives 0.11). That is the mechanism behind
1.7: no constant U₀ exists for a Go service, and the per-request form I_req (per-thread CPU ÷ c̄) is the one that
does not depend on it. (3) **The collapse is not CFS throttling**: `cpu.max` is `max`, `nr_periods` and `nr_throttled`
stay 0 through the 12 000 req/s step while the process drops to 0.39 cores and the generator is refused 65 % of its
target — the clnode258 signature of 1.4 reproduced on a pod with no quota at all. A.4 is answered twice, from the
manifests and from the counters; the collapse rows stay as they are (nothing to re-run without a limit, there was none).
(4) The cgroup counter equals the process counter within 1 % at every served step; under collapse it reads lower
(0.26 vs 0.39: the recorder's own cpu field spans a 500 ms late flush) — one more reason to take U from `cpu.stat` in
the probe record (§2.2).

**Step 3 verdict: no throttling anywhere; U₀ is a property of the load curve, not of the instrumentation; continue to the probe.**

### 2.2 The probe (`probe/`, README there)

**What runs where.** bpftrace 0.14.0 (apt) on node-3 as root, driven by `probe/probe.py` through `probe/probe-ctl.sh`,
attached for the duration of a run and filtered to the SUT's CPUs; the pod's cgroup is found from its container id
(`docker-<id>.scope` under `kubepods.slice`). One JSON line per 100 ms keyed by unix ms, merged with the recorder's
windows by `analysis/g2_probe.py` into `<run>/g2/g2_windows.csv` (new columns `U`, `R_foreign_ms`, `R_self_ms`,
`R_other_ms`, `Q_futex_ms`, `Q_idle_ms`, `Q_mutex_ms`, `G_ms`, `sched_p50/p90_ms`, `preempt`, `multi`, `nr_throttled`,
`probe_self_cores`; nothing overwritten).

**What this kernel allows, and the fallback taken (deviation 11).** The node has BTF, but bpftrace 0.14 is built on
libbpf 0.5, which cannot parse the 6.12 kernel's BTF: `kfunc:finish_task_switch` fails with "BTF data not available"
and so would any struct walk (`prev->cgroups->…`); BEGIN/END probes fail too (the trigger uprobe on the stripped
binary). The handoff's fallback was a kprobe on `finish_task_switch` with a vmlinux header; the better one, and the
one taken, needs neither: **`tracepoint:sched:sched_switch`** runs with current == prev, so prev's cgroup is the
`cgroup` builtin and `prev_state` an argument, and **next's cgroup comes from a pid → cgroup map** learned whenever a
task is switched out (only a thread's first-ever switch-in is unclassified, class "unknown"). The rest is as
specified: on a service thread's resume the gap since its runnable mark is added to R[holder of this CPU] (holder =
the last task that ran there: the service's own cgroup → R_self, the aggressor's cgroup `/sys/fs/cgroup/gordion-aggr`
→ R_foreign, the root cgroup = kernel threads and un-sliced host processes → R_root, any other cgroup → R_other;
`multi` counts resumes with more than one holder in between); a service thread switched out runnable (low byte of
`prev_state` = 0) is marked runnable, one switched out asleep is marked and the time to its `sched_wakeup` goes to
Q_futex if it was inside a futex syscall, to Q_idle otherwise; a log2 histogram of the gaps and the per-thread R go out
once per second; `cpu.stat` (`usage_usec`, `nr_periods`, `nr_throttled`, `throttled_usec`) is read by the wrapper at
every record. What a BTF-capable bpftrace would add is only convenience (no map for next's cgroup); a newer bpftrace
would have to be built from source on this image, which I did not do.

**Probe v2 (Nov 11, B.5 runs): credit by time share.** v1 credited a thread's whole runnable gap to the last task that
ran on the CPU. On the recommendation service with one hog per core that put 86 ms per 100 ms window (two CPUs) into
R_self and 3.6 ms into R_foreign although the pod's CPU was pinned at the 0.50 fair share: the woken service threads
preempt the hog at once (26 µs gaps behind it, 138 resumes per window) and then wait behind each other. v2 keeps
per-CPU occupancy counters per holder class (`@occ0..3`, charged to prev at every switch), snapshots them when a
thread becomes runnable (preemption on that CPU, or wake-up on the target CPU including the running task's partial
slice) and splits the gap by Δoccupancy on resume; threads that resume on another CPU get the last-holder credit
(`mig`, ≈ 0.3 per window). The last-holder figures are kept beside it (`Lself`, `Laggr`, …). **Unit test** (CPU-bound
victim in its own cgroup + one hog on CPU 1, 118 windows): time-share R_foreign **50.0 ms** per 100 ms window, R_self
0.0, last-holder 48.2 / 0.0, 12.3 preemptions per window (8 ms slices); the two cgroups' `cpu.stat` 50 / 50. On the
recommendation pod the same probe reads R_foreign 6.7 ms and R_self 60.5 ms per window with two hogs (last-holder
4.5 / 63.5): the small R_foreign is the service's behaviour, not the probe's (2.4 iii).

**Two readings fixed by the smoke test** (`results/20261111-012406-g2b-probe-test`, 60 s at 400 req/s, synthetic,
k = 4, image k123g4 with the new recorder fields): (i) on a Go service the futex sleeps are the runtime's parked idle
Ms (Q_futex 326 ms per 100 ms window across four CPUs at ρ 0.42), so **the probe's Q_futex is the C++ (Thrift) Q only;
the Go Q is the recorder's new `rt` mutex-wait field (`/sync/mutex/wait/total`) and its `sched` field, and G is the
`rt` GC fields (`/cpu/classes/gc/total`, `/sched/pauses/total/gc`)** — the recorder change is in both copies
(`runtime/plugins/gordion/recorder.go`, `hotelReservation/gordion/recorder.go`), images `big_ctr:k123g4` and
`hr-gordion:gordion7` (the HR builder moved to Go 1.25 for the pause metric). (ii) At 400 req/s the service's threads
spend 4.1 ms per 100 ms window runnable behind their own threads (R_self, 349 resumes per window) and 1.0 ms behind
root/other tasks; U from `cpu.stat` 0.466 against 0.471 from RUSAGE; the probe's own CPU 0.009 cores at ≈ 3500
switches per second. (A first version credited unknown holders to the aggressor class when no aggressor cgroup was
given; fixed before the overhead run.)

### 2.3 Overhead of the probe (claim C6) — stop point

Paired unstressed uniform sweeps on node-3 CPUs 1,3,5,7 (k = 4, gate cap 3, image `k123g4`, 100 ms windows, raw on),
the Phase D schedule 40 → 900 req/s in 12 steps, probe off (`results/20261111-012821-g2b-overhead-probe-off`) then
probe on (`…-014041-g2b-overhead-probe-on`, 6736 probe records joined into `g2/g2_windows.csv`).
`analysis/g2_overhead.py`:

| step | req/s | served off / on | p50 off → on (ms) | ratio | p90 off → on | cores busy off → on | Δ cores | CPU per request off → on (ms) | R_self / R_other per 100 ms window (on) | resumes per window |
|---|---|---|---|---|---|---|---|---|---|---|
| 0 | 40 | 1.00 / 1.00 | 2.016 → 2.011 | 0.998 | 12.63 → 12.65 | 0.191 → 0.196 | +0.005 | 4.79 → 4.89 | 0.08 / 0.27 | 155 |
| 2 | 200 | 1.01 / 1.00 | 2.298 → 2.340 | 1.018 | 13.11 → 13.11 | 0.949 → 0.961 | +0.012 | 4.72 → 4.78 | 1.4 / 0.7 | 368 |
| 4 | 400 | 1.00 / 0.99 | 3.452 → 3.421 | 0.991 | 14.76 → 14.77 | 1.827 → 1.840 | +0.013 | 4.58 → 4.62 | 4.3 / 0.9 | 353 |
| 5 | 500 | 1.00 / 0.99 | 5.229 → 5.218 | 0.998 | 17.70 → 17.86 | 2.290 → 2.303 | +0.013 | 4.60 → 4.62 | 6.5 / 1.0 | 313 |
| 6 | 600 | 1.01 / 1.01 | 12.03 → 11.82 | 0.983 | 32.8 → 32.3 | 2.778 → 2.784 | +0.006 | 4.59 → 4.60 | 8.1 / 1.7 | 261 |
| 7 | 700 | 1.00 / 1.00 | 44.36 → 44.38 | 1.000 | 59.2 → 59.2 | 3.141 → 3.149 | +0.008 | 4.49 → 4.51 | 12.0 / 1.4 | 225 |
| 9 | 800 | 0.86 / 0.86 | 45.39 → 45.12 | 0.994 | 60.1 → 60.1 | 3.160 → 3.183 | +0.023 | 4.58 → 4.60 | 11.4 / 1.3 | 216 |
| 11 | 900 | 0.99 / 0.99 | 36.31 → 36.17 | 0.996 | 53.8 → 53.6 | 3.807 → 3.823 | +0.016 | 4.28 → 4.29 | 4.7 / 0.4 | 67 |

- **Per-endpoint floors** (lowest step, 123 endpoints): on ÷ off median **1.000**, IQR [0.999, 1.002], 99 % within ±5 %.
- **Knee**: 500 req/s in both runs by the caller rule (p50 > 2× the lowest step); served/target ≥ 0.95 to 700 req/s
  in both; the 750/800 steps dip to 0.91/0.86 in both runs and recover at 850/900 (a generator-side effect of this
  CPU, identical with and without the probe; 24 % of the generator's requests time out in both runs, all at those
  steps).
- **Service CPU**: +0.004 to +0.023 cores of 4 (0.1–0.6 %), CPU per request +0.01 to +0.10 ms (≤ 2 % at the lowest
  step, ≤ 0.5 % from 400 req/s up). This is where the in-kernel part of the probe lands: BPF program time is charged to
  the interrupted task, i.e. to the service's threads, so the service's own CPU delta is the right measure of it.
- **The probe's user-space CPU** (bpftrace + wrapper, from `/proc`): **0.0046 cores** over the whole sweep, flat with
  load (0.0044–0.0049 per minute), 3.1 CPU-seconds in 673 s; 0.0051 in the smoke run.
- Switch rate seen: 150–370 service-thread resumes per 100 ms window per 4 CPUs (1 500–3 700 per second) plus the
  other tasks' switches; the highest rates are at 200–500 req/s (the gate makes the top steps switch less).

**C6 holds: ≤ 0.6 % of the service's CPU and 0.5 % of a core for the probe process at the highest switch rate seen;
no floor shift (median ratio 1.000), no knee shift, p50 within ±2 % at every step.** No sampling fallback needed.

**Correction (Nov 13, RESULTS-K §1.7).** These numbers hold for the §2.3 probe at bpftrace's default 4 096-key maps.
From v3.1 on, `probe.py` sizes every map for 1 M keys, and the reader's map walks then cost about 0.2 cores for a
v4-like program (v5: 0.6–1.0 cores). Phase K1 also finds in-kernel cost outside the service's cgroup (other cgroups,
softirq) and, on unique-id, +20 % CPU per request (v5: +40 %). C6 does not hold for v3.1 and later.

What the on-run already shows for the later sections (first look, no claim yet): R_foreign is 0 at every step (no
aggressor, and the unknown-holder leak is fixed); R_self grows from 0.08 ms to 12 ms per 100 ms window between 40
and 700 req/s (3 % of the four CPUs' time runnable behind the service's own threads near the knee) and R_other stays
at 0.3–1.7 ms (kernel threads, kube daemons). **Correction (Nov 11, B.5):** the 850–900 req/s steps are *not* a served
overload. The "served 0.98–1.00" above is the recorder's arrivals ÷ target; the generator's own done ÷ sent is 0.18–0.28
there with its p50 at the 10 s timeout (client p50 9.99 s against a server p50 of 36–38 ms for the requests that got
through), and at 700 req/s the client p50 is already 284–330 ms against 44 ms on the server (caller/server ratio 6–7,
the Phase D rule's "backlog outside the process"). Served by both rules are steps 0–6 (40–600 req/s); 600 req/s is the
last one, with U 0.72 (slots 0.93 busy), server p50 10.9 ms and a client/server ratio of 1.14. The row-2 scenario of
A.5 c ii is therefore built from the served steps beyond a *truncated* profile (2.4 ii), not from 850–900 req/s.
Q_futex on this Go service is the parked Ms (100–470 ms per window across four CPUs: idle, not lock wait); the `rt`
mutex-wait field reads 0.000 ms median and the GC CPU 0.000 median with bursts to 6.8 ms per window.

### 2.4 Re-runs with the probe on (B.5 order) — stop point after (iv)

Common setup: node-3 socket 1, image `k123g4` (synthetic, CPUs 1,3,5,7, k = 4, gate cap 3) or `hr-gordion:gordion7`
(recommendation, CPUs 1,3, k = 2, cap 1), 100 ms windows, raw on, probe v2 (time-share credit, §2.2) on the SUT's
CPUs, the aggressor `stress-ng` in its own cgroup `/sys/fs/cgroup/gordion-aggr`, `cpu.stat` sampled. Dictionary for
(i), (ii), (iv): the probe-off uniform sweep of 2.3; for (iii): the unstressed recommendation sweep run first in the
pair, with costs from it (`costs-recommendation-G170-avg.json`, 0.175 ms per request). Truth: the twin at the same
second (i, iv), the unstressed sweep at the same step (iii), the probe-off sweep at the same step (ii). Scoring:
`analysis/g2_verdict.py` (rows 1–7 of §0 plus "7 = E ≫ 0 and no row fits"; thresholds from the twin's served steps:
≈ within its per-step IQR, ≫ beyond 3×, with floors of 10 ms/s × k for R and Q, 50 ms/s for G, 0.15 for E;
R_foreign's noise scale is the twin's run-queue delay behind foreign-class holders — kernel threads, daemons — since its
own R_foreign is 0; the origin rows 2/3/4/7 are read on slow seconds only, y ≥ 0.5, as the Phase F cells were).
Per-scenario `g2/verdict.csv`; figure `results/g2/figG2-5-verdicts.png` (i and iii from the probe-v3 runs, which add the
cpuset occupancy by holder class; ii and iv from the v2 runs; the v2 runs of i and iii gave the same rows within 2 seconds).

| scenario | seconds scored | twin truth: ext / slow-int / fast | verdict rows (count) | agreement with the truth class* | E vs truth share (slow seconds, within ±0.15) |
|---|---|---|---|---|---|
| **i** synthetic, uniform → mid7 ramp at 400 req/s across the knee, 1 aggressor on CPU 1 of 4 | 147 | 40 / 29 / 78 | 2: 41 (35 of them ext-truth) · 6: 95 · 7: 10 · 4: 1 · **1: 0** | 72.8 % | 57 % (median +0.04) |
| **ii** synthetic, 600 req/s served past a ρ ≤ 0.57 profile (2.3 correction, deviation 18) | 41 (plus 351 fast seconds below) | 2 / 38 / 1 | **2: 27** · 7: 10 · 6: 2 · 3: 1 | 99.2 % over all 392 | — (truth ≈ 0 by construction: the step is served by the same service) |
| **iii** recommendation, one aggressor per core, 1000 → 12 000 req/s | 164 (steps 1000–6000; 8000+ collapse, undefined) | 41 / 83 / 40 | **1: 163** (every second but one, 39 of them fast) | 26 % by the class rule; 100 % as "aggressor present" | **97 %** (median +0.00) |
| **iv** synthetic, 400 req/s, `stress-ng --stream` × 4 on CPUs 9,11,13,15 (socket 1, outside the cpuset) | 116 | 0 / 0 / 116 | **6: 116** | 100 % | 72 % (no slow seconds; E 0.01 vs truth 0.01) |

\* class rule: ext → rows 1 or 3; slow-int → rows 2, 4, 5, 6, 7; fast → row 6.

**(i) Mix shift across the knee with one aggressor on one of four cores.** The decomposition E follows the twin truth
through the ramp (E and truth both rise from ≈ 0.1 to 0.8 after t = 100 s, Fig. G2-5 top right); the verdict reads
**row 2, own overload**, on the 39 seconds where E ≫ 0 and the slots are busy, 33 of which the truth calls
extrinsic. Row 1 never fires: **R_foreign is 1.9 ms/s median (p90 8.5) against a noise floor of 40 ms/s**, while
R_self is 509 ms/s against the twin's 37 (14×), preemptions 69 per 100 ms window against 4, and the pod's own CPU
is *higher* than the twin's (U 0.576 vs 0.512), I_g 0.995. The hog's cost to this service is not direct preemption of
its threads: wake-up placement avoids the busy CPU 1, the service's four Ms crowd the other three CPUs and queue
behind each other, and the probe reports exactly that, as crowding (R_self ≫ twin on 147 of 147 seconds). **The
occupancy counters (probe v3) show where the hog's time went: the aggressor cgroup holds 99.5 ms of every 100 ms
window of the four CPUs' 400 ms, 24.8 % of the cpuset (p90 27 %), i.e. all of CPU 1; the service's own occupancy is
unchanged (226 vs 229 ms).** The holder-credit signal of §0 row 1 therefore does not name a time-slicing hog on a
sleeping-heavy Go service; the cpuset occupancy of the aggressor cgroup does. Read with the occupancy rule (aggressor
≥ 5 % of the cpuset's time on a slow second → row 1), the matrix of (i) becomes **row 1 on 69 seconds, all 40
ext-truth seconds among them** and 29 slow-int ones, rows 6/7 unchanged on the rest.

**(ii) Served overload past the profile.** On the one served step beyond a ρ ≤ 0.57 dictionary (600 req/s, ρ 0.72,
slots 0.94 busy, R_foreign 0, I_g 0.98, E 0.50 median) the verdict is **row 2 on 27 of 41 seconds**, row 7 on 10 (slot
busy 0.85–0.90 at those seconds, below the 0.9 rule), row 6 on 2, row 3 on 1. One step, one service; the 850–900
req/s steps could not be used (not served, 2.3). Row 2 works where a served overload exists; the open-loop generator
makes such steps rare (past the knee the queue grows until the client times out).

**(iii) Recommendation, one aggressor per core.** The hogs pin the pod's CPU at the 0.50 fair share from 8000 req/s up
and cut its served capacity from ≥ 10 000 to 6000 req/s (served 0.77 / 0.58 / 0.45 at 8000 / 10 000 / 12 000; the
unstressed sweep serves 1.00 to 10 000). On the served steps **E matches the truth share within ±0.15 on 100 % of the
slow seconds** (truth 0.33 / 0.27 / 0.45 / 0.82 at 1000 / 2000 / 4000 / 6000 req/s). R_foreign is 79 ms/s median
(p90 134) against a foreign-class noise of 4 ms/s, so **row 1 fires on every second**, including the 110 whose truth
share is below 0.5 and the 10 fast ones: the row is a presence test and the truth class is a magnitude test (the
aggressor's share of the latency); at 1000–4000 req/s the hogs delay the threads (R_foreign 50–135 ms/s) but the
latency they add is 27–45 % of the total, at 6000 req/s 82 %. R_self is 864 ms/s against the twin's 340 and I_g 0.83
(the elastic base of 1.7/1.8). The occupancy counters read **the hogs holding 83 % of the two CPUs' time at 1000
req/s falling to 52 % at 6000 and 48 % from 8000 up** (the fair share), the pod 17 → 52 %. Whether row 1 should carry
a magnitude gate (R_foreign or the occupancy as a share of the latency, or E ≥ 0.5 as in the cells) is a design choice
left open here; with the gate "row 1 and E ≥ 0.5" the matrix would read 41 ext → row 1 (step 3), 83 slow-int →
rows 2/7, 40 fast → row 6.

**(iv) Stream aggressor outside the cpuset, same LLC.** No effect on this victim: E 0.01 against truth 0.01, I_g 1.044
against the twin's 1.014 (+3 %, inside the band), R_foreign 0 (the aggressor never holds the service's CPUs), R_self
and preemptions unchanged, verdict row 6 on 116 of 116 seconds, correct. Claim C3 is not demonstrated: four `--stream`
workers on the other four cores of the socket (35 MB L3) do not slow the spin kernels of the synthetic service, whose
working set lives in L1; the memory-heavy endpoint for this test is `VioMemWalk` (G3), and a real service with a cache
footprint (recommendation, post-storage) is the next candidate.

**Probe cost in these runs:** 0.0051 cores (bpftrace + wrapper) in every run; service CPU as in 2.3.

### 2.4 C — Addendum C.1: row 1 on occupancy; re-score of i, ii, iii (C.2) and the three explanations (C.3)

**Rule C.1 as implemented** (`analysis/g2_verdict.py`, columns `row` = C.1, `row_v0` = the §0 rule, `phi_cpuset`,
`holder_cgroup`, `why7`): per second, in this order — 6 if E ≈ 0 (or the second is not slow); 1 if E ≫ 0 and φ ≫ 0;
3 if φ ≈ 0 and I_g ≫ 1; 4 if φ ≈ 0, I_g ≈ 1, Q ≫ 0; 2 if φ ≈ 0, I_g ≈ 1, Q ≈ 0, slot busy ≥ 0.9; otherwise **7 = no
verdict**, with the failed condition recorded. φ = the aggressor cgroup's share of the cpuset's CPU time in the second
(probe v3 `Oaggr` ÷ (k × 1 s)); φ ≫ 0 above 0.05 (the twin's φ is identically 0, so the threshold is a floor, not an
IQR); `holder_cgroup` = the aggressor cgroup when φ ≫ 0, with R_foreign ≫ 0 noted as corroboration. The amount stays
%ext = E ÷ Lat and the cell (y ≥ 0.5 × E ≥ 0.5) is unchanged. G ≫ 0 is reported alongside (3 seconds in iii, none
elsewhere). Where the probe has no occupancy fields and the run has no aggressor cgroup (ii), φ is 0 by construction.

| scenario | truth ext / slow-int / fast | C.1 rows | §0 rows | cells (slow × ext, %ext = E) against the truth cell |
|---|---|---|---|---|
| i mix shift + 1 aggressor (v3) | 40 / 29 / 78 | **1: 52 (39 ext, 13 slow-int)** · 6: 95 (1 ext, 16 slow-int, 78 fast) | 2: 41 · 7: 10 · 4: 1 · 6: 95 · 1: 0 | **89.8 %** (132 of 147): slow/ext → slow/ext 34, slow/int → slow/int 20, fast/int 78; misses 9 slow/ext read as int by the truth, 6 the other way |
| ii served step past the profile | 2 / 38 / 1 (+ 351 fast below) | 2: 27 · 7: 10 · 6: 3 · 3: 1 (identical: φ = 0) | same | 94.9 % (372 of 392): the 20 misses are slow/ext by the scorer with a slow/int truth (the truth baseline is the same service one step earlier, so its share is ≈ 0 by construction) |
| iii recommendation + 1 aggressor per core (v3) | 41 / 83 / 40 | **1: 124 (41 ext, 83 slow-int)** · 6: 40 (all fast) | 1: 163 (incl. 39 fast) · 6: 1 | **99.4 %** (163 of 164): slow/ext 41 → 41, slow/int 82 → 82, fast/int 40 → 40, 1 miss |

Reading. C.1 does what it was written for: on (i) every extrinsic-truth second but one lands in row 1 (φ 0.25,
holder `gordion-aggr`, R_foreign ≈ 0: the capacity-loss face), on (iii) all 41 (φ 0.52–0.83, R_foreign 76 ms/s: the
preemption face), and the fast seconds that the §0 rule put in row 1 on (iii) go to row 6 because E ≈ 0 is tested
first. Row 1 also fires on 13 (i) and 83 (iii) slow seconds whose truth share is below 0.5: the row says the aggressor
is present on the service's cores and E is beyond the band; the share is in the cell, where the scorer and the truth
agree on 90–99 % of the seconds. (ii) is unchanged (no aggressor).

**C.3 (a) Row 7 = "no verdict".** It is the second where E ≫ 0 on a slow window but no origin condition closes: φ ≈ 0,
I_g ≈ 1, Q ≈ 0 and the slot is *not* busy enough (< 0.9), or one signal sits between its ≈ and ≫ thresholds. In (i)
under the §0 rule it was 10 seconds (4 ext, 6 slow-int): all ten have R_foreign ≈ 0, I_g inside the band, Q 0, and
slot busy 0.72–0.89 while E ≥ 0.31 — the crowding seconds early in the ramp (ρ 0.5–0.6), where the hog has taken CPU 1
but the three remaining CPUs are not yet full. Under C.1 they are row 1 (φ 0.25), and row 7 is empty in (i).
**(b) The 14 of 41 seconds of (ii) that missed row 2:** 10 are row 7 with the one failed condition "slot busy < 0.9"
(0.83–0.89: cores busy 3.3–3.6 of 4 with the cap at 3 — the gate's three slots are full but the transport goroutines'
share makes the process-level figure fluctuate around the rule), 3 are row 6 (E 0.26–0.30, under the ≫ threshold of
0.31 derived from the twin's served steps), 1 is row 3 (I_g 1.13 against a band of ± 0.05 around 1.02, a one-second
CPU burst). None is a wrong origin; all are threshold margins on a step that sits exactly at the boundary (E median
0.50, slot busy median 0.94).
**(c) (i)'s E-vs-truth 57 % against the Phase F row.** They are different statistics on the same scenario design:
the 57 % is the share of slow seconds with |E − truth share| ≤ 0.15; the Phase F number for the mid7 ramp with one
aggressor (RESULTS-F §1.3, the row this re-run reproduces) is a **cell** accuracy, 57.8 % strict / 96.6 % when it
speaks / 40.1 % abstained, and the cell accuracy here is **89.8 %** with no abstention (the guards are not applied in
the verdict path; the dictionary covers the ramp's ρ range on this hardware). The configuration differs in three ways:
clnode170 (Haswell 2.0 GHz) instead of clnode258 (Ice Lake 3.1 GHz) — the knee sits at the same ρ but the floors are
1.0× the configured values; the dictionary is the probe-off uniform sweep of this cluster (12 steps to 900 req/s)
instead of `d-a2-uniform`; the hog is pinned to CPU 1 of {1,3,5,7} in its own cgroup instead of a host-cgroup hog on
one of cores 2–5. The decomposition error itself is small (median +0.04 on slow seconds) but the truth share swings
between 0.1 and 0.8 second to second across the ramp (Fig. G2-5, top right) and a ±0.15 tolerance on a 5 s-smoothed
quantity catches only 57 % of those seconds; on (iii), where the truth is a flat step, the same statistic reads 97 %.

### 2.4 D — Addendum C.4 runs

**(iv-b) 30 % VioMemWalk mix, working set 128 MB (past the 35 MB L3), stream aggressor outside the cpuset — and G3.1.**
Runs `results/20261111-045032-g2d-ivb-memwalk30-sweep` (unstressed, 100 → 700 req/s × 45 s) and
`…-045910-g2d-ivb-memwalk30-stream` (same sweep with `stress-ng --stream` × 4 on CPUs 9,11,13,15 of socket 1, the
aggressor cgroup never touching the cpuset), probe v3, k = 4.

*G3.1, the self-interference bias.* The memwalk mix against the **uniform** dictionary (the probe-off sweep,
`b4g3/`): D[ρ; mix] uses the mix's F̄ (5.03 ms, the memwalk endpoint's 4 ms floor pulls the mixture median up from
2.08) and K (4.25 ms), h(ρ) from the uniform sweep.

| step | req/s | ρ | served | p50 (ms) | D[ρ; mix] (ms) | bias (p50 − D) | bias share | I_g (g from the uniform sweep) |
|---|---|---|---|---|---|---|---|---|
| 0 | 100 | 0.11 | 1.00 | 5.03 | 4.58 | +0.45 | +0.09 | 1.08 |
| 1 | 200 | 0.22 | 1.03 | 5.08 | 4.86 | +0.21 | +0.04 | 1.10 |
| 2 | 300 | 0.32 | 1.01 | 5.13 | 5.13 | −0.01 | 0.00 | 1.09 |
| 3 | 400 | 0.42 | 1.00 | 5.22 | 5.66 | −0.43 | −0.08 | 1.10 |
| 4 | 500 | 0.53 | 1.01 | 6.26 | 7.30 | −1.04 | −0.17 | 1.08 |
| 5 | 600 | 0.63 | 1.01 | 13.21 | 11.84 | +1.36 | +0.10 | 1.08 |
| 6 | 700 | 0.69 | 0.93 | 48.14 | 14.72 | +33.4 | +0.69 | 1.05 |

**The stated-limit number: below the mix's own knee the uniform dictionary is within ±0.17 of the memwalk mix's
latency (bias −1.0 to +1.4 ms on 5–13 ms); at the step past the knee (700 req/s, ρ 0.69, served 0.93) it
under-predicts by 33 ms (69 % of the latency).** The mix's knee sits at ρ ≈ 0.63 against the uniform's 0.72: the
memory-bound endpoint makes the service saturate earlier than its CPU budget says, and I_g tracks it — the memwalk
mix burns 5–10 % more CPU per unit of configured work than the uniform profile predicts at every step (I_g 1.05–1.10
against the uniform-based band of ±0.018), the CPU-side face of the same self-interference (claim C5: the bias is
visible in I before it is visible in the latency; where the latency bias exceeds ±0.15 the I_g excess is the same
8–10 % it was below the knee, so I_g does not scale with the bias, it flags the mix).

*The stream aggressor.* No effect on this victim either: served steps identical, E 0.00 against a truth share of
0.00, cells 100 % (246 of 246), verdict row 6 on 244 seconds and row 2 on 2 (two seconds at 600 req/s that crossed
the slot rule), φ 0 (the aggressor never holds the cpuset), R_foreign 0, R_self unchanged — and I_g *falls* under the
aggressor by 0.04–0.06 at 400–700 req/s (1.129 vs 1.167 at 400), outside the synthetic band, i.e. the service burned
slightly less CPU per request with four stream workers on the other cores, not more. A 128 MB pointer chase misses
the L3 on every access with or without a neighbour, and four `--stream` threads do not saturate the socket's memory
bandwidth, so the aggressor has nothing of this victim's to take. **C3 is not demonstrated at a working set past the
LLC; the case where an LLC aggressor can act is a working set that fits the L3 and is evicted by the neighbour** —
run as (iv-b′) with `MEMWALK_MB=24` after (v), reported below if it completes.

**(iv-b′) the same mix with `BIGSVC_MEMWALK_MB=24` (inside the 35 MB L3) — my addition, not in the handoff.**
Runs `results/20261111-054428-g2d-ivb24-memwalk30-sweep` and `…-055100-g2d-ivb24-memwalk30-stream`, same schedule,
aggressor, probe v3, k = 4 (deviations 27, 28).

| step | req/s | ρ | served twin / stream | p50 twin (ms) | D[ρ; mix], uniform dictionary | bias (p50 − D) | bias share | I_g twin / stream (uniform g) | p50 under stream (ms) |
|---|---|---|---|---|---|---|---|---|---|
| 0 | 100 | 0.11 | 1.00 / 1.00 | 4.41 | 4.56 | −0.15 | −0.03 | 1.10 / 1.09 | 4.10 |
| 1 | 200 | 0.22 | 1.02 / 1.02 | 4.34 | 4.93 | −0.59 | −0.13 | 1.08 / 1.08 | 4.11 |
| 2 | 300 | 0.32 | 1.01 / 1.01 | 4.34 | 5.26 | −0.92 | −0.21 | 1.06 / 1.06 | 4.12 |
| 3 | 400 | 0.42 | 1.00 / 1.00 | 4.45 | 5.67 | −1.22 | −0.27 | 1.06 / 1.06 | 4.18 |
| 4 | 500 | 0.53 | 1.01 / 1.01 | 5.08 | 7.24 | −2.16 | −0.43 | 1.04 / 1.03 | 4.70 |
| 5 | 600 | 0.63 | 1.01 / 1.01 | 9.83 | 11.39 | −1.55 | −0.16 | 1.04 / 1.03 | 8.94 |
| 6 | 700 | 0.72 | 0.98 / 1.00 | 45.76 | — (ρ past the uniform profile) | — | — | 1.01 / 0.99 | 44.49 |

*G3.1 at an in-LLC working set.* The uniform dictionary **over**-predicts this mix: the bias is −0.03 at ρ 0.11 and
grows to −0.43 at ρ 0.53, beyond ±0.15 from ρ 0.32 on, with the opposite sign to the 128 MB case. The mix's own p50
stays at its floor (4.3–4.5 ms) up to ρ 0.42 and is 5.1 ms at ρ 0.53, where the uniform's h(ρ)·K already adds 2.7 ms:
the in-LLC walk queues less than the uniform profile at the same ρ (its own dictionary has F̄ 4.44 ms, K 4.24 ms
against the uniform's 2.08 / 5.10). I_g against the uniform g is 1.04–1.10, the same excess as at 128 MB — so I_g
flags the mix in both cases but does not carry the sign or the size of the latency bias (as stated for 128 MB).

*The stream aggressor.* Again no slowdown: served steps identical, cells 100 % (246 of 246), row 6 on 244 seconds and
one second each in rows 2 and 3 at 600 req/s (E 0.30 / 0.36 against a truth share of 0; the row-3 second is a
one-second I_g of 1.28), φ 0, R_foreign 0, I_g within ±0.01 of the twin at every step. **At the endpoint level**
(raw samples, steps 0–1, both working sets): the memory-walk endpoint is *faster* under the aggressor — `VioMemWalk`
p50 5.28 → 4.39 ms at 128 MB (−17 %), 4.70 → 4.22 ms at 24 MB (−10 %), p90 5.56 → 4.61 and 6.45 → 4.35 — and the 120
CPU-spin endpoints are slower by a constant **+0.034 ms (24 MB) / +0.039 ms (128 MB) per request** (p10–p90 of the
shift across endpoints +0.02 to +0.04 ms), whether the endpoint costs 0.25 or 20 ms: a per-request fixed cost on the
request's transport / serialisation path, 1.6 % of the median endpoint. The mixture p50 falls (4.42 → 4.10 ms at
24 MB) because the memwalk endpoint carries 30 % of the mass near the median. Hypothesis for the speed-up, not
tested: uncore frequency scaling — on this Haswell-EP the L3 ring and memory-controller clock follow socket activity;
with ≤ 3 of 14 cores busy the uncore idles down and four stream workers hold it at its maximum, which a
latency-bound pointer chase gains from. What it would take: the uncore ratio (`MSR 0x621`, or
`/sys/devices/system/cpu/intel_uncore_frequency/` if the driver is present) sampled through both runs, and a control
with the stream workers on socket 0.

**Conclusion for C3.** A row-3 verdict (service-time inflation, I_g ≫ 1 with φ ≈ 0) is not demonstrated on this hardware
with `stress-ng --stream` × 4 on four neighbouring cores, at either working set: the shared-resource effect that
exists is +0.035 ms per request on the fixed path, two orders below E ≫ 0, and the memory-bound endpoint benefits. An
aggressor that costs the victim CPU time per request — SMT siblings of the SUT's cores, or a bandwidth saturator
(8–12 stream workers) — is what would test row 3; neither is in the handoff and neither was run.


**(v) SocialNetwork post-storage (C++ Thrift, one thread per connection), k = 1, the "p90-only" case.** Setup:
the SN gordion stack in namespace `sn` (image `sn-gordion:g5`, datastores and nginx on node-1, the other services on
node-1/2), post-storage pinned to node-3 CPU 1 (`taskset`), hrgen `-app sn` with the compose-heavy mix (compose 0.6 /
home-timeline 0.3 / user-timeline 0.1) from node-3 CPUs 17–27, sweep 200 → 2000 req/s × 45 s. Dictionary = the sweep's
own served steps (200–800 req/s, ρ 0.1–0.7 with post-storage's NNLS costs; c̄ 0.705 ms in pair 1, 0.810 ms in pair 2);
from 1000–1200 req/s the chain collapses in front of post-storage (served/target 0.36–0.83) while its one core is
87–100 % busy. Aggressor = one hog on CPU 1 in `gordion-aggr` (the capacity-cut row); probe v3 on CPU 1. Three pairs,
each twin then hog: **pair 1** `results/sn/20261111-052658-g2e-v-ps-compose-heavy-k1` / `…-053546-…-stress1`;
**pair 2** `…-061423-g2e-v2-…` / `…-062307-…-stress1` with `hostPID: true` (deviation 29); **pair 3** `…-063755-g2e-v3-…`
/ ``…-064627-…-stress1`` with probe v3.1 (deviation 32). Per-request rows from pair 3; the verdict from all three.

| step | req/s | ρ | served twin / hog (pair 1; pair 2) | post-storage cores busy twin / hog (pair 1; pair 2) | φ, hog's share of CPU 1 (pair 1; pair 2) | p50 twin / hog, ms (pair 1; pair 2) | p90 twin / hog, ms (pair 1; pair 2) | per-request p95 twin / hog, ms (pair 1; pair 2) | truth (p50) |
|---|---|---|---|---|---|---|---|---|---|
| 0 | 200 | 0.14–0.16 | 1.00 / 1.00; 1.00 / 1.00 | 0.13 / 0.17; 0.18 / 0.17 | 0.82; 0.83 | 0.065 / 0.048; 0.072 / 0.049 | 1.14 / 1.47; 1.52 / 1.46 | 3.9 / 5.2; 5.0 / 5.3 | fast |
| 1 | 400 | 0.28–0.33 | 0.99 / 0.99; 0.99 / 0.99 | 0.26 / 0.34; 0.34 / 0.33 | 0.66; 0.67 | 0.061 / 0.048; 0.068 / 0.049 | 1.22 / 1.72; 1.72 / 1.71 | 4.1 / 6.4; 6.0 / 6.5 | fast |
| 2 | 600 | 0.43–0.49 | 1.00 / 1.00; 1.00 / 1.00 | 0.40 / 0.50; 0.50 / 0.49 | 0.50; 0.50 | 0.049 / 0.048; 0.063 / 0.048 | 1.39 / 2.22; 2.03 / 2.20 | 4.9 / 9.2; 8.5 / 9.9 | fast |
| 3 | 800 | 0.56–0.64 | 0.98 / 0.98; 0.98 / 0.98 | 0.56 / 0.66; 0.66 / 0.66 | 0.33; 0.34 | 0.048 / 0.048; 0.049 / 0.048 | 1.79 / 2.98; 2.64 / 3.14 | 7.0 / 14.6; 12.4 / 20.8 | fast |
| 4 | 1000 | 0.71 | 0.99 / **0.78**; **0.72** / 0.99 | 0.79 / 0.83; 0.87 / 0.88 | 0.28; 0.16 | 0.048 / 0.053; 0.059 / 0.049 | 3.0 / 17.4; 9.9 / 11.9 | 15.8 / 3087; 3678 / 149 | undefined (one of the two not served) |

Pair 3 (probe v3.1; twin `…-063755-g2e-v3-…`, hog `…-064627-…-stress1`): served twin / hog 1.00 / 1.00, 0.99 / 0.99, 1.00 / 1.00, 0.98 / 0.98 at 200–800 req/s and 0.99 / 0.77 at 1000; p50 twin 0.071 / 0.067 / 0.062 / 0.049 / 0.047 ms against hog 0.048 / 0.048 / 0.047 / 0.047 / 0.053; p90 twin 1.49 / 1.69 / 1.98 / 2.53 / 3.82 ms against hog 1.46 / 1.70 / 2.09 / 2.81 / 7.39; cores busy twin 0.17 / 0.33 / 0.48 / 0.62 / 0.79, hog 0.16 / 0.32 / 0.47 / 0.62 / 0.75; φ 0.75 (probe's first 22 s).

*What the aggressor does here.* The p50 (0.05–0.07 ms, a cache hit in the handler) never moves — where it differs it
falls, the hog keeping CPU 1 out of its idle states — while the tail grows: at 800 req/s the p90 is +67 % (pair 1)
and +19 % (pair 2), the per-request p95 +110 % and +68 %; at 200–400 req/s the tail inflation is +30–57 % in pair 1
and within the twin-to-twin spread in pair 2 (the twins' own p90 at 200 req/s differ by 1.14 vs 1.52 ms: the Thrift
tail's run-to-run noise is the size of the hog's effect at low load). CFS gives the service what it asks for as long
as it asks for less than the hog's share (φ falls from 0.82 to 0.16–0.28 as the service's demand rises), so the
capacity cut is soft until the core is full. The served/target drop at 1000 req/s is the chain's, not
post-storage's (its core is 0.8–0.9 busy there), and it is bistable between runs (pair 1: twin served, hog not; pair
2: the reverse), so "the knee moves" is not a reading these runs support. **CPU per request is not an aggressor
signal on this service against a single twin**: I_g reads 1.26 (median) in pair 1 and 0.995 in pair 2, because the
two unstressed sweeps themselves differ by 25–33 % in CPU per request (cores busy 0.13 vs 0.18 at 200 req/s, c̄ 0.705
vs 0.810 ms; the hog runs are at 0.17 in both pairs) — the store grows by ~250 000 posts per sweep (StorePost is 60 %
of the compose path), a plausible cause not tested. The §1.2 / C.5 reading (the Go services' elastic base) is not what
this shows; it shows the twin baseline of a store-backed service drifting between sweeps.

*Verdict (C.1), pairs 1 and 2: row 6 on all 164 served seconds of each; truth fast on all 164; cells 100 %.* E is a p50
quantity (Lat_p50 − D[ρ; mix]) and the p50 has nothing to explain, so C.1 stops at row 6 although the aggressor is
plainly present (φ 0.3–0.8, `holder_cgroup` = `gordion-aggr`). The §0 rule put 29 (pair 1) / 24 (pair 2) fast seconds
of step 0 in row 1 (R_foreign 15 ms/s, above the 10 ms/s floor) — the presence test without the E ≈ 0 gate.
Pair 3 is identical (row 6 on 164 of 164, cells 100 %; §0 rule: row 1 on 4 seconds), with φ read from the probe's first 22 s only — probe v3.1 aborted there (deviation 32). Q_futex is not usable here as a lock-wait signal: the futex-sleep sum is dominated by parked worker
threads (30 000 threads over a sweep), 2.4 s per second at 200 req/s; row 4 is not reached anyway. G ≡ 0 (no GC).

*The p90 stream, scored beside it* (A.3 code `g_d90.py`, dictionary D90 from pair 1's twin sweep, truth90 = the twin's
p90 at the same second; `results/g2/d90-v/`): at q = 0.9 the truth is slow on 320 of 475 windows and extrinsic on 76;
the scorer is right on 55.8 % strict / 63.5 % when it speaks / 12.2 % abstained, decomposition error median +0.07,
within ±0.15 on 54 %; the same code at q = 0.5 reads 84.8 / 96.6 / 12.2 %. The p90 dictionary sees the aggressor on
the tail where the p50 cannot, at lower accuracy than the p50 pipeline on the gated Go services (Phase F): the Thrift
tail is a wide, noisy quantity (floor noise 1.04 ms at p90 against 0.03 ms at p50).

*Per-request rows* (`analysis/g5_tail.py` → `<run>/g5/requests.csv`, one row per request with its thread id: queue =
decode + dispatch; blocked = the request's store calls (Mongo / memcached) including the wake-up after each reply;
rest = processing − queue − run-queue delay; run-queue delay = the thread's per-second R from the probe's per-thread
map split equally over the thread's requests in that second, with the foreign share from the second's holder split).
Pairs 1 and 2 carry rows for the first two steps only (deviations 29, 32); pair 3 for every step. Pair 3 produced none (probe abort, deviation 32). **Pair 4** (`…-070059-g2e-v4-…` / `…-070941-…-stress1`, probe v3.2, `hostPID: true`) covers every step; its rows are the per-request result (table after the pair-2 one). From pair 2, the two steps that probe covered:

| step | run | requests | p95 (ms) | tail request (≥ p95), medians: total | queue | run-queue delay, foreign | run-queue delay, self | blocked (store calls) | rest of processing | tail requests with run-queue delay > 0 | all requests, medians: run-queue delay / blocked (ms) |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 0 (200 req/s) | twin | 9131 | 5.01 | 6.07 | 0.003 | 0.000 | 0.333 | 4.03 | 1.48 | 88 % | 0.113 / 0.544 |
| 0 | hog | 9123 | 5.34 | 6.76 | 0.003 | 0.083 | 0.411 | 4.74 | 1.23 | 100 % | 0.146 / 0.536 |
| 1 (400 req/s) | twin | 18079 | 5.98 | 7.30 | 0.003 | — | — | 4.90 | 2.21 | — | — / 0.562 |
| 1 | hog | 18074 | 6.51 | 8.37 | 0.003 | — | — | 5.61 | 2.21 | — | — / 0.572 |

(— = the probe's thread map filled during this step.) Reading: a tail request is a store call — the blocked segment is
66–70 % of it, the handler's own processing 1.2–2.2 ms, decode + dispatch 3 µs. Under the hog the tail request is
0.7 ms longer at 200 req/s and the growth is in the blocked segment (4.03 → 4.74 ms); the thread's own run-queue
delay accounts for 0.08 ms of it (the median tail request's foreign share; 0.11 → 0.15 ms over all requests). The
remaining ~0.6 ms sits between the store's reply and the thread's resumption, which the probe does not see as the
thread's run-queue delay. Hypothesis, not tested: the reply's network receive runs in softirq context on CPU 1 behind
the hog before the thread is even woken (ksoftirqd is in the root cgroup, so its delay would be R_root of a different
task). What it would take: `irq:softirq_entry/exit` on CPU 1 in the probe, attributing softirq delay by the task it
interrupted, or the NIC's RX queue IRQ moved off the SUT core as a control.

**Pair 4, per request (probe v3.2: every thread classified; 411 k / 373 k requests on 117 k / 99 k threads, run-queue
delay attributed on 93–95 % of the requests of the served steps).** Two latency streams per request: the *full* latency
(tap → preWrite) and the *processing* stream with the blocked segment excluded (the stream the service dictionary and the
twin truth use: `GORDION_LAT=unstamped` on this service is processing).

| step | req/s | full p50 / p90 / p95 (ms), twin → hog | p90 rise | processing (blocked excluded) p50 / p90 / p95, twin → hog | p90 rise | blocked p50 / p90, twin → hog | run-queue delay per request, mean, twin → hog (foreign part) |
|---|---|---|---|---|---|---|---|
| 0 | 200 | 0.65 / 4.20 / 5.06 → 0.64 / 4.57 / 5.64 | +9 % | 0.071 / 1.55 / 1.84 → 0.051 / 1.57 / 1.90 | +1 % | 0.53 / 2.55 → 0.53 / 2.86 | 0.22 → 0.27 ms (0.06) |
| 1 | 400 | 0.74 / 4.82 / 6.25 → 0.76 / 5.31 / 7.24 | +10 % | 0.065 / 1.77 / 2.31 → 0.050 / 1.83 / 2.60 | +4 % | 0.55 / 2.93 → 0.57 / 3.28 | 0.53 → 0.62 ms (0.08) |
| 2 | 600 | 0.96 / 5.97 / 8.16 → 1.08 / 7.45 / 11.2 | +25 % | 0.053 / 2.10 / 2.97 → 0.050 / 2.35 / 3.69 | +12 % | 0.60 / 3.62 → 0.70 / 4.75 | 0.96 → 1.95 ms (0.15) |
| 3 | 800 | 1.41 / 11.0 / 18.0 → 1.84 / 14.1 / 24.6 | +29 % | 0.051 / 2.90 / 4.96 → 0.051 / 3.47 / 6.26 | +20 % | 1.10 / 7.61 → 1.43 / 10.0 | 3.28 → 4.06 ms (0.29) |

Tail composition (requests ≥ the step's p95, medians): the blocked segment is 70–90 % of a tail request in both runs
(4.1 → 4.9 ms at 200 req/s, 26.6 → 33.7 ms at 800), decode + dispatch 3–4 µs, the handler's own processing 1.4 ms
(0.0 at 800, where the whole tail is the store call); the thread's run-queue delay in the tail is 0.30 → 0.31 ms self
plus 0 → 0.07 ms foreign at 200 req/s and 3.06 → 3.47 self plus 0 → 0.21 foreign at 800. With every thread visible,
R_self is large on this service even without the aggressor (mean 0.2–3.3 ms per request): on one core with hundreds of
connection threads the kernel run queue *is* the service's queue, and the hog adds to it (R_self +0.05–1.0 ms per
request, R_foreign 0.06–0.29 ms). What the earlier pairs' R ≈ 0 above step 1 showed was the probe's map limit
(deviation 32), not the service.

**(vi) VioMutex at 10 % of a uniform mix, no aggressor.** Run `results/20261111-050714-g2d-vi-mutex10` (200 / 400 /
600 req/s × 60 s, k = 4, probe v3, dictionary = the probe-off uniform sweep; truth per second = the latency against
the uniform sweep's p50 at the same rate, deviation 26). **Row 4 does not fire: the endpoint's lock wait is not
measurable at this share.** Q_mutex (the recorder's `/sync/mutex/wait/total` delta) is 0.00 ms/s at 200 and 400
req/s and 0.96 ms/s median at 600 (p90 14 ms/s, single bursts); E is ≈ 0 at every step — the mutex mix is slightly
*faster* than the uniform dictionary's prediction (p50 2.06 / 2.92 / 7.13 ms against D 2.20 / 3.11 / 7.76; VioMutex's
configured cost 2 ms is below the uniform c̄ of 4.3 ms); I_g 1.01–1.03; R_foreign 0, φ 0. Verdict: row 6 on 164 of
168 seconds, row 7 on 3 (slot busy 0.78–0.85 at 600 req/s), **row 4 on 1 second** (the one Q burst at 600 req/s that
coincided with E ≫ 0); cells against the truth cell 99.4 %. The Go `sched` runtime wait stays at 0 p50 / 0.03 ms p90.
Reading: `VioMutex` with its default parameter (two holders, 2 ms of work) at 10 % of a 4-core service serialises
too little to produce the row-4 signature; the signal path (Q_mutex ≫ 0 → row 4) was exercised by the one second it
caught. A heavier contention knob (the endpoint's holder count / critical-section length, or a 50 % share) is what
would test row 4 properly; not run (the handoff fixed 10 %).

### 2.5 Confusion matrix (verdict row × twin truth class), served steps only — C.1 rule and §0 rule, all scenarios

| scenario | n | ext → 1 | ext → 2 | ext → 6 | ext → 7 | slow-int → 1 | slow-int → 2 | slow-int → 3 | slow-int → 4 | slow-int → 6 | slow-int → 7 | fast → 1 | fast → 6 | class agreement | cells vs truth cell | E within ±0.15 (slow s) |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| **C.1 rule** |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |
| i mix shift + 1 aggressor (v3) | 147 | 39 | 0 | 1 | 0 | 13 | 0 | 0 | 0 | 16 | 0 | 0 | 78 | 90 % | 90 % | 57 % (69) |
| ii served step past the profile (v2, no aggressor) | 392 | 0 | 2 | 0 | 0 | 0 | 25 | 1 | 0 | 2 | 10 | 0 | 352 | 99 % | 95 % | 8 % (40) |
| iii recommendation + 1 aggressor/core (v3) | 164 | 41 | 0 | 0 | 0 | 83 | 0 | 0 | 0 | 0 | 0 | 0 | 40 | 49 % | 99 % | 97 % (124) |
| iv uniform + stream (v2) | 116 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 116 | 100 % | 100 % | nan % (0) |
| iv-b memwalk30 128 MB + stream (v3) | 246 | 0 | 0 | 0 | 0 | 0 | 2 | 0 | 0 | 7 | 0 | 0 | 237 | 100 % | 100 % | 78 % (9) |
| iv-b' memwalk30 24 MB + stream (v3) | 246 | 0 | 0 | 0 | 0 | 0 | 1 | 1 | 0 | 16 | 0 | 0 | 228 | 100 % | 100 % | 83 % (18) |
| v post-storage + 1 aggressor, pair 1 (v3) | 164 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 164 | 100 % | 100 % | nan % (0) |
| v post-storage + 1 aggressor, pair 2 (v3, hostPID) | 164 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 164 | 100 % | 100 % | nan % (0) |
| v post-storage + 1 aggressor, pair 4 (v3.2, every thread) | 164 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 164 | 100 % | 100 % | nan % (0) |
| vi mutex10, no aggressor (v3) | 168 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 1 | 28 | 3 | 0 | 136 | 100 % | 99 % | 59 % (32) |
| D.4 memwalk30 24 MB + cache aggressor, A1 (v3.2) | 246 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 2 | 0 | 0 | 244 | 100 % | 100 % | 100 % (2) |
| D.4 order control: cache first vs twin second (v3.2) | 205 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 205 | 100 % | 100 % | nan % (0) |
| D.4 memwalk30 128 MB + cache aggressor, A4 (v3.2) | 246 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 7 | 0 | 0 | 239 | 100 % | 100 % | 86 % (7) |
| D.5 VioMutex 40 %, no aggressor (v3.2) | 369 | 0 | 0 | 0 | 0 | 0 | 0 | 6 | 100 | 23 | 1 | 0 | 239 | 98 % | 75 % | 8 % (130) |
| **§0 rule** |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |
| i mix shift + 1 aggressor (v3) | 147 | 0 | 35 | 1 | 4 | 0 | 6 | 0 | 1 | 16 | 6 | 0 | 78 | 73 % | 90 % | 57 % (69) |
| ii served step past the profile (v2, no aggressor) | 392 | 0 | 2 | 0 | 0 | 0 | 25 | 1 | 0 | 2 | 10 | 0 | 352 | 99 % | 95 % | 8 % (40) |
| iii recommendation + 1 aggressor/core (v3) | 164 | 41 | 0 | 0 | 0 | 83 | 0 | 0 | 0 | 0 | 0 | 39 | 1 | 26 % | 99 % | 97 % (124) |
| iv uniform + stream (v2) | 116 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 116 | 100 % | 100 % | nan % (0) |
| iv-b memwalk30 128 MB + stream (v3) | 246 | 0 | 0 | 0 | 0 | 0 | 2 | 0 | 0 | 7 | 0 | 0 | 237 | 100 % | 100 % | 78 % (9) |
| iv-b' memwalk30 24 MB + stream (v3) | 246 | 0 | 0 | 0 | 0 | 0 | 1 | 1 | 0 | 16 | 0 | 0 | 228 | 100 % | 100 % | 83 % (18) |
| v post-storage + 1 aggressor, pair 1 (v3) | 164 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 29 | 135 | 82 % | 100 % | nan % (0) |
| v post-storage + 1 aggressor, pair 2 (v3, hostPID) | 164 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 24 | 140 | 85 % | 100 % | nan % (0) |
| v post-storage + 1 aggressor, pair 4 (v3.2, every thread) | 164 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 143 | 21 | 13 % | 100 % | nan % (0) |
| vi mutex10, no aggressor (v3) | 168 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 1 | 28 | 3 | 0 | 136 | 100 % | 99 % | 59 % (32) |
| D.4 memwalk30 24 MB + cache aggressor, A1 (v3.2) | 246 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 2 | 0 | 0 | 244 | 100 % | 100 % | 100 % (2) |
| D.4 order control: cache first vs twin second (v3.2) | 205 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 205 | 100 % | 100 % | nan % (0) |
| D.4 memwalk30 128 MB + cache aggressor, A4 (v3.2) | 246 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 7 | 0 | 0 | 239 | 100 % | 100 % | 86 % (7) |
| D.5 VioMutex 40 %, no aggressor (v3.2) | 369 | 0 | 0 | 0 | 0 | 0 | 0 | 6 | 100 | 23 | 1 | 0 | 239 | 98 % | 75 % | 8 % (130) |

Columns: n = served seconds with a twin truth; "truth → row" = seconds of that truth class the rule put in that
verdict row (all-zero columns of both rule blocks pruned); "class agreement" maps ext → rows 1/3, slow-int →
2/4/5/6/7, fast → 6, so a slow-int second in row 1 counts against it — under C.1 row 1 is a presence test (aggressor
on the cpuset and E ≫ 0) and the amount is in the cell, which is why iii reads 49 % there and 99 % on cells; "E within
±0.15" is the decomposition error on slow seconds (count in brackets). Source `results/g2/matrix_c1.md` from
`analysis/g2_matrix.py` over the per-scenario `verdict.csv`; figure `results/g2/figG2-5-verdicts.png` (verdict rows
per step beside the twin truth, and E / R_foreign / R_self over time). The Addendum D rows (D.4 × 3, D.5) are the
last four (§2.7, §2.8).

Reading across the fourteen rows (eight scenarios of §2.4, three (v) pairs, four Addendum D runs).
- **Row 1 fires exactly where an aggressor shares the cpuset and the p50 moved**: i (39 of the 40 ext-truth seconds,
  plus 13 slow-int seconds with the hog on the cpuset and E beyond the band) and iii (all 41 ext, all 83 slow-int);
  never on the rows without an aggressor on the cpuset (ii, iv, iv-b, iv-b′, vi, D.4 × 3, D.5: 0 seconds), and not on
  v, where the aggressor is present (φ 0.3–0.8) but the p50 is untouched (row 6; the damage is the tail, §2.4 D).
  Against the §0 rule, C.1 gains the 40 ext seconds of i (the capacity-loss face, R_foreign ≈ 0) and loses the 39 fast
  seconds of iii and the 24–143 of the v pairs that R_foreign ≫ 0 alone had put in row 1 (the E ≈ 0 gate; pair 4
  shows what R_foreign alone does on a thread-per-connection server once every thread is visible: 143 fast seconds).
- **Row 2 (own queue)** carries the overload step of ii (27 of its 41 slow seconds; the other 14 are threshold margins,
  §2.4 C) and single boundary seconds elsewhere. **Row 3** never fires in a sustained way: single seconds in ii, iv-b′
  and D.5 (one-second I_g bursts); no LLC aggressor produced it at either working set, with the stream or the cache
  stressor (§2.4 D, §2.7, §2.8). **Row 4 is demonstrated on D.5**: 100 of the 130 slow seconds of the 40 % VioMutex mix
  (Q 120–280 ms of mutex wait per second), once on vi. **Row 7** on 10 seconds of ii, 3 of vi and 1 of D.5, all
  threshold margins.
- **Cells against the truth cell are 90–100 % on every row except D.5 (75 %)**, where the cell axis calls the service's
  own lock queue "ext" (E = 0.7 against a CPU-work dictionary) and the verdict row corrects it to row 4 — the row that
  shows why the row, not the cell, is the diagnosis. The decomposition on slow seconds is within ±0.15 on 97 % (iii),
  78–100 % (iv-b, iv-b′, D.4), 57–59 % (i, vi) and 8 % on ii and D.5, where the truth baseline is the same service one
  step earlier (ii) or a different mix at the same rate (D.5) and reads ≈ 0 by construction.
- The rows the rule was written for (i, iii, D.5) are separated from the ones it should leave alone (the iv and D.4
  families, vi) and from the own-queue case (ii); its blind spots are the two named in §2.4 D and §2.8: a p50 rule
  cannot see an aggressor whose whole effect is in the tail of a sub-0.1 ms service (v), and on this hardware no LLC
  neighbour slows a memory-bound victim at all — it speeds it up (§2.8).

### 2.6 Addendum D.1–D.3 — (v) made explicit, (vi) as a test, deviation 28 restated

**D.1 (v).** *Which stream rose at p90.* The service dictionary, the twin truth and the "p90 +30–110 %" of §2.4 D are
all on the **processing stream** (blocked segment excluded; `GORDION_LAT=unstamped` is processing on the C++ recorder).
On that stream the hog's p90 effect is 30–70 % in pair 1, 0–19 % in pair 2 and 1–20 % in pair 4 (per request); on the
**full** latency (store call included) it is 9–29 % in pair 4 and the per-request p95 +11–37 %. *How much survives
with blocked time excluded:* at 600–800 req/s 15–20 % of the full p90 rise (+0.25 of +1.48 ms, +0.57 of +3.15 ms), at
200–400 req/s 5–14 % (+0.02–0.07 of +0.38–0.49 ms); the rest is in the blocked segment, i.e. between the store's reply
and the thread's resumption. *Does the store share the node with the aggressor:* no — `post-storage-mongodb` and
`post-storage-memcached` run on node-1, the aggressor on node-3 CPU 1; the store itself is untouched and the blocked
segment grows on the victim's side of the call (the reply's wake-up behind the hog and behind the service's own
threads; R_self + R_foreign per request +0.1–1.0 ms, pair 4). The paper sentence holds in both directions: the
dictionary's verdict on this service is "nothing to explain" on the stream it scores (row 6, cells 100 % on three
pairs), and the per-request rows name the store call as the segment that carries the aggressor's tail effect, with
R_foreign naming the hog.

**D.2 (vi).** Realised lock utilisation = 2 ms hold × VioMutex requests per second: 20.1 / 39.0 / 60.0 req/s at the
three steps → **0.040 / 0.078 / 0.120**, below the 0.3 under which the null is by construction. (vi) was not a test of
row 4; D.5 (40 % share, 0.7 at 875 req/s) is. VioMutex's own p50 at 600 req/s (4.9 ms against a 2 ms floor) is the
service's slot queue at ρ 0.63, not the lock.

**D.3 deviation 28, restated.** While the stage-D queue script was being rewritten on node-0, the running bash read on
into the new text and started a duplicate chain; the chain was killed within minutes but the probe it had started
(`probe.py` + `bpftrace`, cpuset 1,3,5,7) was not, and ran from 05:12 to 06:14 beside the queue's own probe through (v)
pair 1 and both (iv-b′) runs. Effect: a second `sched_switch` program on those CPUs, ≈ 0.005 cores, identical on the twin
and the stressed run of each pair; §2.3's overhead numbers predate it; no result depends on it. Rule adopted: never edit
a queue script in place while it runs (write a new file). **Limitation of I_g on store-backed services:** post-storage's
CPU per request differed by 25–33 % between two unstressed sweeps of the same schedule (c̄ 0.705 vs 0.810 ms; pair 1's
twin at 0.13 cores and its hog run at 0.17 at 200 req/s, pair 2's twin and hog both at 0.17), so I_g read 1.26 in one
pair and 1.00 in the next with the same aggressor. On a service whose per-request work depends on the state of a store
that every sweep changes, I_g needs a twin taken under the same store state (back to back, or a reset between runs);
against a dictionary from another day it is not a signal.

### 2.7 Addendum D.4 / D.5 — the overnight runs scored under the Addendum C rule (queue `queue-g2f.sh`, probe v3.2)

**Conditions (every run).** cpufreq found pinned and left (`intel_cpufreq` passive, `performance`, 2.0 GHz min = max on
every socket-1 core; `<run>/freq.txt`); uncore fixed by the platform at ratio 27 (`MSR 0x620 = 0x1b1b`, read not
written; decision D-9); L3 35 MB per socket; SUT cpuset 1,3,5,7 (k = 4, gate cap 3), aggressor CPUs 9,11,13,15 in
`gordion-aggr`, probe v3.2 (userspace on CPU 9, 0.10 cores: the `@Rthr` print grew with the v3.2 map limit even on
this Go service, see deviation 32), generator on CPUs 17–27.

**A1 — D.4 at 24 MB: `stress-ng --cache 4 --cache-level 3` (LLC capacity with reuse) against the 30 % VioMemWalk mix.**
Twin `results/20261111-071848-g2f-d4-memwalk30-24mb-twin`, aggressor run `…-072625-…-cache` (aggressor cgroup CPU 599 s
over the run = 4 cores busy).

| step | req/s | ρ | served twin / aggr | p50 twin / aggr (ms) | cores busy twin / aggr | I_g twin / aggr (uniform g) | E (aggr vs the twin's dictionary) | truth share | C.1 rows (aggr run) | twin's bias vs the uniform dictionary (G3.1) |
|---|---|---|---|---|---|---|---|---|---|---|
| 0 | 100 | 0.11 | 1.00 / 1.00 | 5.21 / 4.30 | 0.63 / 0.51 | 1.31 / 1.07 | 0.00 | 0.00 | 6 × 41 | +0.07 |
| 1 | 200 | 0.22 | 1.02 / 1.02 | 5.55 / 4.27 | 1.21 / 1.00 | 1.29 / 1.07 | 0.00 | 0.00 | 6 × 41 | +0.11 |
| 2 | 300 | 0.32 | 1.00 / 1.00 | 5.61 / 4.26 | 1.68 / 1.43 | 1.23 / 1.05 | 0.00 | 0.00 | 6 × 41 | +0.09 |
| 3 | 400 | 0.42 | 1.00 / 1.00 | 6.29 / 4.30 | 2.14 / 1.86 | 1.21 / 1.05 | 0.00 | 0.00 | 6 × 41 | +0.07 |
| 4 | 500 | 0.53 | 1.01 / 1.01 | 9.21 / 4.83 | 2.63 / 2.33 | 1.16 / 1.03 | 0.00 | 0.00 | 6 × 41 | +0.18 |
| 5 | 600 | 0.63 | 1.01 / 1.01 | 36.6 / 9.41 | 3.08 / 2.77 | 1.15 / 1.03 | 0.00 | 0.00 | 6 × 39, 2 × 2 | **+0.67** |
| 6 | 700 | 0.65 / 0.73 | 0.88 / 0.99 | 51.3 / 45.1 | 3.14 / 3.17 | 1.14 / 1.00 | — (twin not served) | — | — | +0.76 |

Verdict: row 6 on 244 of 246 served seconds, row 2 on 2; cells 100 %; φ 0 (the aggressor never holds the cpuset);
R_foreign 0; **I_g in the aggressor run is 0.81–0.90 of the twin's**, i.e. the service burned *less* CPU per request
with four LLC-thrashing workers beside it, and its p50 was lower at every step and its knee one step later. **No row 3.**
But this twin is the slowest memwalk run of the night (I_g 1.31 at 100 req/s against 1.07–1.10 in every other 24 MB
run, knee at 600 instead of 700 req/s), and it ran first — as the twin did in iv-b and iv-b′, where the second run was
also the faster one. The D.4 reading therefore waits for the order control (decision D-7, §2.8): until then A1 says
only that the LLC aggressor produced no slowdown that the rule could see, and that the pair's difference has the sign
and size of a run-order effect. The twin's bias against the uniform dictionary is +0.07 to +0.18 below the knee and
+0.67 at 600 req/s (this twin's knee), the opposite sign to iv-b′'s −0.03 to −0.43 (§2.4 D) — the same mix, the same
working set, one hour apart: the G3.1 number at 24 MB is not stable from run to run and §3 reports both.

**A2 — D.5: VioMutex at 40 % of a uniform mix, no aggressor; twin = uniform sweep under the same conditions**
(`results/20261111-073357-g2f-uniform-twin`, 100 → 900 req/s; mutex run `…-074358-g2f-d5-mutex40`, 100 → 1100 req/s;
truth = the uniform twin's p50 at the same step, as in (vi); dictionary = the uniform twin's). Lock hold 2 ms of CPU
under an endpoint-private `sync.Mutex`.

| step | req/s | VioMutex req/s | lock utilisation (2 ms × rate) | VioMutex p50 / p90 (ms) | service p50 (ms), mutex mix / uniform twin | Q = Go `/sync/mutex/wait/total` (ms per s, median) | y | E | C.1 rows (41 s each) |
|---|---|---|---|---|---|---|---|---|---|
| 0 | 100 | 38 | 0.08 | 2.06 / 2.08 | 2.06 / 2.04 | 0 | 0.00 | 0.00 | 6 × 41 |
| 1 | 200 | 81 | 0.16 | 2.05 / 2.48 | 2.05 / 2.28 | 16 | 0.00 | 0.00 | 6 × 41 |
| 2 | 300 | 120 | 0.24 | 2.05 / 3.02 | 2.05 / 2.63 | 27 | 0.00 | 0.00 | 6 × 41 |
| 3 | 400 | 158 | 0.32 | 2.06 / 4.05 | 2.12 / 3.54 | 64 | 0.03 | 0.00 | 6 × 41 |
| 4 | 500 | 198 | 0.40 | 2.50 / 5.61 | 2.84 / 5.24 | 101 | 0.16 | 0.00 | 6 × 40, 3 × 1 |
| 5 | 600 | 240 | 0.48 | 3.66 / 8.03 | 4.46 / 11.4 | 147 | 0.41 | 0.00 | 6 × 39, 4 × 1, 7 × 1 |
| 6 | 700 | 281 | 0.56 | 6.86 / 13.9 | 9.28 / 45.2 | 195 | 0.86 | 0.27 | **4 × 21**, 6 × 19, 3 × 1 |
| 7 | 800 | 319 | 0.64 | 38.8 / 45.1 | 39.1 / 46.3 (twin served 0.85) | 271 | 1.00 | 0.70 | **4 × 39**, 3 × 2 |
| 8 | 900 | 320 (served 0.89) | 0.64 | 38.7 / 45.2 | 39.0 / 37.9 | 280 | 1.00 | 0.71 | **4 × 39**, 3 × 2 |
| 9–10 | 1000–1100 | 358–419 | 0.72–0.84 | 41 / 50 → 1754 / 1768 | — (unserved: 0.91 / collapse) | 682 → 518 000 | — | — | — (no truth) |

**Row 4 is demonstrated.** Where the lock's queue is the latency (steps 6–8: y 0.86–1.00, E 0.27–0.71, Q 195–280 ms
of mutex wait per second against a twin of 0 and a floor of 10), the Addendum C rule reads **row 4 (lock
wait) on 99 of 123 slow seconds**, row 6 on 19 (step 6, E under the ≫ threshold), row 3 on 5 (one-second I_g bursts),
row 7 on 1; φ 0, R_foreign 0, I_g ≈ 1.0 throughout (the lock costs no CPU per request), slot busy 0.87–0.97. The Go
counter is right (no switch to the eBPF futex split needed): Q rises from 16 ms/s at utilisation 0.16 to 101 at 0.40
and 271 at 0.64, and VioMutex's own p50 leaves 2 ms at 0.40, is 3.4× at 0.56 and 19× at 0.64 — `sync.Mutex` under
300 waiters convoys far earlier than an M/D/1 queue at the same utilisation would. Over all 369 served seconds the
truth class is slow-int on 130 and fast on 239, agreement 98.4 % (363; the 6 row-3 seconds count against). **The cell
axis reads "ext" on 91 of those seconds (cells 75.3 %)**: E = (Lat − D[ρ; mix]) ÷ Lat is 0.7 because the dictionary,
built from CPU work, cannot model a lock queue; the cell calls that "not explained", and it is the verdict row that
names it the service's own wait. This is the G4 point in one row: the cell is the symptom, the row is the diagnosis.
No third attempt needed.

### 2.8 D.4 closed — A4 (128 MB), the order control, and what the "faster under a neighbour" effect is

> **Correction (Nov 17, RESULTS-K §6.3).** D.4's speed-up under the cache aggressor was the calibration order, not a
> state of the socket. The synthetic service sizes its memory-walk endpoint once at pod start from the measured time
> per step, and every aggressor run here was deployed with the aggressor already running (95 ns per step against 31–37
> ns in every twin and control), so it did 2.8× fewer steps per request (1.4× at 128 MB). Per step, the aggressor
> slowed the walk in every pair. The hypotheses and controls below stand as recorded; the effect they were testing is
> this calibration.

**A4 — D.4 at 128 MB** (`results/20261111-082638-g2f-d4-memwalk30-128mb-twin` / `…-083452-…-cache`, same conditions as
A1; uncore ratio 27 through both runs, `uncore.tsv`): p50 twin 5.22 / 5.42 / 5.40 / 5.49 / 6.64 / 17.8 / 48.9 ms against
4.30 / 4.27 / 4.27 / 4.33 / 4.86 / 9.30 / 45.0 under the cache aggressor; cores busy 0.53 vs 0.51 at 100 req/s; I_g 1.11–1.13
(twin) vs 1.03–1.07 (aggressor run); served steps identical to 600 req/s. Verdict row 6 on 239 of 246 seconds, row 2 on
7; cells 100 %; φ 0; R_foreign 0; I_g in the aggressor run 0.94 of the twin's. **No row 3 at 128 MB either.**

**Order control (decision D-7; `queue-g2f2.sh`, 08:56–09:17).** The 24 MB pair in reverse order — cache aggressor
first, then a twin, then a second twin — with the pod's placement and huge-page state recorded. Every memwalk30 run of
the day at 24 MB, in time order (p50 per step 100 → 700 req/s; cores busy at 100 req/s; the VioMemWalk endpoint's own
p50 at 100–200 req/s):

| run (24 MB, 30 % VioMemWalk) | neighbour on CPUs 9–15 | p50 per step (ms) | cores busy @ 100 req/s | VioMemWalk p50 (ms) |
|---|---|---|---|---|
| iv-b′ twin, 05:44 | none | 4.42 4.33 4.36 4.45 5.26 10.5 45.9 | 0.53 | 4.69 |
| iv-b′ stream, 05:51 | `--stream` × 4 | 4.10 4.11 4.12 4.18 4.70 8.94 44.5 | 0.52 | 4.22 |
| A1 twin, 07:18 | none | 5.21 5.55 5.61 6.29 9.21 36.6 51.3 | 0.63 | 8.91 |
| A1 cache, 07:26 | `--cache` × 4 | 4.30 4.27 4.26 4.30 4.83 9.41 45.2 | 0.51 | 4.37 |
| **cache first, 08:56** | `--cache` × 4 | 4.30 4.27 4.27 4.31 4.82 9.35 45.1 | 0.51 | 4.37 |
| **twin second, 09:03** | none | 5.22 5.84 5.91 6.80 10.6 51.0 53.2 | 0.65 | 9.54 |
| **twin third, 09:10** | none | 5.22 5.63 5.65 6.34 9.27 38.3 51.6 | 0.63 | 9.01 |

**It is not an order effect.** With a busy neighbour the memory-walk endpoint runs at 4.2–4.4 ms whatever the order
and whatever the neighbour does to the cache (streaming or cache-resident); without one it runs at 4.7 ms (05:44) or
8.9–9.5 ms (07:18, 09:03, 09:10) — two unstressed states, 2× apart, and the three later twins are the slow one. The
run with the aggressor is the *fast* state every time (p50 −18 %, knee one step later, cores busy −20 %, I_g −0.1 to
−0.25), at 24 MB and at 128 MB alike; `thp.txt` shows no huge pages in any run (THP `madvise`, AnonHugePages 0) and
`Mems_allowed` 0–1 for all, so neither THP nor NUMA placement separates the states; the uncore ratio is 27 in every
run that has the trace. Scored against its own twin, the cache-first run reads row 6 on 205 of 205 served seconds,
cells 100 % (and twin-third against twin-second, two unstressed runs back to back, row 6 on 205 of 205, cells 99.5 %:
the twin noise of the verdict is not the issue; the state of the *socket* is).

*Mechanism, as a hypothesis the night's data cannot close:* package idle power management. With CPUs 9–15 and the
rest of socket 1 idle, the package goes into deeper idle states (core C6; on this generation the L3's ways shrink and
DRAM enters power-down) and every walk from the lightly loaded victim pays the wake-up and refill; four busy
neighbours keep the package in C0. It fits a slowdown that is the same at 24 MB (LLC) and 128 MB (DRAM), a victim
that gets *faster* under any kind of neighbour, a fast unstressed state when something else was awake on the socket
(05:44, while the stray probe of deviation 28 ran unpinned), and the +0.035 ms per request that every CPU-spin
endpoint pays under the neighbour (§2.4 D), which is the real, small cache cost. What it would take (not run — the
addendum fixed one attempt): (a) a pure CPU spinner (`stress-ng --cpu 4`, no memory traffic) on CPUs 9–15 as the
neighbour — if the victim speeds up the same way, the mechanism is the idle state, not the cache; (b) a twin with deep
idle states disabled on socket 1 (`cpupower idle-set -D 1`, or `intel_idle.max_cstate=1`); (c) C-state residency
(`/sys/devices/system/cpu/cpuN/cpuidle/state*/time`) sampled through a twin and an aggressor run. About 30 minutes of
cluster time; listed in §5 for the decision.

**The two controls (closing task 1; `queue-g2f3.sh`, 22:12–22:34 UTC, memwalk30 at 24 MB, same conditions; C-state
residency of the SUT and neighbour cores sampled once per second into `cstate.tsv`; found and left: C3 and C6 disabled
on every CPU since before Phase G, C1E the deepest reachable state).**

| run | neighbour on CPUs 9–15 | idle states | p50 per step (ms) | cores busy @ 100 req/s | VioMemWalk p50 (ms) | SUT cores in C1E at 100 → 700 req/s | neighbour cores in C1E |
|---|---|---|---|---|---|---|---|
| twin, as found | none | C1E on | 5.21 5.42 5.44 5.99 8.50 26.8 50.3 | 0.62 | 8.42 | 83 → 20 % | 99 % |
| **pure CPU spinner** (`stress-ng --cpu 4`, no memory traffic) | 4 spinners | C1E on | 5.22 5.64 5.83 6.57 9.99 47.6 52.2 | 0.63 | 8.89 | 83 → 20 % | 0 % |
| **twin, C1E disabled** on all 28 socket-1 CPUs | none | C1 only | 5.23 5.64 5.76 6.47 9.41 43.0 51.8 | 0.63 | 9.02 | 0 % (C1 83 → 21 %) | 0 % |

**Both idle-state hypotheses are ruled out.** A neighbour that only burns CPU leaves the victim in the slow state
(VioMemWalk 8.9 ms, p50 and knee as the twins'), and disabling the last reachable idle state on the whole socket
changes nothing (9.0 ms). The fast state (4.2–4.4 ms, §2.8 table) appeared only under the two neighbours that
generate *memory traffic* — `--stream` and `--cache` — at 24 MB and 128 MB alike, with the uncore fixed and the
cores' idle states irrelevant. What is left is the memory side of the socket (DRAM or memory-controller power
management that a traffic-generating neighbour keeps out of its low-power mode, or a prefetcher / page-policy
state the neighbour's traffic trains), which none of this night's instruments observe. Cause unknown; not pursued
(the addendum fixed one attempt, and the closing task two controls).

**D.4, final (the addendum's words).** Row 3 is a stated limitation: *I_g flags the memory-bound mix (1.1–1.5 against
the uniform profile, rising with the share) but is unsized against the latency bias, and there is no positive LLC
case on this hardware* — a cache-resident aggressor on the sibling cores does not slow a victim whose working set is in
the LLC (24 MB) or past it (128 MB); it makes it faster. **Why it makes it faster: ruled out (package idle states, run
order, uncore clock, THP, NUMA placement); cause unknown**, narrowed to the socket's memory side by the controls above.
The §3 bias numbers are the self-interference result.

### 2.9 Per-window sensitivity — the Addendum C rule on every 100 ms window, no smoothing (post-acceptance, analysis only)

**What was done.** Every recorder window of the fourteen scenarios (100 ms nominal; the window's measured length `dt`
is used, 100–105 ms on the Go services, 1–1000 ms jitter on the Thrift post-storage recorder, median 100 ms
everywhere) is scored with the same rule and the same thresholds as the per-second verdict (`verdict_thresholds.json`
of each scenario: the twin's per-step IQR bands for E, I_g, Q, R; φ > 0.05; slot busy ≥ 0.9), with **no Algorithm-1
smoothing**: Lat_w is the pooled p50 of the window's own server-side Lat_e samples (processing stream), D_mix,w the
dictionary at the window's own ρ and arrival mix, E_w = (Lat_w − D_mix,w) ÷ Lat_w, y_w from the raw window; the probe's
per-window sums are scaled to per-second rates, φ_w is the aggressor cgroup's share of the cpuset in that window, I_g
from the window's `cpu.stat` and λ_w c̄_w g(ρ_w). The truth and the per-second verdict are held at the second the window
falls in; only windows of the served seconds of §2.5 are scored (same denominators). "row = second's row" is the share
of windows whose verdict row equals the row of their second; "flips per s (windows)" counts window-to-window row
changes between consecutive scored windows divided by the scored seconds, "flips per s (seconds)" the same for the
per-second verdict. Script `analysis/g2_window.py` (per scenario, `g2/window_verdict.csv` + `window_summary.json`),
table `analysis/g2_window_table.py` → `results/g2/window_table.{md,csv}`; figure `analysis/g2_window_fig.py` →
`results/g2/figG2-9-window-strips.png` (scenario i: the truth strip, the per-second strip of Fig. G2-5 and the
per-window strip beneath it, with E per second and per window).

| scenario | windows | seconds | median dt (ms) | row = second's row | flips per s (windows) | flips per s (seconds) | cells vs truth, windows | cells vs truth, seconds | class agreement, windows | class agreement, seconds | rows, windows (1/2/3/4/6/7) | rows, seconds (1/2/3/4/6/7) |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| i mix shift + 1 aggressor (v3) | 1469 | 147 | 100 | 77.1 % | 3.03 | 0.075 | 78.1 % | 89.8 % | 79.4 % | 90.5 % | 432/80/12/0/883/62 | 52/0/0/0/95/0 |
| ii served step past the profile (v2, no aggressor) | 3820 | 392 | 100 | 90.1 % | 1.25 | 0.048 | 81.3 % | 94.8 % | 95.4 % | 99.4 % | 0/152/49/0/3452/167 | 0/27/1/0/354/10 |
| iii recommendation + 1 aggressor/core (v3) | 1600 | 164 | 100 | 89.9 % | 1.14 | 0.012 | 82.8 % | 99.4 % | 40.2 % | 49.4 % | 1354/1/0/0/244/1 | 124/0/0/0/40/0 |
| iv uniform + stream (v2) | 1150 | 116 | 100 | 92.9 % | 1.27 | 0.000 | 90.3 % | 100.0 % | 92.9 % | 100.0 % | 0/7/6/0/1068/69 | 0/0/0/0/116/0 |
| iv-b memwalk30 128 MB + stream (v3) | 2401 | 246 | 100 | 95.7 % | 0.67 | 0.008 | 91.7 % | 100.0 % | 96.5 % | 100.0 % | 0/56/16/0/2298/31 | 0/2/0/0/244/0 |
| iv-b' memwalk30 24 MB + stream (v3) | 2401 | 246 | 100 | 92.4 % | 1.13 | 0.008 | 88.9 % | 100.0 % | 94.7 % | 99.7 % | 0/89/32/0/2218/62 | 0/1/1/0/244/0 |
| v post-storage + 1 aggressor, pair 1 (v3) | 1600 | 164 | 100 | 99.9 % | 0.01 | 0.000 | 86.8 % | 100.0 % | 99.9 % | 100.0 % | 1/0/0/0/1599/0 | 0/0/0/0/164/0 |
| v post-storage + 1 aggressor, pair 2 (v3, hostPID) | 1600 | 164 | 100 | 99.7 % | 0.05 | 0.000 | 95.5 % | 100.0 % | 99.7 % | 100.0 % | 5/0/0/0/1595/0 | 0/0/0/0/164/0 |
| v post-storage + 1 aggressor, pair 4 (v3.2, every thread) | 1601 | 164 | 100 | 99.4 % | 0.10 | 0.000 | 95.3 % | 100.0 % | 99.4 % | 100.0 % | 9/0/0/0/1592/0 | 0/0/0/0/164/0 |
| vi mutex10, no aggressor (v3) | 1650 | 168 | 100 | 89.3 % | 1.49 | 0.036 | 79.9 % | 99.4 % | 95.0 % | 100.0 % | 0/52/22/11/1498/67 | 0/0/0/1/164/3 |
| D.4 memwalk30 24 MB + cache aggressor, A1 (v3.2) | 2401 | 246 | 100 | 99.9 % | 0.02 | 0.000 | 95.7 % | 100.0 % | 99.9 % | 100.0 % | 0/1/0/0/2398/2 | 0/0/0/0/246/0 |
| D.4 order control: cache first vs twin second (v3.2) | 2000 | 205 | 100 | 99.7 % | 0.06 | 0.000 | 99.2 % | 100.0 % | 99.7 % | 100.0 % | 0/2/2/0/1994/2 | 0/0/0/0/205/0 |
| D.4 memwalk30 128 MB + cache aggressor, A4 (v3.2) | 2401 | 246 | 100 | 96.8 % | 0.51 | 0.000 | 92.6 % | 100.0 % | 97.4 % | 100.0 % | 0/41/8/0/2323/29 | 0/0/0/0/246/0 |
| D.5 VioMutex 40 %, no aggressor (v3.2) | 3601 | 369 | 100 | 78.1 % | 3.03 | 0.054 | 65.5 % | 75.3 % | 90.2 % | 99.1 % | 0/109/220/625/2442/205 | 0/0/6/100/262/1 |

Rows columns: windows (or seconds) in rows 1/2/3/4/6/7 (row 5 is not in the C.1 order).

**Reading.**
- **The quiet rows do not move.** On the three (v) pairs and the three D.4 runs the window verdict is the second's
  verdict on 99.4–99.9 % of the windows and flips 0.01–0.10 times per second: E ≈ 0 and y < 0.5 hold in every window,
  the "nothing to explain" gate absorbs the window noise before any threshold is reached. The 1–9 row-1 windows on the
  (v) pairs are single 100 ms windows where the pooled p50 of a sub-0.1 ms service jumped over E_far while the
  aggressor held the cpuset — the tail effect of §2.4 D seen once per minute at the window scale.
- **Where E sits near its band, the raw window verdict flips 0.5–3 times per second**, against 0–0.075 for the
  per-second verdict (a factor 25–95 where the per-second verdict flips at all; it never flips on iv and the D.4 runs): i 3.0, D.5 3.0, vi 1.5, iv 1.3, ii 1.3, iv-b′ 1.1, iii 1.1, iv-b 0.7, A4 0.5
  per second. The window E carries the pooled-p50 noise of a few dozen samples (33–47 per window on i, 100–600 on iii): on scenario i it spans 0–0.8 around a
  per-second value of 0.2 (Fig. G2-9, bottom), and every crossing of E_far (0.37 there) sends the window into rows
  2/3/7 (then a single 100 ms sample of I_g, Q or slot busy decides which) or, with the hog's φ_w over 0.05, into row
  1. Agreement with the second is 77–93 % on these rows; the flips are almost all between row 6 and rows 2/3/7 (ii:
  of the 3446 windows of row-6 seconds, 94 go to row 7, 23 to row 2, 22 to row 3; iv-b′: of 2383, 62 / 83 / 26), i.e.
  threshold margins, not changes of diagnosis.
- **Row 1 is the robust row.** On iii, 1201 of the 1210 windows of row-1 seconds are row 1 (99 %), and 153 windows of
  row-6 seconds read row 1 (the window E over the band while the smoothed E is under: φ_w is 0.33–0.99 (p10–p90) and
  under 0.05 in 2 of 1600 windows, so the E gate alone decides). On i, 320 of the 520 windows of row-1 seconds stay in row 1 (62 %); the rest
  go to row 6 (71: E_w under the band), row 2 (77) and row 7 (40) — in those windows the single hog's share of the
  four-CPU cpuset over 100 ms fell under 0.05 (the hog is time-sliced against the service's own threads), which the
  per-second φ never does; and 112 windows of row-6 seconds read row 1 during the ramp (70–110 s), where the truth is
  already ext and the smoothed E lags it. At the second the occupancy signal is a presence test; at the window it is
  a sampled one.
- **Row 4 on D.5 keeps its majority but not its edges**: 485 of the 1000 windows of row-4 seconds stay in row 4; 174
  go to row 3 (a 100 ms I_g over the band while Q_w is under its own), 131 to row 7, 111 to row 6, 99 to row 2 —
  the mutex wait is bursty at 100 ms (Q_w 0–760 ms/s, p5–p95, around a per-second 195–280), and the rule's order reads the burst
  that is present. 124 windows of row-6 seconds read row 4 for the same reason.
- **Cells and class agreement at the window level** lose 5–15 points against the second (cells 65–99 % vs 75–100 %;
  class 79 / 90 / 40 % on i / D.5 / iii vs 90 / 99 / 49 %, iii's class figure being low by construction, §2.5). The
  decomposition at 100 ms is a weaker number: E_w within ±0.15 of the truth share on 49 / 86 / 8 % of the slow windows
  of i / iii / D.5 against 57 / 97 / 8 % of the slow seconds (Fig. G2-9 shows the spread on i).
- **Conclusion.** The verdict is a per-second instrument with a 5 s memory: its thresholds are the twin's per-second
  IQRs and its E is the smoothed residual share. Run on raw 100 ms windows with those thresholds it keeps the
  majority verdict of every second on the rows that carry a diagnosis (row 1: 62–99 %; row 4: 49 %; row 6: 85–100 %)
  and flips at the edges 25–95 times more often. A window-rate instrument would need its own bands (the twin's
  per-window IQRs, roughly 3× wider for E and I_g) — not derived here, not asked; nothing in §2.5 depends on it.

### 2.10 Old-method columns on the fourteen-row matrix — D(λ) and the Phase F scorer beside the verdict rules (post-acceptance, analysis only)

**What was done.** On the same served seconds as §2.5 and against the same truth cell: the class agreement of the
occupancy (C.1) rule and of the §0 rule (from `verdict.csv`); the Phase G cell (slow × ext with E from D[ρ; mix], no
guard); **D(λ)** — Gordion Algorithm 1's service-level curve, D indexed by the second's arrival rate on the dictionary
run's own sweep, Lat and %ext smoothed with the same 5 s causal Gaussian, `ext_old` / `cell_old` of each scenario's
`b4g2/b4_windows.csv` — as cells vs truth, plus the share of ext-truth and of int-truth seconds it calls ext and the
seconds whose λ lies past the curve's last profiled bin (there `np.interp` holds D at the last value: the second is
scored, with the curve's end); and the **Phase F scorer** — D[ρ; mix] with the ρ guard and the wait guard, `cell_mix`
of the same file — as strict (abstained counts as wrong), when it speaks, and abstained with the guard split (ρ /
wait). Script `analysis/g2_oldcols.py` → `results/g2/oldcols.{md,csv}`.

| scenario | n | occupancy rule, class | §0 rule, class | Phase G cell (no guard) | D(λ) cell | D(λ) ext on ext-truth s | D(λ) ext on int-truth s | λ past the old curve | Phase F strict | Phase F when it speaks | Phase F abstained (ρ / wait) |
|---|---|---|---|---|---|---|---|---|---|---|---|
| i mix shift + 1 aggressor (v3) | 147 | 90 % | 73 % | 90 % | 80 % | 100 % (40) | 28 % (107) | 0 of 147 | 63 % | 100 % | 37 % (0 / 54) |
| ii served step past the profile (v2, no aggressor) | 392 | 99 % | 99 % | 95 % | 93 % | 100 % (2) | 7 % (390) | 53 of 392 (λ > 503) | 87 % | 100 % | 13 % (47 / 5) |
| iii recommendation + 1 aggressor/core (v3) | 164 | 49 % | 26 % | 99 % | 98 % | 100 % (41) | 2 % (123) | 0 of 164 | 75 % | 100 % | 25 % (0 / 41) |
| iv uniform + stream (v2) | 116 | 100 % | 100 % | 100 % | 100 % | — (0) | 0 % (116) | 0 of 116 | 100 % | 100 % | 0 % (0 / 0) |
| iv-b memwalk30 128 MB + stream (v3) | 246 | 100 % | 100 % | 100 % | 100 % | — (0) | 0 % (246) | 0 of 246 | 96 % | 100 % | 4 % (10 / 1) |
| iv-b' memwalk30 24 MB + stream (v3) | 246 | 100 % | 100 % | 100 % | 100 % | — (0) | 0 % (246) | 0 of 246 | 96 % | 100 % | 4 % (8 / 3) |
| v post-storage + 1 aggressor, pair 1 (v3) | 164 | 100 % | 82 % | 100 % | 100 % | — (0) | 0 % (164) | 0 of 164 | 100 % | 100 % | 0 % (0 / 0) |
| v post-storage + 1 aggressor, pair 2 (v3, hostPID) | 164 | 100 % | 85 % | 100 % | 99 % | — (0) | 1 % (164) | 0 of 164 | 100 % | 100 % | 0 % (0 / 0) |
| v post-storage + 1 aggressor, pair 4 (v3.2, every thread) | 164 | 100 % | 13 % | 100 % | 100 % | — (0) | 0 % (164) | 0 of 164 | 100 % | 100 % | 0 % (0 / 0) |
| vi mutex10, no aggressor (v3) | 168 | 100 % | 100 % | 99 % | 100 % | — (0) | 0 % (168) | 0 of 168 | 99 % | 100 % | 1 % (0 / 1) |
| D.4 memwalk30 24 MB + cache aggressor, A1 (v3.2) | 246 | 100 % | 100 % | 100 % | 100 % | — (0) | 0 % (246) | 1 of 246 (λ > 651) | 97 % | 100 % | 3 % (8 / 0) |
| D.4 order control: cache first vs twin second (v3.2) | 205 | 100 % | 100 % | 100 % | 100 % | — (0) | 0 % (205) | 0 of 205 | 98 % | 100 % | 2 % (5 / 0) |
| D.4 memwalk30 128 MB + cache aggressor, A4 (v3.2) | 246 | 100 % | 100 % | 100 % | 100 % | — (0) | 0 % (246) | 0 of 246 | 96 % | 100 % | 4 % (10 / 0) |
| D.5 VioMutex 40 %, no aggressor (v3.2) | 369 | 98 % | 98 % | 75 % | 98 % | — (0) | 2 % (369) | 0 of 369 | 76 % | 100 % | 24 % (0 / 89) |

**Reading.**
- **D(λ) equals the Phase G cell on the eleven rows where the scored mix is the dictionary's own mix** (iv, iv-b,
  iv-b′, the three (v) pairs, vi, the three D.4 runs: 99–100 % both; those seconds are fast/int almost everywhere and
  any curve that is not far above the measured p50 reads int) and on iii (98 vs 99 %). It differs where the mix moves
  or the rate leaves the profile: **i 80 % vs 90 %** — D(λ) calls 28 % of the int-truth seconds "ext" (the heavier mix
  of the ramp sits above the uniform curve at the same λ: the Phase E finding, restated on the G2 rows) while catching
  all 40 ext-truth seconds; **ii 93 % vs 95 %** with 53 of its 392 seconds at λ > 503 req/s, past the truncated
  profile's last bin — the scenario built to be past the profile is the one where D(λ) **cannot be scored as designed**:
  the curve has no value there and the column holds its end value (27 of the 390 int-truth seconds then read ext). On
  **D.5 D(λ) reads 98 % against the Phase G cell's 75 %, by construction, not by diagnosis**: D.5's truth is the
  uniform service at the same rate, and D(λ) is that same uniform sweep indexed by rate, so the column restates the
  truth's own baseline; it calls the lock queue "int" for the reason the truth does and would carry no information on
  a run whose baseline were not available — the dictionary at the mix's own ρ is what produces E = 0.7 there, and the
  verdict row 4 is what resolves it (§2.7). One marginal second on A1 (λ > 651). Every other served second had a
  D(λ) value; no scenario was dropped from the column.
- **The Phase F scorer speaks correctly and abstains on the seconds that matter**: when it speaks, 100 % on every row;
  strict 63–100 %; abstained 37 % on i (54 seconds, all the wait guard: the hog's seconds), 25 % on iii (41, wait:
  the aggressor's seconds), 24 % on D.5 (89, wait: the lock-wait seconds), 13 % on ii (47 ρ-guard seconds of the step
  past the profile + 5 wait), 1–4 % on the memwalk30 rows (ρ guard at the top step, 5–10 seconds) and vi (1), 0 on the
  (v) pairs (floor-only service, wait guard off) and iv. The guard removes exactly the seconds with the signal — the §4
  point (the verdict replaces the guard with no row dropping) seen on the G2 rows: on those abstained seconds the
  occupancy rule reads row 1 (i, iii) or row 4 (D.5) with the cell 90–100 % (§2.5).
- **Order of the columns on the rows with a diagnosis** (i / iii / D.5): occupancy rule 90 / 49 / 98 % class (49 on iii
  is the presence-test reading of §2.5, cells 99 %), §0 rule 73 / 26 / 98 %, Phase G cell 90 / 99 / 75 %, D(λ) cell
  80 / 98 / 98 %, Phase F strict 63 / 75 / 76 % — the verdict row with the occupancy signal is the only column that
  is both right on the ext seconds of i and iii and names the lock queue on D.5; D(λ) is right where the mix is the
  profile's and wrong by 10 points where it is not; the Phase F guard is never wrong and never speaks where it would
  have to.

### 2.11 The old methods per 100 ms window — D(λ) and the Phase F scorer on the windows of §2.9 (post-acceptance, analysis only)

**What was done.** On the windows, truth and conventions of §2.9 (raw pooled-p50 Lat_w, λ_w = arrivals / dt, y_w against
the old curve's floor step, E_w from D[ρ; mix], truth cell held at the second): **D(λ)** per window — D at λ_w on the
dictionary run's own curve (`np.interp`, D held past the last bin), `ext_old` = clamp01((Lat_w − D) / Lat_w), the cell
slow × ext at the §2.10 thresholds; the **Phase F scorer** per window — slow × (n-a if the ρ guard or the wait guard
fires on the raw window, else ext from E_w), reported strict / when it speaks / abstained with the guard split. Beside
each: the per-second value of §2.10 (`cell_old` / `cell_mix` of `b4g2/b4_windows.csv`, same seconds, unweighted), the
window-vs-second agreement of each method (window cell = the second's cell, n-a counted as a value), the windows whose
λ_w lies past the curve, and the two guard discordances: windows the guard stops inside seconds it let speak, and windows
it lets speak inside seconds it stopped. Script `analysis/g2_oldcols_window.py` → `results/g2/oldcols_window.{md,csv}`.

| scenario | windows | seconds | D(λ) cell, windows | D(λ) cell, seconds | D(λ) window = second | λ past the old curve, windows | Phase F strict, windows | Phase F strict, seconds | Phase F when it speaks, windows | Phase F when it speaks, seconds | Phase F abstained, windows (ρ / wait) | Phase F abstained, seconds (ρ / wait) | Phase F window = second | guarded windows of unguarded seconds (ρ / wait) | unguarded windows of guarded seconds |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| i mix shift + 1 aggressor (v3) | 1469 | 147 | 72 % | 80 % | 86 % | 0 of 1469 | 52 % | 63 % | 80 % | 100 % | 35 % (1 / 508) | 37 % (0 / 54) | 81 % | 43 (0 / 43) | 74 of 540 |
| ii served step past the profile (v2, no aggressor) | 3820 | 392 | 74 % | 93 % | 77 % | 572 of 3820 (λ > 503) | 76 % | 87 % | 92 % | 100 % | 18 % (472 / 198) | 13 % (47 / 5) | 82 % | 204 (25 / 179) | 44 of 510 |
| iii recommendation + 1 aggressor/core (v3) | 1600 | 164 | 82 % | 98 % | 82 % | 0 of 1600 | 59 % | 75 % | 79 % | 100 % | 25 % (0 / 407) | 25 % (0 / 41) | 83 % | 26 (0 / 26) | 19 of 400 |
| iv uniform + stream (v2) | 1150 | 116 | 91 % | 100 % | 91 % | 0 of 1150 | 90 % | 100 % | 91 % | 100 % | 1 % (0 / 10) | 0 % (0 / 0) | 90 % | 10 (0 / 10) | 0 of 0 |
| iv-b memwalk30 128 MB + stream (v3) | 2401 | 246 | 93 % | 100 % | 93 % | 95 of 2401 (λ > 658) | 89 % | 96 % | 96 % | 100 % | 7 % (99 / 75) | 4 % (10 / 1) | 91 % | 100 (28 / 72) | 28 of 102 |
| iv-b' memwalk30 24 MB + stream (v3) | 2401 | 246 | 90 % | 100 % | 90 % | 55 of 2401 (λ > 686) | 86 % | 96 % | 95 % | 100 % | 10 % (105 / 132) | 4 % (8 / 3) | 88 % | 154 (33 / 121) | 25 of 108 |
| v post-storage + 1 aggressor, pair 1 (v3) | 1600 | 164 | 87 % | 100 % | 87 % | 0 of 1600 | 87 % | 100 % | 87 % | 100 % | 0 % (0 / 0) | 0 % (0 / 0) | 87 % | 0 (0 / 0) | 0 of 0 |
| v post-storage + 1 aggressor, pair 2 (v3, hostPID) | 1600 | 164 | 86 % | 99 % | 86 % | 3 of 1600 (λ > 1124) | 95 % | 100 % | 96 % | 100 % | 0 % (4 / 0) | 0 % (0 / 0) | 95 % | 4 (4 / 0) | 0 of 0 |
| v post-storage + 1 aggressor, pair 4 (v3.2, every thread) | 1601 | 164 | 85 % | 100 % | 85 % | 0 of 1601 | 95 % | 100 % | 95 % | 100 % | 0 % (2 / 0) | 0 % (0 / 0) | 95 % | 2 (2 / 0) | 0 of 0 |
| vi mutex10, no aggressor (v3) | 1650 | 168 | 81 % | 100 % | 81 % | 0 of 1650 | 78 % | 99 % | 83 % | 100 % | 6 % (4 / 99) | 1 % (0 / 1) | 78 % | 96 (2 / 94) | 3 of 10 |
| D.4 memwalk30 24 MB + cache aggressor, A1 (v3.2) | 2401 | 246 | 96 % | 100 % | 96 % | 105 of 2401 (λ > 651) | 93 % | 97 % | 97 % | 100 % | 4 % (101 / 0) | 3 % (8 / 0) | 94 % | 41 (41 / 0) | 19 of 79 |
| D.4 order control: cache first vs twin second (v3.2) | 2000 | 205 | 100 % | 100 % | 100 % | 21 of 2000 (λ > 620) | 96 % | 98 % | 100 % | 100 % | 4 % (59 / 17) | 2 % (5 / 0) | 97 % | 39 (24 / 15) | 10 of 47 |
| D.4 memwalk30 128 MB + cache aggressor, A4 (v3.2) | 2401 | 246 | 93 % | 100 % | 93 % | 102 of 2401 (λ > 655) | 90 % | 96 % | 96 % | 100 % | 6 % (107 / 42) | 4 % (10 / 0) | 91 % | 77 (37 / 40) | 28 of 100 |
| D.5 VioMutex 40 %, no aggressor (v3.2) | 3601 | 369 | 90 % | 98 % | 89 % | 22 of 3601 (λ > 943) | 65 % | 76 % | 87 % | 100 % | 25 % (6 / 901) | 24 % (0 / 89) | 88 % | 73 (0 / 73) | 37 of 871 |

**Reading.** At window level D(λ) reads 72–100 % against 80–100 % per second and agrees with its own per-second cell on
77–100 % of the windows; the loss is largest where the per-second column was error-free (vi 81 vs 100, the (v) pairs
85–87 vs 99–100, iv 91 vs 100): with no 5 s smoothing the window's y and %ext carry the pooled-p50 noise of a few
dozen samples around the floor step, so the quiet rows pick up fast/slow and int/ext flips that no second shows, and
λ_w, a 100 ms arrival count, runs past the curve's last bin on 1–4 % of the windows of the memwalk30 rows and 15 % of
ii's (572 of 3820, the clamped second's windows plus the count noise) where the per-second λ did not (0–1 and 53). The
Phase F scorer loses its one property: when it speaks it is 100 % on every row per second and 79–100 % per window (i
80, iii 79, vi 83, D.5 87, the (v) pairs 87–96), strict 52–96 % against 63–100 %, and it agrees with its own per-second
cell on 78–97 % of the windows. **The wait guard abstains more at window level than per second on every row where
the per-second guard was quiet or nearly so** — ii 18 vs 13 %, vi 6 vs 1, iv-b′ 10 vs 4, iv-b 7 vs 4, A4 6 vs 4, the
order control 4 vs 2, iv 1 vs 0 — and about the same on the three rows where it fired (i 35 vs 37, iii 25 / 25, D.5 25
vs 24). The reason is the statistic it tests: per second the guard tests the 5 s-smoothed Lat the cell is labelled from
(§2.10, the Nov 3 revision of `b4_compare.py`), per window it tests the raw p50 of the window, and the windows it stops
inside unguarded seconds sit 1.3–4.7× (p10–p90, median 2.3–2.6 on every row) above the smoothed Lat of their second:
single-window spikes (10–180 windows per row, 43 on i, 179 on ii, 94 on vi, 121 on iv-b′, 73 on D.5) that the
smoothing removes before the per-second guard sees them. On the rows with a real wait signal the two errors cancel: the
window guard also lets 4–14 % of the windows of guarded seconds speak (74 of 540 on i, 19 of 400 on iii, 37 of 871 on
D.5, the quiet windows inside a slow second), so the abstention share hardly moves while the set of windows it covers
differs on 3–8 % of them — which is where the Phase F scorer's 100 % when it speaks goes at window level. The ρ guard
is not window-sensitive in the same way (ρ_w is already the ±0.5 s centred mean): its window counts track the seconds'
(ii 472 windows for 47 seconds, the memwalk30 rows 59–107 for 5–10) with 24–41 windows stopped at the edges of unguarded seconds and 10–28 let through inside guarded ones.
Conclusion as §2.9's: the old methods are per-second instruments too, and the guard that is never wrong per second is
wrong on 1 window in 5 of the rows it speaks on at 100 ms.

## 3. G3 — self-interference: the uniform dictionary on memory-bound mixes, both working sets (clnode170, 2.0 GHz, uncore 2.7 GHz)

**3.1 G3.1 — the 30 % VioMemWalk mix against the uniform dictionary, per working set.** Bias = (p50 − D[ρ; mix]) ÷ p50
per step (100 → 700 req/s, ρ 0.11 → 0.65–0.73); D from the session's own uniform sweep (`g2f-uniform-twin`, probe v3.2;
the earlier twins re-scored against it in `b4g3u/`); I_g with g from the same sweep.

| run | working set | socket state (§2.8) | bias per step, ρ 0.11 / 0.22 / 0.32 / 0.42 / 0.53 / 0.63 / knee+ | I_g at 100 req/s → 600 req/s |
|---|---|---|---|---|
| iv-b twin, 04:50 | 128 MB | fast | −0.04 / −0.18 / −0.18 / −0.21 / −0.34 / +0.03 / **+0.67** | 1.08 → 1.08 |
| A4 twin, 08:26 | 128 MB | slow | +0.02 / −0.11 / −0.14 / −0.16 / −0.27 / +0.27 / **+0.68** | 1.11 → 1.10 |
| iv-b′ twin, 05:44 | 24 MB | fast | −0.18 / −0.33 / −0.44 / −0.45 / −0.65 / −0.26 / **+0.65** | 1.10 → 1.04 |
| A1 twin, 07:18 | 24 MB | slow | +0.07 / +0.11 / +0.09 / +0.07 / +0.18 / **+0.67** / +0.76 | 1.31 → 1.15 |

**The stated-limit numbers.** *128 MB (past the LLC):* the uniform dictionary is within ±0.15 up to ρ 0.22 and
over-predicts by 0.18–0.34 at ρ 0.32–0.53 (the walk's real floor is below its 4 ms configured cost plus the uniform
queue at that ρ), then under-predicts by **+0.67–0.68 at the step past the mix's knee** (700 req/s, ρ 0.68–0.73,
served 0.91–0.99) — the number quoted in §2.4 D (+0.69) reproduced on a second day-time pair. *24 MB (in the LLC):*
the sign below the knee depends on the socket state of §2.8 — −0.18 to −0.65 in the fast state, +0.07 to +0.18 in the
slow one — and at the knee it is **+0.65–0.76** in both. The bias at the knee is the one number that does not move:
every memwalk mix saturates at ρ 0.63–0.66 instead of the uniform's 0.72, because the memwalk endpoint burns more CPU
per request than its configured cost (I_g 1.08–1.31 at low load) and the dictionary, indexed by configured ρ, places
the knee where the uniform profile has it.

**3.2 G3.2 — the self-interference curve (A3: 10 / 20 / 30 / 40 / 50 % VioMemWalk at 24 MB, one session, slow socket
state).** Figure `results/g2/figG3-self-interference.png` (left: bias per step per share; right: I_g per step per
share; the 128 MB twins and the fast-state 24 MB twin dashed / grey for comparison).

| VioMemWalk share | bias per step, ρ 0.10 / 0.22 / 0.32 / 0.42 / 0.53 / 0.64 / 0.66 | I_g per step |
|---|---|---|
| 10 % | +0.01 / +0.01 / +0.13 / +0.23 / +0.25 / **+0.52** / +0.71 | 1.19 / 1.09 / 1.10 / 1.11 / 1.08 / 1.07 / 1.04 |
| 20 % | +0.03 / +0.04 / +0.15 / +0.22 / +0.29 / **+0.72** / +0.74 | 1.27 / 1.17 / 1.16 / 1.15 / 1.11 / 1.10 / 1.09 |
| 30 % (A1 twin) | −0.02 / −0.05 / −0.06 / −0.06 / +0.07 / **+0.65** / +0.74 | 1.34 / 1.24 / 1.20 / 1.18 / 1.14 / 1.12 / 1.12 |
| 40 % | −0.42 / −0.61 / −0.94 / −0.66 / −0.37 / **+0.63** / +0.68 | 1.42 / 1.29 / 1.25 / 1.21 / 1.16 / 1.14 / 1.14 |
| 50 % | −0.53 / −0.89 / −1.05 / −0.96 / −0.43 / **+0.68** / +0.67 | 1.52 / 1.34 / 1.28 / 1.23 / 1.19 / 1.15 / 1.16 |

**Reading.** (a) *Below the knee the bias is not monotonic in the share.* At 10–20 % the dictionary is right at low
load and under-predicts by 0.13–0.29 from ρ 0.32 (the memwalk endpoint's slow-state floor of 9 ms sits above the
dictionary's 4 ms for it); at 30 % it is within ±0.07; at 40–50 % it over-predicts by 0.4–1.0, because the mixture
median moves onto the memwalk endpoint and the dictionary's mixture F̄ (from the uniform sweep's per-endpoint floors
plus the configured 4 ms) lands 2–5 ms above the observed p50. The mix's own c̄ also falls with the share (4.3 →
3.4 ms), so the same request rate is a lower ρ. (b) *At the knee the bias is +0.52 to +0.76 for every share* — the
one stable number of G3: any memory-bound share moves the knee from ρ 0.72 to 0.63–0.66. (c) *I_g is the monotone
signal:* 1.19 / 1.27 / 1.34 / 1.42 / 1.52 at 100 req/s for 10 → 50 %, falling toward 1.04–1.16 at the knee (the base
CPU term, §1), i.e. the mix is flagged before the latency bias has a sign, but — as (a) shows — I_g does not carry the
sign or the size of the bias (the 40–50 % mixes have the largest I_g and the largest *negative* bias). (d) *The
in-LLC floor itself is bistable* (§2.8): the same 30 % mix reads −0.45 or +0.07 at ρ 0.42 depending on the socket's
idle state, a 2× change of the memwalk endpoint's floor that no dictionary profiled in one state predicts in the
other. For the paper: on this hardware the uniform dictionary's bias on a memory-bound mix is within ±0.15 below
ρ ≈ 0.3 at shares ≤ 30 % and nowhere at 40–50 %; it is +0.65–0.76 at the knee for every share and working set; and
the memory-bound endpoint's own floor moves with what the neighbouring cores are doing — in the helpful direction.

## 4. G4 — the verdict replaces the guard (Addendum D.6; analysis on the Phase F runs, clnode258)

**Scorer.** `analysis/g4_rescore.py`: `b4_compare` into `<run>/b4g4` with the current service dictionary (g(ρ) stored),
the Phase F truth (twin ramp or step baseline), and per served second the Addendum C rule in its order — 6 if E ≈ 0;
then 3 / 4 / 2 on I_g, Q (the Go recorder's runtime wait: `sched` p90 of Phase D's G-B, the `rt` mutex counter not
existing on these images; none on C++), slot busy ≥ 0.9; 7 otherwise — with **φ unavailable** (no probe existed in
Phase F, so row 1 cannot fire and the fallback R is not there either; stated per row). The wait guard is gone; the OOR
guard (ρ outside the profiled range) abstains as before; seconds on steps the service did not serve read **"saturation
in front of the tap; no verdict"** and leave the denominator, with D(λ)'s verdict on them counted beside. Thresholds
from the twin's served seconds as in §2.4 (E ≫ beyond the twin median + max(3 IQR, 0.15), I_g beyond median +
max(3 IQR, 0.05), Q beyond max(3 IQR, 10 ms/s)). "Before" = the Phase F cells recomputed from `<run>/b4g` (strict /
when it speaks / abstained); "after" = the G4 cell accuracy (slow × E ≥ 0.5 against the truth cell) with no abstention
but OOR. Figure `results/g4/figG4-before-after.png` (F6 layout, four rows: synthetic mid7 ramp, synthetic sweep,
recommendation one hog, geo to 16 000; left Phase F with the wait guard, right G4 with the verdict row as the third strip).

| row | served s | OOR | saturation: no verdict (D(λ) ext) | Phase F cells strict / speaks / abstained | G4 cells (OOR kept, wait guard off) | E within ±0.15 (slow s) | verdict rows per truth class | pattern agreement | Q source |
|---|---|---|---|---|---|---|---|---|---|
| synthetic B-065-104 + 1 aggressor | 146 | 0 | 0 (0) | 82.9 / 90.3 / 8.2 | **85.6** | 54 % (67) | ext: 1→3, 4→6, 17→7 · slow-int: 5→6, 44→7 · fast: 75→6 | 86 % | sched p90 (G-B) |
| synthetic mid7-070-109 + 1 aggressor | 147 | 0 | 3 (3) | 57.8 / 96.6 / 40.1 | **89.8** | 59 % (74) | ext: 3→6, 48→7 · slow-int: 7→6, 17→7 · fast: 72→6 | 65 % | sched p90 (G-B) |
| synthetic one-aggressor sweep | 302 | 0 | 82 (49) | 87.1 / 99.2 / 12.3 | **96.0** | 79 % (67) | ext: 10→2, 1→3, 1→6, 24→7 · slow-int: 1→3, 9→6, 21→7 · fast: 235→6 | 88 % | sched p90 (G-B) |
| HR reservation ramp + aggressors | 236 | 0 | 0 (0) | 100.0 / 100.0 / 0.0 | **100.0** | 0 % (0) | fast: 236→6 | 100 % | sched p90 (G-B) |
| HR reservation stressed sweep | 533 | 0 | 0 (0) | 100.0 / 100.0 / 0.0 | **100.0** | 98 % (40) | slow-int: 1→3, 39→7 · fast: 493→6 | 100 % | sched p90 (G-B) |
| HR frontend ramp + aggressors | 236 | 0 | 0 (0) | 100.0 / 100.0 / 0.0 | **100.0** | 0 % (0) | fast: 236→6 | 100 % | sched p90 (G-B) |
| HR frontend stressed sweep | 533 | 0 | 0 (0) | 100.0 / 100.0 / 0.0 | **100.0** | 0 % (0) | fast: 533→6 | 100 % | sched p90 (G-B) |
| post-storage k1c ramp + 1 aggressor | 146 | 1 | 0 (0) | 100.0 / 100.0 / 0.0 | **100.0** | 99 % (90) | slow-int: 90→6 · fast: 56→6 | 100 % | none (C++) |
| post-storage k1c stressed sweep | 409 | 2 | 0 (0) | 100.0 / 100.0 / 0.0 | **100.0** | 0 % (0) | fast: 409→6 | 100 % | none (C++) |
| geo, to 9000 | 410 | 0 | 0 (0) | 100.0 / 100.0 / 0.0 | **100.0** | 0 % (0) | fast: 410→6 | 100 % | sched p90 (G-B) |
| geo, to 16 000 | 205 | 28 | 220 (165) | 82.4 / 100.0 / 17.6 | **100.0** | 0 % (0) | fast: 205→6 | 100 % | sched p90 (G-B) |
| recommendation, 1 hog | 287 | 43 | 38 (32) | 85.7 / 100.0 / 14.3 | **100.0** | 100 % (82) | ext: 2→2, 1→3, 38→7 · slow-int: 41→6 · fast: 205→6 | 86 % | sched p90 (G-B) |
| recommendation, aggressor on both cores | 287 | 4 | 77 (77) | 84.3 / 99.6 / 15.3 | **97.9** | 57 % (82) | ext: 2→3, 39→7 · slow-int: 1→3, 25→6, 15→7 · fast: 205→6 | 86 % | sched p90 (G-B) |

**Reading.** (a) *Claim C8 holds: no row drops.* On every row the G4 cell accuracy is at or above Phase F's strict
figure — 85.6 vs 82.9 (B-065-104), **89.8 vs 57.8** (mid7, where the wait guard abstained on 40 %), 96.0 vs 87.1 (the
one-aggressor sweep), 100 vs 82.4 (geo to 16 000), 100 vs 85.7 and 97.9 vs 84.3 (recommendation one / two hogs), and
100 = 100 on the seven HR / post-storage rows. The seconds the wait guard abstained on are scored at the accuracy the
guard had "when it speaks" or better: the guard was protecting against an error that E does not make once ρ is in
range. (b) *Collapse windows.* 420 seconds on unserved steps (82 synthetic sweep, 220 geo, 38 + 77 recommendation,
3 mid7) now read "saturation in front of the tap; no verdict"; D(λ) had called 326 of them extrinsic (the Phase F
all-window figure quoted 262 for the subset it scored). (c) *The verdict rows without φ.* Where an aggressor held a
core, the Addendum C rule without φ ends in **row 7**: 17 of 22, 48 of 51, 24 of 36, 38 of 41 and 39 of 41 ext-truth
seconds on the five aggressor rows — I_g ≈ 1 (a time-slicing hog costs no CPU per request), Q ≈ 0, slot busy below 0.9
(the hog's core is missing from the count), so nothing closes. That is the designed answer: row 1 needs the probe's
occupancy (§2.4 C, where the same scenarios give row 1 on 39 of 40 and 41 of 41), and without it the rule says "no
verdict" rather than naming a wrong origin. The few ext seconds in row 2 (10 on the sweep, 2 on recommendation) are
the ones where the hog pushed the remaining slots past 0.9 — the capacity-cut face read as own overload, the §2.4 C
finding again. Row 3 fires on single seconds only (I_g bursts); row 4 never (Q = `sched` p90 stays under 10 ms on
every Go row). HR reservation's stressed sweep puts 39 slow-int seconds in row 7: its gate wait at 8000–10 000 req/s
with cap 1 has E ≫ 0 and slot busy 0.6–0.9 (one slot, cores busy 0.6–0.9 of 2) — the own-queue case under the slot
threshold, the same margin as ii.

**Row 7 at the slot-busy margin (D.6).** On the G2 files (probe v3, C.1 rule) the row-7 seconds whose only failed
condition is slot busy are 10 in ii (0.83–0.89) and 3 in vi (0.78–0.85):

| threshold | ii → row 2 | vi → row 2 | wrong conversions (ext truth) in the G2 set |
|---|---|---|---|
| 0.9 (current) | 0 of 10 | 0 of 3 | 0 |
| 0.85 | 8 | 1 | 0 |
| **0.8** | 10 | 2 | 0 |
| 0.75 | 10 | 3 | 0 |

With φ available the lower threshold costs nothing: every ext second has already gone to row 1. **Without φ** (the
Phase F rows) the same threshold at 0.8 turns the aggressor seconds from row 7 into **row 2, a wrong origin**: synthetic B-065-104 + 1 aggressor 12; synthetic mid7-070-109 + 1 aggressor 46; synthetic one-aggressor sweep 33; recommendation, 1 hog 39; recommendation, aggressor on both cores 39 ext-truth seconds read as the service's own overload (`results/g4-slot80/`; cells unchanged, because the cell
does not depend on the row). Decision: the threshold stays at 0.9 where φ is unavailable; with the probe on, 0.8 removes
row 7 at no cost on the eight G2 scenarios, and that is the setting the controller should run with.

**Controller target (the cgroup named by φ) against the aggressor cgroup, every probe-v3 run with an aggressor:**

| run | seconds with φ ≥ 0.05 | aggressor cgroup is the largest foreign holder | root cgroup larger | other larger | row-1 seconds | `holder_cgroup` = aggressor |
|---|---|---|---|---|---|---|
| i mix shift + 1 aggressor | 162 | 87 | 75 | 0 | 52 | 52 |
| iii recommendation + 1 aggressor per core | 327 | 327 | 0 | 0 | 124 | 124 |
| iv-b / iv-b′ stream (outside the cpuset) | 0 | – | – | – | 0 | – |
| v post-storage pairs 1 / 2 | 301 / 386 | 301 / 386 | 0 | 0 | 0 | – |

On every row-1 second the named holder is `gordion-aggr` (176 of 176). Among *tenant* cgroups the aggressor is the
largest foreign holder on 100 % of the φ-seconds in every run. On (i) the **root** cgroup's occupancy exceeds the
aggressor's on 75 of 162 seconds: root here is kernel work on the SUT's cores — the network softirq of the service's
own traffic (the generator is on the same node) and kworkers — not a tenant. The controller's target must therefore be
chosen among cgroups below the root (φ counts aggressor-class cgroups only, as implemented); root occupancy is reported
as `occ_root` and is not a target.

**D90 judged on the same rows (B3; `analysis/g_d90.py`, `results/g4/d90/`).** Per row, the p50 and the p90
dictionary from the same sweep: strict / when it speaks / abstained, and the decomposition within ±0.15:

| row | windows | p50: truth slow / ext | p50 strict / speaks / abstained | p90: truth slow / ext | p90 strict / speaks / abstained | within ±0.15, p50 / p90 |
|---|---|---|---|---|---|---|
| synthetic B-065-104 + 1 aggressor | 161 | 91 / 8 | 98.1 / 98.8 / 0.6 | 59 / 8 | 92.5 / 92.5 / 0.0 | 99 % / 98 % |
| synthetic mid7-070-109 + 1 aggressor | 165 | 113 / 50 | 64.2 / 100.0 / 35.8 | 69 / 40 | 88.5 / 88.5 / 0.0 | 100 % / 82 % |
| synthetic one-aggressor sweep | 431 | 183 / 171 | 60.6 / 84.2 / 28.1 | 146 / 115 | 84.7 / 84.7 / 0.0 | 65 % / 67 % |
| HR reservation ramp + aggressors | 251 | 0 / 0 | 100.0 / 100.0 / 0.0 | 0 / 0 | 100.0 / 100.0 / 0.0 | 84 % / 99 % |
| HR reservation stressed sweep | 595 | 0 / 0 | 100.0 / 100.0 / 0.0 | 102 / 1 | 99.5 / 99.5 / 0.0 | 100 % / 99 % |
| HR frontend ramp + aggressors | 251 | 0 / 0 | 100.0 / 100.0 / 0.0 | 0 / 0 | 100.0 / 100.0 / 0.0 | 48 % / 91 % |
| HR frontend stressed sweep | 596 | 0 / 0 | 100.0 / 100.0 / 0.0 | 0 / 0 | 100.0 / 100.0 / 0.0 | 100 % / 100 % |
| post-storage k1c ramp + 1 aggressor | 161 | 0 / 0 | 100.0 / 100.0 / 0.0 | 0 / 0 | 100.0 / 100.0 / 0.0 | 100 % / 99 % |
| post-storage k1c stressed sweep | 461 | 0 / 0 | 99.8 / 100.0 / 0.2 | 14 / 1 | 99.8 / 100.0 / 0.2 | 100 % / 100 % |
| geo to 9000 | 461 | 72 / 0 | 100.0 / 100.0 / 0.0 | 251 / 0 | 90.7 / 100.0 / 9.3 | 99 % / 100 % |
| geo to 16000 | 493 | 306 / 60 | 60.9 / 65.6 / 7.3 | 380 / 65 | 39.4 / 98.0 / 59.8 | 57 % / 93 % |
| recommendation 1 hog | 413 | 215 / 52 | 79.2 / 93.4 / 15.3 | 262 / 12 | 80.1 / 94.0 / 14.8 | 92 % / 92 % |
| recommendation aggressor on both cores | 412 | 212 / 79 | 76.9 / 88.8 / 13.3 | 228 / 27 | 79.6 / 91.6 / 13.1 | 87 % / 89 % |


Judgement: **keep D90 as a window-level tail path for the Thrift services, not as a second scorer.** On the synthetic
rows the p90 dictionary never abstains (W90 is within its profiled range where W50 is not) and so scores 88–93 % strict
where the p50 path abstained 28–36 %, but where both speak the p50 path is the more accurate one (98.8 vs 92.5 on
B-065-104; 100 vs 88.5 on mid7). On the Go services both are ≥ 99 % except where the p90 path abstains (geo to 16 000:
60 %) or both sit at 80 % (recommendation). Its one contribution is the case the p50 cannot see — (v), where the
aggressor's whole effect is in the tail: there D90 speaks on 88 % of the windows at 64 % when it speaks (§2.4 D). That
is worth one column on C++ rows and no more; the side track is closed as a scorer and kept as that column.

## Addendum D — decisions log (unattended run, Nov 11 from 07:15 UTC; written as each decision was taken)

- **D-1 (07:10) cpufreq "set, not none".** Found on node-3 socket 1: `intel_cpufreq` (intel_pstate passive), governor
  `performance`, `scaling_min_freq = scaling_max_freq = 2 000 000 kHz` on every core, i.e. already set and pinned at the
  nominal 2.0 GHz. Decision: not changed, recorded per run (`<run>/freq.txt`) and at the end (`results/g2f-final/freq.txt`);
  "restore" is therefore a no-op, logged as such.
- **D-2 (07:10) uncore frequency.** `/sys/devices/system/cpu/intel_uncore_frequency` is absent (the sysfs driver covers
  Skylake-SP and later; this is Haswell-EP). Decision per D.4's wording ("where it exists"): not pinned. Instead the
  current uncore ratio (`MSR 0x621`, bits 6:0) is read once per second on CPU 1 and CPU 9 through every A-run
  (`msr-tools` installed, read-only) so the uncore-scaling hypothesis of §2.4 D is tested rather than assumed. Pinning
  would take `wrmsr -p 1 0x620 <min:max ratio>` on package 1; not done unattended.
- **D-3 (07:12) LLC-capacity aggressor = `stress-ng --cache 4 --cache-level 3 --cache-no-affinity`** on CPUs 9,11,13,15
  (socket 1, outside the cpuset, sharing the 35 MB L3; `--cache-no-affinity` because the cache stressor otherwise re-pins
  its workers over all online CPUs, including the SUT's). The alternative (a memwalk loop at the LLC size) was not
  built; stress-ng's cache stressor reads and writes a buffer sized to the L3 with reuse, which is the D.4 definition.
- **D-4 (07:12) VioMutex share for D.5 = 40 %.** D.2 computed first: (vi) realised 0.04 / 0.08 / 0.12 lock utilisation
  (10 % × 200 / 400 / 600 req/s × 2 ms hold), so the null was by construction. For 0.7 at operating load with the 2 ms
  hold: 0.4 × λ × 2 ms = 0.7 at λ = 875 req/s, where the 40 % mix (c̄ ≈ 3.4 ms, k = 4) sits at ρ ≈ 0.74 — the step
  before its knee. Sweep 100 → 1100 req/s × 45 s; twin = a uniform sweep under the same conditions (also the 0 % point
  and the dictionary of G3.2).
- **D-5 (07:13) G3.2 = four more shares** (10 / 20 / 40 / 50 % VioMemWalk at 24 MB, 100 → 700 req/s × 45 s each) beside
  the 30 % (A1 twin) and 0 % (A2 uniform) points: six points of the self-interference curve under one pinning state.
- **D-6 (07:15) Queue order A1 → A2 → A3 → A4** as instructed, one detached script (`queue-g2f.sh`), started after (v)
  pair 4 ends; it kills the smoke probe left by the v3.2 test first (deviation 33). Expected cluster time ≈ 90 min.
- **D-7 (07:45) order control for the memwalk pairs.** A1's twin ran slower than its cache-aggressor run at every step
  (p50 5.2 vs 4.3 ms at 100 req/s, knee 600 vs 700 req/s, cores busy 0.63 vs 0.51, I_g 1.31 vs 1.07) — and in every
  memwalk pair so far (iv-b, iv-b′, A1) the twin ran first and the aggressor run second, and the second run was the
  faster one. The "stream makes the victim faster" reading of §2.4 D is therefore confounded with run order. Decision:
  a third queue (`queue-g2f2.sh`, after G5.2) runs the 24 MB pair in the reverse order — cache aggressor first, then the
  twin, then a second twin — with the pod's huge-page and NUMA placement recorded (`thp.txt`), so the order effect and
  the aggressor effect can be separated before D.4 is closed. Transparent huge pages are `madvise` on node-3 (the Go
  runtime does not madvise: AnonHugePages 0), so THP is not the mechanism; NUMA placement of the lazily allocated
  memwalk buffer is the open candidate.
- **D-9 (07:50) the uncore is already pinned.** The first working MSR read on node-3 gives `0x620 = 0x1b1b`: minimum
  and maximum uncore ratio both 27 (2.7 GHz), i.e. the platform runs the uncore at a fixed ratio, and `0x621` reads 27.
  There is nothing to pin and the uncore-frequency-scaling hypothesis for "the aggressor makes the memory walk faster"
  (§2.4 D) is closed: the uncore clock did not move. The order control (D-7) is now the only open explanation.
- **D-10 (09:20, read at 21:40) order control result.** Not an order effect: the aggressor runs are the fast state in
  both orders; the unstressed memwalk floor is bistable (4.7 vs 8.9–9.5 ms) with the socket's idle state as the only
  candidate left (§2.8). D.4 closed as the limitation in the addendum's words; the two idle-state controls listed in
  §5 for the decision, not run.
- **D-11 (21:40) the laptop session was asleep from ≈ 09:30 to 21:30 UTC** (deviation 37): the three queues finished
  unattended at 08:45 / 08:56 / 09:20 and nothing on the cluster depended on the session; the analysis of A4, G5.2 and
  the order control and §§2.8, 3, 5 were written after 21:36. The cluster is clean (final `freq.txt`, no leftover
  stress-ng / probe processes).
- **D-12 (22:35) controls done, cause unknown.** Spinner neighbour and C1E-off twin both stay in the slow state; the
  effect needs a memory-traffic neighbour. Recorded in §2.8, §5 and the summary's limitation 3; no further runs.
- **D-8 (07:45) uncore readout fixed.** `msr-tools` did not install (no `rdmsr` on node-3; the A1 `uncore.tsv` files
  hold `NA`). The readout now reads `/dev/cpu/N/msr` directly (`msr` module loaded) from A3 on; A1/A2 have no uncore
  trace, A3/A4 and the order control do.

## Deviations (RESULTS-D §4 style)

1. **Δt for the Go recorder is the measured t-difference, not `w`.** `w` is nominal there; using it would misstate U by
   the flusher's lateness (up to 5× on collapse windows). Falls back to `w` when the difference is outside [0.5 w, 3 w]
   (gaps after dropped idle heartbeats). I itself does not depend on Δt.
2. **Idle heartbeat windows** stay dropped by the loader (as in every earlier phase); the cpu delta after a heartbeat gap
   spans the gap, which can overstate U on the window following it. Only the 40 req/s step of the uniform sweep has
   empty windows (2 %); no effect on the per-step medians.
3. **Costs for ρ are the Phase F files** (average / op); they include the base CPU term, so I = 1 is by construction
   at the fitting band (reservation, post-storage) or at the step before the knee (geo, recommendation). The base +
   marginal fit in 1.2 is an addition, not in the handoff; it is the reason for the proposed redefinition.
4. **Twin ramps have no load steps**: I is tabulated per 0.1 ρ bin instead of per step.
5. **"Slot busy" in 1.4 is cores busy of the whole process ÷ slots**; the process counter cannot separate the executing
   slot from the transport core. Reported as provisional; the probe replaces it.
6. **Q is reported before G2** from the Go recorder's existing `sched` field (Phase D G-B), because it costs nothing; it
   is the Go half of Q only, and the C++ service has none until the probe.
7. **1.5 uses `svc_curve.csv` per-step p90** (pooled, served steps) rather than re-fitting the dictionary at p90; K per
   mix is the dictionary's K_sweep.
8. **A.1 applied with ρ from the marginal cₑ** (same regression as U₀), not the Phase F average cₑ; the dictionary's ρ
   is unchanged. Fallback taken for the real services (1.7).
9. **A.4 answered from the driver code**, not from a live cluster (clnode258 is gone): the patch that strips the quota
   is unconditional in `hr.sh`, `sn.sh`; the synthetic manifest never had one.
10. **New cluster.** `clnode170.clemson.cloudlab.us` is the stage-3 motivation testbed (runbook
    `simple-mitigation-1/deploy/stage3/RUNBOOK.md` §17); its §17.1 lists every environment change made for Phase G
    (side-by-side Go 1.25, docker group, `/local/{blueprint,hr,results}`, images, node-3 socket-1 layout, namespace
    `gordion`, taint toleration, royno7's key on node-1/2, bpftrace on node-3). Nothing of the motivation setup was
    modified; node-4 was not touched.
11. **Probe hook: `tracepoint:sched:sched_switch` + pid → cgroup map instead of `kfunc:finish_task_switch`.** bpftrace
    0.14 / libbpf 0.5 cannot read the 6.12 BTF ("BTF data not available"), BEGIN/END probes do not resolve on the
    stripped binary; the tracepoint form needs no BTF and measures the same quantities (2.2). Kernel threads are
    classified by the root cgroup, not by `PF_KTHREAD` (no struct access).
12. **Q split by hook, not by language.** The probe's futex-sleep Q is the Thrift (C++) Q; on Go it measures parked idle
    Ms and is reported as `Q_futex_ms` only. Go's Q = the recorder's new `rt` mutex-wait field + `sched`; G = the `rt`
    GC fields. Recorder change in both copies, images `k123g4` / `gordion7`, HR builder Go 1.21 → 1.25.
13. **Base-term runs use three steps, not one** (0.3 / 0.6 / 0.9 × knee), so that U₀ is an intercept and not a guess;
    the recommendation steps come from an explore on this hardware (knee rule 6000 req/s; served to 10 000).
14. **No costs file for this CPU yet**: the recommendation intercepts are regressions on the arrival rate (slope = CPU
    per request); the synthetic ones use the configured per-request costs.
15. **HR stack in namespace `gordion` with PVCs on `nfs-client`** (the fork's static per-service PVs are bound to the
    motivation's `default` namespace); the eight services have no CPU quota (`hr.sh install` strips it, `deploy_svc`
    again); DBs, consul, jaeger on node-1/2 with the chart's 3-core limits.
17. **Probe v1 → v2 after the first §2.4 runs.** The v1 runs of i, iii, iv (last-holder credit) are kept as `*-v1`
    directories; every §2.4 number comes from the v2 re-runs. Stage i's first attempt produced no generator data (the
    mix files were on node-0, wlgen runs on node-3; copied to node-3 `/local/results/`).
18. **(ii) built from the served step beyond a truncated profile.** The probe-on sweep's 850–900 req/s steps are not
    served by the generator (B.5 correction in 2.3), so row 2 is read on the 600 req/s step (ρ 0.72, served 1.01, slots
    0.93 busy) scored against the same sweep's dictionary cut after 500 req/s (`…probe-off-trunc500`, ρ ≤ 0.57), with
    the step-baseline truth from the full probe-off sweep. 41 seconds, one step, synthetic service.
19. **R_foreign threshold scale.** The twin's R_foreign is identically 0 (no aggressor cgroup), so the ≈ / ≫ scale is
    the twin's run-queue delay behind foreign-class holders (root cgroup = kernel threads and un-sliced daemons, other
    cgroups), per-step IQR, floored at 10 ms/s × k (1 % of the cpuset's time). With the twin's R_self IQR as the scale
    (first version) row 1 was unreachable on busy services (threshold 115–166 ms/s).
20. **Origin rows on slow seconds only.** Rows 2, 3, 4 and 7 are read where y = (Lat − F̄) ÷ Lat ≥ 0.5, as the Phase F
    cells were; row 1 and row 5 are read on every second (presence of the aggressor / of GC).
21. **Probe v3 for the second run of i and iii:** the per-window CPU occupancy of the cpuset by holder class (`Oself`,
    `Oaggr`, `Oroot`, `Oother`), already accumulated in v2 for the time-share credit, is exported so that a hog that
    occupies a core without preempting the service's threads is visible; reported beside the §0 rows as an alternative
    reading, not as a change to them.
22. **C.1 thresholds.** φ ≫ 0 is a floor (0.05 of the cpuset's CPU time): the twin's φ is identically 0, so no IQR
    exists; E, I_g, Q keep the twin-IQR bands with floors (0.15; ±0.05; 10 ms/s × k). The rows are evaluated after the
    slowness test (y ≥ 0.5), so a fast second is always row 6; G ≫ 0 is a flag beside the row, not a row.
23. **(ii) has no aggressor cgroup and its probe is v2 (no O* fields)**: φ = 0 by construction there, stated in the
    table; the C.1 fallback (R_foreign ≫ 0 or R_self ≫ R_self_twin) was not needed.
24. **(iv-b) mix and working set.** `mix-memwalk30.json` = 30 % VioMemWalk + 70 % uniform over Ep000–Ep119,
    `BIGSVC_MEMWALK_MB=128` (the socket's L3 is 35 MB; the configured cost of VioMemWalk, 4 ms, is the legacy
    256 MB calibration and is kept in the costs as is — the I_g of the memwalk mix against the uniform g is the
    G3.1 measurement, not a calibration error to fix). Sweep 100 → 700 req/s × 45 s, twin first, then `--stream` × 4.
25. **(v) per-request thread ids.** The C++ recorder emits `rawt` (kernel tid per raw sample, same order as `raw`) in
    image `sn-gordion:g5`; the per-request run delay is the thread's per-second R from the probe split equally over
    the requests the thread served in that second (`analysis/g5_tail.py`): a 1 s join, not the per-request series G5
    asks for; the per-second holder split gives the foreign share.
26. **(vi) truth.** VioMutex at 10 % has no twin; the truth share per second is the mutex mix's latency against the
    uniform probe-off sweep's p50 at the same rate (200 / 400 / 600 req/s = its steps 2 / 4 / 6), i.e. the extra
    latency the mutex endpoint adds; the expected class is slow-int, the expected row 4.
27. **(iv-b′) is an addition.** After (iv-b) showed no effect with the working set past the L3, the same pair was run
    with `BIGSVC_MEMWALK_MB=24` (an environment variable of the SUT pod; mix file and costs unchanged) so that an LLC
    aggressor could act on a cached working set. Result above: it does not, either.
28. **A stray second probe instance ran from 05:12 to 06:14 on CPUs 1,3,5,7.** Cause: the stage-D queue script was
    rewritten on node-0 while bash was executing it, and bash read on into the new block, starting a duplicate chain;
    the chain was killed but its `probe.py`/`bpftrace` survived. It overlapped (v) run 1 (05:26–05:44) and both (iv-b′)
    runs (05:44–05:58): a second `sched_switch` program on those CPUs at ≈ 0.005 cores, equal on the twin and the
    stressed run of each pair. Found and stopped at 06:14 before (v) run 2; its output discarded. The §2.3 overhead
    numbers predate it.
29. **(v) per-request thread ids and PID namespaces.** `rawt` holds `gettid()` inside the container's PID namespace
    (ids 10–140 527 over the run) while the probe keys `@Rthr` by host tid (564 195–570 161): run 1's per-request join
    matched zero threads. Fix: `hostPID: true` on the SUT pod (`sn.sh` with `HOSTPID=1`, host and container tids then
    coincide), **run 2** of the pair at 06:14 (`g2e-v2-…`). Run 1 stays valid for the verdict and the D90 side-score
    (neither uses the thread ids) and is kept as a repeat; run 2's rows joined, but only on the first two steps
    (deviation 32), so the per-request rows come from run 3.
30. **(v) truth and E are p50 quantities.** The post-storage p50 is 0.05–0.06 ms and does not move under the
    aggressor (it falls: the hog keeps the core out of idle states), while the p90/p95 rise 30–110 %; the twin truth
    and the cells therefore read "fast" on every served second. The p90 stream is scored beside it with the A.3 code
    (`g_d90.py`, dictionary from the twin's own sweep) and reported in §2.4 D (v).
32. **Probe v3.1 for (v) run 3: bpftrace's 4096-key map default.** On a thread-per-connection server (post-storage:
    ~30 000 threads per sweep, ~60 new per second) the probe's pid-keyed maps (`@cg`, `@runnable`, `@snap*`, `@sleep`,
    `@infutex`, `@Rthr`) fill within ~90 s; every later thread is unclassified and its run-queue delay and futex
    sleep are lost (R and Q read 0 from the third step on in runs 1 and 2, while the occupancy O*, read from the
    `cgroup` builtin, stays right — so φ and the verdict are unaffected). Fix: `BPFTRACE_MAP_KEYS_MAX=1048576` in
    `probe.py` and per-thread entries deleted on `sched_process_exit` (`probe/README.md` v3.1); unit test unchanged
    (aggressor credited 50.0 ms per 100 ms window). The Go services (a handful of threads) were never affected; the
    synthetic and HR rows stand. Run 3 of the (v) pair at 06:38 with v3.1 and `hostPID: true` — **and v3.1 aborted
    11–22 s into each sweep**: bpftrace 0.14's `zero(@Rthr)` is a userspace pass over the map's keys and fails
    ("Could not zero map … failed to look up elem") when the new kernel-side delete removes a key under it (the same
    class of race that ruled out `clear()` in deviation 11). Pair 3 therefore has φ and R for its first 22 s only and no
    per-request rows; its verdict (row 6 throughout) is unchanged. **v3.2** = v3.1 without the `@Rthr` delete (exited
    threads keep a zero entry; the other per-thread maps, never read from userspace, are still pruned) and the probe's
    userspace pinned to CPU 9 (`PROBE_CPU` in `probe-ctl.sh`), because printing a map of every thread seen since the
    start costs more userspace CPU than before. 40 s smoke test on the live pod clean; **pair 4** (`g2e-v4-…`) at 07:01
    ran to the end with every thread classified. Cost of the fix on this service: the per-second `@Rthr` print grows
    with the threads seen (117 k by the end of a sweep), the probe's userspace rose from 0.03 to 0.9 of CPU 9 over the
    sweep (off the SUT core, so no interference; `probe-hist.jsonl` 100 MB per run). On the Go services (a handful of
    threads) nothing changes. A per-request alternative that prints one line per resume instead of the map is noted in
    §5 as what G5 on Thrift would need.
33. **Smoke probe overlapping pair 4.** The 40 s v3.2 smoke test's `probe-ctl.sh stop` did not end its probe (the pid
    file was overwritten by the queue's own start); a second probe instance on CPU 1 ran through all of pair 4 (twin and
    hog alike) until the overnight queue killed it at 07:18. Same class as deviation 28; equal on both halves of the pair.
34. **Pair 4 above 1000 req/s.** From 1200 req/s post-storage completed 1150–1280 req/s with 0.04–0.07 cores and a
    processing p90 of 0.11 ms in both runs (pairs 1–3: 850–880 req/s at 0.95–1.0 cores, p90 40–140 ms); no errors,
    arrivals = completions. The store calls returned near-instantly at those rates; cause not investigated (the steps are
    unserved by the chain and outside the truth; steps 0–3 match pairs 2–3 in p50, p90 and CPU). The NNLS costs of pair
    4's sweep are therefore not used (R² < 0); the verdict and the per-request rows do not depend on them.
31. **SocialNetwork graph load.** 2285 of the 9424 compose-post calls of the socfb-Reed98 load returned
    `Internal Server Error` from nginx (the user and follow loads completed); post-storage holds fewer posts than the
    stock load. The sweeps' requests are generated by hrgen (`-app sn`) against the running services and do not read
    the graph, so the dictionary and the stressed rows are not affected; the setup is flagged for anyone reusing the
    namespace for a graph-dependent workload.
35. **Order control (D-7 → D-10).** Three extra 24 MB runs (cache first, twin, twin) beyond the addendum's A1–A4,
    because every memwalk pair had run twin-first and the second run was always the faster one; the control shows the
    effect is the neighbour's presence, not the order (§2.8). No further attempt at row 3.
36. **Uncore trace missing on A1 and A2** (`msr-tools` never installed; `NA` in their `uncore.tsv`); from A3 on the
    ratio is read from `/dev/cpu/N/msr` and is 27 in every sample of every run (fixed by the platform, D-9).
37. **The laptop session slept ≈ 09:30–21:30 UTC.** The background watcher was stopped by the host for low memory and
    the session went idle; the cluster queues were unaffected (detached on node-0). §§2.8, 3, 5 and the final matrix
    were produced at 21:36–22:30 UTC instead of the morning.
38. **Probe v3.2 userspace cost on the Go runs:** 0.10 cores on CPU 9 (the per-second `@Rthr` print now keeps every
    thread id seen), against 0.005–0.03 for v3 on the same service; on the SUT cores nothing changed. On (v) pair 4 it
    reached 0.9 cores of CPU 9 (deviation 32).
39. **A2's uniform twin collapsed at 700–800 req/s** (served 0.85 at 800, p50 45–46 ms) one step earlier than the
    probe-off uniform sweep of §2.3 (900); the VioMutex mix's truth at 800–900 req/s is therefore measured against a
    collapsing baseline (its share reads ≈ 0, the class slow-int), which is what the row-4 demonstration needs and no
    more; the mutex mix itself served 800 at 1.00 and 900 at 0.89.
40. **G5.2 counter emitted cumulative.** Image `k123g5`'s recorder reset the window's slices but not the new `mChg`
    integer, so `"mchg":[n, changed]` carried a running total; the analysis differences consecutive windows
    (`analysis/g5_mchg.py` note, §5 B4) and the recorder is fixed in source (`buckets[i].mChg = 0` at flush; image not
    rebuilt).
16. **Phase D sweep schedule reused for the overhead pair** (40 → 900 req/s, 12 steps) with image `k123g4`; the pair's
    top steps (ρ ≥ 0.9, served ≥ 0.95, cores busy ≈ 3.5–3.6 of 4 with cap 3) double as the first look at row 2
    with the probe on (A.5 c ii).

## 5. Overnight summary (Addendum D, Nov 11; written 22:00 UTC, see D-11)

**What ran (cluster, all detached on node-0; environment changes in RUNBOOK §17.1; nothing committed).**
`queue-g2f.sh` 07:15–08:45: A1 D.4 at 24 MB (twin + `stress-ng --cache` × 4 on CPUs 9–15), A2 D.5 (uniform twin +
VioMutex 40 %), A3 G3.2 (10 / 20 / 40 / 50 % VioMemWalk at 24 MB), A4 D.4 at 128 MB — every run with probe v3.2, the
aggressor in its own cgroup, cpufreq recorded (found pinned at 2.0 GHz, not changed, "restore" a no-op logged in
`results/g2f-final/freq.txt`), the uncore read (found fixed at ratio 27, not pinned). `queue-g5.sh` 08:45–08:56: the
G5.2 pair with image `k123g5`. `queue-g2f2.sh` 08:56–09:20: the order control (D-7). Before it, (v) pairs 3 and 4
(probe v3.1 abort → v3.2) closed the (vi) stop point's open item. Node-3 was left clean.

**Decisions taken (log above, D-1 … D-11), in one line each.** cpufreq already set → recorded, untouched (D-1). No
uncore sysfs → read-only MSR trace, not pinned (D-2), then found fixed at 2.7 GHz by the platform (D-9). LLC aggressor =
`stress-ng --cache --cache-level 3 --cache-no-affinity` (D-3). VioMutex share 40 % from D.2 (D-4). G3.2 = four extra
shares in one session (D-5). Queue order as instructed (D-6). Order control added when A1's twin ran slower than its
aggressor run (D-7) → not an order effect; the memwalk floor is bistable with the socket's idle state (D-10). Session
asleep 09:30–21:30, report finished at 22:00 (D-11).

**Results, binary where the addendum asked for binary.**
- **D.5 → row 4 demonstrated** (§2.7): with the lock at 0.56–0.64 utilisation the Addendum C rule reads row 4 on 99
  of 123 slow seconds, Q (Go mutex wait) 195–280 ms/s against a twin of 0; the Go counter is right, no switch to the
  eBPF split. The cell axis calls the lock queue "ext" (75 % cells) and the row corrects it — the G4 point in one run.
- **D.4 → row 3 not demonstrated, limitation stated** (§2.7, §2.8): at 24 MB and 128 MB the cache-resident aggressor
  on the LLC-sharing cores produces no slowdown the rule can see (row 6, cells 100 %, φ 0, I_g below the twin's); the
  victim is *faster* under a memory-traffic neighbour, in either run order, by 18 % at the p50 and one load step at the
  knee. The closing controls (22:12–22:34) ruled out package idle states (a pure CPU spinner as neighbour: no effect;
  C1E disabled on the socket: no effect): **cause unknown**, on the memory side of the socket (§2.8).
- **G3, both working sets** (§3): uniform-dictionary bias +0.67–0.68 past the knee at 128 MB (twice), +0.65–0.76 at the
  knee at 24 MB for every share 10–50 %; below the knee within ±0.15 only at shares ≤ 30 % and ρ ≲ 0.3, with a sign that
  follows the socket state; I_g 1.1–1.5 flags every memory-bound mix, unsized. Figure `results/g2/figG3-self-interference.png`.
- **G4** (§4): the verdict replaces the wait guard with no row dropping (cells 85.6 / 89.8 / 96.0 / 100 / 100 / 97.9
  against Phase F's 82.9 / 57.8 / 87.1 / 82.4 / 85.7 / 84.3 on the six rows the guard touched; 100 = 100 on the other
  seven); 420 collapse seconds read "saturation in front of the tap; no verdict" where D(λ) had 326 false extrinsic;
  without φ the aggressor seconds end in row 7 (honest "no verdict"), with φ (§2.4 C) in row 1; slot threshold stays
  0.9 without the probe, 0.8 is free with it; the controller target is the aggressor cgroup on 176 of 176 row-1
  seconds, with the root cgroup (kernel softirq) to be excluded as a target. Figure `results/g4/figG4-before-after.png`.
- **D.1–D.3** (§2.6): (v)'s p90 rise is on the processing stream at 1–70 % by pair and 9–29 % on the full latency; with
  blocked time excluded 5–20 % of the full rise survives; the store is on node-1, not with the aggressor; (vi) was at
  lock utilisation 0.04–0.12, no test; deviation 28 restated and the twin-drift limitation of I_g on store-backed
  services written down.
- **D90** (§4, B3): kept as one window-level tail column on the C++ rows, closed as a scorer.
- **G5.2 (B4), bounded:** image `k123g5` records the OS thread at handler entry and exit. On the scenario-i pair the
  handler ended on another OS thread than it began on for **0.9 % of requests without the hog and 2.0 % with it**
  (per 30 s window 0.4–1.5 % and 1.8–2.3 %; `analysis/g5_mchg.py`, counter differenced, deviation 40). The Go
  thread ↔ request mapping is therefore stable for 98–99 % of requests on this service, so a per-request run-queue
  attribution from the probe's per-thread series would be wrong on ≤ 2 % of requests — a bounded proxy error, not a
  blocker. What G5 on Go still needs is what bpftrace 0.14 gives only through the per-second map print (v3.2: per
  thread, per second); per-request precision would take one printed line per resume event (tid, timestamp, delay,
  holder split) from the tracepoint program — feasible in bpftrace 0.14 at the Go services' resume rate (a few
  thousand per second), and the design for Thrift (deviation 32). Not built tonight; one day of work.
- **(v) pair 4** (§2.4 D, §2.6): the per-request rows with every thread visible; the tail request is a store call
  (blocked 70–90 %), the hog adds 0.7 ms to it at 200 req/s and 7 ms at 800, the thread's own run-queue delay explains
  0.1–0.4 ms of that.

**The final matrix** is §2.5 (fourteen rows, C.1 and §0 rules, `results/g2/matrix_c1.md`, figure
`results/g2/figG2-5-verdicts.png`): row 1 only where an aggressor holds the cpuset and the p50 moved (i, iii), row 4
where the lock queue is the latency (D.5), row 6 everywhere a neighbour did nothing the p50 can see (iv family, D.4
family, v), row 2 on the own-queue step (ii); cells 90–100 % on thirteen rows and 75 % on D.5 by the cell's own
definition.

**Deviations added tonight:** 27–40 (list above; 32–34 the probe map limit and its two fixes, 35 the order control,
37 the sleeping session, 40 the cumulative G5.2 counter).

**Closing (Nov 11, 22:00–23:00 UTC, after acceptance).** The two idle-state controls ran (§2.8: ruled out, cause
unknown); the cluster end state was restored and verified (RUNBOOK §17.1: aggressor cgroups and probe removed, cpufreq /
uncore / cpuidle as found before Phase G); the package is in `results/g-final/` with `RESULTS-G-SUMMARY.md`; every
Phase G file is staged, nothing committed. Nothing is pending a decision. **Post-acceptance additions (analysis
only, no cluster):** §2.9 (the rule on raw 100 ms windows: majority verdict kept, 25–95× the flip rate) and §2.10 (D(λ)
and the Phase F scorer as columns beside the verdict rules; D(λ) cannot be scored as designed on ii's 53 seconds past
the profile) and §2.11 (both old methods per 100 ms window: D(λ) 72–100 %, the Phase F scorer 79–100 % when it speaks,
the wait guard abstaining more at window level because it tests the raw window where per second it tests the smoothed
Lat), all in `results/g-final/` (`window_table`, `oldcols`, `oldcols_window`, `figures/figG2-9-window-strips.png`, `windows/`). **Addendum E (Nov 12, §6):** vocabulary by mechanism; the amount of run-queue contention stays E (the φ-based counterfactual is undefined where the aggressor binds, §6.1); node checks (§6.2, `probe/e2_checks.sh`); probe v4 with φ_sibling, the waker split and perf counters (§6.3); SMT-sibling aggressor runs: φ 0, φ_sibling 0.9, +3–8 % CPU per request, no slow second, row 6 (§6.4 a); the cache pair with counters: the victim is faster with half the LLC loads per request (§6.4 b). **Addendum F (Nov 12, §7):** probe v5 with per-resume records and softirq hooks; on unique-id a 20 % duty-cycled hog leaves the recorder's p95 unmoved and doubles the arrival-stream p95, the whole growth being run-queue delay behind the hog on 2–3 % of the arrivals; on post-storage the tail growth is queueing behind the service's own threads and a blocked-segment remainder before the wake-up — the softirq-on-CPU-1 hypothesis of §2.4 D does not hold.

## 6. Addendum E — the amount from φ, the node's counters, probe v4, SMT-sibling and counter runs (Nov 12)

Spec: `PHASEG-HANDOFF.md` Addendum E. Vocabulary: the diagnoses are named by mechanism from here on (the note under the
title); the cell slow × ext keeps its name. §6.1 and §6.2 are analysis and read-only checks; §6.3 is probe v4; §6.4 the
E.4 runs (cluster clnode170, node-3, same layout as §2.1); §6.5 deviations. Environment changes are in stage-3
`RUNBOOK.md` §17.1 (Addendum E entry).

### 6.1 E.1 — the amount of run-queue contention from φ instead of from E (analysis only)

**What was done.** For every served second of i and iii, and of ii / vi / D.5 as the zero control (φ = 0 there), three
amounts as shares of the second's Lat (the 5 s-smoothed server-side Lat_e p50 the cell is labelled from): **(a)** E =
(Lat − D[ρ; mix]) ÷ Lat, today's amount; **(b)** the signal alone, (R_foreign ÷ λ) ÷ Lat — the aggressor-held run-queue
delay of the service's threads in the second (probe v3, ms per second) spread over the second's requests; **(c)** the
capacity counterfactual, (D[ρ ÷ (1 − φ); mix] − D[ρ; mix]) ÷ Lat with φ the aggressor cgroups' share of the cpuset from
the probe's occupancy and the dictionary at the second's own mix — the wait the service would show if it had (1 − φ) of
its cores and queued exactly as its profile h(ρ) says. ρ ÷ (1 − φ) past the dictionary's last bin is held there
(`np.interp`) and counted as "past the profile"; a second variant **(c′)** continues h linearly from the last two bins,
to show what a straight continuation would give. Each is scored against the twin's truth share (within ±0.15 on the slow
seconds and on all seconds) and as the cell slow × (amount ≥ 0.5) against the truth cell. Script `analysis/e1_amount.py`
→ `results/e1/e1_amount.{md,csv}`, per-second dumps `results/e1/<scenario>-e1.csv`, Fig. G-E1
(`results/e1/figG-E1-amount.png`: i and iii in the F6 layout — latency panel with D[ρ; mix] and D[ρ ÷ (1 − φ); mix],
amount panel with the three amounts and (c′) over the truth, strips truth / φ > 0.05 / past the profile).

| scenario | n (slow) | ext-truth s | φ on ext-truth s, median (p10–p90) | ρ/(1−φ) past the profile | (a) E: ±0.15 slow / all | (a) cell | (b) R_foreign/λ ÷ Lat: ±0.15 slow / all | (b) cell | (c) D(ρ/(1−φ)) − D(ρ): ±0.15 slow / all | (c) cell | (c′) h continued linearly: ±0.15 slow / all | (c′) cell | median (c) − E on ext-truth s | median (c′) − E on ext-truth s | median (b) − E on ext-truth s |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| i mix shift + 1 aggressor (v3) | 147 (69) | 40 | 0.26 (0.17–0.30) | 67 of 147 | 57 % / 66 % | 90 % | 13 % / 46 % | 73 % | 10 % / 5 % | 16 % | 7 % / 3 % | 10 % | -0.80 | -0.59 | -0.80 |
| iii recommendation + 1 aggressor/core (v3) | 164 (124) | 41 | 0.53 (0.50–0.55) | 94 of 164 | 97 % / 94 % | 99 % | 33 % / 48 % | 74 % | 6 % / 9 % | 17 % | 16 % / 16 % | 34 % | -0.60 | -0.22 | -0.79 |
| ii served step past the profile (no aggressor) | 392 (40) | 2 | 0.00 (0.00–0.00) | 47 of 392 | 8 % / 75 % | 95 % | 70 % / 97 % | 99 % | 70 % / 97 % | 99 % | 70 % / 97 % | 99 % | -0.75 | -0.75 | -0.75 |
| vi mutex10 (no aggressor) | 168 (32) | 0 | — | 0 of 168 | 59 % / 91 % | 99 % | 100 % / 100 % | 100 % | 100 % / 100 % | 100 % | 100 % / 100 % | 100 % | — | — | — |
| D.5 VioMutex 40 % (no aggressor) | 369 (130) | 0 | — | 0 of 369 | 8 % / 67 % | 75 % | 100 % / 100 % | 100 % | 100 % / 100 % | 100 % | 100 % / 100 % | 100 % | — | — | — |

**Reading.**
- **E is the only amount that works, and it works because it does not use φ.** On the slow seconds E is within ±0.15 of
  the truth on 57 % (i) and 97 % (iii), the cell 90 / 99 %; on the ext-truth seconds E and the truth agree to the second
  decimal (0.80 / 0.75 on i, 0.80 / 0.80 on iii). The two φ-based amounts read 0.00–0.01 (b) and 0.01–0.19 (c) on the
  same seconds, cells 73–74 % (b) and 16–17 % (c) — on i no better than always answering "int" (73 %).
- **Where the queue model stops: exactly where the aggressor binds.** On every ext-truth second of both scenarios the
  counterfactual load ρ ÷ (1 − φ) lies past the profile: i ρ 0.67 → 0.90 against a last bin at 0.70 (40 of 40 seconds),
  iii ρ 0.52 → 1.09 against 0.74 (41 of 41). The dictionary has no h there; holding h at its end gives (c) 0.01 / 0.19,
  continuing it linearly gives (c′) 0.21 / 0.59, both against E = truth = 0.80. A queue model from φ needs the service's
  wait curve past the knee of a (1 − φ)k-core service, which no profile of the k-core service contains.
- **Where (c) does not match E inside the profile: it overshoots.** On the 80 (i) and 70 (iii) seconds with φ > 0.05
  whose counterfactual load is still inside the profile, (c) reads 0.82 (i) and 0.54 (iii) against a truth of 0.04 and
  0.25 — E reads 0.07 and 0.25. The reason is the service's own saturation shape: h(ρ) is the wait of the k-core service
  behind its gate (k − 1 slots), steep from ρ 0.5 up; a hog that holds one core of four does not cut the capacity of a
  service whose three slots still find three free cores, so ρ ÷ (1 − φ) overstates the load until the slots and the
  cores run out together (i's step 0 at ρ 0.67, where the truth turns ext). On the slow-int seconds of i (29, φ 0.25)
  (c) happens to read 0.37 against a truth of 0.34 and E 0.35 — the one place the three agree — and (c′) reads 0.90.
- **(b) is zero because the hog's cost is not in our threads' run-queue delay behind the hog.** R_foreign ÷ λ is
  0.4–1 % of Lat on the ext-truth seconds of i and iii: under CFS a woken service thread preempts the hog within
  microseconds and then queues behind its own threads on the remaining cores (R_self, §2.2 v2), and on iii the aggressor
  sits on both cores but the recommendation handler's wait is in its own run queue. The probe's R_foreign is a presence
  signal (and φ a better one, §2.5); neither is an amount.
- **Zero control.** With φ = 0 on ii, vi and D.5, (b) and (c) are 0 by construction and score 97–100 % on all seconds
  and 70–100 % on the slow ones; on D.5 they score 100 % where E scores 67 % (cell 75 %) — the lock-wait seconds, where
  there is no run-queue contention to measure and E's residual is the lock wait that row 4 names (§2.7). That is the
  split the vocabulary now states: φ triggers row 1 and names the holder, E is the amount of the residual, and the row
  says what the residual is.

**Conclusion.** The amount of run-queue contention stays E = (Lat − D[ρ; mix]) ÷ Lat. φ is the trigger and the target
(the holder cgroup), not an amount: the capacity counterfactual D[ρ ÷ (1 − φ)] − D[ρ] is undefined on every second where
the aggressor binds (the load it asks for lies past the profile) and over-predicts by 0.3–0.8 where it is defined, and
the signal-only amount is two orders of magnitude too small. Both are kept in `e1_amount.csv` beside E.

### 6.2 E.2 — what clnode170's service node exposes (read-only checks, `probe/e2_checks.sh`)

Run on node-3 (`bash e2_checks.sh 1,3,5,7`, 2026-11-12 05:04 UTC; the one reversible step, mounting resctrl to read its
`info/`, at 05:11 UTC, unmounted in the same command; RUNBOOK §17.1). The script is the checklist for the Ice Lake nodes.

| item | finding on node-3 (Haswell E5-2683 v3, kernel 6.12.116-harvest) | consequence |
|---|---|---|
| `/sys/fs/resctrl` | directory present, not mounted; `CONFIG_X86_CPU_RESCTRL=y`; cpuinfo flags `cqm cqm_llc cqm_occup_llc` only (no `cqm_mbm_*`, no `cat_l3`, no `mba`). Mounted for the check: `info/L3_MON/mon_features = llc_occupancy`, `num_rmids 56`, `max_threshold_occupancy 630784`; no `info/L3` (CAT) and no `info/MB` (MBA); `mon_data/mon_L3_00,01/llc_occupancy` readable (34.6 MiB each = the whole LLC in the root group). Unmounted again. | **CMT only**: per-cgroup LLC occupancy is available by putting the pod's tasks in a `mon_groups/` entry (an experiment-time change, not done); no memory-bandwidth monitoring and no cache allocation on this part. An LLC-capacity verdict (row 3) could be *sized* with CMT (occupancy of victim vs aggressor) but not *enforced*. |
| cgroup v2 `cpu.pressure` | cgroup2fs; PSI on (`/proc/pressure/cpu some avg10 1.8`); `cpu.pressure` present per pod and per container under `kubepods.slice` (9 files at depth ≤ 3), mode 0644 root, **readable by a non-root user**, `some` and `full` lines with avg10/60/300 and total; `cpu.stat` beside it (`nr_throttled 0`) | a per-pod "some" CPU pressure is a free kernel-side run-queue signal (the share of time at least one of the pod's tasks waited for a CPU); it does not name the holder, so it would sit beside φ as a cheap presence test, not replace it |
| PMU | `perf_event_paranoid = 2`, `kptr_restrict = 1`, NMI watchdog off (section 17); `/sys/devices/cpu/rdpmc` absent on this kernel (user-space `rdpmc` availability not readable from sysfs; not needed); PMU `haswell`, `max_precise 3`; dmesg: **4 generic + 3 fixed counters per logical CPU with SMT on**; `perf` at `/usr/local/bin/perf` works as root (`perf stat` smoke: cycles / instructions / LLC-load-misses on one CPU) | cycles and instructions take the fixed counters, LLC-loads and LLC-load-misses two of the four generic ones: the E.3 (c) set fits without multiplexing per logical CPU; a second event group on the same tasks would multiplex |
| SMT map of the cpuset | SMT on; `1↔29, 3↔31, 5↔33, 7↔35` (core ids 0–3, package 1); LLC shared by every odd CPU 1–27 and the siblings 29–55; the siblings run `performance` at 2 000 000 kHz min = max like the cpuset; cpuidle POLL/C1/C1E enabled, C3/C6 disabled on all of them | the E.4 (a) aggressor goes on 29,31,33,35; the probe's sibling filter is those four CPUs |
| libbpf toolchain | installed: gcc 11.4, make, `libbpf0 0.5.0`, `libbpfcc 0.18`, `libelf-dev`, `zlib1g-dev`, `linux-headers-6.12.116-harvest`, BTF at `/sys/kernel/btf/vmlinux`; **not installed:** clang, llvm-strip, bpftool, `/usr/include/bpf/bpf_helpers.h`; apt candidates clang 14.0, llvm 14, `libbpf-dev 0.5.0`, no bpftool package (`linux-tools-common` is 5.15); apt reachable (simulated install resolves) | a CO-RE program could be built, but not from apt alone: libbpf 0.5 cannot read this kernel's BTF (§2.2), so libbpf ≥ 1.0 and bpftool would have to be built from source (kernel tree or GitHub) with clang 14 from apt. Not done: E.3 (a) and (b) fit bpftrace 0.14, and E.3 (c) uses `perf stat` in counting mode, which needs no BPF |

### 6.3 E.3 — probe v4 (`probe/`, bpftrace 0.14 throughout; `perf stat` for the counters)

What changed against v3.2, all in `gordion_sched.bt.tmpl` / `probe.py` / `probe-ctl.sh` (`g2_probe.py` carries the new
fields as appended columns of `g2/g2_windows.csv`; `g2_verdict.py` appends `phi_sibling`, `row_e4`, `holder_sibling` to
`verdict.csv`; nothing existing renamed or moved; the regression on scenario i reproduces every row and cell):

- **(a) φ_sibling.** A second `sched_switch` block filtered to the cpuset's SMT siblings (`--sibling-cpuset`, 29,31,33,35
  here) keeps the same per-class occupancy counters as the cpuset's (`S*` = `Sself / Saggr / Sroot / Sother / Sidle` ns per
  window); φ_sibling = Saggr ÷ (k × window) is the aggressor cgroups' share of the siblings' time, computed per second
  in the scorer like φ. Only the occupancy: the service's threads never run on the siblings (cpuset), so there is no
  run-queue attribution to do there. v4 also charges the idle task to its own class (`Oidle`, `Sidle`; in v3 idle time
  sat inside `Oroot`), which changes nothing in φ or R.
- **(b) Waker split.** On `sched_wakeup` of a sleeping service thread the sleep that just ended is credited to the
  waker's class — the task running the wakeup: our own cgroup (0: a lock or channel hand-off, the Go runtime parking
  and unparking its Ms), the aggressor cgroup (1), the root cgroup (2: kernel threads, un-sliced daemons), another
  cgroup (3), the idle task (4: pid 0, i.e. an interrupt or softirq on an idle CPU — I/O completions, timers) — crossed
  with the futex flag of the sleep (`Wf[c]`, `Wo[c]` ns, counts for classes 0 and 4). `g2_probe.py` exports
  `W_own_futex_ms`, `W_own_other_ms`, `W_aggr_ms`, `W_root_ms`, `W_other_ms`, `W_idle_ms` (+ `W_idle_futex_ms`,
  `W_own_n`, `W_idle_n`). Known limit, stated in the template: a softirq that runs on a busy CPU is credited to the
  task it interrupted, so network receive while one of our threads runs reads as "own"; the C.1 rule does not use the
  split, it is reported.
- **(c) Counters.** Not through BPF (§6.2: no clang / bpftool / libbpf ≥ 1.0 on the node, and bpftrace 0.14 cannot read
  a per-task counter at a switch): `probe-ctl.sh perf-start` runs `perf stat` in **counting mode** (no sampling) with
  `cycles, instructions, LLC-loads, LLC-load-misses` at 100 ms intervals, once on the **victim process with all its
  threads** (cgroup mode `-G`, system-wide on the cpuset: every thread, including the ones created later) and once **system-wide on the aggressor CPUs**
  (`-a -C 29,31,33,35` for the sibling runs, `9,11,13,15` for the cache run); the two fixed + two generic counters fit per
  logical CPU with SMT on, no multiplexing. `analysis/g2_perf.py` joins the intervals to the recorder's windows
  (`g2/perf_windows.csv`: `v_ipc`, `v_cycles_per_req`, `v_llc_misses_per_req`, `v_llc_miss_rate`, `a_ipc`,
  `a_llc_miss_rate`, …). **Deviation 41 (spec: "per thread"):** the victim is counted per cgroup, not per thread
  (`--per-thread` enumerates the threads at start and misses the ones the Go runtime creates under load; complete
  coverage was preferred, and §2.8's thread ↔ request mapping is 98–99 % stable on this service anyway). **Deviation
  42:** the first E.4 queue attached the victim counters with `-p <container init pid>`; the service process is that
  init's child and was already running, so every victim interval read "not counted" on the six probe-on runs (the
  aggressor-CPU counters are complete). Fixed to cgroup mode (`probe-ctl.sh` v4.1, verified on the idle pod) and the
  (b) pair re-run once with the fix (§6.4); the (a) pairs keep their aggressor counters and I_g, which is what (a) asks.
- **Unit test** (`probe-unit-v4.sh`, node-3, 05:21 UTC): a CPU-bound loop in a temporary `gordion-victim` cgroup on CPU 1,
  a hog in `gordion-aggr` first on the sibling CPU 29 (20 s) then on CPU 1 (15 s), probe on CPU 53. Phase 1: `Oaggr` 0,
  `Saggr` 85–114 ms per 100 ms window (≈ 92 ms over 5 s: the occupancy is charged at context switches, and a spinning hog
  switches rarely, so single windows are lumpy and the per-second mean is the number to read), `R_foreign` 0. Phase 2:
  `Oaggr` 41–46 ms, `R_foreign` 43–48 ms per window — the v2/v3 unit test's 50 / 50 split. Nothing left running; the
  `gordion-victim` cgroup removed.
- **Overhead** (as §2.3: the uniform sweep 100–700 req/s with the probe off against the same sweep with probe v4 + both
  perf-stat instances on): `results/20261112-052243-e4-uniform-probe-off` vs `20261112-053118-e4a-uniform-twin`, `analysis/g2_overhead.py`. Per step
  100–700 req/s: served equal, p50 on ÷ off 1.001–1.032 (p90 1.00–1.03), cores busy +0.011–0.038 of 0.45–3.1, CPU per
  request +0.04–0.23 ms (+1–5 %, largest at 100 req/s), per-endpoint floor p50 on ÷ off median 1.004 (IQR 1.002–1.011,
  99 % of 122 endpoints within ±5 %), knee unchanged (500 req/s). The probe's own CPU is 0.21–0.22 cores on CPU 53
  (v3.2: 0.10 — the two perf-stat readers at 10 Hz are the difference). The service-side cost is above §2.3's +0.1–0.6 %:
  with per-task counters the kernel programs the PMU at every switch of the victim's threads, which is what the +1–5 % CPU
  per request is; the latency effect stays within 3 % of the p50 and does not move the knee. Runs that need the counters
  pay it; the probe without perf is the §2.3 figure.

### 6.4 E.4 — runs with probe v4: SMT-sibling aggressor (a) and the cache pair with counters (b)

**Setup** (queue `queue-ge4.sh`, node-0, 05:22–06:16 UTC; the corrected (b) pair `queue-ge4b.sh` 06:27–06:42 UTC; RUNBOOK
§17.1). bigsvc `k123g4` on node-3 CPUs 1,3,5,7 (k = 4, gate cap 3, 2.0 GHz), 100–700 req/s × 45 s after 10 s warm-up, probe
v4 with the sibling filter 29,31,33,35 and both perf-stat instances on every probe-on run, each stressed run after its own
twin in the Addendum D order; dictionary = the pair's twin, truth = step baseline (the twin's p50 at the same step, served
steps only), g for I_g = the probe-off uniform dictionary (`g1_inflation.py` convention). (a) SMT-sibling aggressor:
stress-ng `matrixprod` ×4 pinned to **29,31,33,35 — the siblings of the cpuset, not the cpuset** — in `gordion-aggr`;
first the uniform mix, then memwalk30 at 24 MB. (b) the D.4 A1 pair once more: memwalk30 at 24 MB, stress-ng `--cache`
×4 on 9,11,13,15, counters on the victim cgroup and on the aggressor CPUs. Per-scenario tables
`results/e4/e4a-uniform.md`, `e4a-memwalk30.md`, `e4b2-memwalk30.md` (`analysis/e4_table.py`); the first (b) pair,
whose victim counters are empty (deviation 42), is kept as `e4b-memwalk30-first.md` and its two runs stay in `results/`.

**(a) uniform + sibling aggressor.**

**E.4 (a) uniform + SMT-sibling aggressor** — twin `20261112-053118-e4a-uniform-twin`, stressed `20261112-053923-e4a-uniform-sibling`

| step (req/s) | served tw / st | p50 tw → st (ms) | I_g tw → st | φ | φ_sibling | R_self tw → st (ms/s) | W own-futex / idle (ms/s, st) | truth ext (med) | E (med) | C.1 rows (1/2/3/4/6/7) | sibling-rule rows | victim IPC tw → st | victim LLC miss/req tw → st | aggr-CPU IPC st | aggr-CPU LLC Mmiss/s tw → st |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 0 (100) | 0.99 / 0.99 | 2.04 → 2.05 (×1.00) | 1.04 → 1.09 | 0.00 | 0.94 | 11 → 18 | 782 / 4651 | 0.00 | 0.00 | 0/0/0/0/41/0 | 0/0/0/0/41/0 | — → — | 0 → 0 | 1.16 | 0.05 → 0.17 |
| 1 (200) | 1.02 / 1.02 | 2.34 → 2.35 (×1.01) | 1.04 → 1.08 | 0.00 | 0.93 | 24 → 39 | 1016 / 4086 | 0.01 | 0.04 | 0/0/0/0/41/0 | 0/0/0/0/41/0 | — → — | 0 → 0 | 1.08 | 0.05 → 0.19 |
| 2 (300) | 1.00 / 1.00 | 2.73 → 2.76 (×1.01) | 1.03 → 1.07 | 0.00 | 0.93 | 36 → 50 | 845 / 3780 | 0.01 | 0.01 | 0/0/0/0/41/0 | 0/0/0/0/41/0 | — → — | 0 → 0 | 1.01 | 0.05 → 0.21 |
| 3 (400) | 0.99 / 0.99 | 3.54 → 3.57 (×1.01) | 1.01 → 1.05 | 0.00 | 0.93 | 51 → 76 | 958 / 3353 | 0.01 | 0.02 | 0/0/0/0/41/0 | 0/0/0/0/41/0 | — → — | 0 → 0 | 0.95 | 0.04 → 0.22 |
| 4 (500) | 1.00 / 1.00 | 5.13 → 5.12 (×1.00) | 1.02 → 1.04 | 0.00 | 0.92 | 76 → 96 | 839 / 2979 | 0.00 | 0.00 | 0/0/0/0/41/0 | 0/0/0/0/41/0 | — → — | 0 → 0 | 0.87 | 0.05 → 0.24 |
| 5 (600) | 1.00 / 1.00 | 10.75 → 11.32 (×1.05) | 1.01 → 1.04 | 0.00 | 0.91 | 99 → 119 | 652 / 2435 | 0.05 | 0.07 | 0/3/1/0/31/6 | 0/3/1/0/31/6 | — → — | 0 → 0 | 0.80 | 0.04 → 0.25 |
| 6 (700) | 0.99 / 0.98 | 44.77 → 44.74 (×1.00) | 0.97 → 0.98 | 0.00 | 0.92 | 129 → 138 | 816 / 2028 | — (no truth) | — | — | — | — → — | 0 → 0 | 0.75 | 0.04 → 0.25 |

Served seconds with a truth: 246; C.1 rows 2×3, 3×1, 6×236, 7×6; sibling rule rows 2×3, 3×1, 6×236, 7×6; cells vs truth 99.6 %; ext-truth seconds 0.

**(a) memwalk30 at 24 MB + sibling aggressor.**

**E.4 (a) memwalk30 24 MB + SMT-sibling aggressor** — twin `20261112-054631-e4a-memwalk30-24mb-twin`, stressed `20261112-055442-e4a-memwalk30-24mb-sibling`

| step (req/s) | served tw / st | p50 tw → st (ms) | I_g tw → st | φ | φ_sibling | R_self tw → st (ms/s) | W own-futex / idle (ms/s, st) | truth ext (med) | E (med) | C.1 rows (1/2/3/4/6/7) | sibling-rule rows | victim IPC tw → st | victim LLC miss/req tw → st | aggr-CPU IPC st | aggr-CPU LLC Mmiss/s tw → st |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 0 (100) | 0.99 / 0.99 | 5.21 → 5.26 (×1.01) | 1.36 → 1.41 | 0.00 | 0.95 | 13 → 18 | 900 / 4556 | 0.01 | 0.00 | 0/0/0/0/41/0 | 0/0/0/0/41/0 | — → — | 0 → 0 | 1.14 | 0.04 → 0.19 |
| 1 (200) | 1.02 / 1.02 | 5.85 → 6.12 (×1.05) | 1.33 → 1.39 | 0.00 | 0.94 | 28 → 39 | 981 / 3923 | 0.04 | 0.03 | 0/0/0/0/41/0 | 0/0/0/0/41/0 | — → — | 0 → 0 | 1.04 | 0.05 → 0.24 |
| 2 (300) | 1.00 / 1.00 | 6.08 → 6.39 (×1.05) | 1.29 → 1.34 | 0.00 | 0.94 | 46 → 59 | 770 / 3541 | 0.05 | 0.02 | 0/0/0/0/41/0 | 0/0/0/0/41/0 | — → — | 0 → 0 | 0.96 | 0.05 → 0.26 |
| 3 (400) | 0.99 / 0.99 | 7.04 → 7.53 (×1.07) | 1.26 → 1.31 | 0.00 | 0.95 | 61 → 78 | 846 / 3066 | 0.07 | 0.08 | 0/0/0/0/41/0 | 0/0/0/0/41/0 | — → — | 0 → 0 | 0.89 | 0.06 → 0.29 |
| 4 (500) | 1.00 / 1.00 | 11.77 → 13.16 (×1.12) | 1.21 → 1.25 | 0.00 | 0.94 | 89 → 108 | 665 / 2541 | 0.11 | 0.18 | 0/4/1/0/32/4 | 0/4/1/0/32/4 | — → — | 0 → 0 | 0.79 | 0.05 → 0.32 |
| 5 (600) | 0.97 / 0.95 | 54.21 → 55.47 (×1.02) | 1.19 → 1.23 | 0.00 | 0.95 | 119 → 120 | 708 / 2437 | — (no truth) | — | — | — | — → — | 0 → 0 | 0.76 | 0.03 → 0.34 |
| 6 (700) | 0.84 / 0.87 | 53.41 → 52.62 (×0.99) | 1.20 → 1.22 | 0.00 | 0.94 | 116 → 102 | 1050 / 1791 | — (no truth) | — | — | — | — → — | 0 → 0 | 0.75 | 0.04 → 0.34 |

Served seconds with a truth: 205; C.1 rows 2×4, 3×1, 6×196, 7×4; sibling rule rows 2×4, 3×1, 6×196, 7×4; cells vs truth 100.0 %; ext-truth seconds 0.

**Reading (a).**
- **The signals come out as predicted, the slowdown does not.** φ = 0.00 on every second of both runs (the hog never
  holds a cpuset CPU) and φ_sibling = 0.91–0.95 (the hog holds the four siblings; the rest is the siblings' own idle
  and kernel time). I_g rises on every step — uniform 1.01–1.04 → 1.04–1.12, memwalk30 1.19–1.34 → 1.22–1.42, i.e.
  **+3–8 % CPU per request** under a sibling that runs a matrix product — and R_self rises 10–50 % with it (the
  service's threads run longer, so they queue behind each other a little more). But the p50 moves by ×1.00–1.05 on the
  uniform mix and ×1.01–1.12 on memwalk30 (the ×1.12 at 500 req/s, the knee step); **the twin's truth calls no second
  ext** (0 of 246 and 0 of 205; truth share medians ≤ 0.11) and the slow seconds (68 / 17) are the knee steps' own. A
  hyperthread sibling running an FP/ALU-bound hog costs this Haswell service 3–8 % of its core, not a capacity cut:
  the two hyperthreads share the core's execution ports but the victim's instruction mix (hash loops, a 24 MB walk)
  is not port-bound, and with the gate's three slots on four cores the lost fraction never reaches the queue.
- **Which diagnosis fires under the current rule: row 6, "nothing to explain", on 95–96 % of the seconds** (236 of 246,
  196 of 205), because E stays under its band: there is no residual to attribute. On the knee seconds with E over the
  band the rule reads row 2 (own overload: slot busy ≥ 0.9; 3 + 4 s), row 7 (6 + 4 s: slot busy 0.82–0.89 on the
  uniform run, "I_g between" on memwalk30) and once row 3 on each run. The §0 rule is identical (R_foreign = 0). Cells
  against the truth 99.6 / 100 %. The one wrong row is a row 3 at the knee whose I_g sits above the twin band by the
  sibling's +0.04 — the mechanism is right, the amount (E 0.07–0.18) is the knee's.
- **The proposed sibling rule** (`row_e4` in `verdict.csv`, implemented and scored): slow ∧ E ≫ 0 ∧ φ ≈ 0 ∧ **φ_sibling
  ≫ 0** ∧ I_g ≫ 1 → **row 3, service-time inflation, holder = the sibling's cgroup** (the only row 3 whose target can be
  named: the cgroup on the sibling, read from the same occupancy counters as φ). On these two runs it changed no
  verdict (0 of 451 seconds), because the premise E ≫ 0 never held outside the knee and there the I_g band
  (twin median + max(3 IQR, 0.05) ≈ +0.05) is exactly where the sibling's +0.03–0.08 lands ("I_g between" ×4). Two
  consequences for the rule: (1) φ_sibling is a **presence annotation independent of the verdict** — it read > 0.05 on
  100 % of the stressed seconds and 0.00 on the twins, so "SMT sibling held by `gordion-aggr`" can be reported beside
  the row whatever E is, as `holder_cgroup` is for φ; (2) the I_g test cannot be the trigger for a sibling case at this
  size: catching +0.04 would need a band at +0.03, under the twin-to-twin spread of I_g on this service (§2.6 for the
  store-backed case; here the uniform twin's own steps span 0.97–1.04). The rule therefore stays as written; the
  sibling case is detected by φ_sibling and *sized* by I_g, and a sibling that does cut capacity (a memory-bound or
  port-bound hog on a port-bound victim) would raise E and then take row 3 with the holder named. That run is not
  attempted here ("no third attempts").
- **The waker split on a Go service** (both twins and both stressed runs): 60–100 % of the service threads' sleep time
  per second is ended **by the idle task** (`W_idle` 2.0–4.7 s per second over the ~10 threads: the netpoller's
  `epoll_wait` and parked Ms woken by the network interrupt landing on an idle CPU) and the rest **by our own cgroup
  through a futex** (`W_own-futex` 0.65–1.1 s per second: the runtime handing an M a G, i.e. channel / lock /
  park-unpark); the aggressor, root and other classes wake nothing (≤ 3 ms per second). The split is reported, not
  used by the rule; on this service it says what §2.4 (v) said per request: the Go wait is in the runtime's own
  hand-offs, and I/O completions arrive through the idle CPU's interrupt, which the "idle" class catches without BTF.

**(b) memwalk30 at 24 MB + cache aggressor, counters on both sides.**

**E.4 (b) memwalk30 24 MB + cache aggressor (counters in cgroup mode)** — twin `20261112-062738-e4b2-memwalk30-24mb-twin`, stressed `20261112-063437-e4b2-memwalk30-24mb-cache`

| step (req/s) | served tw / st | p50 tw → st (ms) | I_g tw → st | φ | φ_sibling | R_self tw → st (ms/s) | W own-futex / idle (ms/s, st) | truth ext (med) | E (med) | C.1 rows (1/2/3/4/6/7) | sibling-rule rows | victim IPC tw → st | victim LLC miss/req tw → st | aggr-CPU IPC st | aggr-CPU LLC Mmiss/s tw → st |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 0 (100) | 0.99 / 0.99 | 5.22 → 4.30 (×0.82) | 1.36 → 1.11 | 0.00 | 0.00 | 13 → 15 | 489 / 4550 | 0.00 | 0.00 | 0/0/0/0/41/0 | 0/0/0/0/41/0 | 0.89 → 1.13 | 23847 → 13536 | 2.47 | 0.05 → 6.89 |
| 1 (200) | 1.02 / 1.02 | 5.86 → 4.27 (×0.73) | 1.32 → 1.08 | 0.00 | 0.00 | 30 → 33 | 983 / 4281 | 0.00 | 0.00 | 0/0/0/0/41/0 | 0/0/0/0/41/0 | 0.90 → 1.10 | 20512 → 14447 | 2.43 | 0.05 → 7.12 |
| 2 (300) | 1.00 / 1.00 | 6.08 → 4.28 (×0.70) | 1.29 → 1.07 | 0.00 | 0.00 | 41 → 50 | 936 / 3776 | 0.00 | 0.00 | 0/0/0/0/41/0 | 0/0/0/0/41/0 | 0.93 → 1.11 | 17109 → 13972 | 2.42 | 0.05 → 7.25 |
| 3 (400) | 0.99 / 0.99 | 7.09 → 4.32 (×0.61) | 1.27 → 1.07 | 0.00 | 0.00 | 63 → 70 | 846 / 3520 | 0.00 | 0.00 | 0/0/0/0/41/0 | 0/0/0/0/41/0 | 0.95 → 1.12 | 14684 → 13808 | 2.42 | 0.06 → 7.34 |
| 4 (500) | 1.00 / 1.00 | 11.76 → 4.95 (×0.42) | 1.22 → 1.03 | 0.00 | 0.00 | 86 → 98 | 653 / 3130 | 0.00 | 0.00 | 0/0/0/0/41/0 | 0/0/0/0/41/0 | 0.98 → 1.14 | 12969 → 13586 | 2.39 | 0.05 → 7.48 |
| 5 (600) | 0.97 / 1.00 | 54.03 → 9.62 (×0.18) | 1.20 → 1.05 | 0.00 | 0.00 | 117 → 127 | 353 / 2969 | — (no truth) | — | — | — | 1.00 → 1.14 | 12012 → 13644 | 2.40 | 0.05 → 7.57 |
| 6 (700) | 0.84 / 0.98 | 53.35 → 45.27 (×0.85) | 1.20 → 0.99 | 0.00 | 0.00 | 108 → 152 | 609 / 2025 | — (no truth) | — | — | — | 1.00 → 1.15 | 11848 → 12867 | 2.38 | 0.05 → 7.63 |

Served seconds with a truth: 205; C.1 rows 6×205; sibling rule rows 6×205; cells vs truth 100.0 %; ext-truth seconds 0.

**Reading (b).** The D.4 result reproduces with the counters beside it. With the cache aggressor on 9,11,13,15 the
memwalk30 victim is **faster** on every served step — p50 ×0.82 at 100 req/s, ×0.61 at 400, ×0.42 at 500 — and it no
longer collapses at 600 (served 1.00 against the twin's 0.97, p50 9.6 against 54 ms); φ = φ_sibling = 0, R_foreign 0,
truth share 0 on all 205 seconds, verdict row 6 throughout, cells 100 %. **The counters:** the victim's cycles per request
fall from 13.4 k to 10.4 k (−23 %, the I_g move 1.36 → 1.11 is the same number through the dictionary), its IPC rises
0.89 → 1.13 (+25 %), and its **LLC-load-misses per request halve, 23.8 k → 13.5 k at 100 req/s** (−43 %; −30 % at 200,
−18 % at 300, equal from 400 up), while the LLC-load miss *rate* rises 0.56 → 0.65 — so the victim issues about half the
LLC loads per request (42 k → 21 k) and a larger share of the fewer loads miss. The aggressor side: IPC 2.4 on the four
cores, 7.0–7.6 M LLC-load-misses per second, miss rate 0.074 — a cache stressor with a 7 % miss rate is not a thrasher
on this 35 MB LLC; it keeps the uncore busy and occupies some of the LLC. The speed-up therefore comes with **fewer
L2 misses in the victim, not more**: whatever the mechanism (the §5 controls ruled the idle states out), it is upstream
of the LLC — the memwalk's 24 MB stream is being served from L2 or by prefetch more often when the neighbour keeps the
uncore and the LLC's traffic up. A hypothesis consistent with all four counters is the LLC's adaptive policies
(prefetch throttling / replacement) treating the victim's stream differently under load; it is not tested here (no
third attempt). What §4 / §5 state stands and is now quantified: on this hardware a busy memory-side neighbour makes a
memory-bound victim faster, I_g falls below 1 (0.84 median on the cache run against the uniform g), and row 3 has no
positive case; the counter to put next to the 18 % speed-up is "half the LLC loads per request at the same work".

### 6.5 Deviations (Addendum E; numbering continues §"Deviations")

41. **Counters per cgroup, not per thread** (E.3 c): `perf stat` in counting mode on the victim's cgroup (system-wide on
    the cpuset) and on the aggressor CPUs; `--per-thread` would miss the threads the Go runtime creates under load and
    the second event group would multiplex the four generic counters. Per window, not per thread per window.
42. **The first E.4 queue's victim counters are empty.** `probe-ctl.sh perf-start` attached `-p <container init pid>`;
    the service is that init's already-running child, so every victim interval read "not counted" on the six probe-on
    runs (aggressor-CPU counters complete). Fixed to cgroup mode (`-G`, v4.1; verified on the idle pod: the cgroup and
    the pid-list forms agree), and the (b) pair re-run once (`e4b2-*`, 06:27–06:42 UTC). The (a) pairs were not re-run:
    their claim rests on φ_sibling, I_g and the twin truth, which they have; the sibling's own counters (aggressor
    CPUs) are complete.
43. **No BPF counters, no libbpf program.** E.3 (c) asked for per-task counters "at switch" if E.2 allowed it; E.2
    showed no clang / bpftool / libbpf ≥ 1.0 on the node and bpftrace 0.14 cannot read a counter at a tracepoint, so the
    counters come from `perf stat` at 100 ms (§6.3). The probe's own cost with the two perf readers is 0.21 cores and
    +1–5 % CPU per request on the victim (§6.3), above the §2.3 figure of the probe alone.
44. **The sibling aggressor produced no slow second** (p50 ×1.00–1.12), so the proposed sibling rule could not be
    exercised on a positive case; it is implemented, scored (0 changed verdicts) and argued from the signals (§6.4).
    No second aggressor type was tried on the siblings (the handoff's "no third attempts" read as one attempt per
    scenario).
45. **Overhead pair at 100–700 req/s** (7 steps) instead of §2.3's 40–900 (12 steps); the knee (500 req/s) and the
    per-endpoint floors are inside the range, the collapse step is not.
46. **Template shipping mishap, no data effect.** The BOM-stripping `sed` used to ship files to node-0 removed the
    leading `// G` of the v4 template once; the first unit-test attempt failed to parse and nothing was recorded; fixed
    by hand before any run (RUNBOOK §17.1).

## 7. Addendum F — two runs for the tail: per-request run-queue delay by holder, softirq on the victim's core (Nov 12)

Spec: `PHASEG-HANDOFF.md` Addendum F. Cluster clnode170, node-3 (§2.1 layout), namespace `sn`, image `sn-gordion:g6`
(the recorder emits `rawe`, the completion time of every sample, and `unique-id-service` carries the recorder); probe
**v5**; each stressed run after its own twin; all four runs between 07:51 and 08:20 UTC (queue `queue-gf.sh`; a first
pass of the same queue at 07:39 ran on image g5 by mistake and was discarded, deviation 47). Environment changes and the
restore are in stage-3 `RUNBOOK.md` §17.1. Files: `results/f/` (laptop) and `results/g-final/f/`.

### 7.1 F.1 / F.2 — probe v5: per-resume records and softirq on the cpuset

- **F.1, per-resume records.** In the resume branch of `sched_switch` the probe now prints one record per resume of a
  service thread: `{"r": tid, "ns", "d": the runnable gap, "a"/"s"/"o": its time-share split by holder (aggressor /
  own cgroup / root + other), "c": cpu, "w": 1 after a sleep, 0 after a preemption}` (`"h"`, `"m": 1` for a thread that
  resumed on another CPU: last-holder credit). `probe.py` routes them to `<run>/probe-resume.jsonl` with the unix-ms
  `t`. The join (`analysis/g7_tail.py`): a request is the recorder's sample (`raw`, `rawp`, `rawq`, `rawt`, `rawe`);
  its span is [end − total, end] on its thread; the resumes inside the span are its run-queue delays by holder, split
  by kind (after a preemption → inside processing; after a sleep → inside a store call); the thread's last post-sleep
  resume before the span (within 20 ms) is the **arrival wake-up** — the delay between the request's packet waking the
  thread and the thread running, which the recorder's clock (tap at message begin) does not contain. Two streams per
  request: *server* = total (the recorder's), *arrival* = arrival delay + total.
- **F.2, softirq.** `irq:softirq_raise / softirq_entry / softirq_exit` on the cpuset's CPUs: time in softirq per
  window by vector (NET_RX, TIMER, RCU, SCHED, the rest), NET_RX entries, raise → entry latency per vector and by the
  class of the task running at the raise (own / aggressor / root / other / idle), and — for every wake-up of a service
  thread that happens inside a softirq on the cpuset — the delay from that softirq's raise to the wake-up, credited to
  the class running at the raise (`k` records; `g7_softirq.py` per step). The spec's "raise → exit" is measured to the
  wake-up (the exit follows within the handler; the wake-up is the event that matters to the request).
- **Cost with the export on** (unique-id-service, 4 steps, probe off `…-075111-f-a-uid-off` vs on `…-075458-f-a-uid-twin`,
  `g2_overhead.py`): served equal; p50 of the 25–35 µs requests 1.00 / 0.80 / 0.79 / 1.00 (the µs stream's noise; p90
  equal); **cores busy +0.003–0.008 of 0.014–0.046 and CPU per request 0.117 → 0.146, 0.106 → 0.126, 0.099 → 0.116,
  0.095 → 0.111 ms (+17–25 %)** — every request is two switches, a futex and a wake-up, each now a BPF program plus a
  printf, on a service whose request is 35 µs of work. The probe's own CPU: 0.37–0.42 cores on unique-id (~300
  resumes/s), **0.45–0.79 cores on post-storage's served steps** (0.9–3.5 k resumes/s plus the per-second print of a
  30 000-key per-thread map), **1.0 core at the collapse steps** (the export saturates CPU 53 there; records past that
  point are not complete, which is why §7.4 reads steps 0–2). On the Go services of §2–§6 (≤ 10 threads, 10–50
  resumes/s) the same export would cost a fraction of this; it was not measured there.

### 7.2 F.3 — Run A, the positive case: unique-id-service + a 20 % duty-cycled hog

**Setup.** `unique-id-service` (C++ Thrift, no store, no downstream; one thread per connection, thread = request) as the
SUT on node-3 CPU 1 (`hostPID: true`, image g6), driven through the compose chain (hrgen `compose=0.6, home=0.3,
utl=0.1`; it could not be driven alone — it has no HTTP front — and does not need to be: every compose calls it once),
offered 200 / 400 / 600 / 800 req/s → 120 / 240 / 364 / 478 unique-id req/s, 45 s each; aggressor `stress-ng --cpu 1
--cpu-method matrixprod --cpu-load 20 --cpu-load-slice 10` on CPU 1 in `gordion-aggr` (`stressor.sh METHOD=duty20`):
the probe reads **φ = 0.18** (occupancy 18 ms per 100 ms window, R_foreign 11–18 ms/s), bursts of 10 ms. No tuning pass
was used: the first setting met the target on the arrival stream (deviation 50).

**Twin.** p50 35 / 27 / 25 / 24 µs, p95 57 / 47 / 40 / 38 µs on the recorder's stream; the arrival stream adds a 4–7 µs
median wake-up (p95 of the arrival delay 12–51 µs, 1.3–1.9 % of the requests above 0.1 ms: the kernel's own).

**Tail composition, requests ≥ the step's p95, twin and stressed, medians and means** (`results/f/f-A-tail.md`;
columns in ms: total, handler on-CPU, run-queue delay behind the aggressor's cgroup / own threads / others, softirq,
blocked (0 here), unattributed; then the growth under the hog and its shares):

twin: 20261112-075458-f-a-uid-twin: 54315 requests on 72 threads; 57949 resumes, 50 softirq wake-ups; requests with a resume in the span or at arrival: 99.9 %
stressed: 20261112-075852-f-a-uid-duty20: 54316 requests on 67 threads; 58132 resumes, 32 softirq wake-ups; requests with a resume in the span or at arrival: 99.8 %

**A — stream server** (tail = requests ≥ the step's p95 of this stream)

| step (req/s) | variant | n tail / n | p50 / p95 (ms) | total | oncpu | rq_foreign | rq_self | rq_other | softirq | blocked | unattributed | softirq by aggr | rq at arrival |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 0 (200) | twin med | 274 / 5480 | 0.035 / 0.057 | 0.061 | 0.061 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 0.007 |
| 0 (200) | twin mean | 274 / 5480 | 0.035 / 0.057 | 0.065 | 0.065 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 0.013 |
| 0 (200) | stressed med | 275 / 5484 | 0.031 / 0.059 | 0.061 | 0.061 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 0.008 |
| 0 (200) | stressed mean | 275 / 5484 | 0.031 / 0.059 | 0.066 | 0.065 | 0.000 | 0.001 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 0.114 |
| 0 | growth med |  | p95 ×1.03, p50 ×0.88 | -0.001 | -0.001 | +0.000 | +0.000 | +0.000 | +0.000 | +0.000 | +0.000 | +0.000 | +0.001 |
| 0 | share med |  |  | +1.00 | +1.01 | -0.00 | -0.00 | -0.00 | -0.00 | -0.00 | -0.00 | -0.00 | -1.35 |
| 0 | growth mean |  | p95 ×1.03, p50 ×0.88 | +0.000 | -0.000 | +0.000 | +0.001 | +0.000 | +0.000 | +0.000 | +0.000 | +0.000 | +0.100 |
| 0 | share mean |  |  | +1.00 | -0.82 | +0.00 | +1.82 | +0.00 | +0.00 | +0.00 | +0.00 | +0.00 | +207.92 |
| 1 (400) | twin med | 541 / 10807 | 0.027 / 0.047 | 0.052 | 0.052 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 0.006 |
| 1 (400) | twin mean | 541 / 10807 | 0.027 / 0.047 | 0.056 | 0.055 | 0.000 | 0.001 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 0.010 |
| 1 (400) | stressed med | 541 / 10807 | 0.027 / 0.049 | 0.055 | 0.055 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 0.007 |
| 1 (400) | stressed mean | 541 / 10807 | 0.027 / 0.049 | 0.062 | 0.057 | 0.004 | 0.001 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 0.157 |
| 1 | growth med |  | p95 ×1.03, p50 ×0.98 | +0.003 | +0.003 | +0.000 | +0.000 | +0.000 | +0.000 | +0.000 | +0.000 | +0.000 | +0.001 |
| 1 | share med |  |  | +1.00 | +0.99 | +0.00 | +0.00 | +0.00 | +0.00 | +0.00 | +0.00 | +0.00 | +0.32 |
| 1 | growth mean |  | p95 ×1.03, p50 ×0.98 | +0.007 | +0.002 | +0.004 | +0.001 | +0.000 | +0.000 | +0.000 | +0.000 | +0.000 | +0.147 |
| 1 | share mean |  |  | +1.00 | +0.28 | +0.63 | +0.08 | +0.01 | +0.00 | +0.00 | +0.00 | +0.00 | +21.86 |
| 2 (600) | twin med | 822 / 16425 | 0.025 / 0.040 | 0.046 | 0.046 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 0.005 |
| 2 (600) | twin mean | 822 / 16425 | 0.025 / 0.040 | 0.050 | 0.049 | 0.000 | 0.001 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 0.015 |
| 2 (600) | stressed med | 822 / 16428 | 0.025 / 0.041 | 0.047 | 0.047 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 0.006 |
| 2 (600) | stressed mean | 822 / 16428 | 0.025 / 0.041 | 0.050 | 0.048 | 0.000 | 0.001 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 0.152 |
| 2 | growth med |  | p95 ×1.01, p50 ×1.00 | +0.000 | +0.000 | +0.000 | +0.000 | +0.000 | +0.000 | +0.000 | +0.000 | +0.000 | +0.001 |
| 2 | share med |  |  | +1.00 | +1.00 | +0.00 | +0.00 | +0.00 | +0.00 | +0.00 | +0.00 | +0.00 | +1.41 |
| 2 | growth mean |  | p95 ×1.01, p50 ×1.00 | -0.000 | -0.000 | +0.000 | +0.000 | +0.000 | +0.000 | +0.000 | -0.000 | +0.000 | +0.138 |
| 2 | share mean |  |  | +1.00 | +5.56 | -0.00 | -4.40 | -0.16 | -0.00 | -0.00 | +0.00 | -0.00 | -2621.31 |
| 3 (800) | twin med | 1081 / 21603 | 0.024 / 0.038 | 0.043 | 0.043 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 0.005 |
| 3 (800) | twin mean | 1081 / 21603 | 0.024 / 0.038 | 0.046 | 0.045 | 0.000 | 0.001 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 0.013 |
| 3 (800) | stressed med | 1080 / 21597 | 0.024 / 0.038 | 0.044 | 0.043 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 0.005 |
| 3 (800) | stressed mean | 1080 / 21597 | 0.024 / 0.038 | 0.047 | 0.046 | 0.000 | 0.002 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 0.063 |
| 3 | growth med |  | p95 ×1.00, p50 ×1.00 | +0.000 | +0.000 | +0.000 | +0.000 | +0.000 | +0.000 | +0.000 | +0.000 | +0.000 | +0.000 |
| 3 | share med |  |  | +1.00 | +0.51 | +0.00 | +0.00 | +0.00 | +0.00 | +0.00 | +0.00 | +0.00 | +0.59 |
| 3 | growth mean |  | p95 ×1.00, p50 ×1.00 | +0.001 | +0.000 | +0.000 | +0.001 | +0.000 | +0.000 | +0.000 | -0.000 | +0.000 | +0.051 |
| 3 | share mean |  |  | +1.00 | +0.25 | +0.00 | +0.85 | +0.00 | +0.00 | +0.00 | -0.00 | +0.00 | +49.37 |

**A — stream arrival** (tail = requests ≥ the step's p95 of this stream)

| step (req/s) | variant | n tail / n | p50 / p95 (ms) | total | oncpu | rq_foreign | rq_self | rq_other | softirq | blocked | unattributed | softirq by aggr | rq at arrival |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 0 (200) | twin med | 274 / 5480 | 0.040 / 0.072 | 0.093 | 0.034 | 0.000 | 0.053 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 0.059 |
| 0 (200) | twin mean | 274 / 5480 | 0.040 / 0.072 | 0.136 | 0.047 | 0.000 | 0.058 | 0.031 | 0.000 | 0.000 | 0.000 | 0.000 | 0.089 |
| 0 (200) | stressed med | 275 / 5484 | 0.041 / 0.149 | 1.396 | 0.027 | 1.302 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 1.316 |
| 0 (200) | stressed mean | 275 / 5484 | 0.041 / 0.149 | 1.843 | 0.032 | 1.735 | 0.052 | 0.024 | 0.000 | 0.000 | 0.000 | 0.000 | 1.811 |
| 0 | growth med |  | p95 ×2.09, p50 ×1.01 | +1.303 | -0.007 | +1.302 | -0.053 | +0.000 | +0.000 | +0.000 | +0.000 | +0.000 | +1.257 |
| 0 | share med |  |  | +1.00 | -0.01 | +1.00 | -0.04 | +0.00 | +0.00 | +0.00 | +0.00 | +0.00 | +0.96 |
| 0 | growth mean |  | p95 ×2.09, p50 ×1.01 | +1.707 | -0.015 | +1.735 | -0.006 | -0.007 | -0.000 | +0.000 | +0.000 | +0.000 | +1.722 |
| 0 | share mean |  |  | +1.00 | -0.01 | +1.02 | -0.00 | -0.00 | -0.00 | +0.00 | +0.00 | +0.00 | +1.01 |
| 1 (400) | twin med | 541 / 10807 | 0.034 / 0.070 | 0.110 | 0.024 | 0.000 | 0.081 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 0.082 |
| 1 (400) | twin mean | 541 / 10807 | 0.034 / 0.070 | 0.126 | 0.032 | 0.000 | 0.082 | 0.011 | 0.000 | 0.000 | 0.000 | 0.000 | 0.093 |
| 1 (400) | stressed med | 541 / 10807 | 0.034 / 0.133 | 0.576 | 0.026 | 0.512 | 0.054 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 0.538 |
| 1 (400) | stressed mean | 541 / 10807 | 0.034 / 0.133 | 1.415 | 0.030 | 1.312 | 0.067 | 0.007 | 0.000 | 0.000 | 0.000 | 0.000 | 1.380 |
| 1 | growth med |  | p95 ×1.91, p50 ×0.99 | +0.466 | +0.002 | +0.512 | -0.027 | +0.000 | +0.000 | +0.000 | +0.000 | +0.000 | +0.455 |
| 1 | share med |  |  | +1.00 | +0.00 | +1.10 | -0.06 | +0.00 | +0.00 | +0.00 | +0.00 | +0.00 | +0.98 |
| 1 | growth mean |  | p95 ×1.91, p50 ×0.99 | +1.289 | -0.003 | +1.312 | -0.015 | -0.005 | -0.000 | +0.000 | +0.000 | +0.000 | +1.288 |
| 1 | share mean |  |  | +1.00 | -0.00 | +1.02 | -0.01 | -0.00 | -0.00 | +0.00 | +0.00 | +0.00 | +1.00 |
| 2 (600) | twin med | 822 / 16425 | 0.030 / 0.079 | 0.114 | 0.023 | 0.000 | 0.088 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 0.090 |
| 2 (600) | twin mean | 822 / 16425 | 0.030 / 0.079 | 0.135 | 0.028 | 0.000 | 0.091 | 0.016 | 0.000 | 0.000 | 0.000 | 0.000 | 0.105 |
| 2 (600) | stressed med | 822 / 16428 | 0.031 / 0.130 | 0.208 | 0.025 | 0.009 | 0.102 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 0.179 |
| 2 (600) | stressed mean | 822 / 16428 | 0.031 / 0.130 | 1.108 | 0.028 | 1.008 | 0.068 | 0.004 | 0.000 | 0.000 | 0.000 | 0.000 | 1.079 |
| 2 | growth med |  | p95 ×1.65, p50 ×1.03 | +0.094 | +0.002 | +0.009 | +0.014 | +0.000 | +0.000 | +0.000 | +0.000 | +0.000 | +0.090 |
| 2 | share med |  |  | +1.00 | +0.02 | +0.09 | +0.15 | +0.00 | +0.00 | +0.00 | +0.00 | +0.00 | +0.95 |
| 2 | growth mean |  | p95 ×1.65, p50 ×1.03 | +0.973 | -0.000 | +1.008 | -0.022 | -0.012 | -0.000 | +0.000 | +0.000 | +0.000 | +0.974 |
| 2 | share mean |  |  | +1.00 | -0.00 | +1.04 | -0.02 | -0.01 | -0.00 | +0.00 | +0.00 | +0.00 | +1.00 |
| 3 (800) | twin med | 1081 / 21603 | 0.029 / 0.069 | 0.101 | 0.022 | 0.000 | 0.076 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 0.076 |
| 3 (800) | twin mean | 1081 / 21603 | 0.029 / 0.069 | 0.118 | 0.027 | 0.000 | 0.081 | 0.009 | 0.000 | 0.000 | 0.000 | 0.000 | 0.090 |
| 3 (800) | stressed med | 1080 / 21597 | 0.029 / 0.121 | 0.161 | 0.025 | 0.007 | 0.100 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 0.134 |
| 3 (800) | stressed mean | 1080 / 21597 | 0.029 / 0.121 | 0.747 | 0.027 | 0.641 | 0.074 | 0.004 | 0.000 | 0.000 | 0.000 | 0.000 | 0.718 |
| 3 | growth med |  | p95 ×1.76, p50 ×1.03 | +0.060 | +0.002 | +0.007 | +0.024 | +0.000 | +0.000 | +0.000 | +0.000 | +0.000 | +0.058 |
| 3 | share med |  |  | +1.00 | +0.04 | +0.12 | +0.40 | +0.00 | +0.00 | +0.00 | +0.00 | +0.00 | +0.96 |
| 3 | growth mean |  | p95 ×1.76, p50 ×1.03 | +0.628 | -0.000 | +0.641 | -0.007 | -0.005 | -0.000 | +0.000 | +0.000 | +0.000 | +0.628 |
| 3 | share mean |  |  | +1.00 | -0.00 | +1.02 | -0.01 | -0.01 | -0.00 | +0.00 | +0.00 | +0.00 | +1.00 |

**Reading.**
- **On the recorder's stream nothing moves**: p50 ×0.88–1.00, p95 ×1.00–1.03, the tail request is 43–61 µs of on-CPU
  time in both runs and the growth is 0–3 µs. The request is never preempted (rq inside the span 0) and the server's
  clock starts after the wake-up.
- **On the arrival stream the target is met at every step: p50 ×0.99–1.03, p95 ×2.09 / 1.91 / 1.65 / 1.76, p99 ×25
  (0.14 → 3.6 ms at 200 req/s).** The tail request (median) goes from 0.093 to 1.396 ms at step 0 and **the growth is
  the run-queue delay behind the aggressor's cgroup: +1.302 of +1.303 ms (share 1.00), means +1.735 of +1.707**; at
  steps 1–3 the median growth shrinks (+0.47 / +0.09 / +0.06 ms, the tail's p95 cut moves into the kernel's own
  wake-up noise as the rate rises) while the means stay the aggressor's (+1.29 / +0.97 / +0.63 ms, rq_foreign share
  1.02 / 1.04 / 1.02). On-CPU, own-thread and other holders do not change (±0.05 ms); softirq, blocked and the
  unattributed remainder are 0.
- **How a 20 % hog makes a 3 % tail.** Arrivals delayed by more than 0.1 ms: 1.3–1.9 % on the twin, 4.7–6.1 % under
  the hog, of which 1.9–3.4 % behind the hog; the delayed ones wait up to 6.3 ms (the rest of the hog's 10 ms slice).
  The other ~15 % of arrivals that land in a burst preempt the hog within microseconds (CFS wake-up preemption: the
  sleeping service thread's vruntime is far below the hog's), so the hog costs the p50 nothing and the tail its slice.
- **Window diagnoses for the run** (`g2_verdict.py`, dictionary = the twin, k = 1): **C.1 rule row 6 on 164 of 164
  served seconds** (φ 0.18 ≫ 0.05, but E ≈ 0: the p50 did not move, nothing to explain at the window's p50; cells vs
  truth 100 %); **§0 rule row 1 on 128 of 164** (R_foreign 15 ms/s above the 10 ms/s floor — the presence test fires on
  a hog that does not move the p50). The per-second scorer is a p50 instrument (§2.9); this hog is a p95/p99 effect on
  the arrival stream, which the recorder's stream does not carry. What it would take at the window level: the
  arrival-inclusive stream (the probe's per-resume records joined as here) scored at p95, or the kernel's per-task
  `sched_latency` tail — both are per-request measurements, not a per-second decomposition.

### 7.3 F.4 — Run B, the post-storage pair with F.1 and F.2 on

**Setup.** The (v) pair-4 configuration: `post-storage-service` on node-3 CPU 1 (k = 1, `hostPID: true`, image g6),
compose-heavy mix, 200 → 2000 req/s in 10 × 45 s; stressed = one `matrixprod` hog on CPU 1 in `gordion-aggr`
(φ 0.50, p10–p90 0.23–0.75; R_foreign 67 ms/s median). Served: twin 1.00 / 1.00 / 1.00 / 0.98 on steps 0–3, both
runs collapse from step 4 (served ≤ 0.8, truth undefined). **The twin's step 3 (800 req/s) carries a store stall**
(p95 57 ms, mean blocked 234 ms; the stressed run's step 3 reads p95 24 ms), so the growth is read on steps 0–2; step 3
is shown, not read (deviation 51). Both runs: 309–314 k requests on 30–34 k threads, 0.87–0.88 M resumes; every tail
request has a resume inside its span and blocked time.

**Tail composition, requests ≥ the step's p95** (`results/f/f-B-tail.md`, steps 0–3; blocked = the store calls minus
the wake-up delays the probe saw inside them):

twin: 20261112-080249-f-b-ps-twin: 313782 requests on 30386 threads; 879944 resumes, 600 softirq wake-ups; requests with a resume in the span or at arrival: 39.5 %
stressed: 20261112-081133-f-b-ps-stress1: 308830 requests on 33545 threads; 872629 resumes, 356 softirq wake-ups; requests with a resume in the span or at arrival: 43.3 %

**B — stream server** (tail = requests ≥ the step's p95 of this stream)

| step (req/s) | variant | n tail / n | p50 / p95 (ms) | total | oncpu | rq_foreign | rq_self | rq_other | softirq | blocked | unattributed | softirq by aggr | rq at arrival |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 0 (200) | twin med | 457 / 9124 | 0.655 / 5.041 | 5.888 | 1.549 | 0.000 | 0.591 | 0.000 | 0.000 | 3.598 | 0.000 | 0.000 | 0.005 |
| 0 (200) | twin mean | 457 / 9124 | 0.655 / 5.041 | 72.990 | 1.450 | 0.000 | 0.934 | 0.001 | 0.000 | 70.605 | 0.000 | 0.000 | 0.107 |
| 0 (200) | stressed med | 457 / 9121 | 0.648 / 5.546 | 6.937 | 1.468 | 0.011 | 0.834 | 0.000 | 0.000 | 4.188 | 0.000 | 0.000 | 0.007 |
| 0 (200) | stressed mean | 457 / 9121 | 0.648 / 5.546 | 32.405 | 1.354 | 0.624 | 1.146 | 0.003 | 0.000 | 29.296 | 0.000 | 0.000 | 0.123 |
| 0 | growth med |  | p95 ×1.10, p50 ×0.99 | +1.050 | -0.081 | +0.011 | +0.244 | +0.000 | +0.000 | +0.590 | +0.000 | +0.000 | +0.002 |
| 0 | share med |  |  | +1.00 | -0.08 | +0.01 | +0.23 | +0.00 | +0.00 | +0.56 | +0.00 | +0.00 | +0.00 |
| 0 | growth mean |  | p95 ×1.10, p50 ×0.99 | -40.586 | -0.096 | +0.624 | +0.212 | +0.002 | -0.000 | -41.309 | +0.000 | +0.000 | +0.016 |
| 0 | share mean |  |  | +1.00 | +0.00 | -0.02 | -0.01 | -0.00 | +0.00 | +1.02 | -0.00 | -0.00 | -0.00 |
| 1 (400) | twin med | 904 / 18080 | 0.745 / 6.404 | 7.792 | 1.429 | 0.000 | 2.406 | 0.000 | 0.000 | 4.126 | 0.000 | 0.000 | 0.028 |
| 1 (400) | twin mean | 904 / 18080 | 0.745 / 6.404 | 10.082 | 1.314 | 0.000 | 2.922 | 0.002 | 0.000 | 5.850 | 0.000 | 0.000 | 0.307 |
| 1 (400) | stressed med | 904 / 18064 | 0.825 / 7.610 | 10.007 | 1.419 | 0.011 | 2.788 | 0.000 | 0.000 | 4.872 | 0.000 | 0.000 | 0.015 |
| 1 (400) | stressed mean | 904 / 18064 | 0.825 / 7.610 | 36.739 | 1.302 | 0.869 | 4.628 | 0.006 | 0.000 | 29.984 | 0.000 | 0.000 | 0.779 |
| 1 | growth med |  | p95 ×1.19, p50 ×1.11 | +2.215 | -0.010 | +0.011 | +0.382 | +0.000 | +0.000 | +0.746 | +0.000 | +0.000 | -0.013 |
| 1 | share med |  |  | +1.00 | -0.00 | +0.00 | +0.17 | +0.00 | +0.00 | +0.34 | +0.00 | +0.00 | -0.01 |
| 1 | growth mean |  | p95 ×1.19, p50 ×1.11 | +26.657 | -0.012 | +0.869 | +1.705 | +0.004 | -0.000 | +24.134 | +0.000 | +0.000 | +0.472 |
| 1 | share mean |  |  | +1.00 | -0.00 | +0.03 | +0.06 | +0.00 | -0.00 | +0.91 | +0.00 | +0.00 | +0.02 |
| 2 (600) | twin med | 1363 / 27253 | 1.033 / 8.674 | 11.249 | 1.287 | 0.000 | 5.393 | 0.000 | 0.000 | 4.762 | 0.000 | 0.000 | 0.160 |
| 2 (600) | twin mean | 1363 / 27253 | 1.033 / 8.674 | 14.998 | 1.246 | 0.000 | 5.809 | 0.005 | 0.000 | 7.953 | 0.000 | 0.000 | 0.781 |
| 2 (600) | stressed med | 1363 / 27252 | 1.172 / 11.429 | 15.843 | 1.230 | 0.393 | 8.115 | 0.007 | 0.000 | 5.934 | 0.000 | 0.000 | 0.506 |
| 2 (600) | stressed mean | 1363 / 27252 | 1.172 / 11.429 | 27.784 | 1.222 | 0.974 | 11.244 | 0.025 | 0.000 | 14.455 | 0.000 | 0.000 | 2.052 |
| 2 | growth med |  | p95 ×1.32, p50 ×1.13 | +4.594 | -0.057 | +0.393 | +2.722 | +0.007 | +0.000 | +1.171 | +0.000 | +0.000 | +0.346 |
| 2 | share med |  |  | +1.00 | -0.01 | +0.09 | +0.59 | +0.00 | +0.00 | +0.25 | +0.00 | +0.00 | +0.08 |
| 2 | growth mean |  | p95 ×1.32, p50 ×1.13 | +12.786 | -0.024 | +0.974 | +5.434 | +0.020 | +0.000 | +6.502 | +0.000 | +0.000 | +1.271 |
| 2 | share mean |  |  | +1.00 | -0.00 | +0.08 | +0.43 | +0.00 | +0.00 | +0.51 | +0.00 | +0.00 | +0.10 |
| 3 (800) | twin med | 1791 / 35806 | 2.195 / 57.426 | 110.772 | 1.061 | 0.000 | 39.805 | 0.030 | 0.000 | 54.756 | 0.000 | 0.000 | 1.429 |
| 3 (800) | twin mean | 1791 / 35806 | 2.195 / 57.426 | 286.163 | 10.855 | 0.000 | 41.238 | 0.049 | 0.000 | 234.225 | 0.000 | 0.000 | 8.250 |
| 3 (800) | stressed med | 1791 / 35801 | 2.229 / 24.300 | 40.488 | 1.119 | 1.320 | 24.100 | 0.048 | 0.000 | 11.215 | 0.000 | 0.000 | 3.606 |
| 3 (800) | stressed mean | 1791 / 35801 | 2.229 / 24.300 | 60.837 | 1.409 | 1.850 | 28.062 | 0.072 | 0.000 | 29.702 | 0.000 | 0.000 | 6.694 |
| 3 | growth med |  | p95 ×0.42, p50 ×1.02 | -70.284 | +0.058 | +1.320 | -15.706 | +0.018 | +0.000 | -43.541 | +0.000 | +0.000 | +2.178 |
| 3 | share med |  |  | +1.00 | -0.00 | -0.02 | +0.22 | -0.00 | -0.00 | +0.62 | -0.00 | -0.00 | -0.03 |
| 3 | growth mean |  | p95 ×0.42, p50 ×1.02 | -225.325 | -9.446 | +1.850 | -13.176 | +0.023 | -0.000 | -204.523 | -0.000 | +0.000 | -1.556 |
| 3 | share mean |  |  | +1.00 | +0.04 | -0.01 | +0.06 | -0.00 | +0.00 | +0.91 | +0.00 | -0.00 | +0.01 |

**B — stream arrival** (tail = requests ≥ the step's p95 of this stream)

| step (req/s) | variant | n tail / n | p50 / p95 (ms) | total | oncpu | rq_foreign | rq_self | rq_other | softirq | blocked | unattributed | softirq by aggr | rq at arrival |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 0 (200) | twin med | 457 / 9124 | 0.733 / 5.106 | 6.015 | 1.535 | 0.000 | 0.841 | 0.000 | 0.000 | 3.598 | 0.000 | 0.000 | 0.006 |
| 0 (200) | twin mean | 457 / 9124 | 0.733 / 5.106 | 73.121 | 1.441 | 0.000 | 1.108 | 0.002 | 0.000 | 70.570 | 0.000 | 0.000 | 0.152 |
| 0 (200) | stressed med | 457 / 9121 | 0.754 / 5.699 | 7.021 | 1.456 | 0.018 | 0.962 | 0.000 | 0.000 | 4.153 | 0.000 | 0.000 | 0.007 |
| 0 (200) | stressed mean | 457 / 9121 | 0.754 / 5.699 | 32.562 | 1.334 | 0.709 | 1.307 | 0.002 | 0.000 | 29.229 | 0.000 | 0.000 | 0.253 |
| 0 | growth med |  | p95 ×1.12, p50 ×1.03 | +1.006 | -0.079 | +0.018 | +0.121 | +0.000 | +0.000 | +0.555 | +0.000 | +0.000 | +0.002 |
| 0 | share med |  |  | +1.00 | -0.08 | +0.02 | +0.12 | +0.00 | +0.00 | +0.55 | +0.00 | +0.00 | +0.00 |
| 0 | growth mean |  | p95 ×1.12, p50 ×1.03 | -40.559 | -0.108 | +0.709 | +0.198 | +0.001 | -0.000 | -41.342 | -0.000 | +0.000 | +0.101 |
| 0 | share mean |  |  | +1.00 | +0.00 | -0.02 | -0.00 | -0.00 | +0.00 | +1.02 | +0.00 | -0.00 | -0.00 |
| 1 (400) | twin med | 904 / 18080 | 1.072 / 6.662 | 8.238 | 1.416 | 0.000 | 2.959 | 0.000 | 0.000 | 3.973 | 0.000 | 0.000 | 0.096 |
| 1 (400) | twin mean | 904 / 18080 | 1.072 / 6.662 | 10.475 | 1.300 | 0.000 | 3.420 | 0.002 | 0.000 | 5.757 | 0.000 | 0.000 | 0.473 |
| 1 (400) | stressed med | 904 / 18064 | 1.141 / 8.094 | 10.710 | 1.363 | 0.017 | 3.636 | 0.000 | 0.000 | 4.523 | 0.000 | 0.000 | 0.114 |
| 1 (400) | stressed mean | 904 / 18064 | 1.141 / 8.094 | 37.826 | 1.235 | 1.042 | 5.891 | 0.008 | 0.000 | 29.695 | 0.000 | 0.000 | 1.369 |
| 1 | growth med |  | p95 ×1.21, p50 ×1.06 | +2.472 | -0.054 | +0.017 | +0.677 | +0.000 | +0.000 | +0.549 | +0.000 | +0.000 | +0.017 |
| 1 | share med |  |  | +1.00 | -0.02 | +0.01 | +0.27 | +0.00 | +0.00 | +0.22 | +0.00 | +0.00 | +0.01 |
| 1 | growth mean |  | p95 ×1.21, p50 ×1.06 | +27.351 | -0.065 | +1.042 | +2.470 | +0.007 | -0.000 | +23.938 | +0.000 | +0.000 | +0.896 |
| 1 | share mean |  |  | +1.00 | -0.00 | +0.04 | +0.09 | +0.00 | -0.00 | +0.88 | +0.00 | +0.00 | +0.03 |
| 2 (600) | twin med | 1363 / 27253 | 1.363 / 9.384 | 12.218 | 1.255 | 0.000 | 6.793 | 0.000 | 0.000 | 4.398 | 0.000 | 0.000 | 0.439 |
| 2 (600) | twin mean | 1363 / 27253 | 1.363 / 9.384 | 15.951 | 1.218 | 0.000 | 7.006 | 0.007 | 0.000 | 7.733 | 0.000 | 0.000 | 1.144 |
| 2 (600) | stressed med | 1363 / 27252 | 1.611 / 12.816 | 18.007 | 1.158 | 0.659 | 10.682 | 0.008 | 0.000 | 5.057 | 0.000 | 0.000 | 1.211 |
| 2 (600) | stressed mean | 1363 / 27252 | 1.611 / 12.816 | 30.361 | 1.144 | 1.166 | 14.123 | 0.032 | 0.000 | 14.022 | 0.000 | 0.000 | 3.116 |
| 2 | growth med |  | p95 ×1.37, p50 ×1.18 | +5.789 | -0.097 | +0.659 | +3.889 | +0.008 | +0.000 | +0.659 | +0.000 | +0.000 | +0.772 |
| 2 | share med |  |  | +1.00 | -0.02 | +0.11 | +0.67 | +0.00 | +0.00 | +0.11 | +0.00 | +0.00 | +0.13 |
| 2 | growth mean |  | p95 ×1.37, p50 ×1.18 | +14.410 | -0.074 | +1.166 | +7.117 | +0.024 | +0.000 | +6.289 | +0.000 | +0.000 | +1.972 |
| 2 | share mean |  |  | +1.00 | -0.01 | +0.08 | +0.49 | +0.00 | +0.00 | +0.44 | +0.00 | +0.00 | +0.14 |
| 3 (800) | twin med | 1791 / 35806 | 3.272 / 68.801 | 123.411 | 1.051 | 0.000 | 55.903 | 0.041 | 0.000 | 50.237 | 0.000 | 0.000 | 3.143 |
| 3 (800) | twin mean | 1791 / 35806 | 3.272 / 68.801 | 295.261 | 10.851 | 0.000 | 51.048 | 0.060 | 0.000 | 233.484 | 0.000 | 0.000 | 10.279 |
| 3 (800) | stressed med | 1791 / 35801 | 3.369 / 30.048 | 47.462 | 1.067 | 1.958 | 32.706 | 0.066 | 0.000 | 9.029 | 0.000 | 0.000 | 6.558 |
| 3 (800) | stressed mean | 1791 / 35801 | 3.369 / 30.048 | 68.745 | 1.328 | 2.296 | 36.597 | 0.091 | 0.000 | 28.665 | 0.000 | 0.000 | 9.083 |
| 3 | growth med |  | p95 ×0.44, p50 ×1.03 | -75.949 | +0.016 | +1.958 | -23.197 | +0.025 | +0.000 | -41.209 | +0.000 | +0.000 | +3.415 |
| 3 | share med |  |  | +1.00 | -0.00 | -0.03 | +0.31 | -0.00 | -0.00 | +0.54 | -0.00 | -0.00 | -0.04 |
| 3 | growth mean |  | p95 ×0.44, p50 ×1.03 | -226.516 | -9.523 | +2.296 | -14.451 | +0.031 | -0.000 | -204.819 | -0.000 | +0.000 | -1.195 |
| 3 | share mean |  |  | +1.00 | +0.04 | -0.01 | +0.06 | -0.00 | +0.00 | +0.90 | +0.00 | -0.00 | +0.01 |

**Softirq on CPU 1 per step, twin and stressed** (`g7_softirq.py`; ms of softirq per second by vector, NET_RX entries,
raise → entry latency, that latency by the class running at the raise, wake-ups of the service thread that happened
inside a softirq on CPU 1 by class, and their raise → wake-up delay):

**B twin**

| step (req/s) | softirq ms/s: NET_RX | timer | rcu | sched | other | NET_RX entries/s | raise→entry NET_RX, mean µs | raise→entry ms/s by class at raise (own/aggr/root/other/idle) | wake-ups in softirq per s by class (own/aggr/root/other/idle) | raise→wake-up mean µs by class |
|---|---|---|---|---|---|---|---|---|---|---|
| 0 (200) | 30.9 | 0.5 | 0.5 | 1.1 | 0.0 | 926 | 1.8 | 2.41/0.00/0.00/0.01/2.23 | 0/0/0/0/1 | 30/0/0/0/23 |
| 1 (400) | 59.5 | 0.7 | 0.7 | 0.9 | 0.0 | 1842 | 1.8 | 4.81/0.00/0.00/0.00/2.03 | 1/0/0/0/1 | 34/0/0/0/25 |
| 2 (600) | 81.4 | 0.8 | 0.8 | 0.7 | 0.0 | 2579 | 1.7 | 6.55/0.00/0.01/0.00/1.61 | 1/0/0/0/1 | 25/0/0/0/30 |
| 3 (800) | 115.2 | 1.0 | 1.2 | 0.5 | 0.0 | 3768 | 1.7 | 9.63/0.00/0.04/0.00/1.14 | 1/0/0/0/2 | 27/0/0/0/30 |

**B stress1**

| step (req/s) | softirq ms/s: NET_RX | timer | rcu | sched | other | NET_RX entries/s | raise→entry NET_RX, mean µs | raise→entry ms/s by class at raise (own/aggr/root/other/idle) | wake-ups in softirq per s by class (own/aggr/root/other/idle) | raise→wake-up mean µs by class |
|---|---|---|---|---|---|---|---|---|---|---|
| 0 (200) | 30.3 | 0.8 | 0.4 | 0.1 | 0.0 | 938 | 1.8 | 2.00/1.25/0.00/0.00/0.00 | 0/0/0/0/0 | 32/0/0/0/0 |
| 1 (400) | 58.7 | 1.0 | 0.7 | 0.0 | 0.0 | 1862 | 1.7 | 4.01/1.26/0.01/0.00/0.00 | 1/0/0/0/0 | 30/0/11/0/0 |
| 2 (600) | 79.9 | 0.9 | 0.8 | 0.0 | 0.0 | 2585 | 1.7 | 5.73/0.88/0.02/0.00/0.00 | 1/0/0/0/0 | 28/0/0/0/0 |
| 3 (800) | 113.7 | 1.3 | 1.2 | 0.0 | 0.0 | 3730 | 1.7 | 8.45/0.66/0.07/0.00/0.00 | 2/0/0/0/0 | 25/0/0/0/0 |

**Reading.**
- **The pair-4 numbers reproduce**: server-stream p50 ×0.99 / 1.11 / 1.13, p95 ×1.10 / 1.19 / 1.32 on steps 0–2 (pair 4
  p90: +9 / +10 / +25 %). The tail request (median, server stream) grows +1.05 / +2.2 / +4.6 ms; **the growth is, first,
  run-queue delay behind the service's own threads (rq_self +0.24 / +0.38 / +2.72 ms; means +0.21 / +1.71 / +5.43)**
  — thirty connection threads sharing the half of CPU 1 the hog leaves queue behind each other after every store reply
  — **second, the blocked segment's remainder (+0.59 / +0.75 / +1.17 ms; means −41 (the twin's stall) / +24 / +6.5)**,
  and only third the delay behind the hog itself (**rq_foreign +0.01 / +0.01 / +0.39 ms median, +0.62 / +0.87 / +0.97
  mean**: a woken thread preempts the hog within microseconds, §2.2 v2). Handler on-CPU time does not change (−0.1 to
  +0.0 ms), softirq delay attributed to a request is 0.000 on every tail, the unattributed remainder 0. Shares at step 2
  (median): rq_self 0.59, blocked' 0.26, rq_foreign 0.09, on-CPU −0.01.
- **The softirq hypothesis of §2.4 D does not hold.** NET_RX runs on CPU 1 for 31 / 60 / 81 / 115 ms per second
  (3–11 % of the core, ~4.6 entries per request, the same with and without the hog), but **it does not wake this
  thread: 0–2 of the 860–3400 wake-ups per second of the post-storage thread happen inside a softirq on CPU 1**, in both
  runs. The thread's sleeps end from the idle task's context on 74–95 % of the wake-ups (an interrupt or softirq on an
  idle CPU: the NIC's IRQ CPUs, which the hog never touches) and from our own cgroup (futex hand-offs) on the rest;
  under the hog 1.2–1.9 s of sleep time per second ends in the aggressor's context (the wake-up IPI landing on CPU 1
  while the hog runs), and the resume after that wake-up is exactly the run-queue delay the table attributes
  (rq after sleeps, median tail: 0.04 → 0.09, 0.80 → 1.16, 2.58 → 4.70 ms, nearly all behind own threads). The hog's
  effect on CPU 1's softirqs is 0.7–1.3 ms of raise → entry latency per second — under a microsecond per request. The
  "~0.6 ms between the store's reply and the thread's resumption" of pair 4 is therefore not softirq on the victim's
  core. What the probe now shows is that it sits **before the wake-up** — between the request's send and the reply
  waking the thread — where neither CPU-1 softirq nor run-queue delay reaches: +0.55–1.2 ms median (+6.5 ms mean at
  600 req/s) of the blocked segment. Candidates the data cannot separate: queueing at the stores (a slower drain
  bunches post-storage's requests at memcached / Mongo) and the TCP send/ACK path on CPU 1; what it would take is a
  reply-send timestamp from the store's own recorder, or the IRQ CPUs' softirq trace joined by flow.
- **Window diagnoses**: C.1 rule row 6 on 160 of 160 served seconds (φ 0.50, E ≈ 0 at p50; cells 100 %), the §0 rule
  the same — as pair 4, (v) stays a tail-only case at the window level.

### 7.4 F.5 — deliverables, restore, deviations

- `results/g-final/f/f-A-tail.csv`, `f-B-tail.csv`: one row per (step, variant) with the spec's columns `run, step,
  req_s, variant, total_ms, oncpu_ms, rq_foreign_ms, rq_self_ms, rq_other_ms, softirq_ms, blocked_ms, unattributed_ms`
  plus `softirq_aggr_ms`, `rq_pre_ms`, `n`; `variant` = `<stream>:<twin|stressed|growth|share>-<med|mean>` with stream
  `server` (the recorder's total) or `arrival` (from the wake-up). The figure can be drawn from the `growth` and
  `share` rows. Beside them `f-A-tail.md`, `f-B-tail.md`, `f-B-softirq-{twin,stressed}.md`, and the per-request rows
  `<run>/f/requests.csv` in `results/sn/` (not packaged: 0.3 M rows each).
- Probe v5 in `probe/` (`gordion_sched.bt.tmpl`, `probe.py`), `analysis/g7_tail.py`, `g7_softirq.py`, the recorder
  change `socialNetwork/src/gordion/Gordion.h` (`rawe`) and `UniqueIdService.cpp` (`gordion::Attach`) in dsb_hd_counter,
  `scripts/stressor.sh` (`METHOD=duty<N>`).
- **Restore** (08:30 UTC): no probe / stress-ng / hrgen / perf on node-3, `gordion-aggr` removed, the run copies in
  `/var/tmp/gordion` deleted (probe v5 dir kept), `unique-id-service` back on node-1 unpinned without `hostPID`,
  post-storage as the Phase G closing left it (node-3 CPU 1, `hostPID: true`; image g6 now on every C++ service of `sn`),
  cpufreq / idle states / resctrl untouched. RUNBOOK §17.1.

Deviations (continuing §6.5):

47. **First queue on image g5, discarded.** `sn.sh deploy` patches command and env but not the image (helm set g5 at
    install), so the 07:39 queue's three unique-id runs had no recorder file; the queue was stopped, every C++ service
    of `sn` switched to g6 (`kubectl set image`), and the queue re-run from 07:51. The g5 attempt's directories remain
    in `/local/results/sn` (`…-0739/0743/0747-f-a-uid-*`), not fetched.
48. **Probe cost with the per-resume export** is 0.45–0.79 cores on post-storage's served steps and saturates one core
    at the collapse steps (records incomplete there); +17–25 % CPU per request on the 35 µs unique-id service.
49. **"raise → exit" measured as raise → wake-up** (the wake-up inside the softirq is the event that reaches the
    request; the exit follows within the handler).
50. **Run A's target on the arrival stream.** On the recorder's stream (tap → end) the p95 moved ×1.00–1.03; the
    hog's effect is the arrival wake-up delay, which that clock does not contain. The target (p50 ±5 %, p95 +20 %) is
    met on the arrival stream (p95 ×1.65–2.09) with the first duty-cycle setting; no tuning pass was spent.
51. **Run B twin's step 3 (800 req/s) carries a store stall** (mean blocked 234 ms); the growth is read on steps 0–2.
52. **unique-id-service driven through the compose chain**, not alone (no HTTP front); it has no downstream and one
    thread per connection, which is what F.3 asked for.
53. **No probe-off run for post-storage** (the pair-4 twin served the same steps with the same p50; the export's cost
    on the service side is measured on unique-id only, its own CPU on both).
