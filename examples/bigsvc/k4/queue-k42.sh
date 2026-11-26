#!/bin/bash
# Phase K4.2 queue (node-0, detached; user go Nov 14). K4.1b's configuration (Primary = recommendation on node-4 cores
# 4,6,8,10; burner BestEffort on core 12; lean probe in every run) at 5 000 req/s only, HC moved towards the paper's
# set-up one change at a time. Every step runs with the Monitor (CPU 26) and the Balancer on one core (BAL_CPU=26):
#   a  Monitor and Balancer on CPU 26, shipped Balancer binary
#   b  a + the paper's 60 s Observation Phase (bin2/obs/balancer, k4/hc-patch/observe60.py, -DOBSERVE60)
#   c  a + BALANCE_INTERRUPTS (bin2/irq/balancer, k4/hc-patch/irqfix.py) + the IRQ procfs path written by the driver
#      (eno2's IRQs -> 12,14 as the authors' make_irq_aware.sh does; IRQ_AWARE=1 in k4node.sh)
#   d  a + b + c (bin2/obsirq/balancer, IRQ_AWARE=1)
# Per step an HC + burner run (H) and an HC-only run (C, Monitor polling plus an idle, spinning Balancer), 300 s each
# after a 90 s warm-up (the observation phase of b and d ends before the measured window); a standalone twin (S) at the
# start and at the end. Order S0 | a-H a-C | b-C b-H | c-H c-C | d-C d-H | S1.
# Start: HR redeployed with recorders on (new run name), 60 s pre-warm, bpf_stats 1. End: burner 0, HC loaded as found
# with the shipped binary and placement (Balancer inactive; the next start uses CPU 24), IRQ affinities checked against the
# saved originals, bpf_stats 0, recorders off. Never reads an HC procfs file. STOP file: /local/results/k42/STOP.
set -u
R=/local/results/k42; HRD=/local/hr/hotelReservation/noisy-neighbors/dictionary; HRS=$HRD/hr.sh; B=/var/tmp/k4/bin2
SHIPPED=/local/HarvestContainers/TestFramework/bin/balancer
mkdir -p $R
N4() { ssh -n -o BatchMode=yes node-4 "$@"; }
export NS=gordion SUT=recommendation NODE=node-4 CPUSET=4,6,8,10 TAG=gordion7 GATE=0 WLGEN_NODE=node-4 WLGEN_CPUS=13,15,17,19,21,23 RESULTS=$R
WARM=${WARM:-90}; MEAS=${MEAS:-300}; RATE=${RATE:-5000}
log() { echo "=== $(date -u +%H:%M:%S) $*"; }
puid() { kubectl -n gordion get pods -l io.kompose.service=recommendation --field-selector=status.phase=Running -o jsonpath='{.items[0].metadata.uid}'; }
sec_down() { kubectl -n k4 scale deploy/hc-secondary-burner --replicas=0 >/dev/null; for i in $(seq 1 60); do [ -z "$(kubectl -n k4 get pods --no-headers 2>/dev/null)" ] && break; sleep 2; done; }
sec_up() {
  kubectl -n k4 scale deploy/hc-secondary-burner --replicas=1 >/dev/null
  kubectl -n k4 rollout status deploy/hc-secondary-burner --timeout=180s >/dev/null; sleep 3
  kubectl -n k4 get pods -l app=hc-secondary-burner --field-selector=status.phase=Running -o jsonpath='{.items[0].metadata.uid}'
}
stepenv() {   # step -> environment for k4node.sh
  case $1 in
    a) echo "BAL_CPU=26 BAL_BIN=$SHIPPED IRQ_AWARE=0" ;;
    b) echo "BAL_CPU=26 BAL_BIN=$B/obs/balancer IRQ_AWARE=0" ;;
    c) echo "BAL_CPU=26 BAL_BIN=$B/irq/balancer IRQ_AWARE=1" ;;
    d) echo "BAL_CPU=26 BAL_BIN=$B/obsirq/balancer IRQ_AWARE=1" ;;
  esac
}
seg() {   # name mode(S|C|H) step(a|b|c|d|-)
  local name=$1 mode=$2 step=$3
  [ -f $R/STOP ] && { log "STOP file: skipping $name"; return 1; }
  local s; s="$(date +%Y%m%d-%H%M%S)-k42-$name"; mkdir -p $R/$s
  local suid="" hd=/mnt/extra/logs/hc/$s env; env=$(stepenv $step)
  case $mode in
    S) sec_down; N4 "sudo bash $B/k4node.sh hc-unload" > $R/$s/hc.log 2>&1 ;;
    C) sec_down; N4 "sudo bash $B/k4node.sh hc-load" > $R/$s/hc.log 2>&1
       N4 "sudo $env LOGDIR=$hd bash $B/k4node.sh hc-on-nosec" >> $R/$s/hc.log 2>&1 || log "$s: hc-on-nosec FAILED (see hc.log)" ;;
    H) N4 "sudo bash $B/k4node.sh hc-load" > $R/$s/hc.log 2>&1; sec_down; suid=$(sec_up)
       N4 "sudo bash $B/k4node.sh sec-pin $suid" >> $R/$s/hc.log 2>&1; sleep 2
       N4 "sudo $env LOGDIR=$hd bash $B/k4node.sh hc-on $suid" >> $R/$s/hc.log 2>&1 || log "$s: hc-on FAILED (see hc.log)" ;;
  esac
  local pu; pu=$(puid)
  N4 "sudo bash $B/k4probe.sh start $s $pu $suid" > $R/$s/probe.log 2>&1 || log "$s: probe start FAILED (see probe.log)"
  local secs=$((MEAS + WARM + 20))
  N4 "sudo nohup python3 $B/k4sample.py --out /var/tmp/k4/$s-sample.jsonl --secs $secs --primary-uid $pu ${suid:+--secondary-uid $suid} < /dev/null > /var/tmp/k4/$s-sample.log 2>&1 &"
  local t0; t0=$(date +%s%3N)
  log "$s: mode $mode step $step rate $RATE (secondary ${suid:-none}; $env)"
  bash $HRS wlgen $s -- -mix single:recommend -fixed-params -k 4 -warmup $WARM -schedule "$RATE:$MEAS" > $R/$s/load.log 2>&1 < /dev/null
  local t1; t1=$(date +%s%3N)
  for i in $(seq 1 40); do N4 "pgrep -f '[k]4sample.py --out /var/tmp/k4/$s'" > /dev/null || break; sleep 2; done
  N4 "sudo bash $B/k4probe.sh stop $s" >> $R/$s/probe.log 2>&1
  for f in sample probe probe-hist; do scp -q -o BatchMode=yes node-4:/var/tmp/k4/$s-$f.jsonl $R/$s/$f.jsonl 2>/dev/null; done
  if [ $mode != S ]; then
    N4 "sudo bash $B/k4node.sh hc-off" >> $R/$s/hc.log 2>&1
    mkdir -p $R/$s/balancer; scp -q -o BatchMode=yes node-4:$hd/stats.log node-4:$hd/util.log node-4:$hd/dynamic.log $R/$s/balancer/ 2>/dev/null
    N4 "ls -la $hd" > $R/$s/balancer/ls.txt 2>&1
  fi
  echo "{\"seg\":\"$s\",\"name\":\"$name\",\"mode\":\"$mode\",\"step\":\"$step\",\"rate\":$RATE,\"schedule\":\"$RATE:$MEAS\",\"warm\":$WARM,\"t0\":$t0,\"t1\":$t1,\"secondary_uid\":\"$suid\",\"primary_uid\":\"$pu\",\"balancer_dir\":\"$hd\",\"env\":\"$env\"}" | tee -a $R/segments.jsonl > $R/$s/seg.json
  log "$s done: $(grep -c '"all"' $R/$s/wlgen.jsonl 2>/dev/null) client seconds, $(grep -c '^{"t"' $R/$s/probe.jsonl 2>/dev/null) probe windows; last: $(tail -2 $R/$s/wlgen.jsonl | head -1 | cut -c1-120)"
}

log "K4.2 queue start; shipped Balancer $(N4 "sha256sum $SHIPPED" | cut -c1-16)"
run="$(date +%Y%m%d-%H%M%S)-k42-primary"; echo $run > $R/primary-run.txt
(cd $HRD && NS=gordion SUT=recommendation NODE=node-4 CPUSET=4,6,8,10 TAG=gordion7 GATE=0 OTHER_NODES=node-1,node-2 WINDOW_MS=100 RAW=1 RESULTS=$R bash hr.sh deploy $run) > $R/deploy.log 2>&1
log "HR redeployed with recorders on, run $run: $(kubectl -n gordion get pods -o wide --no-headers | awk '/^recommendation-/ {print $1, $3, $7}')"
kubectl apply -f ~/k4/k4b-secondary.yaml >/dev/null
N4 "sudo sysctl -q -w kernel.bpf_stats_enabled=1; sysctl kernel.bpf_stats_enabled"
sec_down; N4 "sudo bash $B/k4node.sh hc-unload" > $R/prewarm-hc.log 2>&1
bash $HRS wlgen $(date +%Y%m%d-%H%M%S)-k42-prewarm -- -mix single:recommend -fixed-params -k 4 -warmup 0 -schedule "$RATE:60" > $R/prewarm.log 2>&1 < /dev/null
log "pre-warm done: $(tail -1 $R/prewarm.log | cut -c1-120)"

seg S0 S -
seg a-H H a; seg a-C C a
seg b-C C b; seg b-H H b
seg c-H H c; seg c-C C c
seg d-C C d; seg d-H H d
seg S1 S -

sec_down; N4 "sudo bash $B/k4node.sh hc-load" > $R/end-hc.log 2>&1; cat $R/end-hc.log
N4 "sudo sysctl -q -w kernel.bpf_stats_enabled=0; sysctl kernel.bpf_stats_enabled; pgrep -a k1lean || echo 'no probe left'; sha256sum $SHIPPED | cut -c1-16; systemctl is-active hc-balancer-dyn hc-balancer; ls /var/tmp/k4/irq-orig.txt 2>/dev/null && wc -l < /var/tmp/k4/irq-orig.txt; awk 'index(\$0, \"eno2\") {sub(\":\", \"\", \$1); print \$1}' /proc/interrupts | while read i; do cat /proc/irq/\$i/smp_affinity_list; done | sort | uniq -c"
for d in $(kubectl -n gordion get deploy -o jsonpath='{range .items[*]}{.metadata.name}={range .spec.template.spec.containers[0].env[*]}{.name}:{.value},{end}{"\n"}{end}' | grep "GORDION_OUT:/data/$run-" | cut -d= -f1); do
  kubectl -n gordion set env deploy/$d GORDION_DISABLE=1 >/dev/null && echo "recorder off: $d"
done
log DONE-K4.2
