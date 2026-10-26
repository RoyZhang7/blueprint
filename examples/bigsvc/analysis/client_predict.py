#!/usr/bin/env python3
"""A3 from the client's view: predict per-endpoint client-observed p50 in each
(step, mix) segment of a random-composition run from an A2 profiling run.

Two predictors are compared:
  Rule A (FIFO additive):  Lat_e = FL_e + W(rho)          W from A2 (median over normal endpoints)
  Rule B (PS multiplicative): Lat_e = FL_e * g(rho)        g from A2 (median over normal endpoints)

rho for a segment comes from the recorder's arrival counts and the A2 floors.
Usage: client_predict.py A3_RUN --dict A2_RUN [--settle 5]
"""
import argparse, json, os, sys
from collections import defaultdict
import numpy as np
sys.path.insert(0, os.path.dirname(__file__))
from analyze import load_run, cpuset_size, assign, floors, rho_of_window

try:
    import matplotlib; matplotlib.use("Agg"); import matplotlib.pyplot as plt
except Exception:
    plt = None


def client_curves(R, settle, nb=16, fl=None):
    """From a profiling run: floors (server, floor step; or the given fl), and per-rho-bin
    median over normal endpoints of client (p50 - FL) [Rule A] and p50/FL [Rule B]."""
    names = R["run"]["endpoints"]; cfg = dict(zip(names, R["run"]["floors_ms"])); viol = dict(zip(names, R["run"]["violators"]))
    k = cpuset_size(R["meta"].get("cpuset", "0-7"))
    W = R["windows"]; step_of = assign(W, R["steps"], "step"); step_t0 = {s["step"]: s["t"] for s in R["steps"]}
    keep = [i for i, (w, s) in enumerate(zip(W, step_of)) if s is not None and s >= 0 and w["t"] >= step_t0[s] + settle * 1000]
    W = [W[i] for i in keep]; step_of = [step_of[i] for i in keep]
    fs = min(step_of)
    if fl is None:
        fl = {e: m for e, c, m, p90, n in floors([w for w, s in zip(W, step_of) if s == fs], names, cfg)}
    rho_sec = defaultdict(list)
    for w in W:
        rho_sec[w["t"] // 1000].append(rho_of_window(w, fl))
    rho_sec = {s: float(np.mean(v)) for s, v in rho_sec.items()}
    edges = np.linspace(0, k * 1.05, nb + 1); mids = (edges[:-1] + edges[1:]) / 2
    addv = [[] for _ in range(nb)]; mulv = [[] for _ in range(nb)]
    for r in R["reports"]:
        if r.get("step", -9) < 0:
            continue
        rho = rho_sec.get(r["t"] // 1000 - 1)
        if rho is None:
            continue
        b = int(np.clip(np.digitize(rho, edges) - 1, 0, nb - 1))
        for e, v in r["ep"].items():
            if v[0] >= 1 and not viol[e]:
                p = v[1] / 1e6
                addv[b].extend([p - fl[e]] * v[0]); mulv[b].extend([p / fl[e]] * v[0])
    Wc = np.array([np.median(x) if len(x) >= 50 else np.nan for x in addv])
    Gc = np.array([np.median(x) if len(x) >= 50 else np.nan for x in mulv])
    ok = ~np.isnan(Wc)
    return dict(names=names, viol=viol, fl=fl, k=k, mids=mids[ok], W=Wc[ok], G=Gc[ok])


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("run"); ap.add_argument("--dict", required=True); ap.add_argument("--settle", type=float, default=5.0)
    a = ap.parse_args()
    P = client_curves(load_run(a.dict), a.settle)
    names, viol, fl, k = P["names"], P["viol"], P["fl"], P["k"]
    print("A2 client curves (rho/k: W_add ms, g_mul):")
    for m, w, g in zip(P["mids"], P["W"], P["G"]):
        print(f"   {m / k:4.2f}: W={w:7.2f} ms  g={g:6.2f}")
    R = load_run(a.run)
    Wn = R["windows"]; step_of = assign(Wn, R["steps"], "step"); mix_of = assign(Wn, R["mixes"], "mix")
    step_t0 = {s["step"]: s["t"] for s in R["steps"]}; mix_t0 = {m["mix"]: m["t"] for m in R["mixes"]}
    seg_win = defaultdict(list)
    for w, s, m in zip(Wn, step_of, mix_of):
        if s is None or s < 0 or m is None or w["t"] < step_t0[s] + a.settle * 1000 or w["t"] < mix_t0[m] + a.settle * 1000:
            continue
        seg_win[(s, m)].append(w)
    # client reports -> segment by time
    rep_step = assign(R["reports"], R["steps"], "step"); rep_mix = assign(R["reports"], R["mixes"], "mix")
    seg_rep = defaultdict(list)
    for r, s, m in zip(R["reports"], rep_step, rep_mix):
        if s is None or s < 0 or m is None or r["t"] - 1000 < step_t0[s] + a.settle * 1000 or r["t"] - 1000 < mix_t0[m] + a.settle * 1000:
            continue
        seg_rep[(s, m)].append(r)
    rows = []
    for key, ws in seg_win.items():
        reps = seg_rep.get(key)
        if len(ws) < 10 or not reps:
            continue
        rho = float(np.mean([rho_of_window(w, fl) for w in ws]))
        tot = sum(w["a"] for w in ws)
        arr = defaultdict(int)
        for w in ws:
            for e, v in w["ep"].items():
                arr[e] += v[0]
        Wp = float(np.interp(rho, P["mids"], P["W"])); Gp = float(np.interp(rho, P["mids"], P["G"]))
        acc = defaultdict(list)
        for r in reps:
            for e, v in r["ep"].items():
                if v[0] >= 1:
                    acc[e].extend([v[1] / 1e6] * v[0])
        for e, vals in acc.items():
            if len(vals) < 10:
                continue
            obs = float(np.median(vals))
            rows.append(dict(step=key[0], mix=key[1], util=rho / k, e=e, viol=int(viol[e]), share=arr[e] / max(1, tot), fl=fl[e],
                             obs=obs, predA=fl[e] + Wp, predB=fl[e] * Gp, n=len(vals)))
    if not rows:
        sys.exit("no segments")
    A = np.array([[r["util"], r["viol"], r["share"], (r["obs"] - r["predA"]) / r["fl"], (r["obs"] - r["predB"]) / r["fl"],
                   r["obs"] - r["predA"], r["obs"] - r["predB"], r["fl"]] for r in rows])
    nrm = A[A[:, 1] == 0]
    print(f"A3 (client view): {len(set((r['step'], r['mix']) for r in rows))} segments, {len(rows)} endpoint-segments, "
          f"util range {A[:, 0].min():.2f}-{A[:, 0].max():.2f}")
    for name, col, colms in (("Rule A (FIFO add)", 3, 5), ("Rule B (PS mult)", 4, 6)):
        x = nrm[:, col]; y = nrm[:, colms]
        print(f"   {name}: normal endpoints err/FL median={np.median(x):+.3f} IQR=[{np.percentile(x, 25):+.3f},{np.percentile(x, 75):+.3f}] "
              f"|err| p90={np.percentile(np.abs(x), 90):.2f}xFL ; err_ms median={np.median(y):+.2f} |err_ms| p90={np.percentile(np.abs(y), 90):.2f}")
    # share dependence under Rule A
    X = np.c_[np.ones(len(nrm)), nrm[:, 0]]
    resid = nrm[:, 5] - X @ np.linalg.lstsq(X, nrm[:, 5], rcond=None)[0]
    print(f"   Rule A normal: corr(own share, err_ms residual after util) = {np.corrcoef(nrm[:, 2], resid)[0, 1]:+.3f}")
    for e in names:
        if viol[e]:
            M = np.array([[r["util"], r["share"], r["obs"] - r["predA"], (r["obs"] - r["predA"]) / r["fl"]] for r in rows if r["e"] == e])
            if len(M) > 5:
                X = np.c_[np.ones(len(M)), M[:, 0]]
                res = M[:, 2] - X @ np.linalg.lstsq(X, M[:, 2], rcond=None)[0]
                print(f"   Rule A {e:12s}: err_ms median={np.median(M[:, 2]):+.2f} (err/FL {np.median(M[:, 3]):+.2f}); corr(own share, resid)={np.corrcoef(M[:, 1], res)[0, 1]:+.3f} (n={len(M)})")
    with open(os.path.join(a.run, "a3_client_error.csv"), "w") as f:
        f.write("step,mix,util,endpoint,violator,share,fl_ms,obs_ms,predA_ms,predB_ms,n\n")
        for r in rows:
            f.write(f"{r['step']},{r['mix']},{r['util']:.4f},{r['e']},{r['viol']},{r['share']:.5f},{r['fl']:.4f},{r['obs']:.4f},{r['predA']:.4f},{r['predB']:.4f},{r['n']}\n")
    if plt:
        fig, ax = plt.subplots(1, 3, figsize=(16, 4.4))
        ax[0].plot(nrm[:, 0], nrm[:, 5], ".", ms=3, alpha=0.35, label="normal (Rule A)")
        for e in names:
            if viol[e]:
                M = np.array([[r["util"], r["obs"] - r["predA"]] for r in rows if r["e"] == e])
                if len(M): ax[0].plot(M[:, 0], M[:, 1], "x", ms=6, label=e)
        ax[0].axhline(0, color="k", lw=0.8); ax[0].set_xlabel("rho/k"); ax[0].set_ylabel("obs - (FL_e + W(rho))  [ms]"); ax[0].set_title("A3: Rule A error vs rho"); ax[0].legend(fontsize=7)
        ax[1].plot(nrm[:, 2] * 100, nrm[:, 5], ".", ms=3, alpha=0.35, label="normal")
        for e in names:
            if viol[e]:
                M = np.array([[r["share"], r["obs"] - r["predA"]] for r in rows if r["e"] == e])
                if len(M): ax[1].plot(M[:, 0] * 100, M[:, 1], "x", ms=6, label=e)
        ax[1].axhline(0, color="k", lw=0.8); ax[1].set_xlabel("own share of arrivals (%)"); ax[1].set_ylabel("Rule A error [ms]"); ax[1].set_title("A3: error vs own share"); ax[1].legend(fontsize=7)
        ax[2].plot(nrm[:, 0], nrm[:, 6], ".", ms=3, alpha=0.35, color="C3", label="normal (Rule B)")
        ax[2].axhline(0, color="k", lw=0.8); ax[2].set_xlabel("rho/k"); ax[2].set_ylabel("obs - FL_e*g(rho)  [ms]"); ax[2].set_title("A3: Rule B error vs rho"); ax[2].legend(fontsize=7)
        fig.tight_layout(); fig.savefig(os.path.join(a.run, "a3_client.png"), dpi=130); print("wrote", os.path.join(a.run, "a3_client.png"))


if __name__ == "__main__":
    main()
