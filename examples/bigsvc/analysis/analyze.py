#!/usr/bin/env python3
"""Offline analysis for the BigSvc per-endpoint dictionary experiments (Phase A).

Usage:
  analyze.py RUN_DIR [--floor-step N] [--dict A2_RUN_DIR] [--out DIR] [--settle S]

Inputs (written by scripts/exp.sh): RUN_DIR/gordion.jsonl (recorder windows,
with raw samples when GORDION_RAW=1), RUN_DIR/wlgen.jsonl (steps, mixes,
1 s client reports), RUN_DIR/meta.json.

Without --dict (profiling run, e.g. A2):
  * FL_e      : per-endpoint floor from the floor step (lowest rate) -> fl.csv, fl_check.png
  * rho       : per window, sum_e a_e*FL_e / W   (busy-seconds per second)
  * D_e[rho]  : per endpoint, per rho-bin p50/p90 (pooled raw samples) -> dict.csv
  * g_e(rho)  : D_e/FL_e; collapse test over normal endpoints -> collapse.csv, collapse.png
  * service-level curve vs lambda and vs rho -> svc_curve.png

With --dict (prediction run, e.g. A3): builds D_e[rho] from the A2 run and
predicts each mix segment of RUN_DIR; reports per-endpoint error and its
dependence on the endpoint's own share -> a3_error.csv, a3_error.png
"""
import argparse
import json
import math
import os
import sys
from collections import defaultdict

import numpy as np

try:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
except Exception:  # pragma: no cover
    plt = None

EP_A, EP_N, EP_P50, EP_P90, EP_P99, EP_MEAN, EP_MAX, EP_PP50, EP_PP90, EP_Q50, EP_Q90 = range(11)


# ----------------------------------------------------------------------------- loading
def load_run(d):
    meta = json.load(open(os.path.join(d, "meta.json"))) if os.path.exists(os.path.join(d, "meta.json")) else {}
    windows, start = [], None
    for line in open(os.path.join(d, "gordion.jsonl")):
        o = json.loads(line)
        if o.get("event") == "start":
            start = o
            continue
        if o.get("a", 0) == 0 and o.get("n", 0) == 0:
            continue
        windows.append(o)
    run, steps, mixes, reports = None, [], [], []
    for line in open(os.path.join(d, "wlgen.jsonl")):
        o = json.loads(line)
        ev = o.get("event")
        if ev == "run":
            run = o
        elif ev == "step":
            steps.append(o)
        elif ev == "mix":
            mixes.append(o)
        elif ev == "end":
            pass
        else:
            reports.append(o)
    if run is None:
        sys.exit("no 'run' event in wlgen.jsonl")
    return dict(meta=meta, start=start, windows=windows, run=run, steps=steps, mixes=mixes, reports=reports)


def cpuset_size(cs):
    n = 0
    for part in str(cs).split(","):
        if "-" in part:
            a, b = part.split("-")
            n += int(b) - int(a) + 1
        elif part.strip():
            n += 1
    return n or 1


def assign(windows, events, key):
    """Label each window with the event (step/mix) active at its time; -inf before the first."""
    ts = np.array([e["t"] for e in events])
    ids = [e[key] for e in events]
    out = []
    for w in windows:
        i = np.searchsorted(ts, w["t"], side="right") - 1
        out.append(ids[i] if i >= 0 else None)
    return out


def window_len_s(w):
    return w.get("w", 100) / 1000.0


# ----------------------------------------------------------------------------- floors
def pooled_samples(windows, names, key="raw"):
    acc = defaultdict(list)
    for w in windows:
        raw = w.get(key)
        if raw:
            for e, vals in raw.items():
                acc[e].extend(vals)
        else:  # fallback: per-window p50 repeated by count (coarse)
            for e, v in w["ep"].items():
                if v[EP_N] > 0:
                    acc[e].extend([v[EP_P50]] * v[EP_N])
    return {e: np.asarray(acc[e], dtype=float) for e in names if e in acc}


def floors(windows, names, cfg_floors_ms):
    samp = pooled_samples(windows, names)
    rows = []
    for e in names:
        x = samp.get(e)
        if x is None or len(x) == 0:
            rows.append((e, cfg_floors_ms[e], np.nan, np.nan, 0))
            continue
        rows.append((e, cfg_floors_ms[e], np.median(x) / 1e6, np.percentile(x, 90) / 1e6, len(x)))
    return rows


# ----------------------------------------------------------------------------- rho
def rho_of_window(w, fl_ms):
    """busy-seconds per second offered in this window: sum_e arrivals_e * FL_e / W."""
    s = 0.0
    for e, v in w["ep"].items():
        s += v[EP_A] * fl_ms.get(e, 0.0) / 1000.0
    return s / window_len_s(w)


def share_of_window(w):
    tot = max(1, w["a"])
    return {e: v[EP_A] / tot for e, v in w["ep"].items()}


# ----------------------------------------------------------------------------- dictionary
def build_dict(windows, names, fl_ms, k, nbins=24, min_n=20):
    """D_e[rho]: per endpoint, per rho bin -> (rho_mid, p50_ms, p90_ms, n). Bins span [0, k]."""
    edges = np.linspace(0, k * 1.05, nbins + 1)
    rho = np.array([rho_of_window(w, fl_ms) for w in windows])
    bins = np.clip(np.digitize(rho, edges) - 1, 0, nbins - 1)
    per = {e: defaultdict(list) for e in names}
    for w, b in zip(windows, bins):
        raw = w.get("raw") or {}
        for e, vals in raw.items():
            per[e][b].extend(vals)
    D = {}
    for e in names:
        rows = []
        for b in range(nbins):
            x = per[e].get(b)
            if x and len(x) >= min_n:
                x = np.asarray(x, float)
                rows.append(((edges[b] + edges[b + 1]) / 2, np.median(x) / 1e6, np.percentile(x, 90) / 1e6, len(x)))
        D[e] = np.array(rows) if rows else np.zeros((0, 4))
    return D, rho, edges


def lookup(D_e, rho):
    """Linear interpolation of p50 (ms) at rho; clamps to the profiled range. None if empty."""
    if D_e.shape[0] == 0:
        return None
    xs, ys = D_e[:, 0], D_e[:, 1]
    return float(np.interp(rho, xs, ys))


# ----------------------------------------------------------------------------- main analyses
def analyze_profile(R, out, floor_step, settle_s):
    names = R["run"]["endpoints"]
    cfg = dict(zip(names, R["run"]["floors_ms"]))
    viol = dict(zip(names, R["run"]["violators"]))
    k = cpuset_size(R["meta"].get("cpuset", "0-7"))
    W = R["windows"]
    step_of = assign(W, R["steps"], "step")
    # drop warm-up (-1) windows, unassigned windows, and the first settle_s of each step
    step_t0 = {s["step"]: s["t"] for s in R["steps"]}
    keep = [i for i, (w, s) in enumerate(zip(W, step_of))
            if s is not None and s >= 0 and w["t"] >= step_t0[s] + settle_s * 1000]
    W = [W[i] for i in keep]
    step_of = [step_of[i] for i in keep]
    steps_present = sorted(set(step_of))
    rate_of_step = {s["step"]: s["rate"] for s in R["steps"]}
    print(f"windows kept: {len(W)}  steps: {steps_present}  k={k}")
    # A1 consistency: per-endpoint streams must sum to the service-level stream
    bad = sum(1 for w in W if sum(v[EP_A] for v in w["ep"].values()) != w["a"]
              or sum(v[EP_N] for v in w["ep"].values()) != w["n"])
    drops = sum(w.get("drop", 0) for w in W)
    print(f"A1 check: {bad} of {len(W)} windows where sum_e(a_e,n_e) != (a,n); dropped completions: {drops}")

    # clock offset sanity: first window with arrivals vs first step start
    first_arr = min(w["t"] for w in R["windows"])
    print(f"clock check: first busy window {first_arr - R['steps'][0]['t']:+d} ms after first step event")

    # --- floors from the floor step
    fs = floor_step if floor_step is not None else steps_present[0]
    fl_rows = floors([w for w, s in zip(W, step_of) if s == fs], names, cfg)
    fl_ms = {e: (m if not math.isnan(m) else c) for e, c, m, p90, n in fl_rows}
    with open(os.path.join(out, "fl.csv"), "w") as f:
        f.write("endpoint,violator,configured_ms,measured_p50_ms,measured_p90_ms,n,ratio\n")
        for e, c, m, p90, n in fl_rows:
            f.write(f"{e},{int(viol[e])},{c:.4f},{m:.4f},{p90:.4f},{n},{m / c if c else float('nan'):.4f}\n")
    normal = [r for r in fl_rows if not viol[r[0]] and r[4] > 0]
    ratios = np.array([r[2] / r[1] for r in normal])
    print(f"A2 floors (step {fs}, rate {rate_of_step[fs]}): {len(normal)} normal endpoints, "
          f"median n={np.median([r[4] for r in normal]):.0f}, ratio measured/configured "
          f"median={np.median(ratios):.3f} min={ratios.min():.3f} max={ratios.max():.3f}; "
          f"within 5%: {np.mean(np.abs(ratios - 1) < 0.05) * 100:.0f}%")
    for e, c, m, p90, n in fl_rows:
        if viol[e]:
            print(f"   violator {e}: configured {c:.2f} ms, measured p50 {m:.2f} ms (n={n})")

    # --- rho per window, dictionary, collapse
    D, rho, edges = build_dict(W, names, fl_ms, k)
    with open(os.path.join(out, "dict.csv"), "w") as f:
        f.write("endpoint,rho_mid,p50_ms,p90_ms,n\n")
        for e in names:
            for r in D[e]:
                f.write(f"{e},{r[0]:.3f},{r[1]:.4f},{r[2]:.4f},{int(r[3])}\n")

    # g_e(rho) = p50_e(bin)/FL_e; collapse statistic per bin across normal endpoints
    nb = len(edges) - 1
    G = np.full((len(names), nb), np.nan)
    mids = (edges[:-1] + edges[1:]) / 2
    for i, e in enumerate(names):
        for r in D[e]:
            b = int(np.argmin(np.abs(mids - r[0])))
            G[i, b] = r[1] / fl_ms[e]
    normal_idx = [i for i, e in enumerate(names) if not viol[e]]
    gmed = np.nanmedian(G[normal_idx, :], axis=0)
    giqr = np.nanpercentile(G[normal_idx, :], 75, axis=0) - np.nanpercentile(G[normal_idx, :], 25, axis=0)
    dev = np.nanmedian((G - gmed[None, :]) / gmed[None, :], axis=1)  # per endpoint relative deviation
    with open(os.path.join(out, "collapse.csv"), "w") as f:
        f.write("endpoint,violator,fl_ms,rel_dev_median,rel_dev_max,bins\n")
        for i, e in enumerate(names):
            rel = (G[i] - gmed) / gmed
            f.write(f"{e},{int(viol[e])},{fl_ms[e]:.4f},{dev[i]:.4f},{np.nanmax(np.abs(rel)) if np.any(~np.isnan(rel)) else float('nan'):.4f},{int(np.sum(~np.isnan(G[i])))}\n")
    order = np.argsort(-np.nan_to_num(np.abs(dev), nan=-1))
    print("collapse: per-rho-bin IQR of g_e over normal endpoints (rho_mid: median g, IQR):")
    for b in range(nb):
        if not np.isnan(gmed[b]):
            print(f"   rho={mids[b]:5.2f} (u={mids[b] / k:4.2f})  g_med={gmed[b]:6.2f}  IQR={giqr[b]:5.2f}  "
                  f"n_ep={int(np.sum(~np.isnan(G[normal_idx, b])))}")
    print("collapse: endpoints ranked by |median relative deviation of g_e from the normal-endpoint median|:")
    for i in order[:8]:
        print(f"   {names[i]:12s} viol={int(viol[names[i]])} dev={dev[i]:+.3f}  FL={fl_ms[names[i]]:.2f} ms")
    top3 = set(names[i] for i in order[:3])
    planted = set(e for e in names if viol[e])
    print(f"collapse verdict: top-3 deviators = {sorted(top3)}; planted = {sorted(planted)}; "
          f"{'PASS' if top3 == planted else 'CHECK'}")

    # --- service-level curves vs lambda (per step) and vs rho
    svc = []
    for s in steps_present:
        ws = [w for w, st in zip(W, step_of) if st == s]
        allraw = np.concatenate([np.asarray(sum((w.get("raw") or {}).values(), []), float) for w in ws]) if ws else np.array([])
        lam = sum(w["a"] for w in ws) / sum(window_len_s(w) for w in ws)
        rh = np.mean([rho_of_window(w, fl_ms) for w in ws])
        svc.append((s, rate_of_step[s], lam, rh, np.median(allraw) / 1e6 if len(allraw) else np.nan,
                    np.percentile(allraw, 90) / 1e6 if len(allraw) else np.nan, len(allraw)))
    with open(os.path.join(out, "svc_curve.csv"), "w") as f:
        f.write("step,rate_target,lambda,rho,p50_ms,p90_ms,n\n")
        for r in svc:
            f.write(",".join(f"{v:.4f}" if isinstance(v, float) else str(v) for v in r) + "\n")
    print("service-level: step rate lambda rho/k p50 p90")
    for r in svc:
        print(f"   {r[0]:3d} {r[1]:7.0f} {r[2]:8.1f} {r[3] / k:5.2f} {r[4]:8.3f} {r[5]:8.3f}")

    if plt:
        plot_profile(out, names, viol, fl_rows, G, mids, k, gmed, svc)
    return dict(fl_ms=fl_ms, D=D, k=k, names=names, viol=viol)


def plot_profile(out, names, viol, fl_rows, G, mids, k, gmed, svc):
    fig, ax = plt.subplots(1, 3, figsize=(16, 4.6))
    c = [r[1] for r in fl_rows if not viol[r[0]]]
    m = [r[2] for r in fl_rows if not viol[r[0]]]
    ax[0].loglog(c, m, ".", label="normal")
    for r in fl_rows:
        if viol[r[0]]:
            ax[0].loglog([r[1]], [r[2]], "x", ms=9, label=r[0])
    lo, hi = min(c) * 0.8, max(c) * 1.25
    ax[0].plot([lo, hi], [lo, hi], "k--", lw=0.8)
    ax[0].fill_between([lo, hi], [lo * 0.95, hi * 0.95], [lo * 1.05, hi * 1.05], color="k", alpha=0.08)
    ax[0].set_xlabel("configured FL_e (ms)"); ax[0].set_ylabel("measured p50 at floor step (ms)")
    ax[0].set_title("A2: floors"); ax[0].legend(fontsize=7)
    for i, e in enumerate(names):
        ok = ~np.isnan(G[i])
        if not ok.any():
            continue
        if viol[e]:
            ax[1].plot(mids[ok] / k, G[i, ok], "-o", ms=3, lw=1.5, label=e)
        else:
            ax[1].plot(mids[ok] / k, G[i, ok], "-", color="0.6", lw=0.6, alpha=0.6)
    ok = ~np.isnan(gmed)
    ax[1].plot(mids[ok] / k, gmed[ok], "k-", lw=2, label="median (normal)")
    ax[1].set_xlabel("utilisation rho/k"); ax[1].set_ylabel("g_e = p50_e(rho)/FL_e"); ax[1].set_yscale("log")
    ax[1].set_title("A2: collapse of per-endpoint slowdown"); ax[1].legend(fontsize=7)
    s = np.array([(r[2], r[3] / k, r[4], r[5]) for r in svc])
    ax[2].plot(s[:, 1], s[:, 2], "o-", label="p50"); ax[2].plot(s[:, 1], s[:, 3], "s-", label="p90")
    ax[2].set_xlabel("utilisation rho/k"); ax[2].set_ylabel("service-level latency (ms)"); ax[2].set_yscale("log")
    ax[2].set_title("service-level curve"); ax[2].legend()
    fig.tight_layout(); fig.savefig(os.path.join(out, "a2_summary.png"), dpi=130)
    print("wrote", os.path.join(out, "a2_summary.png"))


def analyze_predict(R, prof, out, settle_s):
    """A3: predict per-endpoint p50 in each mix segment of R from prof['D'] via rho."""
    names, viol, fl_ms, D, k = prof["names"], prof["viol"], prof["fl_ms"], prof["D"], prof["k"]
    W = R["windows"]
    step_of = assign(W, R["steps"], "step")
    mix_of = assign(W, R["mixes"], "mix")
    step_t0 = {s["step"]: s["t"] for s in R["steps"]}
    mix_t0 = {m["mix"]: m["t"] for m in R["mixes"]}
    keep = [i for i, (w, s, m) in enumerate(zip(W, step_of, mix_of))
            if s is not None and s >= 0 and m is not None
            and w["t"] >= step_t0[s] + settle_s * 1000 and w["t"] >= mix_t0[m] + settle_s * 1000]
    # segment = (step, mix)
    seg = defaultdict(list)
    for i in keep:
        seg[(step_of[i], mix_of[i])].append(W[i])
    rows = []
    for (s, m), ws in sorted(seg.items()):
        if len(ws) < 10:
            continue
        rho = float(np.mean([rho_of_window(w, fl_ms) for w in ws]))
        tot_a = sum(w["a"] for w in ws)
        raw = defaultdict(list); arr = defaultdict(int)
        for w in ws:
            for e, vals in (w.get("raw") or {}).items():
                raw[e].extend(vals)
            for e, v in w["ep"].items():
                arr[e] += v[EP_A]
        for e in names:
            x = raw.get(e)
            if not x or len(x) < 10:
                continue
            pred = lookup(D[e], rho)
            if pred is None:
                continue
            obs = float(np.median(x)) / 1e6
            rows.append((s, m, rho, rho / k, e, int(viol[e]), arr[e] / max(1, tot_a), fl_ms[e], pred, obs,
                         obs - pred, (obs - pred) / fl_ms[e], len(x)))
    with open(os.path.join(out, "a3_error.csv"), "w") as f:
        f.write("step,mix,rho,util,endpoint,violator,share,fl_ms,pred_p50_ms,obs_p50_ms,err_ms,err_over_fl,n\n")
        for r in rows:
            f.write(",".join(f"{v:.5f}" if isinstance(v, float) else str(v) for v in r) + "\n")
    A = np.array([(r[3], r[5], r[6], r[11], r[7], r[10]) for r in rows])  # util, viol, share, err/FL, FL, err_ms
    nrm, vio = A[A[:, 1] == 0], A[A[:, 1] == 1]
    print(f"A3: {len(set((r[0], r[1]) for r in rows))} (step,mix) segments, {len(rows)} endpoint-segments")
    print(f"   normal endpoints: err/FL median={np.median(nrm[:, 3]):+.3f}  IQR=[{np.percentile(nrm[:, 3], 25):+.3f},{np.percentile(nrm[:, 3], 75):+.3f}]"
          f"  |err| p90={np.percentile(np.abs(nrm[:, 3]), 90):.3f} x FL;  err_ms median={np.median(nrm[:, 5]):+.3f}")
    # does error grow with own share (controlling for rho)? partial correlation via residuals of a util-only fit
    def share_dependence(M, label):
        if len(M) < 8:
            return
        X = np.c_[np.ones(len(M)), M[:, 0]]
        beta = np.linalg.lstsq(X, M[:, 3], rcond=None)[0]
        resid = M[:, 3] - X @ beta
        c = np.corrcoef(M[:, 2], resid)[0, 1]
        print(f"   {label}: corr(own share, err/FL residual after rho) = {c:+.3f}  (n={len(M)})")
    share_dependence(nrm, "normal   ")
    for e in names:
        if viol[e]:
            M = np.array([(r[3], r[5], r[6], r[11], r[7], r[10]) for r in rows if r[4] == e])
            if len(M):
                print(f"   {e:12s}: err/FL median={np.median(M[:, 3]):+.3f}  ", end="")
                share_dependence(M, "")
    if plt and len(rows):
        fig, ax = plt.subplots(1, 2, figsize=(11, 4.4))
        ax[0].plot(nrm[:, 0], nrm[:, 3], ".", ms=3, alpha=0.4, label="normal")
        for e in names:
            if viol[e]:
                M = np.array([(r[3], r[11]) for r in rows if r[4] == e])
                if len(M):
                    ax[0].plot(M[:, 0], M[:, 1], "x", ms=6, label=e)
        ax[0].axhline(0, color="k", lw=0.8); ax[0].set_xlabel("utilisation rho/k"); ax[0].set_ylabel("(obs - D_e[rho]) / FL_e")
        ax[0].set_title("A3: prediction error vs rho"); ax[0].legend(fontsize=7)
        ax[1].plot(nrm[:, 2] * 100, nrm[:, 3], ".", ms=3, alpha=0.4, label="normal")
        for e in names:
            if viol[e]:
                M = np.array([(r[6], r[11]) for r in rows if r[4] == e])
                if len(M):
                    ax[1].plot(M[:, 0] * 100, M[:, 1], "x", ms=6, label=e)
        ax[1].axhline(0, color="k", lw=0.8); ax[1].set_xlabel("own share of arrivals (%)"); ax[1].set_ylabel("(obs - D_e[rho]) / FL_e")
        ax[1].set_title("A3: error vs own share"); ax[1].legend(fontsize=7)
        fig.tight_layout(); fig.savefig(os.path.join(out, "a3_error.png"), dpi=130)
        print("wrote", os.path.join(out, "a3_error.png"))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("run")
    ap.add_argument("--floor-step", type=int, default=None)
    ap.add_argument("--dict", default=None, help="A2 run dir to build the dictionary from (A3 mode)")
    ap.add_argument("--out", default=None)
    ap.add_argument("--settle", type=float, default=5.0, help="seconds dropped after each step/mix change")
    a = ap.parse_args()
    out = a.out or a.run
    os.makedirs(out, exist_ok=True)
    if a.dict:
        prof = analyze_profile(load_run(a.dict), a.dict, a.floor_step, a.settle)
        analyze_predict(load_run(a.run), prof, out, a.settle)
    else:
        analyze_profile(load_run(a.run), out, a.floor_step, a.settle)


if __name__ == "__main__":
    main()
