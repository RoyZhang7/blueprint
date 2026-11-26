#!/bin/bash
# Phase K5 (overhead ladder) and K6 (signature validation) queue, node-0, detached (RESULTS-K 5 and 6; RUNBOOK 17.1).
# Victims on node-3: unique-id-service (sn, image g7, CPU 1, hostPID) and recommendation (gordion, image gordion8,
# CPUs 5,7, GOMAXPROCS 2, gate on). Both recorders start with GORDION_INTERCEPT=off GORDION_READS=none and a control
# file (GORDION_CTL) that the cell controller flips per arm. The agent and the scorer run pinned to CPU 53. Loads:
# ugen (unique-id, Thrift) and hrgen (recommendation) on node-3 CPUs 17-27 odd. Neighbors: none, switch (stress-ng
# --switch per victim CPU in /sys/fs/cgroup/gordion-aggr), net (k5net.sh: UDP stream between two pods on CPUs 9/11, its
# softirq steered to the victim's CPUs with RPS).
# Phases (one per call; "all" chains them): prep sweep a5 rung0 rung1 rung2 rung3 k6 rung4 restore. STOP file:
# /local/results/k5/STOP (checked before every cell; the restore still runs with "all").
set -u
export PATH=/usr/local/go1.25/bin:$HOME/go/bin:$PATH
R=/local/results/k5; K6=/local/results/k6; KB=/local/k5build; KD=$HOME/k5; NODE=node-3; RCPU=53; WL=17,19,21,23,25,27
HRD=/local/hr/hotelReservation/noisy-neighbors/dictionary; SN=/local/sn/sn.clean.sh; BS=/local/blueprint/examples/bigsvc
mkdir -p $R $K6 $R/specs $R/recorders
log() { echo "=== $(date -u +%H:%M:%S) $*"; }
N3() { ssh -n -o BatchMode=yes $NODE "$@"; }
N4() { ssh -n -o BatchMode=yes node-4 "$@"; }
stopped() { [ -f $R/STOP ]; }
MEAS=${MEAS:-300}

# ---------------------------------------------------------------- service facts
svc_vars() {   # uid | rec | syn -> V_*
  case $1 in
    uid) V_NS=sn V_LBL=service=unique-id-service V_CPUS=1 V_SIB=29 V_K=1 V_SLOTS=1 V_GO="" V_CTL=/var/tmp/gordion/k5-ctl-uid V_REC=/var/tmp/gordion/$(cat $R/run-uid.txt)-unique-id-service.jsonl V_DICT=/var/tmp/k5/dict-uid.json V_MID=10200 V_KNEE=19000 ;;
    rec) V_NS=gordion V_LBL=io.kompose.service=recommendation V_CPUS=5,7 V_SIB=33,35 V_K=2 V_SLOTS=1 V_GO=--go V_CTL=/var/tmp/gordion/k5-ctl-rec V_REC=/var/tmp/gordion/$(cat $R/run-rec.txt)-recommendation.jsonl V_DICT=/var/tmp/k5/dict-rec.json V_MID=3600 V_KNEE=6000 ;;
    syn) V_NS=default V_LBL=app=bigsvc V_CPUS=1,3,5,7 V_SIB=29,31,33,35 V_K=4 V_SLOTS=3 V_GO=--go V_CTL="" V_REC=/var/tmp/gordion/$(cat $R/run-syn.txt).jsonl V_DICT="" V_MID=400 V_KNEE=600 ;;
  esac
  V_UID=$(kubectl -n $V_NS get pods -l $V_LBL --field-selector=status.phase=Running -o jsonpath='{.items[0].metadata.uid}')
  V_IP=$(kubectl -n $V_NS get pods -l $V_LBL --field-selector=status.phase=Running -o jsonpath='{.items[0].status.podIP}')
}

load_start() {   # svc cell schedule -> background pid in LP
  local svc=$1 cell=$2 sched=$3; LSVC=$svc
  case $svc in
    uid) N3 "mkdir -p /var/tmp/k5 && taskset -c $WL /var/tmp/k5/ugen -addr $V_IP:9090 -conns 64 -warmup 10 -schedule $sched -out /var/tmp/k5/$cell-ugen.jsonl" > $R/$cell/load.log 2>&1 & LP=$! ;;
    rec) (cd $HRD && NS=gordion SUT=recommendation WLGEN_NODE=$NODE WLGEN_CPUS=$WL RESULTS=$R bash hr.sh wlgen $cell -- -mix single:recommend -fixed-params -k 2 -warmup 10 -schedule "$sched") > $R/$cell/load.log 2>&1 & LP=$! ;;
    syn) (cd $BS && WLGEN_NODE=$NODE WLGEN_CPUS=$WL bash scripts/exp.sh wlgen $cell -- -mix ${SYN_MIX:-uniform} -warmup 10 -schedule "$sched") > $R/$cell/load.log 2>&1 & LP=$! ;;
  esac
}

load_stop() {
  # hrgen / wlgen (rec, syn) write their per-second records only when they exit normally: wait for the finite schedule
  if [ -n "${LP:-}" ] && [ "${LSVC:-uid}" != uid ]; then for i in $(seq 1 180); do kill -0 $LP 2>/dev/null || break; sleep 1; done; fi
  N3 "pkill -INT -f '/var/tmp/k5/[u]gen'; pkill -INT -x hrgen; pkill -INT -f '[w]lgen'; true"
  [ -n "${LP:-}" ] && { sleep 3; kill $LP 2>/dev/null; wait $LP 2>/dev/null; }
  LP=""
}

# cell NAME SVC SCHEDULE NB ARMS REPS MEASURE [extra k5spec args...]
cell() {
  local name=$1 svc=$2 sched=$3 nb=$4 arms=$5 reps=$6 meas=$7; shift 7
  stopped && { log "STOP file: skipping $name"; return 1; }
  local cell; cell="$(date +%Y%m%d-%H%M%S)-k5-$name"; mkdir -p $R/$cell
  svc_vars $svc
  local narms; narms=$(echo "$arms" | tr ',' '\n' | wc -l)
  local sdur; sdur=$(echo "$sched" | tr ',' '\n' | awk -F: '{s += $2} END {print s}')
  local need=$(( reps * narms * (meas + 8) + 60 ))
  [ "$(echo "$sched" | tr ',' '\n' | wc -l)" = 1 ] && [ "$sdur" -lt "$need" ] && sched="${sched%%:*}:$need"   # a single-rate schedule is stretched to the cell's length
  [ "$(echo "$sched" | tr ',' '\n' | wc -l)" = 1 ] && [ $svc != uid ] && sched="${sched%%:*}:$need"   # rec / syn: and cut to it (the generator must end by itself)
  local nbargs=""
  case $nb in
    switch) nbargs="--nb switch" ;;
    sib) nbargs="--nb sib --nb-cpus $V_SIB" ;;
    cache) nbargs="--nb cache --nb-cpus 9,11,13,15 --nb-n 4" ;;
    net) nbargs="--nb net --holder netsend=$(cut -d' ' -f1 $R/net-uids.txt) --holder netrecv=$(cut -d' ' -f2 $R/net-uids.txt)" ;;
    *) nbargs="--nb none" ;;
  esac
  python3 $KD/k5spec.py --cell $cell --out $R/specs/$cell.json --svc $svc --pod-uid $V_UID --cpus $V_CPUS --sib $V_SIB --k $V_K --slots $V_SLOTS $V_GO \
    ${V_CTL:+--ctl $V_CTL} --recorder $V_REC ${V_DICT:+--dict $V_DICT} --ctl-out /var/tmp/k5/$cell --arms $arms --reps $reps --measure $meas --reader-cpu $RCPU $nbargs "$@" > $R/$cell/spec.txt 2>&1 || { log "$cell: spec FAILED: $(cat $R/$cell/spec.txt)"; return 1; }
  scp -q -o BatchMode=yes $R/specs/$cell.json $NODE:/var/tmp/k5/$cell.json
  load_start $svc $cell "$sched"
  sleep 25
  log "$cell: $svc $sched nb $nb arms $arms x $reps x $meas s"
  N3 "sudo python3 /var/tmp/k5/k5ctl.py --spec /var/tmp/k5/$cell.json" > $R/$cell/ctl.log 2>&1
  load_stop
  scp -q -r -o BatchMode=yes $NODE:/var/tmp/k5/$cell $R/$cell/ctl || log "$cell: fetch of the controller directory FAILED"
  case $svc in
    uid) scp -q -o BatchMode=yes $NODE:/var/tmp/k5/$cell-ugen.jsonl $R/$cell/ugen.jsonl 2>/dev/null; N3 "rm -f /var/tmp/k5/$cell-ugen.jsonl" ;;
    syn) (cd $BS && NODE=$NODE HOSTDIR=/var/tmp/gordion bash scripts/exp.sh collect $cell > $R/$cell/collect.log 2>&1); cp /local/results/$cell/*.jsonl $R/$cell/ 2>/dev/null   # exp.sh's RESULTS is /local/results
         mkdir -p $R/recorders; scp -q -o BatchMode=yes $NODE:$V_REC $R/recorders/ 2>/dev/null ;;
  esac
  [ "${KEEP_CTL_DIR:-0}" = 1 ] || N3 "sudo rm -rf /var/tmp/k5/$cell /var/tmp/k5/$cell.json"
  echo "$cell $name $svc $sched $nb $arms $reps $meas" >> $R/cells.txt
  log "$cell done: $(grep -c '"event":"arm"' $R/$cell/ctl/cell.jsonl 2>/dev/null) arm records; $(grep -c error $R/$cell/ctl/cell.jsonl 2>/dev/null) errors"
}

ctl_off() { N3 "echo 'intercept=off reads=none' | sudo tee /var/tmp/gordion/k5-ctl-uid /var/tmp/gordion/k5-ctl-rec > /dev/null"; }

# ---------------------------------------------------------------- phases
phase_prep() {
  log "K5 prep"
  for ns in gordion sn; do kubectl -n $ns get deploy -o json > $R/pre-k5-$ns.json; done
  kubectl -n default get deploy bigsvc -o json > $R/pre-k5-bigsvc.json
  kubectl get pods -A -o wide --no-headers > $R/pods-before-k5.txt
  N3 "sudo mkdir -p /var/tmp/k5 /var/tmp/gordion && sudo chmod 777 /var/tmp/k5; grep -c resctrl /proc/mounts" > $R/node3-resctrl-before.txt
  scp -q -o BatchMode=yes $KB/k5agent $KB/k5probe.bpf.o $KD/k5ctl.py $KD/k5score.py $KD/k5slice.py $HOME/k1/ugen/ugen $NODE:/var/tmp/k5/
  N3 "[ -d /sys/fs/resctrl/info ] || sudo mount -t resctrl resctrl /sys/fs/resctrl; ls /sys/fs/resctrl/info | tr '\n' ' '; sudo mkdir -p /sys/fs/cgroup/gordion-aggr; cat /proc/sys/kernel/bpf_stats_enabled" > $R/node3-prep.txt 2>&1
  kubectl -n default scale deploy/bigsvc --replicas=0 >/dev/null
  # unique-id on node-3 CPU 1 (hostPID), image g7; the K5 switches off at start, control file on the recorder's hostPath
  local run; run="$(date +%Y%m%d-%H%M%S)-k5-uid"; echo $run > $R/run-uid.txt
  NS=sn SUT=unique-id-service INSTRUMENTED=unique-id-service NODE=$NODE CPUSET=1 TAG=g7 HOSTPID=1 OTHER_NODES=node-1,node-2 DB_NODE=node-1 WINDOW_MS=100 RAW=0 bash $SN deploy $run > $R/deploy-uid.log 2>&1
  # sn.sh deploy keeps the installed image tag (Addendum F pitfall): the K5 recorder is in g7
  local ctr; ctr=$(kubectl -n sn get deploy unique-id-service -o jsonpath='{.spec.template.spec.containers[0].name}')
  kubectl -n sn set image deploy/unique-id-service $ctr=10.10.1.1:5000/sn-gordion:g7 >/dev/null
  kubectl -n sn set env deploy/unique-id-service GORDION_INTERCEPT=off GORDION_READS=none GORDION_CTL=/data/k5-ctl-uid >/dev/null
  kubectl -n sn rollout status deploy/unique-id-service --timeout=240s >/dev/null
  # recommendation on node-3 CPUs 5,7, image gordion8, gate on
  run="$(date +%Y%m%d-%H%M%S)-k5-rec"; echo $run > $R/run-rec.txt
  (cd $HRD && NS=gordion SUT=recommendation NODE=$NODE CPUSET=5,7 TAG=gordion8 GATE=1 OTHER_NODES=node-1,node-2 WINDOW_MS=100 RAW=0 RESULTS=$R bash hr.sh deploy $run) > $R/deploy-rec.log 2>&1
  kubectl -n gordion set env deploy/recommendation GORDION_INTERCEPT=off GORDION_READS=none GORDION_CTL=/data/k5-ctl-rec >/dev/null
  kubectl -n gordion rollout status deploy/recommendation --timeout=240s >/dev/null
  sleep 5; ctl_off
  kubectl get pods -A -o wide --no-headers | grep -E "unique-id-service|recommendation|bigsvc" | awk '{print $1, $2, $4, $8}' > $R/pods-k5.txt
  N3 "for p in \$(pgrep -f '[U]niqueIdService|taskset -c 5,7 [r]ecommendation' ); do echo \$p \$(cat /proc/\$p/comm) \$(grep Cpus_allowed_list /proc/\$p/status); done" > $R/pins-k5.txt 2>&1
  log "prep done: $(tr '\n' ';' < $R/pods-k5.txt) pins: $(tr '\n' ';' < $R/pins-k5.txt)"
}

phase_sweep() {
  log "dictionary sweeps (recorder full, agent perf + resctrl + sampler)"
  local t0; t0=$(date +%s%3N)
  KEEP_CTL_DIR=1 cell sweep-uid uid 2000:30,4000:30,6000:30,8000:30,10000:30,12000:30,14000:30,16000:30,18000:30 none sweep 1 250
  local c; c=$(tail -1 $R/cells.txt | cut -d' ' -f1); svc_vars uid
  N3 "sudo python3 /var/tmp/k5/k5score.py --fit $V_REC --fit-from-t $t0 --fit-agent /var/tmp/k5/$c/sweep-r00-agent.jsonl --fit-cg uid --dict /var/tmp/k5/dict-uid.json --k 1; sudo rm -rf /var/tmp/k5/$c /var/tmp/k5/$c.json" > $R/$c/fit.txt 2>&1; cat $R/$c/fit.txt
  scp -q -o BatchMode=yes $NODE:/var/tmp/k5/dict-uid.json $R/dict-uid.json
  t0=$(date +%s%3N)
  KEEP_CTL_DIR=1 cell sweep-rec rec 1000:30,2000:30,3000:30,4000:30,5000:30,6000:30,7000:30 none sweep 1 190
  c=$(tail -1 $R/cells.txt | cut -d' ' -f1); svc_vars rec
  N3 "sudo python3 /var/tmp/k5/k5score.py --fit $V_REC --fit-from-t $t0 --fit-agent /var/tmp/k5/$c/sweep-r00-agent.jsonl --fit-cg rec --dict /var/tmp/k5/dict-rec.json --k 2; sudo rm -rf /var/tmp/k5/$c /var/tmp/k5/$c.json" > $R/$c/fit.txt 2>&1; cat $R/$c/fit.txt
  scp -q -o BatchMode=yes $NODE:/var/tmp/k5/dict-rec.json $R/dict-rec.json
}

net_on() {   # victim cpus, pps
  bash $KD/k5net.sh start $NODE 9 11 $2 512 1 $1 > $R/net-uids.txt 2>> $R/net.log || { log "net neighbor FAILED"; cat $R/net.log | tail -3; }
  cat $R/net-uids.txt
}
net_off() { bash $KD/k5net.sh stop >> $R/net.log 2>&1; }

phase_a5() {
  log "A5: the network neighbor, softirq per victim CPU at two settings (unique-id's CPU 1)"
  { echo "== quiet (no neighbor), 20 s"; bash $KD/k5net.sh calib $NODE 1,5,7 20
    net_on 1 20000 > /dev/null; sleep 5; echo "== 20 000 pps x 512 B + 1 burner (CPUs 9/11), RPS -> CPU 1, 20 s"; bash $KD/k5net.sh calib $NODE 1,5,7 20
    bash $KD/k5net.sh rate 80000 >> $R/net.log 2>&1; sleep 5; echo "== 80 000 pps, 20 s"; bash $KD/k5net.sh calib $NODE 1,5,7 20
    bash $KD/k5net.sh rate 50000 >> $R/net.log 2>&1; sleep 5; echo "== 50 000 pps (the runs' setting), 20 s"; bash $KD/k5net.sh calib $NODE 1,5,7 20
    kubectl -n k5 logs k5net-recv --tail=2; kubectl -n k5 logs k5net-send --tail=2; } > $R/a5-calib.txt 2>&1
  net_off; cat $R/a5-calib.txt
}

phase_rung0() {
  log "rung 0: twins (mid load; neighbors none, switch, net)"
  for svc in uid rec; do svc_vars $svc
    cell r0-$svc-none-mid $svc $V_MID:9999 none none 1 $MEAS
    cell r0-$svc-switch-mid $svc $V_MID:9999 switch none 1 $MEAS
  done
  for svc in uid rec; do svc_vars $svc; net_on $V_CPUS 50000 > /dev/null; cell r0-$svc-net-mid $svc $V_MID:9999 net none 1 $MEAS; net_off; done
}

phase_rung1() {
  log "rung 1: production mode, both services x both loads x three neighbors, n = 5 x 30 s paired"
  for svc in uid rec; do svc_vars $svc
    for load in mid knee; do local rate; [ $load = mid ] && rate=$V_MID || rate=$V_KNEE
      cell r1-$svc-none-$load $svc $rate:9999 none none,full 5 30
      cell r1-$svc-switch-$load $svc $rate:9999 switch none,full 5 30
    done
  done
  for svc in uid rec; do svc_vars $svc; net_on $V_CPUS 50000 > /dev/null
    for load in mid knee; do local rate; [ $load = mid ] && rate=$V_MID || rate=$V_KNEE; cell r1-$svc-net-$load $svc $rate:9999 net none,full 5 30; done
    net_off
  done
  # replay inputs for rung 2: the first "full" arm of the (mid, switch) cell, its recorder windows and agent file
  for svc in uid rec; do svc_vars $svc
    local c; c=$(grep " r1-$svc-switch-mid " $R/cells.txt | tail -1 | cut -d' ' -f1); [ -n "$c" ] || continue
    python3 - $R/$c/ctl/cell.jsonl > $R/$c/replay-window.txt <<'EOF'
import json, sys
for l in open(sys.argv[1]):
    d = json.loads(l)
    if d.get("event") == "arm" and d["arm"] == "full" and d["rep"] == 0:
        print(d["t0"], d["t1"], d["rec_bytes"][0], d["rec_bytes"][1]); break
EOF
    read t0 t1 b0 b1 < $R/$c/replay-window.txt
    N3 "python3 /var/tmp/k5/k5slice.py $V_REC $b0 $b1 /var/tmp/k5/replay-$svc-rec.jsonl"
    scp -q -o BatchMode=yes $R/$c/ctl/full-r00-agent.jsonl $NODE:/var/tmp/k5/replay-$svc-agent.jsonl
    log "replay inputs for $svc: $c full-r00, bytes $b0-$b1"
  done
}

phase_rung2() {
  log "rung 2: one source at a time, mid load, switch neighbor"
  for svc in uid rec; do svc_vars $svc
    for arm in probe reads sampler intercept perf-armed rdt-armed; do cell r2-$svc-$arm $svc $V_MID:9999 switch $arm 1 $MEAS; done
    cell r2-$svc-scorer-replay $svc $V_MID:9999 switch scorer-replay 1 $MEAS --replay-recorder /var/tmp/k5/replay-$svc-rec.jsonl --replay-agent /var/tmp/k5/replay-$svc-agent.jsonl
  done
  for svc in uid rec; do svc_vars $svc; net_on $V_CPUS 50000 > /dev/null; cell r2-$svc-sirq-net $svc $V_MID:9999 net sirq 1 $MEAS; net_off; done
}

phase_rung3() {
  log "rung 3: inside the probe, cumulative (phi -> +run_delay [= rung 2 probe] -> +sibling -> +wake-up -> +softirq -> +timer)"
  for svc in uid rec; do svc_vars $svc
    for arm in phi sib wake wsirq timer; do cell r3-$svc-$arm $svc $V_MID:9999 switch $arm 1 $MEAS; done
    cell r0b-$svc-switch-mid $svc $V_MID:9999 switch none 1 $MEAS      # a second twin against drift
  done
}

phase_rung4() {
  log "rung 4: per signal elsewhere"
  svc_vars rec; for arm in q g cpu mhz count slot perf-read rdt-read; do cell r4-rec-$arm rec $V_MID:9999 switch $arm 1 $MEAS; done
  svc_vars uid; for arm in cpu mhz count slot perf-read rdt-read; do cell r4-uid-$arm uid $V_MID:9999 switch $arm 1 $MEAS; done
  for svc in uid rec; do svc_vars $svc; cell r0c-$svc-switch-mid $svc $V_MID:9999 switch none 1 $MEAS; done
}

phase_k6() {
  log "K6: signatures, one source at a time, agent with everything on, scorer offline"
  R_SAVE=$R; R=$K6; mkdir -p $R/specs
  cp $R_SAVE/run-uid.txt $R_SAVE/run-rec.txt $R/ 2>/dev/null
  svc_vars uid
  cell c6-uid-twin1 uid $V_MID:9999 none all 1 $MEAS
  cell c1-uid-sib uid $V_MID:9999 sib all 1 $MEAS
  net_on 1 50000 > /dev/null; cell c2-uid-net uid $V_MID:9999 net all 1 $MEAS; net_off
  cell c6-uid-twin2 uid $V_MID:9999 none all 1 $MEAS
  # C3 / C4 on the synthetic service: uniform twin, memwalk30 at 24 MB alone, the same under the D.4 cache neighbor
  local run; run="$(date +%Y%m%d-%H%M%S)-k6-syn-uniform"; echo $run > $R/run-syn.txt
  (cd $BS && NODE=$NODE CPUSET=1,3,5,7 TAG=k123g4 HOSTDIR=/var/tmp/gordion GATE=1 WINDOW_MS=100 RAW=1 WLGEN_NODE=$NODE WLGEN_CPUS=$WL STRESSOR=none bash scripts/exp.sh deploy $run) > $R/deploy-syn-uniform.log 2>&1; sleep 8
  SYN_MIX=uniform cell c3-syn-uniform-twin syn 400:9999 none all 1 $MEAS
  run="$(date +%Y%m%d-%H%M%S)-k6-syn-memwalk30"; echo $run > $R/run-syn.txt
  (cd $BS && NODE=$NODE CPUSET=1,3,5,7 TAG=k123g4 HOSTDIR=/var/tmp/gordion GATE=1 WINDOW_MS=100 RAW=1 WLGEN_NODE=$NODE WLGEN_CPUS=$WL STRESSOR=none MEMWALK_MB=24 bash scripts/exp.sh deploy $run) > $R/deploy-syn-memwalk.log 2>&1; sleep 8
  SYN_MIX=file:/local/results/mix-memwalk30.json cell c3-syn-memwalk30 syn 400:9999 none all 1 $MEAS
  SYN_MIX=file:/local/results/mix-memwalk30.json cell c4-syn-memwalk30-cache syn 400:9999 cache all 1 $MEAS
  kubectl -n default scale deploy/bigsvc --replicas=0 >/dev/null
  phase_c5
  R=$R_SAVE
}

phase_c5() {   # the harvester: K4.5 reserve 1 at 5000 req/s on node-4 and its standalone twin, agent with everything on
  stopped && { log "STOP file: skipping C5"; return 1; }
  log "C5: HC reserve 1 at 5000 req/s on node-4 (K4.5's placement) and its twin"
  local B4=/var/tmp/k4/bin
  N4 "sudo mkdir -p $B4 /var/tmp/k5 && sudo chmod 777 /var/tmp/k4 $B4 /var/tmp/k5; grep -c resctrl /proc/mounts; cat /proc/sys/kernel/bpf_stats_enabled" > $R/node4-before.txt
  scp -q -o BatchMode=yes $HOME/k4/k4node.sh $HOME/k4/k4send.py $HOME/k4/k4sample.py node-4:$B4/
  scp -q -o BatchMode=yes $KB/k5agent $KB/k5probe.bpf.o $KD/k5ctl.py $KD/k5score.py node-4:/var/tmp/k5/
  N4 "[ -d /sys/fs/resctrl/info ] || sudo mount -t resctrl resctrl /sys/fs/resctrl; sudo sysctl -q -w kernel.bpf_stats_enabled=1; sudo mkdir -p /sys/fs/cgroup/gordion-aggr"   # empty: the spec's aggr holder slot
  local run; run="$(date +%Y%m%d-%H%M%S)-k6-c5-primary"; echo $run > $R/run-c5.txt
  (cd $HRD && NS=gordion SUT=recommendation NODE=node-4 CPUSET=4,6,8,10 TAG=gordion8 GATE=0 OTHER_NODES=node-1,node-2 WINDOW_MS=100 RAW=0 RESULTS=$R bash hr.sh deploy $run) > $R/deploy-c5.log 2>&1
  kubectl -n gordion rollout status deploy/recommendation --timeout=240s >/dev/null
  kubectl get ns k4 >/dev/null 2>&1 || kubectl create ns k4 >/dev/null
  kubectl apply -f $HOME/k4/k4b-secondary.yaml >/dev/null
  local pu; pu=$(kubectl -n gordion get pods -l io.kompose.service=recommendation --field-selector=status.phase=Running -o jsonpath='{.items[0].metadata.uid}')
  c5_seg() {   # name S|H
    local name=$1 mode=$2 seg; seg="$(date +%Y%m%d-%H%M%S)-k6-$name"; mkdir -p $R/$seg; local suid="" hd=/mnt/extra/logs/hc/$seg
    kubectl -n k4 scale deploy/hc-secondary-burner --replicas=0 >/dev/null; sleep 5
    if [ $mode = S ]; then N4 "sudo bash $B4/k4node.sh hc-unload" > $R/$seg/hc.log 2>&1
    else
      N4 "sudo bash $B4/k4node.sh hc-load" > $R/$seg/hc.log 2>&1
      kubectl -n k4 scale deploy/hc-secondary-burner --replicas=1 >/dev/null; kubectl -n k4 rollout status deploy/hc-secondary-burner --timeout=180s >/dev/null; sleep 3
      suid=$(kubectl -n k4 get pods -l app=hc-secondary-burner --field-selector=status.phase=Running -o jsonpath='{.items[0].metadata.uid}')
      N4 "sudo bash $B4/k4node.sh sec-pin $suid" >> $R/$seg/hc.log 2>&1; sleep 2
      N4 "sudo TIC=1 LOW_TIC=1 LOGDIR=$hd bash $B4/k4node.sh hc-on $suid" >> $R/$seg/hc.log 2>&1 || log "$seg: hc-on FAILED: $(tail -3 $R/$seg/hc.log | tr '\n' ' ')"
      log "$seg: node-4 units: $(N4 "systemctl is-active hc-listener hc-balancer hc-balancer-dyn | tr '\n' ' '")"
    fi
    python3 $KD/k5spec.py --cell $seg --out $R/specs/$seg.json --svc rec --pod-uid $pu --cpus 4,6,8,10 --sib 32,34,36,38 --k 4 --slots 4 --go --recorder /mnt/extra/gordion/$run-recommendation.jsonl \
      --ctl-out /var/tmp/k5/$seg --arms all --reps 1 --measure $MEAS --reader-cpu 53 --nb none ${suid:+--holder burner=$suid} > $R/$seg/spec.txt 2>&1
    scp -q -o BatchMode=yes $R/specs/$seg.json node-4:/var/tmp/k5/$seg.json
    N4 "sudo nohup python3 $B4/k4sample.py --out /var/tmp/k4/$seg-sample.jsonl --secs $((MEAS + 80)) --primary-uid $pu ${suid:+--secondary-uid $suid} < /dev/null > /var/tmp/k4/$seg-sample.log 2>&1 &"
    (cd $HRD && NS=gordion SUT=recommendation WLGEN_NODE=node-4 WLGEN_CPUS=13,15,17,19,21,23 RESULTS=$R bash hr.sh wlgen $seg -- -mix single:recommend -fixed-params -k 4 -warmup 10 -schedule "5000:$((MEAS + 70))") > $R/$seg/load.log 2>&1 & LP=$!
    sleep 25; log "$seg: mode $mode (secondary ${suid:-none})"
    N4 "sudo python3 /var/tmp/k5/k5ctl.py --spec /var/tmp/k5/$seg.json" > $R/$seg/ctl.log 2>&1
    for i in $(seq 1 180); do kill -0 $LP 2>/dev/null || break; sleep 1; done
    N4 "pkill -INT -x hrgen; true"; sleep 3; kill $LP 2>/dev/null; wait $LP 2>/dev/null; LP=""
    for i in $(seq 1 40); do N4 "pgrep -f '[k]4sample.py --out /var/tmp/k4/$seg'" > /dev/null || break; sleep 2; done
    scp -q -r -o BatchMode=yes node-4:/var/tmp/k5/$seg $R/$seg/ctl || log "$seg: fetch of the controller directory FAILED"; scp -q -o BatchMode=yes node-4:/var/tmp/k4/$seg-sample.jsonl $R/$seg/sample.jsonl 2>/dev/null
    if [ $mode = H ]; then N4 "sudo bash $B4/k4node.sh hc-off" >> $R/$seg/hc.log 2>&1; mkdir -p $R/$seg/balancer; scp -q -o BatchMode=yes node-4:$hd/stats.log node-4:$hd/util.log node-4:$hd/dynamic.log $R/$seg/balancer/ 2>/dev/null; fi
    echo "$seg $name rec 5000 $mode all 1 $MEAS" >> $R/cells.txt
    log "$seg done: $(grep -c '"event":"arm"' $R/$seg/ctl/cell.jsonl 2>/dev/null) arm records; $(grep -c '"event":"error"' $R/$seg/ctl/cell.jsonl 2>/dev/null) errors"
  }
  c5_seg c5-twin-S0 S
  c5_seg c5-res1 H
  # node-4 back as found: HC loaded (hc-load after the S0 unload happened in res1), Balancer off, burner gone
  kubectl -n k4 scale deploy/hc-secondary-burner --replicas=0 >/dev/null; sleep 5; kubectl delete ns k4 --wait=true >/dev/null 2>&1
  N4 "lsmod | grep -q qidlecpu || sudo bash $B4/k4node.sh hc-load; sudo sysctl -q -w kernel.bpf_stats_enabled=0; grep -c resctrl /proc/mounts" >> $R/node4-before.txt 2>&1
  mkdir -p $R/recorders; scp -q -o BatchMode=yes node-4:/mnt/extra/gordion/$run-recommendation.jsonl $R/recorders/ 2>/dev/null
  N4 "sudo rmdir /sys/fs/cgroup/gordion-aggr 2>/dev/null; sudo rm -rf /var/tmp/k4 /var/tmp/k5 /mnt/extra/gordion/$run-* ; sudo rmdir /mnt/extra/gordion 2>/dev/null; sudo rm -rf /mnt/extra/logs/hc/*-k6-c5-*; lsmod | grep -c qidlecpu; systemctl is-active hc-listener hc-balancer hc-balancer-dyn | tr '\n' ' '; ls /mnt/extra/logs/hc | wc -l" > $R/node4-after.txt 2>&1
  if ! N3 "[ -d /sys/fs/resctrl/info ]" && [ "$(head -1 $R/node4-before.txt)" = 0 ]; then :; fi
  [ "$(head -1 $R/node4-before.txt)" = 0 ] && N4 "sudo umount /sys/fs/resctrl 2>/dev/null; true"
  log "C5 done; node-4: $(cat $R/node4-after.txt | tr '\n' ' ')"
}

phase_restore() {
  log "restore"
  load_stop; net_off
  for n in $NODE node-4; do ssh -n -o BatchMode=yes $n "sudo pkill -INT k5agent; sudo pkill -INT -f '[k]5score.py'; sudo pkill -TERM -f '[k]5ctl.py'; sudo pkill -TERM stress-ng; sleep 2; sudo pkill -9 stress-ng; sudo rmdir /sys/fs/cgroup/gordion-aggr 2>/dev/null; sudo sysctl -q -w kernel.bpf_stats_enabled=0; true"; done
  svc_vars uid 2>/dev/null; scp -q -o BatchMode=yes $NODE:$V_REC $R/recorders/ 2>/dev/null
  svc_vars rec 2>/dev/null; scp -q -o BatchMode=yes $NODE:$V_REC $R/recorders/ 2>/dev/null
  [ -f $R/run-rec-1.txt ] && scp -q -o BatchMode=yes $NODE:/var/tmp/gordion/$(cat $R/run-rec-1.txt)-recommendation.jsonl $R/recorders/ 2>/dev/null
  N3 "sudo rm -f /var/tmp/gordion/*-k5-*.jsonl /var/tmp/gordion/*-k6-*.jsonl /var/tmp/gordion/k5-ctl-*; sudo rm -rf /var/tmp/k5 /tmp/wlres/*-k5-* /tmp/wlres/*-k6-*"
  [ "$(cat $R/node3-resctrl-before.txt 2>/dev/null)" = 0 ] && N3 "sudo umount /sys/fs/resctrl 2>/dev/null; true"
  kubectl delete ns k5 --wait=true >/dev/null 2>&1; kubectl delete ns k4 --wait=true >/dev/null 2>&1
  python3 - $R/pre-k5-gordion.json $R/pre-k5-sn.json $R/pre-k5-bigsvc.json <<'EOF' > $R/restore-specs.yaml
import json, sys
out = []
for p in sys.argv[1:]:
    d = json.load(open(p)); items = d["items"] if d.get("kind") == "List" else [d]
    for it in items:
        m = it["metadata"]
        for k in ("resourceVersion", "uid", "creationTimestamp", "generation", "managedFields"): m.pop(k, None)
        m.get("annotations", {}).pop("deployment.kubernetes.io/revision", None)
        it.pop("status", None); out.append(it)
print(json.dumps(dict(apiVersion="v1", kind="List", items=out)))
EOF
  kubectl apply -f $R/restore-specs.yaml > $R/restore-apply.log 2>&1
  grep -q GORDION_CTL $R/pre-k5-gordion.json || kubectl -n gordion set env deploy/recommendation GORDION_INTERCEPT- GORDION_READS- GORDION_CTL- >> $R/restore-apply.log 2>&1
  grep -q GORDION_CTL $R/pre-k5-sn.json || kubectl -n sn set env deploy/unique-id-service GORDION_INTERCEPT- GORDION_READS- GORDION_CTL- >> $R/restore-apply.log 2>&1
  python3 -c "import json,sys; d=json.load(open(sys.argv[1])); sys.exit(any(i['metadata']['name']=='unique-id-service' and 'hostPID' in i['spec']['template']['spec'] for i in d.get('items',[d])))" $R/pre-k5-sn.json && kubectl -n sn patch deploy unique-id-service --type=json -p '[{"op":"remove","path":"/spec/template/spec/hostPID"}]' >> $R/restore-apply.log 2>&1   # apply keeps it (the prep set it)
  for ns in gordion sn default; do for d in $(kubectl -n $ns get deploy -o name); do kubectl -n $ns rollout status $d --timeout=300s >/dev/null 2>&1; done; done
  kubectl get pods -A -o wide --no-headers > $R/pods-after-k5.txt
  python3 - $R/pods-before-k5.txt $R/pods-after-k5.txt <<'EOF'
import sys
def rd(p): return {(l.split()[0], l.split()[1].rsplit('-', 2)[0]): (l.split()[3], l.split()[7]) for l in open(p) if l.strip()}
a, b = rd(sys.argv[1]), rd(sys.argv[2])
diff = [(k, a.get(k), b.get(k)) for k in sorted(set(a) | set(b)) if a.get(k) != b.get(k)]
print(f"pods before/after: {len(a)}/{len(b)}; differences (ns, deploy, before -> after): {diff if diff else 'none'}")
EOF
  # node-0: the build toolchain and the recorder sources back
  [ -f $KB/dpkg-added.txt ] && (cd /local && tar -czf $R/k5build.tgz k5build/k5agent k5build/k5probe.bpf.o k5build/dpkg-added.txt 2>/dev/null)   # the redo's rebuilt $KB has no package list: keep the first archive
  if [ -f $KB/dpkg-added.txt ] && [ -s $KB/dpkg-added.txt ]; then sudo DEBIAN_FRONTEND=noninteractive apt-get purge -y -q $(cat $KB/dpkg-added.txt) > $R/apt-purge.log 2>&1; sudo apt-get autoremove -y -q >> $R/apt-purge.log 2>&1; echo "purged $(wc -l < $KB/dpkg-added.txt) packages: $(which clang-14 || echo clang-14 gone)"; fi
  for f in /local/hr/hotelReservation/gordion/recorder.go /local/hr/hotelReservation/gordion/grpc.go /local/sn/gordion/src/gordion/Gordion.h; do [ -f $f.pre-k5 ] && mv $f.pre-k5 $f; done
  sudo rm -rf $KB
  echo "recorders: $(ls $R/recorders | tr '\n' ' ')"
  N3 "pgrep -c 'k5agent|k5score|stress-ng' ; ls /var/tmp/k5 2>&1 | head -1; grep -c resctrl /proc/mounts; cat /proc/sys/kernel/bpf_stats_enabled; ls /var/tmp/gordion | grep -c k5" 2>&1 | tr '\n' ' '
  echo; log "restore done"
}

phase_redo() {   # Nov 17: the recommendation cells of rungs 0-4 (client files lost), K6 C3/C4 (client files and recorders lost), C5 (hc-on failed)
  log "redo: recommendation back on node-3 CPUs 5,7 (gordion8); rec rungs 0-4; K6 C3, C4, C5"
  [ -x $KB/k5agent ] || (cd /local && sudo tar xzf $R/k5build.tgz k5build/k5agent k5build/k5probe.bpf.o)
  N3 "sudo mkdir -p /var/tmp/k5 /var/tmp/gordion && sudo chmod 777 /var/tmp/k5; [ -d /sys/fs/resctrl/info ] || sudo mount -t resctrl resctrl /sys/fs/resctrl; sudo mkdir -p /sys/fs/cgroup/gordion-aggr"
  scp -q -o BatchMode=yes $KB/k5agent $KB/k5probe.bpf.o $KD/k5ctl.py $KD/k5score.py $KD/k5slice.py $R/dict-rec.json $R/dict-uid.json $NODE:/var/tmp/k5/
  [ -f $R/run-rec-1.txt ] || cp $R/run-rec.txt $R/run-rec-1.txt
  local run; run="$(date +%Y%m%d-%H%M%S)-k5-rec"; echo $run > $R/run-rec.txt
  (cd $HRD && NS=gordion SUT=recommendation NODE=$NODE CPUSET=5,7 TAG=gordion8 GATE=1 OTHER_NODES=node-1,node-2 WINDOW_MS=100 RAW=0 RESULTS=$R bash hr.sh deploy $run) > $R/deploy-rec-redo.log 2>&1
  kubectl -n gordion set env deploy/recommendation GORDION_INTERCEPT=off GORDION_READS=none GORDION_CTL=/data/k5-ctl-rec >/dev/null
  kubectl -n gordion rollout status deploy/recommendation --timeout=240s >/dev/null
  sleep 5; ctl_off
  log "redo prep: $(kubectl get pods -A -o wide --no-headers | grep -E 'recommendation' | awk '{print $1, $2, $4, $8}' | tr '\n' ';') pins: $(N3 "for p in \$(pgrep -f '[r]ecommendation'); do echo \$p \$(cat /proc/\$p/comm) \$(grep Cpus_allowed_list /proc/\$p/status | cut -f2); done" | tr '\n' ';')"
  svc_vars rec
  # rung 0
  cell r0-rec-none-mid rec $V_MID:9999 none none 1 $MEAS
  cell r0-rec-switch-mid rec $V_MID:9999 switch none 1 $MEAS
  net_on $V_CPUS 50000 > /dev/null; cell r0-rec-net-mid rec $V_MID:9999 net none 1 $MEAS; net_off
  # rung 1
  for load in mid knee; do local rate; [ $load = mid ] && rate=$V_MID || rate=$V_KNEE
    cell r1-rec-none-$load rec $rate:9999 none none,full 5 30
    cell r1-rec-switch-$load rec $rate:9999 switch none,full 5 30
  done
  net_on $V_CPUS 50000 > /dev/null
  for load in mid knee; do local rate; [ $load = mid ] && rate=$V_MID || rate=$V_KNEE; cell r1-rec-net-$load rec $rate:9999 net none,full 5 30; done
  net_off
  local c; c=$(grep " r1-rec-switch-mid " $R/cells.txt | tail -1 | cut -d' ' -f1)
  python3 - $R/$c/ctl/cell.jsonl > $R/$c/replay-window.txt <<'EOF'
import json, sys
for l in open(sys.argv[1]):
    d = json.loads(l)
    if d.get("event") == "arm" and d["arm"] == "full" and d["rep"] == 0:
        print(d["t0"], d["t1"], d["rec_bytes"][0], d["rec_bytes"][1]); break
EOF
  local t0 t1 b0 b1; read t0 t1 b0 b1 < $R/$c/replay-window.txt
  N3 "python3 /var/tmp/k5/k5slice.py $V_REC $b0 $b1 /var/tmp/k5/replay-rec-rec.jsonl"
  scp -q -o BatchMode=yes $R/$c/ctl/full-r00-agent.jsonl $NODE:/var/tmp/k5/replay-rec-agent.jsonl
  log "replay inputs for rec: $c full-r00, bytes $b0-$b1"
  # rung 2
  for arm in probe reads sampler intercept perf-armed rdt-armed; do cell r2-rec-$arm rec $V_MID:9999 switch $arm 1 $MEAS; done
  cell r2-rec-scorer-replay rec $V_MID:9999 switch scorer-replay 1 $MEAS --replay-recorder /var/tmp/k5/replay-rec-rec.jsonl --replay-agent /var/tmp/k5/replay-rec-agent.jsonl
  net_on $V_CPUS 50000 > /dev/null; cell r2-rec-sirq-net rec $V_MID:9999 net sirq 1 $MEAS; net_off
  # rung 3 and the drift twin
  for arm in phi sib wake wsirq timer; do cell r3-rec-$arm rec $V_MID:9999 switch $arm 1 $MEAS; done
  cell r0b-rec-switch-mid rec $V_MID:9999 switch none 1 $MEAS
  # rung 4 and its twin
  for arm in q g cpu mhz count slot perf-read rdt-read; do cell r4-rec-$arm rec $V_MID:9999 switch $arm 1 $MEAS; done
  cell r0c-rec-switch-mid rec $V_MID:9999 switch none 1 $MEAS
  # K6 C3 / C4 on the synthetic service, then C5 on node-4 (it redeploys recommendation there; the restore brings it back)
  R_SAVE=$R; R=$K6; cp $R_SAVE/run-rec.txt $R/
  stopped || {
  run="$(date +%Y%m%d-%H%M%S)-k6-syn-uniform"; echo $run > $R/run-syn.txt
  (cd $BS && NODE=$NODE CPUSET=1,3,5,7 TAG=k123g4 HOSTDIR=/var/tmp/gordion GATE=1 WINDOW_MS=100 RAW=1 WLGEN_NODE=$NODE WLGEN_CPUS=$WL STRESSOR=none bash scripts/exp.sh deploy $run) > $R/deploy-syn-uniform.log 2>&1; sleep 8
  SYN_MIX=uniform cell c3-syn-uniform-twin syn 400:9999 none all 1 $MEAS
  run="$(date +%Y%m%d-%H%M%S)-k6-syn-memwalk30"; echo $run > $R/run-syn.txt
  (cd $BS && NODE=$NODE CPUSET=1,3,5,7 TAG=k123g4 HOSTDIR=/var/tmp/gordion GATE=1 WINDOW_MS=100 RAW=1 WLGEN_NODE=$NODE WLGEN_CPUS=$WL STRESSOR=none MEMWALK_MB=24 bash scripts/exp.sh deploy $run) > $R/deploy-syn-memwalk.log 2>&1; sleep 8
  SYN_MIX=file:/local/results/mix-memwalk30.json cell c3-syn-memwalk30 syn 400:9999 none all 1 $MEAS
  SYN_MIX=file:/local/results/mix-memwalk30.json cell c4-syn-memwalk30-cache syn 400:9999 cache all 1 $MEAS
  kubectl -n default scale deploy/bigsvc --replicas=0 >/dev/null
  }
  phase_c5; R=$R_SAVE
}

phase_c5redo() {   # Nov 17: C5 a third time; in both earlier runs its agent exited at start (no gordion-aggr cgroup on node-4)
  log "c5redo: C5 with an empty gordion-aggr on node-4"
  [ -x $KB/k5agent ] || (cd /local && sudo tar xzf $R/k5build.tgz k5build/k5agent k5build/k5probe.bpf.o)
  R_SAVE=$R; R=$K6; phase_c5; R=$R_SAVE
}

phase_c3big() {   # Nov 17: K6 C3 at a 128 MB working set (one twin, one run), no neighbor; both pods calibrate with nothing else running
  log "c3big: K6 C3 at 128 MB on node-3 (uniform twin, memwalk30)"
  [ -x $KB/k5agent ] || (cd /local && sudo tar xzf $R/k5build.tgz k5build/k5agent k5build/k5probe.bpf.o)
  N3 "sudo mkdir -p /var/tmp/k5 /var/tmp/gordion && sudo chmod 777 /var/tmp/k5; [ -d /sys/fs/resctrl/info ] || sudo mount -t resctrl resctrl /sys/fs/resctrl; sudo mkdir -p /sys/fs/cgroup/gordion-aggr"
  scp -q -o BatchMode=yes $KB/k5agent $KB/k5probe.bpf.o $KD/k5ctl.py $KD/k5score.py $KD/k5slice.py $NODE:/var/tmp/k5/
  R_SAVE=$R; R=$K6; local run
  run="$(date +%Y%m%d-%H%M%S)-k6-syn-uniform128"; echo $run > $R/run-syn.txt
  (cd $BS && NODE=$NODE CPUSET=1,3,5,7 TAG=k123g4 HOSTDIR=/var/tmp/gordion GATE=1 WINDOW_MS=100 RAW=1 WLGEN_NODE=$NODE WLGEN_CPUS=$WL STRESSOR=none MEMWALK_MB=128 bash scripts/exp.sh deploy $run) > $R/deploy-syn-uniform128.log 2>&1; sleep 8
  SYN_MIX=uniform cell c3-syn-uniform128-twin syn 400:9999 none all 1 $MEAS
  run="$(date +%Y%m%d-%H%M%S)-k6-syn-memwalk30-128"; echo $run > $R/run-syn.txt
  (cd $BS && NODE=$NODE CPUSET=1,3,5,7 TAG=k123g4 HOSTDIR=/var/tmp/gordion GATE=1 WINDOW_MS=100 RAW=1 WLGEN_NODE=$NODE WLGEN_CPUS=$WL STRESSOR=none MEMWALK_MB=128 bash scripts/exp.sh deploy $run) > $R/deploy-syn-memwalk128.log 2>&1; sleep 8
  SYN_MIX=file:/local/results/mix-memwalk30.json cell c3-syn-memwalk30-128mb syn 400:9999 none all 1 $MEAS
  log "c3big calibration: $(grep -h -o '"walk_ns_per_step":{[^}]*}' $R/deploy-syn-uniform128.log $R/deploy-syn-memwalk128.log | tr '
' ' ')"
  kubectl -n default scale deploy/bigsvc --replicas=0 >/dev/null
  R=$R_SAVE
}

phase_defb() {   # Nov 17, RESULTS-K 5.7: default set B measured, and B + the interceptor's timers, paired with the twin, n = 5
  log "defb: unique-id and recommendation back on node-3 as in the prep; arms none, defB, defBt; mid x {none, switch, net}, unique-id net at 7000"
  [ -x $KB/k5agent ] || (cd /local && sudo tar xzf $R/k5build.tgz k5build/k5agent k5build/k5probe.bpf.o)
  N3 "sudo mkdir -p /var/tmp/k5 /var/tmp/gordion && sudo chmod 777 /var/tmp/k5; [ -d /sys/fs/resctrl/info ] || sudo mount -t resctrl resctrl /sys/fs/resctrl; sudo mkdir -p /sys/fs/cgroup/gordion-aggr; sudo sysctl -q -w kernel.bpf_stats_enabled=1"
  scp -q -o BatchMode=yes $KB/k5agent $KB/k5probe.bpf.o $KD/k5ctl.py $KD/k5score.py $KD/k5slice.py $HOME/k1/ugen/ugen $R/dict-rec.json $R/dict-uid.json $NODE:/var/tmp/k5/
  kubectl -n default scale deploy/bigsvc --replicas=0 >/dev/null
  cp $R/run-uid.txt $R/run-uid-k5.txt; cp $R/run-rec.txt $R/run-rec-k5.txt
  local run; run="$(date +%Y%m%d-%H%M%S)-k5-uid"; echo $run > $R/run-uid.txt
  NS=sn SUT=unique-id-service INSTRUMENTED=unique-id-service NODE=$NODE CPUSET=1 TAG=g7 HOSTPID=1 OTHER_NODES=node-1,node-2 DB_NODE=node-1 WINDOW_MS=100 RAW=0 bash $SN deploy $run > $R/deploy-uid-defb.log 2>&1
  local ctr; ctr=$(kubectl -n sn get deploy unique-id-service -o jsonpath='{.spec.template.spec.containers[0].name}')
  kubectl -n sn set image deploy/unique-id-service $ctr=10.10.1.1:5000/sn-gordion:g7 >/dev/null
  kubectl -n sn set env deploy/unique-id-service GORDION_INTERCEPT=off GORDION_READS=none GORDION_CTL=/data/k5-ctl-uid >/dev/null
  kubectl -n sn rollout status deploy/unique-id-service --timeout=240s >/dev/null
  run="$(date +%Y%m%d-%H%M%S)-k5-rec"; echo $run > $R/run-rec.txt
  (cd $HRD && NS=gordion SUT=recommendation NODE=$NODE CPUSET=5,7 TAG=gordion8 GATE=1 OTHER_NODES=node-1,node-2 WINDOW_MS=100 RAW=0 RESULTS=$R bash hr.sh deploy $run) > $R/deploy-rec-defb.log 2>&1
  kubectl -n gordion set env deploy/recommendation GORDION_INTERCEPT=off GORDION_READS=none GORDION_CTL=/data/k5-ctl-rec >/dev/null
  kubectl -n gordion rollout status deploy/recommendation --timeout=240s >/dev/null
  sleep 5; ctl_off
  log "defb prep: $(kubectl get pods -A -o wide --no-headers | grep -E 'unique-id-service|recommendation' | awk '{print $1, $2, $4, $8}' | tr '\n' ';') pins: $(N3 "for p in \$(pgrep -f '[U]niqueIdService|[r]ecommendation'); do echo \$p \$(cat /proc/\$p/comm) \$(grep Cpus_allowed_list /proc/\$p/status | cut -f2); done" | tr '\n' ';')"
  local svc
  for svc in uid rec; do
    svc_vars $svc
    cell b-$svc-none-mid $svc $V_MID:9999 none none,defB,defBt 5 30
    cell b-$svc-switch-mid $svc $V_MID:9999 switch none,defB,defBt 5 30
    net_on $V_CPUS 50000 > /dev/null; cell b-$svc-net-mid $svc $V_MID:9999 net none,defB,defBt 5 30; net_off
  done
  svc_vars uid; net_on 1 50000 > /dev/null; cell b-uid-net-7000 uid 7000:9999 net none,defB,defBt 5 30; net_off
}

phase_defbp() {   # Nov 17, RESULTS-K 5.8: set B' (the recorders' production mode + run_delay in the probe) against the twin
  log "defbp: build (toolchain, agent with F_RD, hr-gordion:gordion9, sn-gordion:g8), smoke test, then B' x 7 cells"
  [ -f $R/k5build-run1.tgz ] || cp $R/k5build.tgz $R/k5build-run1.tgz
  mkdir -p $R/build-k5p
  HRTAG=gordion9 SNTAG=g8 bash $KD/build-k5p.sh > $R/build-k5p/build.log 2>&1; local rc=$?
  cp $KB/*.log $KB/dpkg-*.txt $R/build-k5p/ 2>/dev/null
  [ $rc = 0 ] || { log "build FAILED: $(grep 'BUILD FAILED' $R/build-k5p/build.log | tail -1)"; return 1; }
  log "build done: $(grep -E '^===' $R/build-k5p/build.log | tr '\n' ';')"
  N3 "sudo mkdir -p /var/tmp/k5 /var/tmp/gordion && sudo chmod 777 /var/tmp/k5; [ -d /sys/fs/resctrl/info ] || sudo mount -t resctrl resctrl /sys/fs/resctrl; sudo mkdir -p /sys/fs/cgroup/gordion-aggr; sudo sysctl -q -w kernel.bpf_stats_enabled=1"
  scp -q -o BatchMode=yes $KB/k5agent $KB/k5probe.bpf.o $KD/k5ctl.py $KD/k5score.py $KD/k5slice.py $HOME/k1/ugen/ugen $R/dict-rec.json $R/dict-uid.json $NODE:/var/tmp/k5/
  # smoke: the new probe loads with F_RD and the agent writes run_delay
  N3 "cd /var/tmp/k5 && sudo ./k5agent --obj ./k5probe.bpf.o --out /var/tmp/k5/smoke.jsonl --cpu 53 --cg sys=/sys/fs/cgroup/system.slice,victim=1+3 --probe switch --rq 0 --rd 1 --sib 1 --secs 3" > $R/build-k5p/smoke.log 2>&1
  N3 "grep -c Rrd /var/tmp/k5/smoke.jsonl; grep -o '\"Rrd\":[0-9]*' /var/tmp/k5/smoke.jsonl | tail -3; sudo rm -f /var/tmp/k5/smoke.jsonl" >> $R/build-k5p/smoke.log 2>&1
  grep -q '"Rrd":[0-9]' $R/build-k5p/smoke.log || { log "agent smoke FAILED: $(tail -3 $R/build-k5p/smoke.log | tr '\n' ' ')"; return 1; }
  log "agent smoke ok: $(tail -2 $R/build-k5p/smoke.log | tr '\n' ' ')"
  kubectl -n default scale deploy/bigsvc --replicas=0 >/dev/null
  local run; run="$(date +%Y%m%d-%H%M%S)-k5-uid"; echo $run > $R/run-uid.txt
  NS=sn SUT=unique-id-service INSTRUMENTED=unique-id-service NODE=$NODE CPUSET=1 TAG=g8 HOSTPID=1 OTHER_NODES=node-1,node-2 DB_NODE=node-1 WINDOW_MS=100 RAW=0 bash $SN deploy $run > $R/deploy-uid-defbp.log 2>&1
  local ctr; ctr=$(kubectl -n sn get deploy unique-id-service -o jsonpath='{.spec.template.spec.containers[0].name}')
  kubectl -n sn set image deploy/unique-id-service $ctr=10.10.1.1:5000/sn-gordion:g8 >/dev/null
  kubectl -n sn set env deploy/unique-id-service GORDION_INTERCEPT=off GORDION_READS=none GORDION_CTL=/data/k5-ctl-uid >/dev/null
  kubectl -n sn rollout status deploy/unique-id-service --timeout=240s >/dev/null
  # recommendation with the admission gate off: B' measures the timers, not the gate (rung 1 and 5.7's B + timers had it)
  run="$(date +%Y%m%d-%H%M%S)-k5-rec"; echo $run > $R/run-rec.txt
  (cd $HRD && NS=gordion SUT=recommendation NODE=$NODE CPUSET=5,7 TAG=gordion9 GATE=0 OTHER_NODES=node-1,node-2 WINDOW_MS=100 RAW=0 RESULTS=$R bash hr.sh deploy $run) > $R/deploy-rec-defbp.log 2>&1
  kubectl -n gordion set env deploy/recommendation GORDION_INTERCEPT=off GORDION_READS=none GORDION_CTL=/data/k5-ctl-rec >/dev/null
  kubectl -n gordion rollout status deploy/recommendation --timeout=240s >/dev/null
  sleep 5; ctl_off
  log "defbp prep: $(kubectl get pods -A -o wide --no-headers | grep -E 'unique-id-service|recommendation' | awk '{print $1, $2, $4, $8}' | tr '\n' ';') images: $(kubectl -n sn get deploy unique-id-service -o jsonpath='{.spec.template.spec.containers[0].image}') $(kubectl -n gordion get deploy recommendation -o jsonpath='{.spec.template.spec.containers[0].image}') pins: $(N3 "for p in \$(pgrep -f '[U]niqueIdService|[r]ecommendation'); do echo \$p \$(cat /proc/\$p/comm) \$(grep Cpus_allowed_list /proc/\$p/status | cut -f2); done" | tr '\n' ';')"
  local svc
  for svc in uid rec; do
    svc_vars $svc
    cell bp-$svc-none-mid $svc $V_MID:9999 none none,defBp 5 30
    cell bp-$svc-switch-mid $svc $V_MID:9999 switch none,defBp 5 30
    net_on $V_CPUS 50000 > /dev/null; cell bp-$svc-net-mid $svc $V_MID:9999 net none,defBp 5 30; net_off
  done
  svc_vars uid; net_on 1 50000 > /dev/null; cell bp-uid-net-7000 uid 7000:9999 net none,defBp 5 30; net_off
}

phase_test() {   # attended check before "all": two short cells, their arm records summarised
  svc_vars uid; cell t-uid uid $V_MID:9999 none none,full 1 15
  svc_vars rec; cell t-rec rec $V_MID:9999 switch none,full 1 15
  for c in $(grep " t-" $R/cells.txt | cut -d' ' -f1); do
    echo "== $c"; python3 $KD/k5summary.py $R/$c/ctl/cell.jsonl
    tail -2 $R/$c/ctl.log | cut -c1-300
  done
}

PHASE=${1:-all}
case $PHASE in
  all) for p in prep sweep a5 rung0 rung1 rung2 rung3 k6 rung4; do
         [ $p = prep ] && [ -f $R/run-rec.txt ] && [ "${REDO_PREP:-0}" != 1 ] && { log "prep already done (run-rec.txt exists)"; continue; }
         stopped && { log "STOP file: $p skipped"; continue; }; phase_$p; done; phase_restore; log DONE-K5-K6 ;;
  redo) phase_redo; phase_restore; log DONE-REDO ;;
  c5redo) phase_c5redo; phase_restore; log DONE-C5REDO ;;
  c3big) phase_c3big; phase_restore; log DONE-C3BIG ;;
  defb) phase_defb; phase_restore; log DONE-DEFB ;;
  defbp) phase_defbp; phase_restore; log DONE-DEFBP ;;
  prep|sweep|a5|rung0|rung1|rung2|rung3|k6|c5|rung4|restore|test) phase_$PHASE ;;
  *) echo "usage: $0 all|prep|sweep|a5|rung0|rung1|rung2|rung3|k6|c5|rung4|restore|test"; exit 2 ;;
esac
