#!/bin/bash
# Phase K4.2f queue (node-0, detached, attended; user go Nov 15). HC's own reserve controller left free between a floor of 1
# and a cap of 3 (then 4): the Primary message's targetIdleCores = static_targetIdleCores = TIC (start and cap), LOW_TIC = 1
# in the message and the Balancer's environment (the floor), the Balancer's argv target = TIC. Everything else as K4.2e:
# shipped placement (Monitor CPU 26, Balancer CPU 24, shipped binary), burner BestEffort, 5 000 req/s, 30 s warm-up + 300 s,
# lean probe in every run. Phases, one per call: start = HR redeployed with recorders on (new run), 60 s pre-warm,
# bpf_stats 1, S0 (twin) then cap3 (TIC 3, LOW_TIC 1); cap4 = one run with TIC 4, LOW_TIC 1; end = burner 0, HC loaded as
# found (TIC and LOW_TIC back to the driver's default 1 on the next start), bpf_stats 0, recorders off.
# Never reads an HC procfs file. STOP file: /local/results/k42f/STOP.
set -u
R=/local/results/k42f; HRD=/local/hr/hotelReservation/noisy-neighbors/dictionary; HRS=$HRD/hr.sh; B=/var/tmp/k4/bin2
mkdir -p $R
N4() { ssh -n -o BatchMode=yes node-4 "$@"; }
export NS=gordion SUT=recommendation NODE=node-4 CPUSET=4,6,8,10 TAG=gordion7 GATE=0 WLGEN_NODE=node-4 WLGEN_CPUS=13,15,17,19,21,23 RESULTS=$R
WARM=${WARM:-30}; MEAS=${MEAS:-300}; RATE=${RATE:-5000}
log() { echo "=== $(date -u +%H:%M:%S) $*"; }
puid() { kubectl -n gordion get pods -l io.kompose.service=recommendation --field-selector=status.phase=Running -o jsonpath='{.items[0].metadata.uid}'; }
sec_down() { kubectl -n k4 scale deploy/hc-secondary-burner --replicas=0 >/dev/null; for i in $(seq 1 60); do [ -z "$(kubectl -n k4 get pods --no-headers 2>/dev/null)" ] && break; sleep 2; done; }
sec_up() {   # prints the pod UID
  kubectl -n k4 scale deploy/hc-secondary-burner --replicas=1 >/dev/null
  kubectl -n k4 rollout status deploy/hc-secondary-burner --timeout=180s >/dev/null; sleep 3
  kubectl -n k4 get pods -l app=hc-secondary-burner --field-selector=status.phase=Running -o jsonpath='{.items[0].metadata.uid}'
}
seg() {   # name mode(S|H) tic low_tic
  local name=$1 mode=$2 tic=$3 low=$4 rate=$RATE
  [ -f $R/STOP ] && { log "STOP file: skipping $name"; return 1; }
  local s; s="$(date +%Y%m%d-%H%M%S)-k42f-$name"; mkdir -p $R/$s
  local suid="" hd=/mnt/extra/logs/hc/$s
  case $mode in
    S) sec_down; N4 "sudo bash $B/k4node.sh hc-unload" > $R/$s/hc.log 2>&1 ;;
    H) N4 "sudo bash $B/k4node.sh hc-load" > $R/$s/hc.log 2>&1; sec_down; suid=$(sec_up)
       N4 "sudo bash $B/k4node.sh sec-pin $suid" >> $R/$s/hc.log 2>&1; sleep 2
       N4 "sudo TIC=$tic LOW_TIC=$low LOGDIR=$hd bash $B/k4node.sh hc-on $suid" >> $R/$s/hc.log 2>&1 || log "$s: hc-on FAILED (see hc.log)" ;;
  esac
  local pu; pu=$(puid)
  N4 "sudo bash $B/k4probe.sh start $s $pu $suid" > $R/$s/probe.log 2>&1 || log "$s: probe start FAILED (see probe.log)"
  local secs=$((MEAS + WARM + 20))
  N4 "sudo nohup python3 $B/k4sample.py --out /var/tmp/k4/$s-sample.jsonl --secs $secs --primary-uid $pu ${suid:+--secondary-uid $suid} < /dev/null > /var/tmp/k4/$s-sample.log 2>&1 &"
  local t0; t0=$(date +%s%3N)
  log "$s: mode $mode TIC $tic LOW_TIC $low rate $rate (secondary ${suid:-none})"
  bash $HRS wlgen $s -- -mix single:recommend -fixed-params -k 4 -warmup $WARM -schedule "$rate:$MEAS" > $R/$s/load.log 2>&1 < /dev/null
  local t1; t1=$(date +%s%3N)
  for i in $(seq 1 40); do N4 "pgrep -f '[k]4sample.py --out /var/tmp/k4/$s'" > /dev/null || break; sleep 2; done
  N4 "sudo bash $B/k4probe.sh stop $s" >> $R/$s/probe.log 2>&1
  for f in sample probe probe-hist; do scp -q -o BatchMode=yes node-4:/var/tmp/k4/$s-$f.jsonl $R/$s/$f.jsonl 2>/dev/null; done
  if [ $mode = C ] || [ $mode = H ]; then
    N4 "sudo bash $B/k4node.sh hc-off" >> $R/$s/hc.log 2>&1
    mkdir -p $R/$s/balancer; scp -q -o BatchMode=yes node-4:$hd/stats.log node-4:$hd/util.log node-4:$hd/dynamic.log $R/$s/balancer/ 2>/dev/null
  fi
  echo "{\"seg\":\"$s\",\"name\":\"$name\",\"mode\":\"$mode\",\"tic\":\"$tic\",\"low_tic\":\"$low\",\"reserve\":\"$tic/$low\",\"rate\":$rate,\"schedule\":\"$rate:$MEAS\",\"warm\":$WARM,\"t0\":$t0,\"t1\":$t1,\"secondary_uid\":\"$suid\",\"primary_uid\":\"$pu\",\"balancer_dir\":\"$hd\"}" | tee -a $R/segments.jsonl > $R/$s/seg.json
  log "$s done: $(grep -c '"all"' $R/$s/wlgen.jsonl 2>/dev/null) client seconds, $(grep -c '^{"t"' $R/$s/probe.jsonl 2>/dev/null) probe windows, $(grep -c '"aff"' $R/$s/sample.jsonl 2>/dev/null) aff samples; last: $(tail -2 $R/$s/wlgen.jsonl | head -1 | cut -c1-140)"
}

PHASE=${1:-start}
if [ "$PHASE" = start ]; then
  log "K4.2f queue start"
  run="$(date +%Y%m%d-%H%M%S)-k42f-primary"; echo $run > $R/primary-run.txt
  (cd $HRD && NS=gordion SUT=recommendation NODE=node-4 CPUSET=4,6,8,10 TAG=gordion7 GATE=0 OTHER_NODES=node-1,node-2 WINDOW_MS=100 RAW=1 RESULTS=$R bash hr.sh deploy $run) > $R/deploy.log 2>&1
  log "HR redeployed with recorders on, run $run: $(kubectl -n gordion get pods -o wide --no-headers | awk '/^recommendation-/ {print $1, $3, $7}')"
  kubectl get ns k4 >/dev/null 2>&1 || kubectl create ns k4 >/dev/null
  kubectl apply -f ~/k4/k4b-secondary.yaml >/dev/null
  N4 "sudo sysctl -q -w kernel.bpf_stats_enabled=1; sysctl kernel.bpf_stats_enabled"
  sec_down; N4 "sudo bash $B/k4node.sh hc-unload" > $R/prewarm-hc.log 2>&1
  bash $HRS wlgen $(date +%Y%m%d-%H%M%S)-k42f-prewarm -- -mix single:recommend -fixed-params -k 4 -warmup 0 -schedule "2000:60" > $R/prewarm.log 2>&1 < /dev/null
  log "pre-warm done: $(tail -1 $R/prewarm.log | cut -c1-120)"
  seg S0 S - -
  seg cap3 H 3 1
  log DONE-K4.2f-start
fi
if [ "$PHASE" = cap4 ]; then
  seg cap4 H 4 1
  log DONE-K4.2f-cap4
fi
if [ "$PHASE" = end ]; then
  run=$(cat $R/primary-run.txt)
  sec_down; N4 "sudo bash $B/k4node.sh hc-load" > $R/end-hc.log 2>&1; cat $R/end-hc.log
  N4 "sudo sysctl -q -w kernel.bpf_stats_enabled=0; sysctl kernel.bpf_stats_enabled; pgrep -a k1lean || echo 'no probe left'"
  for d in $(kubectl -n gordion get deploy -o jsonpath='{range .items[*]}{.metadata.name}={range .spec.template.spec.containers[0].env[*]}{.name}:{.value},{end}{"\n"}{end}' | grep "GORDION_OUT:/data/$run-" | cut -d= -f1); do
    kubectl -n gordion set env deploy/$d GORDION_DISABLE=1 >/dev/null && echo "recorder off: $d"
  done
  log DONE-K4.2f
fi
