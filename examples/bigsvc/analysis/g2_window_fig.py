#!/usr/bin/env python3
"""Phase G section 2.9 figure: one scenario's verdict rows over time as three strips — the twin truth (per second),
the per-second verdict of Fig. G2-5 (E from the 5 s-smoothed Lat) and the per-window verdict (raw, 100 ms) — with E
per second and per window underneath. Same row colours as Fig. G2-5.

usage: g2_window_fig.py --out FILE.png --window RUN/g2/window_verdict.csv --label "scenario i ..." [--title ...]
"""
import argparse, csv
import numpy as np
import matplotlib; matplotlib.use("Agg"); import matplotlib.pyplot as plt
from matplotlib.patches import Patch

ROWS = {1: ("1 run-queue contention (E ≫ 0, φ ≫ 0)", "#D85A30"), 2: ("2 own overload", "#2A6F97"), 3: ("3 service-time inflation (I ≫ 1)", "#7F77DD"),
        4: ("4 lock wait", "#1D9E75"), 5: ("5 GC", "#B8860B"), 6: ("6 nothing to explain", "#D5E4EE"), 7: ("7 no verdict", "#8F8E87")}
TRUTH = {"ext": ("truth: ext", "#D85A30"), "slow-int": ("truth: slow, int", "#2A6F97"), "fast": ("truth: fast", "#D5E4EE")}


def strip(ax, xs, ws, colors, label):
    for x, w, c in zip(xs, ws, colors):
        ax.add_patch(plt.Rectangle((x, 0), w, 1, facecolor=c, edgecolor="none"))
    ax.set_ylim(0, 1); ax.set_yticks([]); ax.set_ylabel(label, rotation=0, ha="right", va="center", fontsize=8.5)
    for s in ("top", "right", "left"): ax.spines[s].set_visible(False)


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--out", required=True); ap.add_argument("--window", required=True); ap.add_argument("--label", default="scenario"); ap.add_argument("--title", default="")
    a = ap.parse_args(); rows = list(csv.DictReader(open(a.window)))
    t0 = min(float(r["t_ms"]) for r in rows) / 1000.0
    tw = np.array([float(r["t_ms"]) / 1000.0 - t0 for r in rows]); dw = np.array([float(r["dt_ms"]) / 1000.0 for r in rows])
    secs = sorted(set(int(r["sec"]) for r in rows)); by_sec = {int(r["sec"]): r for r in rows}
    ts = np.array([s - t0 for s in secs]); ds = np.ones(len(secs))
    fig, axs = plt.subplots(4, 1, figsize=(13, 5.6), sharex=True, gridspec_kw={"height_ratios": [0.6, 0.6, 0.6, 2.2], "hspace": 0.12})
    strip(axs[0], ts, ds, [TRUTH[by_sec[s]["truth"]][1] for s in secs], "truth (per second)")
    strip(axs[1], ts, ds, [ROWS[int(by_sec[s]["row_sec"])][1] for s in secs], "verdict per second\n(Fig. G2-5, 5 s-smoothed E)")
    strip(axs[2], tw, dw, [ROWS[int(r["row"])][1] for r in rows], "verdict per window\n(100 ms, raw E)")
    ax = axs[3]
    ax.plot(tw, [float(r["E"]) for r in rows], color="#8F8E87", lw=0.6, label="E per 100 ms window (raw)")
    ax.plot(ts, [float(by_sec[s]["E_sec"]) for s in secs], color="#1B2430", lw=1.3, label="E per second (5 s-smoothed Lat)")
    ax.plot(ts, [float(by_sec[s]["truth_ext"]) for s in secs], color="#1D9E75", lw=1.2, ls="--", label="truth share (twin)")
    ax.axhline(0.5, color="#8F8E87", lw=0.6, ls=":"); ax.set_ylim(0, 1); ax.set_ylabel("share of Lat"); ax.set_xlabel("time since the first scored second (s)")
    ax.legend(fontsize=8, loc="upper left", ncol=3, frameon=False)
    for s in ("top", "right"): ax.spines[s].set_visible(False)
    ax.set_xlim(0, (tw + dw).max())
    handles = [Patch(facecolor=c, label=l) for k, (l, c) in ROWS.items() if k != 5] + [Patch(facecolor=c, label=l, hatch="//") for l, c in TRUTH.values()]
    fig.legend(handles=handles, loc="lower center", ncol=5, fontsize=8, frameon=False, bbox_to_anchor=(0.5, -0.03))
    fig.suptitle(a.title or (f"Fig. G2-9 — {a.label}: verdict rows per second and per 100 ms window against the twin truth\n"
                             "(server-side Lat_e p50, processing stream; E from D[ρ; mix], φ from the probe's cgroup occupancy, I_g from cpu.stat, Q = Go mutex wait)"),
                 x=0.01, ha="left", fontsize=9.5)
    fig.tight_layout(rect=(0, 0.05, 1, 0.95)); fig.savefig(a.out, dpi=140, bbox_inches="tight"); print("wrote", a.out)


if __name__ == "__main__":
    main()
