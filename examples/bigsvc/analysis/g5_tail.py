#!/usr/bin/env python3
"""Phase G (v) / G5-lite: per-request rows for the Thrift service (thread = request). Joins the recorder's raw samples
(raw = total ns, rawp = processing, rawq = queue before the handler, rawt = kernel thread id; image g5+) with the
probe's per-thread run-queue delay (probe-hist.jsonl, @Rthr per second per tid) and the second's holder split (probe
windows: R_foreign / R_self / R_other shares). Per request: queue_ms, proc_ms, total_ms, run_delay_ms = the thread's R in
that second split equally over the requests the thread served in that second, foreign share of it from the second's
R split. Tail: requests above the window's p95 of total, per step; dominant component = argmax(queue, run delay
foreign, run delay self/other, rest of processing).

usage: g5_tail.py RUN_DIR [--svc post-storage-service] [--q 0.95]
Writes RUN_DIR/g5/requests.csv (one row per request with a thread id) and prints per-step tail shares.
"""
import argparse, csv, json, os, sys
import numpy as np
sys.path.insert(0, os.path.dirname(__file__))
from analyze import load_run
from dictionary import assign


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("run"); ap.add_argument("--svc", default=None); ap.add_argument("--q", type=float, default=0.95)
    a = ap.parse_args(); d = a.run
    R = load_run(d, svc=a.svc); W = R["windows"]
    step_of = [(-1 if s is None else s) for s in assign(W, R["steps"], "step")]; rate = {s["step"]: s["rate"] for s in R["steps"]}
    # per-thread R per second from the hist file
    Rthr = {}
    hf = os.path.join(d, "probe-hist.jsonl")
    if os.path.exists(hf):
        for line in open(hf):
            try: o = json.loads(line)
            except Exception: continue
            if o.get("kind") == "map" and "@Rthr" in o.get("data", {}):
                sec = int(o["t"] // 1000)
                for tid, v in o["data"]["@Rthr"].items():
                    if int(v): Rthr[(sec, int(tid))] = Rthr.get((sec, int(tid)), 0) + int(v)   # v3.2 keeps exited threads at 0: skip them
    # per-second holder split from the probe windows
    split = {}
    pf = os.path.join(d, "probe.jsonl")
    if os.path.exists(pf):
        for line in open(pf):
            try: o = json.loads(line)
            except Exception: continue
            if "Rself" in o:
                sec = int(o["t"] // 1000); s_ = split.setdefault(sec, [0, 0, 0])
                s_[0] += o.get("Raggr", 0); s_[1] += o.get("Rself", 0); s_[2] += o.get("Rroot", 0) + o.get("Rother", 0) + o.get("Runk", 0)
    reqs = []
    for w, st in zip(W, step_of):
        if "rawt" not in w: continue
        sec = int(w["t"] // 1000)
        for e in w["raw"]:
            tot = w["raw"][e]; proc = w.get("rawp", {}).get(e, []); que = w.get("rawq", {}).get(e, []); tid = w["rawt"].get(e, [])
            for i in range(min(len(tot), len(tid))):
                reqs.append(dict(t=w["t"], sec=sec, step=st, ep=e, tid=int(tid[i]), total_ms=tot[i] / 1e6, proc_ms=(proc[i] / 1e6 if i < len(proc) else np.nan), queue_ms=(que[i] / 1e6 if i < len(que) else np.nan)))
    if not reqs:
        print("no rawt samples in", d, "(image < g5 or RAW=0)"); return
    # requests per (sec, tid) for the equal split
    cnt = {}
    for r in reqs: cnt[(r["sec"], r["tid"])] = cnt.get((r["sec"], r["tid"]), 0) + 1
    for r in reqs:
        rt = Rthr.get((r["sec"], r["tid"]), 0) / 1e6; n = cnt[(r["sec"], r["tid"])]
        r["run_delay_ms"] = rt / n
        sp = split.get(r["sec"]); tot = sum(sp) if sp else 0
        r["run_foreign_ms"] = r["run_delay_ms"] * (sp[0] / tot) if tot else 0.0
        r["run_self_ms"] = r["run_delay_ms"] - r["run_foreign_ms"]
        # C++ recorder: total = tap -> preWrite; proc = total - blocked (blocked = downstream store / Thrift calls, which
        # include the wake-up after the reply); queue = decode + dispatch, inside proc
        proc = r["proc_ms"] if np.isfinite(r["proc_ms"]) else r["total_ms"]
        r["blocked_ms"] = max(0.0, r["total_ms"] - proc)
        r["rest_ms"] = max(0.0, proc - (r["queue_ms"] if np.isfinite(r["queue_ms"]) else 0.0) - r["run_delay_ms"])
    os.makedirs(os.path.join(d, "g5"), exist_ok=True)
    with open(os.path.join(d, "g5", "requests.csv"), "w", newline="") as f:
        wr = csv.DictWriter(f, fieldnames=list(reqs[0].keys())); wr.writeheader(); wr.writerows(reqs)
    threads = sorted(set(r["tid"] for r in reqs)); print(f"{os.path.basename(d)}: {len(reqs)} requests with thread ids on {len(threads)} threads; per-thread R seconds known: {len(Rthr)}")
    print(f"{'step':>4s} {'rate':>6s} {'n':>7s} {'p95 ms':>8s} | tail (>= p95 of the step), medians: {'total':>7s} {'queue':>7s} {'run for':>8s} {'run self':>8s} {'blocked':>8s} {'rest':>7s}  dominant | R>0 on | all requests: {'queue':>7s} {'run':>6s} {'blocked':>7s} {'rest':>6s} | R>0 on")
    for st in sorted(set(r["step"] for r in reqs)):
        if st < 0: continue
        sel = [r for r in reqs if r["step"] == st]; tot = np.array([r["total_ms"] for r in sel]); p95 = np.percentile(tot, 100 * a.q)
        tail = [r for r in sel if r["total_ms"] >= p95]
        comp = {k: float(np.median([r[k] for r in tail])) for k in ("queue_ms", "run_foreign_ms", "run_self_ms", "blocked_ms", "rest_ms")}
        shares = {k: v / max(1e-9, sum(comp.values())) for k, v in comp.items()}
        dom = max(shares, key=shares.get)
        allc = {k: float(np.median([r[k] for r in sel])) for k in ("queue_ms", "run_delay_ms", "blocked_ms", "rest_ms")}
        rt = 100 * np.mean([r["run_delay_ms"] > 0 for r in tail]); ra = 100 * np.mean([r["run_delay_ms"] > 0 for r in sel])
        print(f"{st:4d} {rate.get(st, 0):6d} {len(sel):7d} {p95:8.2f} | {float(np.median([r['total_ms'] for r in tail])):7.2f} {comp['queue_ms']:7.3f} {comp['run_foreign_ms']:8.3f} {comp['run_self_ms']:8.3f} {comp['blocked_ms']:8.3f} {comp['rest_ms']:7.3f}  {dom[:-3]:11s} {100*shares[dom]:3.0f} % | {rt:4.0f} % | {allc['queue_ms']:7.3f} {allc['run_delay_ms']:6.3f} {allc['blocked_ms']:7.3f} {allc['rest_ms']:6.3f} | {ra:4.1f} %")


if __name__ == "__main__":
    main()
