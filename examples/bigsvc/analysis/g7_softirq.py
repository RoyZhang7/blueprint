#!/usr/bin/env python3
"""Phase G Addendum F.2: softirq on the victim's cores per step, from probe v5's window records (probe.jsonl: ST1 timer,
ST2 net-tx, ST3 net-rx, ST7 sched, ST9 rcu, STo other = ns in softirq per 100 ms window on the cpuset; STn3 = NET_RX
entries; SL3 / SLn3 = raise -> entry latency sum / count for NET_RX; SLc0..4 = that latency by the class running at the
raise; WS0..4 / WSn0..4 = raise -> wake-up delay sum / count of the softirqs that woke a service thread, by the class
running at the raise: 0 own, 1 aggressor, 2 root, 3 other, 4 idle). Per step: ms of softirq per second by vector,
NET_RX entries per second, raise -> entry latency (mean us) overall and when raised under the aggressor, and the wake-up
delays by class (count per second, mean us).

usage: g7_softirq.py RUN [--svc NAME] [--md out.md]
"""
import argparse, json, os, sys
import numpy as np
sys.path.insert(0, os.path.dirname(__file__))
from analyze import load_run
from dictionary import assign


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("run"); ap.add_argument("--svc", default=None); ap.add_argument("--md", default=None); ap.add_argument("--label", default=None)
    a = ap.parse_args(); d = a.run
    R = load_run(d, svc=a.svc); W = R["windows"]; step_of = [(-1 if s is None else s) for s in assign(W, R["steps"], "step")]; rate = {s["step"]: s["rate"] for s in R["steps"]}
    t = np.array([w["t"] for w in W], float)
    P = []
    for line in open(os.path.join(d, "probe.jsonl")):
        try: o = json.loads(line)
        except Exception: continue
        if "ST3" in o: P.append(o)
    pt = np.array([o["t"] for o in P], float)
    out = {}
    for s in sorted(set(step_of)):
        if s < 0: continue
        ws = [i for i, x in enumerate(step_of) if x == s]; ws = ws[50:] if len(ws) > 60 else ws
        t0, t1 = t[ws[0]], t[ws[-1]] + 100
        sel = [o for o in P if t0 <= o["t"] < t1]
        if not sel: continue
        dur = len(sel) * 0.1
        f = lambda k: sum(o.get(k, 0) for o in sel)
        out[s] = dict(rate=rate.get(s, 0), n=len(sel), net_rx=f("ST3") / 1e6 / dur, timer=f("ST1") / 1e6 / dur, rcu=f("ST9") / 1e6 / dur, sched=f("ST7") / 1e6 / dur, other=(f("ST2") + f("STo")) / 1e6 / dur,
                      rx_entries=f("STn3") / dur, rx_lat_us=(f("SL3") / max(1, f("SLn3"))) / 1e3, rx_lat_aggr_us=(f("SLc1") / 1e3 / max(1e-9, f("SLn3") * (f("SLc1") / max(1, f("SL3"))))) if f("SLc1") else 0.0,
                      lat_by_class_ms_s={c: f(f"SLc{c}") / 1e6 / dur for c in range(5)},
                      wake_n_s={c: f(f"WSn{c}") / dur for c in range(5)}, wake_us={c: (f(f"WS{c}") / max(1, f(f"WSn{c}"))) / 1e3 for c in range(5)}, wake_ms_s={c: f(f"WS{c}") / 1e6 / dur for c in range(5)})
    lab = a.label or os.path.basename(d)
    hdr = ["step (req/s)", "softirq ms/s: NET_RX", "timer", "rcu", "sched", "other", "NET_RX entries/s", "raise→entry NET_RX, mean µs", "raise→entry ms/s by class at raise (own/aggr/root/other/idle)", "wake-ups in softirq per s by class (own/aggr/root/other/idle)", "raise→wake-up mean µs by class"]
    lines = [f"**{lab}**", "", "| " + " | ".join(hdr) + " |", "|" + "---|" * len(hdr)]
    for s, v in out.items():
        lines.append("| " + " | ".join([f"{s} ({v['rate']})", f"{v['net_rx']:.1f}", f"{v['timer']:.1f}", f"{v['rcu']:.1f}", f"{v['sched']:.1f}", f"{v['other']:.1f}", f"{v['rx_entries']:.0f}", f"{v['rx_lat_us']:.1f}",
                                         "/".join(f"{v['lat_by_class_ms_s'][c]:.2f}" for c in range(5)), "/".join(f"{v['wake_n_s'][c]:.0f}" for c in range(5)), "/".join(f"{v['wake_us'][c]:.0f}" for c in range(5))]) + " |")
    o = "\n".join(lines); print(o)
    if a.md: open(a.md, "w", encoding="utf-8").write(o + "\n")


if __name__ == "__main__":
    main()
