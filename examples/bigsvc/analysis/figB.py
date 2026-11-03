#!/usr/bin/env python3
"""Fig. B (Phase E, explicit layout): a mix ramp at fixed lambda -- what each scorer reports.

Two columns (the ramp without an aggressor; the same ramp with the aggressor, scored against the no-aggressor twin as
ground truth), five rows vs time, from b4_compare.py's b4_windows.csv (one row per second):
  1. share of the costly mix (ramp fraction)
  2. rho = lambda cbar / (f/f_nom) / k (left axis) and lambda (right axis)
  3. observed pooled p50 vs D(lambda) (the dictionary indexed by lambda) vs D[rho; mix] (the service dictionary,
     mix-corrected, at this second's mix); on the aggressor column also the twin's p50 at the same second
  4. %ext old / mix (Algorithm 1 smoothing, solid; raw, faint) and the truth from the twin (dashed)
  5. the windows each scorer labels extrinsic (%ext >= 0.5): old, mix (n/a = abstained, hatched), truth
Table (stdout): extrinsic-window fractions before / during / after the ramp, and the Q3 table (old cell accuracy;
mix strict, n/a = wrong; mix when it speaks; mix abstained), smoothed and raw.

%ext = (observed - dictionary) / observed; truth %ext = (observed - twin) / observed; cell = slow if y >= 0.5,
ext if %ext >= 0.5. --new plots the Phase D per-endpoint columns (D_new_ms / ext_new) instead of the mix ones.

Usage: figB.py --csv RUN/b4_windows.csv [--stress RUN2/b4_windows.csv] --out figB.png --stream "server-side Lat_e"
             [--stress-label "with aggressor (1 hog on core 5)"] [--mixA ...] [--mixB ...] [--title ...]
"""
import argparse, csv, os, sys, textwrap
import numpy as np
import matplotlib; matplotlib.use("Agg"); import matplotlib.pyplot as plt
sys.path.insert(0, os.path.dirname(__file__))
from dictionary import NOTATION

COLS = ("t", "lambda", "rho", "util", "lat_ms", "D_old_ms", "D_new_ms", "lat_twin_ms", "ext_old", "ext_new", "ext_old_raw", "ext_new_raw",
        "y", "y_raw", "ramp", "usable", "truth_ext", "D_mix_ms", "ext_mix", "ext_mix_raw")
NEW = dict(D="D_mix_ms", ext="ext_mix", raw="ext_mix_raw", cell="cell_mix", cell_raw="cell_mix_raw",
           label="D[rho; mix]: service dictionary, mix-corrected, at this second's mix", short="mix D[rho; mix]", ext_label="%ext mix: (Lat - D[rho; mix]) / Lat")


def load(p):
    rows = list(csv.DictReader(open(p)))
    g = lambda c: np.array([float(r[c]) if c in r and r[c] not in ("", "n/a") else np.nan for r in rows])
    d = {c: g(c) for c in COLS}
    for c in ("cell_old", "cell_new", "cell_shape", "cell_old_raw", "cell_new_raw", "cell_mix", "cell_mix_raw"):
        d[c] = [r.get(c, "") for r in rows]
    d["t"] = d["t"] - d["t"][0]
    return d


def phases(d):
    r = d["ramp"]
    return {"before": r <= 0, "during": (r > 0) & (r < 1), "after": r >= 1}


def q3(cells, tcell, label):
    strict = np.mean([a == b for a, b in zip(cells, tcell)])
    spoke = [(a, b) for a, b in zip(cells, tcell) if not a.endswith("/n-a")]
    when = np.mean([a == b for a, b in spoke]) if spoke else float("nan")
    print(f"   Q3 {label:30s} strict {strict * 100:5.1f} %   when it speaks {when * 100:5.1f} %   abstained {(1 - len(spoke) / len(cells)) * 100:5.1f} %")


def table(name, d, thr=0.5):
    print(f"{name}: {len(d['t'])} windows")
    print(f"   phase    n   extrinsic windows (%ext >= 0.5): old smoothed / raw   {NEW['short']} smoothed / raw   (n/a)")
    for ph, m in phases(d).items():
        if m.any():
            na = int(np.sum(np.isnan(d[NEW["ext"]][m])))
            with np.errstate(invalid="ignore"):
                fin = np.isfinite(d[NEW["ext"]][m]); finr = np.isfinite(d[NEW["raw"]][m])
                print(f"   {ph:7s} {m.sum():4d}   {np.mean(d['ext_old'][m] >= thr) * 100:6.1f} / {np.mean(d['ext_old_raw'][m] >= thr) * 100:5.1f} %   "
                      f"{np.mean(d[NEW['ext']][m][fin] >= thr) * 100 if fin.any() else float('nan'):6.1f} / {np.mean(d[NEW['raw']][m][finr] >= thr) * 100 if finr.any() else float('nan'):5.1f} %   ({na})")
    if np.isfinite(d["truth_ext"]).any():
        for sm, ykey, suffix in (("smoothed", "y", ""), ("raw", "y_raw", "_raw")):
            tcell = [("slow" if y >= 0.5 else "fast") + "/" + ("ext" if t >= thr else "int") for y, t in zip(d[ykey], d["truth_ext"])]
            q3(d["cell_old" + suffix], tcell, f"old D(lambda), {sm}")
            q3(d[NEW["cell"] if sm == "smoothed" else NEW["cell_raw"]], tcell, f"{NEW['short']}, {sm}")
        for lbl, k in (("old", "ext_old"), (NEW["short"], NEW["ext"])):
            err = d[k] - d["truth_ext"]
            print(f"   %ext error vs truth ({lbl}, smoothed): median {np.nanmedian(err):+.3f}, IQR [{np.nanpercentile(err, 25):+.3f}, {np.nanpercentile(err, 75):+.3f}], within +-0.15: {np.nanmean(np.abs(err) < 0.15) * 100:.0f} %")


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--csv", required=True); ap.add_argument("--stress", default=None)
    ap.add_argument("--out", required=True); ap.add_argument("--title", default="Fig. B -- mix ramp at fixed lambda: what each scorer reports")
    ap.add_argument("--mixA", default="mix A"); ap.add_argument("--mixB", default="mix B")
    ap.add_argument("--stress-label", default="with aggressor"); ap.add_argument("--stream", default="server-side Lat_e (tap -> return minus downstream-blocked)")
    ap.add_argument("--dict-form", default="D[rho; mix] = Fbar + h(rho) K, Fbar = mixture median of the floor distributions (service dictionary, mix-corrected)", help="dictionary form named in the title")
    ap.add_argument("--new", action="store_true", help="plot the Phase D per-endpoint columns (D_new_ms / ext_new) instead of the mix ones")
    a = ap.parse_args()
    if a.new:
        NEW.update(D="D_new_ms", ext="ext_new", raw="ext_new_raw", cell="cell_new", cell_raw="cell_new_raw",
                   label="D_e[rho]: per-endpoint dictionary at this second's mix (Phase D)", short="new D_e[rho]", ext_label="%ext new: (Lat_e - D_e[rho]) / Lat_e")
    runs = [("no aggressor", load(a.csv))] + ([(a.stress_label, load(a.stress))] if a.stress else [])
    fig, axes = plt.subplots(5, len(runs), figsize=(7 * len(runs), 11), sharex="col", squeeze=False,
                             gridspec_kw=dict(height_ratios=[0.7, 1, 1.4, 1.2, 0.9]))
    for ci, (name, d) in enumerate(runs):
        ax = axes[:, ci]; t = d["t"]
        ax[0].plot(t, d["ramp"], color="#d62728"); ax[0].set_ylabel(f"share of\n{a.mixB}", fontsize=8); ax[0].set_ylim(-0.05, 1.05)
        ax[1].plot(t, d["util"], color="#1f77b4", label="rho"); ax[1].set_ylabel("rho = lambda cbar/(f/f_nom)/k", fontsize=8); ax[1].axhline(0.65, color="0.6", ls=":", lw=0.8)
        ax[1].set_ylim(0, max(1.05, np.nanmax(d["util"]) * 1.1))
        a2 = ax[1].twinx(); a2.plot(t, d["lambda"], color="0.4", lw=0.8, label="lambda"); a2.set_ylabel("lambda (req/s)", fontsize=8, color="0.4")
        a2.set_ylim(0, np.nanmax(d["lambda"]) * 1.3)
        ax[2].plot(t, d["lat_ms"], color="k", lw=1.2, label="observed pooled p50 (Lat)")
        ax[2].plot(t, d["D_old_ms"], color="#d62728", lw=1.0, label="D(lambda): dictionary indexed by lambda")
        ax[2].plot(t, d[NEW["D"]], color="#1f77b4", lw=1.0, label=NEW["label"])
        if np.isfinite(d["lat_twin_ms"]).any():
            ax[2].plot(t, d["lat_twin_ms"], color="0.5", lw=1.0, ls="--", label="no-aggressor twin, p50 at the same second")
        ax[2].set_ylabel("ms", fontsize=8); ax[2].legend(fontsize=6, loc="upper left")
        if np.nanmax(d["lat_ms"]) / max(np.nanmin(d[NEW["D"]]), 1e-3) > 10:
            ax[2].set_yscale("log")
        ax[3].plot(t, d["ext_old_raw"], color="#d62728", lw=0.6, alpha=0.35)
        ax[3].plot(t, d[NEW["raw"]], color="#1f77b4", lw=0.6, alpha=0.35)
        ax[3].plot(t, d["ext_old"], color="#d62728", lw=1.3, label="%ext old: (Lat - D(lambda)) / Lat, smoothed")
        ax[3].plot(t, d[NEW["ext"]], color="#1f77b4", lw=1.3, label=NEW["ext_label"] + ", smoothed")
        if np.isfinite(d["truth_ext"]).any():
            ax[3].plot(t, d["truth_ext"], "k--", lw=0.9, label="truth: (Lat - twin) / Lat")
        ax[3].axhline(0.5, color="0.6", ls=":", lw=0.8); ax[3].set_ylim(-0.05, 1.05); ax[3].set_ylabel("%ext", fontsize=8); ax[3].legend(fontsize=6, loc="upper left")
        # row 5: labelled-extrinsic strips
        def strip(y0, mask, color, label, hatch=None):
            if hatch is None:
                ax[4].fill_between(t, y0, y0 + 0.8, where=mask, color=color, alpha=0.8, lw=0, step="mid", label=label)
            else:  # abstained windows: light fill + hatch (a hatch alone is invisible at alpha 0)
                ax[4].fill_between(t, y0, y0 + 0.8, where=mask, facecolor=color, alpha=0.25, hatch=hatch, edgecolor=color, lw=0, step="mid", label=label)
        strip(2.0, d["ext_old"] >= 0.5, "#d62728", "old labels extrinsic")
        with np.errstate(invalid="ignore"):
            strip(1.0, d[NEW["ext"]] >= 0.5, "#1f77b4", f"{NEW['short']} labels extrinsic")
        strip(1.0, np.isnan(d[NEW["ext"]]), "#1f77b4", f"{NEW['short']} abstains (n/a)", hatch="////")
        if np.isfinite(d["truth_ext"]).any():
            strip(0.0, d["truth_ext"] >= 0.5, "0.2", "truth extrinsic")
        ax[4].set_yticks([0.4, 1.4, 2.4]); ax[4].set_yticklabels(["truth", "mix" if not a.new else "new", "old"], fontsize=7); ax[4].set_ylim(-0.1, 2.9)
        ax[4].set_ylabel("labelled\nextrinsic", fontsize=8); ax[4].set_xlabel("time (s)")
        ax[0].set_title(name, fontsize=10)
        for x in ax:
            x.grid(alpha=.3)
        table(name, d)
    fig.suptitle(textwrap.fill(a.title + f"  ({a.mixA} -> {a.mixB})", 150) + "\n" + textwrap.fill(f"latency stream: {a.stream};  dictionary: {a.dict_form}", 150), fontsize=10)
    fig.tight_layout(rect=(0, 0.04, 1, 0.95))
    fig.text(0.01, 0.005, "Notation: " + NOTATION + ";  %ext = (observed - dictionary) / observed;  old = dictionary indexed by lambda, mix = service dictionary indexed by rho and corrected for the mix;  "
             "smoothing = Algorithm 1 causal Gaussian;  cell = slow (y >= 0.5) x extrinsic (%ext >= 0.5)", fontsize=6, color="0.3")
    fig.savefig(a.out, dpi=140); print("wrote", a.out)


if __name__ == "__main__":
    main()
