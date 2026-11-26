#!/bin/bash
# Phase J1 queue on clnode270 node-3 (RESULTS-J 1; stage-3 RUNBOOK 18), run detached. J0 + the overhead ladder, unpinned, at
# the production clock: node-3 to its as-found settings (HWP_REQUEST released to the saved 0x5500ff01, C1E and C6 enabled,
# uncore 0.8-2.4 GHz, msr module unloaded), the victims deployed without taskset and with a CFS limit (cpu.max: unique-id
# 1 core, recommendation 4), mid found by a rho ramp on each (rho = cgroup usage / (k x wall)), then per service one cell
# with the 17 ladder arms, a twin (arm "none") before and after each, 3 reps x 60 s, the generator at mid throughout.
# Before the ladder (J2 addendum): perf record of unique-id's unsampled interceptor path on the g8 image (shared counters),
# and the row-5 before/after cell (count only, g8 then g9). Everything under $R is rsynced to the project filesystem after
# every cell and at the end (the cluster expires 2026-11-20 13:32 UTC). Restore from the EXIT trap: deployments as before
# prep, node-3 re-pinned to the pre-state (HWP from the snapshot, C1E/C6 off, uncore 2.4 GHz), closing checks read-only.
# Phases (one per call; "all" chains them): prep mid profile before ladder. STOP file: $R/STOP.
set -u
export PATH=/usr/local/go1.25/bin:$HOME/go/bin:$PATH
R=${R:-/local/results/j1}; I2=/local/results/i2; KB=/local/k5build; KD=$HOME/k5; NODE=node-3; RCPU=53; WL=16,18,20,22,24,26
HRD=/local/hr/hotelReservation/noisy-neighbors/dictionary; SN=/local/sn/sn.clean.sh; BS=/local/blueprint/examples/bigsvc
PROJ=${PROJ:-/proj/tracing-pythia-PG0/royno7-j-archive-clnode270-2026-11-20}
UIDTAG=${UIDTAG:-g9}; UIDTAG_BEFORE=g8; RECTAG=gordion9
MEAS=${MEAS:-60}; REPS=${REPS:-3}
mkdir -p $R $R/specs $R/recorders /local/results/k5 $PROJ/j1
log() { echo "=== $(date -u +%H:%M:%S) $*"; }
N3() { ssh -n -o BatchMode=yes $NODE "$@"; }
stopped() { [ -f $R/STOP ]; }
archive() { rsync -a --exclude 'recorders/*' $R/ $PROJ/j1/ 2>> $R/archive.log && echo "$(date -u +%FT%T) archived" >> $R/archive.log; }

# ---------------------------------------------------------------- service facts (unpinned: CPUs "all", k from cpu.max)
svc_vars() {   # uid | rec -> V_*; V_MID from the mid phase once it has run
  case $1 in
    uid) V_NS=sn V_LBL=service=unique-id-service V_CPUS=all V_SIB="" V_K=1 V_SLOTS=1 V_GO="" V_CTL=/var/tmp/gordion/k5-ctl-uid V_REC=/var/tmp/gordion/$(cat $R/run-uid.txt 2>/dev/null)-unique-id-service.jsonl V_DICT=/var/tmp/k5/dict-uid.json V_MID=22483 ;;
    rec) V_NS=gordion V_LBL=io.kompose.service=recommendation V_CPUS=all V_SIB="" V_K=4 V_SLOTS=3 V_GO=--go V_CTL=/var/tmp/gordion/k5-ctl-rec V_REC=/var/tmp/gordion/$(cat $R/run-rec.txt 2>/dev/null)-recommendation.jsonl V_DICT=/var/tmp/k5/dict-rec.json V_MID=3600 ;;
  esac
  [ -s $R/mid-$1.txt ] && V_MID=$(cut -d' ' -f1 $R/mid-$1.txt)
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

# cell NAME SVC SCHEDULE NB ARMS REPS MEASURE [extra k5spec args...]
cell() {
  local name=$1 svc=$2 sched=$3 nb=$4 arms=$5 reps=$6 meas=$7; shift 7
  stopped && { log "STOP file: skipping $name"; return 1; }
  local cell; cell="$(date +%Y%m%d-%H%M%S)-j1-$name"; mkdir -p $R/$cell
  svc_vars $svc
  local narms; narms=$(echo "$arms" | tr ',' '\n' | wc -l)
  local sdur; sdur=$(echo "$sched" | tr ',' '\n' | awk -F: '{s += $2} END {print s}')
  local need=$(( reps * narms * (meas + 8) + 90 ))
  [ "$(echo "$sched" | tr ',' '\n' | wc -l)" = 1 ] && sched="${sched%%:*}:$need"
  python3 $KD/k5spec.py --cell $cell --out $R/specs/$cell.json --svc $svc --pod-uid $V_UID --cpus $V_CPUS --k $V_K --slots $V_SLOTS $V_GO \
    ${V_CTL:+--ctl $V_CTL} --recorder $V_REC ${V_DICT:+--dict $V_DICT} --ctl-out /var/tmp/k5/$cell --arms $arms --reps $reps --measure $meas --reader-cpu $RCPU --nb none \
    --replay-recorder /var/tmp/k5/replay-rec.jsonl --replay-agent /var/tmp/k5/replay-agent.jsonl "$@" > $R/$cell/spec.txt 2>&1 || { log "$cell: spec FAILED: $(cat $R/$cell/spec.txt)"; return 1; }
  scp -q -o BatchMode=yes $R/specs/$cell.json $NODE:/var/tmp/k5/$cell.json
  load_start $svc $cell "$sched"
  sleep 25
  log "$cell: $svc $sched arms $arms x $reps x $meas s (k $V_K, pod $V_UID)"
  N3 "sudo taskset -c $RCPU python3 /var/tmp/k5/k5ctl.py --spec /var/tmp/k5/$cell.json" > $R/$cell/ctl.log 2>&1
  load_stop
  scp -q -r -o BatchMode=yes $NODE:/var/tmp/k5/$cell $R/$cell/ctl || log "$cell: fetch of the controller directory FAILED"
  case $svc in
    uid) scp -q -o BatchMode=yes $NODE:/var/tmp/k5/$cell-ugen.jsonl $R/$cell/ugen.jsonl 2>/dev/null; N3 "rm -f /var/tmp/k5/$cell-ugen.jsonl" ;;
  esac
  N3 "sudo rm -rf /var/tmp/k5/$cell /var/tmp/k5/$cell.json"
  echo "$cell $name $svc $sched $nb $arms $reps $meas" >> $R/cells.txt
  log "$cell done: $(grep -c '"event":"arm"' $R/$cell/ctl/cell.jsonl 2>/dev/null) arm records; $(grep -c error $R/$cell/ctl/cell.jsonl 2>/dev/null) errors"
  archive
}

# ---------------------------------------------------------------- J0: node-3 to its as-found settings, victims unpinned with cpu.max
node3_pre() {   # read-only snapshots of what J0 changes (and of what the restore must put back)
  N3 "sudo python3 /var/tmp/i2/msr.py read 0x774 0-143" > $R/node3-hwp-pre.txt 2>&1
  awk '{print $2}' $R/node3-hwp-pre.txt | sort | uniq -c | tr -s ' ' | tr '\n' ';' > $R/node3-hwp-pre-summary.txt
  N3 "for s in 2 3; do cat /sys/devices/system/cpu/cpu*/cpuidle/state\$s/disable | sort | uniq -c | tr '\n' ' '; done; echo; cat /sys/devices/system/cpu/intel_uncore_frequency/*/m??_freq_khz | tr '\n' ' '; echo; lsmod | grep -c '^msr '; grep -c resctrl /proc/mounts; cat /proc/sys/kernel/bpf_stats_enabled; grep -E 'CONFIG_IRQ_TIME_ACCOUNTING|CONFIG_TICK_CPU_ACCOUNTING' /boot/config-\$(uname -r)" > $R/node3-state-pre.txt 2>&1
  cat $R/node3-hwp-pre-summary.txt $R/node3-state-pre.txt | tr '\n' '|'
}
node3_defaults() {   # J0: HWP released (the saved original), all idle states enabled, uncore as saved, msr unloaded (all through i2node3.sh's restores)
  N3 "cd /var/tmp/i2 && sudo bash i2node3.sh hwp-restore > /dev/null; sudo bash i2node3.sh cstate-restore | tr '\n' ' '; sudo bash i2node3.sh uncore-restore | tr '\n' ' '; sudo bash i2node3.sh msr-unload; true" > $R/node3-j0.log 2>&1
  N3 "sudo modprobe msr; sudo python3 /var/tmp/i2/msr.py read 0x774 0-143 | awk '{print \$2}' | sort | uniq -c | tr -s ' ' | tr '\n' ';'; sudo rmmod msr; echo; for s in 2 3; do cat /sys/devices/system/cpu/cpu*/cpuidle/state\$s/disable | sort | uniq -c | tr '\n' ' '; done; echo; cat /sys/devices/system/cpu/intel_uncore_frequency/*/m??_freq_khz | tr '\n' ' '; echo; lsmod | grep -c '^msr '" > $R/node3-state-j0.txt 2>&1
  cat $R/node3-state-j0.txt | tr '\n' '|'
}
node3_repin() {   # the restore: back to the pre-state (HWP from the snapshot, C1E and C6 off, uncore 2.4 GHz, msr loaded as found)
  N3 "sudo modprobe msr; sudo python3 /var/tmp/i2/msr.py restore 0x774 /tmp/node3-hwp-pre.txt > /dev/null; cd /var/tmp/i2 && sudo bash i2node3.sh c1e-off; sudo bash i2node3.sh cstate-off; sudo bash i2node3.sh uncore-pin | tr '\n' ' '; [ \"$(sed -n 3p $R/node3-state-pre.txt)\" = 1 ] || sudo rmmod msr; true" >> $R/node3-restore.log 2>&1
}
set_limit() {   # NS DEPLOY CORES: the CFS limit (cpu.max = CORES x 100 ms per 100 ms), no memory limit (QoS Burstable: no static cpuset)
  kubectl -n $1 set resources deploy/$2 --limits=cpu=$3 --requests=cpu=$3 > /dev/null
  kubectl -n $1 rollout status deploy/$2 --timeout=240s > /dev/null
}

phase_prep() {
  log "J1 prep: pre-state, J0 node defaults, victims unpinned with cpu.max"
  for ns in gordion sn; do kubectl -n $ns get deploy -o json > $R/pre-j1-$ns.json; done
  kubectl get pods -A -o wide --no-headers > $R/pods-before-j1.txt
  N3 "sudo mkdir -p /var/tmp/k5 /var/tmp/gordion && sudo chmod 777 /var/tmp/k5; grep -c resctrl /proc/mounts" > $R/node3-resctrl-before.txt
  N3 "cat /proc/sys/kernel/bpf_stats_enabled" > $R/node3-bpfstats-before.txt
  log "node-3 pre-state: $(node3_pre)"
  scp -q -o BatchMode=yes $R/node3-hwp-pre.txt $NODE:/tmp/node3-hwp-pre.txt
  log "node-3 after J0: $(node3_defaults)"
  scp -q -o BatchMode=yes $KB/k5agent $KB/k5probe.bpf.o $KD/k5ctl.py $KD/k5score.py $KD/k5slice.py $HOME/k1/ugen/ugen /local/results/i4/dict/dict-rec.json $NODE:/var/tmp/k5/
  scp -q -o BatchMode=yes /local/results/figs-work/s8/dict-uid-ethr.json $NODE:/var/tmp/k5/dict-uid.json
  # the scorer's replay inputs (row 13: the scorer alone): a Phase I unique-id recorder stream and agent file, 20 000 lines each
  head -20000 $(ls /local/results/i12/recorders/*unique-id-service.jsonl | head -1) > /tmp/replay-rec.jsonl
  head -20000 $(ls /local/results/i12/*-i2-c2m-uid-twin-a/ctl/Bp-sirq-r00-agent.jsonl | head -1) > /tmp/replay-agent.jsonl
  scp -q -o BatchMode=yes /tmp/replay-rec.jsonl /tmp/replay-agent.jsonl $NODE:/var/tmp/k5/
  N3 "[ -d /sys/fs/resctrl/info ] || sudo mount -t resctrl resctrl /sys/fs/resctrl; ls /sys/fs/resctrl/info | tr '\n' ' '; sudo mkdir -p /sys/fs/cgroup/gordion-aggr; sudo sysctl -q -w kernel.bpf_stats_enabled=1; cat /proc/sys/kernel/bpf_stats_enabled" > $R/node3-prep.txt 2>&1
  # unique-id: image $UIDTAG (the per-thread recorder), no taskset, cpu.max 1 core
  local run; run="$(date +%Y%m%d-%H%M%S)-j1-uid"; echo $run > $R/run-uid.txt
  NS=sn SUT=unique-id-service INSTRUMENTED=unique-id-service NODE=$NODE PIN=0 TAG=g8 OTHER_NODES=node-1,node-2 DB_NODE=node-1 WINDOW_MS=100 RAW=0 bash $SN deploy $run > $R/deploy-uid.log 2>&1
  local ctr; ctr=$(kubectl -n sn get deploy unique-id-service -o jsonpath='{.spec.template.spec.containers[0].name}')
  kubectl -n sn set image deploy/unique-id-service $ctr=10.10.1.1:5000/sn-gordion:$UIDTAG >/dev/null
  kubectl -n sn set env deploy/unique-id-service GORDION_INTERCEPT=off GORDION_READS=none GORDION_CTL=/data/k5-ctl-uid >/dev/null
  set_limit sn unique-id-service 1
  # recommendation: no taskset, cpu.max 4 cores (Go 1.25 takes GOMAXPROCS from cpu.max)
  run="$(date +%Y%m%d-%H%M%S)-j1-rec"; echo $run > $R/run-rec.txt
  (cd $HRD && NS=gordion SUT=recommendation NODE=$NODE PIN=0 CPUSET=5,7 TAG=$RECTAG GATE=0 OTHER_NODES=node-1,node-2 WINDOW_MS=100 RAW=0 RESULTS=$R bash hr.sh deploy $run) > $R/deploy-rec.log 2>&1
  kubectl -n gordion set env deploy/recommendation GORDION_INTERCEPT=off GORDION_READS=none GORDION_CTL=/data/k5-ctl-rec >/dev/null
  set_limit gordion recommendation 4
  sleep 5; ctl_off
  kubectl get pods -A -o wide --no-headers | grep -E "unique-id-service|recommendation" | awk '{print $1, $2, $4, $8}' > $R/pods-j1.txt
  svc_vars uid; local cgu; cgu=$(pod_cg $V_UID); svc_vars rec; local cgr; cgr=$(pod_cg $V_UID)
  N3 "echo uid \$(cat $cgu/cpu.max) \$(cat $cgu/cpuset.cpus.effective); echo rec \$(cat $cgr/cpu.max) \$(cat $cgr/cpuset.cpus.effective); for p in \$(pgrep -f '[U]niqueIdService|[r]ecommendation'); do echo \$p \$(cat /proc/\$p/comm) \$(grep Cpus_allowed_list /proc/\$p/status | cut -f2) \$(grep -c . /proc/\$p/task/*/comm 2>/dev/null | wc -l); done" > $R/pins-j1.txt 2>&1
  log "prep done: $(tr '\n' ';' < $R/pods-j1.txt) limits/affinity: $(tr '\n' ';' < $R/pins-j1.txt) images: $(kubectl -n sn get deploy unique-id-service -o jsonpath='{.spec.template.spec.containers[0].image}') $(kubectl -n gordion get deploy recommendation -o jsonpath='{.spec.template.spec.containers[0].image}')"
  archive
}

rho_ramp() {   # SVC "RATE RATE ..." -> $R/mid-SVC.tsv (rate rho), $R/mid-SVC.txt ("mid_rate" at rho 0.5); rho = cgroup usage / (k x wall)
  local svc=$1 rates=$2 r cg
  svc_vars $svc
  cg=$(pod_cg $V_UID)
  [ -n "$cg" ] || { log "mid $svc: pod cgroup not found"; return 1; }
  echo "# $svc pod $V_UID cgroup $cg k $V_K (cpu.max); rate rho (usage over 15 s, 10-25 s after the generator start)" > $R/mid-$svc.tsv
  for r in $rates; do
    stopped && return 1
    load_start $svc mid-$svc-$r "$r:27"
    sleep 10; local a; a=$(N3 "echo \$(date +%s%N) \$(awk '/usage_usec/{print \$2}' $cg/cpu.stat)")
    sleep 15; local b; b=$(N3 "echo \$(date +%s%N) \$(awk '/usage_usec/{print \$2}' $cg/cpu.stat)")
    local th; th=$(N3 "awk '/nr_throttled|throttled_usec/{printf \"%s \", \$2}' $cg/cpu.stat")
    load_stop
    local served="- -"; [ $svc = uid ] && served=$(N3 "python3 - /var/tmp/k5/mid-$svc-$r-ugen.jsonl <<'EOF'
import json, sys, statistics
v = []
for l in open(sys.argv[1]):
    try: v.append(json.loads(l))
    except Exception: pass
d = [x['done'] for x in v if isinstance(x, dict) and 'done' in x]; p = [x['lat_p50_us'] for x in v if isinstance(x, dict) and x.get('lat_p50_us')]
print(statistics.median(d) if d else 0, round(statistics.median(p) / 1000, 3) if p else 0)
EOF" 2>/dev/null || echo "- -")
    echo "$r $(echo $a $b | awk -v k=$V_K '{printf "%.4f", ($4-$2)/1e3/(($3-$1)/1e6)/k}') $served $th" >> $R/mid-$svc.tsv
    log "mid $svc $r: rho $(tail -1 $R/mid-$svc.tsv | cut -d' ' -f2); served/s, client p50 ms: $served; nr_throttled, throttled_usec (cumulative): $th"
    sleep 2
  done
  python3 - $R/mid-$svc.tsv > $R/mid-$svc.txt <<'EOF'
import sys
pts = [tuple(map(float, l.split()[:2])) for l in open(sys.argv[1]) if not l.startswith('#')]
pts.sort()
for (r0, p0), (r1, p1) in zip(pts, pts[1:]):
    if p0 <= 0.5 <= p1 and p1 > p0:
        print(round(r0 + (0.5 - p0) * (r1 - r0) / (p1 - p0)), "interpolated"); break
else:
    (r0, p0), (r1, p1) = pts[-2], pts[-1]; print(round(r1 + (0.5 - p1) * (r1 - r0) / (p1 - p0)), "extrapolated with the slope of the last two steps", r0, p0, r1, p1)
EOF
  log "mid $svc: $(cat $R/mid-$svc.txt)"
}
phase_mid() {
  log "mid: rho ramps at the production clock (k from cpu.max), no instrumentation"
  ctl_off
  rho_ramp uid "${UID_RAMP:-1000 2000 4000 6000 8000 12000}"
  rho_ramp rec "${REC_RAMP:-1000 2000 4000 6000 8000 10000}"
  archive
}

phase_profile() {   # J2 addendum: the unsampled interceptor path on the shared-counter build (g8), every request timed, perf by symbol
  log "profile: unique-id on $UIDTAG_BEFORE (shared counters), intercept full, sample 1; perf record cycles + cache-misses, 15 s"
  local ctr; ctr=$(kubectl -n sn get deploy unique-id-service -o jsonpath='{.spec.template.spec.containers[0].name}')
  kubectl -n sn set image deploy/unique-id-service $ctr=10.10.1.1:5000/sn-gordion:$UIDTAG_BEFORE >/dev/null
  kubectl -n sn rollout status deploy/unique-id-service --timeout=240s >/dev/null; sleep 5
  svc_vars uid
  load_start uid profile-g8 "$V_MID:70"; sleep 15
  N3 "echo 'intercept=full reads=cpu sample=1' | sudo tee /var/tmp/gordion/k5-ctl-uid > /dev/null"; sleep 6
  N3 "P=\$(for p in \$(pgrep -x UniqueIdService); do echo \$(ls /proc/\$p/task | wc -l) \$p; done | sort -rn | head -1 | cut -d' ' -f2); echo pid \$P threads \$(ls /proc/\$P/task | wc -l); sudo perf record -q -e cycles -e cache-misses -c 20011 -p \$P -o /tmp/j1-uid.perf.data -- sleep 15 2>&1 | tail -2; sudo perf report -i /tmp/j1-uid.perf.data --stdio --sort sym -F overhead,sample,sym 2>/dev/null | grep -v '^#' | grep -v '^\$' | head -60" > $R/profile-g8-report.txt 2>&1
  N3 "sudo perf report -i /tmp/j1-uid.perf.data --stdio --sort sym -F overhead,sample,sym 2>/dev/null | grep -iE 'gordion|Recorder|EventHandler|histAdd|arrival|slot|fetch_add|atomic' | head -40" > $R/profile-g8-gordion.txt 2>&1
  N3 "sudo perf annotate -i /tmp/j1-uid.perf.data --stdio -l 2>/dev/null | grep -B2 -A12 -iE 'Percent.*(gordion|Recorder|EventHandler)' | head -200" > $R/profile-g8-annotate.txt 2>&1
  N3 "sudo perf report -i /tmp/j1-uid.perf.data --stdio --sort dso 2>/dev/null | grep -v '^#' | grep -v '^\$' | head -12" > $R/profile-g8-dso.txt 2>&1
  ctl_off; load_stop
  N3 "sudo rm -f /tmp/j1-uid.perf.data"
  log "profile: $(head -3 $R/profile-g8-gordion.txt | tr '\n' '|')"
  archive
}

phase_before() {   # row 5 before/after: count only on the shared-counter build (g8), twin before and after, 3 reps; then the image back to $UIDTAG
  svc_vars uid
  cell uid-count-g8 uid $V_MID:9999 none none,j-count,none $REPS $MEAS
  local ctr; ctr=$(kubectl -n sn get deploy unique-id-service -o jsonpath='{.spec.template.spec.containers[0].name}')
  kubectl -n sn set image deploy/unique-id-service $ctr=10.10.1.1:5000/sn-gordion:$UIDTAG >/dev/null
  kubectl -n sn rollout status deploy/unique-id-service --timeout=240s >/dev/null; sleep 5; ctl_off
}

LADDER=j-probe,j-holder,j-wake,j-sirq,j-count,j-slot,j-full,j-s8,j-perf,j-rdt,j-clock,j-reads,j-scorer,j-all,j-all-s8,Bp-J
ladder_arms() { local a="none"; for x in $(echo $LADDER | tr ',' ' '); do a="$a,$x,none"; done; echo $a; }

phase_ladder() {
  log "ladder: $(ladder_arms | tr ',' '\n' | grep -c .) arms per rep (16 rows + twins), $REPS reps x $MEAS s, unique-id then recommendation"
  svc_vars uid; cell uid-ladder uid $V_MID:9999 none $(ladder_arms) $REPS $MEAS
  svc_vars rec; cell rec-ladder rec $V_MID:9999 none $(ladder_arms) $REPS $MEAS
}

phase_restore() {   # back to the state before prep, then checked
  log "restore"
  load_stop
  N3 "sudo pkill -INT k5agent; sudo pkill -INT -f '[k]5score.py'; sudo pkill -TERM -f '[k]5ctl.py'; sleep 2; sudo rmdir /sys/fs/cgroup/gordion-aggr 2>/dev/null; sudo rmdir /sys/fs/resctrl/mon_groups/k5-* 2>/dev/null; sudo sysctl -q -w kernel.bpf_stats_enabled=$(cat $R/node3-bpfstats-before.txt 2>/dev/null || echo 0); true"
  svc_vars uid 2>/dev/null; scp -q -o BatchMode=yes $NODE:$V_REC $R/recorders/ 2>/dev/null
  svc_vars rec 2>/dev/null; scp -q -o BatchMode=yes $NODE:$V_REC $R/recorders/ 2>/dev/null
  N3 "mkdir -p /var/tmp/i2keep; sudo mv /var/tmp/k5/dict-*.json /var/tmp/i2keep/ 2>/dev/null; sudo rm -f /var/tmp/gordion/*-j1-*.jsonl /var/tmp/gordion/k5-ctl-*; sudo rm -rf /var/tmp/k5 /tmp/wlres/*-j1-* /tmp/wlres/mid-*; true"
  [ "$(cat $R/node3-resctrl-before.txt 2>/dev/null)" = 0 ] && N3 "sudo umount /sys/fs/resctrl 2>/dev/null; true"
  python3 - $R/pre-j1-gordion.json $R/pre-j1-sn.json <<'EOF' > $R/restore-specs.yaml
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
  grep -q GORDION_CTL $R/pre-j1-gordion.json || kubectl -n gordion set env deploy/recommendation GORDION_INTERCEPT- GORDION_READS- GORDION_CTL- >> $R/restore-apply.log 2>&1
  grep -q GORDION_CTL $R/pre-j1-sn.json || kubectl -n sn set env deploy/unique-id-service GORDION_INTERCEPT- GORDION_READS- GORDION_CTL- >> $R/restore-apply.log 2>&1
  for ns in gordion sn; do for d in $(kubectl -n $ns get deploy -o name); do kubectl -n $ns rollout status $d --timeout=300s >/dev/null 2>&1; done; done
  kubectl get pods -A -o wide --no-headers > $R/pods-after-j1.txt
  for ns in gordion sn; do kubectl -n $ns get deploy -o json > $R/post-j1-$ns.json; done
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
    a, b = specs(f"{R}/pre-j1-{ns}.json"), specs(f"{R}/post-j1-{ns}.json")
    for k in sorted(set(a) | set(b)):
        nd += 1
        if a.get(k) != b.get(k): diffs.append(f"{ns}/{k}")
print(f"deployments compared: {nd}; differing from before prep: {diffs if diffs else 'none'}")
EOF
  python3 - $R/pods-before-j1.txt $R/pods-after-j1.txt <<'EOF'
import sys
def rd(p): return {(l.split()[0], l.split()[1].rsplit('-', 2)[0]): l.split()[7] for l in open(p) if l.strip() and l.split()[3] == 'Running'}
a, b = rd(sys.argv[1]), rd(sys.argv[2])
diff = [(k, a.get(k), b.get(k)) for k in sorted(set(a) | set(b)) if a.get(k) != b.get(k)]
print(f"running pods before/after: {len(a)}/{len(b)}; node differences (ns, deploy, before -> after): {diff if diff else 'none'}")
EOF
  # node-3 back to the pre-state, then the read-only closing check against the snapshots
  node3_repin
  N3 "sudo modprobe msr; sudo python3 /var/tmp/i2/msr.py read 0x774 0-143 | awk '{print \$2}' | sort | uniq -c | tr -s ' ' | tr '\n' ';'; [ \"$(sed -n 3p $R/node3-state-pre.txt)\" = 1 ] || sudo rmmod msr; echo; for s in 2 3; do cat /sys/devices/system/cpu/cpu*/cpuidle/state\$s/disable | sort | uniq -c | tr '\n' ' '; done; echo; cat /sys/devices/system/cpu/intel_uncore_frequency/*/m??_freq_khz | tr '\n' ' '; echo; lsmod | grep -c '^msr '; grep -c resctrl /proc/mounts; cat /proc/sys/kernel/bpf_stats_enabled" > $R/node3-state-post.txt 2>&1
  { cat $R/node3-hwp-pre-summary.txt; echo; sed -n 1,5p $R/node3-state-pre.txt; } > $R/node3-cmp-pre.txt
  { sed -n 1p $R/node3-state-post.txt; sed -n 2,6p $R/node3-state-post.txt; } > $R/node3-cmp-post.txt
  if cmp -s $R/node3-cmp-pre.txt $R/node3-cmp-post.txt; then echo "node-3 state (HWP, idle states, uncore, msr, resctrl, bpf_stats) equal to pre-state"; else echo "node-3 STATE DIFFERS from pre-state:"; diff $R/node3-cmp-pre.txt $R/node3-cmp-post.txt; fi
  echo "node-3 after: $(N3 "pgrep -c 'k5agent|k5score'; ls /var/tmp/k5 2>&1 | head -1; ls /var/tmp/gordion | grep -c j1" 2>&1 | tr '\n' ' ')"
  log "restore done"
  archive
}

RESTORED=${NO_RESTORE:-0}   # NO_RESTORE=1: a single phase without the trap restore (prep for a hand check; the restore phase must follow)
on_exit() { [ "$RESTORED" = 1 ] && return 0; RESTORED=1; phase_restore; }
trap on_exit EXIT
PHASE=${1:-all}
case $PHASE in
  all) for p in ${PHASES:-prep mid profile before ladder}; do stopped && { log "STOP file: $p skipped"; continue; }; phase_$p; done; on_exit; log DONE-J1 ;;
  *) phase_$PHASE; log "DONE-$PHASE" ;;
esac
