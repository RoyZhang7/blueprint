#!/usr/bin/env python3
"""Phase G A.5 a — the base-term runs: process CPU (recorder `cpu`, RUSAGE_SELF) against the pod cgroup's cpu.stat
sampled from the host (cpustat.tsv: t_ms, then the cpu.stat key/value pairs), per load step; the intercept U0 of
cores busy = U0 + m * offered work over the steps; throttling counters.

Usage: g2a_base.py --run LABEL=DIR [--run ...] [--costs costs.json] [--settle 5]
  The per-second cgroup series is aligned to the recorder windows by time; both are reported as cores.
Writes DIR/g2a/g2a_steps.csv per run (new dir) and prints the comparison table.
"""
import argparse, csv, os, sys
import numpy as np
sys.path.insert(0, os.path.dirname(__file__))
from analyze import load_run
from dictionary import EP_A, costs_of_run, cpuset_size, assign, served_ratio


def load_cpustat(path):
    rows = []
    for line in open(path):
        f = line.rstrip("\n").split("\t")
        if len(f) < 3: continue
        t = int(f[0]); kv = {}
        toks = [x for x in f[1:] if x]
        for tok in toks:
            k, _, v = tok.partition(" ")
            if v: kv[k] = int(v)
        rows.append((t, kv))
    return rows


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--run", action="append", required=True); ap.add_argument("--costs", default=None); ap.add_argument("--settle", type=float, default=5.0)
    a = ap.parse_args()
    print(f"{'run':44s} {'step':>5s} {'rate':>6s} {'served':>6s} {'rho k':>6s} | {'cores busy':>10s} {'cgroup':>8s} {'ratio':>6s} | {'I avg':>6s} | {'throttled':>9s} {'periods':>8s}")
    allfits = []
    for spec in a.run:
        lab, d = spec.split("=", 1)
        R = load_run(d); k = cpuset_size(R["meta"].get("cpuset", "0-7")); c_ms, csrc = costs_of_run(R, a.costs)
        W = R["windows"]; t = np.array([w["t"] for w in W], float); wn = np.array([w.get("w", 100) for w in W], float)
        dt = wn.copy(); dd = np.append(np.diff(t), np.nan); ok = np.isfinite(dd) & (dd >= 0.5 * wn) & (dd <= 3 * wn); dt[ok] = dd[ok]
        cpu = np.array([w.get("cpu", np.nan) for w in W], float); work = np.array([sum(v[EP_A] * c_ms.get(e, 0.0) for e, v in w["ep"].items()) for w in W]) * 1e6
        step_of = [(-1 if s is None else s) for s in assign(W, R["steps"], "step")]; rate = {s["step"]: s["rate"] for s in R["steps"]}
        step_t0 = {s["step"]: s["t"] for s in R["steps"]}
        keep = [i for i, s in enumerate(step_of) if s >= 0]; sv = served_ratio(R, [W[i] for i in keep], [step_of[i] for i in keep])
        cg = load_cpustat(os.path.join(d, "cpustat.tsv")) if os.path.exists(os.path.join(d, "cpustat.tsv")) else []
        cgt = np.array([r[0] for r in cg], float); cgu = np.array([r[1].get("usage_usec", np.nan) for r in cg], float)
        cgthr = np.array([r[1].get("nr_throttled", 0) for r in cg], float); cgper = np.array([r[1].get("nr_periods", 0) for r in cg], float)
        os.makedirs(os.path.join(d, "g2a"), exist_ok=True); out = []
        xs, ys, ycg = [], [], []
        for s in sorted(set(step_of)):
            if s < 0: continue
            m = np.array([(st == s) and (t[i] >= step_t0[s] + a.settle * 1000) for i, st in enumerate(step_of)])
            if m.sum() < 3: continue
            cores = np.nansum(cpu[m]) / 1e6 / np.nansum(dt[m])            # process cores busy over the step
            work_rate = np.nansum(work[m]) / 1e6 / np.nansum(dt[m])      # offered cores (lambda cbar)
            t0, t1 = t[m].min(), t[m].max() + dt[m][-1]
            mc = (cgt >= t0) & (cgt <= t1)
            cgcores = (cgu[mc][-1] - cgu[mc][0]) / 1e6 / ((cgt[mc][-1] - cgt[mc][0]) / 1000) if mc.sum() >= 2 else np.nan
            thr = (cgthr[mc][-1] - cgthr[mc][0]) if mc.sum() >= 2 else np.nan; per = (cgper[mc][-1] - cgper[mc][0]) if mc.sum() >= 2 else np.nan
            row = dict(run=lab, step=s, rate=rate.get(s), served=sv.get(s, np.nan), rho_k=work_rate, cores_busy=cores, cgroup_cores=cgcores,
                       ratio=cgcores / cores if cores else np.nan, I_avg=cores / work_rate if work_rate else np.nan, nr_throttled=thr, nr_periods=per, n=int(m.sum()))
            out.append(row); xs.append(work_rate); ys.append(cores); ycg.append(cgcores)
            print(f"{lab:44s} {s:5d} {rate.get(s, 0):6d} {row['served']:6.2f} {work_rate:6.3f} | {cores:10.3f} {cgcores:8.3f} {row['ratio']:6.3f} | {row['I_avg']:6.3f} | {thr:9.0f} {per:8.0f}")
        with open(os.path.join(d, "g2a", "g2a_steps.csv"), "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(out[0].keys())); w.writeheader(); w.writerows(out)
        if len(xs) >= 2 and max(xs) > 1e-3 * max(ys):   # offered work known (a costs file or configured cpu_ms)
            m1, b1 = np.polyfit(xs, ys, 1); fit = dict(run=lab, k=k, U0_proc=b1, m_proc=m1, costs=csrc)
            if np.all(np.isfinite(ycg)): m2, b2 = np.polyfit(xs, ycg, 1); fit.update(U0_cgroup=b2, m_cgroup=m2)
            allfits.append(fit)
    # no costs for this service on this hardware yet: regress on the arrival rate (slope = marginal CPU ms per request)
    for spec in a.run:
        lab, d = spec.split("=", 1)
        if any(f["run"] == lab for f in allfits): continue
        rows = list(csv.DictReader(open(os.path.join(d, "g2a", "g2a_steps.csv"))))
        rows = [r for r in rows if float(r["served"]) >= 0.95]
        if len(rows) >= 2:
            lam = np.array([float(r["rate"]) for r in rows]); ys = np.array([float(r["cores_busy"]) for r in rows]); ycg = np.array([float(r["cgroup_cores"]) for r in rows])
            m1, b1 = np.polyfit(lam, ys, 1); fit = dict(run=lab, k="?", U0_proc=b1, m_proc=m1 * 1000, costs="no costs: slope = CPU ms per request, served steps")
            if np.all(np.isfinite(ycg)): m2, b2 = np.polyfit(lam, ycg, 1); fit.update(U0_cgroup=b2, m_cgroup=m2 * 1000)
            allfits.append(fit)
    print("\nintercepts (cores busy = U0 + m * offered cores, over the steps):")
    for f in allfits:
        print(f"  {f['run']:44s} k={f['k']}  process: U0 {f['U0_proc']:.3f} cores, slope {f['m_proc']:.3f}" + (f";  cgroup: U0 {f['U0_cgroup']:.3f}, slope {f['m_cgroup']:.3f}" if 'U0_cgroup' in f else "") + f"  ({f['costs']})")


if __name__ == "__main__":
    main()
