#!/usr/bin/env python3
"""Fig. B (Phase C section 3): a mix ramp at fixed lambda, no aggressor -- what each scorer reports.

Five stacked traces vs time from b4_compare.py's b4_windows.csv (one row per second): share of the
costly mix (ramp fraction), lambda, rho/k, %ext of the old scorer (D indexed by lambda) and %ext of
the new scorer (D_e indexed by rho). Optional second column for the same ramp with an aggressor
(--stress CSV), whose %ext traces are drawn against the ground truth. Table (stdout): fraction of
windows each scorer labels extrinsic before / during / after the ramp, and the 2x2 cell accuracy
against the truth when present.

Usage: figB.py --csv RUN/b4_windows.csv [--stress RUN2/b4_windows.csv] --out figB.png [--title ...]
"""
import argparse, csv, os, sys
import numpy as np
import matplotlib; matplotlib.use("Agg"); import matplotlib.pyplot as plt
sys.path.insert(0, os.path.dirname(__file__))
from dictionary import NOTATION


def load(p):
    rows = list(csv.DictReader(open(p)))
    g = lambda c: np.array([float(r[c]) if r[c] not in ("", "n/a") else np.nan for r in rows])
    d = {c: g(c) for c in ("t", "lambda", "rho", "util", "ext_old", "ext_new", "y", "ramp", "usable")}
    d["truth"] = g("truth_ext") if "truth_ext" in rows[0] else None
    d["cell_old"] = [r["cell_old"] for r in rows]; d["cell_new"] = [r["cell_new"] for r in rows]
    d["t"] = d["t"] - d["t"][0]
    return d


def phases(d):
    r = d["ramp"]
    return {"before": r <= 0, "during": (r > 0) & (r < 1), "after": r >= 1}


def table(name, d, thr=0.5):
    print(f"{name}: {len(d['t'])} windows")
    print("   phase    n   extrinsic windows old   new    (%ext >= 0.5)")
    for ph, m in phases(d).items():
        if m.any():
            print(f"   {ph:7s} {m.sum():4d}   {np.mean(d['ext_old'][m] >= thr) * 100:6.1f} %   {np.mean(d['ext_new'][m] >= thr) * 100:6.1f} %")
    if d["truth"] is not None and np.isfinite(d["truth"]).any():
        tcell = [("slow" if y >= 0.5 else "fast") + "/" + ("ext" if t >= thr else "int") for y, t in zip(d["y"], d["truth"])]
        acc_old = np.mean([a == b for a, b in zip(d["cell_old"], tcell)]); acc_new = np.mean([a == b for a, b in zip(d["cell_new"], tcell)])
        print(f"   2x2 cell accuracy vs ground truth: old {acc_old * 100:.0f} %, new {acc_new * 100:.0f} %  (truth cells: " +
              ", ".join(f"{c}={tcell.count(c)}" for c in sorted(set(tcell))) + ")")
        for lbl, k in (("old", "ext_old"), ("new", "ext_new")):
            err = d[k] - d["truth"]
            print(f"   %ext error vs truth ({lbl}): median {np.nanmedian(err):+.3f}, IQR [{np.nanpercentile(err, 25):+.3f}, {np.nanpercentile(err, 75):+.3f}], within +-0.15: {np.nanmean(np.abs(err) < 0.15) * 100:.0f} %")


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--csv", required=True); ap.add_argument("--stress", default=None)
    ap.add_argument("--out", required=True); ap.add_argument("--title", default="Fig. B -- mix ramp at fixed lambda: what each scorer reports")
    ap.add_argument("--mixA", default="mix A"); ap.add_argument("--mixB", default="mix B")
    a = ap.parse_args()
    runs = [("no aggressor", load(a.csv))] + ([("with aggressor (2 hogs)", load(a.stress))] if a.stress else [])
    fig, axes = plt.subplots(5, len(runs), figsize=(6.5 * len(runs), 9), sharex="col", squeeze=False)
    for ci, (name, d) in enumerate(runs):
        ax = axes[:, ci]; t = d["t"]
        ax[0].plot(t, d["ramp"], color="#d62728"); ax[0].set_ylabel(f"share of {a.mixB}", fontsize=8); ax[0].set_ylim(-0.05, 1.05)
        ax[1].plot(t, d["lambda"], color="0.3"); ax[1].set_ylabel("lambda (req/s)", fontsize=8); ax[1].set_ylim(0, np.nanmax(d["lambda"]) * 1.3)
        ax[2].plot(t, d["util"], color="#1f77b4"); ax[2].set_ylabel("rho/k", fontsize=8); ax[2].axhline(0.6, color="0.6", ls=":", lw=0.8)
        ax[3].plot(t, d["ext_old"], color="#d62728", lw=1); ax[3].set_ylabel("%ext, old D(lambda)", fontsize=8); ax[3].set_ylim(-0.05, 1.05); ax[3].axhline(0.5, color="0.6", ls=":", lw=0.8)
        ax[4].plot(t, d["ext_new"], color="#1f77b4", lw=1); ax[4].set_ylabel("%ext, new D_e[rho]", fontsize=8); ax[4].set_ylim(-0.05, 1.05); ax[4].axhline(0.5, color="0.6", ls=":", lw=0.8)
        if d["truth"] is not None and np.isfinite(d["truth"]).any():
            for k in (3, 4):
                ax[k].plot(t, d["truth"], "k--", lw=0.8, label="ground truth"); ax[k].legend(fontsize=7, loc="upper left")
        ax[0].set_title(name, fontsize=10); ax[4].set_xlabel("time (s)")
        for x in ax:
            x.grid(alpha=.3)
        table(name, d)
    fig.suptitle(a.title + f"  ({a.mixA} -> {a.mixB})", fontsize=11)
    fig.tight_layout(rect=(0, 0.04, 1, 0.96))
    fig.text(0.01, 0.005, "Notation: " + NOTATION + ";  %ext = extrinsic share reported by Algorithm 1;  old = dictionary indexed by lambda, new = per-endpoint dictionary indexed by rho", fontsize=6, color="0.3")
    fig.savefig(a.out, dpi=140); print("wrote", a.out)


if __name__ == "__main__":
    main()
