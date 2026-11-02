#!/usr/bin/env python3
"""B6: how much of the caller-observed wait is visible inside the service, with and without
the caller stamp, per step of a sweep.

Per step (n-weighted medians over endpoints):
    caller:  RTT = load generator p50 (hrgen) or the caller-side recorder (--caller)
    server:  f = stamp -> handler return minus blocked (Lat_e, needs the stamp)
             p = within-service total (transport tap -> return; no stamp needed)
             pp = processing (p minus time blocked on instrumented downstream calls)
             x = stamp -> first in-process timestamp, q = tap -> handler start
    waits relative to the lowest step:  W_caller = RTT - RTT_0,  W_f = f - f_0,  W_p = p - p_0
    visible fraction with stamp = W_f / W_caller, without stamp = W_p / W_caller.
Also answers "does the within-service latency bend in-process": W_pp vs W_caller.

Usage: b6_compare.py RUN [--svc NAME] [--caller NAME] [--settle 5]
"""
import argparse, os, sys
from collections import defaultdict
import numpy as np
sys.path.insert(0, os.path.dirname(__file__))
from analyze import load_run
from dictionary import EP_A, EP_F50, EP_N, EP_NS, EP_P50, EP_PP50, EP_Q50, EP_X50, settled_windows, window_len_s


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("run"); ap.add_argument("--svc", default=None); ap.add_argument("--caller", default=None)
    ap.add_argument("--settle", type=float, default=5.0)
    a = ap.parse_args()
    R = load_run(a.run, svc=a.svc, caller=a.caller)
    W, step_of, _ = settled_windows(R, a.settle)
    step_t0 = {s["step"]: s["t"] for s in R["steps"]}
    rate_of = {s["step"]: s["rate"] for s in R["steps"]}
    rows = []
    for s in sorted(set(step_of)):
        ws = [w for w, st in zip(W, step_of) if st == s]
        acc = defaultdict(list)
        for w in ws:
            for e, v in w["ep"].items():
                if v[EP_N] == 0:
                    continue
                n = v[EP_N]
                acc["p"].extend([v[EP_P50]] * n); acc["pp"].extend([v[EP_PP50]] * n); acc["q"].extend([v[EP_Q50]] * n)
                if len(v) > EP_X50:
                    acc["x"].extend([v[EP_X50]] * n)
                if len(v) > EP_F50 and v[EP_NS] > 0:
                    acc["f"].extend([v[EP_F50]] * v[EP_NS])
        lam = sum(w["a"] for w in ws) / sum(window_len_s(w) for w in ws)
        rtt = []
        for r in R["reports"]:
            if r.get("step", -9) == s and r["t"] >= step_t0[s] + (a.settle + 1) * 1000:
                for e, v in r["ep"].items():
                    if v[0] >= 1:
                        rtt.extend([v[1]] * v[0])
        med = lambda k: float(np.median(acc[k])) / 1e6 if acc[k] else np.nan
        rows.append(dict(step=s, rate=rate_of.get(s), lam=lam, rtt=float(np.median(rtt)) / 1e6 if rtt else np.nan,
                         f=med("f"), p=med("p"), pp=med("pp"), x=med("x"), q=med("q"), mhz=float(np.mean([w["mhz"] for w in ws if "mhz" in w])) if any("mhz" in w for w in ws) else np.nan))
    b = rows[0]
    print(f"{os.path.basename(a.run)} ({a.svc or 'SUT'}; caller = {a.caller or 'load generator'}): per step, ms; waits relative to step {b['step']} ({b['rate']} req/s)")
    print("   step  rate   lambda   MHz | RTT     f       p       pp      x       q    | W_caller  W_f(stamp)  W_p(no stamp)  W_pp  | visible: stamp  no-stamp  in-process-bend")
    for r in rows:
        Wc = r["rtt"] - b["rtt"]; Wf = r["f"] - b["f"]; Wp = r["p"] - b["p"]; Wpp = r["pp"] - b["pp"]
        vis = lambda w: f"{w / Wc:6.2f}" if Wc > 0.05 else "   -  "
        print(f"   {r['step']:3d} {r['rate']!s:>6} {r['lam']:7.0f} {r['mhz']:5.0f} | {r['rtt']:6.2f}  {r['f']:6.3f}  {r['p']:6.3f}  {r['pp']:6.3f}  {r['x']:6.3f}  {r['q']:6.3f} |"
              f" {Wc:8.2f}  {Wf:9.3f}  {Wp:12.3f}  {Wpp:6.3f} |  {vis(Wf)}  {vis(Wp)}   {vis(Wpp)}")
    with open(os.path.join(a.run, "b6.csv"), "w") as f:
        f.write("step,rate,lambda,mhz,rtt_ms,f_ms,p_ms,pp_ms,x_ms,q_ms\n")
        for r in rows:
            f.write(f"{r['step']},{r['rate']},{r['lam']:.1f},{r['mhz']:.0f},{r['rtt']:.4f},{r['f']:.4f},{r['p']:.4f},{r['pp']:.4f},{r['x']:.4f},{r['q']:.4f}\n")


if __name__ == "__main__":
    main()
