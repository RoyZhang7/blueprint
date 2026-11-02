#!/usr/bin/env python3
"""Fig. A (Phase C section 2): mixes diverge when indexed by lambda, collapse when indexed by rho/k.

One row per testbed, two panels: left y vs lambda (req/s), right y vs rho/k (offered utilisation per
core); y = service-level p50 (solid) and p90 (dashed) of the within-service latency, one colour per
mix; "above floor" rows subtract each mix's lowest-step p50 (the mixes' own floors differ, the wait
does not). Log y where the range exceeds 10x; dashed line at rho/k = 0.6.

Each run must carry svc_curve.csv (analyze.py: step, rate_target, lambda, rho, avgS_ms, p50_ms,
p90_ms, n) produced with the costs / clock settings of the row, and meta.json (cpuset -> k).

Usage: figA.py --out figA.png --row "title|above_floor|run1=label1,run2=label2" [--row ...]
"""
import argparse, csv, json, os, sys
import numpy as np
import matplotlib; matplotlib.use("Agg"); import matplotlib.pyplot as plt
sys.path.insert(0, os.path.dirname(__file__))
from dictionary import cpuset_size, NOTATION

COLORS = ["#1f77b4", "#d62728", "#2ca02c", "#9467bd", "#ff7f0e"]
MARKERS = ["o", "s", "^", "D", "v"]


def load(run):
    rows = list(csv.DictReader(open(os.path.join(run, "svc_curve.csv"))))
    meta = json.load(open(os.path.join(run, "meta.json")))
    k = cpuset_size(meta.get("cpuset", "0-7"))
    r = {c: np.array([float(x[c]) for x in rows]) for c in ("lambda", "rho", "avgS_ms", "p50_ms", "p90_ms", "n")}
    r["k"] = k
    r["u"] = r["rho"] / k
    return r


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--out", required=True); ap.add_argument("--row", action="append", required=True)
    ap.add_argument("--title", default="Fig. A -- same lambda, different latency (left); same rho, same latency (right)")
    ap.add_argument("--max-u", type=float, default=1.05, help="drop steps with rho/k above this (past the knee: arrival counts inflate under overload)")
    a = ap.parse_args()
    rows = []
    for spec in a.row:
        title, above, runs = spec.split("|", 2)
        runs = [(p.split("=", 1)[0], p.split("=", 1)[1]) for p in runs.split(",")]
        rows.append((title, above.strip().lower() in ("1", "true", "yes", "above_floor"), runs))
    fig, axes = plt.subplots(len(rows), 2, figsize=(11, 3.6 * len(rows)), squeeze=False)
    for ri, (title, above, runs) in enumerate(rows):
        axl, axr = axes[ri]
        ymin, ymax = np.inf, -np.inf
        for mi, (run, label) in enumerate(runs):
            r = load(run)
            keep = r["u"] <= a.max_u  # past-the-knee steps (arrival counts inflate under overload) are not profiled data
            # p50 vs p90: the floor is the lowest step's p50 (and p90 for the p90 curve)
            fl50 = r["p50_ms"][0] if above else 0.0
            fl90 = r["p90_ms"][0] if above else 0.0
            y50 = r["p50_ms"] - fl50; y90 = r["p90_ms"] - fl90
            if above:  # the floor step itself (and noise below it) is shown at the 0.05 ms resolution line
                y50 = np.maximum(y50, 0.05); y90 = np.maximum(y90, 0.05)
            c, m = COLORS[mi % len(COLORS)], MARKERS[mi % len(MARKERS)]
            lab = f"{label} (avg(S) {np.nanmean(r['avgS_ms']):.2f} ms)"
            for ax, x in ((axl, r["lambda"]), (axr, r["u"])):
                ax.plot(x[keep], y50[keep], "-", marker=m, ms=4, color=c, lw=1.4, label=lab if ax is axl else None)
                ax.plot(x[keep], y90[keep], "--", marker=m, ms=3, color=c, lw=0.9, alpha=0.8)
            ymin = min(ymin, np.nanmin(y50[y50 > 0]) if (y50 > 0).any() else ymin); ymax = max(ymax, np.nanmax(y90))
        ylab = ("latency above floor" if above else "within-service latency") + ", p50 (solid) / p90 (dashed), ms"
        for ax in (axl, axr):
            ax.grid(alpha=.3)
            if ymax / max(ymin, 1e-3) > 10:
                ax.set_yscale("log")
        axl.set_title(f"{title}, indexed by lambda", fontsize=10); axr.set_title(f"{title}, indexed by rho", fontsize=10)
        axl.set_xlabel("lambda, arrival rate (req/s)"); axr.set_xlabel("rho/k, offered utilisation per core")
        axl.set_ylabel(ylab, fontsize=8)
        axr.axvline(0.6, color="0.5", ls="--", lw=0.8); axr.text(0.6, axr.get_ylim()[1], " knee region ->", fontsize=7, color="0.4", va="top")
        axl.legend(fontsize=7)
    fig.suptitle(a.title, fontsize=11)
    fig.tight_layout(rect=(0, 0.04, 1, 0.97))
    fig.text(0.01, 0.005, "Notation: " + NOTATION + ";  k = cores of the service;  avg(S) = sum %_e c_e of the mix", fontsize=6, color="0.3")
    fig.savefig(a.out, dpi=140)
    print("wrote", a.out)


if __name__ == "__main__":
    main()
