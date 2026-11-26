#!/usr/bin/env python3
"""Step 11 (RESULTS-I 11): every r650 scenario of verdicts_by_second.csv replayed with the final scorer (k5score.py: E_thr(rho)
from each service's own sweep, k as given (the pod's threads), I_g with the victim CPUs' softirq above baseline subtracted,
phi_irq and row 9, escalation on E only). One code path for C1', C4', C7 x4, HC reserve 1 / 3, the saturating C2 and the
milder C2. Per arm: the recorder windows cut by [t0, t1), two replays (--mask all = the dictionary-only verdict; every
signal on), one row per second (majority row, row-1 holder, medians of the signals) with the twin truth: server-side
(Lat_s = the n-weighted median of the second's window p50s against the twins' p50, as the step-4 rows) or, for the milder
C2, client-side (the generator's per-second client p50 against the twins'); the truth source is written in every row.
The softirq baseline per scenario is the twins' own mean share from the sampler's /proc/stat lines (the queue's measured
baseline where one exists). Runs on node-0. usage: figs_final_replay.py OUTDIR
"""
import csv, json, os, subprocess, sys
from collections import Counter

K5 = "/local/results/figs-work/k5score.py"; W8 = "/local/results/figs-work/s8"; R = "/local/results"
OUT = sys.argv[1]; os.makedirs(OUT, exist_ok=True); PY = sys.executable
NAMES = {1: "1 run-queue contention", 2: "2 own overload", 3: "3 service-time inflation", 4: "4 lock wait", 6: "6 nothing to explain", 7: "7 no verdict", 9: "9 capacity loss to interrupts"}


def jl(p):
    out = []
    for l in open(p):
        try: out.append(json.loads(l))
        except Exception: pass
    return out


def cell_dir(root, name):
    return next(os.path.join(root, c) for c in sorted(os.listdir(root)) if c.endswith("-i2-" + name))


def arms(cdir):
    recs = jl(os.path.join(cdir, "ctl", "cell.jsonl")); hdr = next(r for r in recs if r.get("event") == "cell"); v = hdr["victim"]
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


def client_seconds(cdir, t0, t1):
    out = {}
    for x in jl(os.path.join(cdir, "ugen.jsonl")):
        if isinstance(x, dict) and x.get("lat_p50_us") and t0 <= x.get("t", 0) < t1: out[x["t"] // 1000] = x["lat_p50_us"] / 1000.0
    return out


def sirq_share(agent_path):
    """the mean softirq share of the victim's CPUs over an agent file (the sampler's /proc/stat lines)"""
    T = jl(agent_path); st = next((d for d in T if d.get("event") == "start"), None)
    cpus = [str(c) for g in (st.get("cg") or []) if g.get("victim") for c in g.get("cpus", [])] if st else []
    v = []
    for d in T:
        ps = d.get("pst") or {}; x = [ps[c][2] / (ps[c][0] + ps[c][3]) for c in cpus if c in ps and ps[c][0] + ps[c][3] > 0]
        if x: v.append(sum(x) / len(x))
    return (sum(v) / len(v)) if v else 0.0


def replay(rec, agent, dct, out, k, slots, go, victim, base, mask):
    cmd = [PY, K5, "--replay", "--fast", "--recorder", rec, "--agent", agent, "--dict", dct, "--out", out, "--k", str(k), "--slots", str(slots), "--victim", victim, "--sirq-base", f"{base:.4f}"]
    if go: cmd.append("--go")
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
        amt = [x["amount"] for x in xs if x["row"] == row and x.get("amount") is not None]; src = Counter(x.get("amount_src") or "" for x in xs if x["row"] == row).most_common(1)[0][0]
        out[s] = dict(row=row, holder=holder, E=med("E"), thr=med("thr"), Ig=med("Ig"), Ig_raw=med("Ig_raw"), phi=med("phi"), phi_irq=med("phi_irq"), rho_eff=med("rho_eff"), amount=(sorted(amt)[len(amt) // 2] if amt else None), amount_src=src, sirq=med("sirq"), sub=Counter(x.get("sub") or "" for x in xs).most_common(1)[0][0], lat=med("lat"), windows=len(xs))
    return out


# scenario specs: label, results root, [(cell, arm, reps)], [(twin cell, arm)], dictionary, k, slots, go, victim, truth source, fixed baseline (None = the twins'), csv scenario key
SPECS = [
    ("I C1' SMT sibling (unique-id, C1E off)", f"{R}/i7", [("c1-uid-sib", "sigF", [0])], [("c6-uid-twin-a", "sigF"), ("c6-uid-twin-b", "sigF")], f"{W8}/dict-uid-ethr.json", 1, 1, False, "uid", "server", None),
    ("I C4' cache neighbor (memwalk30 24 MB)", f"{R}/i6", [("c4-syn-memwalk24-cache", "sigF", [0])], [("c3-syn-memwalk24-a", "sigF"), ("c3-syn-memwalk24-b", "sigF")], f"{W8}/dict-syn-memwalk24-ethr.json", 4, 3, True, "syn", "server", None),
    ("I C7 x4 bandwidth neighbor (memwalk30 128 MB)", f"{R}/i6", [("c7-syn-memwalk128-stream4", "sigF", [0])], [("c3-syn-memwalk128-a", "sigF"), ("c3-syn-memwalk128-b", "sigF")], f"{W8}/dict-syn-memwalk128-ethr.json", 4, 3, True, "syn", "server", None),
    ("I HC reserve 1 (r650 node-4, recommendation 5 000 req/s on 4 cores, burner on core 12)", f"{R}/i10e", [("c5-rec-res1", "sigS", [0])], [("c5-rec-twin-S0", "sigS"), ("c5-rec-twin-S1", "sigS")], f"{R}/i10e/dict/dict-rec.json", 4, 4, True, "rec", "server", None),
    ("I HC reserve 3 (r650 node-4)", f"{R}/i10e", [("c5-rec-res3", "sigS", [0])], [("c5-rec-twin-S0", "sigS"), ("c5-rec-twin-S1", "sigS")], f"{R}/i10e/dict/dict-rec.json", 4, 4, True, "rec", "server", None),
    ("I C2 contention-grade (unique-id, NIC RX IRQs on CPU 1, stream from node-1; C1E off)", f"{R}/i11b", [("c2-uid-net-r1", "Bp-sirq", [0])], [("c2-uid-twin-a", "Bp-sirq"), ("c2-uid-twin-b", "Bp-sirq")], f"{W8}/dict-uid-ethr.json", 1, 1, False, "uid", "server", 0.142),
    ("I C2 milder (unique-id, NIC RX IRQs on CPU 1, interrupt moderation off, stream from node-1; C1E off)", f"{R}/i12", [("c2m-uid-net-r1", "Bp-sirq", [0]), ("c2m-uid-net-r2", "Bp-sirq", [0]), ("c2m-uid-net-r3", "Bp-sirq", [0])], [("c2m-uid-twin-a", "Bp-sirq"), ("c2m-uid-twin-b", "Bp-sirq")], f"{W8}/dict-uid-ethr.json", 1, 1, False, "uid", "client", 0.142),
]
rows = []
for label, root, cells, twins, dct, k, slots, go, victim, truth_src, base_fixed in SPECS:
    tw = []; twc = {}; bases = []
    for tc, ta in twins:
        tdir = cell_dir(root, tc); trec, TA = arms(tdir); trecp = os.path.join(root, "recorders", os.path.basename(trec))
        for a in TA:
            if a["arm"] != ta: continue
            p = os.path.join(OUT, f"{os.path.basename(tdir)}-{ta}-r{a['rep']:02d}.rec.jsonl"); slice_rec(trecp, a["t0"], a["t1"], p); tw.extend(windows(p))
            if truth_src == "client": twc.update(client_seconds(tdir, a["t0"], a["t1"]))
            bases.append(sirq_share(os.path.join(tdir, "ctl", os.path.basename(a["files"]["agent"]))))
    base = base_fixed if base_fixed is not None else (sum(bases) / len(bases) if bases else 0.0)
    if truth_src == "client":
        tl = sorted(twc.values()); p50_twin = tl[len(tl) // 2]; band_hi = tl[int(0.95 * (len(tl) - 1))]
    else:
        p50_twin = wmedian([(p, n) for _, n, p in tw]); tl = sorted(per_second_lat(tw).values()); band_hi = tl[int(0.95 * (len(tl) - 1))]
    print(f"{label[:60]}: twin p50 {p50_twin:.5f} ms ({truth_src}), per-second p95 {band_hi:.5f}, base {base:.4f}")
    for cname, arm_name, reps in cells:
        cdir = cell_dir(root, cname); rec, A = arms(cdir); recp = os.path.join(root, "recorders", os.path.basename(rec))
        for a in A:
            if a["arm"] != arm_name or a["rep"] not in reps: continue
            agent = os.path.join(cdir, "ctl", os.path.basename(a["files"]["agent"]))
            p = os.path.join(OUT, f"{os.path.basename(cdir)}-{arm_name}-r{a['rep']:02d}.rec.jsonl"); n = slice_rec(recp, a["t0"], a["t1"], p)
            on = seconds(replay(p, agent, dct, p.replace(".rec.jsonl", ".score-on.jsonl"), k, slots, go, victim, base, ""))
            off = seconds(replay(p, agent, dct, p.replace(".rec.jsonl", ".score-mask.jsonl"), k, slots, go, victim, base, "all"))
            lat = client_seconds(cdir, a["t0"], a["t1"]) if truth_src == "client" else per_second_lat(windows(p))
            for s in sorted(on):
                L = lat.get(s)
                if L is None: continue
                ext = (L - p50_twin) / L if L > 0 else 0.0; truth = "ext" if ext >= 0.5 else ("slow-int" if L > band_hi else "fast")
                d = off.get(s, {}).get("row"); dict_only = {6: "int (E under E_thr)", 7: "ext (E >= E_thr, no signal)"}.get(d, str(d)); o = on[s]
                rows.append(dict(scenario=label, platform="r650", second=s, dict_only_verdict=dict_only, signal_verdict=NAMES.get(o["row"], str(o["row"])), holder=o["holder"], truth=truth, truth_ext=round(ext, 4),
                                 truth_source=("the generator's per-second client p50 against the twins' client p50 (the in-handler timer cannot see the queue in front of the handler)" if truth_src == "client" else "the server-side p50 per second against the twins' p50 (Phase G)"),
                                 lat_ms=round(L, 5), twin_p50_ms=round(p50_twin, 5), E=o["E"], thr=o["thr"], I_g=o["Ig"], I_g_raw=o["Ig_raw"], phi=o["phi"], phi_irq=o["phi_irq"], rho_eff=o["rho_eff"], amount_ms=o["amount"], amount_src=o["amount_src"], sirq=o["sirq"], mbm_sub=o["sub"], windows=o["windows"],
                                 source_cell=f"{os.path.basename(cdir)} arm {arm_name} rep {a['rep']}", twin_cells=";".join(os.path.basename(cell_dir(root, t)) for t, _ in twins), dictionary=os.path.basename(dct), sirq_base=round(base, 4), rule_version="s11c-final"))
            print(f"  {os.path.basename(cdir)} {arm_name} r{a['rep']}: {n} windows, {len(on)} seconds; rows {dict(Counter(v['row'] for v in on.values()))}; mask {dict(Counter(v['row'] for v in off.values()))}")
keys = list(rows[0].keys())
with open(os.path.join(OUT, "final_verdicts_by_second.csv"), "w", newline="") as f:
    w = csv.DictWriter(f, fieldnames=keys); w.writeheader(); w.writerows(rows)
print("rows", len(rows))
