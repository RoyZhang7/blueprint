#!/usr/bin/env python3
"""Phase G section 4 figure, F6 layout (paper_figs.before_after) with the verdict row as a third strip: per row, left
"before" = Phase F scoring (service dictionary with the wait guard, <run>/b4g), right "after" = G4 (OOR guard only, the
Addendum C verdict), <run>/b4g4/verdict.csv. Panels: observed p50 / D[rho; mix] / twin; share E (score vs
truth); strips: truth ext, scorer ext (grey = abstained), G4 verdict row (coloured).

usage: g4_fig.py --out FILE.png --row "label|run" [--row ...]
Title names the stream (server-side Lat_e p50, processing) and the signals (E from D[rho; mix]; I_g, Q, slot busy).
"""
import argparse, csv, os
import numpy as np
import matplotlib; matplotlib.use("Agg"); import matplotlib.pyplot as plt

ROWC = {-1: "#BBBBBB", 0: "#8F8E87", 2: "#2A6F97", 3: "#7F77DD", 4: "#1D9E75", 6: "#D5E4EE", 7: "#5A5A5A"}
ROWN = {-1: "OOR abstain", 0: "saturation: no verdict", 2: "2 own overload", 3: "3 service-time inflation", 4: "4 lock wait", 6: "6 nothing to explain", 7: "7 no verdict"}


def f(x):
    try: return float(x)
    except Exception: return np.nan


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--out", required=True); ap.add_argument("--row", action="append", required=True)
    a = ap.parse_args(); specs = [r.split("|", 1) for r in a.row]
    fig, axs = plt.subplots(3 * len(specs), 2, figsize=(13, 5.6 * len(specs)), gridspec_kw={"height_ratios": [2.0, 1.6, 1.1] * len(specs), "hspace": 0.75, "wspace": 0.3}, squeeze=False)
    for i, (lab, run) in enumerate(specs):
        g4 = list(csv.DictReader(open(os.path.join(run, "b4g4", "verdict.csv"))))
        bf_path = os.path.join(run, "b4g", "b4_windows.csv"); bf = {int(f(r["t"])): r for r in csv.DictReader(open(bf_path))} if os.path.exists(bf_path) else {}
        t0 = min(int(f(r["t"])) for r in g4); ts = np.array([int(f(r["t"])) - t0 for r in g4])
        lat = np.array([f(r["lat_ms"]) for r in g4]); D = np.array([f(r["D_mix_ms"]) for r in g4]); tr = np.array([f(r["truth_ext"]) for r in g4]); y = np.array([f(r["y"]) for r in g4])
        E = np.array([f(r["E"]) for r in g4]); Eg = np.array([f(r["E_guarded"]) for r in g4]); rows = np.array([int(f(r["row"])) for r in g4])
        for j, (name, ext, col) in enumerate((("before: Phase F (service dictionary, wait guard)", Eg, "#D85A30"), ("after: G4 (OOR guard only, Addendum C verdict)", E, "#2A6F97"))):
            ax = axs[3 * i, j]; ax.plot(ts, lat, color="#333333", lw=1.0, label="observed p50 (server-side Lat_e, processing)"); ax.plot(ts, D, color=col, lw=1.6, label="expected D[ρ; mix]")
            ax.set_ylim(0, np.nanpercentile(lat, 98) * 1.2 if np.isfinite(np.nanpercentile(lat, 98)) else 1); ax.set_title(((lab + chr(10)) if j == 0 else chr(10)) + name, loc="left", fontsize=9); ax.legend(loc="upper left", fontsize=8)
            if j == 0: ax.set_ylabel("latency (ms)")
            ax = axs[3 * i + 1, j]; ax.plot(ts, ext, color=col, lw=1.4, label="E = (Lat − D) ÷ Lat" + (" (NaN = abstained)" if j == 0 else "")); ax.plot(ts, tr, color="#1D9E75", lw=1.6, ls="--", label="truth share (twin / step baseline)")
            ax.axhline(0.5, color="#999999", ls=":"); ax.set_ylim(0, 1); ax.legend(loc="upper left", fontsize=8)
            if j == 0: ax.set_ylabel("share")
            ax = axs[3 * i + 2, j]
            ax.fill_between(ts, 2.1, 2.9, where=(tr >= 0.5), color="#1D9E75", step="mid", alpha=0.8)
            if j == 0:
                sc = np.array([bf.get(int(f(r["t"])), {}).get("cell_mix", "") for r in g4]); isext = np.array([c.endswith("/ext") for c in sc]); isna = np.array([c.endswith("n-a") or c == "" for c in sc])
                ax.fill_between(ts, 1.1, 1.9, where=isext, color=col, step="mid", alpha=0.9); ax.fill_between(ts, 1.1, 1.9, where=isna, color="#BBBBBB", step="mid")
                ax.set_yticks([1.5, 2.5]); ax.set_yticklabels(["scorer: ext (grey = abstained)", "truth: ext"])
            else:
                ax.fill_between(ts, 1.1, 1.9, where=(E >= 0.5) & (y >= 0.5), color=col, step="mid", alpha=0.9)
                for rw in sorted(set(rows)): ax.fill_between(ts, 0.1, 0.9, where=(rows == rw), color=ROWC.get(rw, "#000000"), step="mid", label=ROWN.get(rw, str(rw)))
                ax.set_yticks([0.5, 1.5, 2.5]); ax.set_yticklabels(["verdict row", "scorer: slow × ext", "truth: ext"])
            ax.set_ylim(0, 3); ax.grid(False); ax.set_xlabel("time (s)")
    from matplotlib.patches import Patch
    fig.legend([Patch(color=ROWC[k]) for k in (-1, 0, 2, 3, 4, 6, 7)] + [Patch(color="#1D9E75")], [ROWN[k] for k in (-1, 0, 2, 3, 4, 6, 7)] + ["truth: ext"], loc="lower center", ncol=4, fontsize=8, frameon=False, bbox_to_anchor=(0.5, -0.01))
    fig.suptitle("Fig. G4 — before / after per Phase F row: server-side Lat_e p50 (processing stream) against D[ρ; mix]; E = (Lat − D) ÷ Lat; verdict from E, I_g (cpu ÷ λc̄g), Q (Go runtime wait), slot busy; φ unavailable on these pre-probe runs", x=0.01, ha="left", fontsize=10)
    fig.savefig(a.out, dpi=120, bbox_inches="tight"); print("wrote", a.out)


if __name__ == "__main__":
    main()
