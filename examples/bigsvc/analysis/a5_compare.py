#!/usr/bin/env python3
"""A5/B5: per-endpoint extrinsic attribution under a stressor, caller view, rule A.

    intrinsic_e = FL_e + W(rho, avgS)         W from the no-stressor profiling run(s)
    ext_e       = Lat_e - intrinsic_e          per endpoint and (step, mix) segment
    ground truth (additive channel) = W_s(rho) - W(rho)   W_s from the stressed sweep at the
                                                           profiled mix
    shape channels (F6): per segment, r_e = ext_e regressed on FL_e -> slope (multiplicative
    in-handler contention) and intercept (additive excess); their ground truth = the same
    regression on the stressed sweep's per-endpoint medians at the same rho.
    guard (F5): segments whose predicted W exceeds the profiled maximum, or whose rho lies
    above the stressed knee, are reported separately; statistics are over usable segments.
    Reported (F7): error distribution (median, IQR, |err| p90) and the ratio distribution
    (median, IQR, % within +-30%) where truth > 0.5 ms; the usable region; the control run.

Usage: a5_compare.py STRESS_RANDOM_RUN --a2 A2_RUN [--a2 A2_RUN2] --truth STRESS_SWEEP_RUN
                     [--control NOSTRESS_RANDOM_RUN] [--settle 5] [--costs costs.json]
                     [--stressed-knee RHO | auto]
"""
import argparse, os, sys
import numpy as np
sys.path.insert(0, os.path.dirname(__file__))
from analyze import load_run
from dictionary import client_curve, client_model, client_segments, fmt_ratio, notation, ratio_stats, shape_test

try:
    import matplotlib; matplotlib.use("Agg"); import matplotlib.pyplot as plt
except Exception:
    plt = None


def truth_channels(P, Ps):
    """Per rho bin of the stressed sweep: additive truth W_s - W, and the shape-test truth
    (slope, intercept) of Lat_s,e - FL_e - W(rho) across endpoints."""
    model, fl, names, viol = P["model"], P["fl"], P["names"], P["viol"]
    out = []
    for b, m in enumerate(Ps["mids"]):
        if np.isnan(Ps["W50"][b]):
            continue
        Wi = model.predict(m, Ps["avgS"])
        resid = {e: Ps["L"][i, b] - fl[e] - Wi for i, e in enumerate(names) if not viol[e] and not np.isnan(Ps["L"][i, b])}
        st = shape_test(resid, fl)
        out.append((m, Ps["W50"][b] - Wi, st[0] if st else np.nan, st[1] if st else np.nan))
    T = np.array(out)
    ok = ~np.isnan(T[:, 2])  # bins with enough endpoints for the shape regression
    return dict(rho=T[:, 0], add=T[:, 1], rho_s=T[ok, 0], slope=T[ok, 2], intercept=T[ok, 3])


def evaluate(R, P, T, knee, settle, label):
    model, fl, viol, k = P["model"], P["fl"], P["viol"], P["k"]
    rows, segs = [], []
    for seg in client_segments(R, P["c_ms"], settle):
        rho, S = seg["rho"], seg["avgS"]
        Wi = model.predict(rho, S)
        usable = model.usable(rho, S, knee)
        if T is not None:
            t_add = float(np.interp(rho, T["rho"], T["add"])); t_sl = float(np.interp(rho, T["rho_s"], T["slope"])); t_ic = float(np.interp(rho, T["rho_s"], T["intercept"]))
        else:
            t_add = t_sl = t_ic = 0.0
        resid = {}
        for e, (obs, n, share) in seg["obs"].items():
            if e not in fl:
                continue
            ext = obs - fl[e] - Wi
            rows.append(dict(u=rho / k, viol=int(viol[e]), share=share, ext=ext, truth=t_add, fl=fl[e], e=e, usable=int(usable)))
            if not viol[e]:
                resid[e] = ext
        st = shape_test(resid, fl)
        if st:
            segs.append(dict(u=rho / k, slope=st[0], intercept=st[1], r2=st[2], t_slope=t_sl, t_intercept=t_ic, t_add=t_add, usable=int(usable)))
    U = [r for r in rows if r["usable"] and not r["viol"]]
    print(f"{label}: {len(rows)} endpoint-segments in {len(segs)} segments, u {min(r['u'] for r in rows):.2f}-{max(r['u'] for r in rows):.2f}; "
          f"guard removed {sum(1 for s in segs if not s['usable'])} segments ({len([r for r in rows if not r['usable']])} endpoint-segments)")
    if not U:
        print("   no usable segments"); return rows, segs
    print(f"   usable region: u <= {max(r['u'] for r in U):.2f}; ground-truth additive extrinsic there: median {np.median([r['truth'] for r in U]):.2f} ms, max {max(r['truth'] for r in U):.2f} ms")
    s = ratio_stats([r["ext"] for r in U], [r["truth"] for r in U])
    print("   additive channel, ext_e vs truth: " + fmt_ratio(s))
    err = np.array([r["ext"] - r["truth"] for r in U])
    X = np.c_[np.ones(len(U)), [r["u"] for r in U]]
    res = err - X @ np.linalg.lstsq(X, err, rcond=None)[0]
    print(f"   corr(own share, err residual after u) = {np.corrcoef([r['share'] for r in U], res)[0, 1]:+.3f}")
    su = [sg for sg in segs if sg["usable"]]
    if su:
        print(f"   shape channels over {len(su)} usable segments (F6):")
        print("      slope (x FL_e):   " + fmt_ratio(ratio_stats([sg["slope"] for sg in su], [sg["t_slope"] for sg in su], floor_ms=0.05)))
        print("      intercept (ms):   " + fmt_ratio(ratio_stats([sg["intercept"] for sg in su], [sg["t_intercept"] for sg in su])))
        print(f"      r2 median={np.median([sg['r2'] for sg in su]):.2f}")
    ex = [r for r in rows if not r["usable"] and not r["viol"]]
    if ex:
        s2 = ratio_stats([r["ext"] for r in ex], [r["truth"] for r in ex])
        print("   (removed segments, for the record: " + fmt_ratio(s2) + ")")
    for e in P["names"]:
        if viol[e]:
            M = [r for r in rows if r["usable"] and r["e"] == e]
            if len(M) > 5:
                print(f"   {e:14s}: ext_e - truth median={np.median([r['ext'] - r['truth'] for r in M]):+.2f} ms (n={len(M)})")
    return rows, segs


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("run"); ap.add_argument("--a2", action="append", required=True); ap.add_argument("--truth", required=True)
    ap.add_argument("--control", default=None); ap.add_argument("--settle", type=float, default=5.0); ap.add_argument("--costs", default=None)
    ap.add_argument("--stressed-knee", default="auto", help="rho (not /k) of the stressed knee, or 'auto' = first rho where the stressed W exceeds the profiled max")
    a = ap.parse_args()
    P = client_model(a.a2, a.settle, a.costs, load_run)
    model, k = P["model"], P["k"]
    Rs = load_run(a.truth)
    Ps = client_curve(Rs, a.settle, P["c_ms"], P["fl"], viol=P["viol"])
    T = truth_channels(P, Ps)
    if a.stressed_knee == "auto":
        above = [m for m, w in zip(Ps["mids"], Ps["W50"]) if not np.isnan(w) and w > model.wmax]
        knee = float(min(above)) if above else None
    else:
        knee = float(a.stressed_knee)
    print(f"dictionary: {model.describe()}")
    print(f"stressed sweep {os.path.basename(a.truth)}: avgS={Ps['avgS']:.2f} ms; stressed knee (guard) rho={knee if knee is None else round(knee, 2)} (u {'-' if knee is None else round(knee / k, 2)}): first rho where W_s > profiled max {model.wmax:.2f} ms")
    print("ground truth per rho (caller view): W unstressed, W_s stressed, additive ext = W_s - W; shape truth slope/intercept of Lat_s,e - FL_e - W across endpoints")
    for m in np.linspace(0.1, 0.7, 7) * k:
        if m > T["rho"].max():
            break
        w0 = model.predict(m, Ps["avgS"]); w1 = float(np.interp(m, Ps["mids"][~np.isnan(Ps["W50"])], Ps["W50"][~np.isnan(Ps["W50"])]))
        print(f"   rho/k={m / k:4.2f}: W={w0:7.2f}  W_s={w1:8.2f}  ext={w1 - w0:8.2f} ms   slope={np.interp(m, T['rho_s'], T['slope']):+.3f}  intercept={np.interp(m, T['rho_s'], T['intercept']):+.2f} ms")
    rows, segs = evaluate(load_run(a.run), P, T, knee, a.settle, "stressed random mixes")
    C = evaluate(load_run(a.control), P, None, knee, a.settle, "control (same schedule, no stressor)") if a.control else None
    if C:
        cu = [r for r in C[0] if r["usable"] and not r["viol"]]
        cs = [s for s in C[1] if s["usable"]]
        if cu:
            print(f"   control false-positive band: ext_e p90 = {np.percentile([r['ext'] for r in cu], 90):.2f} ms; slope p90 = {np.percentile([s['slope'] for s in cs], 90):+.3f}; intercept p90 = {np.percentile([s['intercept'] for s in cs], 90):.2f} ms")
    if plt:
        fig, ax = plt.subplots(1, 3, figsize=(16, 4.8))
        n = [r for r in rows if not r["viol"]]
        us = np.array([r["usable"] for r in n], bool); u = np.array([r["u"] for r in n]); ext = np.array([r["ext"] for r in n])
        ax[0].plot(T["rho"] / k, T["add"], "k-", lw=1.5, label="ground truth W_s - W")
        ax[0].plot(u[us], ext[us], ".", ms=3, alpha=0.35, label="ext_e, usable"); ax[0].plot(u[~us], ext[~us], ".", ms=3, alpha=0.3, color="0.6", label="removed by guard")
        if C:
            c = [r for r in C[0] if not r["viol"] and r["usable"]]
            ax[0].plot([r["u"] for r in c], [r["ext"] for r in c], ".", ms=2, alpha=0.3, color="C2", label="control (no stressor)")
        ax[0].set_yscale("symlog", linthresh=0.5); ax[0].set_xlabel("rho/k"); ax[0].set_ylabel("extrinsic latency (ms)"); ax[0].set_title("additive channel: ext_e = Lat_e - FL_e - W"); ax[0].legend(fontsize=7)
        if knee:
            ax[0].axvline(knee / k, color="r", lw=0.8, ls="--")
        for axi, key, tkey, ttl in ((ax[1], "slope", "t_slope", "shape channel: slope (x FL_e)"), (ax[2], "intercept", "t_intercept", "shape channel: intercept (ms)")):
            axi.plot(T["rho_s"] / k, T["slope" if key == "slope" else "intercept"], "k-", lw=1.5, label="ground truth (stressed sweep)")
            axi.plot([s["u"] for s in segs if s["usable"]], [s[key] for s in segs if s["usable"]], "o", ms=3, alpha=0.6, label="estimate, usable")
            axi.plot([s["u"] for s in segs if not s["usable"]], [s[key] for s in segs if not s["usable"]], "o", ms=3, alpha=0.3, color="0.6", label="removed")
            if C:
                axi.plot([s["u"] for s in C[1] if s["usable"]], [s[key] for s in C[1] if s["usable"]], ".", ms=2, alpha=0.4, color="C2", label="control")
            axi.axhline(0, color="k", lw=0.6); axi.set_xlabel("rho/k"); axi.set_title(ttl); axi.legend(fontsize=7)
            if knee:
                axi.axvline(knee / k, color="r", lw=0.8, ls="--")
        for x in ax:
            x.grid(alpha=.3)
        fig.tight_layout(); notation(fig, f"caller view; run {os.path.basename(a.run)}; truth {os.path.basename(a.truth)}; dictionary {model.describe()}; red line = stressed knee (guard)")
        fig.savefig(os.path.join(a.run, "a5.png"), dpi=130); print("wrote", os.path.join(a.run, "a5.png"))


if __name__ == "__main__":
    main()
