#!/bin/bash
# Phase I step 7 queue (i10) on clnode270 node-0 (RESULTS-I 8; stage-3 RUNBOOK 18), run detached: HarvestContainers on
# node-4 (6.12.118-harvest, SMT off, 72 cores; HC addresses CPUs < 64, so 64-71 go offline for the queue), one K4.2e-style
# run at reserve 1 and reserve 3, recommendation at 5 000 req/s on CPUs 4,6,8,10 with the burner (Secondary) on core 12,
# standalone twins before and after, with the Phase I apparatus instead of K4's lean probe: k5agent with every signal
# (burner cgroup as a holder: phi, R_foreign) and the scorer on-node (arm sigS = sigF + scorer), 300 s per run. The
# recommendation dictionary at this placement comes from a sweep in the queue (arm sweepF, i2_dict on node-0).
# node-4 has no HWP (intel_cpufreq, schedutil, 0.8-3.5 GHz): the queue pins the experiment's CPUs to 2.4 GHz through
# cpufreq (scaling_min = scaling_max; restored from the snapshot) and leaves the other CPUs alone. HC is driven by K4's
# k4node.sh (writes only; never reads an HC procfs file); its logs go under /var/tmp/k4/logs, not /mnt/extra.
# Functions, prep and restore are queue-i9.sh's (i8's) with node-4 in place of node-3; the HC segments are queue-k45.sh's.
# Phases (one per call; "all" chains them): prep sweep hc restore. STOP file: $R/STOP.
set -u
export PATH=/usr/local/go1.25/bin:$HOME/go/bin:$PATH
R=${R:-/local/results/i10}; I2=/local/results/i2; KB=/local/k5build; KD=$HOME/k5; NODE=node-4; RCPU=30; WL=17,19,21,23,25,27
K4=$HOME/k4; B4=/var/tmp/k4/bin2; PCPUS=4,6,8,10; SCPUS=12; BAL_CPU=24; PIN_CPUS="4 6 8 10 12 13 14 15 17 19 21 23 24 25 27 30"; PIN_KHZ=2400000; OFF_CPUS="64 65 66 67 68 69 70 71"
HRD=/local/hr/hotelReservation/noisy-neighbors/dictionary; SN=/local/sn/sn.clean.sh; BS=/local/blueprint/examples/bigsvc
SYNTAG=k123i3; HWP=0x55001818
mkdir -p $R $R/specs $R/recorders /local/results/k5
cp -n $I2/mid-uid.txt $I2/mid-rec.txt $I2/mid-syn.txt $I2/memwalk-steps.txt $R/ 2>/dev/null   # step 2's mids and step counts
log() { echo "=== $(date -u +%H:%M:%S) $*"; }
N3() { ssh -n -o BatchMode=yes $NODE "$@"; }
stopped() { [ -f $R/STOP ]; }
MEAS=${MEAS:-300}

# ---------------------------------------------------------------- service facts
svc_vars() {   # uid | rec | syn -> V_*; V_MID from the mid phase on this node once it has run
  case $1 in
    uid) V_NS=sn V_LBL=service=unique-id-service V_CPUS=1 V_SIB=73 V_K=1 V_SLOTS=1 V_GO="" V_CTL=/var/tmp/gordion/k5-ctl-uid V_REC=/var/tmp/gordion/$(cat $R/run-uid.txt 2>/dev/null)-unique-id-service.jsonl V_DICT=/var/tmp/k5/dict-uid.json V_MID=10200 ;;
    rec) V_NS=gordion V_LBL=io.kompose.service=recommendation V_CPUS=$PCPUS V_SIB= V_K=4 V_SLOTS=4 V_GO=--go V_CTL=/mnt/extra/gordion/k5-ctl-rec V_REC=/mnt/extra/gordion/$(cat $R/run-rec.txt 2>/dev/null)-recommendation.jsonl V_DICT=/var/tmp/k5/dict-rec.json V_MID=5000 ;;
    syn) V_NS=default V_LBL=app=bigsvc V_CPUS=1,3,5,7 V_SIB=73,75,77,79 V_K=4 V_SLOTS=3 V_GO=--go V_CTL="" V_REC=/var/tmp/gordion/$(cat $R/run-syn.txt 2>/dev/null).jsonl V_DICT=/var/tmp/k5/dict-syn-memwalk128.json V_MID=400 ;;
  esac
  [ -s $R/mid-$1.txt ] && V_MID=$(cut -d' ' -f1 $R/mid-$1.txt)
  # the pod on the service node: right after a redeploy the old pod (another node) still reads Running while it terminates
  local pv; pv=$(kubectl -n $V_NS get pods -l $V_LBL --field-selector=status.phase=Running,spec.nodeName=$NODE -o jsonpath='{range .items[*]}{.metadata.uid} {.status.podIP} {.metadata.deletionTimestamp}{"\n"}{end}' | awk 'NF == 2' | head -1)
  V_UID=$(echo $pv | cut -d' ' -f1); V_IP=$(echo $pv | cut -d' ' -f2)
}

load_start() {   # svc cell schedule -> background pid in LP
  local svc=$1 cell=$2 sched=$3; LSVC=$svc; mkdir -p $R/$cell
  case $svc in
    uid) N3 "mkdir -p /var/tmp/k5 && taskset -c $WL /var/tmp/k5/ugen -addr $V_IP:9090 -conns 64 -warmup 10 -schedule $sched -out /var/tmp/k5/$cell-ugen.jsonl" > $R/$cell/load.log 2>&1 & LP=$! ;;
    rec) (cd $HRD && NS=gordion SUT=recommendation WLGEN_NODE=$NODE WLGEN_CPUS=$WL RESULTS=$R bash hr.sh wlgen $cell -- -mix single:recommend -fixed-params -k 4 -warmup 10 -schedule "$sched") > $R/$cell/load.log 2>&1 & LP=$! ;;
    syn) (cd $BS && TAG=$SYNTAG WLGEN_NODE=$NODE WLGEN_CPUS=$WL RESULTS=$R bash scripts/exp.sh wlgen $cell -- -mix ${SYN_MIX:-uniform} -warmup 10 -schedule "$sched") > $R/$cell/load.log 2>&1 & LP=$! ;;
  esac
}

load_stop() {
  # hrgen / wlgen (rec, syn) write their per-second records only when they exit normally: wait for the finite schedule
  if [ -n "${LP:-}" ] && [ "${LSVC:-uid}" != uid ]; then for i in $(seq 1 180); do kill -0 $LP 2>/dev/null || break; sleep 1; done; fi
  N3 "pkill -INT -f '/var/tmp/k5/[u]gen'; pkill -INT -x hrgen; pkill -INT -f '[w]lgen'; true"
  [ -n "${LP:-}" ] && { sleep 3; kill $LP 2>/dev/null; wait $LP 2>/dev/null; }
  LP=""
}

hwp_check() {   # node-4 has no HWP: the experiment's CPUs are pinned through cpufreq (min = max = PIN_KHZ); a drift is logged and re-applied
  local v; v=$(N3 "for c in $PIN_CPUS; do echo \$(cat /sys/devices/system/cpu/cpu\$c/cpufreq/scaling_min_freq)-\$(cat /sys/devices/system/cpu/cpu\$c/cpufreq/scaling_max_freq); done | sort | uniq -c | tr -s ' ' | tr '\n' ';'")
  echo "$(date -u +%FT%T) $v" >> $R/hwp-check.txt
  case "$v" in " 16 $PIN_KHZ-$PIN_KHZ;") ;; *) log "CPUFREQ PIN DRIFT: $v -> re-applied"; freq_pin; echo "$(date -u +%FT%T) re-applied" >> $R/hwp-check.txt ;; esac
}
freq_pin() {   # scaling_max first when raising the floor, min first when lowering (the kernel refuses min > max)
  N3 "for c in $PIN_CPUS; do d=/sys/devices/system/cpu/cpu\$c/cpufreq; echo $PIN_KHZ | sudo tee \$d/scaling_max_freq > /dev/null; echo $PIN_KHZ | sudo tee \$d/scaling_min_freq > /dev/null; echo $PIN_KHZ | sudo tee \$d/scaling_max_freq > /dev/null; done"
}
freq_restore() {   # from the snapshot taken in prep (cpu min max per line); N3 gives the remote shell no stdin, so the loop is built here
  local cmd; cmd=$(awk '{printf "d=/sys/devices/system/cpu/cpu%s/cpufreq; echo %s | sudo tee $d/scaling_min_freq >/dev/null; echo %s | sudo tee $d/scaling_max_freq >/dev/null; echo %s | sudo tee $d/scaling_min_freq >/dev/null; ", $1, $2, $3, $2}' $R/node4-freq-before.txt)
  N3 "$cmd true"
}

# cell NAME SVC SCHEDULE NB ARMS REPS MEASURE [extra k5spec args...]   (queue-k5.sh's cell, names "-i2-")
cell() {
  local name=$1 svc=$2 sched=$3 nb=$4 arms=$5 reps=$6 meas=$7; shift 7
  stopped && { log "STOP file: skipping $name"; return 1; }
  hwp_check
  local cell; cell="$(date +%Y%m%d-%H%M%S)-i2-$name"; mkdir -p $R/$cell
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
    stream*) nbargs="--nb stream --nb-cpus 9,11,13,15,17,19,21,23 --nb-n ${nb#stream}" ;;   # C7: N stress-ng streams, socket 1
    net) nbargs="--nb net --holder netsend=$(cut -d' ' -f1 $R/net-uids.txt) --holder netrecv=$(cut -d' ' -f2 $R/net-uids.txt)" ;;
    *) nbargs="--nb none" ;;
  esac
  python3 $KD/k5spec.py --cell $cell --out $R/specs/$cell.json --svc $svc --pod-uid $V_UID --cpus $V_CPUS --sib "$V_SIB" --k $V_K --slots $V_SLOTS $V_GO \
    ${V_CTL:+--ctl $V_CTL} --recorder $V_REC ${V_DICT:+--dict $V_DICT} --ctl-out /var/tmp/k5/$cell --arms $arms --reps $reps --measure $meas --reader-cpu $RCPU $nbargs "$@" > $R/$cell/spec.txt 2>&1 || { log "$cell: spec FAILED: $(cat $R/$cell/spec.txt)"; return 1; }
  scp -q -o BatchMode=yes $R/specs/$cell.json $NODE:/var/tmp/k5/$cell.json
  load_start $svc $cell "$sched"
  sleep 25
  log "$cell: $svc $sched nb $nb arms $arms x $reps x $meas s"
  N3 "sudo taskset -c $RCPU python3 /var/tmp/k5/k5ctl.py --spec /var/tmp/k5/$cell.json" > $R/$cell/ctl.log 2>&1   # pinned off the victims (Phase I 4: its /proc reads at arm boundaries)
  load_stop
  scp -q -r -o BatchMode=yes $NODE:/var/tmp/k5/$cell $R/$cell/ctl || log "$cell: fetch of the controller directory FAILED"
  case $svc in
    uid) scp -q -o BatchMode=yes $NODE:/var/tmp/k5/$cell-ugen.jsonl $R/$cell/ugen.jsonl 2>/dev/null; N3 "rm -f /var/tmp/k5/$cell-ugen.jsonl" ;;
    syn) mkdir -p $R/recorders; scp -q -o BatchMode=yes $NODE:$V_REC $R/recorders/ 2>/dev/null ;;
  esac
  [ "${KEEP_CTL_DIR:-0}" = 1 ] || N3 "sudo rm -rf /var/tmp/k5/$cell /var/tmp/k5/$cell.json"
  echo "$cell $name $svc $sched $nb $arms $reps $meas" >> $R/cells.txt
  log "$cell done: $(grep -c '"event":"arm"' $R/$cell/ctl/cell.jsonl 2>/dev/null) arm records; $(grep -c error $R/$cell/ctl/cell.jsonl 2>/dev/null) errors"
}

ctl_off() { N3 "sudo mkdir -p /mnt/extra/gordion; echo 'intercept=off reads=none' | sudo tee /var/tmp/gordion/k5-ctl-rec /mnt/extra/gordion/k5-ctl-rec > /dev/null"; }

syn_deploy() {   # NAME MEMWALK_MB MEMWALK_STEPS: a fresh bigsvc pod on CPUs 1,3,5,7 (it calibrates with nothing else running)
  local run; run="$(date +%Y%m%d-%H%M%S)-i2-syn-$1"; echo $run > $R/run-syn.txt
  (cd $BS && NODE=$NODE CPUSET=1,3,5,7 TAG=$SYNTAG HOSTDIR=/var/tmp/gordion GATE=1 WINDOW_MS=100 RAW=1 WLGEN_NODE=$NODE WLGEN_CPUS=$WL STRESSOR=none MEMWALK_MB=$2 MEMWALK_STEPS=$3 RESULTS=$R bash scripts/exp.sh deploy $run) > $R/deploy-syn-$1.log 2>&1
  kubectl -n default rollout status deploy/bigsvc --timeout=240s > /dev/null; sleep 8
  kubectl -n default logs deploy/bigsvc 2>/dev/null | grep bigsvc_calibration | tail -1 > $R/calib-syn-$1.json
}
syn_off() { kubectl -n default scale deploy/bigsvc --replicas=0 > /dev/null 2>&1; sleep 5; }

steps_of() {   # MB -> fixed memwalk step count from $R/memwalk-steps.txt ("MB N" lines; 0 if not calibrated)
  awk -v m=$1 '$1 == m {print $2}' $R/memwalk-steps.txt 2>/dev/null | tail -1 | grep . || echo 0
}

# ---------------------------------------------------------------- phases
phase_prep() {
  log "I10 prep"
  for ns in gordion sn; do kubectl -n $ns get deploy -o json > $R/pre-i2-$ns.json; done
  kubectl -n default get deploy bigsvc -o json > $R/pre-i2-bigsvc.json 2>/dev/null || rm -f $R/pre-i2-bigsvc.json
  kubectl get pods -A -o wide --no-headers > $R/pods-before-i2.txt
  N3 "sudo mkdir -p /var/tmp/k5 /var/tmp/gordion && sudo chmod 777 /var/tmp/k5; grep -c resctrl /proc/mounts" > $R/node3-resctrl-before.txt
  N3 "cat /proc/sys/kernel/bpf_stats_enabled" > $R/node3-bpfstats-before.txt
  scp -q -o BatchMode=yes $KB/k5agent $KB/k5probe.bpf.o $KD/k5ctl.py $KD/k5score.py $KD/k5slice.py $HOME/k1/ugen/ugen /local/results/i4/dict/dict-uid.json /local/results/i4/dict/dict-rec.json $NODE:/var/tmp/k5/
  scp -q -o BatchMode=yes /local/results/i7/dict/dict-uid.json $NODE:/var/tmp/k5/dict-uid.json; scp -q -o BatchMode=yes /local/results/i5/dict/dict-syn-memwalk30-128.k5.json $NODE:/var/tmp/k5/dict-syn-memwalk128.json
  N3 "[ -d /sys/fs/resctrl/info ] || sudo mount -t resctrl resctrl /sys/fs/resctrl; ls /sys/fs/resctrl/info | tr '\n' ' '; sudo mkdir -p /sys/fs/cgroup/gordion-aggr; sudo sysctl -q -w kernel.bpf_stats_enabled=1; cat /proc/sys/kernel/bpf_stats_enabled" > $R/node3-prep.txt 2>&1
  # read-only: where the NIC's interrupts land, and the idle states and clock left on node-3
  N3 "for i in \$(awk -F: '/mlx5/{print \$1}' /proc/interrupts); do echo \"\$i \$(cat /proc/irq/\$i/smp_affinity_list) \$(cat /proc/irq/\$i/effective_affinity_list 2>/dev/null)\"; done" > $R/node3-nic-irqs.txt 2>&1
  N3 "cat /sys/devices/system/cpu/online; cat /sys/devices/system/cpu/cpu4/cpufreq/scaling_driver /sys/devices/system/cpu/cpu4/cpufreq/scaling_governor; cat /sys/devices/system/cpu/cpu4/cpuidle/state*/name | tr '\n' ' '" > $R/node4-idle-hwp.txt 2>&1
  N3 "cat /sys/devices/system/cpu/online" > $R/node4-online-before.txt
  N3 "for c in $PIN_CPUS; do d=/sys/devices/system/cpu/cpu\$c/cpufreq; echo \$c \$(cat \$d/scaling_min_freq) \$(cat \$d/scaling_max_freq); done" > $R/node4-freq-before.txt
  N3 "sudo mkdir -p $B4 /var/tmp/k4/logs && sudo chmod -R 777 /var/tmp/k4"; scp -q -o BatchMode=yes $K4/k4node.sh $K4/k4send.py $NODE:$B4/
  N3 "sudo bash $B4/k4node.sh status" > $R/node4-hc-before.txt 2>&1
  N3 "for c in $OFF_CPUS; do echo 0 | sudo tee /sys/devices/system/cpu/cpu\$c/online > /dev/null; done; cat /sys/devices/system/cpu/online" > $R/node4-online-prep.txt
  freq_pin; hwp_check
  kubectl get ns k4 >/dev/null 2>&1 || kubectl create ns k4 >/dev/null; kubectl apply -f $K4/k4b-secondary.yaml > $R/burner-apply.log 2>&1
  log "node-4: online $(cat $R/node4-online-prep.txt), pinned $PIN_CPUS at $PIN_KHZ kHz, HC before: $(head -1 $R/node4-hc-before.txt)"
  syn_off
  local run
  run="$(date +%Y%m%d-%H%M%S)-i2-rec"; echo $run > $R/run-rec.txt
  (cd $HRD && NS=gordion SUT=recommendation NODE=$NODE CPUSET=$PCPUS TAG=gordion9 GATE=0 OTHER_NODES=node-1,node-2 WINDOW_MS=100 RAW=0 RESULTS=$R bash hr.sh deploy $run) > $R/deploy-rec.log 2>&1
  kubectl -n gordion set env deploy/recommendation GORDION_INTERCEPT=off GORDION_READS=none GORDION_CTL=/data/k5-ctl-rec >/dev/null
  kubectl -n gordion rollout status deploy/recommendation --timeout=240s >/dev/null
  sleep 5; ctl_off
  kubectl get pods -A -o wide --no-headers | grep -E "recommendation|hc-secondary" | awk '{print $1, $2, $4, $8}' > $R/pods-i2.txt
  N3 "for p in \$(pgrep -f '[r]ecommendation'); do echo \$p \$(cat /proc/\$p/comm) \$(grep Cpus_allowed_list /proc/\$p/status | cut -f2); done" > $R/pins-i2.txt 2>&1
  log "prep done: $(tr '\n' ';' < $R/pods-i2.txt) pins: $(tr '\n' ';' < $R/pins-i2.txt) images: $(kubectl -n gordion get deploy recommendation -o jsonpath='{.spec.template.spec.containers[0].image}')"
}

sched_of() {   # SVC MIXFILE|uniform STEP_S -> rho 0.1 .. 0.9 schedule from this node's mid (bigsvc: scaled by cbar)
  local mid; case $1 in uid) mid=10200 ;; rec) mid=3600 ;; syn) mid=400 ;; esac   # clnode170's, if the mid phase has not run
  [ -s $R/mid-$1.txt ] && mid=$(cut -d' ' -f1 $R/mid-$1.txt)
  python3 - $1 $2 $3 $mid $BS/workflow/bigsvc/floors.json <<'EOF'
import json, sys
svc, mix, step, mid, floors = sys.argv[1], sys.argv[2], int(sys.argv[3]), float(sys.argv[4]), sys.argv[5]
scale = 1.0
if svc == "syn":
    c = [e["cpu_ms"] for e in json.load(open(floors))]
    cb = lambda w: sum(a * b for a, b in zip(w, c)) / sum(w)
    w = [1.0] * len(c) if mix == "uniform" else json.load(open(mix.replace("file:", "")))
    scale = cb([1.0] * len(c)) / cb(w + [0.0] * (len(c) - len(w)))
print(",".join(f"{max(1, round(mid * scale * r / 0.5))}:{step}" for r in (0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9)))
EOF
}

hc_unload() { N3 "sudo bash $B4/k4node.sh hc-unload" >> $R/$1/hc.log 2>&1; }
sec_down() { kubectl -n k4 scale deploy/hc-secondary-burner --replicas=0 >/dev/null 2>&1; for i in $(seq 1 60); do [ -z "$(kubectl -n k4 get pods --no-headers 2>/dev/null)" ] && break; sleep 2; done; }
sec_up() {   # prints the burner pod's UID
  kubectl -n k4 scale deploy/hc-secondary-burner --replicas=1 >/dev/null
  kubectl -n k4 rollout status deploy/hc-secondary-burner --timeout=180s >/dev/null; sleep 3
  kubectl -n k4 get pods -l app=hc-secondary-burner --field-selector=status.phase=Running -o jsonpath='{.items[0].metadata.uid}'
}

phase_sweep() {   # recommendation's dictionary at this placement (4 cores, node-4), fit on node-0, shipped for the scorer
  log "sweep: recommendation on node-4 CPUs $PCPUS, 2 000-18 000 req/s, arm sweepF"
  hc_unload prep 2>/dev/null; sec_down
  KEEP_CTL_DIR=1 cell sweep-rec rec "2000:30,4000:30,6000:30,8000:30,10000:30,12000:30,14000:30,16000:30,18000:30" none sweepF 1 250
  svc_vars rec; scp -q -o BatchMode=yes $NODE:$V_REC $R/recorders/
  python3 $BS/analysis/i2_dict.py $R --out $R/dict > $R/dict.log 2>&1 || log "i2_dict FAILED: $(tail -3 $R/dict.log)"
  [ -s $R/dict/dict-rec.json ] && scp -q -o BatchMode=yes $R/dict/dict-rec.json $NODE:/var/tmp/k5/dict-rec.json && log "dictionary: $(grep -o 'fit rec.*' $R/dict.log | head -1)" || log "no dictionary: the scorer will fail in the hc cells"
}

hc_cell() {   # NAME RESERVE: HC loaded, the burner on core 12, the Balancer at TIC = LOW_TIC = RESERVE; the cell with the burner as a holder; then off
  local name=$1 res=$2 suid cell0
  mkdir -p $R/$name
  N3 "sudo bash $B4/k4node.sh hc-load" > $R/$name/hc.log 2>&1 || { log "$name: hc-load FAILED: $(tail -2 $R/$name/hc.log)"; return 1; }
  sec_down; suid=$(sec_up)
  N3 "sudo bash $B4/k4node.sh sec-pin $suid" >> $R/$name/hc.log 2>&1; sleep 2
  N3 "sudo TIC=$res LOW_TIC=$res LOGDIR=/var/tmp/k4/logs/$name bash $B4/k4node.sh hc-on $suid" >> $R/$name/hc.log 2>&1 || log "$name: hc-on FAILED (see hc.log)"
  sleep 10
  cell $name rec 5000:9999 none sigS 1 300 --holder burner=$suid
  N3 "sudo bash $B4/k4node.sh hc-off" >> $R/$name/hc.log 2>&1
  mkdir -p $R/$name/balancer; scp -q -o BatchMode=yes $NODE:/var/tmp/k4/logs/$name/stats.log $NODE:/var/tmp/k4/logs/$name/util.log $NODE:/var/tmp/k4/logs/$name/dynamic.log $R/$name/balancer/ 2>/dev/null
  sec_down; hc_unload $name
  log "$name done: $(grep -c . $R/$name/balancer/stats.log 2>/dev/null) balancer stats lines; hc: $(grep -E 'HC on|HC off|unloaded' $R/$name/hc.log | tr '\n' ' ' | cut -c1-200)"
}

phase_hc() {   # S0 twin, reserve 1, reserve 3, S1 twin; 5 000 req/s, 300 s each, arm sigS (every signal + the scorer on-node)
  log "hc: twin S0, reserve 1, reserve 3, twin S1 at 5 000 req/s"
  mkdir -p $R/c5-rec-twin-S0; hc_unload c5-rec-twin-S0; sec_down
  cell c5-rec-twin-S0 rec 5000:9999 none sigS 1 300
  hc_cell c5-rec-res1 1
  hc_cell c5-rec-res3 3
  cell c5-rec-twin-S1 rec 5000:9999 none sigS 1 300
}

phase_restore() {   # back to the state before prep (queue-k5.sh's restore without the toolchain purge), then checked
  log "restore"
  load_stop
  N3 "sudo bash $B4/k4node.sh hc-off; sudo bash $B4/k4node.sh hc-unload" > $R/restore-hc.log 2>&1
  sec_down; kubectl delete ns k4 --wait=true >> $R/restore-hc.log 2>&1
  N3 "sudo pkill -INT k5agent; sudo pkill -INT -f '[k]5score.py'; sudo pkill -TERM -f '[k]5ctl.py'; sudo pkill -TERM stress-ng; sleep 2; sudo pkill -9 stress-ng; sudo rmdir /sys/fs/cgroup/gordion-aggr 2>/dev/null; sudo rmdir /sys/fs/resctrl/k5v/mon_groups/* /sys/fs/resctrl/k5n/mon_groups/* /sys/fs/resctrl/k5v /sys/fs/resctrl/k5n 2>/dev/null; sudo sysctl -q -w kernel.bpf_stats_enabled=$(cat $R/node3-bpfstats-before.txt 2>/dev/null || echo 0); true"
  svc_vars uid 2>/dev/null; scp -q -o BatchMode=yes $NODE:$V_REC $R/recorders/ 2>/dev/null
  svc_vars rec 2>/dev/null; scp -q -o BatchMode=yes $NODE:$V_REC $R/recorders/ 2>/dev/null
  N3 "mkdir -p /var/tmp/i2keep; sudo mv /var/tmp/k5/dict-*.json /var/tmp/i2keep/ 2>/dev/null; sudo rm -f /var/tmp/gordion/*-i2-*.jsonl /var/tmp/gordion/k5-ctl-*; sudo rm -rf /var/tmp/k5 /tmp/wlres/*-i2-* /tmp/wlres/mid-*; true"
  [ "$(cat $R/node3-resctrl-before.txt 2>/dev/null)" = 0 ] && N3 "sudo umount /sys/fs/resctrl 2>/dev/null; true"
  freq_restore
  N3 "for c in $OFF_CPUS; do echo 1 | sudo tee /sys/devices/system/cpu/cpu\$c/online > /dev/null; done; sudo rm -rf /var/tmp/k4 /mnt/extra/gordion; true"
  python3 - $R/pre-i2-gordion.json $R/pre-i2-sn.json $( [ -f $R/pre-i2-bigsvc.json ] && echo $R/pre-i2-bigsvc.json ) <<'EOF' > $R/restore-specs.yaml
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
  # replace, not apply: before prep the deployments were as installed, and apply would keep what prep added (nodeSelector,
  # env, volumes, hostPID) because none of it is in their last-applied configuration
  kubectl replace -f $R/restore-specs.yaml > $R/restore-apply.log 2>&1
  grep -q GORDION_CTL $R/pre-i2-gordion.json || kubectl -n gordion set env deploy/recommendation GORDION_INTERCEPT- GORDION_READS- GORDION_CTL- >> $R/restore-apply.log 2>&1
  grep -q GORDION_CTL $R/pre-i2-sn.json || kubectl -n sn set env deploy/unique-id-service GORDION_INTERCEPT- GORDION_READS- GORDION_CTL- >> $R/restore-apply.log 2>&1
  python3 -c "import json,sys; d=json.load(open(sys.argv[1])); sys.exit(any(i['metadata']['name']=='unique-id-service' and 'hostPID' in i['spec']['template']['spec'] for i in d.get('items',[d])))" $R/pre-i2-sn.json && kubectl -n sn patch deploy unique-id-service --type=json -p '[{"op":"remove","path":"/spec/template/spec/hostPID"}]' >> $R/restore-apply.log 2>&1
  [ -f $R/pre-i2-bigsvc.json ] || kubectl -n default delete deploy bigsvc --ignore-not-found >> $R/restore-apply.log 2>&1
  kubectl -n default delete svc bigsvc --ignore-not-found >> $R/restore-apply.log 2>&1
  for ns in gordion sn default; do for d in $(kubectl -n $ns get deploy -o name); do kubectl -n $ns rollout status $d --timeout=300s >/dev/null 2>&1; done; done
  kubectl get pods -A -o wide --no-headers > $R/pods-after-i2.txt
  # field-by-field: every deployment's spec as before prep (the restore's own annotations aside)
  for ns in gordion sn; do kubectl -n $ns get deploy -o json > $R/post-i2-$ns.json; done
  python3 - $R <<'EOF'
import json, sys
R = sys.argv[1]; nd = 0; diffs = []
def specs(p):
    d = json.load(open(p)); out = {}
    for it in d["items"]:
        t = it["spec"]["template"]; t.get("metadata", {}).get("annotations", {}).pop("kubectl.kubernetes.io/restartedAt", None)
        out[it["metadata"]["name"]] = (it["spec"].get("replicas"), t)
    return out
for ns in ("gordion", "sn"):
    a, b = specs(f"{R}/pre-i2-{ns}.json"), specs(f"{R}/post-i2-{ns}.json")
    for k in sorted(set(a) | set(b)):
        nd += 1
        if a.get(k) != b.get(k): diffs.append(f"{ns}/{k}")
print(f"deployments compared: {nd}; differing from before prep: {diffs if diffs else 'none'}")
EOF
  python3 - $R/pods-before-i2.txt $R/pods-after-i2.txt <<'EOF'
import sys
def rd(p): return {(l.split()[0], l.split()[1].rsplit('-', 2)[0]): l.split()[7] for l in open(p) if l.strip() and l.split()[3] == 'Running'}
a, b = rd(sys.argv[1]), rd(sys.argv[2])
diff = [(k, a.get(k), b.get(k)) for k in sorted(set(a) | set(b)) if a.get(k) != b.get(k)]
print(f"running pods before/after: {len(a)}/{len(b)}; node differences (ns, deploy, before -> after): {diff if diff else 'none'}")
EOF
  echo "node-4 after: online $(N3 cat /sys/devices/system/cpu/online); freq $(N3 "for c in $PIN_CPUS; do echo \$(cat /sys/devices/system/cpu/cpu\$c/cpufreq/scaling_min_freq)-\$(cat /sys/devices/system/cpu/cpu\$c/cpufreq/scaling_max_freq); done | sort | uniq -c | tr -s ' ' | tr '\n' ';'"); HC $(N3 "sudo bash /var/tmp/k4/bin2/k4node.sh status 2>/dev/null | head -1 || lsmod | grep -c qidlecpu"); agents $(N3 "pgrep -c k5agent"); k4 ns $(kubectl get ns k4 --no-headers 2>&1 | head -1)"
  log "restore done"
}

PHASE=${1:-all}
case $PHASE in
  all) for p in ${PHASES:-prep sweep hc}; do stopped && { log "STOP file: $p skipped"; continue; }; phase_$p; done; phase_restore; log DONE-I10 ;;
  *) phase_$PHASE; log "DONE-$PHASE" ;;
esac
