#!/usr/bin/env python3
"""Client-observed per-endpoint latency vs rho for a profiling run (A2).

Uses the load generator's 1 s per-endpoint reports (n, p50, p90, mean) and the
recorder's floors/arrivals to compute, per rho bin and endpoint, the
n-weighted median of the client p50; then tests two collapse rules:

  Rule B (processor sharing): g_e = Lat_e/FL_e is the same function of rho for all e
  Rule A (FIFO, additive):    W_e = Lat_e - FL_e is the same function of rho for all e

Usage: client_view.py RUN_DIR [--settle 5]
"""
import argparse, json, os, sys
from collections import defaultdict
import numpy as np
sys.path.insert(0, os.path.dirname(__file__))
from analyze import load_run, cpuset_size, assign, floors, rho_of_window, EP_A

try:
    import matplotlib; matplotlib.use("Agg"); import matplotlib.pyplot as plt
except Exception:
    plt = None


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("run"); ap.add_argument("--settle", type=float, default=5.0)
    a = ap.parse_args()
    R = load_run(a.run)
    names = R["run"]["endpoints"]; cfg = dict(zip(names, R["run"]["floors_ms"])); viol = dict(zip(names, R["run"]["violators"]))
    k = cpuset_size(R["meta"].get("cpuset", "0-7"))
    W = R["windows"]; step_of = assign(W, R["steps"], "step"); step_t0 = {s["step"]: s["t"] for s in R["steps"]}
    keep = [i for i, (w, s) in enumerate(zip(W, step_of)) if s is not None and s >= 0 and w["t"] >= step_t0[s] + a.settle * 1000]
    W = [W[i] for i in keep]; step_of = [step_of[i] for i in keep]
    fs = min(step_of)
    fl_rows = floors([w for w, s in zip(W, step_of) if s == fs], names, cfg)
    fl = {e: m for e, c, m, p90, n in fl_rows}
    # rho per second: sum of window rhos within that second (server arrivals)
    rho_sec = defaultdict(list)
    for w in W:
        rho_sec[w["t"] // 1000].append(rho_of_window(w, fl))
    rho_sec = {s: float(np.mean(v)) for s, v in rho_sec.items()}
    # client reports: per endpoint samples (rho, p50, n)
    per = defaultdict(list)
    for r in R["reports"]:
        if r.get("step", -9) < 0:
            continue
        sec = r["t"] // 1000 - 1  # report covers the previous second
        rs = [rho_sec.get(sec + d) for d in (0, -1, 1)]
        rs = [x for x in rs if x is not None]
        if not rs:
            continue
        rho = rs[0]
        for e, v in r["ep"].items():
            if v[0] >= 1:
                per[e].append((rho, v[1] / 1e6, v[0]))
    nb = 16; edges = np.linspace(0, k * 1.05, nb + 1); mids = (edges[:-1] + edges[1:]) / 2
    L = np.full((len(names), nb), np.nan)
    for i, e in enumerate(names):
        arr = np.array(per.get(e, []))
        if not len(arr):
            continue
        b = np.clip(np.digitize(arr[:, 0], edges) - 1, 0, nb - 1)
        for j in range(nb):
            m = b == j
            if arr[m, 2].sum() >= 15:
                vals = np.repeat(arr[m, 1], arr[m, 2].astype(int))
                L[i, j] = np.median(vals)
    normal = [i for i, e in enumerate(names) if not viol[e]]
    FLv = np.array([fl[e] for e in names])
    G = L / FLv[:, None]
    Wq = L - FLv[:, None]
    print("client-observed p50 per rho bin (normal endpoints):  rho/k | Rule B g_e: median IQR | Rule A W_e(ms): median IQR | net+grpc at floor removed? no")
    for j in range(nb):
        g = G[normal, j]; w = Wq[normal, j]
        if np.all(np.isnan(g)):
            continue
        print(f"  {mids[j] / k:4.2f} | g med={np.nanmedian(g):6.2f} IQR={np.nanpercentile(g, 75) - np.nanpercentile(g, 25):6.2f}"
              f" | W med={np.nanmedian(w):7.2f} IQR={np.nanpercentile(w, 75) - np.nanpercentile(w, 25):6.2f} ms  (n_ep={np.sum(~np.isnan(g))})")
    # which rule collapses better: compare dispersion normalised by level, bins with u>0.5
    hi = [j for j in range(nb) if mids[j] / k > 0.5 and not np.all(np.isnan(G[normal, j]))]
    cvB = np.nanmean([np.nanstd(G[normal, j]) / np.nanmean(G[normal, j]) for j in hi])
    cvA = np.nanmean([np.nanstd(Wq[normal, j]) / np.nanmean(Wq[normal, j]) for j in hi])
    print(f"dispersion across normal endpoints at u>0.5: Rule B (g_e) CV={cvB:.2f}   Rule A (W_e) CV={cvA:.2f}  -> {'FIFO/additive (Rule A)' if cvA < cvB else 'PS/multiplicative (Rule B)'} collapses better")
    with open(os.path.join(a.run, "client_dict.csv"), "w") as f:
        f.write("endpoint,violator,fl_ms,rho_mid,client_p50_ms\n")
        for i, e in enumerate(names):
            for j in range(nb):
                if not np.isnan(L[i, j]):
                    f.write(f"{e},{int(viol[e])},{fl[e]:.4f},{mids[j]:.3f},{L[i, j]:.4f}\n")
    if plt:
        fig, ax = plt.subplots(1, 2, figsize=(11, 4.4))
        for i, e in enumerate(names):
            ok = ~np.isnan(L[i]);
            if not ok.any(): continue
            st = dict(lw=0.6, color="0.6", alpha=0.6) if not viol[e] else dict(lw=1.5, marker="o", ms=3, label=e)
            ax[0].plot(mids[ok] / k, G[i, ok], **st); ax[1].plot(mids[ok] / k, Wq[i, ok], **st)
        ax[0].set_yscale("log"); ax[0].set_xlabel("rho/k"); ax[0].set_ylabel("client p50 / FL_e"); ax[0].set_title("Rule B view (PS)"); ax[0].legend(fontsize=7)
        ax[1].set_xlabel("rho/k"); ax[1].set_ylabel("client p50 - FL_e (ms)"); ax[1].set_title("Rule A view (FIFO additive)"); ax[1].legend(fontsize=7)
        fig.tight_layout(); fig.savefig(os.path.join(a.run, "client_rules.png"), dpi=130); print("wrote", os.path.join(a.run, "client_rules.png"))


if __name__ == "__main__":
    main()
