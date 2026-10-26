#!/bin/bash
# A1 overhead: same load with the recorder enabled vs GORDION_DISABLE=1; compare client-side p50/p90.
# usage: a1-overhead.sh TAG [rate=1000] [dur=60]
set -e
cd "$(dirname "$0")"
TAG=${1:?tag}; RATE=${2:-1000}; DUR=${3:-60}
export TAG
r_on=$(./exp.sh run a1-on -- -mix uniform -warmup 5 -rate $RATE -duration $DUR | tail -1)
# disabled recorder: patch env via DISABLE=1 (exp.sh passes GORDION_DISABLE)
r_off=$(DISABLE=1 ./exp.sh run a1-off -- -mix uniform -warmup 5 -rate $RATE -duration $DUR | tail -1)
echo "on=$r_on off=$r_off"
python3 - "$r_on" "$r_off" <<'PY'
import json,sys,statistics
for name in sys.argv[1:]:
    p50=[];p90=[]
    for line in open(f"/local/results/{name}/wlgen.jsonl"):
        d=json.loads(line)
        if 'event' in d or d.get('step',-9)<0: continue
        p50.append(d['all'][1]); p90.append(d['all'][2])
    print(f"{name}: client p50 median={statistics.median(p50)/1e3:.1f}us  p90 median={statistics.median(p90)/1e3:.1f}us  (n={len(p50)} s)")
PY
