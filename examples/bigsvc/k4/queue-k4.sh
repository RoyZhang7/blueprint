#!/bin/bash
# Phase K4.1 queue (node-0, detached; user go Nov 13). Primary = gordion recommendation on node-4 cores 4,6,8,10 (deployed
# by k4-deploy.sh, recorder on, gate off); Secondary = the motivation CPU burner owning core 12 (namespace k4); HC driven as
# simple-mitigation-1's actuator drives it (k4node.sh); hrgen on node-4's other socket.
#   1. knee: standalone (HC unloaded, no Secondary) explore, 2000 -> 26000 req/s in 15 s steps; k4knee.py picks the knee
#   2. 0.2 / 0.5 / 0.7 of the knee: standalone (HC unloaded, no Secondary) and HC, 30 s warm-up + 300 s each, order
#      S H | H S | S H; then one extra HC run at 0.5 with the burner pinning its own workers (as-is, NOAFF=0)
#   3. end: Secondary scaled to 0, HC loaded as found (Listener up, cpulist 4,6,8,10, Balancer inactive); Primary stays
# Never reads an HC procfs file. A STOP file in /local/results/k4 ends the queue between segments.
set -u
R=/local/results/k4; HRS=/local/hr/hotelReservation/noisy-neighbors/dictionary/hr.sh; B=/var/tmp/k4/bin
N4() { ssh -n -o BatchMode=yes node-4 "$@"; }
export NS=gordion SUT=recommendation NODE=node-4 CPUSET=4,6,8,10 TAG=gordion7 GATE=0 WLGEN_NODE=node-4 WLGEN_CPUS=13,15,17,19,21,23 RESULTS=$R
WARM=${WARM:-30}; MEAS=${MEAS:-300}
log() { echo "=== $(date -u +%H:%M:%S) $*"; }
puid() { kubectl -n gordion get pods -l io.kompose.service=recommendation --field-selector=status.phase=Running -o jsonpath='{.items[0].metadata.uid}'; }
sec_down() { kubectl -n k4 scale deploy/hc-secondary-burner --replicas=0 >/dev/null; for i in $(seq 1 60); do [ -z "$(kubectl -n k4 get pods --no-headers 2>/dev/null)" ] && break; sleep 2; done; }
sec_up() {   # $1 = NOAFF value; prints the pod UID
  kubectl -n k4 set env deploy/hc-secondary-burner NOAFF=$1 >/dev/null
  kubectl -n k4 scale deploy/hc-secondary-burner --replicas=1 >/dev/null
  kubectl -n k4 rollout status deploy/hc-secondary-burner --timeout=180s >/dev/null; sleep 3
  kubectl -n k4 get pods -l app=hc-secondary-burner --field-selector=status.phase=Running -o jsonpath='{.items[0].metadata.uid}'
}
seg() {   # name mode(S|H|Hasis) rate [schedule]
  local name=$1 mode=$2 rate=$3 sched=${4:-}
  [ -f $R/STOP ] && { log "STOP file: skipping $name"; return 1; }
  local s; s="$(date +%Y%m%d-%H%M%S)-k4-$name"; mkdir -p $R/$s
  local suid=""
  if [ $mode = S ]; then
    sec_down; N4 "sudo bash $B/k4node.sh hc-unload" > $R/$s/hc.log 2>&1
  else
    N4 "sudo bash $B/k4node.sh hc-load" > $R/$s/hc.log 2>&1
    sec_down; suid=$(sec_up $([ $mode = Hasis ] && echo 0 || echo 1))
    N4 "sudo bash $B/k4node.sh sec-pin $suid" >> $R/$s/hc.log 2>&1; sleep 2
    N4 "sudo LOGDIR=/mnt/extra/logs/hc/$s bash $B/k4node.sh hc-on $suid" >> $R/$s/hc.log 2>&1 || log "$s: hc-on FAILED (see hc.log)"
  fi
  local pu; pu=$(puid)
  [ -z "$sched" ] && sched="$rate:$MEAS"
  local secs; secs=$(python3 -c "print(int(sum(float(x.split(':')[1]) for x in '$sched'.split(',')) + $WARM + 20))")
  N4 "sudo nohup python3 $B/k4sample.py --out /var/tmp/k4/$s-sample.jsonl --secs $secs --primary-uid $pu ${suid:+--secondary-uid $suid} < /dev/null > /var/tmp/k4/$s-sample.log 2>&1 &"
  local t0; t0=$(date +%s%3N)
  log "$s: mode $mode schedule $sched (sampler $secs s, secondary ${suid:-none})"
  bash $HRS wlgen $s -- -mix single:recommend -fixed-params -k 4 -warmup $WARM -schedule "$sched" > $R/$s/load.log 2>&1 < /dev/null
  local t1; t1=$(date +%s%3N)
  for i in $(seq 1 40); do N4 "pgrep -f '[k]4sample.py --out /var/tmp/k4/$s'" > /dev/null || break; sleep 2; done
  scp -q -o BatchMode=yes node-4:/var/tmp/k4/$s-sample.jsonl $R/$s/sample.jsonl
  if [ $mode != S ]; then
    N4 "sudo bash $B/k4node.sh hc-off" >> $R/$s/hc.log 2>&1
    scp -q -r -o BatchMode=yes node-4:/mnt/extra/logs/hc/$s $R/$s/balancer 2>/dev/null
  fi
  echo "{\"seg\":\"$s\",\"name\":\"$name\",\"mode\":\"$mode\",\"rate\":$rate,\"schedule\":\"$sched\",\"warm\":$WARM,\"t0\":$t0,\"t1\":$t1,\"secondary_uid\":\"$suid\",\"primary_uid\":\"$pu\"}" | tee -a $R/segments.jsonl > $R/$s/seg.json
  echo $s > $R/last-seg.txt
  log "$s done: $(grep -c '"all"' $R/$s/wlgen.jsonl 2>/dev/null) client seconds, $(grep -c '"aff"' $R/$s/sample.jsonl 2>/dev/null) aff samples; last: $(tail -2 $R/$s/wlgen.jsonl | head -1 | cut -c1-160)"
}

log "K4.1 queue start; Primary run $(cat $R/primary-run.txt)"
kubectl get ns k4 >/dev/null 2>&1 || kubectl create ns k4 >/dev/null
kubectl apply -f ~/k4/k4-secondary.yaml >/dev/null

# 1. knee
seg knee S 0 "2000:15,4000:15,6000:15,8000:15,10000:15,12000:15,14000:15,16000:15,18000:15,20000:15,22000:15,24000:15,26000:15"
K=$R/$(cat $R/last-seg.txt)
python3 ~/k4/k4knee.py $K > $R/knee.json 2> $R/knee.err; cp $R/knee.json $K/
L02=$(python3 -c "import json; print(json.load(open('$R/knee.json'))['loads']['0.2'])")
L05=$(python3 -c "import json; print(json.load(open('$R/knee.json'))['loads']['0.5'])")
L07=$(python3 -c "import json; print(json.load(open('$R/knee.json'))['loads']['0.7'])")
log "knee $(python3 -c "import json; k=json.load(open('$R/knee.json')); print(k['knee'], '|', k['reason'])"); loads $L02 / $L05 / $L07"

# 2. the three loads, standalone and HC interleaved
seg r02-S S $L02;  seg r02-H H $L02
seg r05-H H $L05;  seg r05-S S $L05
seg r07-S S $L07;  seg r07-H H $L07
seg r05-Hasis Hasis $L05

# 3. end state
sec_down; N4 "sudo bash $B/k4node.sh hc-load" > $R/end-hc.log 2>&1; cat $R/end-hc.log
log DONE-K4.1
