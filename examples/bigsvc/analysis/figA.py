#!/usr/bin/env python3
"""Fig. A (Phase E): mixes diverge when indexed by lambda, collapse when indexed by rho.

One row per testbed, two panels: left y vs lambda (req/s), right y vs rho = lambda cbar / (f/f_nom) / k; y = pooled
p50 (solid) and p90 (dashed) of the within-service latency, one colour per mix; "above floor" rows subtract each
mix's lowest-step p50 (the mixes' own floors differ, the wait does not); --y wk rows show the above-floor W divided
by the mix's K = c2bar / (2 cbar) (the dictionary's h(rho): one curve for every mix if D[rho; mix] holds). Log y
where the range exceeds 10x; dashed line at rho = 0.65 (the gated knee).

Each run must carry svc_curve.csv (analyze.py: step, rate_target, lambda, rho, avgS_ms, p50_ms,
p90_ms, n) produced with the costs / clock settings of the row, and meta.json (cpuset -> k). --y wk also needs the
run's recorder stream and c_e (the row's "costs=" item, else the run's configured cpu_ms).

Usage: figA.py --out figA.png --row "title|above_floor|run1=label1,run2=label2[,costs=costs.json]" [--row ...]
                [--y lat|wk]
"""
import argparse, csv, json, os, sys, textwrap
import numpy as np
import matplotlib; matplotlib.use("Agg"); import matplotlib.pyplot as plt
sys.path.insert(0, os.path.dirname(__file__))
from dictionary import cpuset_size, NOTATION, EP_A, costs_of_run, mix_stats, settled_windows

COLORS = ["#1f77b4", "#d62728", "#2ca02c", "#9467bd", "#ff7f0e"]
MARKERS = ["o", "s", "^", "D", "v"]


def load(run, costs=None, need_K=False):
    rows = list(csv.DictReader(open(os.path.join(run, "svc_curve.csv"))))
    meta = json.load(open(os.path.join(run, "meta.json")))
    k = cpuset_size(meta.get("cpuset", "0-7"))
    r = {c: np.array([float(x[c]) for x in rows]) for c in ("lambda", "rho", "avgS_ms", "p50_ms", "p90_ms", "n", "rate_target")}
    r["served"] = r["lambda"] / np.maximum(r["rate_target"], 1e-9)
    r["k"] = k
    r["u"] = r["rho"] / k
    r["K"] = np.nan
    if need_K:
        from analyze import load_run
        R = load_run(run)
        c_ms, _ = costs_of_run(R, costs)
        W, _, _ = settled_windows(R, 5.0)
        arr = {}
        for w in W:
            for e, v in w["ep"].items():
                arr[e] = arr.get(e, 0.0) + v[EP_A]
        r["K"] = mix_stats(arr, c_ms, {e: 0.0 for e in arr})["K"]
    return r


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--out", required=True); ap.add_argument("--row", action="append", required=True)
    ap.add_argument("--title", default="Fig. A -- same lambda, different latency (left); same rho, same latency (right)")
    ap.add_argument("--max-u", type=float, default=1.05, help="drop steps with rho/k above this (past the knee: arrival counts inflate under overload)")
    ap.add_argument("--stream", default="server-side Lat_e (tap -> return minus downstream-blocked)", help="latency stream named in the title")
    ap.add_argument("--dict-form", default="D[rho; mix] = Fbar + h(rho) K, Fbar = mixture median of the floor distributions (service dictionary, mix-corrected)", help="dictionary form named in the title")
    ap.add_argument("--y", choices=("lat", "wk"), default="lat", help="lat: latency (above floor if the row says so); wk: above-floor W divided by the mix's K (p50 only)")
    ap.add_argument("--knee", type=float, default=0.65, help="rho of the dashed knee line")
    ap.add_argument("--served-min", type=float, default=0.95, help="drop steps whose served/target is below this (overloaded)")
    a = ap.parse_args()
    rows = []
    for spec in a.row:
        title, above, runs = spec.split("|", 2)
        items = [(p.split("=", 1)[0], p.split("=", 1)[1]) for p in runs.split(",")]
        costs = dict(items).get("costs")
        runs = [(r_, l_) for r_, l_ in items if r_ != "costs"]
        rows.append((title, above.strip().lower() in ("1", "true", "yes", "above_floor"), runs, costs))
    fig, axes = plt.subplots(len(rows), 2, figsize=(11, 3.6 * len(rows)), squeeze=False)
    for ri, (title, above, runs, costs) in enumerate(rows):
        axl, axr = axes[ri]
        ymin, ymax = np.inf, -np.inf
        wk = a.y == "wk"
        for mi, (run, label) in enumerate(runs):
            r = load(run, costs, need_K=wk)
            # past-the-knee steps are not profiled data: rho above --max-u, or served/target below --served-min (overload)
            keep = (r["u"] <= a.max_u) & (r["served"] >= a.served_min)
            if keep.sum() < 2:  # a run whose generator never reached its target (served/target low throughout): keep the rho rule only
                keep = r["u"] <= a.max_u
            # p50 vs p90: the floor is the lowest step's p50 (and p90 for the p90 curve)
            fl50 = r["p50_ms"][0] if (above or wk) else 0.0
            fl90 = r["p90_ms"][0] if (above or wk) else 0.0
            y50 = r["p50_ms"] - fl50; y90 = r["p90_ms"] - fl90
            if wk:
                y50 = y50 / r["K"]; y90 = y90 / r["K"]
            if above or wk:  # the floor step itself (and noise below it) is shown at the resolution line
                res = 0.01 if wk else 0.05
                y50 = np.maximum(y50, res); y90 = np.maximum(y90, res)
            c, m = COLORS[mi % len(COLORS)], MARKERS[mi % len(MARKERS)]
            lab = f"{label} (cbar {np.nanmean(r['avgS_ms']):.2f} ms" + (f", K {r['K']:.2f} ms)" if wk else ")")
            for ax, x in ((axl, r["lambda"]), (axr, r["u"])):
                ax.plot(x[keep], y50[keep], "-", marker=m, ms=4, color=c, lw=1.4, label=lab if ax is axl else None)
                if not wk:
                    ax.plot(x[keep], y90[keep], "--", marker=m, ms=3, color=c, lw=0.9, alpha=0.8)
            ymin = min(ymin, np.nanmin(y50[y50 > 0]) if (y50 > 0).any() else ymin); ymax = max(ymax, np.nanmax(y90 if not wk else y50))
        if wk:
            ylab = "W / K   (W = pooled p50 above the lowest step; K = c2bar / 2 cbar)"
        else:
            ylab = ("latency above floor" if above else "within-service latency") + ", p50 (solid) / p90 (dashed), ms"
        for ax in (axl, axr):
            ax.grid(alpha=.3)
            if ymax / max(ymin, 1e-3) > 10:
                ax.set_yscale("log")
        axl.set_title(f"{title}, indexed by lambda", fontsize=10); axr.set_title(f"{title}, indexed by rho", fontsize=10)
        axl.set_xlabel("lambda, arrival rate (req/s)"); axr.set_xlabel("rho = lambda cbar / (f/f_nom) / k, offered utilisation")
        axl.set_ylabel(ylab, fontsize=8)
        axr.axvline(a.knee, color="0.5", ls="--", lw=0.8); axr.text(a.knee, axr.get_ylim()[1], " knee ->", fontsize=7, color="0.4", va="top")
        axl.legend(fontsize=7)
    fig.suptitle(textwrap.fill(a.title, 140) + chr(10) + textwrap.fill("latency stream: " + a.stream + ";  dictionary: " + a.dict_form, 140), fontsize=10)
    fig.tight_layout(rect=(0, 0.04, 1, 0.96))
    fig.text(0.01, 0.005, "Notation: " + NOTATION + ";  k = cores of the service;  cbar = sum %_e c_e, c2bar = sum %_e c_e^2 of the mix", fontsize=6, color="0.3")
    fig.savefig(a.out, dpi=140)
    print("wrote", a.out)


if __name__ == "__main__":
    main()
