#!/usr/bin/env python3
"""Phase G section 2: join the scheduler probe's 100 ms records (probe.jsonl, keyed by unix ms) with the recorder's
windows, and write the new per-window columns beside the existing ones (new file <run>/g2/g2_windows.csv; nothing
overwritten): U (cores busy from the cgroup's usage_usec; the recorder's cpu field beside it), R_foreign_ms, R_self_ms,
R_other_ms (root + other + unknown holders, reported, never attributed), Q_ms (C++: probe futex-sleep; Go: the
recorder's rt mutex wait), G_ms (Go: rt GC CPU), sched_p50/p90 (Go runtime wait), preempt, multi, nr_throttled,
probe_self_cores. Per-step summary printed; the probe's own CPU per step for the overhead claim (C6).

usage: g2_probe.py RUN_DIR [--svc recommendation] [--smooth 5]
"""
import argparse, csv, json, os, sys
import numpy as np
sys.path.insert(0, os.path.dirname(__file__))
from analyze import load_run
from dictionary import assign, cpuset_size, served_ratio


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("run"); ap.add_argument("--svc", default=None); ap.add_argument("--smooth", type=float, default=5.0)
    a = ap.parse_args(); d = a.run
    R = load_run(d, svc=a.svc); W = R["windows"]; k = cpuset_size(R["meta"].get("cpuset", "0-7"))
    t = np.array([w["t"] for w in W], float); wn = np.array([w.get("w", 100) for w in W], float)
    dt = wn.copy(); dd = np.append(np.diff(t), np.nan); ok = np.isfinite(dd) & (dd >= 0.5 * wn) & (dd <= 3 * wn); dt[ok] = dd[ok]
    cpu = np.array([w.get("cpu", np.nan) for w in W], float)
    rt = [w.get("rt") for w in W]; sched = [w.get("sched") for w in W]
    # probe records
    P = []; selfrec = []
    pf = os.path.join(d, "probe.jsonl")
    if os.path.exists(pf):
        for line in open(pf):
            try: o = json.loads(line)
            except Exception: continue
            if o.get("event") == "self": selfrec.append(o)
            elif "Rself" in o: P.append(o)
    P.sort(key=lambda o: o["t"]); pt = np.array([o["t"] for o in P], float)
    def psum(key, t0, t1):
        if not len(pt): return np.nan
        i0, i1 = np.searchsorted(pt, t0), np.searchsorted(pt, t1); return float(sum(o.get(key, 0) for o in P[i0:i1])) if i1 > i0 else 0.0
    def pcg(key, t0, t1):
        if not len(pt): return np.nan
        i0, i1 = np.searchsorted(pt, t0), np.searchsorted(pt, t1)
        if i1 - i0 < 1 or i1 >= len(P): return np.nan
        return float(P[i1][key] - P[i0][key]) if key in P[i1] and key in P[i0] else np.nan
    st = np.array([o["t"] for o in selfrec], float); sc = np.array([o["cpu_ns"] for o in selfrec], float)
    def pself(t0, t1):
        # the self records are 1 s apart: rate over the +-5 s around the window (the 10 ms tick granularity needs it)
        if len(st) < 2: return np.nan
        i0, i1 = np.searchsorted(st, t0 - 5000), np.searchsorted(st, t1 + 5000)
        i0 = max(0, min(i0, len(st) - 2)); i1 = min(len(st) - 1, max(i1, i0 + 1))
        return (sc[i1] - sc[i0]) / 1e9 / ((st[i1] - st[i0]) / 1000)
    step_of = [(-1 if s is None else s) for s in assign(W, R["steps"], "step")]; rate = {s["step"]: s["rate"] for s in R["steps"]}
    keep = [i for i, s in enumerate(step_of) if s >= 0]; sv = served_ratio(R, [W[i] for i in keep], [step_of[i] for i in keep])
    os.makedirs(os.path.join(d, "g2"), exist_ok=True); rows = []
    for i, w in enumerate(W):
        t0, t1 = t[i], t[i] + dt[i]
        ucg = pcg("usage_usec", t0, t1); U = ucg / 1e6 / (dt[i] / 1000) / k if np.isfinite(ucg) else np.nan
        row = dict(t=int(t[i]), dt_ms=int(dt[i]), step=step_of[i], a=w["a"], cpu_cores=cpu[i] / 1e6 / dt[i], U=U,
                   R_foreign_ms=psum("Raggr", t0, t1) / 1e6, R_self_ms=psum("Rself", t0, t1) / 1e6,
                   R_other_ms=(psum("Rroot", t0, t1) + psum("Rother", t0, t1) + psum("Runk", t0, t1)) / 1e6,
                   RL_foreign_ms=psum("Laggr", t0, t1) / 1e6, RL_self_ms=psum("Lself", t0, t1) / 1e6, mig=psum("mig", t0, t1),   # v2: last-holder credit kept beside the time-share one
                   occ_self_ms=psum("Oself", t0, t1) / 1e6, occ_foreign_ms=psum("Oaggr", t0, t1) / 1e6, occ_root_ms=psum("Oroot", t0, t1) / 1e6, occ_other_ms=psum("Oother", t0, t1) / 1e6,   # v3: CPU time of the cpuset held by each class
                   occ_idle_ms=psum("Oidle", t0, t1) / 1e6,   # v4: idle charged to its own class (v3 had it inside occ_root)
                   occ_sib_self_ms=psum("Sself", t0, t1) / 1e6, occ_sib_foreign_ms=psum("Saggr", t0, t1) / 1e6, occ_sib_root_ms=psum("Sroot", t0, t1) / 1e6, occ_sib_other_ms=psum("Sother", t0, t1) / 1e6, occ_sib_idle_ms=psum("Sidle", t0, t1) / 1e6,   # v4 (a): the cpuset's SMT siblings
                   W_own_futex_ms=psum("Wf0", t0, t1) / 1e6, W_own_other_ms=psum("Wo0", t0, t1) / 1e6, W_aggr_ms=(psum("Wf1", t0, t1) + psum("Wo1", t0, t1)) / 1e6, W_root_ms=(psum("Wf2", t0, t1) + psum("Wo2", t0, t1)) / 1e6,
                   W_other_ms=(psum("Wf3", t0, t1) + psum("Wo3", t0, t1)) / 1e6, W_idle_ms=(psum("Wf4", t0, t1) + psum("Wo4", t0, t1)) / 1e6, W_idle_futex_ms=psum("Wf4", t0, t1) / 1e6,   # v4 (b): sleep time by waker class
                   W_own_n=psum("Wfn0", t0, t1) + psum("Won0", t0, t1), W_idle_n=psum("Wfn4", t0, t1) + psum("Won4", t0, t1),
                   R_n=psum("nself", t0, t1) + psum("naggr", t0, t1) + psum("nroot", t0, t1) + psum("nother", t0, t1) + psum("nunk", t0, t1),
                   Q_futex_ms=psum("Qlock", t0, t1) / 1e6, Q_idle_ms=psum("Qidle", t0, t1) / 1e6, preempt=psum("preempt", t0, t1), multi=psum("multi", t0, t1),
                   nr_throttled=pcg("nr_throttled", t0, t1), probe_self_cores=pself(t0, t1),
                   G_ms=(rt[i][0] / 1e6 if rt[i] else np.nan), G_stw_ms=(rt[i][1] / 1e6 if rt[i] else np.nan), Q_mutex_ms=(rt[i][2] / 1e6 if rt[i] else np.nan),
                   sched_p50_ms=(sched[i][0] / 1e6 if sched[i] else np.nan), sched_p90_ms=(sched[i][1] / 1e6 if sched[i] else np.nan))
        rows.append(row)
    with open(os.path.join(d, "g2", "g2_windows.csv"), "w", newline="") as f:
        wr = csv.DictWriter(f, fieldnames=list(rows[0].keys())); wr.writeheader(); wr.writerows(rows)
    print(f"{os.path.basename(d)}: {len(W)} windows, {len(P)} probe records, k={k}")
    print(f"{'step':>5s} {'rate':>6s} {'served':>6s} {'U cg':>6s} {'U rus':>6s} | {'R_for':>7s} {'R_self':>7s} {'R_oth':>7s} {'R/win':>6s} | {'Qfutex':>7s} {'Qidle':>8s} {'Qmutex':>7s} {'Gcpu':>6s} | {'preem':>5s} {'multi':>5s} | {'probe cores':>11s}")
    for s in sorted(set(step_of)):
        if s < 0: continue
        m = [r for r in rows if r["step"] == s][50:] if sum(1 for r in rows if r["step"] == s) > 60 else [r for r in rows if r["step"] == s]
        med = lambda key: float(np.nanmedian([r[key] for r in m])) if m else np.nan
        tot = lambda key: float(np.nansum([r[key] for r in m])) / max(1, len(m)) if m else np.nan
        print(f"{s:5d} {rate.get(s, 0):6d} {sv.get(s, np.nan):6.2f} {med('U'):6.3f} {med('cpu_cores') / k:6.3f} | {tot('R_foreign_ms'):7.3f} {tot('R_self_ms'):7.3f} {tot('R_other_ms'):7.3f} {tot('R_n'):6.1f} | {tot('Q_futex_ms'):7.2f} {tot('Q_idle_ms'):8.1f} {med('Q_mutex_ms'):7.3f} {med('G_ms'):6.3f} | {tot('preempt'):5.1f} {tot('multi'):5.1f} | {med('probe_self_cores'):11.4f} | occ self/foreign {tot('occ_self_ms'):5.1f}/{tot('occ_foreign_ms'):5.1f} ms")
    print("(R, Q, preempt, multi: mean per 100 ms window of the step; ms of delay per window; probe cores = bpftrace + wrapper CPU as a fraction of one core)")
    if any(r["occ_sib_foreign_ms"] > 0 or r["occ_sib_idle_ms"] > 0 for r in rows):
        print(f"{'step':>5s} {'phi':>6s} {'phi_sib':>7s} | {'Wown_f':>7s} {'Wown_o':>7s} {'Waggr':>6s} {'Wroot':>6s} {'Woth':>5s} {'Widle':>6s}   (v4: phi = occ_foreign / (k dt); phi_sib = sibling occupancy by the aggressor cgroup / (k dt); W* = ms of sleep per window ended by each waker class)")
        for s in sorted(set(step_of)):
            if s < 0: continue
            m = [r for r in rows if r["step"] == s][50:] if sum(1 for r in rows if r["step"] == s) > 60 else [r for r in rows if r["step"] == s]
            if not m: continue
            tot = lambda key: float(np.nansum([r[key] for r in m])) / max(1, len(m))
            dtm = float(np.mean([r["dt_ms"] for r in m]))
            print(f"{s:5d} {tot('occ_foreign_ms') / (k * dtm):6.3f} {tot('occ_sib_foreign_ms') / (k * dtm):7.3f} | {tot('W_own_futex_ms'):7.2f} {tot('W_own_other_ms'):7.2f} {tot('W_aggr_ms'):6.2f} {tot('W_root_ms'):6.2f} {tot('W_other_ms'):5.2f} {tot('W_idle_ms'):6.2f}")


if __name__ == "__main__":
    main()
