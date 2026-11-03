#!/usr/bin/env python3
"""K-normalised spread of the wait across mixes (Phase E section 1.4 / section 3): for each mix's sweep, the pooled
p50 above the mix's own lowest-step p50 (W, the Fig. A "above floor" statistic) at matched rho, raw and divided by the
mix's K = c2bar / (2 cbar) (and, for the record, by cbar). If D[rho; mix] = Fbar + h(rho) K holds, W / K is one
curve for every mix. Overloaded steps dropped (same rule as the dictionary).

Usage: e1_mix.py --out DIR --ref uniform=RUN --mix short=RUN [--mix ...] [--costs costs.json] [--settle 5]
Writes DIR/e1_mix.csv (per rho: W, W/cbar, W/K of every mix and the ratio range to the reference) and prints it.
"""
import argparse, csv, os, sys
import numpy as np
sys.path.insert(0, os.path.dirname(__file__))
from analyze import load_run
from dictionary import fit_service_dict


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--out", required=True); ap.add_argument("--ref", required=True)
    ap.add_argument("--mix", action="append", default=[]); ap.add_argument("--settle", type=float, default=5.0); ap.add_argument("--costs", default=None)
    ap.add_argument("--grid", default="0.45,0.50,0.55,0.60,0.65,0.70")
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)
    runs = [tuple(a.ref.split("=", 1))] + [tuple(m.split("=", 1)) for m in a.mix]
    S = {}
    for name, d in runs:
        sd = fit_service_dict(load_run(d), a.settle, a.costs)
        st = sd.steps
        kept = sorted(s for s in st if s not in sd.overloaded)
        rho = np.array([st[s]["rho"] for s in kept]); p50 = np.array([st[s]["p50"] for s in kept])
        S[name] = dict(rho=rho, W=p50 - p50[0], K=sd.K_sweep, cbar=sd.cbar_sweep, rho_max=rho.max(), over=sd.overloaded)
    ref = runs[0][0]
    grid = [float(x) for x in a.grid.split(",")]
    rows = []
    hdr = f"{'rho':>5s} " + " ".join(f"{n + ' W':>10s}{n + ' W/K':>10s}" for n, _ in runs) + "   W ratio to " + ref + "   W/K ratio to " + ref
    print(hdr)
    for u in grid:
        W = {}; WK = {}
        for n, _ in runs:
            s = S[n]
            if s["rho"].min() <= u <= s["rho_max"]:
                W[n] = float(np.interp(u, s["rho"], s["W"])); WK[n] = W[n] / s["K"]
            else:
                W[n] = WK[n] = np.nan
        rw = [W[n] / W[ref] for n, _ in runs[1:] if np.isfinite(W[n]) and np.isfinite(W[ref]) and W[ref] > 0.05]
        rk = [WK[n] / WK[ref] for n, _ in runs[1:] if np.isfinite(WK[n]) and np.isfinite(WK[ref]) and W[ref] > 0.05]
        rows.append(dict(rho=u, **{f"W_{n}": W[n] for n, _ in runs}, **{f"WK_{n}": WK[n] for n, _ in runs}, **{f"Wcbar_{n}": W[n] / S[n]["cbar"] for n, _ in runs},
                         W_ratio_min=min(rw) if rw else np.nan, W_ratio_max=max(rw) if rw else np.nan, WK_ratio_min=min(rk) if rk else np.nan, WK_ratio_max=max(rk) if rk else np.nan))
        print(f"{u:5.2f} " + " ".join(f"{W[n]:10.2f}{WK[n]:10.2f}" for n, _ in runs) +
              (f"   [{min(rw):.2f}, {max(rw):.2f}]" if rw else "   -") + (f"   [{min(rk):.2f}, {max(rk):.2f}]" if rk else "   -"))
    print("mix: " + "; ".join(f"{n} cbar {S[n]['cbar']:.2f} ms, K {S[n]['K']:.2f} ms, profiled to rho {S[n]['rho_max']:.2f} (overloaded steps {S[n]['over'] or 'none'})" for n, _ in runs))
    with open(os.path.join(a.out, "e1_mix.csv"), "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys())); w.writeheader(); w.writerows(rows)
    print("wrote", os.path.join(a.out, "e1_mix.csv"))


if __name__ == "__main__":
    main()
