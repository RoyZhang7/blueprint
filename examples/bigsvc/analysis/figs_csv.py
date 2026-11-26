#!/usr/bin/env python3
"""results/figs: three CSVs for the paper's figures, analysis only (Nov 19). Every row names its source cell.

1. overhead_cells.csv: platform, service, neighbor, load, component, mode (default | diagnostic), CPU per request and client
   p99 as percent of the twin with their 95 % interval, n. Rows: K5 rungs 2-4 (results/k5/final/k5_ladder.csv: one 5-min
   arm each against the (mid, switch) twins, the twins' spread as the interval, n = 1), K5 5.7 set B and B + timers and 5.8
   set B' (k5_setb.csv, paired n = 5), Phase I 3 (i3, B' as shipped), 4.1 / 4.2 (i4 switch cell, i4b quiet cell, the rec
   cells) and 5.2 (i7, C1E off). One "measured_total" row per condition from the measured B' arm (Haswell: defBp; r650 as
   shipped: defBp; r650 corrected, C1E off: Bp-fixedReads).
2. verdicts_by_second.csv: scenario, platform, second, dict_only_verdict, signal_verdict, holder, truth. Phase G ii, iii,
   D.5 from each run's g2/verdict.csv (the C.1 verdict, truth from the twin) with the old-method D(lambda) cell
   (b4g2/b4_windows.csv cell_old) as dict_only; K4.5 reserve 1 at 7 000 req/s re-scored (results/k45/k45_refit_seconds.csv,
   row_after) and at 5 000 (the K4.5 chain re-run on node-0 from the clnode170 archive, g2-vs-S0/verdict.csv, when present);
   Phase I C1', C2, C4', C7 x4 from k5score.py replayed offline twice (--mask all, then every signal on;
   results/figs/src/i_verdicts_by_second.csv from scratchpad figs/i_replay.py on node-0).
3. signatures.csv: source, signal, ratio_vs_twin, platform, run_id (+ value, twin_value, kind) from K6
   (results/k5/final/k6_runs.csv) and Phase I 5.0 / 5.1 (results/i6/an/i6_sig.csv) with 5.2's C1' at one clock
   (results/i7/an/i6_sig.csv); one row per (source, signal) cell, the ~1 cells included.
usage: figs_csv.py [--out results/figs]
"""
import argparse, csv, json, os, sys
from collections import Counter

HERE = os.path.dirname(os.path.abspath(__file__)); BIG = os.path.dirname(HERE); RES = os.path.join(BIG, "results")
HAS = "Haswell E5-2683 v3 (clnode170 node-3)"; ICE = "Ice Lake 8360Y (clnode270 node-3, r650)"
SVC = {"uid": "unique-id", "rec": "recommendation", "syn": "bigsvc (synthetic)"}


def rd(p):
    return list(csv.DictReader(open(p, encoding="utf-8")))


def f(x):
    try: return float(x)
    except Exception: return None


def r(x, d=3):
    return None if x is None else round(x, d)


# ======================================================================================================== 1. overhead
# the K5 ladder's component names -> readable name, mode (RESULTS-K 5.6: what stays on by default), note
LADDER = {
    (2, "probe"): ("probe: switch program + run-queue split", "default", "rung 2, one arm"),
    (2, "reads"): ("per-window reads (getrusage, cpufreq; Go: q, g)", "default", ""),
    (2, "sampler"): ("sampler (/proc/stat, cpu.stat)", "default", ""),
    (2, "intercept"): ("interceptor, full timers (unsampled)", "diagnostic", "the full per-request timers; B' later made the sampled histogram timers default"),
    (2, "perf-armed"): ("perf cgroup counters armed", "diagnostic", "armed on demand (RESULTS-K 5.6)"),
    (2, "rdt-armed"): ("resctrl CMT armed", "default", ""),
    (2, "scorer-replay"): ("scorer (replaying recorded inputs, own CPU)", "default", ""),
    (2, "sirq-net"): ("softirq programs (under the network neighbor)", "diagnostic", "one twin (r0-net), no spread: n.d. by rule"),
    (3, "phi"): ("probe step: phi only", "default", "rung 3 is cumulative"),
    (3, "sib"): ("probe step: + phi_sibling", "default", "cumulative; the run-queue split (rung 2 probe) is between phi and this step"),
    (3, "wake"): ("probe step: + wake-up programs", "diagnostic", "cumulative"),
    (3, "wsirq"): ("probe step: + softirq programs", "diagnostic", "cumulative"),
    (3, "timer"): ("probe step: + timer record", "diagnostic", "cumulative"),
    (4, "q"): ("read: Go runtime q", "default", ""), (4, "g"): ("read: Go runtime g", "default", ""),
    (4, "cpu"): ("read: cpu (getrusage)", "default", ""), (4, "mhz"): ("read: mhz (cpufreq)", "default", "Phase I 4.1: on the r650 this read carries +1 ms of client p99; moved to the agent"),
    (4, "count"): ("interceptor: count", "default", ""), (4, "slot"): ("interceptor: slot busy", "default", ""),
    (4, "perf-read"): ("perf counters armed + CPI/MPKI read", "diagnostic", ""), (4, "rdt-read"): ("resctrl CMT read", "default", ""),
}
# arm names of the r650 cells (Phase I 3, 4, 5.2) -> readable, mode, note
ARMS_I = {
    "intercept": ("interceptor: sampled histogram timers only (1 in 8)", "default", "timers only, nothing else of the set"),
    "defBp": ("set B' as shipped", "default", "probe (phi, phi_sibling, run_delay), CMT + MBM on victim and neighbors, sampled timers, reads cpu + mhz, sampler, scorer"),
    "Bp-noRdt": ("B' minus resctrl", "default", "decomposition arm"), "Bp-noTimers": ("B' minus timers", "default", "decomposition arm"),
    "Bp-noReads": ("B' minus the per-window reads", "default", "decomposition arm"), "Bp-noSampler": ("B' minus sampler", "default", "decomposition arm"),
    "Bp-noScorer": ("B' minus scorer", "default", "decomposition arm"), "Bp-rdtNbr": ("B' with resctrl on the neighbors only", "default", "decomposition arm"),
    "Bp-fixedReads": ("B' with fixed reads (corrected set: recorder reads cpu, agent reads APERF/MPERF)", "default", "the corrected B' (RESULTS-I 4.1)"),
    "Bp-fix2": ("B' with fixed reads and resctrl on the victim too", "default", "corrected B', variant"),
}
SETB = {"defB": ("set B (default set, timers off)", "default", "RESULTS-K 5.7"), "defBt": ("set B + full timers", "diagnostic", "RESULTS-K 5.7"),
        "defBp": ("set B' (sampled timers, run_delay in the probe)", "default", "RESULTS-K 5.8")}


def cells_of(path):
    """cells.txt -> {(svc, nb, load_label): cell} ; load label mid / knee / NNNN from the cell name"""
    out = {}
    if os.path.exists(path):
        for l in open(path, encoding="utf-8"):
            p = l.split()
            if len(p) >= 4: out[p[1]] = p[0]
    else:   # no cells.txt (queue i7): the cell directories themselves, <stamp>-i2-<name>
        for d in sorted(os.listdir(os.path.dirname(path))):
            if "-i2-" in d and os.path.isdir(os.path.join(os.path.dirname(path), d)): out[d.split("-i2-", 1)[1]] = d
    return out


def overhead(out):
    rows = []
    arms = rd(os.path.join(RES, "k5", "final", "k5_arms.csv")); cell_by_name = {a["name"]: a["cell"] for a in arms}
    # K5 rungs 2-4 (Haswell)
    for x in rd(os.path.join(RES, "k5", "final", "k5_ladder.csv")):
        rung = int(x["rung"]); comp = x["component"]; name, mode, note = LADDER.get((rung, comp), (comp, "", ""))
        cname = f"r{rung}-{x['svc']}-{comp}" if comp != "sirq-net" else f"r2-{x['svc']}-sirq-net"
        cell = cell_by_name.get(cname, "")
        sp = f(x["twins_spread_cpu_pct"]); d = f(x["d_cpu_per_req_pct"]); pr = f(x["p99_ratio"]); sp99 = f(x["twins_spread_p99_pct"])
        p99 = None if pr is None else (pr - 1) * 100
        rows.append(dict(platform=HAS, service=SVC[x["svc"]], neighbor=x["nb"], load="mid", component=name, mode=mode, cpu_per_req_pct=r(d), cpu_lo=r(d - sp) if sp is not None else None, cpu_hi=r(d + sp) if sp is not None else None,
                         p99_pct=r(p99), p99_lo=r(p99 - sp99) if (p99 is not None and sp99 is not None) else None, p99_hi=r(p99 + sp99) if (p99 is not None and sp99 is not None) else None, n=1,
                         interval="twins_spread (n = 1: the (mid, switch) twins' spread, not a confidence interval)" + ("" if x["distinguishable_cpu"] == "True" else "; CPU n.d."), set="rung %d" % rung, source_cell=cell, source_file="results/k5/final/k5_ladder.csv", notes=note + ("; p99 = client p99" if note else "p99 = client p99")))
    # K5 5.7 / 5.8 (Haswell), paired n = 5
    for x in rd(os.path.join(RES, "k5", "final", "k5_setb.csv")):
        name, mode, note = SETB[x["arm"]]; comp = "measured_total" if x["arm"] == "defBp" else name
        pre = "bp" if x["arm"] == "defBp" else "b"; cname = f"{pre}-{x['svc']}-{x['nb']}-{x['load']}"
        rows.append(_setb_row(HAS, x, comp, mode, cell_by_name.get(cname, ""), "results/k5/final/k5_setb.csv", (note + "; " if note else "") + (f"the measured B' arm ({name})" if comp == "measured_total" else "")))
    # Phase I 3: B' as shipped on the r650 (measured_total), i3
    c3 = cells_of(os.path.join(RES, "i3", "cells.txt"))
    for x in rd(os.path.join(RES, "i3", "an", "k5_setb.csv")):
        if x["arm"] != "defBp": continue
        rows.append(_setb_row(ICE, x, "measured_total", "default", c3.get(f"bp-{x['svc']}-{x['nb']}-{x['load']}", ""), "results/i3/an/k5_setb.csv", "the measured B' arm as shipped (defBp), RESULTS-I 3.1; C1E on (idle unique-id core at ~1.7 GHz)"))
    # Phase I 4.1 (uid: i4 switch cell, i4b quiet cell) and 4.2 (rec cells, i4)
    for root, keep in (("i4", lambda x: not (x["svc"] == "uid" and x["nb"] == "none")), ("i4b", lambda x: x["svc"] == "uid" and x["nb"] == "none")):
        cc = cells_of(os.path.join(RES, root, "cells.txt"))
        for x in rd(os.path.join(RES, root, "an", "k5_setb.csv")):
            if not keep(x): continue
            name, mode, note = ARMS_I.get(x["arm"], (x["arm"], "", ""))
            pre = "dc" if x["svc"] == "uid" else "bq"; cell = cc.get(f"{pre}-{x['svc']}-{x['nb']}-{x['load']}", "")
            sec = "RESULTS-I 4.1" if x["svc"] == "uid" else "RESULTS-I 4.2"
            rows.append(_setb_row(ICE, x, name, mode, cell, f"results/{root}/an/k5_setb.csv", f"{sec}; C1E on; {note}"))
            if x["arm"] == "defBp":
                rows.append(_setb_row(ICE, x, "measured_total", "default", cell, f"results/{root}/an/k5_setb.csv", f"{sec}; the measured B' arm as shipped (defBp); C1E on"))
    # Phase I 5.2 (i7, C1E off): intercept, Bp-fixedReads; measured_total = the corrected B'
    c7 = cells_of(os.path.join(RES, "i7", "cells.txt")) if os.path.exists(os.path.join(RES, "i7", "cells.txt")) else {}
    cells7 = {"none": "20261118-220125-i2-dc-uid-none-mid", "switch": "20261118-221049-i2-dc-uid-switch-mid"}
    for x in rd(os.path.join(RES, "i7", "an", "k5_setb.csv")):
        name, mode, note = ARMS_I.get(x["arm"], (x["arm"], "", ""))
        cell = c7.get(f"dc-{x['svc']}-{x['nb']}-{x['load']}") or cells7.get(x["nb"], "")
        rows.append(_setb_row(ICE, x, name, mode, cell, "results/i7/an/k5_setb.csv", f"RESULTS-I 5.2; C1E off (core at 2 400 MHz in every cell); mid 22 483 req/s; {note}"))
        if x["arm"] == "Bp-fixedReads":
            rows.append(_setb_row(ICE, x, "measured_total", "default", cell, "results/i7/an/k5_setb.csv", "RESULTS-I 5.2; the measured corrected B' arm (Bp-fixedReads); C1E off"))
    # Phase I 9 (step 8, queue i11, C1E off): the gated escalation cell (3 x 60 s) and the standard B' net cell, 5 x 300 s
    # (figA2's missing cell), paired per rep within each cell (k5_analyze's set-B table pools the two cells' B' arms)
    p11 = os.path.join(RES, "i11", "an", "k5_arms.csv")
    if os.path.exists(p11):
        import math, statistics
        A = rd(p11); cells11 = sorted({x["cell"] for x in A if x["name"] in ("dc-uid-net-mid", "bp-uid-net-mid")})
        for cell in cells11:
            by = {}
            for x in A:
                if x["cell"] == cell: by.setdefault(int(float(x["rep"])), {})[x["arm"]] = x
            name11 = next(x["name"] for x in A if x["cell"] == cell)
            for arm in sorted({x["arm"] for x in A if x["cell"] == cell} - {"none"}):
                dc = []; dp = []
                for rep, ar in sorted(by.items()):
                    n_, f_ = ar.get("none"), ar.get(arm)
                    if n_ and f_ and f(n_["cpu_per_req_ms"]) and f(f_["cpu_per_req_ms"]):
                        dc.append((f(f_["cpu_per_req_ms"]) / f(n_["cpu_per_req_ms"]) - 1) * 100); dp.append((f(f_["client_p99_ms"]) / f(n_["client_p99_ms"]) - 1) * 100)
                if not dc: continue
                T = {2: 4.303, 3: 3.182, 4: 2.776, 5: 2.571, 6: 2.447, 7: 2.365, 8: 2.306}
                def ci(xs):
                    m = statistics.mean(xs); h = (T.get(len(xs) - 1, 2.0) * statistics.stdev(xs) / math.sqrt(len(xs))) if len(xs) > 1 else 0.0; return m, m - h, m + h
                mc, lc, hc = ci(dc); mp, lp, hp = ci(dp)
                nm, mode, note = ARMS_I.get(arm, (arm, "", ""))
                if arm == "Bp-esc": nm, mode, note = ("B' with fixed reads + the escalation (diagnostics while E is flagged)", "diagnostic", "the escalation arm with the step 8 gate (E only, row 8 for softirq): 0 fires in every rep")
                base = dict(platform=ICE, service="unique-id", neighbor="net", load="mid", cpu_per_req_pct=r(mc), cpu_lo=r(lc), cpu_hi=r(hc), p99_pct=r(mp), p99_lo=r(lp), p99_hi=r(hp), n=len(dc),
                            interval="t95 (paired per repetition against the twin arm of the same rep)", set=arm, source_cell=cell, source_file="results/i11/an/k5_arms.csv")
                rows.append(dict(base, component=nm, mode=mode, notes=f"RESULTS-I 9 ({name11}, {'5 x 300 s' if name11.startswith('bp-') else '3 x 60 s'}); C1E off; mid 22 483 req/s; {note}; p99 = client p99"))
                if arm == "Bp-fixedReads" and name11.startswith("bp-"):
                    rows.append(dict(base, component="measured_total", mode="default", notes="RESULTS-I 9 (figA2's cell): the measured corrected B' arm (Bp-fixedReads) under the standard net neighbor, 5 x 300 s; C1E off; p99 = client p99"))
    # Phase J1 (RESULTS-J 1): the unpinned ladder's rows, already in this format (analysis/j1_ladder.py on node-0)
    j1 = os.path.join(RES, "figs", "src", "j1_ladder.csv")
    if os.path.exists(j1):
        with open(j1, newline="") as fj:
            for x in csv.DictReader(fj): rows.append(dict(x))
    keys = ["platform", "service", "neighbor", "load", "component", "mode", "cpu_per_req_pct", "cpu_lo", "cpu_hi", "p99_pct", "p99_lo", "p99_hi", "n", "interval", "set", "source_cell", "source_file", "notes"]
    _w(os.path.join(out, "overhead_cells.csv"), keys, rows); return rows


def _setb_row(platform, x, comp, mode, cell, src, note):
    return dict(platform=platform, service=SVC[x["svc"]], neighbor=x["nb"], load=x["load"], component=comp, mode=mode,
                cpu_per_req_pct=r(f(x["d_cpu_per_req_pct"])), cpu_lo=r(f(x["d_cpu_lo"])), cpu_hi=r(f(x["d_cpu_hi"])),
                p99_pct=r(f(x["d_client_p99_pct"])), p99_lo=r(f(x["d_p99_lo"])), p99_hi=r(f(x["d_p99_hi"])), n=int(float(x["n"])),
                interval="t95 (paired per repetition against the twin arm of the same rep)", set=x["arm"], source_cell=cell, source_file=src, notes=note + "; p99 = client p99")


# ======================================================================================================== 2. verdicts
ROWN = {"-1": "OOR (abstain)", "0": "0 saturation; no verdict", "1": "1 run-queue contention", "2": "2 own overload", "3": "3 service-time inflation", "4": "4 lock wait", "5": "5 GC", "6": "6 nothing to explain", "7": "7 no verdict"}


def rown(v):
    try: return ROWN.get(str(int(float(v))), str(v))
    except Exception: return str(v)


def phaseg(rows, label, run, win, vsub="g2", b4sub="b4g2", holder_rule=None):
    """per second: the verdict row / truth from results/g-final/windows/<win>.csv (row_sec, truth; one row per second, the
    final C.1 scoring of RESULTS-G 2.5 / 2.9), the holder from <run>/<vsub>/verdict.csv (holder_cgroup, row-1 seconds), the
    old-method D(lambda) cell from <run>/<b4sub>/b4_windows.csv (cell_old, RESULTS-G 2.10)"""
    W = rd(os.path.join(RES, "g-final", "windows", win + ".csv")); S = {}
    for w in W: S.setdefault(w["sec"], w)
    V = {x["t"].split(".")[0]: x for x in rd(os.path.join(RES, run, vsub, "verdict.csv"))}
    B = {x["t"].split(".")[0]: x for x in rd(os.path.join(RES, run, b4sub, "b4_windows.csv"))}
    n0 = len(rows); miss_b = 0; miss_v = 0
    for sec, w in S.items():
        v = V.get(sec); b = B.get(sec)
        if b is None: miss_b += 1
        if v is None: miss_v += 1
        row = rown(w["row_sec"]); hc = (v or {}).get("holder_cgroup")
        holder = (hc if hc is not None else (holder_rule or "")) if row.startswith("1") else ""
        if v is not None and rown(v["row"]) != row: raise SystemExit(f"{label} {sec}: verdict.csv row {v['row']} != windows row_sec {w['row_sec']}")
        rows.append(dict(scenario=label, platform=HAS, second=sec, dict_only_verdict=(b or {}).get("cell_old", ""), signal_verdict=row, holder=holder, truth=w["truth"], truth_ext=w.get("truth_ext", ""), E=w.get("E_sec", ""),
                         dict_only_method="D(lambda), Gordion Algorithm 1 on the dictionary run's own sweep, 5 s smoothing: slow x ext cell (cell_old of b4_windows.csv, RESULTS-G 2.10)", dict_mix_cell=w.get("cell_sec", ""),
                         source_cell=f"results/{run} ({vsub}/verdict.csv, {b4sub}/b4_windows.csv); results/g-final/windows/{win}.csv", notes=("holder by rule: the aggressor cgroup on the cpuset" if (hc is None and holder) else "")))
    print(f"  {label}: {len(S)} seconds, {miss_v} without a verdict.csv row, {miss_b} without a D(lambda) row")
    return len(rows) - n0


def k45_ethr():
    """step 9: the Haswell K4.5 sweep's E_thr(rho) (k5score --fit on the D run, rho on its own cbar axis, k = 4)"""
    p = os.path.join(RES, "figs", "src", "dict-k45-rec-ethr.json")
    if not os.path.exists(p): return None
    d = json.load(open(p)); R = [x["rho"] for x in d["steps"]]; T = [x["e_thr"] for x in d["steps"]]; cb = d["cbar_ms"]
    def at(lam):
        r = lam * cb / 4 / 1000
        if r <= R[0]: return T[0]
        if r >= R[-1]: return T[-1]
        for i in range(1, len(R)):
            if r <= R[i]:
                fr = (r - R[i - 1]) / (R[i] - R[i - 1]); return T[i - 1] * (1 - fr) + T[i] * fr
        return T[-1]
    return at


def k45_new(E, phi, lam, at):
    """the step 9 verdict on a Haswell second: row 1 when E >= E_thr(rho) and phi > 0.05, else 6 (no softirq columns on the lean probe)"""
    if at is None or E is None: return ""
    return "1 run-queue contention" if (E >= at(lam) and (phi or 0) > 0.05) else "6 nothing to explain"


def k45_7000(rows):
    p = os.path.join(RES, "k45", "k45_refit_seconds.csv"); n0 = len(rows)
    thr = 0.150; at = k45_ethr()
    # the truth class (slow-int / fast / ext) and the row-1 holder per second from the K4.5 chain re-run (default fit; the
    # truth and the holder do not depend on the dictionary fit): results/figs/src/k45-res1x7-vs-T7-verdict.csv
    vp = os.path.join(RES, "figs", "src", "k45-res1x7-vs-T7-verdict.csv"); V = {x["t"].split(".")[0]: x for x in rd(vp)} if os.path.exists(vp) else {}
    for x in rd(p):
        if x["run"] != "res1x7": continue
        E = f(x["E_after"]); row = rown(x["row_after"]); te = f(x["truth"]); v = V.get(x["t"].split(".")[0], {})
        truth = v.get("truth") or ("ext" if te is not None and te >= 0.5 else "int (truth_ext < 0.5; slow/fast not in the file)")
        lam = f(v.get("lam")) if v else 7000.0; new = k45_new(E, f(x.get("phi")), lam, at) or row; th = at(lam) if at else thr
        rows.append(dict(scenario="K4.5 reserve 1 at 7 000 req/s (re-scored, RESULTS-K 4.5.1)", platform=HAS + " node-4 (HC)", second=x["t"], dict_only_verdict=("ext (E >= E_thr %.3f)" % th if (E is not None and E >= th) else "int (E under E_thr %.3f)" % th),
                         signal_verdict=new, signal_verdict_old=row, holder=("hc-secondary-burner" if new.startswith("1") else ""), truth=truth, truth_ext=x["truth"], E=x["E_after"],
                         dict_only_method="E (4.5.1 fit) against the Haswell sweep's E_thr(rho) (step 9); signal_verdict_old: the stored 4.5.1 verdict against the twin-relative 0.150", dict_mix_cell="",
                         source_cell="results/k45/k45_refit_seconds.csv run res1x7 (20261115-071433-k45-res1x7 vs T7, 4.5.1 fit); holder by rule (--aggr-name hc-secondary-burner)", notes="E_after = the 4.5.1 fit (50-window floor, step interpolation); RESULTS-K 4.5's 38 % is K4.5's own fit (row_before, 112 of 296), the 4.5.1 fit reads 222 (75 %); with E_thr(rho) 0.245: 84 (28 %) on the 4.5.1 fit's E (this row), 46 (16 %) on K4.5's own E"))
    return len(rows) - n0


def k45_5000(rows):
    p = os.path.join(RES, "figs", "src", "k45-res1-vs-S0-verdict.csv"); n0 = len(rows)
    if not os.path.exists(p): return 0
    thr_p = os.path.join(RES, "figs", "src", "k45-res1-vs-S0-thresholds.json"); thr = 0.153
    if os.path.exists(thr_p):
        try: thr = float(json.load(open(thr_p)).get("E_far", thr))
        except Exception: pass
    at = k45_ethr()
    for x in rd(p):
        E = f(x["E"]); row = rown(x["row"]); lam = f(x.get("lam")) or 5000.0; new = k45_new(E, f(x.get("phi_cpuset")), lam, at) or row; th = at(lam) if at else thr
        holder = x.get("holder_cgroup", "") if new.startswith("1") else ""
        rows.append(dict(scenario="K4.5 reserve 1 at 5 000 req/s (re-scored; a sweep step, so the 4.5.1 fit changes nothing)", platform=HAS + " node-4 (HC)", second=x["t"], dict_only_verdict=("ext (E >= E_thr %.3f)" % th if (E is not None and E >= th) else "int (E under E_thr %.3f)" % th),
                         signal_verdict=new, signal_verdict_old=row, holder=holder, truth=x.get("truth", ""), truth_ext=x.get("truth_ext", ""), E=x.get("E", ""),
                         dict_only_method="E against the Haswell sweep's E_thr(rho) (step 9, 0.125 at 5 000 req/s); signal_verdict_old: the stored verdict against the twin-relative 0.153", dict_mix_cell=x.get("cell_mix", ""),
                         source_cell="20261115-070028-k45-res1 vs 20261115-064140-k45-S0 (K4.5 chain re-run on clnode270 node-0 from /proj/.../royno7-k-archive-clnode170-2026-11-17/results/k45; results/figs/src/k45-res1-vs-S0-verdict.csv)", notes=""))
    return len(rows) - n0


def phasei(rows):
    p = os.path.join(RES, "figs", "src", "i_verdicts_by_second.csv"); n0 = len(rows)
    for x in rd(p):
        rows.append(dict(scenario=x["scenario"], platform=ICE, second=x["second"], dict_only_verdict=x["dict_only_verdict"], signal_verdict=x["signal_verdict"], holder=x["holder"], truth=x["truth"],
                         truth_ext=x["truth_ext"], E=x["E"], dict_only_method="k5score.py --replay --mask all: E against E_far 0.15 with phi, R_foreign, I_g, Q, slot and MBM withheld (rows 6 / 7)",
                         dict_mix_cell="", source_cell=x["source_cell"] + "; twins " + x["twin_cells"] + "; dictionary " + x["dictionary"],
                         notes=f"I_g {x['I_g']}, phi {x['phi']}, MBM sub-rule '{x['mbm_sub']}', {x['windows']} windows; truth: Lat_s = n-weighted median of the second's window p50s, ext when (Lat - p50_twin)/Lat >= 0.5, slow-int when Lat > the twins' per-second p95, else fast; twin p50 {x['twin_p50_ms']} ms"))
    return len(rows) - n0


def verdicts(out):
    rows = []; counts = {}
    counts["G ii"] = phaseg(rows, "G ii served step past the profile (v2, no aggressor)", "20261111-014041-g2b-overhead-probe-on", "ii", vsub="g2t", b4sub="b4g2t")
    counts["G iii"] = phaseg(rows, "G iii recommendation + 1 aggressor per core (v3)", "hr/20261111-041108-g2c-iii-rec-stress2", "iii", holder_rule="gordion-aggr")
    counts["G D.5"] = phaseg(rows, "G D.5 VioMutex 40 %, no aggressor (v3.2)", "20261111-074358-g2f-d5-mutex40", "d5-mutex40")
    counts["K4.5 7000"] = k45_7000(rows); counts["K4.5 5000"] = k45_5000(rows); counts["I"] = phasei(rows)
    p11 = os.path.join(RES, "figs", "src", "i11_verdicts_by_second.csv"); n0 = len(rows)
    if os.path.exists(p11):
        for x in rd(p11):
            rows.append(dict(scenario=x["scenario"], platform=ICE, second=x["second"], dict_only_verdict=x["dict_only_verdict"], signal_verdict=x["signal_verdict"], holder=x["holder"], truth=x["truth"],
                             truth_ext=x["truth_ext"], E=x["E"], dict_only_method=f"k5score.py --replay --mask all: E against the sweep's E_thr(rho) ({x['thr']}) with every signal withheld",
                             dict_mix_cell="", source_cell=x["source_cell"] + "; twins " + x["twin_cells"] + "; dictionary " + x["dictionary"],
                             notes=f"I_g {x['I_g']}, phi {x['phi']}, softirq share {x['sirq']} (base {x['sirq_base']}), {x['windows']} windows; truth as the step-4 rows; twin p50 {x['twin_p50_ms']} ms"))
    counts["I C2 (i11)"] = len(rows) - n0
    # step 9: the C2 arm under the capacity-loss rule (signal_verdict_old = the step 8 rows above, replaced in place)
    p9 = os.path.join(RES, "figs", "src", "i11_verdicts_by_second_s9.csv")
    if os.path.exists(p9):
        old = {r["second"]: r for r in rows if r["scenario"].startswith("I C2 contention")}
        for x in rd(p9):
            o = old.get(x["second"])
            if o is None: continue
            o["signal_verdict_old"] = o["signal_verdict"]; o["signal_verdict"] = x["signal_verdict"]; o["dict_only_verdict"] = x["dict_only_verdict"]
            o["dict_only_method"] = "k5score.py --replay --mask all (step 9): E against E_thr with every signal, phi_irq included, withheld"
            o["notes"] = "step 9: phi_irq " + x["phi_irq"] + " (base " + x["sirq_base"] + "), I_g corrected " + x["I_g"] + " (raw " + x["I_g_raw"] + "), rho_eff " + x["rho_eff"] + ", amount " + x["amount_ms"] + " ms (D is flat on unique-id), target irq-affinity; truth from the server-side p50 (fast on every second while the client's p50 was 24 s: the in-handler timer cannot see the queue in front of the handler); softirq share " + x["sirq"] + ", " + x["windows"] + " windows"
    # step 9: HC on the r650 (queue i10e, live scorer, k = 4, E_thr from its sweep); signal_verdict_old = the rule at the fixed 0.15
    p10 = os.path.join(RES, "figs", "src", "i10e_verdicts_by_second.csv"); n0 = len(rows)
    if os.path.exists(p10):
        for x in rd(p10):
            E = f(x["E"]); phi = f(x["phi"]); oldv = "1 run-queue contention" if (E is not None and E >= 0.15 and (phi or 0) > 0.05) else "6 nothing to explain"
            rows.append(dict(scenario="I " + x["scenario"], platform=ICE + " node-4 (HC, 6.12.118-harvest, SMT off)", second=x["second"], dict_only_verdict=x["dict_only_verdict"], signal_verdict=x["signal_verdict"], signal_verdict_old=oldv, holder=x["holder"], truth=x["truth"],
                             truth_ext=x["truth_ext"], E=x["E"], dict_only_method="E against the queue's own sweep E_thr(rho) (" + str(x["thr"]) + ") with no signal; signal_verdict_old: the rule at the fixed 0.15", dict_mix_cell="",
                             source_cell=x["source_cell"] + "; twins " + x["twin_cells"], notes="I_g " + str(x["I_g"]) + ", phi " + str(x["phi"]) + ", " + x["windows"] + " windows; truth from the twins' server-side p50 (p50_twin " + x["twin_p50_ms"] + " ms): ext >= 0.5, slow-int above the twins' per-second p95, else fast"))
    counts["I HC r650 (i10e)"] = len(rows) - n0
    # step 9: the milder C2 (queue i12) with client-side truth
    p12 = os.path.join(RES, "figs", "src", "i12_verdicts_by_second.csv"); n0 = len(rows)
    if os.path.exists(p12):
        for x in rd(p12):
            rows.append(dict(scenario=x["scenario"], platform=ICE, second=x["second"], dict_only_verdict=x["dict_only_verdict"], signal_verdict=x["signal_verdict"], signal_verdict_old="", holder=x["holder"], truth=x["truth"],
                             truth_ext=x["truth_ext"], E=x["E"], dict_only_method="k5score.py --replay --mask all (step 9): E against E_thr (" + str(x["thr"]) + ") with every signal, phi_irq included, withheld", dict_mix_cell="",
                             source_cell=x["source_cell"] + "; twins " + x["twin_cells"] + "; dictionary " + x["dictionary"],
                             notes="TRUTH SOURCE: the generator's per-second client p50 (" + x["client_p50_ms"] + " ms) against the twins' client p50 (" + x["twin_client_p50_ms"] + " ms): ext >= 0.5, slow-int above the twins' per-second p95, else fast (the in-handler timer cannot see the queue in front of the handler; server lat " + str(x["srv_lat_ms"]) + " ms); step 9: phi_irq " + str(x["phi_irq"]) + " (base " + str(x["sirq_base"]) + "), I_g corrected " + str(x["I_g"]) + " (raw " + str(x["I_g_raw"]) + "), rho_eff " + str(x["rho_eff"]) + ", amount " + str(x["amount_ms"]) + " ms, softirq share " + str(x["sirq"]) + ", " + x["windows"] + " windows"))
    counts["I C2 milder (i12)"] = len(rows) - n0
    # step 11: one scorer, every row. The final replay (analysis/figs_final_replay.py on node-0: E_thr(rho) per sweep, k from the
    # pod's threads, I_g with softirq subtracted, phi_irq row 9 gated on the smoothed corrected I_g, escalation on E only) replaces
    # the r650 rows' verdicts in place; the previous verdict moves to signal_verdict_old. Phase G rows from analysis/figs_g_final.py.
    pf = os.path.join(RES, "figs", "src", "final_verdicts_by_second.csv")
    if os.path.exists(pf):
        F = {}
        for x in rd(pf): F[(x["scenario"], x["second"])] = x
        hit = 0
        for o in rows:
            x = F.get((o["scenario"], o["second"]))
            if x is None: continue
            hit += 1; o["signal_verdict_old"] = o["signal_verdict"]; o["signal_verdict"] = x["signal_verdict"]; o["dict_only_verdict"] = x["dict_only_verdict"]; o["holder"] = x["holder"]; o["rule_version"] = x["rule_version"]
            o["dict_only_method"] = "k5score.py --replay --mask all (step 11 final): E against the service's own sweep E_thr(rho) (" + str(x["thr"]) + ") with every signal withheld"
            sec5 = ("; RESULTS-I 5.0 check beside it (service dictionary F+hK, cbar 4.244, arm level): rho 0.463, I_g 1.445, rho_eff 0.670 +- 0.017, D' 16.8 ms, D'-D 12.6 ms [10.6, 21.7] vs measured p50 - twin 11.5 ms; the scorer's K5 dictionary (cbar 5.000, per-window-rho bins) at rho_eff 0.789 +- 0.020 reads D'-D 39 ms [26, 52] (slope 460 vs 132 ms per unit rho: its knee is smeared toward lower rho by the per-window binning)" if o["scenario"].startswith("I C4'") else
                    ("; RESULTS-I 5.0 check beside it (service dictionary, cbar 4.240, arm level): rho 0.463, I_g 1.179, rho_eff 0.546 +- 0.012, D' 9.9 ms, D'-D 3.8 ms [3.1, 4.3] vs measured p50 - twin 3.7 ms; the scorer's K5 dictionary (cbar 5.470) at rho_eff 0.704 +- 0.015 reads D'-D 14.7 ms [9.0, 20.4] (slope 322 vs 43 ms per unit rho)" if o["scenario"].startswith("I C7") else ""))
            o["notes"] = ("step 11 final replay (11c: amount on the 5 s-smoothed rho and I_g): I_g corrected " + str(x["I_g"]) + " (raw " + str(x["I_g_raw"]) + "), phi " + str(x["phi"]) + ", phi_irq " + str(x["phi_irq"]) + " (base " + str(x["sirq_base"]) + "), rho_eff " + str(x["rho_eff"]) + ", MBM sub-rule " + (x["mbm_sub"] or "none") + ", amount " + (str(x["amount_ms"]) + " ms (" + x["amount_src"] + ")" if x["amount_ms"] not in ("", None) else "n/a (rows 2/6/7 carry none)") + ", " + x["windows"] + " windows; truth: " + x["truth_source"] + " (" + x["lat_ms"] + " vs " + x["twin_p50_ms"] + " ms); " + x["dictionary"] + sec5)
        counts["step 11 replaced (r650)"] = hit
    pg = os.path.join(RES, "figs", "src", "g_final_verdicts_by_second.csv")
    if os.path.exists(pg):
        G = {}
        for x in rd(pg): G[(x["scenario"], x["second"])] = x
        hit = 0
        for o in rows:
            x = G.get((o["scenario"], o["second"]))
            if x is None: continue
            hit += 1; o["signal_verdict_old"] = o["signal_verdict"]; o["signal_verdict"] = x["signal_verdict"]; o["rule_version"] = x["rule_version"]
            o["notes"] = (o["notes"] + "; " if o["notes"] else "") + "step 11: " + x["notes"] + " (E_thr " + str(x["thr"]) + ", I_g " + str(x["I_g"]) + ", phi " + str(x["phi"]) + ", Q " + str(x["Q_ms_s"]) + " ms/s, slot " + str(x["slot_busy"]) + ")"
        counts["step 11 replaced (Phase G)"] = hit
    for o in rows:
        if not o.get("rule_version"): o["rule_version"] = "s9" if o["scenario"].startswith("K4.5") else ("s4" if o["scenario"].startswith("I C2 network neighbor") else "")
    keys = ["scenario", "platform", "second", "dict_only_verdict", "signal_verdict", "signal_verdict_old", "rule_version", "holder", "truth", "truth_ext", "E", "dict_only_method", "dict_mix_cell", "source_cell", "notes"]
    _w(os.path.join(out, "verdicts_by_second.csv"), keys, rows); return rows, counts


# ======================================================================================================== 3. signatures
K6_SIG = [  # (signal, column pattern with {s} = svc short name, kind)
    ("CPU per request", "cpu_per_req_ms", "ratio"), ("I_g (dictionary interference index, unique-id only)", "I_g", "ratio"), ("client p99", "client_p99_ms", "ratio"),
    ("CPI (victim cgroup)", "cpi:{s}", "ratio"), ("MPKI (victim cgroup)", "mpki:{s}", "ratio"), ("own LLC occupancy (CMT)", "cmt_mb:{s}", "ratio"),
    ("neighbor LLC occupancy (CMT, aggr cgroup)", "cmt_mb:aggr", "absolute"), ("neighbor usage cores (aggr cgroup)", "usage_cores:aggr", "absolute"),
    ("phi (foreign share of the cpuset)", "phi", "absolute"), ("R_foreign ms/s", "R_foreign_ms_s", "ratio"), ("R_self ms/s", "R_self_ms_s", "ratio"),
    ("phi_sibling", "phi_sibling", "absolute"), ("softirq ms/s (total)", "softirq_ms_s", "ratio"), ("softirq while current ms/s", "softirq_while_current_ms_s", "ratio"),
    ("victim usage cores", "usage_cores:{s}", "ratio"),
]
K6_LABEL = {"c1-uid-sib": "C1 SMT sibling (matrixprod on CPU 29)", "c2-uid-net": "C2 network softirq (A5 stream, RPS to CPU 1)", "c3-syn-memwalk30": "C3 own working set, memwalk30 at 24 MB (vs uniform twin)",
            "c3-syn-memwalk30-128mb": "C3 own working set, memwalk30 at 128 MB (vs uniform twin at 128 MB)", "c4-syn-memwalk30-cache": "C4 cache neighbor, stress-ng --cache x4 (vs C3 memwalk30)",
            "c5-res1": "C5 harvester, HC reserve 1 at 5 000 req/s (vs standalone S0)", "c6-uid-twin2": "C6 twin vs twin (unique-id)"}


def k6(rows):
    R = rd(os.path.join(RES, "k5", "final", "k6_runs.csv")); by = {x["name"]: x for x in R}; n0 = len(rows)
    for x in R:
        if x["name"] not in K6_LABEL: continue
        tw = [by[t] for t in x["twin"].split("+") if t in by]
        if not tw: continue
        s = x["svc"]
        for sig, col, kind in K6_SIG:
            c = col.format(s=s)
            if s != "uid" and col == "I_g": continue
            if s == "rec" and col in ("cmt_mb:aggr", "usage_cores:aggr"): c = col.replace("aggr", "burner")
            v = f(x.get(c)); tv = [f(t.get(c)) for t in tw]; tv = [t for t in tv if t is not None]
            if v is None and not tv: continue
            t = sum(tv) / len(tv) if tv else None
            ratio = (v / t) if (kind == "ratio" and v is not None and t not in (None, 0)) else None
            rows.append(dict(source=K6_LABEL[x["name"]], signal=sig + (" [neighbor = burner cgroup]" if "burner" in c else ""), ratio_vs_twin=r(ratio, 4), platform=HAS + (" node-4 (HC)" if s == "rec" else ""), run_id=x["cell"],
                             value=r(v, 5), twin_value=r(t, 5), twin_run_id="+".join(t_["cell"] for t_ in tw), kind=kind if ratio is not None else ("absolute" if kind == "absolute" else "ratio undefined (twin 0 or missing)"),
                             source_file="results/k5/final/k6_runs.csv", notes=("CPU per request stand-in for I_g (no dictionary for this service in K6)" if (col == "cpu_per_req_ms" and s != "uid") else "")))
    return len(rows) - n0


I_SIG = [("CPU per request", "cpu_per_req_ms", "ratio"), ("I_g (own dictionary)", "I_g", "ratio"), ("in-handler p50", "p50_ms", "ratio"), ("in-handler p99", "p99_ms", "ratio"), ("client p99", "client_p99_ms", "ratio"),
         ("CPI", "v_cpi", "ratio"), ("MPKI", "v_mpki", "ratio"), ("memory-bound share", "v_mem_bound", "ratio"), ("DRAM-bound share", "v_dram_bound", "ratio"), ("core-bound share", "v_core_bound", "ratio"),
         ("own LLC occupancy MB", "v_occ_mb", "ratio"), ("own MBM GB/s", "v_mbm_gbps", "ratio"), ("neighbor LLC occupancy MB", "nb_occ_mb", "absolute"), ("neighbor MBM GB/s", "nb_mbm_gbps", "absolute"), ("neighbor cores", "nb_cores", "absolute"),
         ("clock MHz (APERF/MPERF)", "mhz", "ratio"), ("phi", "phi", "absolute"), ("phi_sibling", "phi_sibling", "absolute"), ("R_foreign ms/s", "R_foreign_ms_s", "ratio"), ("softirq ms/s", "softirq_ms_s", "ratio"), ("D'/p50 (amount check)", "Dp_over_p50", "absolute")]
I_LABEL = {"c2-uid-net-r1": "C2 contention-grade network neighbor, rep 1 (NIC RX IRQs on CPU 1, stream from node-1)", "c2-uid-net-r2": "C2 contention-grade network neighbor, rep 2", "c2-uid-net-r3": "C2 contention-grade network neighbor, rep 3", "c2-uid-twin-b": "C6' twin vs twin (unique-id, C2 cells)",
           "c1-uid-sib": "C1' SMT sibling (unique-id, matrixprod on CPU 73)", "c3-syn-memwalk128-a": "C3' own 128 MB working set (vs uniform twins)", "c7-syn-memwalk128-stream2": "C7 bandwidth neighbor, stress-ng --stream x2",
           "c7-syn-memwalk128-stream4": "C7 bandwidth neighbor, stress-ng --stream x4", "c7-syn-memwalk128-stream8": "C7 bandwidth neighbor, stress-ng --stream x8", "c4-syn-memwalk24-cache": "C4' cache neighbor, stress-ng --cache x4 (memwalk30 at 24 MB)",
           "c6-uid-twin-b": "C6' twin vs twin (unique-id)", "c6-syn-uniform-b": "C6' twin vs twin (bigsvc uniform)", "c3-syn-memwalk128-b": "C6' twin vs twin (memwalk30 at 128 MB)", "c3-syn-memwalk24-b": "C6' twin vs twin (memwalk30 at 24 MB)"}


def phasei_sig(rows, root, label_suffix, cells_file):
    R = rd(os.path.join(RES, root, "an", "i6_sig.csv")); by = {x["name"]: x for x in R}; cells = cells_of(os.path.join(RES, root, "cells.txt")); n0 = len(rows)
    for x in R:
        if x["name"] not in I_LABEL or not x.get("twin"): continue
        tw = [by[t] for t in x["twin"].split("+") if t in by]
        if not tw: continue
        for sig, col, kind in I_SIG:
            v = f(x.get(col)); tv = [f(t.get(col)) for t in tw]; tv = [t for t in tv if t is not None]
            if v is None and not tv: continue
            t = sum(tv) / len(tv) if tv else None
            ratio = (v / t) if (kind == "ratio" and v is not None and t not in (None, 0)) else None
            rows.append(dict(source=I_LABEL[x["name"]] + label_suffix, signal=sig, ratio_vs_twin=r(ratio, 4), platform=ICE, run_id=cells.get(x["name"], x["name"]), value=r(v, 5), twin_value=r(t, 5),
                             twin_run_id="+".join(cells.get(t_["name"], t_["name"]) for t_ in tw), kind=kind if ratio is not None else ("absolute" if kind == "absolute" else "ratio undefined (twin 0 or missing)"),
                             source_file=f"results/{root}/an/i6_sig.csv", notes=("at the twins' noise floor (~1 MB, ~0.3 GB/s) on unique-id" if (x["svc"] if "svc" in x else x["name"].split("-")[1]) == "uid" and col in ("v_occ_mb", "v_mbm_gbps") else "")))
    return len(rows) - n0


def signatures(out):
    rows = []; counts = {}
    counts["K6"] = k6(rows)
    counts["I 5.0 (i6)"] = phasei_sig(rows, "i6", "", "cells.txt")
    counts["I 5.2 C1' at one clock (i7)"] = phasei_sig(rows, "i7", " [C1E off, queue i7]", "cells.txt")
    if os.path.exists(os.path.join(RES, "i11b", "an", "i6_sig.csv")): counts["I 9 C2 contention-grade (i11b)"] = phasei_sig(rows, "i11b", " [C1E off, queue i11b]", "cells.txt")
    p12 = os.path.join(RES, "figs", "src", "i12_signatures.csv")   # step 11: the milder C2 arm and its twins (analysis/figs_i12_sig.py on node-0)
    if os.path.exists(p12):
        n0 = len(rows)
        for x in rd(p12): rows.append(dict(x, source=x["source"] + " [C1E off, queue i12]"))
        counts["I 10 C2 milder (i12)"] = len(rows) - n0
    keys = ["source", "signal", "ratio_vs_twin", "platform", "run_id", "value", "twin_value", "twin_run_id", "kind", "source_file", "notes"]
    _w(os.path.join(out, "signatures.csv"), keys, rows); return rows, counts


def _w(p, keys, rows):
    with open(p, "w", newline="", encoding="utf-8") as fo:
        w = csv.DictWriter(fo, fieldnames=keys, extrasaction="ignore"); w.writeheader(); w.writerows(rows)


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--out", default=os.path.join(RES, "figs")); a = ap.parse_args(); os.makedirs(a.out, exist_ok=True)
    o = overhead(a.out); print("overhead_cells.csv", len(o), "rows:", Counter((x["platform"].split()[0], x["set"].split()[0]) for x in o))
    v, vc = verdicts(a.out); print("verdicts_by_second.csv", len(v), "rows:", vc)
    s, sc = signatures(a.out); print("signatures.csv", len(s), "rows:", sc)
    # cross-check: Phase G verdict row counts against RESULTS-G 2.5 (C.1 block)
    for lab in ("G ii", "G iii", "G D.5"):
        xs = [x for x in v if x["scenario"].startswith(lab + " ")]
        print(lab, len(xs), "rows", Counter(x["signal_verdict"][:1] for x in xs), "truth", Counter(x["truth"] for x in xs), "dict_only", Counter(x["dict_only_verdict"] for x in xs))


if __name__ == "__main__":
    main()
