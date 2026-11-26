#!/usr/bin/env python3
"""K4.5 split (node-0): cut the Primary's per-deploy recorder file (read decompressed from stdin) into one file per run of
the queue. Each run gets the recorder's start event(s) and every 100 ms window whose start lies in [t0 - 2 s, t1 + 2 s]
of its seg.json (t0 is taken before hrgen starts, t1 after it ends). The window start is read from the line prefix, as
k4reduce.py does; the windows are not parsed.
usage: gzip -dc RUN-recommendation.jsonl.gz | k45split.py RESULTS [OUTDIR]
       writes RESULTS/<seg>/recommendation.jsonl.gz, or OUTDIR/<seg>-recommendation.jsonl.gz when OUTDIR is given"""
import bisect, gzip, json, os, sys
root = sys.argv[1]; outdir = sys.argv[2] if len(sys.argv) > 2 else None
segs = [json.loads(l) for l in open(os.path.join(root, "segments.jsonl")) if l.strip()]
iv = sorted((s["t0"] - 2000, s["t1"] + 2000, s["seg"]) for s in segs)
starts = [x[0] for x in iv]
if outdir: os.makedirs(outdir, exist_ok=True)
path = lambda sg: os.path.join(outdir, sg + "-recommendation.jsonl.gz") if outdir else os.path.join(root, sg, "recommendation.jsonl.gz")
out = {x[2]: gzip.open(path(x[2]), "wt") for x in iv}
cnt = {k: 0 for k in out}; heads = 0; bad = 0
for line in sys.stdin:
    if line.startswith('{"event":'):
        heads += 1
        for f in out.values(): f.write(line)
        continue
    if not line.startswith('{"t":') or not line.endswith(chr(10)): bad += 1; continue
    try: t = int(line[5:line.index(",")])
    except Exception: bad += 1; continue
    i = bisect.bisect_right(starts, t) - 1
    for j in (i - 1, i):
        if 0 <= j < len(iv) and iv[j][0] <= t <= iv[j][1]:
            out[iv[j][2]].write(line); cnt[iv[j][2]] += 1
for f in out.values(): f.close()
for s in sorted(cnt): print("split", s, cnt[s], "windows")
print("start events", heads, "lines skipped", bad)
