#!/usr/bin/env python3
"""Step 8 (RESULTS-I 9): the contention-grade C2 cells of queue i11 replayed offline for verdicts_by_second.csv, as
figs_i_replay.py did for the step-4 cells: each C2 arm's recorder windows cut by [t0, t1), k5score.py replayed twice
(--mask all: the dictionary-only verdict; every signal on, with the queue's softirq baseline), one row per second with the
majority row, the row-1 holder and the twin truth (Lat_s = the n-weighted median of the second's window p50s; ext when
(Lat - p50_twin) / Lat >= 0.5, slow-int when Lat is above the twins' per-second p95, fast otherwise; p50_twin over both
twin cells). Runs on node-0.
usage: figs_i11_replay.py /local/results/i11 K5SCORE DICT OUTDIR [--sirq-base X]
"""
import argparse, csv, json, os, subprocess, sys
from collections import Counter

ap = argparse.ArgumentParser(); ap.add_argument("root"); ap.add_argument("k5"); ap.add_argument("dict"); ap.add_argument("out"); ap.add_argument("--sirq-base", default=None)
a = ap.parse_args(); os.makedirs(a.out, exist_ok=True); PY = sys.executable
if a.sirq_base is None and os.path.exists(os.path.join(a.root, "sirq-base.txt")): a.sirq_base = open(os.path.join(a.root, "sirq-base.txt")).read().strip()


def jl(p):
    out = []
    for l in open(p):
        try: out.append(json.loads(l))
        except Exception: pass
    return out


def arms(cell_dir):
    recs = jl(os.path.join(cell_dir, "ctl", "cell.jsonl")); hdr = next(r for r in recs if r.get("event") == "cell"); v = hdr["victim"]
    if isinstance(v, str): v = eval(v)
    return v["recorder"], [r for r in recs if r.get("event") == "arm"]


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


def replay(rec, agent, out, mask):
    cmd = [PY, a.k5, "--replay", "--fast", "--recorder", rec, "--agent", agent, "--dict", a.dict, "--out", out, "--k", "1", "--slots", "1", "--victim", "uid"]
    if a.sirq_base: cmd += ["--sirq-base", str(a.sirq_base)]
    if mask: cmd += ["--mask", mask]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0: sys.exit(f"k5score failed: {' '.join(cmd)}\n{r.stderr[-1500:]}")
    return [x for x in jl(out) if "row" in x]


def seconds(rows):
    S = {}
    for x in rows: S.setdefault(x["t"] // 1000, []).append(x)
    out = {}
    for s, xs in S.items():
        c = Counter(x["row"] for x in xs); top = max(c.values()); row = min(r for r, n in c.items() if n == top)
        hold = Counter(x.get("holder") or "" for x in xs if x["row"] == 1); holder = hold.most_common(1)[0][0] if hold else ""
        med = lambda k: (sorted(v)[len(v) // 2] if (v := [x[k] for x in xs if x.get(k) is not None]) else None)
        out[s] = dict(row=row, holder=holder, E=med("E"), thr=med("thr"), Ig=med("Ig"), Ig_raw=med("Ig_raw"), phi=med("phi"), phi_irq=med("phi_irq"), rho_eff=med("rho_eff"), amount=med("amount"), sirq=med("sirq"), windows=len(xs))
    return out


NAMES = {1: "1 run-queue contention", 2: "2 own overload", 3: "3 service-time inflation", 4: "4 lock wait", 6: "6 nothing to explain", 7: "7 no verdict", 8: "8 interrupt load on our CPUs, no latency harm", 9: "9 capacity loss to interrupts"}
cells = sorted(os.listdir(a.root))
twins = [c for c in cells if "-i2-c2-uid-twin-" in c]; c2 = [c for c in cells if "-i2-c2-uid-net-r" in c]
tw = []
for tc in twins:
    rec, A = arms(os.path.join(a.root, tc)); recp = os.path.join(a.root, "recorders", os.path.basename(rec))
    for ar in A:
        p = os.path.join(a.out, f"{tc}-{ar['arm']}-r{ar['rep']:02d}.rec.jsonl"); slice_rec(recp, ar["t0"], ar["t1"], p); tw.extend(windows(p))
p50_twin = wmedian([(p, n) for _, n, p in tw]); tl = sorted(per_second_lat(tw).values()); band_hi = tl[int(0.95 * (len(tl) - 1))]
print(f"twins {twins}: p50 {p50_twin:.5f} ms, per-second p95 {band_hi:.5f} ms, {len(tw)} windows")
rows = []
for cell in c2:
    cdir = os.path.join(a.root, cell); rec, A = arms(cdir); recp = os.path.join(a.root, "recorders", os.path.basename(rec))
    for ar in A:
        agent = os.path.join(cdir, "ctl", os.path.basename(ar["files"]["agent"])); p = os.path.join(a.out, f"{cell}-{ar['arm']}-r{ar['rep']:02d}.rec.jsonl"); n = slice_rec(recp, ar["t0"], ar["t1"], p)
        on = seconds(replay(p, agent, p.replace(".rec.jsonl", ".score-on.jsonl"), "")); off = seconds(replay(p, agent, p.replace(".rec.jsonl", ".score-mask.jsonl"), "all")); lat = per_second_lat(windows(p))
        for s in sorted(on):
            L = lat.get(s)
            if L is None: continue
            ext = (L - p50_twin) / L if L > 0 else 0.0; truth = "ext" if ext >= 0.5 else ("slow-int" if L > band_hi else "fast")
            d = off.get(s, {}).get("row"); dict_only = {6: "int (E under E_thr)", 7: "ext (E >= E_thr, no signal)", 8: "int (E under E_thr)", 9: "int (E under E_thr)"}.get(d, str(d))
            o = on[s]
            rows.append(dict(scenario="I C2 contention-grade (unique-id, NIC RX IRQs on CPU 1, stream from node-1; C1E off)", platform="r650", second=s, dict_only_verdict=dict_only, signal_verdict=NAMES.get(o["row"], str(o["row"])),
                             holder=o["holder"], truth=truth, truth_ext=round(ext, 4), lat_ms=round(L, 5), twin_p50_ms=round(p50_twin, 5), E=o["E"], thr=o["thr"], I_g=o["Ig"], I_g_raw=o["Ig_raw"], phi=o["phi"], phi_irq=o["phi_irq"], rho_eff=o["rho_eff"], amount_ms=o["amount"], sirq=o["sirq"], windows=o["windows"],
                             source_cell=f"{cell} arm {ar['arm']} rep {ar['rep']}", twin_cells=";".join(twins), dictionary=os.path.basename(a.dict), sirq_base=a.sirq_base))
        print(f"  {cell} {ar['arm']} r{ar['rep']}: {n} windows, {len(on)} seconds; rows on {dict(Counter(v['row'] for v in on.values()))}, mask {dict(Counter(v['row'] for v in off.values()))}; truth {dict(Counter(r['truth'] for r in rows if r['source_cell'].startswith(cell)))}")
if rows:
    keys = list(rows[0].keys())
    with open(os.path.join(a.out, "i11_verdicts_by_second.csv"), "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=keys); w.writeheader(); w.writerows(rows)
print("rows", len(rows))
