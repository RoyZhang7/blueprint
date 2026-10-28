#!/usr/bin/env python3
"""Offline analysis for the per-endpoint dictionary experiments (rule A primary).

Usage:
  analyze.py RUN_DIR [--out DIR] [--settle S] [--costs costs.json]
      profiling sweep (A2/B2): FL_e (lowest step and Cherkasova-refined), c_e, rho and
      avgS per window, W(rho) p50/p90, rule-A collapse (rule B as diagnostic), sigma_e,
      Lat_e source / caller-stamp coverage, CPU accounting, service-level curve
      -> fl.csv, wait.csv, collapse.csv, svc_curve.csv, a2_summary.png
  analyze.py RUN_DIR --dict A2_RUN [--dict A2_RUN2 ...] [--stressed-knee RHO]
      prediction run (A3/B3/B4): D_e = FL_e + W(rho, avgS) from the profiling run(s)
      (two or more --dict at different mixes give the avgS input), W-range guard (F5),
      per-segment error (F7 wording) and the shape test (F6) -> a3_error.csv, a3_shape.csv,
      a3_error.png
  analyze.py --idle-costs RUN [RUN ...] --out-costs costs.json
      B1: c_e from single-endpoint idle runs (process CPU / completions)

Inputs (written by scripts/exp.sh): RUN_DIR/gordion.jsonl (recorder windows, raw samples
when GORDION_RAW=1), RUN_DIR/wlgen.jsonl (steps, mixes, 1 s client reports), RUN_DIR/meta.json.
Notation: see dictionary.NOTATION.
"""
import argparse
import json
import os
import sys
from collections import defaultdict

import numpy as np

sys.path.insert(0, os.path.dirname(__file__))
from dictionary import (EP_A, EP_N, EP_P50, EP_P90, EP_P99, EP_MEAN, EP_MAX, EP_PP50, EP_PP90, EP_Q50, EP_Q90,
                        EP_X50, EP_X90, EP_NS, EP_F50, EP_F90, NOTATION, OVERLOAD_WAIT, WaitModel, assign, avgS_of_window,
                        costs_from_idle_run, costs_of_run, cpuset_size, fit_rule_a, floors_lowest_step,
                        fmt_ratio, lat_samples, notation, overloaded_steps, ratio_stats, rho_of_window,
                        settled_windows, shape_test, stamp_coverage, window_len_s)

try:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
except Exception:  # pragma: no cover
    plt = None


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
    wl = os.path.join(d, "wlgen.jsonl")
    if os.path.exists(wl):
        for line in open(wl):
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
        sys.exit(f"no 'run' event in {wl}")
    return dict(meta=meta, start=start, windows=windows, run=run, steps=steps, mixes=mixes, reports=reports, dir=d)


# kept for older scripts
def floors(windows, names, cfg_floors_ms):
    acc = defaultdict(list)
    for w in windows:
        for e in w["ep"]:
            x, _ = lat_samples(w, e)
            acc[e].extend(x.tolist())
    rows = []
    for e in names:
        x = np.asarray(acc.get(e, []), float)
        if len(x) == 0:
            rows.append((e, cfg_floors_ms[e], np.nan, np.nan, 0))
        else:
            rows.append((e, cfg_floors_ms[e], np.median(x) / 1e6, np.percentile(x, 90) / 1e6, len(x)))
    return rows


# ----------------------------------------------------------------------------- profiling run
def analyze_profile(R, out, settle_s, costs_file=None, quiet=False):
    names = R["run"]["endpoints"]
    cfg = dict(zip(names, R["run"]["floors_ms"]))
    viol = dict(zip(names, R["run"]["violators"]))
    k = cpuset_size(R["meta"].get("cpuset", "0-7"))
    say = (lambda *a, **kw: None) if quiet else print
    W, step_of, _ = settled_windows(R, settle_s)
    steps_present = sorted(set(step_of))
    rate_of_step = {s["step"]: s["rate"] for s in R["steps"]}
    say(f"windows kept: {len(W)}  steps: {steps_present}  k={k}")
    bad = sum(1 for w in W if sum(v[EP_A] for v in w["ep"].values()) != w["a"] or sum(v[EP_N] for v in w["ep"].values()) != w["n"])
    say(f"A1 check: {bad} of {len(W)} windows where sum_e(a_e,n_e) != (a,n); dropped completions: {sum(w.get('drop', 0) for w in W)}")
    first_arr = min(w["t"] for w in R["windows"])
    say(f"clock check: first busy window {first_arr - R['steps'][0]['t']:+d} ms after first step event")

    # --- inputs: c_e for rho (F3), Lat_e source (F4)
    c_ms, csrc = costs_of_run(R, costs_file)
    cov = stamp_coverage(W)
    say(f"rho from c_e: {csrc}; mean c_e {np.mean(list(c_ms.values())):.3f} ms vs mean configured FL {np.mean(list(cfg.values())):.3f} ms")
    # --- floors and the rule-A fit
    fl0, n0 = floors_lowest_step(W, step_of, names, cfg)
    over = overloaded_steps(R, W, step_of)
    fit = fit_rule_a(W, step_of, names, c_ms, fl0, k, viol, overloaded=over)
    fl = fit["fl"]
    srcs = fit["sources"]; tot = sum(srcs.values()) or 1
    say("Lat_e source: " + ", ".join(f"{s} {n / tot * 100:.0f}%" for s, n in sorted(srcs.items())) +
        f"; caller stamp coverage {cov * 100:.0f}% of completions" +
        ("" if cov > 0.95 else "  ** caller not (fully) instrumented: Lat_e misses the admission wait **"))
    normal = [e for e in names if not viol[e] and n0[e] > 0]
    r0 = np.array([fl0[e] / cfg[e] for e in normal]); r1 = np.array([fl[e] / fl0[e] for e in normal])
    fs = min(step_of)
    say(f"floors (lowest step {fs}, rate {rate_of_step[fs]}): {len(normal)} normal endpoints, median n={np.median([n0[e] for e in normal]):.0f}; "
        f"lowest-step/configured median={np.median(r0):.3f} [{r0.min():.3f},{r0.max():.3f}], within 5%: {np.mean(np.abs(r0 - 1) < 0.05) * 100:.0f}%"
        + ("  (Lat_e includes the caller->handler transport, so FL_e sits above the configured handler time by a fixed amount)" if cov > 0 else ""))
    say(f"floors refined (median over all windows of Lat_e - W(rho)): refined/lowest-step median={np.median(r1):.3f} [{r1.min():.3f},{r1.max():.3f}]")
    for e in names:
        if viol[e]:
            say(f"   violator {e}: configured {cfg[e]:.2f} ms, lowest-step {fl0[e]:.2f}, refined {fl[e]:.2f} ms (n={n0[e]})")
    with open(os.path.join(out, "fl.csv"), "w") as f:
        f.write("endpoint,violator,configured_ms,c_ms,fl_lowest_ms,fl_refined_ms,n_lowest,sigma_ms,devA_ms\n")
        for i, e in enumerate(names):
            f.write(f"{e},{int(viol[e])},{cfg[e]:.4f},{c_ms[e]:.4f},{fl0[e]:.4f},{fl[e]:.4f},{n0[e]},{fit['sigma'][e]:.4f},{fit['devA'][i]:.4f}\n")
    with open(os.path.join(out, "wait.csv"), "w") as f:
        f.write("rho_mid,util,W50_ms,W90_ms,n,iqrA_ms,cvA,cvB_diag\n")
        for b in range(len(fit["mids"])):
            if not np.isnan(fit["W50"][b]):
                f.write(f"{fit['mids'][b]:.3f},{fit['mids'][b] / k:.3f},{fit['W50'][b]:.4f},{fit['W90'][b]:.4f},{fit['Wn'][b]},{fit['iqrA'][b]:.4f},{fit['cvA'][b]:.4f},{fit['cvB'][b]:.4f}\n")
    # --- collapse (rule A primary, rule B diagnostic)
    say("rule A collapse per rho bin (W = median_e(Lat_e - FL_e); IQR/CV across normal endpoints; CV_B = rule-B diagnostic):")
    say("   rho    u     W50     W90    IQR_A    CV_A   CV_B   n_ep")
    for b in range(len(fit["mids"])):
        if not np.isnan(fit["W50"][b]):
            nep = int(np.sum(~np.isnan(fit["medA"][:, b])))
            say(f"   {fit['mids'][b]:5.2f} {fit['mids'][b] / k:4.2f} {fit['W50'][b]:7.2f} {fit['W90'][b]:7.2f} {fit['iqrA'][b]:7.3f} {fit['cvA'][b]:6.2f} {fit['cvB'][b]:6.2f}  {nep}")
    hi = [b for b in range(len(fit["mids"])) if fit["mids"][b] / k > 0.5 and not np.isnan(fit["W50"][b])]
    if hi:
        say(f"   mean over bins with u>0.5: CV_A={np.nanmean(fit['cvA'][hi]):.2f}  CV_B={np.nanmean(fit['cvB'][hi]):.2f}  -> "
            f"{'rule A (additive) collapses better' if np.nanmean(fit['cvA'][hi]) < np.nanmean(fit['cvB'][hi]) else 'rule B (multiplicative) collapses better: CHECK'}")
    order = np.argsort(-np.nan_to_num(np.abs(fit["devA"]), nan=-1))
    say("endpoints ranked by |median_b(Lat_e - FL_e - W_b)| (additive deviation from the shared wait):")
    for i in order[:8]:
        say(f"   {names[i]:14s} viol={int(viol[names[i]])} dev={fit['devA'][i]:+.3f} ms  sigma_e={fit['sigma'][names[i]]:.3f} ms  FL={fl[names[i]]:.2f} ms")
    planted = set(e for e in names if viol[e])
    if planted:
        top = set(names[i] for i in order[:len(planted)])
        say(f"collapse verdict: top-{len(planted)} deviators = {sorted(top)}; planted = {sorted(planted)}; {'PASS' if top == planted else 'CHECK'}")
    with open(os.path.join(out, "collapse.csv"), "w") as f:
        f.write("endpoint,violator,fl_ms,devA_ms,sigma_ms,devB_diag,bins\n")
        for i, e in enumerate(names):
            devB = np.nanmedian(fit["medB"][i] - np.nanmedian(fit["medB"], axis=0)) if np.any(~np.isnan(fit["medB"][i])) else np.nan
            f.write(f"{e},{int(viol[e])},{fl[e]:.4f},{fit['devA'][i]:.4f},{fit['sigma'][e]:.4f},{devB:.4f},{int(np.sum(~np.isnan(fit['medA'][i])))}\n")
    say(f"guard inputs (F5): profiled W max = {fit['wmax']:.2f} ms at rho <= {fit['rho_max']:.2f} (u {fit['rho_max'] / k:.2f}); "
        f"overloaded steps dropped from the dictionary: {fit['overloaded'] or 'none'} (served < 95% of target, or median wait > {OVERLOAD_WAIT:.0f} x avgS)")

    # --- service-level curves and CPU accounting
    svc = []
    for s in steps_present:
        ws = [w for w, st in zip(W, step_of) if st == s]
        allraw = np.concatenate([lat_samples(w, e)[0] for w in ws for e in w["ep"]]) if ws else np.zeros(0)
        lam = sum(w["a"] for w in ws) / sum(window_len_s(w) for w in ws)
        rh = float(np.mean([rho_of_window(w, c_ms) for w in ws])); S = float(np.nanmean([avgS_of_window(w, c_ms) for w in ws]))
        svc.append((s, rate_of_step[s], lam, rh, S, np.median(allraw) / 1e6 if len(allraw) else np.nan,
                    np.percentile(allraw, 90) / 1e6 if len(allraw) else np.nan, len(allraw), stamp_coverage(ws)))
    with open(os.path.join(out, "svc_curve.csv"), "w") as f:
        f.write("step,rate_target,lambda,rho,avgS_ms,p50_ms,p90_ms,n,stamp_coverage\n")
        for r in svc:
            f.write(",".join(f"{v:.4f}" if isinstance(v, float) else str(v) for v in r) + "\n")
    if any("cpu" in w for w in W):
        say("CPU accounting: step  lambda  cores_busy  sum(a*c_e)/s(cores)  cpu_per_req(ms)  mean_c_e(ms)  overhead/req(ms)")
        for s in steps_present:
            ws = [w for w, st in zip(W, step_of) if st == s and "cpu" in w]
            if not ws:
                continue
            secs = sum(window_len_s(w) for w in ws); cpu = sum(w["cpu"] for w in ws) / 1e9
            arr = sum(w["a"] for w in ws); offered = sum(rho_of_window(w, c_ms) * window_len_s(w) for w in ws)
            if arr:
                say(f"   {s:3d}  {arr / secs:7.0f}  {cpu / secs:7.2f}      {offered / secs:7.2f}           {cpu / arr * 1e3:7.3f}      {offered / arr * 1e3:7.3f}      {(cpu - offered) / arr * 1e3:+7.3f}")
    say("service-level: step rate lambda rho/k avgS p50 p90 stamped%")
    for r in svc:
        say(f"   {r[0]:3d} {r[1]:7.0f} {r[2]:8.1f} {r[3] / k:5.2f} {r[4]:6.2f} {r[5]:8.3f} {r[6]:8.3f} {r[8] * 100:5.0f}")
    if plt and not quiet:
        plot_profile(out, names, viol, cfg, fl0, fl, fit, k, svc, R["dir"])
    model = WaitModel(k).add(fit["avgS"], fit["mids"], fit["W50"], fit["W90"], fit["wmax"], fit["rho_max"], os.path.basename(R["dir"]))
    return dict(fl=fl, fl0=fl0, c_ms=c_ms, viol=viol, names=names, k=k, fit=fit, model=model, run=R["dir"])


def plot_profile(out, names, viol, cfg, fl0, fl, fit, k, svc, run):
    fig, ax = plt.subplots(1, 4, figsize=(20, 4.8))
    c = [cfg[e] for e in names if not viol[e]]; m = [fl0[e] for e in names if not viol[e]]
    ax[0].loglog(c, m, ".", label="FL_e lowest step")
    ax[0].loglog(c, [fl[e] for e in names if not viol[e]], "+", ms=4, label="FL_e refined")
    for e in names:
        if viol[e]:
            ax[0].loglog([cfg[e]], [fl0[e]], "x", ms=9, label=e)
    lo, hi = min(c) * 0.8, max(c) * 1.25
    ax[0].plot([lo, hi], [lo, hi], "k--", lw=0.8)
    ax[0].set_xlabel("configured FL_e (ms)"); ax[0].set_ylabel("measured FL_e (ms)"); ax[0].set_title("floors"); ax[0].legend(fontsize=7)
    mids = fit["mids"]
    for i, e in enumerate(names):
        ok = ~np.isnan(fit["medA"][i])
        if not ok.any():
            continue
        st = dict(lw=0.6, color="0.6", alpha=0.6) if not viol[e] else dict(lw=1.5, marker="o", ms=3, label=e)
        ax[1].plot(mids[ok] / k, fit["medA"][i, ok], **st); ax[2].plot(mids[ok] / k, fit["medB"][i, ok], **st)
    ok = ~np.isnan(fit["W50"])
    ax[1].plot(mids[ok] / k, fit["W50"][ok], "k-", lw=2, label="W (median over endpoints)")
    ax[1].plot(mids[ok] / k, fit["W90"][ok], "k--", lw=1.2, label="W p90")
    ax[1].set_xlabel("rho/k"); ax[1].set_ylabel("Lat_e - FL_e (ms)"); ax[1].set_yscale("symlog", linthresh=0.1); ax[1].set_title("rule A: additive wait, per endpoint"); ax[1].legend(fontsize=7)
    ax[2].set_xlabel("rho/k"); ax[2].set_ylabel("Lat_e / FL_e"); ax[2].set_yscale("log"); ax[2].set_title("rule B view (diagnostic only)"); ax[2].legend(fontsize=7)
    s = np.array([(r[2], r[3] / k, r[5], r[6]) for r in svc])
    ax[3].plot(s[:, 1], s[:, 2], "o-", label="p50"); ax[3].plot(s[:, 1], s[:, 3], "s-", label="p90")
    ax[3].set_xlabel("rho/k"); ax[3].set_ylabel("service-level Lat (ms)"); ax[3].set_yscale("log"); ax[3].set_title("service-level curve"); ax[3].legend()
    for a in ax:
        a.grid(alpha=.3)
    fig.tight_layout(); notation(fig, f"run {os.path.basename(run)}; k = {k}")
    fig.savefig(os.path.join(out, "a2_summary.png"), dpi=130)
    print("wrote", os.path.join(out, "a2_summary.png"))


# ----------------------------------------------------------------------------- prediction run
def combine_models(profs):
    """One WaitModel from several profiling runs (F2): each contributes W(rho) at its avgS."""
    m = WaitModel(profs[0]["k"])
    for p in profs:
        f = p["fit"]
        m.add(f["avgS"], f["mids"], f["W50"], f["W90"], f["wmax"], f["rho_max"], os.path.basename(p["run"]))
    return m


def analyze_predict(R, prof, model, out, settle_s, stressed_knee=None, use_avgS=True):
    """Predict per-endpoint Lat_e in each (step, mix) segment of R from D_e = FL_e + W(rho, avgS)."""
    names, viol, fl, c_ms, k = prof["names"], prof["viol"], prof["fl"], prof["c_ms"], prof["k"]
    W, step_of, mix_of = settled_windows(R, settle_s)
    seg = defaultdict(list)
    for w, s, m in zip(W, step_of, mix_of):
        seg[(s, m)].append(w)
    rows, shapes, removed, kept = [], [], 0, 0
    for (s, m), ws in sorted(seg.items()):
        if len(ws) < 10:
            continue
        rho = float(np.mean([rho_of_window(w, c_ms) for w in ws])); S = float(np.nanmean([avgS_of_window(w, c_ms) for w in ws]))
        usable = model.usable(rho, S if use_avgS else None, stressed_knee)
        if not usable:
            removed += 1
        else:
            kept += 1
        Wp = model.predict(rho, S if use_avgS else None)
        tot_a = sum(w["a"] for w in ws)
        acc = defaultdict(list); arr = defaultdict(int)
        for w in ws:
            for e, v in w["ep"].items():
                arr[e] += v[EP_A]
                x, _ = lat_samples(w, e)
                if len(x):
                    acc[e].extend((x / 1e6).tolist())
        resid = {}
        for e in names:
            x = acc.get(e)
            if not x or len(x) < 10:
                continue
            obs = float(np.median(x)); pred = fl[e] + Wp
            rows.append(dict(step=s, mix=m, rho=rho, util=rho / k, avgS=S, e=e, viol=int(viol[e]), share=arr[e] / max(1, tot_a), fl=fl[e],
                             W=Wp, pred=pred, obs=obs, err=obs - pred, err_fl=(obs - pred) / fl[e], n=len(x), usable=int(usable)))
            if not viol[e]:
                resid[e] = obs - pred
        st = shape_test(resid, fl)
        if st:
            shapes.append(dict(step=s, mix=m, rho=rho, util=rho / k, avgS=S, slope=st[0], intercept=st[1], r2=st[2], n=st[3], usable=int(usable)))
    with open(os.path.join(out, "a3_error.csv"), "w") as f:
        f.write("step,mix,rho,util,avgS_ms,endpoint,violator,share,fl_ms,W_ms,pred_ms,obs_ms,err_ms,err_over_fl,n,usable\n")
        for r in rows:
            f.write(f"{r['step']},{r['mix']},{r['rho']:.4f},{r['util']:.4f},{r['avgS']:.4f},{r['e']},{r['viol']},{r['share']:.5f},{r['fl']:.4f},{r['W']:.4f},{r['pred']:.4f},{r['obs']:.4f},{r['err']:.4f},{r['err_fl']:.4f},{r['n']},{r['usable']}\n")
    with open(os.path.join(out, "a3_shape.csv"), "w") as f:
        f.write("step,mix,rho,util,avgS_ms,slope,intercept_ms,r2,n_endpoints,usable\n")
        for r in shapes:
            f.write(f"{r['step']},{r['mix']},{r['rho']:.4f},{r['util']:.4f},{r['avgS']:.4f},{r['slope']:.5f},{r['intercept']:.4f},{r['r2']:.3f},{r['n']},{r['usable']}\n")
    print(f"prediction: W model = {model.describe()}; avgS input {'on' if use_avgS and len(model.curves) > 1 else 'off (single profiled mix)'}")
    print(f"guard (F5): {removed} of {removed + kept} segments removed (predicted W above the profiled maximum {model.wmax:.2f} ms"
          f"{f', or rho above the stressed knee {stressed_knee:.2f}' if stressed_knee else ''}); stats below are over the {kept} usable segments")
    U = [r for r in rows if r["usable"]]
    nrm = [r for r in U if not r["viol"]]
    if not nrm:
        print("   no usable normal endpoint-segments"); return
    err = np.array([r["err"] for r in nrm]); errfl = np.array([r["err_fl"] for r in nrm])
    print(f"   {len(set((r['step'], r['mix']) for r in U))} segments, {len(nrm)} normal endpoint-segments, u {min(r['util'] for r in U):.2f}-{max(r['util'] for r in U):.2f}, avgS {min(r['avgS'] for r in U):.2f}-{max(r['avgS'] for r in U):.2f} ms")
    print(f"   Lat_e - D_e: median={np.median(err):+.3f} ms, IQR=[{np.percentile(err, 25):+.3f},{np.percentile(err, 75):+.3f}], |err| p90={np.percentile(np.abs(err), 90):.3f} ms;"
          f"  /FL_e: median={np.median(errfl):+.3f}, |err| p90={np.percentile(np.abs(errfl), 90):.3f}")
    ex = [r for r in rows if not r["usable"] and not r["viol"]]
    if ex:
        e2 = np.array([r["err"] for r in ex])
        print(f"   (removed segments, for the record: {len(ex)} endpoint-segments, err median={np.median(e2):+.2f} ms, |err| p90={np.percentile(np.abs(e2), 90):.2f} ms)")
    X = np.c_[np.ones(len(nrm)), [r["util"] for r in nrm]]
    res = err - X @ np.linalg.lstsq(X, err, rcond=None)[0]
    print(f"   corr(own share, err residual after u) = {np.corrcoef([r['share'] for r in nrm], res)[0, 1]:+.3f}")
    for e in names:
        if viol[e]:
            M = [r for r in U if r["e"] == e]
            if len(M) > 5:
                X = np.c_[np.ones(len(M)), [r["util"] for r in M]]; y = np.array([r["err"] for r in M])
                res = y - X @ np.linalg.lstsq(X, y, rcond=None)[0]
                print(f"   {e:14s}: err median={np.median(y):+.3f} ms ({np.median([r['err_fl'] for r in M]):+.2f} x FL); corr(own share, resid)={np.corrcoef([r['share'] for r in M], res)[0, 1]:+.3f} (n={len(M)})")
    sh = [r for r in shapes if r["usable"]]
    if sh:
        sl = np.array([r["slope"] for r in sh]); ic = np.array([r["intercept"] for r in sh])
        print(f"shape test (F6) over {len(sh)} usable segments: slope median={np.median(sl):+.3f} IQR=[{np.percentile(sl, 25):+.3f},{np.percentile(sl, 75):+.3f}];"
              f" intercept median={np.median(ic):+.3f} ms IQR=[{np.percentile(ic, 25):+.3f},{np.percentile(ic, 75):+.3f}]; r2 median={np.median([r['r2'] for r in sh]):.2f}")
    if plt and rows:
        fig, ax = plt.subplots(1, 3, figsize=(16, 4.6))
        u = np.array([r["util"] for r in nrm]); sh_ = np.array([r["share"] for r in nrm])
        ax[0].plot(u, err, ".", ms=3, alpha=0.4, label="normal, usable")
        if ex:
            ax[0].plot([r["util"] for r in ex], e2, ".", ms=3, alpha=0.3, color="0.6", label="removed by W guard")
        for e in names:
            if viol[e]:
                M = [(r["util"], r["err"]) for r in U if r["e"] == e]
                if M:
                    ax[0].plot(*zip(*M), "x", ms=6, label=e)
        ax[0].axhline(0, color="k", lw=0.8); ax[0].set_xlabel("rho/k"); ax[0].set_ylabel("Lat_e - D_e[rho, avgS] (ms)"); ax[0].set_title("rule A error vs rho"); ax[0].legend(fontsize=7)
        ax[1].plot(sh_ * 100, err, ".", ms=3, alpha=0.4)
        ax[1].axhline(0, color="k", lw=0.8); ax[1].set_xlabel("own share %_e (%)"); ax[1].set_ylabel("Lat_e - D_e (ms)"); ax[1].set_title("error vs own share")
        if sh:
            us = [r["util"] for r in sh]
            ax[2].plot(us, sl, "o", ms=3, label="slope (x FL_e)"); ax[2].plot(us, ic, "s", ms=3, label="intercept (ms)")
            ax[2].axhline(0, color="k", lw=0.8); ax[2].set_xlabel("rho/k"); ax[2].set_title("shape test: r_e = intercept + slope*FL_e"); ax[2].legend(fontsize=7)
        for a in ax:
            a.grid(alpha=.3)
        fig.tight_layout(); notation(fig, f"run {os.path.basename(R['dir'])}; dictionary {model.describe()}")
        fig.savefig(os.path.join(out, "a3_error.png"), dpi=130)
        print("wrote", os.path.join(out, "a3_error.png"))


# ----------------------------------------------------------------------------- B1 costs
def idle_costs(runs, out_file):
    costs = {}
    for d in runs:
        R = load_run(d)
        c = costs_from_idle_run(R)
        for e, v in c.items():
            costs.setdefault(e, []).append(v)
        print(f"{os.path.basename(d)}: {len(c)} endpoints, " + ", ".join(f"{e}={v:.3f}" for e, v in list(c.items())[:4]))
    merged = {e: float(np.median(v)) for e, v in costs.items()}
    json.dump({"costs_ms": merged, "runs": [os.path.basename(d) for d in runs]}, open(out_file, "w"), indent=1)
    print(f"wrote {out_file}: c_e for {len(merged)} endpoints")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("run", nargs="?")
    ap.add_argument("--dict", action="append", default=[], help="profiling run(s) to build the dictionary from; several at different mixes give W(rho, avgS)")
    ap.add_argument("--out", default=None)
    ap.add_argument("--settle", type=float, default=5.0, help="seconds dropped after each step/mix change")
    ap.add_argument("--costs", default=None, help="costs.json from --idle-costs (c_e per endpoint); default: configured cpu_ms")
    ap.add_argument("--stressed-knee", type=float, default=None, help="rho (not /k) of the stressed knee, if known: F5 guard")
    ap.add_argument("--no-avgS", action="store_true", help="ignore the avgS input even with several --dict")
    ap.add_argument("--idle-costs", nargs="+", default=None, help="B1: single-endpoint idle runs -> costs.json")
    ap.add_argument("--out-costs", default="costs.json")
    a = ap.parse_args()
    if a.idle_costs:
        idle_costs(a.idle_costs, a.out_costs); return
    if not a.run:
        ap.error("run is required")
    out = a.out or a.run
    os.makedirs(out, exist_ok=True)
    if a.dict:
        profs = [analyze_profile(load_run(d), d, a.settle, a.costs, quiet=(i > 0)) for i, d in enumerate(a.dict)]
        model = combine_models(profs)
        analyze_predict(load_run(a.run), profs[0], model, out, a.settle, a.stressed_knee, use_avgS=not a.no_avgS)
    else:
        analyze_profile(load_run(a.run), out, a.settle, a.costs)


if __name__ == "__main__":
    main()
