#!/usr/bin/env python3
"""RESULTS-K 4.5.1: where the service dictionary's D[rho] came from, on every scored second of Phase G's fourteen matrix
rows (RESULTS-G 2.5: i-vi, the three v pairs, D.4 x 3, D.5) and of K4.5.

Per dictionary sweep, with the fit of `fit_service_dict` as the runs were scored (settle 5 s, the run's costs, 24 rho bins
over [0, 1.05], the overloaded steps dropped):
  - every rho bin's settled windows, whether a step holds it (at least a quarter of one step's settled windows fall in it)
    or it only collects windows between steps (bursts of the step below, dips of the step above, transitions), and its
    pooled p50 against the line between the two adjacent steps' own p50s at the bin's rho ("deviation");
  - per step its mean rho and rho band (p5-p95 of its windows): the anchors of the 4.5.1 fit.
Per scored second (the seconds of the run's verdict.csv; rho = the b4 output's util, the value D was read at): on a step
(inside a step's band), between two steps, or beyond the profile (outside the first and last steps); and the window count
of the kept bin that carried most of the interpolation weight of D there ("D came from").

usage: k45_dictcheck.py [--min-windows N] [--k45-runs RUNS] [--out results/k45]
Writes k45_dictcheck_bins.csv (one row per dictionary and bin) and k45_dictcheck_rows.csv (one row per scored run).
"""
import argparse, csv, json, os, sys
from collections import Counter
import numpy as np
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
BIG = os.path.dirname(HERE)

# label, scored run, verdict dir, b4 dir, service (GORDION_SVC), costs file or None (the run's configured cpu_ms)
ROWS = [
    ("i mix shift + 1 aggressor", "results/20261111-035721-g2c-i-ramp-mid7-stress1", "g2", "b4g2", None, None),
    ("ii served step past the profile", "results/20261111-014041-g2b-overhead-probe-on", "g2t", "b4g2t", None, None),
    ("iii recommendation + 1 aggressor/core", "results/hr/20261111-041108-g2c-iii-rec-stress2", "g2", "b4g2", "recommendation", "results/hr/costs-recommendation-G170-avg.json"),
    ("iv uniform + stream", "results/20261111-033427-g2c-iv-stream-stress-v2", "g2", "b4g2", None, None),
    ("iv-b memwalk30 128 MB + stream", "results/20261111-045910-g2d-ivb-memwalk30-stream", "g2", "b4g2", None, None),
    ("iv-b' memwalk30 24 MB + stream", "results/20261111-055100-g2d-ivb24-memwalk30-stream", "g2", "b4g2", None, None),
    ("v pair 1", "results/sn/20261111-053546-g2e-v-ps-compose-heavy-k1-stress1", "g2", "b4g2", "post-storage-service", "results/sn/costs-post-storage-G170-avg.json"),
    ("v pair 2", "results/sn/20261111-062307-g2e-v2-ps-compose-heavy-k1-stress1", "g2", "b4g2", "post-storage-service", "results/sn/costs-post-storage-G170-v2-avg.json"),
    ("v pair 4", "results/sn/20261111-070941-g2e-v4-ps-compose-heavy-k1-stress1", "g2", "b4g2", "post-storage-service", "results/sn/costs-post-storage-G170-v4-avg.json"),
    ("vi mutex10", "results/20261111-050714-g2d-vi-mutex10", "g2", "b4g2", None, None),
    ("D.4 memwalk30 24 MB + cache, A1", "results/20261111-072625-g2f-d4-memwalk30-24mb-cache", "g2", "b4g2", None, None),
    ("D.4 order control", "results/20261111-085628-g2f2-d4-memwalk30-24mb-cache-first", "g2", "b4g2", None, None),
    ("D.4 memwalk30 128 MB + cache, A4", "results/20261111-083452-g2f-d4-memwalk30-128mb-cache", "g2", "b4g2", None, None),
    ("D.5 VioMutex 40 %", "results/20261111-074358-g2f-d5-mutex40", "g2", "b4g2", None, None),
]


def fnum(x):
    try: return float(x)
    except Exception: return np.nan


def dict_bins(run_dir, svc, costs):
    """the fit's bins with their window counts, step shares, p50 deviation from the step line, and the step anchors"""
    if svc: os.environ["GORDION_SVC"] = svc
    else: os.environ.pop("GORDION_SVC", None)
    from analyze import load_run
    from dictionary import fit_service_dict, settled_windows, rho_series, costs_of_run, cpuset_size
    R = load_run(run_dir, svc=svc)
    SD = fit_service_dict(R, 5.0, costs)
    k = cpuset_size(R["meta"].get("cpuset", "0-7")); c_ms, _ = costs_of_run(R, costs)
    W, step_of, _ = settled_windows(R, 5.0)
    keep = [i for i, s in enumerate(step_of) if s not in SD.overloaded]
    Wk = [W[i] for i in keep]; stk = np.array([step_of[i] for i in keep])
    rho = rho_series(Wk, c_ms, 1.0) / k
    edges = np.linspace(0, 1.05, 25); mids = (edges[:-1] + edges[1:]) / 2
    b = np.clip(np.digitize(rho, edges) - 1, 0, 23)
    per_step = Counter(stk.tolist()); rate = {s["step"]: s["rate"] for s in R["steps"]}
    A = SD.anchors; ar = np.array([a["rho"] for a in A]); ap = np.array([a["p50"] for a in A])
    kp = {round(float(m), 4): float(p) for m, p in zip(SD.mids, SD.p50)}
    bins = []
    for i in range(24):
        m = b == i
        if not m.any(): continue
        share = {int(s): int(n) for s, n in Counter(stk[m].tolist()).items()}
        held = max(n / per_step[s] for s, n in share.items())
        mid = round(float(mids[i]), 4); p = kp.get(mid)
        line = float(np.interp(mid, ar, ap)) if len(ar) >= 2 and ar[0] <= mid <= ar[-1] else np.nan
        bins.append(dict(mid=mid, windows=int(m.sum()), steps=share, held_share=round(held, 3), held=held >= 0.25, kept=p is not None,
                         p50_ms=p, step_line_ms=line, deviation=(p / line - 1) if p is not None and np.isfinite(line) else np.nan))
    steps = [dict(step=a["step"], rate=rate.get(a["step"]), rho=a["rho"], lo=a["lo"], hi=a["hi"], windows=a["windows"], p50=a["p50"]) for a in A]
    return SD, bins, steps, sorted(rate.get(s) for s in per_step)


def scored(row, SD, bins, steps, N):
    label, run, g2d, b4d = row[:4]
    V = list(csv.DictReader(open(os.path.join(BIG, run, g2d, "verdict.csv"))))
    util = {int(r["t"]): fnum(r["util"]) for r in csv.DictReader(open(os.path.join(BIG, run, b4d, "b4_windows.csv")))}
    nw = {b["mid"]: b["windows"] for b in bins}
    mids = np.round(np.asarray(SD.mids), 4); cls = Counter(); came = Counter(); sparse_main = sparse_any = 0; between_rhos = []
    lo0, hi1 = steps[0]["lo"], steps[-1]["hi"]
    for r in V:
        u = util.get(int(r["t"]))
        if u is None or not np.isfinite(u): continue
        if any(a["lo"] <= u <= a["hi"] for a in steps): c = "on a step"
        elif u < lo0 or u > hi1: c = "beyond the profile"
        else: c = "between steps"; between_rhos.append(u)
        cls[c] += 1
        j = int(np.searchsorted(mids, u))
        if j <= 0 or j >= len(mids): knots = [(mids[min(max(j, 0), len(mids) - 1)], 1.0)]
        else:
            w1 = (u - mids[j - 1]) / (mids[j] - mids[j - 1]); knots = [(mids[j - 1], 1 - w1), (mids[j], w1)]
        main = max(knots, key=lambda x: x[1])[0]; wmain = nw.get(float(main), 0)
        came[(c, float(main), wmain)] += 1
        sparse_main += int(wmain < N); sparse_any += int(min(nw.get(float(m), 0) for m, w in knots if w > 0) < N)
    rates = sorted({json.loads(l)["rate"] for l in open(os.path.join(BIG, run, "wlgen.jsonl")) if '"event":"step"' in l.replace(" ", "") and json.loads(l).get("step", -1) >= 0})
    return dict(seconds=len(V), on_step=cls["on a step"], between_steps=cls["between steps"], beyond_profile=cls["beyond the profile"],
                main_bin_below_N=sparse_main, any_bin_below_N=sparse_any, between_rho=(f"{min(between_rhos):.3f}-{max(between_rhos):.3f}" if between_rhos else ""),
                d_came_from="; ".join(f"{c}: bin {m:.3f} ({w} windows) x{n}" for (c, m, w), n in sorted(came.items())), scored_rates=" ".join(str(x) for x in rates))


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--min-windows", type=int, default=50); ap.add_argument("--k45-runs", default=None)
    ap.add_argument("--out", default=os.path.join(BIG, "results", "k45")); a = ap.parse_args()
    rows = list(ROWS)
    if a.k45_runs:   # K4.5, scored against its own sweep D
        seg = {d.split("-k45-")[-1]: os.path.join(a.k45_runs, d) for d in os.listdir(a.k45_runs) if "-k45-" in d}
        for n, t in (("res1", "S0"), ("res2", "S0"), ("res3", "S0"), ("res1x7", "T7"), ("free7", "T7"), ("stall7", "T7")):
            if n in seg: rows.append((f"K4.5 {n}", seg[n], f"g2-vs-{t}", "b4g2", "recommendation", os.path.join(BIG, "results", "k45", "costs-recommendation-K45-avg.json"), seg["D"]))
    cache = {}; out_b, out_r = [], []
    for row in rows:
        label, run, g2d, b4d, svc, costs = row[:6]
        sd = json.load(open(os.path.join(BIG, run, b4d, "service_dict.json")))
        if len(row) > 6: dd = row[6]
        else:
            src = sd["source"]; dd = next(os.path.join(BIG, x, src) for x in ("results", "results/hr", "results/sn") if os.path.isdir(os.path.join(BIG, x, src)))
        cf = os.path.join(BIG, costs) if costs and not os.path.isabs(costs) else costs
        if dd not in cache:
            cache[dd] = dict_bins(dd, svc, cf)
            SD, bins, steps, rates = cache[dd]
            for b in bins: out_b.append(dict(dictionary=os.path.basename(dd), sweep_rates=" ".join(str(r) for r in rates), **{k: (json.dumps(v) if isinstance(v, dict) else v) for k, v in b.items()}))
            print(f"== {os.path.basename(dd)}: steps (rate, rho, band, windows) {[(s['rate'], round(s['rho'], 3), (round(s['lo'], 3), round(s['hi'], 3)), s['windows']) for s in steps]}")
            print("   bins (mid: windows, held / between, p50 deviation from the step line): " + ", ".join(
                f"{b['mid']:.3f}: {b['windows']} {'H' if b['held'] else 'B'}{'' if b['kept'] else ' [not in D]'}{'' if not np.isfinite(b['deviation']) else f' {100 * b['deviation']:+.1f}%'}" for b in bins))
        SD, bins, steps, rates = cache[dd]
        r = dict(row=label, scored_run=os.path.basename(run), dictionary=os.path.basename(dd), sweep_rates=" ".join(str(x) for x in rates), **scored(row, SD, bins, steps, a.min_windows))
        out_r.append(r); print("  ", {k: v for k, v in r.items() if k not in ("d_came_from", "scored_run", "dictionary")})
        print("      D came from:", r["d_came_from"][:600])
    os.makedirs(a.out, exist_ok=True)
    for fn, rr in (("k45_dictcheck_bins.csv", out_b), ("k45_dictcheck_rows.csv", out_r)):
        with open(os.path.join(a.out, fn), "w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=list(rr[0].keys())); w.writeheader(); w.writerows(rr)
    # deviation against window count, all dictionaries, kept bins between two steps
    dev = [(b["windows"], b["deviation"], b["held"]) for b in out_b if b["kept"] and np.isfinite(b["deviation"])]
    for lo, hi in ((0, 25), (25, 50), (50, 100), (100, 200), (200, 10000)):
        v = [abs(d) for w, d, h in dev if lo <= w < hi]
        if v: print(f"bins with {lo}-{hi - 1} windows: {len(v)}, |p50 - step line| median {100 * np.median(v):.1f} %, max {100 * max(v):.1f} %")
    print("wrote", os.path.join(a.out, "k45_dictcheck_bins.csv"), "and k45_dictcheck_rows.csv")


if __name__ == "__main__":
    main()
