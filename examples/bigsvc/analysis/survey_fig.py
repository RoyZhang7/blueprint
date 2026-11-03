#!/usr/bin/env python3
"""Phase F section 2 survey figure: per candidate, handler p50 / p90 (above the lowest-step floor) and the SUT's cores
busy vs lambda, from <run>/survey.csv (survey.py). Usage: survey_fig.py --out F.png --row "label=RUN" [--row ...]"""
import argparse, csv, os, sys, textwrap
import numpy as np
import matplotlib; matplotlib.use("Agg"); import matplotlib.pyplot as plt
sys.path.insert(0, os.path.dirname(__file__))
from dictionary import NOTATION


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--out", required=True); ap.add_argument("--row", action="append", required=True)
    ap.add_argument("--stream", default="server-side Lat_e (tap -> return minus downstream-blocked; gate wait inside on the Go services)")
    ap.add_argument("--dict-form", default="D[rho; mix] = Fbar + h(rho) K, Fbar = mixture median of the floor distributions (service dictionary, mix-corrected)")
    a = ap.parse_args()
    rows = [(r.rsplit("=", 1)[0], r.rsplit("=", 1)[1]) for r in a.row]
    fig, axes = plt.subplots(1, len(rows), figsize=(4.6 * len(rows), 4.2), squeeze=False)
    for ax, (lab, run) in zip(axes[0], rows):
        d = list(csv.DictReader(open(os.path.join(run, "survey.csv"))))
        lam = np.array([float(r["lam"]) for r in d]); p50 = np.array([float(r["p50"]) for r in d]); p90 = np.array([float(r["p90"]) for r in d])
        busy = np.array([float(r["busy"]) for r in d]); served = np.array([float(r["served"]) for r in d])
        ok = served >= 0.95
        ax.plot(lam[ok], np.maximum(p50[ok] - p50[0], 0.005), "o-", color="#1f77b4", label="p50 above the floor")
        ax.plot(lam[ok], np.maximum(p90[ok] - p90[0], 0.005), "s--", color="#1f77b4", alpha=0.7, label="p90 above the floor")
        if (~ok).any():
            ax.plot(lam[~ok], np.maximum(p50[~ok] - p50[0], 0.005), "o", color="0.6", label="served/target < 0.95")
        ax.set_yscale("log"); ax.set_xlabel("lambda at the service (req/s)"); ax.set_ylabel("ms above the lowest-step value", fontsize=8); ax.grid(alpha=.3)
        a2 = ax.twinx(); a2.plot(lam, busy, "-", color="#d62728", lw=1, label="cores busy / k"); a2.set_ylim(0, 1.05); a2.set_ylabel("cores busy / k", color="#d62728", fontsize=8)
        ax.set_title(textwrap.fill(lab, 38), fontsize=9); ax.legend(fontsize=6, loc="upper left")
    fig.suptitle(textwrap.fill("Fig. S (Phase F section 2) -- survey: does the handler queue before the service's knee?  latency stream: " + a.stream + ";  dictionary: " + a.dict_form, 150), fontsize=9)
    fig.tight_layout(rect=(0, 0.05, 1, 0.9))
    fig.text(0.01, 0.005, "Notation: " + NOTATION, fontsize=6, color="0.3")
    fig.savefig(a.out, dpi=140); print("wrote", a.out)


if __name__ == "__main__":
    main()
