#!/usr/bin/env python3
"""G-C (PHASED-HANDOFF section 1): where did the Phase C "invisible" wait sit, and does the gate make it visible?

For each run (a uniform sweep recorded with the Phase D recorder: stamp on, scheduler-latency metric on, gate on or
off) the per-second series of
  (i)   x  = stamp -> tap            (caller send stamp to the transport tap: network + kernel socket + reader goroutine)
  (ii)  sched = runtime scheduler latency p50 of the window (/sched/latencies:seconds delta; every goroutine)
  (iii) g  = admission-gate wait     (G-A; 0 with the gate off)
  plus  q  = tap -> handler goroutine running
and, per step, the service-level medians of the server-side Lat_e (pp: tap -> return minus downstream-blocked, which
includes g and q) and the stamped Lat_e (f = x + pp), with the ratio pp / f and (FL + g) / f ("does gate + floor
reproduce the stamped latency within 10 %").

Usage: gc_check.py --out DIR --run label=dir [--run label=dir ...] [--settle 5]
Writes DIR/gc_steps.csv and DIR/gc.png (one row per run: the series on the left, pp vs f per step on the right).
"""
import argparse, csv, json, os, sys
from collections import defaultdict
import numpy as np
import matplotlib; matplotlib.use("Agg"); import matplotlib.pyplot as plt
sys.path.insert(0, os.path.dirname(__file__))
from analyze import load_run
from dictionary import settled_windows, NOTATION

# "all" array: n,p50,p90,p99,mean,max,pp50,pp90,q50,q90,x50,x90,ns,f50,f90,g50,g90
A_N, A_P50, A_PP50, A_Q50, A_X50, A_NS, A_F50, A_G50 = 0, 1, 6, 8, 10, 12, 13, 15


def pooled(w, key):
    r = w.get(key)
    if not r:
        return np.zeros(0)
    return np.concatenate([np.asarray(v, float) for v in r.values() if len(v)]) if any(len(v) for v in r.values()) else np.zeros(0)


def floor_of(R, W, step_of):
    """service-level floor: pooled median of pp at the lowest step (the dictionary's FL, pooled over the uniform mix)."""
    s0 = min(s for s in step_of if s is not None and s >= 0)
    v = np.concatenate([pooled(w, "rawp") for w, s in zip(W, step_of) if s == s0] or [np.zeros(0)])
    return float(np.median(v)) / 1e6 if len(v) else float("nan")


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--out", required=True); ap.add_argument("--run", action="append", required=True)
    ap.add_argument("--settle", type=float, default=5.0)
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)
    runs = [(p.split("=", 1)[0], p.split("=", 1)[1]) for p in a.run]
    fig, axes = plt.subplots(len(runs), 2, figsize=(13, 3.8 * len(runs)), squeeze=False)
    rows = []
    for ri, (label, d) in enumerate(runs):
        R = load_run(d)
        W, step_of, _ = settled_windows(R, a.settle)
        meta = R.get("meta", {})
        gate = next((w.get("gate") for w in W if "gate" in w), None)
        fl = floor_of(R, W, step_of)
        # per second
        sec = defaultdict(lambda: defaultdict(list))
        for w in W:
            s = w["t"] // 1000
            for key, src in (("x", "rawx"), ("q", "rawq"), ("g", "rawg"), ("pp", "rawp"), ("f", "rawf")):
                v = pooled(w, src)
                if len(v):
                    sec[s][key].extend((v / 1e6).tolist())
            if "sched" in w and w["sched"][2] > 0:
                sec[s]["sched"].append(w["sched"][1] / 1e6)  # p90: the p50 is dominated by the runtime's own short wake-ups
            sec[s]["a"].append(w["a"] / (w.get("w", 100) / 1000.0))
        secs = sorted(sec)
        t0 = secs[0]
        med = lambda s, k: float(np.median(sec[s][k])) if sec[s][k] else np.nan
        ts = np.array([s - t0 for s in secs])
        series = {k: np.array([med(s, k) for s in secs]) for k in ("x", "q", "g", "sched", "pp", "f")}
        lam = np.array([float(np.mean(sec[s]["a"])) for s in secs])
        axl, axr = axes[ri]
        axl.plot(ts, series["x"], "-", color="#1f77b4", lw=1.2, label="(i) x: stamp -> tap (network + socket + reader goroutine)")
        axl.plot(ts, series["sched"], "-", color="#2ca02c", lw=1.2, label="(ii) sched: runtime scheduler latency p90 of the window (all goroutines)")
        axl.plot(ts, series["g"], "-", color="#d62728", lw=1.2, label="(iii) g: admission-gate wait")
        axl.plot(ts, series["q"], "-", color="#9467bd", lw=0.9, alpha=0.8, label="q: tap -> handler goroutine running")
        axl.set_yscale("symlog", linthresh=0.01); axl.set_ylabel("per-second median, ms"); axl.grid(alpha=.3)
        axl.set_title(f"{label}: where the wait sits (gate {'off' if not gate else f'on, cap {gate}'}), server stream", fontsize=9)
        axl.legend(fontsize=6, loc="upper left")
        ax2 = axl.twinx(); ax2.plot(ts, lam, "-", color="0.6", lw=0.7); ax2.set_ylabel("lambda (req/s)", color="0.5", fontsize=7)
        # per step
        steps = sorted(set(s for s in step_of if s is not None and s >= 0))
        rate = {s["step"]: s["rate"] for s in R["steps"]}
        st_rows = []
        for st in steps:
            ws = [w for w, s in zip(W, step_of) if s == st]
            agg = {k: np.concatenate([pooled(w, src) for w in ws] or [np.zeros(0)]) for k, src in (("x", "rawx"), ("q", "rawq"), ("g", "rawg"), ("pp", "rawp"), ("f", "rawf"))}
            sch = [w["sched"][1] for w in ws if "sched" in w and w["sched"][2] > 0]
            sch50 = [w["sched"][0] for w in ws if "sched" in w and w["sched"][2] > 0]
            served = sum(w["a"] for w in ws) / sum(w.get("w", 100) / 1000.0 for w in ws)
            m = {k: float(np.median(v)) / 1e6 if len(v) else np.nan for k, v in agg.items()}
            m["sched"] = float(np.median(sch)) / 1e6 if sch else np.nan      # per-window p90, median over the step's windows
            m["sched50"] = float(np.median(sch50)) / 1e6 if sch50 else np.nan
            m["pp90"] = float(np.percentile(agg["pp"], 90)) / 1e6 if len(agg["pp"]) else np.nan
            m["f90"] = float(np.percentile(agg["f"], 90)) / 1e6 if len(agg["f"]) else np.nan
            r = dict(run=label, step=st, rate=rate.get(st, np.nan), served=served, fl=fl, **m,
                     pp_over_f=m["pp"] / m["f"] if m["f"] else np.nan, flg_over_f=(fl + m["g"]) / m["f"] if m["f"] else np.nan,
                     x_share=m["x"] / m["f"] if m["f"] else np.nan, n=len(agg["pp"]), gate=gate or 0)
            st_rows.append(r); rows.append(r)
        rt = np.array([r["rate"] for r in st_rows]); sv = np.array([r["served"] for r in st_rows])
        axr.plot(rt, [r["f"] for r in st_rows], "k-o", ms=4, lw=1.2, label="f: stamped Lat (x + pp), p50")
        axr.plot(rt, [r["pp"] for r in st_rows], "-s", color="#d62728", ms=4, lw=1.2, label="pp: server-side Lat_e (incl. g, q), p50")
        axr.plot(rt, [r["fl"] + r["g"] for r in st_rows], "--^", color="#ff7f0e", ms=4, lw=1.0, label="FL + g (floor + gate wait)")
        axr.plot(rt, [r["x"] for r in st_rows], ":", color="#1f77b4", lw=1.0, label="x: stamp -> tap")
        axr.set_yscale("log"); axr.set_xlabel("lambda target (req/s)"); axr.set_ylabel("ms"); axr.grid(alpha=.3); axr.legend(fontsize=6)
        axr.set_title(f"{label}: per step; pp/f = {np.nanmedian([r['pp_over_f'] for r in st_rows]):.2f} (median over steps), served/target at top step {sv[-1] / rt[-1]:.2f}", fontsize=9)
    fig.suptitle("G-C: stamp -> tap (x) vs runtime scheduler latency vs admission-gate wait; uniform sweep, 4 cores at 3.1 GHz", fontsize=11)
    fig.tight_layout(rect=(0, 0.04, 1, 0.96))
    fig.text(0.01, 0.005, "Notation: " + NOTATION + ";  x = caller stamp -> transport tap;  g = admission-gate wait;  q = tap -> handler goroutine running;  sched = Go runtime /sched/latencies p90 per window", fontsize=6, color="0.3")
    fig.savefig(os.path.join(a.out, "gc.png"), dpi=140)
    with open(os.path.join(a.out, "gc_steps.csv"), "w", newline="") as f:
        wri = csv.DictWriter(f, fieldnames=list(rows[0].keys())); wri.writeheader(); wri.writerows(rows)
    print(f"{'run':24s} {'step':>4s} {'rate':>6s} {'served':>7s} {'x':>7s} {'q':>7s} {'g':>7s} {'sched90':>8s} {'pp50':>7s} {'f50':>7s} {'pp/f':>5s} {'(FL+g)/f':>8s} {'x/f':>5s}   (ms; sched90 = per-window p90 of the runtime scheduler latency)")
    for r in rows:
        print(f"{r['run']:24s} {r['step']:4d} {r['rate']:6.0f} {r['served']:7.0f} {r['x']:7.3f} {r['q']:7.3f} {r['g']:7.3f} {r['sched']:8.3f} {r['pp']:7.3f} {r['f']:7.3f} {r['pp_over_f']:5.2f} {r['flg_over_f']:8.2f} {r['x_share']:5.2f}")
    print("wrote", os.path.join(a.out, "gc.png"), os.path.join(a.out, "gc_steps.csv"))


if __name__ == "__main__":
    main()
