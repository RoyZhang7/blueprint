#!/usr/bin/env python3
"""Phase G section 1 (G1): service-time inflation I = U / rho from the recorder's process-CPU field.

Per window of a run:
  cpu   = the recorder's "cpu" field: process user+sys CPU (RUSAGE_SELF) spent since the previous flush, ns
          (Go: runtime/plugins/gordion/recorder.go flush(), cpu_linux.go processCPUNs(); C++: Gordion.h cpuNs())
  dt    = the interval the cpu delta covers: the Go recorder writes the nominal window in "w" and stamps "t" with the
          previous flush time, so dt = t[i+1] - t[i] (clipped to [0.5 w, 3 w], else w); the C++ recorder writes the
          measured flush-to-flush length in "w" (image g2+), so dt = w
  U     = cpu / dt / k                      (k = |cpuset|; cores busy = U k)
  rho   = sum_e a_e c_e / dt / k            (offered utilisation, GORDION_FREQ_NORM as elsewhere)
  I     = U / rho = cpu / sum_e a_e c_e     (dt cancels; I is the measured CPU per unit of offered work)
U and rho are smoothed with the causal Gaussian of Algorithm 1 (5 s, as Lat) and I_s is their ratio. The Go recorder's
"sched" field (runtime wait p50/p90/count, ns; runtime/metrics /sched/latencies) is carried along as Q50/Q90 (ms).

Usage: g1_inflation.py --out results/g1 --run "label|service|dir|costs|kind|twin_label" [--run ...] [--smooth 5]
  kind = sweep | ramp ; twin_label names the unstressed run of a stressed one (empty for unstressed runs).
Writes <run>/g1/g1_windows.csv (new dir, nothing overwritten), <out>/g1_inflation.csv (service x run x step),
<out>/g1_stressed.csv (stressed vs twin per step), <out>/g1_collapse.csv, <out>/g1_thresholds.csv and two figures.
"""
import argparse, csv, json, os, sys
import numpy as np
import matplotlib; matplotlib.use("Agg"); import matplotlib.pyplot as plt
sys.path.insert(0, os.path.dirname(__file__))
from analyze import load_run
from dictionary import EP_A, costs_of_run, cpuset_size, assign, served_ratio, clock_mhz, NOMINAL_MHZ, fit_service_dict
from b4_compare import causal_gauss


def windows_of(R, c_ms, k, smooth):
    W = R["windows"]
    t = np.array([w["t"] for w in W], float)
    wn = np.array([w.get("w", 100) for w in W], float)
    cpp = len(set(wn.tolist())) > 1            # the C++ recorder writes the measured window length
    dt = wn.copy()
    if not cpp:
        d = np.append(np.diff(t), np.nan)
        ok = np.isfinite(d) & (d >= 0.5 * wn) & (d <= 3 * wn)
        dt[ok] = d[ok]
    cpu = np.array([w.get("cpu", np.nan) for w in W], float)
    work = np.array([sum(v[EP_A] * c_ms.get(e, 0.0) for e, v in w["ep"].items()) for w in W]) * 1e6  # ns of CPU at f_nom
    clk = np.array([(clock_mhz(w) or NOMINAL_MHZ) / NOMINAL_MHZ for w in W])
    arr = np.array([w["a"] for w in W], float)
    U = cpu / 1e6 / dt / k                        # cores busy / k
    rho = work / 1e6 / dt / k / clk
    I = cpu * clk / work
    I[work <= 0] = np.nan
    ts = t / 1000.0
    U_s = causal_gauss(U, ts, smooth); rho_s = causal_gauss(rho, ts, smooth)
    I_s = U_s / rho_s
    sched = [w.get("sched") for w in W]
    q50 = np.array([s[0] / 1e6 if s else np.nan for s in sched]); q90 = np.array([s[1] / 1e6 if s else np.nan for s in sched])
    step_of = assign(W, R["steps"], "step") if R["steps"] else [0] * len(W)
    step_of = [(-1 if s is None else s) for s in step_of]
    step_t0 = {s["step"]: s["t"] for s in R["steps"]}
    settled = np.array([s >= 0 and t[i] >= step_t0.get(s, -1e18) + 5000 for i, s in enumerate(step_of)])
    rate = {s["step"]: s["rate"] for s in R["steps"]}
    keep = [i for i, s in enumerate(step_of) if s >= 0]   # served/target relative to the lowest real step (warm-up step -1 excluded)
    sv = served_ratio(R, [W[i] for i in keep], [step_of[i] for i in keep]) if R["steps"] and keep else {}
    return dict(t=t, dt=dt, cpu=cpu, work=work, arr=arr, U=U, rho=rho, I=I, U_s=U_s, rho_s=rho_s, I_s=I_s, q50=q50, q90=q90,
                step=np.array(step_of), settled=settled, rate=rate, served=sv, cpp=cpp, k=k, n=len(W))


def iqr(x):
    x = x[np.isfinite(x)]
    if len(x) < 5: return (np.nan, np.nan, np.nan, len(x))
    return (float(np.median(x)), float(np.percentile(x, 25)), float(np.percentile(x, 75)), len(x))


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--out", required=True); ap.add_argument("--run", action="append", required=True)
    ap.add_argument("--smooth", type=float, default=5.0)
    ap.add_argument("--g", action="append", default=[], help="B.1: svc=profiling-sweep-dir[=costs]: g(rho) of that service from its sweep's dictionary")
    a = ap.parse_args(); os.makedirs(a.out, exist_ok=True)
    G = {}
    for spec in a.g:
        svc, d, *rest = spec.split("=")           # key=dir[=costs[=svc-env]]
        svcenv = rest[1] if len(rest) > 1 else (svc if svc not in ("", "-", "frontend", "synthetic") else "")
        if svcenv: os.environ["GORDION_SVC"] = svcenv
        elif "GORDION_SVC" in os.environ: del os.environ["GORDION_SVC"]
        SD = fit_service_dict(load_run(d), 5.0, rest[0] if rest and rest[0] else None)
        G[svc] = SD
        gs = {st: round(v["g"], 3) for st, v in sorted(SD.steps.items())}
        print(f"g({svc}) from {os.path.basename(d)}: cbar_sweep {SD.cbar_sweep:.3f} ms; g per step {gs}")
    runs = []
    for spec in a.run:
        lab, svc, d, costs, kind, twin, u0s, gkey = (spec.split("|") + [""] * 8)[:8]
        os.environ["GORDION_SVC"] = svc if svc not in ("", "-", "frontend", "synthetic") else ""
        if not os.environ["GORDION_SVC"]: del os.environ["GORDION_SVC"]
        R = load_run(d)
        k = cpuset_size(R["meta"].get("cpuset", "0-7"))
        c_ms, csrc = costs_of_run(R, costs or None)
        u0 = float(u0s) if u0s else (json.load(open(costs)).get("base_ms_per_s", 0.0) / 1000.0 if costs else 0.0)  # base CPU, cores (A.1: the regression's D0)
        X = windows_of(R, c_ms, k, a.smooth)
        X["U0"] = u0; X["I_marg"] = (X["U"] - u0 / k) / X["rho"]; X["I_marg_s"] = (X["U_s"] - u0 / k) / X["rho_s"]
        SDg = G.get(gkey or svc or "synthetic")
        X["g_rho"] = SDg.g_at(X["rho_s"]) if SDg is not None else np.full(X["n"], np.nan)
        X["I_g"] = X["I"] / X["g_rho"]; X["I_g_s"] = X["I_s"] / X["g_rho"]
        g = os.path.join(d, "g1"); os.makedirs(g, exist_ok=True)
        with open(os.path.join(g, "g1_windows.csv"), "w", newline="") as f:
            w = csv.writer(f); w.writerow(["t", "dt_ms", "cpu_ns", "work_ns", "a", "U", "rho", "I", "U_s", "rho_s", "I_s", "Q50_ms", "Q90_ms", "step", "settled", "U0", "I_marg", "I_marg_s", "g_rho", "I_g", "I_g_s"])
            for i in range(X["n"]):
                w.writerow([int(X["t"][i]), f"{X['dt'][i]:.0f}", f"{X['cpu'][i]:.0f}", f"{X['work'][i]:.0f}", int(X["arr"][i])] +
                           [f"{X[c][i]:.4f}" for c in ("U", "rho", "I", "U_s", "rho_s", "I_s", "q50", "q90")] + [X["step"][i], int(X["settled"][i])] +
                           [f"{u0:.4f}", f"{X['I_marg'][i]:.4f}", f"{X['I_marg_s'][i]:.4f}", f"{X['g_rho'][i]:.4f}", f"{X['I_g'][i]:.4f}", f"{X['I_g_s'][i]:.4f}"])
        gaps = int(np.sum(~np.isfinite(X["cpu"])))
        print(f"{lab:38s} {os.path.basename(d):44s} k={k} windows {X['n']} dt source {'w (C++ measured)' if X['cpp'] else 't-diff (Go)'} "
              f"costs: {csrc}; U0 {u0:.3f} cores; cores busy median {np.nanmedian(X['U'] * k):.2f} max {np.nanmax(X['U_s'] * k):.2f}; I_s median {np.nanmedian(X['I_s']):.3f}; I_marg_s median {np.nanmedian(X['I_marg_s']):.3f}; I_g_s median {np.nanmedian(X['I_g_s']):.3f}"
              + (f"; {gaps} windows without cpu" if gaps else ""))
        runs.append(dict(label=lab, svc=svc or "synthetic", dir=d, kind=kind, twin=twin, X=X, k=k, csrc=csrc))

    # ---- per step table (sweeps) / per rho bin (ramps)
    rows = []
    def groups(r):
        X = r["X"]
        if r["kind"] == "sweep":
            for s in sorted(set(X["step"])):
                if s < 0: continue
                m = (X["step"] == s) & X["settled"]
                yield (f"step {s} ({X['rate'].get(s, '?')} req/s)", s, X["rate"].get(s, np.nan), X["served"].get(s, np.nan), m)
        else:
            for lo in np.arange(0.0, 1.5, 0.1):
                m = (X["rho_s"] >= lo) & (X["rho_s"] < lo + 0.1) & (X["step"] >= 0)
                if m.sum() >= 5: yield (f"rho {lo:.1f}-{lo + 0.1:.1f}", lo, np.nan, np.nan, m)
    for r in runs:
        X = r["X"]; first = True
        for name, key, rate, served, m in groups(r):
            med, p25, p75, n = iqr(X["I_s"][m]); medr, _, _, _ = iqr(X["I"][m]); mm, m25, m75, _ = iqr(X["I_marg_s"][m]); gg, g25, g75, _ = iqr(X["I_g_s"][m])
            rows.append(dict(service=r["svc"], run=r["label"], kind=r["kind"], stressed=int(bool(r["twin"])), group=name, key=key, rate=rate,
                             served=served, rho=float(np.nanmedian(X["rho_s"][m])), cores_busy=float(np.nanmedian(X["U_s"][m] * r["k"])),
                             I_med=med, I_p25=p25, I_p75=p75, I_iqr=(p75 - p25) if np.isfinite(p75) else np.nan, I_raw_med=medr, n=n,
                             lowest_step=int(first and r["kind"] == "sweep"), Q50_ms=float(np.nanmedian(X["q50"][m])), Q90_ms=float(np.nanmedian(X["q90"][m])),
                             U0=X["U0"], I_marg_med=mm, I_marg_p25=m25, I_marg_p75=m75, I_marg_iqr=(m75 - m25) if np.isfinite(m75) else np.nan,
                             g_rho=float(np.nanmedian(X["g_rho"][m])), I_g_med=gg, I_g_p25=g25, I_g_p75=g75, I_g_iqr=(g75 - g25) if np.isfinite(g75) else np.nan))
            first = False
    with open(os.path.join(a.out, "g1_inflation.csv"), "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys())); w.writeheader(); w.writerows(rows)
    print("\n== I per step (sweeps) / per rho bin (ramps): median [p25, p75] of the 5 s smoothed I; * = lowest step; ! = served/target < 0.95")
    for r in runs:
        print(f"-- {r['svc']}: {r['label']}  ({'stressed, twin = ' + r['twin'] if r['twin'] else 'unstressed'})")
        for x in [x for x in rows if x["run"] == r["label"]]:
            flag = ("*" if x["lowest_step"] else " ") + ("!" if np.isfinite(x["served"]) and x["served"] < 0.95 else " ")
            print(f"   {flag} {x['group']:24s} rho {x['rho']:.2f}  cores busy {x['cores_busy']:.2f}  I {x['I_med']:.3f} [{x['I_p25']:.3f}, {x['I_p75']:.3f}]  I_marg {x['I_marg_med']:.3f} [{x['I_marg_p25']:.3f}, {x['I_marg_p75']:.3f}]  g {x['g_rho']:.3f} I_g {x['I_g_med']:.3f} [{x['I_g_p25']:.3f}, {x['I_g_p75']:.3f}]  Q50 {x['Q50_ms']:.3f} ms  n={x['n']}")

    # ---- thresholds from the unstressed runs (lowest step and overloaded steps excluded)
    thr = {}
    for svc in sorted(set(r["svc"] for r in runs)):
        sel = [x for x in rows if x["service"] == svc and not x["stressed"] and not x["lowest_step"] and not (np.isfinite(x["served"]) and x["served"] < 0.95) and np.isfinite(x["I_iqr"])]
        if not sel: continue
        iq = float(np.median([x["I_iqr"] for x in sel])); med = float(np.median([x["I_med"] for x in sel]))
        dev = float(np.median([abs(x["I_med"] - 1) for x in sel]))
        q = [x["Q50_ms"] for x in sel if np.isfinite(x["Q50_ms"])]; q9 = [x["Q90_ms"] for x in sel if np.isfinite(x["Q90_ms"])]
        iqm = float(np.median([x["I_marg_iqr"] for x in sel if np.isfinite(x["I_marg_iqr"])])); medm = float(np.median([x["I_marg_med"] for x in sel]))
        devm = float(max(abs(x["I_marg_med"] - 1) for x in sel)); within = sum(abs(x["I_marg_med"] - 1) <= 0.05 for x in sel)
        selg = [x for x in rows if x["service"] == svc and not x["stressed"] and not (np.isfinite(x["served"]) and x["served"] < 0.95) and np.isfinite(x["I_g_iqr"])]
        iqg = float(np.median([x["I_g_iqr"] for x in selg])) if selg else np.nan; medg = float(np.median([x["I_g_med"] for x in selg])) if selg else np.nan
        devg = float(max(abs(x["I_g_med"] - 1) for x in selg)) if selg else np.nan; withing = sum(abs(x["I_g_med"] - 1) <= 0.05 for x in selg)
        thr[svc] = dict(service=svc, steps=len(sel), I_median=med, I_step_iqr=iq, approx_band=f"[{1 - iq:.3f}, {1 + iq:.3f}]", far_beyond=f"> {1 + 3 * iq:.3f}",
                        I_g_steps=len(selg), I_g_median=medg, I_g_step_iqr=iqg, I_g_max_abs_dev=devg, I_g_steps_within_005=withing,
                        g_approx_band=f"[{1 - iqg:.3f}, {1 + iqg:.3f}]", g_far_beyond=f"> {1 + 3 * iqg:.3f}",
                        I_med_abs_dev=dev, Q50_ms_median=float(np.median(q)) if q else np.nan, Q90_ms_median=float(np.median(q9)) if q9 else np.nan,
                        U0_cores=float(np.median([x["U0"] for x in sel])), I_marg_median=medm, I_marg_step_iqr=iqm, I_marg_max_abs_dev=devm, I_marg_steps_within_005=within,
                        marg_approx_band=f"[{1 - iqm:.3f}, {1 + iqm:.3f}]", marg_far_beyond=f"> {1 + 3 * iqm:.3f}")
    with open(os.path.join(a.out, "g1_thresholds.csv"), "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(next(iter(thr.values())).keys())); w.writeheader(); w.writerows(thr.values())
    print("\n== provisional thresholds (unstressed runs; per-step IQR of the smoothed I, median over served steps above the lowest)")
    for v in thr.values():
        print(f"   {v['service']:16s} steps {v['steps']:2d}  I median {v['I_median']:.3f}  step IQR {v['I_step_iqr']:.3f}  ~ within {v['approx_band']}  >> beyond {v['far_beyond']}  Q50 {v['Q50_ms_median']:.3f} ms Q90 {v['Q90_ms_median']:.3f} ms")
        print(f"   {'':16s} g-form (B.1): I_g median {v['I_g_median']:.3f}  step IQR {v['I_g_step_iqr']:.3f}  max |I_g - 1| {v['I_g_max_abs_dev']:.3f}  steps within +-0.05 of 1: {v['I_g_steps_within_005']} of {v['I_g_steps']} (lowest step included)  ~ {v['g_approx_band']}  >> {v['g_far_beyond']}")
        print(f"   {'':16s} marginal: U0 {v['U0_cores']:.3f} cores  I_marg median {v['I_marg_median']:.3f}  step IQR {v['I_marg_step_iqr']:.3f}  max |I_marg - 1| over steps {v['I_marg_max_abs_dev']:.3f}  steps within +-0.05 of 1: {v['I_marg_steps_within_005']} of {v['steps']}  ~ {v['marg_approx_band']}  >> {v['marg_far_beyond']}")

    # ---- stressed vs twin, per step / bin
    srows = []
    for r in runs:
        if not r["twin"]: continue
        tw = next((q for q in runs if q["label"] == r["twin"]), None)
        if tw is None: print("twin not loaded:", r["twin"]); continue
        mine = {x["group"]: x for x in rows if x["run"] == r["label"]}; theirs = {x["group"]: x for x in rows if x["run"] == tw["label"]}
        band = thr.get(r["svc"], {}).get("I_step_iqr", np.nan)
        for g_, x in mine.items():
            y = theirs.get(g_)
            if y is None: continue
            d = x["I_med"] - y["I_med"]; dm = x["I_marg_med"] - y["I_marg_med"]; bandm = thr.get(r["svc"], {}).get("I_marg_step_iqr", np.nan)
            verdict = "~" if abs(d) <= band else (">>" if abs(d) > 3 * band else "between")
            verdict_m = "~" if abs(dm) <= bandm else (">>" if abs(dm) > 3 * bandm else "between")
            dg = x["I_g_med"] - y["I_g_med"]; bandg = thr.get(r["svc"], {}).get("I_g_step_iqr", np.nan)
            verdict_g = "~" if abs(dg) <= bandg else (">>" if abs(dg) > 3 * bandg else "between")
            srows.append(dict(service=r["svc"], stressed_run=r["label"], twin=tw["label"], group=g_, served_stressed=x["served"], rho=x["rho"],
                              I_twin=y["I_med"], I_twin_iqr=y["I_iqr"], I_stressed=x["I_med"], I_stressed_iqr=x["I_iqr"], delta=d, band=band, verdict=verdict,
                              cores_busy_twin=y["cores_busy"], cores_busy_stressed=x["cores_busy"], n=x["n"],
                              I_marg_twin=y["I_marg_med"], I_marg_stressed=x["I_marg_med"], delta_marg=dm, band_marg=bandm, verdict_marg=verdict_m,
                              I_g_twin=y["I_g_med"], I_g_stressed=x["I_g_med"], delta_g=dg, band_g=bandg, verdict_g=verdict_g))
    if srows:
        with open(os.path.join(a.out, "g1_stressed.csv"), "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(srows[0].keys())); w.writeheader(); w.writerows(srows)
        print("\n== I under one aggressor vs the twin (delta against the service's ~ band; ! = stressed step served/target < 0.95)")
        for s in srows:
            flag = "!" if np.isfinite(s["served_stressed"]) and s["served_stressed"] < 0.95 else " "
            print(f"   {flag} {s['service']:14s} {s['stressed_run']:34s} {s['group']:24s} I twin {s['I_twin']:.3f}  stressed {s['I_stressed']:.3f}  delta {s['delta']:+.3f}  {s['verdict']:7s} | I_marg twin {s['I_marg_twin']:.3f} stressed {s['I_marg_stressed']:.3f} delta {s['delta_marg']:+.3f} {s['verdict_marg']:7s} | I_g twin {s['I_g_twin']:.3f} stressed {s['I_g_stressed']:.3f} delta {s['delta_g']:+.3f} {s['verdict_g']}")

    # ---- collapse windows (steps with served/target < 0.95 in stressed geo / recommendation sweeps and their sweeps)
    crows = []
    for r in runs:
        if r["kind"] != "sweep" or r["svc"] not in ("geo", "recommendation"): continue
        X = r["X"]; band = thr.get(r["svc"], {}).get("I_step_iqr", np.nan)
        slots = max(1, r["k"] - 1)
        for s in sorted(set(X["step"])):
            if s < 0 or not (np.isfinite(X["served"].get(s, np.nan)) and X["served"][s] < 0.95): continue
            m = (X["step"] == s) & X["settled"]
            I = X["I_g_s"][m]; cb = X["U_s"][m] * r["k"]; band = thr.get(r["svc"], {}).get("I_g_step_iqr", np.nan)
            fitI = np.abs(I - 1) <= band; busy = cb >= 0.9 * slots
            crows.append(dict(service=r["svc"], run=r["label"], step=s, rate=X["rate"].get(s), served=X["served"][s], n=int(m.sum()), I_med=float(np.nanmedian(I)),
                              cores_busy_med=float(np.nanmedian(cb)), slots=slots, share_I_approx1=float(np.mean(fitI)), share_slot_busy=float(np.mean(busy)),
                              share_row2=float(np.mean(fitI & busy))))
    if crows:
        with open(os.path.join(a.out, "g1_collapse.csv"), "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(crows[0].keys())); w.writeheader(); w.writerows(crows)
        print("\n== collapse windows (served/target < 0.95): would they fit row 2 (I_g ~ 1 within the service band, cores busy >= 0.9 x slots)?")
        for c in crows:
            print(f"   {c['service']:14s} {c['run']:34s} step {c['step']} ({c['rate']} req/s, served {c['served']:.2f}) n={c['n']:3d}  I {c['I_med']:.3f}  cores busy {c['cores_busy_med']:.2f} of {c['slots']} slot(s)  "
                  f"I~1 {100 * c['share_I_approx1']:.0f} %  slot busy {100 * c['share_slot_busy']:.0f} %  both {100 * c['share_row2']:.0f} %")

    # ---- figure 1: I vs rho on the unstressed runs, one panel per service
    svcs = [s for s in ("synthetic", "reservation", "frontend", "geo", "recommendation", "post-storage-service") if any(r["svc"] == s for r in runs)]
    fig, axs = plt.subplots(2, 3, figsize=(14, 7.5)); axs = axs.ravel()
    cols = plt.rcParams["axes.prop_cycle"].by_key()["color"]
    for ax, svc in zip(axs, svcs):
        for j, r in enumerate([r for r in runs if r["svc"] == svc and not r["twin"]]):
            X = r["X"]; m = X["settled"] & np.isfinite(X["I_s"])
            ax.scatter(X["rho_s"][m], X["I_s"][m], s=4, alpha=0.35, color=cols[j % 10], label=f"{r['label']} ({r['kind']})")
        ax.axhline(1, color="k", lw=0.8); ax.set_ylim(0.3, 4); ax.set_yscale("log"); ax.set_xlim(0, 1.3)
        if svc in thr: ax.axhspan(1 - thr[svc]["I_step_iqr"], 1 + thr[svc]["I_step_iqr"], color="grey", alpha=0.2)
        ax.set_title(svc, loc="left"); ax.set_xlabel("offered utilisation ρ (5 s)"); ax.set_ylabel("I = U ÷ ρ (5 s)"); ax.legend(fontsize=7, loc="upper right")
    for ax in axs[len(svcs):]: ax.axis("off")
    fig.suptitle("Fig. G1-a — service-time inflation I = U ÷ ρ on the unstressed sweeps and twin ramps" + chr(10) + "(U from the recorder's process CPU field, "
                 "RUSAGE_SELF per window; ρ = λc̄ ÷ k from the Phase F costs; 5 s causal smoothing; grey = per-step IQR band)", x=0.01, ha="left", fontsize=10)
    fig.tight_layout(rect=(0, 0, 1, 0.93)); fig.savefig(os.path.join(a.out, "figG1-I-vs-rho-twins.png"), dpi=130); plt.close(fig)
    # ---- figure 1m: the marginal I on the same runs
    fig, axs = plt.subplots(2, 3, figsize=(14, 7.5)); axs = axs.ravel()
    for ax, svc in zip(axs, svcs):
        for j, r in enumerate([r for r in runs if r["svc"] == svc and not r["twin"]]):
            X = r["X"]; m = X["settled"] & np.isfinite(X["I_marg_s"])
            ax.scatter(X["rho_s"][m], X["I_marg_s"][m], s=4, alpha=0.35, color=cols[j % 10], label=f"{r['label']} ({r['kind']})")
        ax.axhline(1, color="k", lw=0.8); ax.axhspan(0.95, 1.05, color="grey", alpha=0.2); ax.set_ylim(0.3, 4); ax.set_yscale("log"); ax.set_xlim(0, 1.3)
        ax.set_title(f"{svc}  (U0 = {thr.get(svc, {}).get('U0_cores', float('nan')):.2f} cores)", loc="left"); ax.set_xlabel("offered utilisation rho (5 s, marginal costs)"); ax.set_ylabel("I_marg = (U - U0) / rho (5 s)"); ax.legend(fontsize=7, loc="upper right")
    for ax in axs[len(svcs):]: ax.axis("off")
    fig.suptitle("Fig. G1-a' (revised Nov 10) - marginal service-time inflation I_marg = (U - U0) / rho on the unstressed sweeps and twin ramps" + chr(10) +
                 "(U = process CPU per window, RUSAGE_SELF; U0 = intercept of the cost regression; rho from the marginal costs; 5 s causal smoothing; grey = +-0.05)", x=0.01, ha="left", fontsize=10)
    fig.tight_layout(rect=(0, 0, 1, 0.93)); fig.savefig(os.path.join(a.out, "figG1-Imarg-vs-rho-twins.png"), dpi=130); plt.close(fig)
    # ---- figure 1g: I_g on the same runs (B.1)
    fig, axs = plt.subplots(2, 3, figsize=(14, 7.5)); axs = axs.ravel()
    for ax, svc in zip(axs, svcs):
        for j, r in enumerate([r for r in runs if r["svc"] == svc and not r["twin"]]):
            X = r["X"]; m = X["settled"] & np.isfinite(X["I_g_s"])
            ax.scatter(X["rho_s"][m], X["I_g_s"][m], s=4, alpha=0.35, color=cols[j % 10], label=f"{r['label']} ({r['kind']})")
        ax.axhline(1, color="k", lw=0.8); ax.axhspan(0.95, 1.05, color="grey", alpha=0.2); ax.set_ylim(0.3, 4); ax.set_yscale("log"); ax.set_xlim(0, 1.3)
        ax.set_title(svc, loc="left"); ax.set_xlabel("offered utilisation rho (5 s)"); ax.set_ylabel("I_g = U / (lambda cbar g(rho)) (5 s)"); ax.legend(fontsize=7, loc="upper right")
    for ax in axs[len(svcs):]: ax.axis("off")
    fig.suptitle("Fig. G1-a'' (Addendum B.1) - I_g = U / [lambda cbar g(rho)] on the unstressed sweeps and twin ramps; g(rho) = CPU per request of the profiling sweep / cbar_sweep" + chr(10) +
                 "(U = process CPU per window, RUSAGE_SELF; 5 s causal smoothing; grey = +-0.05; the profiling sweep of each service is 1 by construction at its step medians)", x=0.01, ha="left", fontsize=10)
    fig.tight_layout(rect=(0, 0, 1, 0.93)); fig.savefig(os.path.join(a.out, "figG1-Ig-vs-rho-twins.png"), dpi=130); plt.close(fig)
    # ---- figure 2: stressed vs twin
    st = [r for r in runs if r["twin"]]
    if st:
        n = len(st); fig, axs = plt.subplots((n + 2) // 3, 3, figsize=(14, 3.6 * ((n + 2) // 3))); axs = np.atleast_1d(axs).ravel()
        for ax, r in zip(axs, st):
            tw = next((q for q in runs if q["label"] == r["twin"]), None)
            for q, col, lab in ((tw, "grey", "twin / unstressed"), (r, "C3", "with aggressor")):
                if q is None: continue
                X = q["X"]; m = X["settled"] & np.isfinite(X["I_s"])
                ax.scatter(X["rho_s"][m], X["I_s"][m], s=4, alpha=0.4, color=col, label=lab)
            ax.axhline(1, color="k", lw=0.8); ax.set_yscale("log"); ax.set_ylim(0.3, 6); ax.set_xlim(0, 1.3)
            ax.set_title(f"{r['svc']}: {r['label']}", loc="left", fontsize=9); ax.set_xlabel("ρ (5 s)"); ax.set_ylabel("I (5 s)"); ax.legend(fontsize=7)
        for ax in axs[n:]: ax.axis("off")
        fig.suptitle("Fig. G1-b — I = U ÷ ρ with one aggressor against the twin" + chr(10) + "(same U, ρ definitions as G1-a: process CPU per window ÷ offered work λc̄, 5 s smoothing)", x=0.01, ha="left", fontsize=10)
        fig.tight_layout(rect=(0, 0, 1, 0.94)); fig.savefig(os.path.join(a.out, "figG1-I-stressed.png"), dpi=130); plt.close(fig)
    print("wrote", a.out)


if __name__ == "__main__":
    main()
