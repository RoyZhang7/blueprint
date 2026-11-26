#!/bin/bash
# Phase I queue i5 on clnode270 node-0 (RESULTS-I 3 and 4; stage-3 RUNBOOK 18), run detached, after the decision of
# Nov 18 on the per-window clock read:
#   strace   - one short cell with the corrected set (Bp-fix2) on unique-id; during it each process on node-3 that runs
#              per window (the two recorders, a bigsvc pod on k123i3, the agent with its sampler, the scorer) is traced
#              for three windows: the files it reads and the bytes
#   sweepsyn - the eight bigsvc dictionaries again on big_ctr:k123i3 (the runtime recorder's clock read off by default),
#              arm sweepF (the agent reads APERF/MPERF of CPUs 1,3,5,7); mids and memwalk step counts from step 2
# Functions, prep and restore are queue-i2.sh's.
# Phases (one per call; "all" chains them): prep strace sweepsyn restore. STOP file: $R/STOP.
set -u
export PATH=/usr/local/go1.25/bin:$HOME/go/bin:$PATH
R=${R:-/local/results/i5}; I2=/local/results/i2; KB=/local/k5build; KD=$HOME/k5; NODE=node-3; RCPU=53; WL=16,18,20,22,24,26
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
    rec) V_NS=gordion V_LBL=io.kompose.service=recommendation V_CPUS=5,7 V_SIB=77,79 V_K=2 V_SLOTS=1 V_GO=--go V_CTL=/var/tmp/gordion/k5-ctl-rec V_REC=/var/tmp/gordion/$(cat $R/run-rec.txt 2>/dev/null)-recommendation.jsonl V_DICT=/var/tmp/k5/dict-rec.json V_MID=3600 ;;
    syn) V_NS=default V_LBL=app=bigsvc V_CPUS=1,3,5,7 V_SIB=73,75,77,79 V_K=4 V_SLOTS=3 V_GO=--go V_CTL="" V_REC=/var/tmp/gordion/$(cat $R/run-syn.txt 2>/dev/null).jsonl V_DICT="" V_MID=400 ;;
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
    rec) (cd $HRD && NS=gordion SUT=recommendation WLGEN_NODE=$NODE WLGEN_CPUS=$WL RESULTS=$R bash hr.sh wlgen $cell -- -mix single:recommend -fixed-params -k 2 -warmup 10 -schedule "$sched") > $R/$cell/load.log 2>&1 & LP=$! ;;
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

hwp_check() {   # node-3's HWP_REQUEST must still be min = max = 24 on every CPU; a drift is logged and the pin re-applied
  local v; v=$(N3 "sudo python3 /var/tmp/i2/msr.py read 0x774 0-143 | awk '{print \$2}' | sort | uniq -c | tr -s ' ' | tr '\n' ';'")
  echo "$(date -u +%FT%T) $v" >> $R/hwp-check.txt
  case "$v" in " 144 $HWP;") ;; *) log "HWP_REQUEST DRIFT: $v -> re-applied"; N3 "sudo python3 /var/tmp/i2/msr.py write 0x774 $HWP 0-143 > /dev/null"; echo "$(date -u +%FT%T) re-applied" >> $R/hwp-check.txt ;; esac
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
    net) nbargs="--nb net --holder netsend=$(cut -d' ' -f1 $R/net-uids.txt) --holder netrecv=$(cut -d' ' -f2 $R/net-uids.txt)" ;;
    *) nbargs="--nb none" ;;
  esac
  python3 $KD/k5spec.py --cell $cell --out $R/specs/$cell.json --svc $svc --pod-uid $V_UID --cpus $V_CPUS --sib $V_SIB --k $V_K --slots $V_SLOTS $V_GO \
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

ctl_off() { N3 "echo 'intercept=off reads=none' | sudo tee /var/tmp/gordion/k5-ctl-uid /var/tmp/gordion/k5-ctl-rec > /dev/null"; }

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
  log "I5 prep"
  for ns in gordion sn; do kubectl -n $ns get deploy -o json > $R/pre-i2-$ns.json; done
  kubectl -n default get deploy bigsvc -o json > $R/pre-i2-bigsvc.json 2>/dev/null || rm -f $R/pre-i2-bigsvc.json
  kubectl get pods -A -o wide --no-headers > $R/pods-before-i2.txt
  N3 "sudo mkdir -p /var/tmp/k5 /var/tmp/gordion && sudo chmod 777 /var/tmp/k5; grep -c resctrl /proc/mounts" > $R/node3-resctrl-before.txt
  N3 "cat /proc/sys/kernel/bpf_stats_enabled" > $R/node3-bpfstats-before.txt
  scp -q -o BatchMode=yes $KB/k5agent $KB/k5probe.bpf.o $KD/k5ctl.py $KD/k5score.py $KD/k5slice.py $HOME/k1/ugen/ugen $I2/dict-uid.json $I2/dict-rec.json $NODE:/var/tmp/k5/
  N3 "[ -d /sys/fs/resctrl/info ] || sudo mount -t resctrl resctrl /sys/fs/resctrl; ls /sys/fs/resctrl/info | tr '\n' ' '; sudo mkdir -p /sys/fs/cgroup/gordion-aggr; sudo sysctl -q -w kernel.bpf_stats_enabled=1; cat /proc/sys/kernel/bpf_stats_enabled" > $R/node3-prep.txt 2>&1
  # read-only: where the NIC's interrupts land, and the idle states and clock left on node-3
  N3 "for i in \$(awk -F: '/mlx5/{print \$1}' /proc/interrupts); do echo \"\$i \$(cat /proc/irq/\$i/smp_affinity_list) \$(cat /proc/irq/\$i/effective_affinity_list 2>/dev/null)\"; done" > $R/node3-nic-irqs.txt 2>&1
  N3 "cat /sys/devices/system/cpu/cpu1/cpuidle/state*/disable | tr '\n' ' '; sudo python3 /var/tmp/i2/msr.py read 0x774 1" > $R/node3-idle-hwp.txt 2>&1
  hwp_check
  syn_off
  local run; run="$(date +%Y%m%d-%H%M%S)-i2-uid"; echo $run > $R/run-uid.txt
  NS=sn SUT=unique-id-service INSTRUMENTED=unique-id-service NODE=$NODE CPUSET=1 TAG=g8 HOSTPID=1 OTHER_NODES=node-1,node-2 DB_NODE=node-1 WINDOW_MS=100 RAW=0 bash $SN deploy $run > $R/deploy-uid.log 2>&1
  local ctr; ctr=$(kubectl -n sn get deploy unique-id-service -o jsonpath='{.spec.template.spec.containers[0].name}')
  kubectl -n sn set image deploy/unique-id-service $ctr=10.10.1.1:5000/sn-gordion:g8 >/dev/null
  kubectl -n sn set env deploy/unique-id-service GORDION_INTERCEPT=off GORDION_READS=none GORDION_CTL=/data/k5-ctl-uid >/dev/null
  kubectl -n sn rollout status deploy/unique-id-service --timeout=240s >/dev/null
  run="$(date +%Y%m%d-%H%M%S)-i2-rec"; echo $run > $R/run-rec.txt
  (cd $HRD && NS=gordion SUT=recommendation NODE=$NODE CPUSET=5,7 TAG=gordion9 GATE=0 OTHER_NODES=node-1,node-2 WINDOW_MS=100 RAW=0 RESULTS=$R bash hr.sh deploy $run) > $R/deploy-rec.log 2>&1
  kubectl -n gordion set env deploy/recommendation GORDION_INTERCEPT=off GORDION_READS=none GORDION_CTL=/data/k5-ctl-rec >/dev/null
  kubectl -n gordion rollout status deploy/recommendation --timeout=240s >/dev/null
  sleep 5; ctl_off
  kubectl get pods -A -o wide --no-headers | grep -E "unique-id-service|recommendation|bigsvc" | awk '{print $1, $2, $4, $8}' > $R/pods-i2.txt
  N3 "for p in \$(pgrep -f '[U]niqueIdService|[r]ecommendation'); do echo \$p \$(cat /proc/\$p/comm) \$(grep Cpus_allowed_list /proc/\$p/status | cut -f2); done" > $R/pins-i2.txt 2>&1
  log "prep done: $(tr '\n' ';' < $R/pods-i2.txt) pins: $(tr '\n' ';' < $R/pins-i2.txt) images: $(kubectl -n sn get deploy unique-id-service -o jsonpath='{.spec.template.spec.containers[0].image}') $(kubectl -n gordion get deploy recommendation -o jsonpath='{.spec.template.spec.containers[0].image}')"
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

phase_strace() {   # decision 1 check: what each per-window process on node-3 reads (one cell, not a measurement)
  log "strace: unique-id cell with Bp-fix2 (10 s); recorders, bigsvc pod, agent, scorer traced for 0.3 s each"
  syn_deploy strace 256 $(steps_of 256)   # an idle bigsvc pod on k123i3 (its runtime recorder writes heartbeat windows)
  svc_vars uid
  ( cell st-uid-none-mid uid $V_MID:9999 none Bp-fix2 1 10 ) &
  local cp=$!; sleep 33   # the cell starts its controller 25 s in; the arm measures from ~30 s for 10 s
  N3 'set -u
    tr() { local name=$1 pid=$2; [ -n "$pid" ] || { echo "== $name: no process"; return; }
      sudo timeout 0.3 strace -f -y -qq -e trace=openat,read,pread64 -p $pid -o /tmp/st-$name.txt 2>/dev/null
      echo "== $name (pid $pid, cpus $(grep Cpus_allowed_list /proc/$pid/status | cut -f2)): $(wc -l < /tmp/st-$name.txt) calls"
      grep -oE "(openat|read|pread64)\([0-9]+<[^>]*>|openat\(AT_FDCWD, \"[^\"]*\"" /tmp/st-$name.txt | sed -E "s/\([0-9]+</ /; s/>$//; s/\(AT_FDCWD, \"/ /; s/\"$//" | sort | uniq -c | sort -rn | head -12
      echo "   bytes by path: $(grep -oE "^[0-9]+ +(read|pread64)\([0-9]+<[^>]*>.* = [0-9]+" /tmp/st-$name.txt | sed -E "s/.*<([^>]*)>.* = ([0-9]+)$/\1 \2/" | awk "{b[\$1]+=\$2} END{for(k in b) printf \"%s=%d \", k, b[k]}")"
      sudo rm -f /tmp/st-$name.txt; }
    tr uid-recorder "$(pgrep -f "[U]niqueIdService" | head -1)"
    tr rec-recorder "$(pgrep -x recommendation | head -1)"
    tr bigsvc-recorder "$(pgrep -f "[b]ig_ctr|[B]igSvc|bigsvc_proc" | head -1)"
    tr agent "$(pgrep -x k5agent | head -1)"
    tr scorer "$(pgrep -f "[k]5score.py" | head -1)"' > $R/strace.txt 2>&1
  wait $cp
  cat $R/strace.txt
  syn_off
}

phase_sweepsyn() {   # the eight bigsvc dictionaries on k123i3, arm sweepF
  log "sweepsyn: bigsvc at the Phase G mixes on $SYNTAG, arm sweepF"
  local m mix mb s
  for m in uniform short long mid7-070-109 B-065-104 mutex10 mutex40 memwalk30-128; do
    stopped && return 1
    mb=256; mix=file:/local/results/mix-$m.json
    case $m in uniform) mix=uniform ;; memwalk30-128) mb=128; mix=file:/local/results/mix-memwalk30.json ;; esac
    syn_deploy sweep-$m $mb $(steps_of $mb)
    s=$(sched_of syn $mix 45)
    SYN_MIX=$mix KEEP_CTL_DIR=1 cell sweep-syn-$m syn $s none sweepF 1 380
    syn_off
  done
}

phase_restore() {   # back to the state before prep (queue-k5.sh's restore without the toolchain purge), then checked
  log "restore"
  load_stop
  N3 "sudo pkill -INT k5agent; sudo pkill -INT -f '[k]5score.py'; sudo pkill -TERM -f '[k]5ctl.py'; sudo pkill -TERM stress-ng; sleep 2; sudo pkill -9 stress-ng; sudo rmdir /sys/fs/cgroup/gordion-aggr 2>/dev/null; sudo sysctl -q -w kernel.bpf_stats_enabled=$(cat $R/node3-bpfstats-before.txt 2>/dev/null || echo 0); true"
  svc_vars uid 2>/dev/null; scp -q -o BatchMode=yes $NODE:$V_REC $R/recorders/ 2>/dev/null
  svc_vars rec 2>/dev/null; scp -q -o BatchMode=yes $NODE:$V_REC $R/recorders/ 2>/dev/null
  N3 "mkdir -p /var/tmp/i2keep; sudo mv /var/tmp/k5/dict-*.json /var/tmp/i2keep/ 2>/dev/null; sudo rm -f /var/tmp/gordion/*-i2-*.jsonl /var/tmp/gordion/k5-ctl-*; sudo rm -rf /var/tmp/k5 /tmp/wlres/*-i2-* /tmp/wlres/mid-*; true"
  [ "$(cat $R/node3-resctrl-before.txt 2>/dev/null)" = 0 ] && N3 "sudo umount /sys/fs/resctrl 2>/dev/null; true"
  kubectl delete ns k5 --wait=true >/dev/null 2>&1
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
  hwp_check
  echo "node-3 after: $(N3 "pgrep -c 'k5agent|k5score|stress-ng'; ls /var/tmp/k5 2>&1 | head -1; grep -c resctrl /proc/mounts; cat /proc/sys/kernel/bpf_stats_enabled; ls /var/tmp/gordion | grep -c i2; cat /sys/devices/system/cpu/cpu*/cpuidle/state3/disable | sort | uniq -c" 2>&1 | tr '\n' ' ')"
  log "restore done"
}

PHASE=${1:-all}
case $PHASE in
  all) for p in ${PHASES:-prep strace sweepsyn}; do stopped && { log "STOP file: $p skipped"; continue; }; phase_$p; done; phase_restore; log DONE-I5 ;;
  *) phase_$PHASE; log "DONE-$PHASE" ;;
esac
