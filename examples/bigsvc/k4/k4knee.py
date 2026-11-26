#!/usr/bin/env python3
"""Phase K4.1: the Primary's knee from a standalone hrgen explore (wlgen.jsonl of the knee segment).
Per step (first 3 s dropped): served = completions per second / offered; client p50 = median of the per-second p50.
Rule (Phase G's caller rule): the knee is the last step before the first step whose client p50 exceeds 2x the lowest
step's, or whose served falls below 0.95. Writes JSON with the steps, the knee and the three K4.1 loads (0.2, 0.5 and
0.7 of the knee, rounded to 100 req/s). usage: k4knee.py SEG_DIR > knee.json
"""
import json, os, statistics as st, sys

d = sys.argv[1]
L = []
for l in open(os.path.join(d, "wlgen.jsonl")):
    try: L.append(json.loads(l))
    except Exception: pass
sec = [x for x in L if "all" in x and x.get("step", -1) >= 0]
steps = []
for s in sorted({x["step"] for x in sec}):
    xs = [x for x in sec if x["step"] == s][3:]
    if len(xs) < 3: continue
    rate = xs[0]["rate"]
    dt = (xs[-1]["t"] - xs[0]["t"]) / 1000.0
    served = (xs[-1]["done"] - xs[0]["done"]) / dt / rate if dt > 0 and rate else 0.0
    p50 = st.median(x["all"][1] for x in xs if x["all"][0] > 0) / 1000.0
    p99 = st.median(x["all"][3] for x in xs if x["all"][0] > 0) / 1000.0
    steps.append(dict(step=s, rate=rate, served=round(served, 3), p50_ms=round(p50, 3), p99_ms=round(p99, 3)))
base = steps[0]["p50_ms"]
knee, reason = steps[-1]["rate"], "not reached in the sweep"
for i, s in enumerate(steps):
    if s["p50_ms"] > 2 * base or s["served"] < 0.95:
        knee = steps[i - 1]["rate"] if i > 0 else s["rate"]
        reason = f"step {s['step']} ({s['rate']} req/s): p50 {s['p50_ms']} ms vs 2 x {base} ms, served {s['served']}"
        break
loads = {str(f): int(round(f * knee / 100.0)) * 100 for f in (0.2, 0.5, 0.7)}
print(json.dumps(dict(seg=os.path.basename(d.rstrip("/")), knee=knee, reason=reason, base_p50_ms=base, loads=loads, steps=steps), indent=1))
