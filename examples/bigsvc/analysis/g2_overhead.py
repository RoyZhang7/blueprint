#!/usr/bin/env python3
"""Phase G section 2.3 (claim C6): probe overhead from the paired unstressed uniform sweeps, probe off vs on.
Per step: served/target, pooled p50 / p90 of the service latency, process cores busy, CPU per request (ms), the
probe's own CPU (on-run, from its self records via g2_windows.csv); per endpoint: lowest-step floor p50 off vs on
(median ratio and the share within +-5 %); knee = first step with served/target < 0.95 or pooled p50 > 2x the lowest step.

usage: g2_overhead.py OFF_RUN ON_RUN [--settle 5]
"""
import argparse, csv, os, sys
import numpy as np
sys.path.insert(0, os.path.dirname(__file__))
from analyze import load_run
from dictionary import EP_A, EP_P50, assign, cpuset_size, lat_samples, served_ratio, settled_windows


def steps_of(d, settle):
    R = load_run(d); k = cpuset_size(R["meta"].get("cpuset", "0-7")); names = R["run"]["endpoints"]
    W, step_of, _ = settled_windows(R, settle)
    keep = [i for i, s in enumerate(step_of) if s >= 0]; sv = served_ratio(R, [W[i] for i in keep], [step_of[i] for i in keep])
    rate = {s["step"]: s["rate"] for s in R["steps"]}
    out = {}; floors = {}
    low = min(s for s in step_of if s >= 0)
    for s in sorted(set(step_of)):
        if s < 0: continue
        ws = [w for w, st in zip(W, step_of) if st == s]
        samp = np.concatenate([lat_samples(w, e)[0] for w in ws for e in w["ep"]]) / 1e6
        cpu = sum(w.get("cpu", 0) for w in ws); secs = sum(w.get("w", 100) for w in ws) / 1000; arr = sum(w["a"] for w in ws)
        out[s] = dict(rate=rate[s], served=sv.get(s, np.nan), p50=float(np.median(samp)), p90=float(np.percentile(samp, 90)), cores=cpu / 1e9 / secs, cpu_ms_req=cpu / 1e6 / max(1, arr), n=len(samp))
        if s == low:
            for e in names:
                x = np.concatenate([lat_samples(w, e)[0] for w in ws if e in w["ep"]]) / 1e6 if any(e in w["ep"] for w in ws) else np.array([])
                if len(x) >= 20: floors[e] = float(np.median(x))
    return out, floors, k


def knee(st):
    base = st[min(st)]["p50"]
    for s in sorted(st):
        if st[s]["served"] < 0.95 or st[s]["p50"] > 2 * base: return st[s]["rate"], s
    return st[max(st)]["rate"], max(st)


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("off"); ap.add_argument("on"); ap.add_argument("--settle", type=float, default=5.0)
    a = ap.parse_args()
    off, fl_off, k = steps_of(a.off, a.settle); on, fl_on, _ = steps_of(a.on, a.settle)
    pself = {}
    g2 = os.path.join(a.on, "g2", "g2_windows.csv")
    if os.path.exists(g2):
        rows = list(csv.DictReader(open(g2)))
        for s in set(int(r["step"]) for r in rows):
            v = [float(r["probe_self_cores"]) for r in rows if int(r["step"]) == s and r["probe_self_cores"] not in ("", "nan")]
            pself[s] = float(np.median(v)) if v else np.nan
    print(f"{'step':>4s} {'rate':>5s} | {'served off/on':>13s} | {'p50 off':>8s} {'p50 on':>8s} {'ratio':>6s} | {'p90 off':>8s} {'p90 on':>8s} | {'cores off':>9s} {'cores on':>8s} {'+cores':>7s} | {'ms/req off':>10s} {'ms/req on':>9s} | {'probe cores':>11s}")
    for s in sorted(set(off) & set(on)):
        o, n = off[s], on[s]
        print(f"{s:4d} {o['rate']:5d} | {o['served']:6.2f} {n['served']:6.2f} | {o['p50']:8.3f} {n['p50']:8.3f} {n['p50']/o['p50']:6.3f} | {o['p90']:8.2f} {n['p90']:8.2f} | {o['cores']:9.3f} {n['cores']:8.3f} {n['cores']-o['cores']:+7.3f} | {o['cpu_ms_req']:10.3f} {n['cpu_ms_req']:9.3f} | {pself.get(s, np.nan):11.4f}")
    ko, kn = knee(off), knee(on)
    print(f"knee (served < 0.95 or p50 > 2x lowest): off {ko[0]} req/s (step {ko[1]}), on {kn[0]} req/s (step {kn[1]})")
    common = [e for e in fl_off if e in fl_on]
    r = np.array([fl_on[e] / fl_off[e] for e in common])
    print(f"per-endpoint floor p50 (lowest step), on / off over {len(common)} endpoints: median {np.median(r):.3f}, IQR [{np.percentile(r,25):.3f}, {np.percentile(r,75):.3f}], within +-5 %: {100*np.mean(np.abs(r-1) <= 0.05):.0f} %, within +-10 %: {100*np.mean(np.abs(r-1) <= 0.10):.0f} %")
    top = max(s for s in pself if np.isfinite(pself[s])) if pself else None
    if top is not None: print(f"probe's own CPU: median over steps {np.nanmedian(list(pself.values())):.4f} cores, at the highest step {pself[top]:.4f} cores ({off[top]['rate']} req/s)")


if __name__ == "__main__":
    main()
