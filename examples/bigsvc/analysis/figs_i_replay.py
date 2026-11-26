#!/usr/bin/env python3
"""Offline scorer replay for the Phase I signature cells (results/figs/verdicts_by_second.csv, Ice Lake rows).
Runs on node-0 against /local/results/{i3,i5,i6,i7}: slices each arm's recorder windows by the arm record's [t0, t1),
replays k5score.py twice (--mask all: the dictionary-only verdict; no mask: every signal on), and writes one row per
second: majority row over the second's windows, the row-1 holder, and the twin truth as in Phase G (per second
Lat = the n-weighted median of the window p50s; ext when (Lat - p50_twin) / Lat >= 0.5, slow-int when Lat is above the
twin's per-second p95, fast otherwise; p50_twin = the n-weighted median over both twin cells' windows).
usage: i_replay.py OUTDIR
"""
import json, os, subprocess, sys
from collections import Counter

R = "/local/results"
K5 = "/local/results/figs-work/k5score.py"
OUT = sys.argv[1]; os.makedirs(OUT, exist_ok=True)
PY = sys.executable


def jl(p):
    out = []
    for l in open(p):
        try: out.append(json.loads(l))
        except Exception: pass
    return out


def arms(cell_dir):
    recs = jl(os.path.join(cell_dir, "ctl", "cell.jsonl"))
    hdr = next(r for r in recs if r.get("event") == "cell")
    v = hdr["victim"]
    if isinstance(v, str): v = eval(v)
    return v["recorder"], [r for r in recs if r.get("event") == "arm"]


def rec_path(root, rec):
    return os.path.join(root, "recorders", os.path.basename(rec))


def slice_rec(src, t0, t1, dst):
    n = 0
    with open(dst, "w") as f:
        for l in open(src):
            i = l.find('"t":')
            if i < 0: continue
            try: t = int(l[i + 4:l.index(",", i)])
            except Exception: continue
            if t0 <= t < t1: f.write(l); n += 1
    return n


def windows(path):
    W = []
    for w in jl(path):
        al = w.get("lat") if w.get("lat") else w.get("all")
        if not al or not al[0]: continue
        W.append((w["t"], al[0], al[1] / 1e6))
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


def replay(rec, agent, dct, out, k, slots, go, victim, mask):
    cmd = [PY, K5, "--replay", "--fast", "--recorder", rec, "--agent", agent, "--dict", dct, "--out", out, "--k", str(k), "--slots", str(slots), "--victim", victim]
    if go: cmd.append("--go")
    if mask: cmd += ["--mask", mask]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0: sys.exit(f"k5score failed: {' '.join(cmd)}\n{r.stderr[-1500:]}")
    return [x for x in jl(out) if "row" in x]


def fit(rec, agent, t0, t1, dct, k, cg):
    cmd = [PY, K5, "--fit", rec, "--fit-agent", agent, "--fit-from-t", str(t0), "--fit-to-t", str(t1), "--dict", dct, "--k", str(k), "--fit-cg", cg]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0: sys.exit(f"fit failed: {' '.join(cmd)}\n{r.stderr[-1500:]}")
    print("fit", os.path.basename(dct), r.stdout.strip())


def seconds(score_rows):
    S = {}
    for x in score_rows: S.setdefault(x["t"] // 1000, []).append(x)
    out = {}
    for s, xs in S.items():
        c = Counter(x["row"] for x in xs); top = max(c.values())
        row = min(r for r, n in c.items() if n == top)
        hold = Counter(x.get("holder") or "" for x in xs if x["row"] == 1)
        holder = hold.most_common(1)[0][0] if hold else ""
        E = sorted(x["E"] for x in xs)[len(xs) // 2]
        ig = [x["Ig"] for x in xs if x.get("Ig") is not None]
        phi = [x["phi"] for x in xs if x.get("phi") is not None]
        out[s] = dict(row=row, holder=holder, E=E, Ig=(sorted(ig)[len(ig) // 2] if ig else None), phi=(sum(phi) / len(phi) if phi else None), windows=len(xs), sub=Counter(x.get("sub") or "" for x in xs).most_common(1)[0][0])
    return out


NAMES = {1: "1 run-queue contention", 2: "2 own overload", 3: "3 service-time inflation", 4: "4 lock wait", 6: "6 nothing to explain", 7: "7 no verdict"}


def one(label, platform, root, cell, arm_name, reps, twin_cells, dct, k, slots, go, victim, rows_out):
    cdir = os.path.join(root, cell); rec, A = arms(cdir); recp = rec_path(root, rec)
    # twin: both twin cells' windows (arm = the same arm name on a twin cell, or the arm given)
    tw = []
    for tc, ta in twin_cells:
        tdir = os.path.join(root, tc); trec, TA = arms(tdir); trecp = rec_path(root, trec)
        for a in TA:
            if a["arm"] != ta: continue
            p = os.path.join(OUT, f"{tc}-{ta}-r{a['rep']:02d}.rec.jsonl"); slice_rec(trecp, a["t0"], a["t1"], p); tw.extend(windows(p))
    p50_twin = wmedian([(p, n) for _, n, p in tw]); tl = sorted(per_second_lat(tw).values()); band_hi = tl[int(0.95 * (len(tl) - 1))]
    print(f"{label}: twin p50 {p50_twin:.4f} ms, twin per-second p95 {band_hi:.4f} ms, {len(tw)} twin windows")
    for a in A:
        if a["arm"] != arm_name or a["rep"] not in reps: continue
        agent = os.path.join(cdir, "ctl", os.path.basename(a["files"]["agent"]))
        p = os.path.join(OUT, f"{cell}-{arm_name}-r{a['rep']:02d}.rec.jsonl"); n = slice_rec(recp, a["t0"], a["t1"], p)
        on = seconds(replay(p, agent, dct, p.replace(".rec.jsonl", ".score-on.jsonl"), k, slots, go, victim, ""))
        off = seconds(replay(p, agent, dct, p.replace(".rec.jsonl", ".score-mask.jsonl"), k, slots, go, victim, "all"))
        lat = per_second_lat(windows(p))
        for s in sorted(on):
            L = lat.get(s)
            if L is None: continue
            ext = (L - p50_twin) / L if L > 0 else 0.0
            truth = "ext" if ext >= 0.5 else ("slow-int" if L > band_hi else "fast")
            d = off.get(s, {}).get("row")
            dict_only = {6: "int (E ~ 0)", 7: "ext (E >> 0, no signal)"}.get(d, str(d))
            rows_out.append(dict(scenario=label, platform=platform, second=s, dict_only_verdict=dict_only, signal_verdict=NAMES.get(on[s]["row"], str(on[s]["row"])),
                                 holder=on[s]["holder"], truth=truth, truth_ext=round(ext, 4), lat_ms=round(L, 5), twin_p50_ms=round(p50_twin, 5), E=on[s]["E"], I_g=on[s]["Ig"], phi=(round(on[s]["phi"], 4) if on[s]["phi"] is not None else None),
                                 mbm_sub=on[s]["sub"], windows=on[s]["windows"], source_cell=f"{cell} arm {arm_name} rep {a['rep']}", twin_cells=";".join(f"{c}:{t}" for c, t in twin_cells), dictionary=os.path.basename(dct)))
        print(f"  {cell} {arm_name} r{a['rep']}: {n} windows, {len(on)} seconds, rows on {Counter(v['row'] for v in on.values())}, mask {Counter(v['row'] for v in off.values())}")


rows = []
# dictionaries for the synthetic service: k5score's pooled form, fit from the sweep arms (memwalk30 at 24 MB: queue i6; at 128 MB: queue i5)
d24 = os.path.join(OUT, "dict-syn-memwalk30-24.k5.json"); d128 = os.path.join(OUT, "dict-syn-memwalk30-128.k5.json")
for root, cell, dct in ((f"{R}/i6", "20261118-180428-i2-sweep-syn-memwalk30-24", d24), (f"{R}/i5", "20261118-174507-i2-sweep-syn-memwalk30-128", d128)):
    rec, A = arms(os.path.join(root, cell)); a = A[0]
    fit(rec_path(root, rec), os.path.join(root, cell, "ctl", os.path.basename(a["files"]["agent"])), a["t0"], a["t1"], dct, 4, "syn")

one("I C1' SMT sibling (unique-id, C1E off)", "r650", f"{R}/i7", "20261118-215008-i2-c1-uid-sib", "sigF", [0],
    [("20261118-214431-i2-c6-uid-twin-a", "sigF"), ("20261118-215547-i2-c6-uid-twin-b", "sigF")], f"{R}/i7/dict/dict-uid.json", 1, 1, False, "uid", rows)
one("I C2 network neighbor (unique-id, set B')", "r650", f"{R}/i3", "20261117-211942-i2-bp-uid-net-mid", "defBp", [0, 1, 2, 3, 4],
    [("20261117-210634-i2-bp-uid-none-mid", "defBp")], f"{R}/i4/dict/dict-uid.json", 1, 1, False, "uid", rows)
one("I C4' cache neighbor (memwalk30 24 MB)", "r650", f"{R}/i6", "20261118-192808-i2-c4-syn-memwalk24-cache", "sigF", [0],
    [("20261118-192144-i2-c3-syn-memwalk24-a", "sigF"), ("20261118-193432-i2-c3-syn-memwalk24-b", "sigF")], d24, 4, 3, True, "syn", rows)
one("I C7 x4 bandwidth neighbor (memwalk30 128 MB)", "r650", f"{R}/i6", "20261118-185746-i2-c7-syn-memwalk128-stream4", "sigF", [0],
    [("20261118-184456-i2-c3-syn-memwalk128-a", "sigF"), ("20261118-191035-i2-c3-syn-memwalk128-b", "sigF")], d128, 4, 3, True, "syn", rows)

import csv
keys = list(rows[0].keys())
with open(os.path.join(OUT, "i_verdicts_by_second.csv"), "w", newline="") as f:
    w = csv.DictWriter(f, fieldnames=keys); w.writeheader(); w.writerows(rows)
print("rows", len(rows))
