#!/usr/bin/env python3
"""Addendum B.2: does g = u / cbar collapse across mixes as h does? u = process CPU per served request per step
(the recorder's cpu field over the step's arrivals), cbar = the sweep mix's mean cost; the four Phase E synthetic
mixes, F3 layout: (a) u by lambda, (b) u by rho, (c) g = u / cbar by rho; cross-mix ratio of g at grid rho below the knee.

usage: g_collapse.py --out DIR --mix uniform=RUN --mix short=RUN ... [--grid 0.1,...] [--knee 0.65] [--costs c.json]
"""
import argparse, csv, os, sys
import numpy as np
import matplotlib; matplotlib.use("Agg"); import matplotlib.pyplot as plt
sys.path.insert(0, os.path.dirname(__file__))
from analyze import load_run
from dictionary import fit_service_dict


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--out", required=True); ap.add_argument("--mix", action="append", required=True)
    ap.add_argument("--grid", default="0.1,0.2,0.3,0.4,0.5,0.6,0.7"); ap.add_argument("--knee", type=float, default=0.65); ap.add_argument("--costs", default=None)
    a = ap.parse_args(); os.makedirs(a.out, exist_ok=True)
    S = {}
    for spec in a.mix:
        name, d = spec.split("=", 1); R = load_run(d); SD = fit_service_dict(R, 5.0, a.costs); rate = {s["step"]: s["rate"] for s in R["steps"]}
        st = [(s, v) for s, v in sorted(SD.steps.items()) if np.isfinite(v.get("served", np.nan)) and v["served"] >= 0.95 and np.isfinite(v["u_ms"])]
        S[name] = dict(lam=np.array([rate[s] for s, _ in st], float), rho=np.array([v["rho"] for _, v in st]), u=np.array([v["u_ms"] for _, v in st]),
                       g=np.array([v["g"] for _, v in st]), cbar=SD.cbar_sweep, over=SD.overloaded)
        print(f"{name:8s} cbar {SD.cbar_sweep:.2f} ms; served steps rho {S[name]['rho'].min():.2f}..{S[name]['rho'].max():.2f}; u {S[name]['u'].min():.2f}..{S[name]['u'].max():.2f} ms/req; g {S[name]['g'].min():.3f}..{S[name]['g'].max():.3f}")
    names = [m.split("=", 1)[0] for m in a.mix]; ref = names[0]; grid = [float(x) for x in a.grid.split(",")]
    rows = []
    print(f"\n{'rho':>5s}  " + "  ".join(f"{n + ' g':>10s}" for n in names) + "   ratio range to " + ref)
    for u_ in grid:
        g = {n: (float(np.interp(u_, S[n]["rho"], S[n]["g"])) if S[n]["rho"].min() <= u_ <= S[n]["rho"].max() else np.nan) for n in names}
        r = [g[n] / g[ref] for n in names[1:] if np.isfinite(g[n]) and np.isfinite(g[ref])]
        rows.append(dict(rho=u_, **{f"g_{n}": g[n] for n in names}, ratio_min=min(r) if r else np.nan, ratio_max=max(r) if r else np.nan))
        print(f"{u_:5.2f}  " + "  ".join(f"{g[n]:10.3f}" for n in names) + (f"   [{min(r):.3f}, {max(r):.3f}]" if r else "   -"))
    with open(os.path.join(a.out, "g_collapse.csv"), "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys())); w.writeheader(); w.writerows(rows)
    fig, axs = plt.subplots(1, 3, figsize=(13, 3.9)); cols = ["#2A6F97", "#D85A30", "#1D9E75", "#7F77DD"]
    for j, n in enumerate(names):
        s = S[n]; lab = f"{n}, c̄ {s['cbar']:.1f} ms"
        axs[0].plot(s["lam"], s["u"], "o-", ms=4, color=cols[j], label=lab); axs[1].plot(s["rho"], s["u"], "o-", ms=4, color=cols[j], label=lab); axs[2].plot(s["rho"], s["g"], "o-", ms=4, color=cols[j], label=lab)
    axs[0].set_xlabel("arrival rate λ (req/s)"); axs[0].set_ylabel("u = process CPU per request (ms)"); axs[0].set_title("a · u by λ", loc="left")
    axs[1].set_xlabel("offered utilisation ρ"); axs[1].set_title("b · u by ρ", loc="left")
    axs[2].set_xlabel("offered utilisation ρ"); axs[2].set_ylabel("g = u ÷ c̄"); axs[2].set_title("c · g = u ÷ c̄ by ρ", loc="left"); axs[2].axhline(1, color="grey", lw=0.8)
    for ax in axs[1:]: ax.axvline(a.knee, color="grey", ls=":", lw=1)
    axs[0].legend(loc="upper right", fontsize=8)
    fig.suptitle("Fig. G-g — CPU per request u and g = u ÷ c̄ across the four synthetic mixes (process CPU, RUSAGE_SELF, per served step; served steps only)", x=0.01, ha="left", fontsize=10)
    fig.tight_layout(rect=(0, 0, 1, 0.92)); fig.savefig(os.path.join(a.out, "figG-g-collapse.png"), dpi=130); plt.close(fig); print("wrote", os.path.join(a.out, "figG-g-collapse.png"))


if __name__ == "__main__":
    main()
