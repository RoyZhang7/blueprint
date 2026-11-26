#!/usr/bin/env python3
"""Phase G section 3 figure (G3.2): the self-interference curve. Left: bias of the uniform dictionary on the memwalk
mixes, (p50 - D[rho; mix]) / p50 per load step, one line per VioMemWalk share (and working set). Right: I_g =
cores busy / (lambda cbar g(rho)) with g from the uniform sweep, per step, same lines. Title names the stream
(server-side Lat_e p50, processing) and the signals.

usage: g3_fig.py --out FILE.png --dict UNIFORM_SWEEP/b4g2/service_dict.json --row "label|run" [--row ...]
  (each run must have b4g3/b4_windows.csv = b4_compare against the uniform dictionary)
"""
import argparse, csv, json, os, sys
import numpy as np
import matplotlib; matplotlib.use("Agg"); import matplotlib.pyplot as plt
sys.path.insert(0, os.path.dirname(__file__))
from analyze import load_run
from dictionary import assign

COLS = ["#2A6F97", "#1D9E75", "#B8860B", "#D85A30", "#7F77DD", "#8F8E87", "#333333", "#5A5A5A"]


def f(x):
    try: return float(x)
    except Exception: return np.nan


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--out", required=True); ap.add_argument("--dict", required=True); ap.add_argument("--row", action="append", required=True); ap.add_argument("--k", type=int, default=4)
    a = ap.parse_args()
    D = json.load(open(a.dict)); gm = np.array(D["rho"]); gv = np.array([np.nan if v is None else v for v in (D.get("g") or [np.nan] * len(gm))], float); ok = np.isfinite(gv)
    g_at = (lambda r: float(np.interp(r, gm[ok], gv[ok]))) if ok.any() else (lambda r: 1.0)
    fig, axs = plt.subplots(1, 2, figsize=(12, 4.4));
    for i, spec in enumerate(a.row):
        parts = spec.split("|"); lab, run = parts[0], parts[1]; sub = parts[2] if len(parts) > 2 else "b4g3"; c = COLS[i % len(COLS)]; ls = "--" if "128" in lab else "-"
        rows = list(csv.DictReader(open(os.path.join(run, sub, "b4_windows.csv"))))
        steps = sorted({r["step"] for r in rows if r["step"]}, key=int); xs, bias, igs = [], [], []
        R = load_run(run); W = R["windows"]; st = assign(W, R["steps"], "step")
        for s in steps:
            sel = [r for r in rows if r["step"] == s and r["lat_ms"] not in ("", "nan") and r["D_mix_ms"] not in ("", "nan")]
            if not sel: continue
            lat = np.median([f(r["lat_ms"]) for r in sel]); Dm = np.median([f(r["D_mix_ms"]) for r in sel]); rho = np.median([f(r["rho"]) for r in sel]) / a.k
            lam = np.median([f(r["lambda"]) for r in sel]); cb = np.median([f(r["avgS_ms"]) for r in sel])
            ws = [w for w, x in zip(W, st) if x == int(s) and w.get("cpu")]
            cores = np.mean([w["cpu"] / 1e9 / (w.get("w", 100) / 1000) for w in ws]) if ws else np.nan
            served = lam / float(R["steps"][int(s)]["rate"]) if int(s) < len(R["steps"]) else 1.0
            xs.append(rho); bias.append((lat - Dm) / lat); igs.append(cores / (lam * cb * g_at(rho) / 1000))
            if served < 0.95: xs[-1] = rho  # kept, marked below
        axs[0].plot(xs, bias, marker="o", color=c, ls=ls, label=lab); axs[1].plot(xs, igs, marker="o", color=c, ls=ls, label=lab)
    axs[0].axhline(0, color="#999999", lw=0.8); axs[0].axhspan(-0.15, 0.15, color="#F2F2F2", zorder=0); axs[0].set_xlabel("offered utilisation ρ (per core, mix c̄)"); axs[0].set_ylabel("bias = (p50 − D[ρ; mix]) ÷ p50"); axs[0].set_title("uniform dictionary on the VioMemWalk mixes: latency bias per step (grey = ±0.15)", loc="left", fontsize=9)
    axs[1].axhline(1, color="#999999", lw=0.8); axs[1].set_xlabel("offered utilisation ρ (per core, mix c̄)"); axs[1].set_ylabel("I_g = cores busy ÷ (λ c̄ g(ρ)), g from the uniform sweep"); axs[1].set_title("CPU per unit of configured work against the uniform profile", loc="left", fontsize=9)
    axs[1].legend(fontsize=7.5, loc="upper right")
    fig.suptitle("Fig. G3 — self-interference: server-side Lat_e p50 (processing) of VioMemWalk mixes vs the uniform dictionary D[ρ; mix], and I_g (2.0 GHz pinned, uncore fixed 2.7 GHz)", x=0.01, ha="left", fontsize=9.5)
    fig.tight_layout(rect=(0, 0, 1, 0.94)); fig.savefig(a.out, dpi=130); print("wrote", a.out)


if __name__ == "__main__":
    main()
