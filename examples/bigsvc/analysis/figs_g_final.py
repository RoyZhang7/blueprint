#!/usr/bin/env python3
"""Step 11: the Phase G scenarios (ii, iii, D.5 from results/g-final/windows/*.csv, Haswell node-4) re-scored per window with the
final rule set and reduced to one row per second (majority row; medians of the signals): E (the window's own, as stored) against
the Haswell sweep's E_thr(rho) (results/figs/src/dict-k45-rec-ethr.json, interpolated on the window's rho), phi_cpuset > 0.05 ->
row 1, I_g > 1.15 -> row 3, |I_g - 1| <= 0.15 and Q > 10 ms/s -> row 4, I_g ok and slot_busy >= 0.9 -> row 2, else 7; no latency
sample -> 7. Phase G's windows carry no softirq columns (the lean probe), so phi_irq is undefined and the corrected I_g equals the
stored I_g; stated in notes. signal_verdict_old = the stored per-second row (row_sec, Phase G's twin-relative thresholds).
usage: figs_g_final.py RESULTS_DIR OUT.csv
"""
import csv, json, os, sys
from collections import Counter

RES, OUT = sys.argv[1:3]
d = json.load(open(os.path.join(RES, "figs", "src", "dict-k45-rec-ethr.json"))); R_ = [x["rho"] for x in d["steps"]]; T_ = [x["e_thr"] for x in d["steps"]]


def e_thr(r):
    if r <= R_[0]: return T_[0]
    if r >= R_[-1]: return T_[-1]
    for i in range(1, len(R_)):
        if r <= R_[i]:
            fr = (r - R_[i - 1]) / (R_[i] - R_[i - 1]); return T_[i - 1] * (1 - fr) + T_[i] * fr
    return T_[-1]


def f(x):
    try: return float(x)
    except Exception: return None


NAMES = {1: "1 run-queue contention", 2: "2 own overload", 3: "3 service-time inflation", 4: "4 lock wait", 6: "6 nothing to explain", 7: "7 no verdict"}
SC = [("ii", "G ii served step past the profile (v2, no aggressor)"), ("iii", "G iii recommendation + 1 aggressor per core (v3)"), ("d5-mutex40", "G D.5 VioMutex 40 %, no aggressor (v3.2)")]
rows = []
for key, label in SC:
    W = list(csv.DictReader(open(os.path.join(RES, "g-final", "windows", key + ".csv")))); S = {}
    for w in W: S.setdefault(w["sec"], []).append(w)
    for sec, xs in S.items():
        rs = []; th = []
        for w in xs:
            E = f(w["E"]); rho = f(w["rho"]); lat = f(w["lat_ms"]); phi = f(w["phi_cpuset"]); Ig = f(w["I_g"]); Q = f(w["Q_ms_s"]) or 0.0; slot = f(w["slot_busy"]) or 0.0
            thr = e_thr(rho) if rho is not None else 0.15; th.append(thr)
            if lat is None or E is None: r = 7
            elif E < thr: r = 6
            elif phi is not None and phi > 0.05: r = 1
            elif Ig is not None and Ig > 1.15: r = 3
            elif Ig is not None and abs(Ig - 1) <= 0.15 and Q > 10.0: r = 4
            elif Ig is not None and abs(Ig - 1) <= 0.15 and slot >= 0.9: r = 2
            else: r = 7
            rs.append(r)
        c = Counter(rs); top = max(c.values()); row = min(r for r, n in c.items() if n == top); old = int(float(xs[0]["row_sec"]))
        med = lambda k: (sorted(v)[len(v) // 2] if (v := [f(w[k]) for w in xs if f(w[k]) is not None]) else None)
        rows.append(dict(scenario=label, second=sec, signal_verdict=NAMES[row], signal_verdict_old=NAMES.get(old, str(old)), truth=xs[0]["truth"], truth_ext=xs[0]["truth_ext"], E=xs[0]["E_sec"], thr=round(sorted(th)[len(th) // 2], 4), I_g=med("I_g"), phi=med("phi_cpuset"), Q_ms_s=med("Q_ms_s"), slot_busy=med("slot_busy"), rho=med("rho"), windows=len(xs), rule_version="s11-final",
                         notes="final rule on Phase G's stored windows: E against the Haswell sweep's E_thr(rho); no softirq columns on the lean probe, so phi_irq is undefined and I_g is the stored (uncorrected) value; signal_verdict_old = Phase G's row_sec (twin-relative thresholds)"))
    xs = [r for r in rows if r["scenario"] == label]; ch = sum(1 for r in xs if r["signal_verdict"] != r["signal_verdict_old"])
    print(f"{key}: {len(xs)} s; new {dict(Counter(r['signal_verdict'][:1] for r in xs))} old {dict(Counter(r['signal_verdict_old'][:1] for r in xs))} changed {ch} ({100*ch/len(xs):.1f} %) : {dict(Counter((r['signal_verdict_old'][:1]+'->'+r['signal_verdict'][:1]) for r in xs if r['signal_verdict'] != r['signal_verdict_old']))}; thr med {sorted(r['thr'] for r in xs)[len(xs)//2]}")
with open(OUT, "w", newline="") as fo:
    w = csv.DictWriter(fo, fieldnames=list(rows[0].keys())); w.writeheader(); w.writerows(rows)
print("rows", len(rows))
