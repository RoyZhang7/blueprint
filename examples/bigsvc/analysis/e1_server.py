#!/usr/bin/env python3
"""W near the knee on the server-side stream (Phase D section 2, "W bound"): each mix's own W(rho) from its sweep
(analyze_profile's fit: n-weighted median of Lat_e - FL_e per rho bin, overloaded steps dropped), tabulated at matched
rho/k, with the ratio of every other mix to the reference (uniform) mix.

Usage: e1_server.py --out DIR --ref uniform=RUN --mix short=RUN [--mix ...] [--settle 5] [--costs costs.json]
Writes DIR/e1_server.csv (per rho/k bin: W of every mix, ratio range) and prints the table.
"""
import argparse, csv, os, sys
import numpy as np
sys.path.insert(0, os.path.dirname(__file__))
from analyze import load_run, analyze_profile


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--out", required=True); ap.add_argument("--ref", required=True)
    ap.add_argument("--mix", action="append", default=[]); ap.add_argument("--settle", type=float, default=5.0); ap.add_argument("--costs", default=None)
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)
    runs = [tuple(a.ref.split("=", 1))] + [tuple(m.split("=", 1)) for m in a.mix]
    fits = {}
    for name, d in runs:
        R = load_run(d)
        p = analyze_profile(R, d, a.settle, a.costs, quiet=True)
        f = p["fit"]
        fits[name] = dict(u=f["mids"] / f["k"], W50=f["W50"], W90=f["W90"], avgS=f["avgS"], rho_max=f["rho_max"] / f["k"], over=f["overloaded"])
    ref = runs[0][0]
    grid = np.arange(0.3, 1.0, 0.05)
    rows = []
    print(f"{'rho/k':>5s} " + " ".join(f"{n:>14s}" for n, _ in runs) + "   ratio to " + ref + " (min, max)")
    for u in grid:
        vals = {}
        for n, _ in runs:
            f = fits[n]; ok = np.isfinite(f["W50"]) & (f["u"] <= f["rho_max"] + 1e-9)
            if ok.sum() >= 2 and f["u"][ok].min() <= u <= f["u"][ok].max():
                vals[n] = float(np.interp(u, f["u"][ok], f["W50"][ok]))
            else:
                vals[n] = np.nan
        others = [vals[n] / vals[ref] for n, _ in runs[1:] if np.isfinite(vals[n]) and np.isfinite(vals[ref]) and vals[ref] > 0.05]
        rows.append(dict(u=round(u, 2), **{f"W_{n}": vals[n] for n, _ in runs}, ratio_min=min(others) if others else np.nan, ratio_max=max(others) if others else np.nan))
        print(f"{u:5.2f} " + " ".join(f"{vals[n]:14.2f}" for n, _ in runs) + (f"   [{min(others):.2f}, {max(others):.2f}]" if others else "   -"))
    print("avg(S) ms: " + ", ".join(f"{n} {fits[n]['avgS']:.2f} (profiled to rho/k {fits[n]['rho_max']:.2f}; overloaded steps {fits[n]['over'] or 'none'})" for n, _ in runs))
    with open(os.path.join(a.out, "e1_server.csv"), "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys())); w.writeheader(); w.writerows(rows)
    print("wrote", os.path.join(a.out, "e1_server.csv"))


if __name__ == "__main__":
    main()
