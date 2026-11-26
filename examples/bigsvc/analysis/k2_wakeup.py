#!/usr/bin/env python3
"""Phase K2 (analysis only): how much of a neighbor's effect the run-queue delay by holder attributes when the waits are
timed by the sched_switch hook alone, and when the wake-up-to-run delay is added (sched_wakeup on, or the probe's
per-resume kind).

A service thread's run-queue wait starts either at a preemption (switched out still runnable: the sched_switch hook sees
both ends) or at a wake-up (the start is only visible to sched_wakeup). "Switch hook alone" = the waits that start at a
preemption, credited by time share; "with wake-up" = all waits, credited by time share (Phase G probes v2-v5 time both
kinds; v5 tags each resume with its kind, "w").

Neighbor's effect and shares:
  Run A (Addendum F, probe v5 per-resume, per-request join as analysis/g7_tail.py): per step, the growth stressed - twin of
    the mean arrival-stream latency (wake-up + server time), over all requests and over the tail (>= the step's p95);
    attributed = the growth of the requests' aggressor-held run-queue delay: inside the span after a preemption
    (switch alone), plus inside the span after a sleep and at the arrival wake-up (with wake-up).
  Scenarios i and iii (Phase G, probe v3 per-window): per served second, the truth amount truth_ext x Lat (ms per request,
    from the twin; results/g-final/e1/<scenario>-e1.csv) against R_foreign / lambda (ms per request, all waits: v3's
    sched_wakeup handler snapshots the holders at the wake-up, so its R already includes the wake-up-to-run delay).
    v3 does not tag the kind of a wait. The switch-alone part is estimated per load step by non-negative least squares of
    the per-second foreign delay on the per-second counts of waits that began at a preemption (preempt) and at a wake-up
    (resumes timed - preempt); the estimator is first scored on Runs A and B, where v5's per-resume kinds give the truth.
usage: k2_wakeup.py [--out results/k2]
"""
import argparse, bisect, collections, csv, json, os, sys
import numpy as np
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from g7_tail import load_resumes
from analyze import load_run
from dictionary import assign

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RES = os.path.join(HERE, "results")
RUN_A = ("sn/20261112-075458-f-a-uid-twin", "sn/20261112-075852-f-a-uid-duty20", "unique-id-service")
RUN_B = ("sn/20261112-080249-f-b-ps-twin", "sn/20261112-081133-f-b-ps-stress1", "post-storage-service")
V3 = {"i": ("20261111-035721-g2c-i-ramp-mid7-stress1", "g-final/e1/i-e1.csv"),
      "iii": ("hr/20261111-041108-g2c-iii-rec-stress2", "g-final/e1/iii-e1.csv")}


def requests_by_kind(d, svc, R, offset_ns):
    """per request: arrival-stream total and the aggressor-held run-queue delay by kind (ms)"""
    Rn = load_run(d, svc=svc); W = Rn["windows"]
    step_of = [(-1 if s is None else s) for s in assign(W, Rn["steps"], "step")]; rate = {s["step"]: s["rate"] for s in Rn["steps"]}
    Rt = {t: np.array([x[0] for x in v]) for t, v in R.items()}
    out = []
    for w, st in zip(W, step_of):
        if st < 0 or "rawe" not in w or "rawt" not in w: continue
        for e in w["raw"]:
            tot = w["raw"][e]; tid = w["rawt"].get(e, []); end = w["rawe"].get(e, [])
            for i in range(min(len(tot), len(tid), len(end))):
                e_ns = end[i] * 1000 - offset_ns; s_ns = e_ns - tot[i]; t = int(tid[i])
                f_pre = f_wake = f_arr = d_arr = 0.0
                if t in Rt:
                    arr = Rt[t]; lo = bisect.bisect_left(arr, s_ns); hi = bisect.bisect_right(arr, e_ns)
                    for x in R[t][lo:hi]:
                        if x[5] == 1: f_wake += x[2]
                        else: f_pre += x[2]
                    if lo > 0:
                        x = R[t][lo - 1]
                        if x[5] == 1 and s_ns - x[0] <= 20e6: f_arr = x[2]; d_arr = x[1]
                out.append(dict(step=st, req_s=rate.get(st, 0), total=tot[i] / 1e6, arr_total=(tot[i] + d_arr) / 1e6,
                                f_pre=f_pre / 1e6, f_wake=f_wake / 1e6, f_arr=f_arr / 1e6))
    return out


def run_v5(pair, label):
    rows = []
    data = {}
    for lab, d in (("twin", pair[0]), ("stressed", pair[1])):
        d = os.path.join(RES, d); R, K, start = load_resumes(d)
        data[lab] = requests_by_kind(d, pair[2], R, start["offset_ns"])
    for st in sorted(set(r["step"] for r in data["stressed"])):
        T = [r for r in data["twin"] if r["step"] == st]; S = [r for r in data["stressed"] if r["step"] == st]
        if not T or not S: continue
        for sel in ("all", "tail"):
            def pick(g, key):
                if sel == "all": return g
                cut = np.percentile([r[key] for r in g], 95); return [r for r in g if r[key] >= cut]
            for stream, key in (("arrival", "arr_total"), ("server", "total")):
                t_, s_ = pick(T, key), pick(S, key)
                m = lambda g, k: float(np.mean([r[k] for r in g]))
                effect = m(s_, key) - m(t_, key)
                sw = m(s_, "f_pre") - m(t_, "f_pre")
                wk = sw + (m(s_, "f_wake") - m(t_, "f_wake")) + ((m(s_, "f_arr") - m(t_, "f_arr")) if stream == "arrival" else 0.0)
                rows.append(dict(scenario=label, data="probe v5, per request", step=st, req_s=S[0]["req_s"], requests=sel, stream=stream,
                                 n=len(s_), effect_ms=effect, switch_ms=sw, wake_ms=wk,
                                 switch_share=(sw / effect) if abs(effect) > 1e-9 else None, wake_share=(wk / effect) if abs(effect) > 1e-9 else None))
    return rows


def per_second_v5(d):
    """per second of a v5 run: preempt, timed resumes, foreign delay (window sums) and the truth by kind (per-resume)"""
    W = collections.defaultdict(lambda: dict(P=0, N=0, Rf=0.0))
    for line in open(os.path.join(d, "probe.jsonl")):
        try: o = json.loads(line)
        except Exception: continue
        if "Raggr" not in o or "t" not in o: continue
        s = int(o["t"] // 1000); w = W[s]
        w["P"] += o["preempt"]; w["N"] += o["nself"] + o["naggr"] + o["nroot"] + o["nother"]; w["Rf"] += o["Raggr"]
    T = collections.defaultdict(lambda: [0.0, 0.0])
    for line in open(os.path.join(d, "probe-resume.jsonl")):
        try: o = json.loads(line)
        except Exception: continue
        if "r" not in o or "t" not in o: continue
        a = o["a"] if "a" in o else (o["d"] if o.get("h") == 1 else 0)
        T[int(o["t"] // 1000)][1 if o.get("w", 1) == 1 else 0] += a
    return W, T


def per_second_v3(d):
    W = collections.defaultdict(lambda: dict(P=0, N=0, Rf=0.0))
    for line in open(os.path.join(d, "probe.jsonl")):
        try: o = json.loads(line)
        except Exception: continue
        if "Raggr" not in o or "t" not in o: continue
        s = int(o["t"] // 1000); w = W[s]
        w["P"] += o["preempt"]; w["N"] += o["nself"] + o["naggr"] + o["nroot"] + o["nother"]; w["Rf"] += o["Raggr"]
    return W


def nnls2(x1, x2, y):
    """y ~ a x1 + b x2 with a, b >= 0 (two-column NNLS by enumeration)"""
    X = np.column_stack([x1, x2]); best = None
    for cols in ((0, 1), (0,), (1,)):
        A = X[:, cols]
        if not np.any(A): continue
        c, *_ = np.linalg.lstsq(A, y, rcond=None)
        if np.any(c < 0): continue
        full = np.zeros(2); full[list(cols)] = c; r = float(np.sum((X @ full - y) ** 2))
        if best is None or r < best[0]: best = (r, full)
    return best[1] if best else np.zeros(2)


def estimate(W, steps):
    """per step: fitted foreign delay per preemption-started and per wake-started wait; returns {second: est_pre_ns}"""
    est = {}; fits = {}
    for st, secs in steps.items():
        secs = [s for s in secs if s in W]
        if len(secs) < 5: continue
        P = np.array([W[s]["P"] for s in secs], float); N = np.array([W[s]["N"] for s in secs], float); Y = np.array([W[s]["Rf"] for s in secs], float)
        Wk = np.clip(N - P, 0, None); a = nnls2(P, Wk, Y); fits[st] = dict(a_pre_us=a[0] / 1e3, a_wake_us=a[1] / 1e3, n=len(secs), P=float(P.mean()), Wk=float(Wk.mean()))
        for s, p in zip(secs, P): est[s] = a[0] * p
    return est, fits


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--out", default=os.path.join(RES, "k2")); a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)
    rows = run_v5(RUN_A, "Addendum F run A (unique-id + 20 % duty-cycled hog)")
    rows += [r for r in run_v5(RUN_B, "reference: Addendum F run B (post-storage + hog)") if r["step"] <= 2]   # steps 0-2: complete records (RESULTS-G 7.4)
    # estimator check on v5 runs (A and B): truth by kind from the per-resume records
    val = []
    for lab, pair in (("A", RUN_A), ("B", RUN_B)):
        d = os.path.join(RES, pair[1]); Wv, Tv = per_second_v5(d)
        Rn = load_run(d, svc=pair[2]); sec2step = {}
        for w, st in zip(Rn["windows"], assign(Rn["windows"], Rn["steps"], "step")):
            if st is not None: sec2step[int(w["t"] // 1000)] = st
        steps = collections.defaultdict(list)
        for s in sorted(Wv):
            if s in sec2step: steps[sec2step[s]].append(s)
        est, fits = estimate(Wv, steps)
        for st, ss in steps.items():
            tr_pre = sum(Tv[s][0] for s in ss); tr_wk = sum(Tv[s][1] for s in ss); e_pre = sum(est.get(s, 0.0) for s in ss); rf = sum(Wv[s]["Rf"] for s in ss)
            val.append(dict(run=lab, step=st, seconds=len(ss), Rf_ms=rf / 1e6, truth_pre_ms=tr_pre / 1e6, truth_wake_ms=tr_wk / 1e6, est_pre_ms=e_pre / 1e6,
                            truth_pre_frac=(tr_pre / (tr_pre + tr_wk)) if (tr_pre + tr_wk) else None, est_pre_frac=(e_pre / rf) if rf else None, **{k: fits.get(st, {}).get(k) for k in ("a_pre_us", "a_wake_us")}))
    # v3 scenarios
    for sc, (run, e1) in V3.items():
        d = os.path.join(RES, run); W = per_second_v3(d)
        E = list(csv.DictReader(open(os.path.join(RES, e1))))
        steps = collections.defaultdict(list)
        for r in E: steps[int(r["step"])].append(int(r["t"]))
        est, fits = estimate(W, steps)
        for sel in ("served", "ext-truth"):
            G = [r for r in E if (sel == "served" or r["truth"] == "ext")]
            eff = sum(float(r["truth_ext"]) * float(r["lat"]) for r in G)
            wk = sum(W[int(r["t"])]["Rf"] / 1e6 / float(r["lam"]) for r in G if int(r["t"]) in W and float(r["lam"]) > 0)
            sw = sum(est.get(int(r["t"]), 0.0) / 1e6 / float(r["lam"]) for r in G if float(r["lam"]) > 0)
            n = len(G)
            # v3 does not tag a wait's kind and the per-step estimator fails its check on runs A and B (k2_estimator.csv):
            # the switch-alone share is reported as its bound, at most the with-wake-up share; sw is kept for the record
            rows.append(dict(scenario={"i": "Phase G i (synthetic, mix ramp, 1 hog on 1 of 4 cores)", "iii": "Phase G iii (recommendation, 1 hog per core)"}[sc],
                             data="probe v3, per second", step="all", req_s="", requests=sel, stream="server (p50 truth)", n=n,
                             effect_ms=eff / n if n else None, switch_ms=None, wake_ms=wk / n if n else None,
                             switch_share=None, wake_share=(wk / eff) if eff else None, switch_share_bound=(wk / eff) if eff else None,
                             switch_est_unvalidated=(sw / eff) if eff else None))
        for st, f_ in sorted(fits.items()):
            val.append(dict(run=sc, step=st, seconds=f_["n"], a_pre_us=f_["a_pre_us"], a_wake_us=f_["a_wake_us"]))
    for nm, data in (("k2_shares.csv", rows), ("k2_estimator.csv", val)):
        keys = []
        for r in data:
            for k in r:
                if k not in keys: keys.append(k)
        with open(os.path.join(a.out, nm), "w", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=keys); w.writeheader(); w.writerows(data)
    for r in rows:
        print({k: (round(v, 4) if isinstance(v, float) else v) for k, v in r.items()})
    print("--- estimator")
    for r in val:
        print({k: (round(v, 4) if isinstance(v, float) else v) for k, v in r.items()})


if __name__ == "__main__":
    main()
