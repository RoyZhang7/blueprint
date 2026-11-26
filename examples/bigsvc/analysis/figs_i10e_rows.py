#!/usr/bin/env python3
"""Step 9 (RESULTS-I 10): the HC-on-r650 reserve 1 and 3 cells of queue i10e as verdicts_by_second.csv rows, from the live
on-node scorer's windows (sigS-r00-score.jsonl: E_thr(rho), k = 4): per second the majority row, the row-1 holder, the
dictionary-only verdict (E against thr with no signal) and the twin truth from the twins S0 and S1 (server-side p50, as
the step-4 rows). Also re-scores the four cells offline with the step 9 scorer when K5 is given (phi_irq is None on node-4:
the burner is not softirq). usage: figs_i10e_rows.py /local/results/i10e OUT.csv
"""
import csv, json, os, sys
from collections import Counter

R, OUT = sys.argv[1], sys.argv[2]


def jl(p):
    out = []
    for l in open(p):
        try: out.append(json.loads(l))
        except Exception: pass
    return out


def arm_of(cell):
    recs = jl(os.path.join(R, cell, "ctl", "cell.jsonl")); hdr = next(r for r in recs if r.get("event") == "cell"); v = hdr["victim"]
    if isinstance(v, str): v = eval(v)
    return v["recorder"], next(r for r in recs if r.get("event") == "arm")


def windows(rec, t0, t1):
    W = []
    for w in jl(rec):
        if not (t0 <= w.get("t", 0) < t1): continue
        al = w.get("lat") if w.get("lat") else w.get("all")
        if al and al[0]: W.append((w["t"], al[0], al[1] / 1e6))
    return W


def wmedian(pairs):
    pairs = sorted(pairs); tot = sum(n for _, n in pairs); acc = 0
    for v, n in pairs:
        acc += n
        if acc >= tot / 2: return v
    return pairs[-1][0] if pairs else float("nan")


def per_second_lat(W):
    S = {}
    for t, n, p50 in W: S.setdefault(t // 1000, []).append((p50, n))
    return {s: wmedian(v) for s, v in S.items()}


cells = sorted(os.listdir(R)); C = {k: next(c for c in cells if c.endswith("-i2-c5-rec-" + k)) for k in ("twin-S0", "res1", "res3", "twin-S1")}
rec0, a0 = arm_of(C["twin-S0"]); rec1, a1 = arm_of(C["twin-S1"]); recp = os.path.join(R, "recorders", os.path.basename(rec0))
tw = windows(recp, a0["t0"], a0["t1"]) + windows(recp, a1["t0"], a1["t1"])
p50_twin = wmedian([(p, n) for _, n, p in tw]); tl = sorted(per_second_lat(tw).values()); band_hi = tl[int(0.95 * (len(tl) - 1))]
print(f"twins: p50 {p50_twin:.5f} ms, per-second p95 {band_hi:.5f}, {len(tw)} windows")
NAMES = {1: "1 run-queue contention", 2: "2 own overload", 3: "3 service-time inflation", 4: "4 lock wait", 6: "6 nothing to explain", 7: "7 no verdict", 8: "8 interrupt load, no latency harm", 9: "9 capacity loss to interrupts"}
rows = []
for key, label in (("res1", "HC reserve 1 (r650 node-4, recommendation 5 000 req/s on 4 cores, burner on core 12)"), ("res3", "HC reserve 3 (r650 node-4)")):
    cell = C[key]; rec, a = arm_of(cell); sc = [w for w in jl(os.path.join(R, cell, "ctl", os.path.basename(a["files"]["scorer"]))) if "row" in w]
    lat = per_second_lat(windows(recp, a["t0"], a["t1"]))
    S = {}
    for w in sc: S.setdefault(w["t"] // 1000, []).append(w)
    for s in sorted(S):
        xs = S[s]; L = lat.get(s)
        if L is None: continue
        c = Counter(x["row"] for x in xs); top = max(c.values()); row = min(r for r, n in c.items() if n == top)
        hold = Counter(x.get("holder") or "" for x in xs if x["row"] == 1); holder = hold.most_common(1)[0][0] if hold else ""
        med = lambda k: (sorted(v)[len(v) // 2] if (v := [x[k] for x in xs if x.get(k) is not None]) else None)
        E = med("E"); thr = med("thr"); ext = (L - p50_twin) / L if L > 0 else 0.0
        truth = "ext" if ext >= 0.5 else ("slow-int" if L > band_hi else "fast")
        rows.append(dict(scenario=label, platform="r650", second=s, dict_only_verdict=("ext (E >= E_thr, no signal)" if E is not None and thr is not None and E >= thr else "int (E under E_thr)"),
                         signal_verdict=NAMES.get(row, str(row)), holder=holder, truth=truth, truth_ext=round(ext, 4), E=E, thr=thr, I_g=med("Ig"), phi=med("phi"), lat_ms=round(L, 5), twin_p50_ms=round(p50_twin, 5), windows=len(xs),
                         source_cell=f"{cell} arm sigS rep 0 (live on-node scorer, k = 4, E_thr from the queue's sweep)", twin_cells=f"{C['twin-S0']};{C['twin-S1']}"))
    print(f"  {cell}: {len(S)} seconds; rows {dict(Counter(r['signal_verdict'][:1] for r in rows if r['source_cell'].startswith(cell)))}; truth {dict(Counter(r['truth'] for r in rows if r['source_cell'].startswith(cell)))}")
keys = list(rows[0].keys())
with open(OUT, "w", newline="") as f:
    w = csv.DictWriter(f, fieldnames=keys); w.writeheader(); w.writerows(rows)
print("rows", len(rows))
