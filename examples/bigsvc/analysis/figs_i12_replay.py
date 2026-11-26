#!/usr/bin/env python3
"""Step 9 (RESULTS-I 10): the milder C2 cells of queue i12 (c2m-uid-net-r1..r3, arm Bp-sirq) as verdicts_by_second.csv rows
with CLIENT-side truth: the in-handler timer cannot see a queue in front of the handler, so the truth per second is the
generator's client p50 (ugen.jsonl, lat_p50_us) against the twins' (c2m-uid-twin-a/b): ext when (p50_c - p50_c,twin) /
p50_c >= 0.5, slow-int when p50_c is above the twins' per-second p95, fast otherwise. The verdicts come from the live
on-node scorer's windows (Bp-sirq-r00-score.jsonl, the step 9 rule) and the dictionary-only verdict from k5score.py
replayed with --mask all on the arm's recorder windows. Runs on node-0.
usage: figs_i12_replay.py /local/results/i12 K5SCORE DICT OUTDIR
"""
import csv, json, os, subprocess, sys
from collections import Counter

R, K5, DCT, OUT = sys.argv[1:5]; os.makedirs(OUT, exist_ok=True); PY = sys.executable
base = open(os.path.join(R, "sirq-base.txt")).read().strip() if os.path.exists(os.path.join(R, "sirq-base.txt")) else None


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


def client_seconds(cell_dir, t0, t1):
    """second -> client p50 ms from the generator's per-second records inside the arm"""
    out = {}
    for x in jl(os.path.join(cell_dir, "ugen.jsonl")):
        if isinstance(x, dict) and x.get("lat_p50_us") and t0 <= x.get("t", 0) < t1: out[x["t"] // 1000] = x["lat_p50_us"] / 1000.0
    return out


def seconds(rows):
    S = {}
    for x in rows: S.setdefault(x["t"] // 1000, []).append(x)
    out = {}
    for s, xs in S.items():
        c = Counter(x["row"] for x in xs); top = max(c.values()); row = min(r for r, n in c.items() if n == top)
        med = lambda k: (sorted(v)[len(v) // 2] if (v := [x[k] for x in xs if x.get(k) is not None]) else None)
        out[s] = dict(row=row, holder=Counter(x.get("holder") or "" for x in xs if x["row"] == 1).most_common(1)[0][0] if any(x["row"] == 1 for x in xs) else "", E=med("E"), thr=med("thr"), Ig=med("Ig"), Ig_raw=med("Ig_raw"), phi=med("phi"), phi_irq=med("phi_irq"), rho_eff=med("rho_eff"), amount=med("amount"), sirq=med("sirq"), lat=med("lat"), windows=len(xs))
    return out


NAMES = {1: "1 run-queue contention", 2: "2 own overload", 3: "3 service-time inflation", 4: "4 lock wait", 6: "6 nothing to explain", 7: "7 no verdict", 9: "9 capacity loss to interrupts"}
cells = sorted(os.listdir(R)); twins = [c for c in cells if "-i2-c2m-uid-twin-" in c]; c2 = [c for c in cells if "-i2-c2m-uid-net-r" in c]
tw = {}
for tc in twins:
    rec, A = arms(os.path.join(R, tc))
    for ar in A: tw.update(client_seconds(os.path.join(R, tc), ar["t0"], ar["t1"]))
tl = sorted(tw.values()); p50_twin = tl[len(tl) // 2]; band_hi = tl[int(0.95 * (len(tl) - 1))]
print(f"twins {twins}: client p50 {p50_twin:.4f} ms, per-second p95 {band_hi:.4f} ms, {len(tl)} seconds")
rows = []
for cell in c2:
    cdir = os.path.join(R, cell); rec, A = arms(cdir); recp = os.path.join(R, "recorders", os.path.basename(rec))
    for ar in A:
        sc = seconds([w for w in jl(os.path.join(cdir, "ctl", os.path.basename(ar["files"]["scorer"]))) if "row" in w])
        p = os.path.join(OUT, f"{cell}-{ar['arm']}-r{ar['rep']:02d}.rec.jsonl"); slice_rec(recp, ar["t0"], ar["t1"], p)
        agent = os.path.join(cdir, "ctl", os.path.basename(ar["files"]["agent"]))
        cmd = [PY, K5, "--replay", "--fast", "--recorder", p, "--agent", agent, "--dict", DCT, "--out", p.replace(".rec.jsonl", ".score-mask.jsonl"), "--k", "1", "--slots", "1", "--victim", "uid", "--mask", "all"] + (["--sirq-base", base] if base else [])
        r = subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode: sys.exit("k5score failed: " + r.stderr[-800:])
        off = seconds([w for w in jl(p.replace(".rec.jsonl", ".score-mask.jsonl")) if "row" in w])
        cl = client_seconds(cdir, ar["t0"], ar["t1"])
        for s in sorted(sc):
            L = cl.get(s)
            if L is None: continue
            ext = (L - p50_twin) / L if L > 0 else 0.0; truth = "ext" if ext >= 0.5 else ("slow-int" if L > band_hi else "fast")
            o = sc[s]; d = off.get(s, {}).get("row"); dict_only = {6: "int (E under E_thr)", 7: "ext (E >= E_thr, no signal)"}.get(d, str(d))
            rows.append(dict(scenario="I C2 milder (unique-id, NIC RX IRQs on CPU 1, interrupt moderation off, stream from node-1; C1E off)", platform="r650", second=s, dict_only_verdict=dict_only, signal_verdict=NAMES.get(o["row"], str(o["row"])),
                             holder=o["holder"], truth=truth, truth_ext=round(ext, 4), client_p50_ms=round(L, 4), twin_client_p50_ms=round(p50_twin, 4), srv_lat_ms=o["lat"], E=o["E"], thr=o["thr"], I_g=o["Ig"], I_g_raw=o["Ig_raw"], phi=o["phi"], phi_irq=o["phi_irq"], rho_eff=o["rho_eff"], amount_ms=o["amount"], sirq=o["sirq"], windows=o["windows"],
                             source_cell=f"{cell} arm {ar['arm']} rep {ar['rep']} (live on-node scorer)", twin_cells=";".join(twins), dictionary=os.path.basename(DCT), sirq_base=base))
        print(f"  {cell}: {len(sc)} seconds; rows {dict(Counter(v['row'] for v in sc.values()))}; truth {dict(Counter(r['truth'] for r in rows if r['source_cell'].startswith(cell)))}; client p50 med {sorted(cl.values())[len(cl)//2] if cl else None}")
if rows:
    with open(os.path.join(OUT, "i12_verdicts_by_second.csv"), "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys())); w.writeheader(); w.writerows(rows)
print("rows", len(rows))
