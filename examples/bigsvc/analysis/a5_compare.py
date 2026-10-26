#!/usr/bin/env python3
"""A5: per-endpoint extrinsic attribution under a stressor, caller view, Rule A.

  intrinsic_e(rho) = FL_e + W(rho)            W from the no-stressor sweep (A2)
  ground truth     = W_s(rho) - W(rho)        W_s from the stressed uniform-mix sweep
  per segment of the stressed random-mix run: ext_e = obs_e - intrinsic_e(rho)
  pass: ext_e tracks ground truth across endpoints and compositions.

Usage: a5_compare.py STRESS_RANDOM_RUN --a2 A2_RUN --truth STRESS_SWEEP_RUN [--control NOSTRESS_RANDOM_RUN]
"""
import argparse, os, sys
from collections import defaultdict
import numpy as np
sys.path.insert(0, os.path.dirname(__file__))
from analyze import load_run, assign, rho_of_window
from client_predict import client_curves

try:
    import matplotlib; matplotlib.use("Agg"); import matplotlib.pyplot as plt
except Exception:
    plt = None


def segments(R, fl, k, settle):
    Wn = R["windows"]; step_of = assign(Wn, R["steps"], "step"); mix_of = assign(Wn, R["mixes"], "mix")
    step_t0 = {s["step"]: s["t"] for s in R["steps"]}; mix_t0 = {m["mix"]: m["t"] for m in R["mixes"]}
    seg_win = defaultdict(list)
    for w, s, m in zip(Wn, step_of, mix_of):
        if s is None or s < 0 or m is None or w["t"] < step_t0[s] + settle * 1000 or w["t"] < mix_t0[m] + settle * 1000:
            continue
        seg_win[(s, m)].append(w)
    rep_step = assign(R["reports"], R["steps"], "step"); rep_mix = assign(R["reports"], R["mixes"], "mix")
    seg_rep = defaultdict(list)
    for r, s, m in zip(R["reports"], rep_step, rep_mix):
        if s is None or s < 0 or m is None or r["t"] - 1000 < step_t0[s] + settle * 1000 or r["t"] - 1000 < mix_t0[m] + settle * 1000:
            continue
        seg_rep[(s, m)].append(r)
    out = []
    for key, ws in seg_win.items():
        reps = seg_rep.get(key)
        if len(ws) < 10 or not reps:
            continue
        rho = float(np.mean([rho_of_window(w, fl) for w in ws]))
        tot = sum(w["a"] for w in ws); arr = defaultdict(int)
        for w in ws:
            for e, v in w["ep"].items():
                arr[e] += v[0]
        acc = defaultdict(list)
        for r in reps:
            for e, v in r["ep"].items():
                if v[0] >= 1:
                    acc[e].extend([v[1] / 1e6] * v[0])
        out.append((key, rho, {e: (float(np.median(v)), len(v), arr[e] / max(1, tot)) for e, v in acc.items() if len(v) >= 10}))
    return out


def evaluate(R, P, Ps, k, settle, label):
    names, viol, fl = P["names"], P["viol"], P["fl"]
    rows = []
    for key, rho, obs in segments(R, fl, k, settle):
        Wi = float(np.interp(rho, P["mids"], P["W"]))
        truth = float(np.interp(rho, Ps["mids"], Ps["W"])) - Wi if Ps is not None else 0.0
        for e, (o, n, share) in obs.items():
            ext = o - (fl[e] + Wi)
            rows.append((rho / k, int(viol[e]), share, ext, truth, fl[e], e))
    A = np.array([r[:6] for r in rows], float)
    nrm = A[A[:, 1] == 0]
    err = nrm[:, 3] - nrm[:, 4]
    print(f"{label}: {len(rows)} endpoint-segments, util {A[:, 0].min():.2f}-{A[:, 0].max():.2f}; ground-truth extrinsic median={np.median(nrm[:, 4]):.2f} ms (max {nrm[:, 4].max():.2f})")
    print(f"   normal endpoints: ext_e - truth median={np.median(err):+.2f} ms, IQR=[{np.percentile(err, 25):+.2f},{np.percentile(err, 75):+.2f}], |err| p90={np.percentile(np.abs(err), 90):.2f} ms")
    if np.any(nrm[:, 4] > 0.5):
        m = nrm[:, 4] > 0.5
        rel = nrm[m, 3] / nrm[m, 4]
        print(f"   where truth > 0.5 ms: ext_e/truth median={np.median(rel):.2f}, IQR=[{np.percentile(rel, 25):.2f},{np.percentile(rel, 75):.2f}]; within +-30%: {np.mean(np.abs(rel - 1) < 0.3) * 100:.0f}%")
    X = np.c_[np.ones(len(nrm)), nrm[:, 0]]
    res = err - X @ np.linalg.lstsq(X, err, rcond=None)[0]
    print(f"   corr(own share, err residual after util) = {np.corrcoef(nrm[:, 2], res)[0, 1]:+.3f}")
    for e in names:
        if viol[e]:
            M = np.array([r[:6] for r in rows if r[6] == e], float)
            if len(M) > 5:
                print(f"   {e:12s}: ext_e - truth median={np.median(M[:, 3] - M[:, 4]):+.2f} ms (n={len(M)})")
    return A


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("run"); ap.add_argument("--a2", required=True); ap.add_argument("--truth", required=True)
    ap.add_argument("--control", default=None); ap.add_argument("--settle", type=float, default=5.0)
    a = ap.parse_args()
    P = client_curves(load_run(a.a2), a.settle)
    k = P["k"]
    Ps = client_curves(load_run(a.truth), a.settle, fl=P["fl"])
    print("W(rho) no-stress vs stressed (ground truth = difference):")
    for m in np.linspace(0.1, 0.6, 6) * k:
        w0 = float(np.interp(m, P["mids"], P["W"])); w1 = float(np.interp(m, Ps["mids"], Ps["W"]))
        print(f"   rho/k={m / k:4.2f}: W={w0:6.2f}  W_s={w1:6.2f}  ext={w1 - w0:6.2f} ms")
    A = evaluate(load_run(a.run), P, Ps, k, a.settle, "A5 stressed random mixes")
    C = evaluate(load_run(a.control), P, None, k, a.settle, "A5 control (no stressor)") if a.control else None
    if plt:
        fig, ax = plt.subplots(1, 2, figsize=(11, 4.4))
        n = A[A[:, 1] == 0]
        ax[0].plot(n[:, 0], n[:, 4], "k.", ms=3, label="ground truth (sweep)")
        ax[0].plot(n[:, 0], n[:, 3], ".", ms=3, alpha=0.4, label="ext_e per endpoint-segment")
        ax[0].set_xlabel("rho/k"); ax[0].set_ylabel("extrinsic latency (ms)"); ax[0].set_title("A5: attribution vs truth"); ax[0].legend(fontsize=7)
        ax[1].plot(n[:, 2] * 100, n[:, 3] - n[:, 4], ".", ms=3, alpha=0.4, label="stressed")
        if C is not None:
            c = C[C[:, 1] == 0]; ax[1].plot(c[:, 2] * 100, c[:, 3], ".", ms=3, alpha=0.4, color="C2", label="control (no stressor)")
        ax[1].axhline(0, color="k", lw=0.8); ax[1].set_xlabel("own share (%)"); ax[1].set_ylabel("ext_e - truth (ms)"); ax[1].set_title("A5: error vs own share"); ax[1].legend(fontsize=7)
        fig.tight_layout(); fig.savefig(os.path.join(a.run, "a5.png"), dpi=130); print("wrote", os.path.join(a.run, "a5.png"))


if __name__ == "__main__":
    main()
