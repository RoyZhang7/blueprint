#!/usr/bin/env python3
"""R3 (Phase C section 1): c_e by NNLS with intercept on the loaded steps of a mixed sweep.

Per settled window of the sweep:  CPU_nominal(ms) = base * dt + sum_e c_e * a_e,
CPU_nominal = process CPU in the window scaled by the window's logged clock / f_nom (c_e is
expressed at f_nom). Windows are kept when rho/k lies in [lo, hi] (default 0.3 .. 0.9), rho
computed from the costs being fitted (iterated from the --init costs or the configured ones).
Non-negative least squares (scipy) with a free intercept (base CPU per second: the process's
idle work, GC, pollers).

Compares with the scaled-idle costs of Phase B (--compare costs.json) and flags endpoints whose
c_e moved by more than 15 %. Writes --out-costs (same layout as the other costs files).

Usage: r3_costs.py SWEEP_RUN [--svc reservation] [--init costs-*.json] [--compare costs-*.json]
                   [--lo 0.3 --hi 0.9] [--settle 5] [--out-costs costs-<svc>-nnls.json]
"""
import argparse, json, os, sys
import numpy as np
from scipy.optimize import nnls
sys.path.insert(0, os.path.dirname(__file__))
from analyze import load_run
from dictionary import (EP_A, NOMINAL_MHZ, costs_of_run, cpuset_size, rho_series, settled_windows, window_len_s)


def fit(W, names, c_ms, k, lo, hi, nominal, intercept=True, band="util"):
    """band="util": keep windows whose MEASURED clock-normalised utilisation (CPU / (dt k)), smoothed
    over 1 s, lies in [lo, hi] (cost-independent, so the selection does not feed back into the fit);
    band="rho": keep by rho/k from the current costs (the literal R3 wording, iterated)."""
    rs = rho_series(W, c_ms, 1.0)
    util = np.array([(w["cpu"] * ((w.get("mhz") or nominal) / nominal) / 1e6) / (window_len_s(w) * 1000 * k) if "cpu" in w else np.nan for w in W])
    # 1 s centred smoothing of the measured utilisation (same as rho_series)
    ts = np.array([w["t"] for w in W]); us = util.copy()
    for i in range(len(W)):
        m = (ts >= ts[i] - 500) & (ts <= ts[i] + 500)
        us[i] = np.nanmean(util[m])
    sel = us if band == "util" else np.array(rs) / k
    keep = [i for i in range(len(W)) if "cpu" in W[i] and lo <= sel[i] <= hi]
    X, y, mhz = [], [], []
    for i in keep:
        w = W[i]
        f = w.get("mhz") or nominal
        X.append(([window_len_s(w)] if intercept else []) + [w["ep"].get(e, [0])[EP_A] for e in names])
        y.append(w["cpu"] * (f / nominal) / 1e6)  # ms of CPU at the nominal clock
        mhz.append(f)
    if len(keep) < 50:
        return None
    X = np.array(X, float); y = np.array(y)
    beta, rnorm = nnls(X, y)
    pred = X @ beta
    r2 = 1 - np.sum((y - pred) ** 2) / np.sum((y - y.mean()) ** 2)
    off = 1 if intercept else 0
    return dict(c={e: float(beta[i + off]) for i, e in enumerate(names)}, base=float(beta[0]) if intercept else 0.0, n=len(keep),
                r2=float(r2), mhz=float(np.mean(mhz)), rho_range=(min(rs[i] for i in keep) / k, max(rs[i] for i in keep) / k),
                util_range=(float(np.nanmin(us[keep])), float(np.nanmax(us[keep]))))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("run"); ap.add_argument("--svc", default=None); ap.add_argument("--init", default=None)
    ap.add_argument("--also", action="append", default=[], help="pool the windows of these sweeps too (a second mix makes the per-endpoint split identifiable)")
    ap.add_argument("--compare", default=None); ap.add_argument("--lo", type=float, default=0.3); ap.add_argument("--hi", type=float, default=0.9)
    ap.add_argument("--settle", type=float, default=5.0); ap.add_argument("--out-costs", default=None)
    ap.add_argument("--variant", choices=["intercept", "average"], default="intercept", help="which fit to write: marginal c_e (NNLS with intercept, R3) or average c_e (no intercept)")
    a = ap.parse_args()
    R = load_run(a.run, svc=a.svc)
    names = R["run"]["endpoints"]
    k = cpuset_size(R["meta"].get("cpuset", "")) if R.get("meta") else 1
    c_ms, src = costs_of_run(R, a.init)
    W, step_of, _ = settled_windows(R, a.settle)
    for d in a.also:
        R2 = load_run(d, svc=a.svc)
        W2, _, _ = settled_windows(R2, a.settle)
        W = W + W2
    has_mhz = sum("mhz" in w for w in W)
    print(f"{os.path.basename(a.run)}{' + ' + ' + '.join(os.path.basename(d) for d in a.also) if a.also else ''} ({a.svc or 'SUT'}): "
          f"{len(W)} settled windows, k={k}, clock logged in {has_mhz} of them "
          f"(f_nom {NOMINAL_MHZ:.0f} MHz){'' if has_mhz else ' -- NO per-window clock: c_e assumes f_nom'}; starting costs: {src}")
    # identifiability: with one fixed mix every a_e column is proportional to the total, so only the
    # mix-average cost is determined; report the smallest pairwise angle between endpoint columns
    A = np.array([[w["ep"].get(e, [0])[EP_A] for e in names] for w in W], float)
    A = A / (np.linalg.norm(A, axis=0) + 1e-12)
    C = A.T @ A
    off = [C[i, j] for i in range(len(names)) for j in range(i + 1, len(names))]
    if off:
        print(f"   column collinearity: max |cos| between endpoint arrival columns = {max(off):.4f} (1 = not identifiable; a second mix lowers it)")
    # window band by MEASURED utilisation (cost-independent); the literal rho/k selection is reported too
    res = fit(W, names, c_ms, k, a.lo, a.hi, NOMINAL_MHZ, intercept=True, band="util")
    if res is None:
        sys.exit("fewer than 50 windows in the utilisation band; widen --lo/--hi")
    res0 = fit(W, names, c_ms, k, a.lo, a.hi, NOMINAL_MHZ, intercept=False, band="util")
    print(f"   windows with measured utilisation {a.lo}..{a.hi}: n={res['n']} (rho/k by the starting costs {res['rho_range'][0]:.2f}..{res['rho_range'][1]:.2f}), mean clock {res['mhz']:.0f} MHz")
    print(f"   NNLS with intercept (marginal c_e):  R2={res['r2']:.3f}, base={res['base']:.1f} ms CPU/s ({res['base'] / 1000 / k:.2f} of {k} cores); " + ", ".join(f"{e}={v:.3f}" for e, v in res["c"].items()))
    print(f"   NNLS without intercept (average c_e): R2={res0['r2']:.3f}; " + ", ".join(f"{e}={v:.3f}" for e, v in res0["c"].items()))
    rl = fit(W, names, c_ms, k, a.lo, a.hi, NOMINAL_MHZ, intercept=True, band="rho")
    if rl:
        print(f"   (literal R3 band by rho/k from the starting costs: n={rl['n']}, R2={rl['r2']:.3f}, base={rl['base']:.1f}; " + ", ".join(f"{e}={v:.3f}" for e, v in rl["c"].items()) + ")")
    c_ms = res["c"] if a.variant == "intercept" else res0["c"]
    print(f"   -> costs written: {'marginal (with intercept)' if a.variant == 'intercept' else 'average (no intercept)'}")
    cmp = None
    if a.compare:
        cmp = json.load(open(a.compare)); cmp = cmp.get("costs_ms", cmp)
        print("   endpoint            c_e NNLS (ms)   c_e scaled-idle (ms)   change")
        flag = []
        for e in names:
            if e in cmp and cmp[e] > 0:
                ch = (c_ms[e] - cmp[e]) / cmp[e]
                mark = "  <-- moved > 15 %" if abs(ch) > 0.15 else ""
                if mark:
                    flag.append(e)
                print(f"   {e:18s} {c_ms[e]:12.3f} {cmp[e]:20.3f} {ch * 100:+8.1f} %{mark}")
        if flag:
            print(f"   ** {len(flag)} of {len(names)} endpoints moved by more than 15 % vs {os.path.basename(a.compare)}: {', '.join(flag)}")
        else:
            print(f"   all endpoints within 15 % of {os.path.basename(a.compare)}")
    out = a.out_costs or os.path.join(os.path.dirname(a.run.rstrip('/\\')), f"costs-{a.svc or 'sut'}-nnls.json")
    json.dump({"costs_ms": c_ms, "variant": a.variant, "marginal_ms": res["c"], "base_ms_per_s": res["base"], "average_ms": res0["c"],
               "nominal_mhz": NOMINAL_MHZ, "windows": res["n"], "r2_intercept": res["r2"], "r2_no_intercept": res0["r2"],
               "util_band": [a.lo, a.hi], "method": f"NNLS ({a.variant}) on windows of {', '.join(os.path.basename(d) for d in [a.run] + a.also)} with measured utilisation in [{a.lo}, {a.hi}], clock-normalised to {NOMINAL_MHZ:.0f} MHz",
               "runs": [os.path.basename(d) for d in [a.run] + a.also], "compared_to": os.path.basename(a.compare) if a.compare else None,
               "moved_over_15pct": flag if a.compare else None}, open(out, "w"), indent=1)
    print("   wrote", out)


if __name__ == "__main__":
    main()
