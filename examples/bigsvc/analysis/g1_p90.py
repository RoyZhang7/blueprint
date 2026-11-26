#!/usr/bin/env python3
"""Phase G 1.5: does W90 / K collapse across mixes as W50 / K does? Same inputs as e1_mix.py (the four synthetic
sweeps), the pooled p90 of each step above the mix's lowest-step p90 (W90) in place of the p50; K = c2bar / (2 cbar)
of the sweep mix from the service dictionary fit. Steps with served/target < 0.95 dropped (svc_curve.csv rule).

Usage: g1_p90.py --out DIR --mix uniform=RUN --mix short=RUN ... [--grid 0.45,...] [--knee 0.65]
Writes DIR/g1_p90_collapse.csv (per grid rho: W50, W90, W50/K, W90/K per mix and the cross-mix ratio ranges) and
DIR/figG1-p90-collapse.png in the F3 layout (W by lambda; W by rho; W/K by rho), p50 row above the p90 row.
"""
import argparse, csv, os, sys
import numpy as np
import matplotlib; matplotlib.use("Agg"); import matplotlib.pyplot as plt
sys.path.insert(0, os.path.dirname(__file__))
from analyze import load_run
from dictionary import fit_service_dict, cpuset_size


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--out", required=True); ap.add_argument("--mix", action="append", required=True)
    ap.add_argument("--grid", default="0.45,0.50,0.55,0.60,0.65,0.70"); ap.add_argument("--knee", type=float, default=0.65); ap.add_argument("--costs", default=None)
    a = ap.parse_args(); os.makedirs(a.out, exist_ok=True)
    S = {}
    for spec in a.mix:
        name, d = spec.split("=", 1)
        R = load_run(d); k = cpuset_size(R["meta"].get("cpuset", "0-7"))
        sd = fit_service_dict(R, 5.0, a.costs)
        rows = list(csv.DictReader(open(os.path.join(d, "svc_curve.csv"))))
        rows = [r for r in rows if float(r["lambda"]) / float(r["rate_target"]) >= 0.95]
        lam = np.array([float(r["lambda"]) for r in rows]); rho = np.array([float(r["rho"]) for r in rows]) / k
        p50 = np.array([float(r["p50_ms"]) for r in rows]); p90 = np.array([float(r["p90_ms"]) for r in rows])
        S[name] = dict(lam=lam, rho=rho, W50=p50 - p50[0], W90=p90 - p90[0], K=sd.K_sweep, cbar=sd.cbar_sweep)
        print(f"{name:8s} k={k} K {sd.K_sweep:.2f} ms cbar {sd.cbar_sweep:.2f} ms; served steps to rho {rho.max():.2f}; p50 floor {p50[0]:.2f} p90 floor {p90[0]:.2f} ms")
    names = [m.split("=", 1)[0] for m in a.mix]; ref = names[0]
    grid = [float(x) for x in a.grid.split(",")]
    out = []
    print(f"\n{'rho':>5s}  " + "  ".join(f"{n + ' W50/K':>12s}{n + ' W90/K':>12s}" for n in names) + "   W50/K ratio range   W90/K ratio range")
    for u in grid:
        v = {}
        for n in names:
            s = S[n]
            inr = s["rho"].min() <= u <= s["rho"].max()
            v[n] = dict(W50=np.interp(u, s["rho"], s["W50"]) if inr else np.nan, W90=np.interp(u, s["rho"], s["W90"]) if inr else np.nan)
            v[n]["WK50"] = v[n]["W50"] / s["K"]; v[n]["WK90"] = v[n]["W90"] / s["K"]
        def rng(key, floor):
            r = [v[n][key] / v[ref][key] for n in names[1:] if np.isfinite(v[n][key]) and np.isfinite(v[ref][key]) and v[ref][key.replace("WK", "W")] > floor]
            return (min(r), max(r)) if r else (np.nan, np.nan)
        r50 = rng("WK50", 0.05); r90 = rng("WK90", 0.05)
        row = dict(rho=u); [row.update({f"W50_{n}": v[n]["W50"], f"W90_{n}": v[n]["W90"], f"WK50_{n}": v[n]["WK50"], f"WK90_{n}": v[n]["WK90"]}) for n in names]
        row.update(WK50_ratio_min=r50[0], WK50_ratio_max=r50[1], WK90_ratio_min=r90[0], WK90_ratio_max=r90[1]); out.append(row)
        print(f"{u:5.2f}  " + "  ".join(f"{v[n]['WK50']:12.2f}{v[n]['WK90']:12.2f}" for n in names) + f"   [{r50[0]:.2f}, {r50[1]:.2f}]   [{r90[0]:.2f}, {r90[1]:.2f}]")
    with open(os.path.join(a.out, "g1_p90_collapse.csv"), "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(out[0].keys())); w.writeheader(); w.writerows(out)
    # figure: F3 layout, two rows
    fig, axs = plt.subplots(2, 3, figsize=(13, 7.2))
    cols = ["#2A6F97", "#D85A30", "#1D9E75", "#7F77DD"]
    for row_i, q in enumerate(("W50", "W90")):
        for j, n in enumerate(names):
            s = S[n]; W = np.clip(s[q], 0.01, None); lab = f"{n}, K {s['K']:.1f} ms"
            axs[row_i, 0].plot(s["lam"], W, "o-", ms=4, color=cols[j], label=lab)
            axs[row_i, 1].plot(s["rho"], W, "o-", ms=4, color=cols[j], label=lab)
            axs[row_i, 2].plot(s["rho"], W / s["K"], "o-", ms=4, color=cols[j], label=lab)
        for ax in axs[row_i]: ax.set_yscale("log")
        qq = "p50" if q == "W50" else "p90"
        axs[row_i, 0].set_xlabel("arrival rate λ (req/s)"); axs[row_i, 0].set_ylabel(f"W{qq[1:]} = {qq} above the mix's floor {qq} (ms)")
        axs[row_i, 0].set_title(f"{'abc'[0]} · {qq}, indexed by λ", loc="left")
        axs[row_i, 1].set_xlabel("offered utilisation ρ"); axs[row_i, 1].set_title(f"b · {qq}, indexed by ρ", loc="left")
        axs[row_i, 2].set_xlabel("offered utilisation ρ"); axs[row_i, 2].set_ylabel(f"W{qq[1:]} ÷ K"); axs[row_i, 2].set_title(f"c · {qq}, indexed by ρ, wait scaled by K", loc="left")
        for ax in axs[row_i, 1:]: ax.axvline(a.knee, color="grey", ls=":", lw=1)
        axs[row_i, 0].legend(loc="upper left", fontsize=8)
    fig.suptitle("Fig. G1-c — W ÷ K collapse across the four synthetic mixes, pooled p50 (top) against pooled p90 (bottom)" + chr(10) + "server-side Lat_e, gate wait "
                 "included (Phase D image k123g2); K = c̄² ÷ 2c̄ of each sweep mix; served steps only", x=0.01, ha="left", fontsize=10)
    fig.tight_layout(rect=(0, 0, 1, 0.94)); fig.savefig(os.path.join(a.out, "figG1-p90-collapse.png"), dpi=130); plt.close(fig)
    print("wrote", os.path.join(a.out, "figG1-p90-collapse.png"))


if __name__ == "__main__":
    main()
