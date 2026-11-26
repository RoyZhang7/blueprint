#!/usr/bin/env python3
"""Phase K4.1 reduction, run where the big files live (pure Python, no numpy).
  k4reduce.py windows RESULTS_K4 [--sub N] > windows.json
      per segment (segments.jsonl): the measured window [start of hrgen step 0, last per-second record of the schedule], ms;
      with --sub N also N equal sub-windows per segment, named "<name>#<k>" (K4.1b: one recorder pass for both)
  k4reduce.py recorder RECORDER_JSONL windows.json > recorder_reduced.json            (node-4)
      the Primary's server-side recorder (100 ms windows, GORDION_RAW=1) pooled over each measured window: requests,
      errors, p50/p95/p99 of total (tap -> return), full (caller's send stamp -> return) and queue (tap -> handler);
      process CPU (cores) and mean MHz
  k4reduce.py balancer RESULTS_K4 windows.json OFFSET_S [LOGROOT] > balancer_reduced.json   (node-0, or node-4 with
      LOGROOT=/mnt/extra/logs/hc where K4.1b leaves the Balancer's logs: LOGROOT/<seg>/balancer.log)
      HC's own record of what it lent: balancer.log rows (CLOCK_MONOTONIC_RAW; OFFSET_S = wall - raw at run time) give the
      number of Primary cores in the Secondary's mask after every rebalance; time-weighted mean and time share of 0..4 cores
      over the measured window, rebalances per second
"""
import json, os, sys


def pct(xs, q):
    if not xs: return None
    k = (len(xs) - 1) * q; f = int(k); c = min(f + 1, len(xs) - 1)
    return xs[f] + (xs[c] - xs[f]) * (k - f)


def windows(root):
    out = []
    for l in open(os.path.join(root, "segments.jsonl")):
        s = json.loads(l); d = os.path.join(root, s["seg"]); t0 = t1 = None
        for x in open(os.path.join(d, "wlgen.jsonl")):
            try: r = json.loads(x)
            except Exception: continue
            if r.get("event") == "step" and r.get("step") == 0 and t0 is None: t0 = r["t"]
            if "all" in r and r.get("step", -1) >= 0 and r.get("rate", 0) > 0: t1 = r["t"]
        out.append(dict(seg=s["seg"], name=s["name"], mode=s["mode"], rate=s["rate"], t0_ms=t0, t1_ms=t1))
    return out


def recorder(path, wins):
    # windows in non-overlapping layers (main windows; "<name>#k" sub-windows), found per line by bisection
    import bisect
    layers = {}
    for w in wins:
        if w["t0_ms"]: layers.setdefault("#" in w["seg"], []).append((w["t0_ms"], w["t1_ms"], w["seg"]))
    for v in layers.values(): v.sort()
    starts = {k: [x[0] for x in v] for k, v in layers.items()}
    acc = {x[2]: dict(tot=[], full=[], q=[], n=0, e=0, a=0, cpu=0, mhz=[], nwin=0) for v in layers.values() for x in v}
    for line in open(path):
        if not line.startswith('{"t":'): continue
        try: t = int(line[5:line.index(",")])
        except Exception: continue
        hits = []
        for k, iv in layers.items():
            i = bisect.bisect_right(starts[k], t) - 1
            if i >= 0 and t >= iv[i][0] and t <= iv[i][1]: hits.append(iv[i])
        if not hits: continue
        try: r = json.loads(line)
        except Exception: continue
        te = t + r.get("w", 100)
        for t0, t1, sg in hits:
            if te > t1: continue
            b = acc[sg]; b["n"] += r.get("n", 0); b["e"] += r.get("e", 0); b["a"] += r.get("a", 0); b["cpu"] += r.get("cpu", 0) or 0; b["nwin"] += 1
            if r.get("mhz"): b["mhz"].append(r["mhz"])
            for k, dst in (("raw", "tot"), ("rawf", "full"), ("rawq", "q")):
                for v in (r.get(k) or {}).values(): b[dst].extend(v)
    out = {}
    for v in layers.values():
        for t0, t1, sg in v:
            b = acc[sg]; dur = (t1 - t0) / 1000.0; o = dict(requests=b["n"], errors=b["e"], arrivals=b["a"], dur_s=dur, windows=b["nwin"],
                                                           cpu_cores=b["cpu"] / 1e9 / dur if dur else None, mhz=(sum(b["mhz"]) / len(b["mhz"]) if b["mhz"] else None))
            for k in ("tot", "full", "q"):
                xs = sorted(b[k]); o[k + "_n"] = len(xs)
                for q, nm in ((.5, "p50"), (.95, "p95"), (.99, "p99"), (.999, "p999")):
                    o[f"{k}_{nm}_ms"] = pct(xs, q) / 1e6 if xs else None
                o[k + "_mean_ms"] = sum(xs) / len(xs) / 1e6 if xs else None
            out[sg] = o
    return out


def balancer(root, wins, off, logroot=None):
    out = {}
    for w in wins:
        if "#" in w["seg"]: continue
        p = os.path.join(logroot, w["seg"], "balancer.log") if logroot else os.path.join(root, w["seg"], "balancer", "balancer.log")
        if not os.path.exists(p) or not w["t0_ms"]: continue
        a, b = w["t0_ms"] / 1000.0 - off, w["t1_ms"] / 1000.0 - off      # window in the Balancer's clock
        share = [0.0] * 5; last_t = None; last_c = 0; n_ev = 0; first = last = None
        for line in open(p):
            f = line.rstrip("\n").split(",")
            if len(f) < 10 or not f[0].isdigit(): continue
            t = int(f[0]) + int(f[1]) / 1e9; c = int(f[9])
            first = t if first is None else first; last = t
            if t <= a: last_c = c; continue
            if last_t is None: last_t = a
            if t >= b: break
            share[min(last_c, 4)] += t - last_t; last_t = t; last_c = c; n_ev += 1
        if last_t is not None: share[min(last_c, 4)] += b - last_t
        tot = sum(share) or 1.0
        out[w["seg"]] = dict(lent_mean=sum(i * s for i, s in enumerate(share)) / tot, share=[s / tot for s in share], rebalances_per_s=n_ev / (b - a),
                             log_first_wall=(first + off) if first else None, log_last_wall=(last + off) if last else None, window=[w["t0_ms"], w["t1_ms"]])
    return out


if __name__ == "__main__":
    cmd = sys.argv[1]
    if cmd == "windows":
        ws = windows(sys.argv[2])
        if "--sub" in sys.argv:
            n = int(sys.argv[sys.argv.index("--sub") + 1]); sub = []
            for w in ws:
                if not w["t0_ms"] or w["name"] == "knee": continue
                d = (w["t1_ms"] - w["t0_ms"]) / n
                sub += [dict(w, seg=f"{w['name']}#{k}", t0_ms=int(w["t0_ms"] + k * d), t1_ms=int(w["t0_ms"] + (k + 1) * d)) for k in range(n)]
            ws += sub
        print(json.dumps(ws, indent=1))
    elif cmd == "recorder": print(json.dumps(recorder(sys.argv[2], json.load(open(sys.argv[3]))), indent=1))
    elif cmd == "balancer": print(json.dumps(balancer(sys.argv[2], json.load(open(sys.argv[3])), float(sys.argv[4]), sys.argv[5] if len(sys.argv) > 5 else None), indent=1))
