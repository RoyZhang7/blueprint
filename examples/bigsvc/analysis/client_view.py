#!/usr/bin/env python3
"""Caller-observed per-endpoint latency vs rho for a profiling run (rule A primary).

From the load generator's 1 s per-endpoint reports (n, p50, p90, mean) and the
recorder's arrivals (rho from c_e), per rho bin and endpoint, the n-weighted median
of the client p50. Rule A (FIFO, additive): W_e = Lat_e - FL_e is one function of rho
for all e -> W(rho) p50/p90, IQR and CV across endpoints per bin. Rule B
(Lat_e / FL_e) is printed as a diagnostic only.

Usage: client_view.py RUN_DIR [--settle 5] [--costs costs.json]
"""
import argparse, os, sys
import numpy as np
sys.path.insert(0, os.path.dirname(__file__))
from analyze import load_run
from dictionary import NOTATION, client_curve, client_floors, costs_of_run, notation

try:
    import matplotlib; matplotlib.use("Agg"); import matplotlib.pyplot as plt
except Exception:
    plt = None


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("run"); ap.add_argument("--settle", type=float, default=5.0); ap.add_argument("--costs", default=None)
    a = ap.parse_args()
    R = load_run(a.run)
    names = R["run"]["endpoints"]; viol = dict(zip(names, R["run"]["violators"]))
    c_ms, csrc = costs_of_run(R, a.costs)
    fl = client_floors(R, a.settle)
    cc = client_curve(R, a.settle, c_ms, fl, viol=viol)
    k, mids, L = cc["k"], cc["mids"], cc["L"]
    FLv = np.array([fl[e] for e in names])
    Wq = L - FLv[:, None]; G = L / FLv[:, None]
    normal = [i for i, e in enumerate(names) if not viol[e]]
    print(f"caller view, rho from {csrc}; avgS = {cc['avgS']:.2f} ms; k = {k}; overloaded steps (excluded from the profiled max): {cc['overloaded'] or 'none'}")
    print("rule A per rho bin: W50/W90 = n-weighted median/p90 over normal endpoints of Lat_e - FL_e; IQR_A/CV_A across endpoints; CV_B = rule-B diagnostic (Lat_e/FL_e)")
    print("   rho/k    W50     W90    IQR_A   CV_A   CV_B  n_ep")
    cvA, cvB = [], []
    for j in range(len(mids)):
        w = Wq[normal, j]; g = G[normal, j]
        if np.all(np.isnan(w)):
            continue
        W50 = cc["W50"][j]
        ca = np.nanstd(w) / abs(np.nanmean(w)) if W50 > 0.5 else np.nan
        cb = np.nanstd(g) / np.nanmean(g) if W50 > 0.5 else np.nan
        if not np.isnan(ca):
            cvA.append(ca); cvB.append(cb)
        print(f"   {mids[j] / k:4.2f} {W50:8.2f} {cc['W90'][j]:8.2f} {np.nanpercentile(w, 75) - np.nanpercentile(w, 25):7.3f} {ca:6.2f} {cb:6.2f}  {np.sum(~np.isnan(w))}")
    if cvA:
        print(f"wait regime (W50 > 0.5 ms): mean CV_A={np.mean(cvA):.2f}  CV_B={np.mean(cvB):.2f}  -> {'rule A (additive) collapses better' if np.mean(cvA) < np.mean(cvB) else 'rule B: CHECK'}")
    print(f"profiled W max (guard input) = {cc['wmax']:.2f} ms at rho <= {cc['rho_max']:.2f} (u {cc['rho_max'] / k:.2f})")
    dev = np.nanmedian(Wq - cc["W50"][None, :], axis=1)
    order = np.argsort(-np.nan_to_num(np.abs(dev), nan=-1))
    print("endpoints ranked by |median_b(Lat_e - FL_e - W_b)|:")
    for i in order[:6]:
        print(f"   {names[i]:14s} viol={int(viol[names[i]])} dev={dev[i]:+.3f} ms  FL={fl[names[i]]:.2f} ms")
    with open(os.path.join(a.run, "client_wait.csv"), "w") as f:
        f.write("rho_mid,util,W50_ms,W90_ms,g_diag\n")
        for j in range(len(mids)):
            if not np.isnan(cc["W50"][j]):
                f.write(f"{mids[j]:.3f},{mids[j] / k:.3f},{cc['W50'][j]:.4f},{cc['W90'][j]:.4f},{cc['G'][j]:.4f}\n")
    with open(os.path.join(a.run, "client_dict.csv"), "w") as f:
        f.write("endpoint,violator,fl_ms,rho_mid,client_p50_ms\n")
        for i, e in enumerate(names):
            for j in range(len(mids)):
                if not np.isnan(L[i, j]):
                    f.write(f"{e},{int(viol[e])},{fl[e]:.4f},{mids[j]:.3f},{L[i, j]:.4f}\n")
    if plt:
        fig, ax = plt.subplots(1, 2, figsize=(11, 4.8))
        for i, e in enumerate(names):
            ok = ~np.isnan(L[i])
            if not ok.any():
                continue
            st = dict(lw=0.6, color="0.6", alpha=0.6) if not viol[e] else dict(lw=1.5, marker="o", ms=3, label=e)
            ax[0].plot(mids[ok] / k, Wq[i, ok], **st); ax[1].plot(mids[ok] / k, G[i, ok], **st)
        ok = ~np.isnan(cc["W50"])
        ax[0].plot(mids[ok] / k, cc["W50"][ok], "k-", lw=2, label="W (median over endpoints)")
        ax[0].set_yscale("symlog", linthresh=0.1); ax[0].set_xlabel("rho/k"); ax[0].set_ylabel("Lat_e - FL_e (ms)"); ax[0].set_title("rule A (additive): primary"); ax[0].legend(fontsize=7)
        ax[1].set_yscale("log"); ax[1].set_xlabel("rho/k"); ax[1].set_ylabel("Lat_e / FL_e"); ax[1].set_title("rule B (multiplicative): diagnostic only"); ax[1].legend(fontsize=7)
        for x in ax:
            x.grid(alpha=.3)
        fig.tight_layout(); notation(fig, f"caller view, run {os.path.basename(a.run)}; k = {k}; avgS = {cc['avgS']:.2f} ms")
        fig.savefig(os.path.join(a.run, "client_rules.png"), dpi=130); print("wrote", os.path.join(a.run, "client_rules.png"))


if __name__ == "__main__":
    main()
