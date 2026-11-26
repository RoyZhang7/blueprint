#!/usr/bin/env python3
"""Phase K1: reduce a cell's recorder file to per-arm, per-rep victim latency (pure Python, runs on node-0 where the
recorder files live). For every measured interval [t0, t1] of ctl/cell.jsonl, the recorder windows that lie inside it
contribute their per-request samples: synthetic (bigsvc) = `rawp` (processing stream, tap -> return minus
downstream-blocked, as dictionary.lat_samples with GORDION_LAT=unstamped); SocialNetwork (uid, ps) = `raw` (tap -> end).
Writes <run>/lat_reduced.json: {arm: {rep: {n_samples, requests, dur_s, p50_ms, p95_ms, p99_ms}}}.

usage: k1_reduce.py RUN_DIR [RUN_DIR ...]
"""
import json, os, sys


def pct(xs, q):
    if not xs: return None
    k = (len(xs) - 1) * q; f = int(k); c = min(f + 1, len(xs) - 1)
    return xs[f] + (xs[c] - xs[f]) * (k - f)          # numpy's default linear interpolation


for d in sys.argv[1:]:
    spec = json.load(open(os.path.join(d, "spec.json"))); victim = spec["victim"]
    arms = [json.loads(l) for l in open(os.path.join(d, "ctl", "cell.jsonl"))]
    arms = [a for a in arms if a.get("event") == "arm"]
    iv = sorted((a["t0"], a["t1"], a["arm"], a["rep"]) for a in arms)
    acc = {(a, r): dict(xs=[], requests=0) for _, _, a, r in iv}
    rec = os.path.join(d, "recorder.jsonl")
    key = "rawp" if victim == "syn" else "raw"
    if os.path.exists(rec):
        j = 0
        for line in open(rec):
            if not line.startswith('{"t"'): continue
            try: w = json.loads(line)
            except Exception: continue
            t = w["t"] / 1000.0; te = t + w.get("w", 100) / 1000.0
            while j < len(iv) and iv[j][1] < t: j += 1
            if j >= len(iv): break
            t0, t1, a, r = iv[j]
            if t < t0 or te > t1: continue
            b = acc[(a, r)]; b["requests"] += w.get("n", w.get("a", 0))
            for v in (w.get(key) or {}).values(): b["xs"].extend(v)
    out = {}
    for (t0, t1, a, r) in iv:
        b = acc[(a, r)]; xs = sorted(b["xs"])
        out.setdefault(a, {})[str(r)] = dict(n_samples=len(xs), requests=b["requests"], dur_s=t1 - t0,
                                             p50_ms=(pct(xs, .5) / 1e6 if xs else None), p95_ms=(pct(xs, .95) / 1e6 if xs else None), p99_ms=(pct(xs, .99) / 1e6 if xs else None))
    json.dump(out, open(os.path.join(d, "lat_reduced.json"), "w"), indent=0)
    print(os.path.basename(d), victim, key, {a: round(sum(v["n_samples"] for v in reps.values()) / max(1, len(reps))) for a, reps in out.items()})
