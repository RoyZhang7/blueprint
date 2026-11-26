#!/bin/bash
# Phase J0'' queue (RESULTS-J 2; stage-3 RUNBOOK 18), run detached on a fresh node-0: the busy node near the knee. Node-3 at
# its as-found settings (J0: HWP released, C1E/C6 on, uncore as saved, msr unloaded), nothing pinned by cpuset or taskset. A
# background of the DSB social-network chain (the SN services on node-3, unpinned, under a steady compose load from hrgen on
# node-1) plus bigsvc memwalk tenants (unpinned, cpu.max 4 each, uniform mix at a fixed rate) stepped up until node-3's CPU
# utilization sits in 60-70 %; the recipe (pods, rates) is written to $R/recipe.json and node utilization + the clock of the
# busy CPUs are logged per 100 ms window for the whole queue (nodewatch agent on CPU 53: /proc/stat of every CPU, per-CPU
# cycles / ref-cycles events). Victims unique-id (cpu.max 1) and recommendation (cpu.max 4): load stepped to the knee (served
# < 98 % of offered, or client p50 over 2x the first step's), rho per step from cgroup usage / (k x wall), then held at the
# rate of rho 0.75 for the ladder subset: rows 1, 5-7, 9-10, 14 (j-probe, j-count, j-slot, j-full, j-perf, j-rdt, j-all), a
# twin before and after each, 3 x 60 s. Restore from the EXIT trap: tenants and the background load gone, deployments as
# before prep, node-3 re-pinned to the pre-state, closing checks read-only; $R rsynced to the project filesystem after every
# phase. Phases (one per call; "all" chains them): prep busy knee ladder. STOP file: $R/STOP. NO_RESTORE=1 for a single phase.
set -u
export PATH=/usr/local/go1.25/bin:$HOME/go/bin:$PATH
R=${R:-/local/results/j0b}; KB=/local/k5build; KD=$HOME/k5; NODE=node-3; RCPU=53; WL=16,18,20,22,24,26; BGCPUS=28,30,32,34,36,38
HRD=/local/hr/hotelReservation/noisy-neighbors/dictionary; SN=/local/sn/sn.clean.sh; BS=/local/blueprint/examples/bigsvc
PROJ=${PROJ:-/proj/tracing-pythia-PG0/royno7-j-archive-$(hostname -f | cut -d. -f1)-$(date +%Y-%m-%d)}
UIDTAG=${UIDTAG:-g9}; RECTAG=gordion9; SYNTAG=${SYNTAG:-k123i3}
MEAS=${MEAS:-60}; REPS=${REPS:-3}; UTIL_LO=${UTIL_LO:-0.60}; UTIL_HI=${UTIL_HI:-0.70}; RHO_HOLD=${RHO_HOLD:-0.75}
TENANT_RATE=${TENANT_RATE:-300}; TENANT_MAX=${TENANT_MAX:-24}; DSB_RATE=${DSB_RATE:-600}
mkdir -p $R $R/specs $R/recorders /local/results/k5 $PROJ/j0b
log() { echo "=== $(date -u +%H:%M:%S) $*"; }
N3() { ssh -n -o BatchMode=yes $NODE "$@"; }
stopped() { [ -f $R/STOP ]; }
archive() { rsync -a --exclude 'recorders/*' $R/ $PROJ/j0b/ 2>> $R/archive.log && echo "$(date -u +%FT%T) archived" >> $R/archive.log; }

# ---------------------------------------------------------------- service facts (unpinned: CPUs "all", k from cpu.max)
svc_vars() {
  case $1 in
    uid) V_NS=sn V_LBL=service=unique-id-service V_CPUS=all V_SIB="" V_K=1 V_SLOTS=1 V_GO="" V_CTL=/var/tmp/gordion/k5-ctl-uid V_REC=/var/tmp/gordion/$(cat $R/run-uid.txt 2>/dev/null)-unique-id-service.jsonl V_DICT=/var/tmp/k5/dict-uid.json V_MID=2591 ;;
    rec) V_NS=gordion V_LBL=io.kompose.service=recommendation V_CPUS=all V_SIB="" V_K=4 V_SLOTS=3 V_GO=--go V_CTL=/var/tmp/gordion/k5-ctl-rec V_REC=/var/tmp/gordion/$(cat $R/run-rec.txt 2>/dev/null)-recommendation.jsonl V_DICT=/var/tmp/k5/dict-rec.json V_MID=3976 ;;
  esac
  [ -s $R/hold-$1.txt ] && V_MID=$(cut -d' ' -f1 $R/hold-$1.txt)
  local pv; pv=$(kubectl -n $V_NS get pods -l $V_LBL --field-selector=status.phase=Running,spec.nodeName=$NODE -o jsonpath='{range .items[*]}{.metadata.uid} {.status.podIP} {.metadata.deletionTimestamp}{"\n"}{end}' | awk 'NF == 2' | head -1)
  V_UID=$(echo $pv | cut -d' ' -f1); V_IP=$(echo $pv | cut -d' ' -f2)
}
pod_cg() { N3 "find /sys/fs/cgroup/kubepods.slice -maxdepth 3 -type d -name '*pod$(echo $1 | tr - _).slice' | head -1"; }

load_start() {   # svc cell schedule -> background pid in LP
  local svc=$1 cell=$2 sched=$3; LSVC=$svc; mkdir -p $R/$cell
  case $svc in
    uid) N3 "mkdir -p /var/tmp/k5 && taskset -c $WL /var/tmp/k5/ugen -addr $V_IP:9090 -conns 64 -warmup 10 -schedule $sched -out /var/tmp/k5/$cell-ugen.jsonl" > $R/$cell/load.log 2>&1 & LP=$! ;;
    rec) (cd $HRD && NS=gordion SUT=recommendation WLGEN_NODE=$NODE WLGEN_CPUS=$WL RESULTS=$R bash hr.sh wlgen $cell -- -mix single:recommend -fixed-params -k 2 -warmup 10 -schedule "$sched") > $R/$cell/load.log 2>&1 & LP=$! ;;
  esac
}
load_stop() {
  if [ -n "${LP:-}" ] && [ "${LSVC:-uid}" != uid ]; then for i in $(seq 1 180); do kill -0 $LP 2>/dev/null || break; sleep 1; done; fi
  N3 "pkill -INT -f '/var/tmp/k5/[u]gen'; pkill -INT -x hrgen; pkill -INT -f '[w]lgen'; true"
  [ -n "${LP:-}" ] && { sleep 3; kill $LP 2>/dev/null; wait $LP 2>/dev/null; }
  LP=""
}
ctl_off() { N3 "echo 'intercept=off reads=none sample=8' | sudo tee /var/tmp/gordion/k5-ctl-uid /var/tmp/gordion/k5-ctl-rec > /dev/null"; }
served_of() {   # ugen file on node-3 -> "served/s client_p50_ms client_p99_ms"
  N3 "python3 - $1 <<'EOF'
import json, sys, statistics
v = []
for l in open(sys.argv[1]):
    try: v.append(json.loads(l))
    except Exception: pass
d = [x['done'] for x in v if isinstance(x, dict) and 'done' in x and 'event' not in x]; p = [x['lat_p50_us'] for x in v if isinstance(x, dict) and x.get('lat_p50_us')]; q = [x['lat_p99_us'] for x in v if isinstance(x, dict) and x.get('lat_p99_us')]
print(statistics.median(d) if d else 0, round(statistics.median(p) / 1000, 3) if p else 0, round(statistics.median(q) / 1000, 3) if q else 0)
EOF"
}
served_rec() {   # wlgen.jsonl of a rec run dir -> "served/s client_p50_ms client_p99_ms"
  python3 - $1 <<'EOF'
import json, sys, statistics
v = []
for l in open(sys.argv[1]):
    try:
        x = json.loads(l)
        if "all" in x and x.get("rate", 0) > 0: v.append(x)
    except Exception: pass
print(statistics.median(x["all"][0] for x in v) if v else 0, round(statistics.median(x["all"][1] for x in v) / 1e6, 3) if v else 0, round(statistics.median(x["all"][3] for x in v) / 1e6, 3) if v else 0)
EOF
}
node_util() {   # SECS -> node-3's busy share of all CPUs over SECS (read-only, /proc/stat)
  N3 "python3 - $1 <<'EOF'
import time, sys
def rd():
    f = open('/proc/stat').readline().split()[1:9]; f = [int(x) for x in f]
    return sum(f), f[3] + f[4]
a = rd(); time.sleep(float(sys.argv[1])); b = rd()
print(round(1 - (b[1] - a[1]) / max(1, b[0] - a[0]), 4))
EOF"
}

# cell NAME SVC SCHEDULE NB ARMS REPS MEASURE
cell() {
  local name=$1 svc=$2 sched=$3 nb=$4 arms=$5 reps=$6 meas=$7; shift 7
  stopped && { log "STOP file: skipping $name"; return 1; }
  local cell; cell="$(date +%Y%m%d-%H%M%S)-j0b-$name"; mkdir -p $R/$cell
  svc_vars $svc
  local narms; narms=$(echo "$arms" | tr ',' '\n' | wc -l)
  local need=$(( reps * narms * (meas + 8) + 90 ))
  [ $need -gt 5000 ] && log "WARNING: cell $name needs $need s; hrgen's per-second log stops after ~5 200 s (J1): keep cells under 85 min"
  sched="${sched%%:*}:$need"
  python3 $KD/k5spec.py --cell $cell --out $R/specs/$cell.json --svc $svc --pod-uid $V_UID --cpus $V_CPUS --k $V_K --slots $V_SLOTS $V_GO \
    ${V_CTL:+--ctl $V_CTL} --recorder $V_REC ${V_DICT:+--dict $V_DICT} --ctl-out /var/tmp/k5/$cell --arms $arms --reps $reps --measure $meas --reader-cpu $RCPU --nb none \
    --replay-recorder /var/tmp/k5/replay-rec.jsonl --replay-agent /var/tmp/k5/replay-agent.jsonl "$@" > $R/$cell/spec.txt 2>&1 || { log "$cell: spec FAILED: $(cat $R/$cell/spec.txt)"; return 1; }
  scp -q -o BatchMode=yes $R/specs/$cell.json $NODE:/var/tmp/k5/$cell.json
  load_start $svc $cell "$sched"
  sleep 25
  log "$cell: $svc $sched arms $arms x $reps x $meas s (k $V_K, pod $V_UID); node util $(node_util 5)"
  N3 "sudo taskset -c $RCPU python3 /var/tmp/k5/k5ctl.py --spec /var/tmp/k5/$cell.json" > $R/$cell/ctl.log 2>&1
  load_stop
  scp -q -r -o BatchMode=yes $NODE:/var/tmp/k5/$cell $R/$cell/ctl || log "$cell: fetch of the controller directory FAILED"
  case $svc in uid) scp -q -o BatchMode=yes $NODE:/var/tmp/k5/$cell-ugen.jsonl $R/$cell/ugen.jsonl 2>/dev/null; N3 "rm -f /var/tmp/k5/$cell-ugen.jsonl" ;; esac
  N3 "sudo rm -rf /var/tmp/k5/$cell /var/tmp/k5/$cell.json"
  echo "$cell $name $svc $sched $nb $arms $reps $meas" >> $R/cells.txt
  log "$cell done: $(grep -c '"event":"arm"' $R/$cell/ctl/cell.jsonl 2>/dev/null) arm records; $(grep -c error $R/$cell/ctl/cell.jsonl 2>/dev/null) errors"
  archive
}

# ---------------------------------------------------------------- J0: node-3 defaults, snapshots, restore (queue-j1.sh's)
node3_pre() {
  N3 "sudo python3 /var/tmp/i2/msr.py read 0x774 0-143" > $R/node3-hwp-pre.txt 2>&1
  awk '{print $2}' $R/node3-hwp-pre.txt | sort | uniq -c | tr -s ' ' | tr '\n' ';' > $R/node3-hwp-pre-summary.txt
  N3 "for s in 2 3; do cat /sys/devices/system/cpu/cpu*/cpuidle/state\$s/disable | sort | uniq -c | tr '\n' ' '; done; echo; cat /sys/devices/system/cpu/intel_uncore_frequency/*/m??_freq_khz | tr '\n' ' '; echo; lsmod | grep -c '^msr '; grep -c resctrl /proc/mounts; cat /proc/sys/kernel/bpf_stats_enabled" > $R/node3-state-pre.txt 2>&1
  cat $R/node3-hwp-pre-summary.txt $R/node3-state-pre.txt | tr '\n' '|'
}
node3_defaults() {
  N3 "cd /var/tmp/i2 && sudo bash i2node3.sh hwp-restore > /dev/null; sudo bash i2node3.sh cstate-restore | tr '\n' ' '; sudo bash i2node3.sh uncore-restore | tr '\n' ' '; sudo bash i2node3.sh msr-unload; true" > $R/node3-j0.log 2>&1
  N3 "sudo modprobe msr; sudo python3 /var/tmp/i2/msr.py read 0x774 0-143 | awk '{print \$2}' | sort | uniq -c | tr -s ' ' | tr '\n' ';'; sudo rmmod msr; echo; for s in 2 3; do cat /sys/devices/system/cpu/cpu*/cpuidle/state\$s/disable | sort | uniq -c | tr '\n' ' '; done; echo; cat /sys/devices/system/cpu/intel_uncore_frequency/*/m??_freq_khz | tr '\n' ' '; echo; lsmod | grep -c '^msr '" > $R/node3-state-j0.txt 2>&1
  cat $R/node3-state-j0.txt | tr '\n' '|'
}
node3_repin() {
  N3 "sudo modprobe msr; sudo python3 /var/tmp/i2/msr.py restore 0x774 /tmp/node3-hwp-pre.txt > /dev/null; cd /var/tmp/i2 && sudo bash i2node3.sh c1e-off; sudo bash i2node3.sh cstate-off; sudo bash i2node3.sh uncore-pin | tr '\n' ' '; [ \"$(sed -n 3p $R/node3-state-pre.txt)\" = 1 ] || sudo rmmod msr; true" >> $R/node3-restore.log 2>&1
}
set_limit() { kubectl -n $1 set resources deploy/$2 --limits=cpu=$3 --requests=cpu=$3 > /dev/null; kubectl -n $1 rollout status deploy/$2 --timeout=240s > /dev/null; }

nodewatch_start() {   # the whole-queue log: node utilization (/proc/stat of every CPU, "pst") and the busy CPUs' clock ("clk_busy") per 100 ms
  N3 "sudo taskset -c $RCPU nohup /var/tmp/k5/k5agent --out /var/tmp/k5/nodewatch.jsonl --cpu $RCPU --tick-ms 100 --clock-cpus all --sampler 1 --cg node=/sys/fs/cgroup/kubepods.slice,cpus=all > /var/tmp/k5/nodewatch.log 2>&1 < /dev/null &"
  sleep 2; log "nodewatch: $(N3 'pgrep -c k5agent') agent(s), $(N3 'tail -1 /var/tmp/k5/nodewatch.log')"
}
nodewatch_stop() { N3 "sudo pkill -INT -f 'k5agent --out /var/tmp/k5/[n]odewatch'; sleep 2; true"; scp -q -o BatchMode=yes $NODE:/var/tmp/k5/nodewatch.jsonl $NODE:/var/tmp/k5/nodewatch.log $R/ 2>/dev/null; }

phase_prep() {
  log "J0'' prep: pre-state, J0 node defaults, victims unpinned with cpu.max, nodewatch"
  for ns in gordion sn default; do kubectl -n $ns get deploy -o json > $R/pre-j0b-$ns.json; done
  kubectl get pods -A -o wide --no-headers > $R/pods-before-j0b.txt
  N3 "sudo mkdir -p /var/tmp/k5 /var/tmp/gordion && sudo chmod 777 /var/tmp/k5; grep -c resctrl /proc/mounts" > $R/node3-resctrl-before.txt
  N3 "cat /proc/sys/kernel/bpf_stats_enabled" > $R/node3-bpfstats-before.txt
  log "node-3 pre-state: $(node3_pre)"
  scp -q -o BatchMode=yes $R/node3-hwp-pre.txt $NODE:/tmp/node3-hwp-pre.txt
  log "node-3 after J0: $(node3_defaults)"
  scp -q -o BatchMode=yes $KB/k5agent $KB/k5probe.bpf.o $KD/k5ctl.py $KD/k5score.py $KD/k5slice.py $HOME/k1/ugen/ugen $NODE:/var/tmp/k5/
  for f in /local/results/i4/dict/dict-rec.json /local/results/figs-work/s8/dict-uid-ethr.json; do [ -f $f ] && scp -q -o BatchMode=yes $f $NODE:/var/tmp/k5/$( [ ${f##*/} = dict-uid-ethr.json ] && echo dict-uid.json || echo ${f##*/}); done
  R0=$(ls -d /local/results/i12 /local/results/j1 2>/dev/null | head -1)
  [ -n "$R0" ] && { head -20000 $(ls $R0/recorders/*unique-id-service.jsonl | head -1) > /tmp/replay-rec.jsonl; head -20000 $(ls $R0/*/ctl/*-r00-agent.jsonl | head -1) > /tmp/replay-agent.jsonl; scp -q -o BatchMode=yes /tmp/replay-rec.jsonl /tmp/replay-agent.jsonl $NODE:/var/tmp/k5/; }
  N3 "[ -d /sys/fs/resctrl/info ] || sudo mount -t resctrl resctrl /sys/fs/resctrl; sudo mkdir -p /sys/fs/cgroup/gordion-aggr; sudo sysctl -q -w kernel.bpf_stats_enabled=1" > $R/node3-prep.txt 2>&1
  nodewatch_start
  # the DSB chain on node-3, unpinned (OTHER_NODES=node-3 puts every other SN service there too); unique-id on image $UIDTAG with cpu.max 1
  local run; run="$(date +%Y%m%d-%H%M%S)-j0b-uid"; echo $run > $R/run-uid.txt
  NS=sn SUT=unique-id-service INSTRUMENTED=unique-id-service NODE=$NODE PIN=0 TAG=g8 OTHER_NODES=$NODE DB_NODE=node-1 WINDOW_MS=100 RAW=0 bash $SN deploy $run > $R/deploy-uid.log 2>&1
  local ctr; ctr=$(kubectl -n sn get deploy unique-id-service -o jsonpath='{.spec.template.spec.containers[0].name}')
  kubectl -n sn set image deploy/unique-id-service $ctr=10.10.1.1:5000/sn-gordion:$UIDTAG >/dev/null
  kubectl -n sn set env deploy/unique-id-service GORDION_INTERCEPT=off GORDION_READS=none GORDION_CTL=/data/k5-ctl-uid >/dev/null
  set_limit sn unique-id-service 1
  run="$(date +%Y%m%d-%H%M%S)-j0b-rec"; echo $run > $R/run-rec.txt
  (cd $HRD && NS=gordion SUT=recommendation NODE=$NODE PIN=0 CPUSET=5,7 TAG=$RECTAG GATE=0 OTHER_NODES=node-1,node-2 WINDOW_MS=100 RAW=0 RESULTS=$R bash hr.sh deploy $run) > $R/deploy-rec.log 2>&1
  kubectl -n gordion set env deploy/recommendation GORDION_INTERCEPT=off GORDION_READS=none GORDION_CTL=/data/k5-ctl-rec >/dev/null
  set_limit gordion recommendation 4
  sleep 5; ctl_off
  kubectl get pods -A -o wide --no-headers | awk -v n=$NODE '$8 == n {print $1, $2, $4}' > $R/pods-node3-j0b.txt
  svc_vars uid; local cgu; cgu=$(pod_cg $V_UID); svc_vars rec; local cgr; cgr=$(pod_cg $V_UID)
  N3 "echo uid \$(cat $cgu/cpu.max); echo rec \$(cat $cgr/cpu.max); for p in \$(pgrep -f '[U]niqueIdService|[r]ecommendation'); do echo \$p \$(cat /proc/\$p/comm) \$(grep Cpus_allowed_list /proc/\$p/status | cut -f2); done" > $R/pins-j0b.txt 2>&1
  log "prep done: $(grep -c . $R/pods-node3-j0b.txt) pods on $NODE; limits: $(tr '\n' ';' < $R/pins-j0b.txt | cut -c1-200); idle node util $(node_util 10)"
  archive
}

# ---------------------------------------------------------------- the busy node: DSB compose load + bigsvc tenants to 60-70 %
dsb_start() {   # steady compose-post load on the SN chain from node-1 (hrgen -app sn), rate $1 req/s, open-ended
  local ip; ip=$(kubectl -n sn get svc nginx-thrift -o jsonpath='{.spec.clusterIP}')
  ssh -n -o BatchMode=yes node-1 "mkdir -p /tmp/wlres/j0b-dsb && nohup taskset -c $BGCPUS /tmp/hrgen -app sn -url http://$ip:8080 -mix compose -warmup 10 -schedule $1:99999 -out /tmp/wlres/j0b-dsb/wlgen.jsonl > /tmp/wlres/j0b-dsb/hrgen.log 2>&1 < /dev/null &"
  sleep 3; echo "dsb compose $1 req/s from node-1 ($BGCPUS) -> $ip:8080"
}
dsb_stop() { ssh -n -o BatchMode=yes node-1 "pkill -INT -x hrgen; sleep 2; true"; scp -q -o BatchMode=yes node-1:/tmp/wlres/j0b-dsb/wlgen.jsonl $R/dsb-wlgen.jsonl 2>/dev/null; }
tenant_deploy() {   # N: bigsvc tenant deployments bigsvc-tN on node-3, unpinned, cpu.max 4, memwalk 24 MB, uniform mix
  local n=$1 run; run="$(date +%Y%m%d-%H%M%S)-j0b-t$n"
  (cd $BS && NAME=bigsvc-t$n NODE=$NODE CPUSET="" TAG=$SYNTAG HOSTDIR=/var/tmp/gordion GATE=0 WINDOW_MS=100 RAW=0 WLGEN_NODE=node-1 WLGEN_CPUS=$BGCPUS STRESSOR=none MEMWALK_MB=24 MEMWALK_STEPS=$(cat $R/memwalk-steps-24.txt 2>/dev/null || echo 0) RESULTS=$R bash scripts/exp.sh deploy $run) > $R/deploy-t$n.log 2>&1
  kubectl -n default set resources deploy/bigsvc-t$n --limits=cpu=4 --requests=cpu=4 > /dev/null 2>&1
  kubectl -n default rollout status deploy/bigsvc-t$n --timeout=240s > /dev/null 2>&1
  echo $run >> $R/tenant-runs.txt
}
tenant_load() {   # N RATE: wlgen from node-1 against tenant N at RATE, open-ended
  local ip; ip=$(kubectl -n default get svc bigsvc-t$1 -o jsonpath='{.spec.clusterIP}')
  ssh -n -o BatchMode=yes node-1 "mkdir -p /tmp/wlres/j0b-t$1 && nohup taskset -c $BGCPUS /tmp/wlgen-$SYNTAG --big_service.grpc.dial_addr=$ip:12345 -mix uniform -warmup 5 -schedule $2:99999 -out /tmp/wlres/j0b-t$1/wlgen.jsonl > /tmp/wlres/j0b-t$1/wlgen.log 2>&1 < /dev/null &"
}
tenants_stop() { ssh -n -o BatchMode=yes node-1 "pkill -INT -f '[w]lgen-$SYNTAG'; sleep 2; true"; for d in $(kubectl -n default get deploy -o name | grep bigsvc-t); do kubectl -n default delete $d --ignore-not-found > /dev/null 2>&1; kubectl -n default delete svc ${d##*/} --ignore-not-found > /dev/null 2>&1; done; }

phase_busy() {   # the recipe: DSB compose at DSB_RATE, then tenants one at a time at TENANT_RATE each until node util in [UTIL_LO, UTIL_HI]
  log "busy: DSB compose at $DSB_RATE req/s, then bigsvc tenants (cpu.max 4, memwalk 24 MB, uniform, $TENANT_RATE req/s each) until node util $UTIL_LO-$UTIL_HI"
  scp -q -o BatchMode=yes $HRD/../../hrgen node-1:/tmp/hrgen 2>/dev/null || scp -q -o BatchMode=yes /local/hr/hotelReservation/hrgen node-1:/tmp/hrgen
  scp -q -o BatchMode=yes $BS/wiring/wlgen-$SYNTAG node-1:/tmp/wlgen-$SYNTAG 2>/dev/null
  # bigsvc's memwalk step count for 24 MB from a quiet calibration pod, as queue-i2 did (reused if present)
  [ -s $R/memwalk-steps-24.txt ] || { cp /local/results/i2/memwalk-steps.txt $R/ 2>/dev/null; awk '$1 == 24 {print $2}' $R/memwalk-steps.txt 2>/dev/null | tail -1 > $R/memwalk-steps-24.txt; }
  local u0; u0=$(node_util 10); echo "{\"node\":\"$NODE\",\"idle_util\":$u0,\"dsb\":{\"rate\":$DSB_RATE,\"mix\":\"compose\",\"from\":\"node-1\"},\"tenants\":[]}" > $R/recipe.json
  log "busy: $(dsb_start $DSB_RATE)"; sleep 20
  local u; u=$(node_util 15); log "busy: with the DSB chain: node util $u"; echo "dsb $DSB_RATE $u" >> $R/busy-steps.txt
  local n=0
  while python3 -c "import sys; sys.exit(0 if float('$u') < $UTIL_LO and $n < $TENANT_MAX else 1)"; do
    stopped && break
    n=$((n + 1)); tenant_deploy $n; tenant_load $n $TENANT_RATE; sleep 20
    u=$(node_util 15); echo "tenant $n $TENANT_RATE $u" >> $R/busy-steps.txt; log "busy: $n tenants at $TENANT_RATE req/s: node util $u"
  done
  python3 -c "import sys; sys.exit(0 if float('$u') > $UTIL_HI else 1)" && log "busy: util $u is above $UTIL_HI with $n tenants (the step is coarse; recorded as is)"
  python3 - $R/recipe.json $n $TENANT_RATE $u <<'EOF'
import json, sys
d = json.load(open(sys.argv[1])); n = int(sys.argv[2]); d["tenants"] = [dict(name=f"bigsvc-t{i}", rate=int(sys.argv[3]), cpu_max="400000 100000", memwalk_mb=24, mix="uniform") for i in range(1, n + 1)]
d["node_util"] = float(sys.argv[4]); d["pods_on_node"] = open(sys.argv[1].replace("recipe.json", "pods-node3-j0b.txt")).read().count("\n")
json.dump(d, open(sys.argv[1], "w"), indent=1)
EOF
  kubectl get pods -A -o wide --no-headers | awk -v n=$NODE '$8 == n {print $1, $2, $4}' > $R/pods-node3-busy.txt
  log "busy: recipe $(tr -d '\n ' < $R/recipe.json | cut -c1-300); $(grep -c . $R/pods-node3-busy.txt) pods on $NODE"
  archive
}

# ---------------------------------------------------------------- the knee and the hold rate (rho 0.75) per victim
knee() {   # SVC "RATE RATE ..." -> $R/knee-SVC.tsv (rate rho served p50 p99 util), $R/knee-SVC.txt (knee rate), $R/hold-SVC.txt (rate at rho 0.75)
  local svc=$1 rates=$2 r cg p0=""
  svc_vars $svc; cg=$(pod_cg $V_UID)
  echo "# $svc pod $V_UID k $V_K; rate rho served/s p50_ms p99_ms node_util (15 s, 10-25 s after the generator start)" > $R/knee-$svc.tsv
  for r in $rates; do
    stopped && return 1
    load_start $svc knee-$svc-$r "$r:30"
    sleep 10; local a; a=$(N3 "echo \$(date +%s%N) \$(awk '/usage_usec/{print \$2}' $cg/cpu.stat)"); local u; u=$(node_util 15); local b; b=$(N3 "echo \$(date +%s%N) \$(awk '/usage_usec/{print \$2}' $cg/cpu.stat)")
    load_stop
    local sv; case $svc in uid) sv=$(served_of /var/tmp/k5/knee-$svc-$r-ugen.jsonl); N3 "rm -f /var/tmp/k5/knee-$svc-$r-ugen.jsonl" ;; rec) sv=$(served_rec $R/knee-$svc-$r/wlgen.jsonl 2>/dev/null || echo "0 0 0") ;; esac
    local rho; rho=$(echo $a $b | awk -v k=$V_K '{printf "%.4f", ($4-$2)/1e3/(($3-$1)/1e6)/k}')
    echo "$r $rho $sv $u" >> $R/knee-$svc.tsv; log "knee $svc $r: rho $rho; served/s p50 p99: $sv; node util $u"
    read served p50 p99 <<< "$sv"; [ -z "$p0" ] && p0=$p50
    python3 -c "import sys; sys.exit(0 if (float('$served') < 0.98 * $r) or (float('$p0') > 0 and float('$p50') > 2 * float('$p0')) else 1)" && { log "knee $svc: at $r req/s (served $served, p50 $p50 vs $p0)"; break; }
    sleep 2
  done
  python3 - $R/knee-$svc.tsv $RHO_HOLD > $R/hold-$svc.txt <<'EOF'
import sys
pts = [l.split() for l in open(sys.argv[1]) if not l.startswith('#')]; h = float(sys.argv[2])
pr = [(float(p[0]), float(p[1])) for p in pts]; knee = pts[-1][0]
for (r0, p0), (r1, p1) in zip(pr, pr[1:]):
    if p0 <= h <= p1 and p1 > p0: print(round(r0 + (h - p0) * (r1 - r0) / (p1 - p0)), "interpolated", "knee", knee); break
else:
    (r0, p0), (r1, p1) = pr[-2], pr[-1]; print(round(r1 + (h - p1) * (r1 - r0) / (p1 - p0)), "extrapolated", "knee", knee)
EOF
  tail -1 $R/knee-$svc.tsv | cut -d' ' -f1 > $R/knee-$svc.txt
  log "$svc: hold (rho $RHO_HOLD) $(cat $R/hold-$svc.txt); knee step $(cat $R/knee-$svc.txt)"
}
phase_knee() {
  log "knee: load steps on the busy node, rho from cgroup usage / (k x wall)"
  ctl_off
  knee uid "${UID_STEPS:-1000 2000 3000 4000 5000 6000 7000 8000 10000}"
  knee rec "${REC_STEPS:-1000 2000 3000 4000 5000 6000 7000 8000 10000}"
  archive
}

LADDER=${LADDER:-j-probe,j-count,j-slot,j-full,j-perf,j-rdt,j-all}
ladder_arms() { local a="none"; for x in $(echo $LADDER | tr ',' ' '); do a="$a,$x,none"; done; echo $a; }
phase_ladder() {   # rows 1, 5-7, 9-10, 14 at the hold rate, a twin before and after each, 3 x 60 s (unique-id ~51 min, recommendation ~51 min)
  log "ladder: $(ladder_arms | tr ',' '\n' | grep -c .) arms per rep ($LADDER + twins), $REPS x $MEAS s at rho $RHO_HOLD"
  svc_vars uid; cell uid-ladder-busy uid $V_MID:9999 none $(ladder_arms) $REPS $MEAS
  svc_vars rec; cell rec-ladder-busy rec $V_MID:9999 none $(ladder_arms) $REPS $MEAS
}

phase_restore() {
  log "restore"
  load_stop; dsb_stop 2>/dev/null; tenants_stop 2>/dev/null; nodewatch_stop 2>/dev/null
  N3 "sudo pkill -INT k5agent; sudo pkill -INT -f '[k]5score.py'; sudo pkill -TERM -f '[k]5ctl.py'; sleep 2; sudo rmdir /sys/fs/cgroup/gordion-aggr 2>/dev/null; sudo rmdir /sys/fs/resctrl/mon_groups/k5-* 2>/dev/null; sudo sysctl -q -w kernel.bpf_stats_enabled=$(cat $R/node3-bpfstats-before.txt 2>/dev/null || echo 0); true"
  svc_vars uid 2>/dev/null; scp -q -o BatchMode=yes $NODE:$V_REC $R/recorders/ 2>/dev/null
  svc_vars rec 2>/dev/null; scp -q -o BatchMode=yes $NODE:$V_REC $R/recorders/ 2>/dev/null
  N3 "mkdir -p /var/tmp/i2keep; sudo mv /var/tmp/k5/dict-*.json /var/tmp/i2keep/ 2>/dev/null; sudo rm -f /var/tmp/gordion/*-j0b-*.jsonl /var/tmp/gordion/k5-ctl-*; sudo rm -rf /var/tmp/k5 /tmp/wlres/*-j0b-* /tmp/wlres/knee-*; true"
  ssh -n -o BatchMode=yes node-1 "rm -rf /tmp/wlres/j0b-*; true"
  [ "$(cat $R/node3-resctrl-before.txt 2>/dev/null)" = 0 ] && N3 "sudo umount /sys/fs/resctrl 2>/dev/null; true"
  python3 - $R/pre-j0b-gordion.json $R/pre-j0b-sn.json <<'EOF' > $R/restore-specs.yaml
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
  kubectl replace -f $R/restore-specs.yaml > $R/restore-apply.log 2>&1
  grep -q GORDION_CTL $R/pre-j0b-gordion.json || kubectl -n gordion set env deploy/recommendation GORDION_INTERCEPT- GORDION_READS- GORDION_CTL- >> $R/restore-apply.log 2>&1
  grep -q GORDION_CTL $R/pre-j0b-sn.json || kubectl -n sn set env deploy/unique-id-service GORDION_INTERCEPT- GORDION_READS- GORDION_CTL- >> $R/restore-apply.log 2>&1
  for ns in gordion sn default; do for d in $(kubectl -n $ns get deploy -o name); do kubectl -n $ns rollout status $d --timeout=300s >/dev/null 2>&1; done; done
  kubectl get pods -A -o wide --no-headers > $R/pods-after-j0b.txt
  for ns in gordion sn; do kubectl -n $ns get deploy -o json > $R/post-j0b-$ns.json; done
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
    a, b = specs(f"{R}/pre-j0b-{ns}.json"), specs(f"{R}/post-j0b-{ns}.json")
    for k in sorted(set(a) | set(b)):
        nd += 1
        if a.get(k) != b.get(k): diffs.append(f"{ns}/{k}")
print(f"deployments compared: {nd}; differing from before prep: {diffs if diffs else 'none'}")
EOF
  echo "bigsvc tenants left: $(kubectl -n default get deploy -o name | grep -c bigsvc-t)"
  node3_repin
  N3 "sudo modprobe msr; sudo python3 /var/tmp/i2/msr.py read 0x774 0-143 | awk '{print \$2}' | sort | uniq -c | tr -s ' ' | tr '\n' ';'; [ \"$(sed -n 3p $R/node3-state-pre.txt)\" = 1 ] || sudo rmmod msr; echo; for s in 2 3; do cat /sys/devices/system/cpu/cpu*/cpuidle/state\$s/disable | sort | uniq -c | tr '\n' ' '; done; echo; cat /sys/devices/system/cpu/intel_uncore_frequency/*/m??_freq_khz | tr '\n' ' '; echo; lsmod | grep -c '^msr '; grep -c resctrl /proc/mounts; cat /proc/sys/kernel/bpf_stats_enabled" > $R/node3-state-post.txt 2>&1
  { cat $R/node3-hwp-pre-summary.txt; echo; sed -n 1,5p $R/node3-state-pre.txt; } > $R/node3-cmp-pre.txt
  { sed -n 1p $R/node3-state-post.txt; sed -n 2,6p $R/node3-state-post.txt; } > $R/node3-cmp-post.txt
  if cmp -s $R/node3-cmp-pre.txt $R/node3-cmp-post.txt; then echo "node-3 state (HWP, idle states, uncore, msr, resctrl, bpf_stats) equal to pre-state"; else echo "node-3 STATE DIFFERS from pre-state:"; diff $R/node3-cmp-pre.txt $R/node3-cmp-post.txt; fi
  echo "node-3 after: $(N3 "pgrep -c 'k5agent|k5score'; ls /var/tmp/k5 2>&1 | head -1" 2>&1 | tr '\n' ' ')"
  log "restore done"
  archive
}

RESTORED=${NO_RESTORE:-0}
on_exit() { [ "$RESTORED" = 1 ] && return 0; RESTORED=1; phase_restore; }
trap on_exit EXIT
PHASE=${1:-all}
case $PHASE in
  all) for p in ${PHASES:-prep busy knee ladder}; do stopped && { log "STOP file: $p skipped"; continue; }; phase_$p; done; on_exit; log DONE-J0B ;;
  *) phase_$PHASE; log "DONE-$PHASE" ;;
esac
