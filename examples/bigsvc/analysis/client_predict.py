#!/usr/bin/env python3
"""Prediction from the caller's view: per-endpoint client p50 in each (step, mix)
segment of a random-composition run from one or more profiling runs (rule A):

    D_e[rho, avgS] = FL_e + W(rho, avgS)      W from the profiling run(s); with two or more
                                              --dict at different mixes, W carries the avgS input
    guard (F5): a segment is usable only if the predicted W is within the profiled range
    shape test (F6): per segment, r_e = Lat_e - D_e regressed on FL_e -> slope, intercept

Usage: client_predict.py RUN --dict A2_RUN [--dict A2_RUN2 ...] [--settle 5] [--costs costs.json]
                         [--stressed-knee RHO] [--no-avgS]
"""
import argparse, os, sys
import numpy as np
sys.path.insert(0, os.path.dirname(__file__))
from analyze import load_run
from dictionary import client_model, client_segments, notation, shape_test

try:
    import matplotlib; matplotlib.use("Agg"); import matplotlib.pyplot as plt
except Exception:
    plt = None


def predict_rows(R, P, settle, stressed_knee=None, use_avgS=True):
    model, fl, viol, k = P["model"], P["fl"], P["viol"], P["k"]
    rows, shapes = [], []
    for seg in client_segments(R, P["c_ms"], settle):
        rho, S = seg["rho"], seg["avgS"]
        Wp = model.predict(rho, S if use_avgS else None)
        usable = model.usable(rho, S if use_avgS else None, stressed_knee)
        resid = {}
        for e, (obs, n, share) in seg["obs"].items():
            if e not in fl:
                continue
            rows.append(dict(step=seg["key"][0], mix=seg["key"][1], rho=rho, util=rho / k, avgS=S, e=e, viol=int(viol[e]), share=share,
                             fl=fl[e], W=Wp, obs=obs, err=obs - fl[e] - Wp, n=n, usable=int(usable)))
            if not viol[e]:
                resid[e] = obs - fl[e] - Wp
        st = shape_test(resid, fl)
        if st:
            shapes.append(dict(step=seg["key"][0], mix=seg["key"][1], rho=rho, util=rho / k, avgS=S, slope=st[0], intercept=st[1], r2=st[2], n=st[3], usable=int(usable)))
    return rows, shapes


def report(rows, shapes, model, stressed_knee, names, viol, label="caller view"):
    nseg = len(set((r["step"], r["mix"]) for r in rows)); nu = len(set((r["step"], r["mix"]) for r in rows if r["usable"]))
    print(f"{label}: W model = {model.describe()}")
    print(f"guard (F5): {nseg - nu} of {nseg} segments removed (predicted W above the profiled max {model.wmax:.2f} ms"
          f"{f' or rho above the stressed knee {stressed_knee:.2f}' if stressed_knee else ''}); statistics over the {nu} usable segments")
    nrm = [r for r in rows if r["usable"] and not r["viol"]]
    if not nrm:
        print("   no usable segments"); return
    err = np.array([r["err"] for r in nrm]); efl = err / np.array([r["fl"] for r in nrm])
    print(f"   {len(nrm)} normal endpoint-segments, u {min(r['util'] for r in nrm):.2f}-{max(r['util'] for r in nrm):.2f}, avgS {min(r['avgS'] for r in nrm):.2f}-{max(r['avgS'] for r in nrm):.2f} ms")
    print(f"   Lat_e - D_e: median={np.median(err):+.3f} ms IQR=[{np.percentile(err, 25):+.3f},{np.percentile(err, 75):+.3f}] |err| p90={np.percentile(np.abs(err), 90):.2f} ms; /FL_e median={np.median(efl):+.3f} |.| p90={np.percentile(np.abs(efl), 90):.2f}")
    ex = [r for r in rows if not r["usable"] and not r["viol"]]
    if ex:
        e2 = np.array([r["err"] for r in ex])
        print(f"   (removed: {len(ex)} endpoint-segments, err median={np.median(e2):+.2f} ms, |err| p90={np.percentile(np.abs(e2), 90):.2f} ms)")
    X = np.c_[np.ones(len(nrm)), [r["util"] for r in nrm]]
    res = err - X @ np.linalg.lstsq(X, err, rcond=None)[0]
    print(f"   corr(own share, err residual after u) = {np.corrcoef([r['share'] for r in nrm], res)[0, 1]:+.3f}")
    for e in names:
        if viol[e]:
            M = [r for r in rows if r["usable"] and r["e"] == e]
            if len(M) > 5:
                y = np.array([r["err"] for r in M]); X = np.c_[np.ones(len(M)), [r["util"] for r in M]]
                res = y - X @ np.linalg.lstsq(X, y, rcond=None)[0]
                print(f"   {e:14s}: err median={np.median(y):+.2f} ms ({np.median(y / np.array([r['fl'] for r in M])):+.2f} x FL); corr(own share, resid)={np.corrcoef([r['share'] for r in M], res)[0, 1]:+.3f} (n={len(M)})")
    sh = [s for s in shapes if s["usable"]]
    if sh:
        sl = np.array([s["slope"] for s in sh]); ic = np.array([s["intercept"] for s in sh])
        print(f"shape test (F6), {len(sh)} usable segments: slope median={np.median(sl):+.3f} IQR=[{np.percentile(sl, 25):+.3f},{np.percentile(sl, 75):+.3f}]; "
              f"intercept median={np.median(ic):+.2f} ms IQR=[{np.percentile(ic, 25):+.2f},{np.percentile(ic, 75):+.2f}]; r2 median={np.median([s['r2'] for s in sh]):.2f}")


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("run"); ap.add_argument("--dict", action="append", required=True)
    ap.add_argument("--settle", type=float, default=5.0); ap.add_argument("--costs", default=None)
    ap.add_argument("--stressed-knee", type=float, default=None); ap.add_argument("--no-avgS", action="store_true")
    a = ap.parse_args()
    P = client_model(a.dict, a.settle, a.costs, load_run)
    names, viol, k, model = P["names"], P["viol"], P["k"], P["model"]
    print("profiled W (caller view):")
    for c in model.curves:
        print(f"   {c[6]}: avgS={c[0]:.2f} ms  " + "  ".join(f"u{m / k:.2f}:{w:.2f}" for m, w in zip(c[1], c[2])))
    R = load_run(a.run)
    rows, shapes = predict_rows(R, P, a.settle, a.stressed_knee, use_avgS=not a.no_avgS)
    if not rows:
        sys.exit("no segments")
    report(rows, shapes, model, a.stressed_knee, names, viol)
    with open(os.path.join(a.run, "a3_client_error.csv"), "w") as f:
        f.write("step,mix,rho,util,avgS_ms,endpoint,violator,share,fl_ms,W_ms,obs_ms,err_ms,n,usable\n")
        for r in rows:
            f.write(f"{r['step']},{r['mix']},{r['rho']:.4f},{r['util']:.4f},{r['avgS']:.4f},{r['e']},{r['viol']},{r['share']:.5f},{r['fl']:.4f},{r['W']:.4f},{r['obs']:.4f},{r['err']:.4f},{r['n']},{r['usable']}\n")
    with open(os.path.join(a.run, "a3_client_shape.csv"), "w") as f:
        f.write("step,mix,rho,util,avgS_ms,slope,intercept_ms,r2,n_endpoints,usable\n")
        for s in shapes:
            f.write(f"{s['step']},{s['mix']},{s['rho']:.4f},{s['util']:.4f},{s['avgS']:.4f},{s['slope']:.5f},{s['intercept']:.4f},{s['r2']:.3f},{s['n']},{s['usable']}\n")
    if plt:
        nrm = [r for r in rows if not r["viol"]]
        fig, ax = plt.subplots(1, 3, figsize=(16, 4.8))
        u = np.array([r["util"] for r in nrm]); err = np.array([r["err"] for r in nrm]); us = np.array([r["usable"] for r in nrm], bool)
        ax[0].plot(u[us], err[us], ".", ms=3, alpha=0.35, label="usable"); ax[0].plot(u[~us], err[~us], ".", ms=3, alpha=0.3, color="0.6", label="removed by W guard")
        for e in names:
            if viol[e]:
                M = [(r["util"], r["err"]) for r in rows if r["e"] == e and r["usable"]]
                if M:
                    ax[0].plot(*zip(*M), "x", ms=6, label=e)
        ax[0].axhline(0, color="k", lw=0.8); ax[0].set_xlabel("rho/k"); ax[0].set_ylabel("Lat_e - D_e[rho, avgS] (ms)"); ax[0].set_title("rule A error vs rho"); ax[0].legend(fontsize=7)
        ax[1].plot(np.array([r["share"] for r in nrm])[us] * 100, err[us], ".", ms=3, alpha=0.35)
        ax[1].axhline(0, color="k", lw=0.8); ax[1].set_xlabel("own share %_e (%)"); ax[1].set_ylabel("Lat_e - D_e (ms)"); ax[1].set_title("error vs own share")
        sh = [s for s in shapes if s["usable"]]
        if sh:
            ax[2].plot([s["util"] for s in sh], [s["slope"] for s in sh], "o", ms=3, label="slope (x FL_e)")
            ax[2].plot([s["util"] for s in sh], [s["intercept"] for s in sh], "s", ms=3, label="intercept (ms)")
            ax[2].axhline(0, color="k", lw=0.8); ax[2].set_xlabel("rho/k"); ax[2].set_title("shape test: r_e = intercept + slope*FL_e"); ax[2].legend(fontsize=7)
        for x in ax:
            x.grid(alpha=.3)
        fig.tight_layout(); notation(fig, f"caller view, run {os.path.basename(a.run)}; dictionary: {model.describe()}")
        fig.savefig(os.path.join(a.run, "a3_client.png"), dpi=130); print("wrote", os.path.join(a.run, "a3_client.png"))


if __name__ == "__main__":
    main()
