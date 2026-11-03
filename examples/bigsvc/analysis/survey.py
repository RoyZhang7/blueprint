#!/usr/bin/env python3
"""Phase F section 2 survey: does a service queue in its handler? One explore sweep -> one row per step and a verdict.

Per step: lambda at the SUT (arrivals at the tap), served/target of the generator (completions per second / target
rate), process CPU per request (recorder "cpu" / arrivals), in-handler latency p50 / p90 (pooled Lat_e, tap -> return
minus blocked), the share of the process CPU that is inside the handler (p50 / CPU per request, CPU-bound handler),
and rho = lambda * c_e / k with c_e = process CPU per request at the lowest step (handoff 3.1, single-endpoint rule)
or from --costs. Then: floor = pooled p50 of the lowest step, floor noise = the lowest step's second-to-second spread
of the pooled p50 (IQR; the dictionary's rule), W_max = max(p50 - floor) over the steps before served/target < 0.95,
wait guard on iff W_max > 3 x noise, and the qualification: p50 above the floor by >= 3 x noise at some step
BEFORE the step where served/target < 0.95 (= a wait regime the dictionary can profile).

Usage: survey.py RUN [--svc NAME] [--costs costs.json] [--settle 3] [--k K] [--label TEXT]
Writes RUN/survey.csv and prints the table + verdict. GORDION_SVC/--svc select <svc>.jsonl of a downstream SUT.
"""
import argparse, csv, json, os, sys
from collections import defaultdict
import numpy as np
sys.path.insert(0, os.path.dirname(__file__))
from analyze import load_run
from dictionary import EP_A, cpuset_size, floor_noise_lowest_step, lat_samples, settled_windows, window_len_s, costs_of_run


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("run"); ap.add_argument("--svc", default=None); ap.add_argument("--costs", default=None)
    ap.add_argument("--settle", type=float, default=3.0); ap.add_argument("--k", type=int, default=None); ap.add_argument("--label", default=None)
    ap.add_argument("--served-min", type=float, default=0.95)
    a = ap.parse_args()
    R = load_run(a.run, svc=a.svc)
    k = a.k or cpuset_size(R["meta"].get("cpuset", "0-7"))
    W, step_of, _ = settled_windows(R, a.settle)
    rate = {s["step"]: s["rate"] for s in R["steps"]}
    # generator side: completions per second per step (served/target)
    rep = defaultdict(lambda: [0.0, 0])
    for r in R["reports"]:
        st = r.get("step", -9)
        if st >= 0 and r["all"][0] > 0:
            rep[st][0] += r["all"][0]; rep[st][1] += 1
    rows = []
    for s in sorted(set(step_of)):
        ws = [w for w, st in zip(W, step_of) if st == s]
        secs = sum(window_len_s(w) for w in ws); arr = sum(w["a"] for w in ws)
        samp = np.concatenate([lat_samples(w, e)[0] for w in ws for e in w["ep"]]) / 1e6 if ws else np.zeros(0)
        cpu = sum(w.get("cpu", 0) for w in ws) / 1e6  # ms
        served = (rep[s][0] / rep[s][1] / rate[s]) if rep.get(s) and rate.get(s) else np.nan
        rows.append(dict(step=s, rate=rate.get(s), lam=arr / secs if secs else np.nan, served=served, cpu_req=cpu / arr if arr else np.nan,
                         busy=cpu / 1000.0 / secs / k if secs else np.nan,
                         p50=float(np.median(samp)) if len(samp) else np.nan, p90=float(np.percentile(samp, 90)) if len(samp) else np.nan, n=len(samp)))
    if a.costs:
        c_ms, _ = costs_of_run(R, a.costs); c_e = float(np.mean(list(c_ms.values()))); csrc = os.path.basename(a.costs)
    else:
        c_e = rows[0]["cpu_req"]; csrc = "process CPU per request at the lowest step"
    gate0 = str(R["meta"].get("gate", "0")) == "1" and R["start"] is not None and R["start"].get("lang") != "cpp"
    slots = int(R["meta"].get("gate_cap") or 0) or (k - 1 if gate0 else k)  # executing slots: k - 1 under the gate
    for r in rows:
        r["rho"] = r["lam"] * c_e / 1000.0 / k
        r["rho_slot"] = r["lam"] * c_e / 1000.0 / slots  # rho' = utilisation of the executing slots
        r["inhandler"] = r["p50"] / r["cpu_req"] if r["cpu_req"] else np.nan
    floor = rows[0]["p50"]
    ok = [r for r in rows if np.isfinite(r["served"]) and r["served"] >= a.served_min]
    first_bad = next((r["step"] for r in rows if np.isfinite(r["served"]) and r["served"] < a.served_min), None)
    # floor noise = the lowest step's own second-to-second spread of the pooled p50 (p95 - p5), as in the dictionary
    noise = floor_noise_lowest_step(W, step_of, rows[0]["step"])
    wmax = max(r["p50"] - floor for r in ok) if ok else 0.0
    qual = [r for r in ok if r["p50"] - floor >= 3 * noise and noise > 0]
    lab = a.label or (a.svc or os.path.basename(a.run))
    print(f"{lab}: k={k}, c_e={c_e:.4f} ms ({csrc}); floor p50 {floor:.4f} ms; floor noise {noise:.4f} ms (lowest step's per-second p50 spread, IQR); "
          f"W_max {wmax:.4f} ms before served/target < {a.served_min} (first such step: {first_bad}); wait guard {'ON' if wmax > 3 * noise else 'off'}")
    print(f"  {'step':>4s} {'target':>7s} {'lambda':>8s} {'served':>6s} {'rho':>6s} {"rho'":>6s} {'busy/k':>6s} {'cpu/req ms':>10s} {'p50 ms':>8s} {'p90 ms':>8s} {'p50-floor':>9s} {'in-handler':>10s}   (rho' = lambda c_e / slots, slots = {slots})")
    for r in rows:
        print(f"  {r['step']:4d} {r['rate']:7.0f} {r['lam']:8.1f} {r['served']:6.3f} {r['rho']:6.3f} {r['rho_slot']:6.3f} {r['busy']:6.3f} {r['cpu_req']:10.4f} {r['p50']:8.4f} {r['p90']:8.4f} {r['p50'] - floor:9.4f} {r['inhandler'] * 100:9.1f}%")
    # knee attribution: at the first step with served/target < served_min (or the last step), was the SUT's CPU near its
    # capacity (own knee) or not (capped by the application's capacity)?
    kn = next((r for r in rows if r["step"] == first_bad), rows[-1])
    # a gated Go service admits cap = k - 1 handlers (GORDION_GATE_CAP overrides): its own capacity is the gated one
    gate = str(R["meta"].get("gate", "0")) == "1" and R["start"] is not None and R["start"].get("lang") != "cpp"
    cap = int(R["meta"].get("gate_cap") or 0) or (k - 1 if gate else k)
    cores = kn["busy"] * k
    own = cores >= 0.8 * cap
    print(f"  knee: {'served/target < %.2f first at step %d' % (a.served_min, first_bad) if first_bad is not None else 'served/target never below %.2f within the schedule (last step %d)' % (a.served_min, rows[-1]['step'])}; "
          f"SUT cores busy there {cores:.2f} of k = {k} (gate cap {cap if gate else 'none'}) -> "
          f"{'the service' + chr(39) + 's OWN capacity (gated slots busy)' if own else 'CAPPED BY THE APPLICATION or the schedule (the SUT below 80 % of its ' + ('gated ' if gate else '') + 'capacity)'}")
    if qual:
        q = qual[0]
        print(f"  VERDICT: qualifies — p50 leaves the floor by {q['p50'] - floor:.4f} ms (>= 3 x {noise:.4f}) at step {q['step']} (rho {q['rho']:.2f}, served/target {q['served']:.3f})")
    else:
        print(f"  VERDICT: does not qualify — p50 - floor <= 3 x floor noise ({3 * noise:.4f} ms) on every step with served/target >= {a.served_min}")
    with open(os.path.join(a.run, "survey.csv"), "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["step", "rate", "lam", "served", "rho", "rho_slot", "busy", "cpu_req", "p50", "p90", "n", "inhandler"]); w.writeheader(); w.writerows(rows)
    print("wrote", os.path.join(a.run, "survey.csv"))


if __name__ == "__main__":
    main()
