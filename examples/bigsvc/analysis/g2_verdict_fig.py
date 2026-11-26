#!/usr/bin/env python3
"""Phase G section 2.5 figure (F7 layout): per scenario, the share of scored seconds per verdict row, per load step or
per rho bin, with the twin truth beside it; plus one panel per scenario of the signals over time (R_foreign, R_self,
I_g, E) for the mix-shift and stream scenarios.

usage: g2_verdict_fig.py --out FILE.png --row "label|verdict.csv|by=step|bins=..." [--row ...]
"""
import argparse, csv, os
import numpy as np
import matplotlib; matplotlib.use("Agg"); import matplotlib.pyplot as plt
from matplotlib.patches import Patch

ROWS = {1: ("1 run-queue contention (E ≫ 0, φ ≫ 0)", "#D85A30"), 2: ("2 own overload", "#2A6F97"), 3: ("3 service-time inflation (I ≫ 1)", "#7F77DD"),
        4: ("4 lock wait", "#1D9E75"), 5: ("5 GC", "#B8860B"), 6: ("6 nothing to explain", "#D5E4EE"), 7: ("7 no verdict", "#8F8E87")}
TRUTH = {"ext": ("truth: ext", "#D85A30"), "slow-int": ("truth: slow, int", "#2A6F97"), "fast": ("truth: fast", "#D5E4EE")}


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--out", required=True); ap.add_argument("--row", action="append", required=True); ap.add_argument("--title", default="")
    a = ap.parse_args()
    specs = [r.split("|") for r in a.row]
    fig, axs = plt.subplots(len(specs), 2, figsize=(13, 2.6 * len(specs) + 1.2), gridspec_kw={"width_ratios": [1, 1], "hspace": 0.55, "wspace": 0.25}, squeeze=False)
    for i, sp in enumerate(specs):
        label, path = sp[0], sp[1]; opts = dict(x.split("=", 1) for x in sp[2:] if "=" in x)
        rows = list(csv.DictReader(open(path)))
        by = opts.get("by", "step")
        if by == "step":
            keys = sorted(set(r["step"] for r in rows), key=lambda x: int(float(x))); groups = {k: [r for r in rows if r["step"] == k] for k in keys}
            xl = [f"{k}" for k in keys]; xlabel = "load step"
        else:
            edges = np.arange(0, 1.4, 0.1); groups = {}
            for lo in edges:
                g = [r for r in rows if lo <= float(r["rho"]) < lo + 0.1]
                if len(g) >= 3: groups[f"{lo:.1f}"] = g
            keys = list(groups); xl = keys; xlabel = "offered utilisation ρ (bin)"
        ax = axs[i, 0]; x = np.arange(len(keys))
        for j, k in enumerate(keys):
            g = groups[k]; n = len(g); bottom = 0
            for rid in (1, 3, 4, 5, 2, 7, 6):
                v = sum(1 for r in g if int(r["row"]) == rid) / n
                if v > 0: ax.bar(j - 0.2, v, 0.36, bottom=bottom, color=ROWS[rid][1], edgecolor="white", linewidth=0.5); bottom += v
            bottom = 0
            for t in ("ext", "slow-int", "fast"):
                v = sum(1 for r in g if r["truth"] == t) / n
                if v > 0: ax.bar(j + 0.2, v, 0.36, bottom=bottom, color=TRUTH[t][1], edgecolor="white", linewidth=0.5, hatch="//"); bottom += v
        ax.set_xticks(x); ax.set_xticklabels(xl, fontsize=8); ax.set_ylim(0, 1); ax.set_yticks([0, 0.5, 1]); ax.set_xlabel(xlabel, fontsize=9)
        ax.set_title(f"{label}", loc="left", fontsize=9.5)
        ax = axs[i, 1]; ts = np.array([float(r["t"]) for r in rows]); ts = ts - ts.min()
        ax.plot(ts, [float(r["E"]) for r in rows], color="#1B2430", lw=1.0, label="E = residual share")
        ax.plot(ts, [float(r["truth_ext"]) for r in rows], color="#1D9E75", lw=1.0, ls="--", label="truth (twin)")
        ax2 = ax.twinx()
        ax2.plot(ts, [float(r["R_foreign_ms"]) for r in rows], color="#D85A30", lw=1.0, label="R_foreign (ms/s)")
        ax2.plot(ts, [float(r["R_self_ms"]) for r in rows], color="#2A6F97", lw=0.8, alpha=0.7, label="R_self (ms/s)")
        ax.set_ylim(0, 1); ax.set_ylabel("share"); ax2.set_ylabel("run-queue delay (ms per s)"); ax.set_xlabel("time (s)", fontsize=9)
        h1, l1 = ax.get_legend_handles_labels(); h2, l2 = ax2.get_legend_handles_labels(); ax.legend(h1 + h2, l1 + l2, fontsize=7, loc="upper left", ncol=2)
        ax.set_title("", loc="left")
    handles = [Patch(facecolor=c, label=l) for l, c in ROWS.values()] + [Patch(facecolor=c, label=l, hatch="//") for l, c in TRUTH.values()]
    fig.legend(handles=handles, loc="lower center", ncol=5, fontsize=8, frameon=False, bbox_to_anchor=(0.5, -0.01))
    fig.suptitle(a.title or "Fig. G2-5 — verdict rows per step against the twin truth (server-side Lat_e; E from D[ρ; mix], R from the scheduler probe, I_g from cpu.stat)", x=0.01, ha="left", fontsize=10)
    fig.tight_layout(rect=(0, 0.04, 1, 0.96)); fig.savefig(a.out, dpi=130, bbox_inches="tight"); print("wrote", a.out)


if __name__ == "__main__":
    main()
