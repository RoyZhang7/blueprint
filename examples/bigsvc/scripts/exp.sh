#!/bin/bash
# Experiment driver for the BigSvc per-endpoint dictionary validation (Phase A).
# Runs on the cluster head node (node-0). Subcommands:
#
#   exp.sh build [TAG]            compile the kubernetes spec, build+push the image, build wlgen
#   exp.sh deploy RUN [opts]      (re)deploy the service pod writing /data/RUN.jsonl on the service node
#   exp.sh wlgen RUN -- <args>    run the workload generator against the service, log to results/RUN/wlgen.jsonl
#   exp.sh collect RUN            copy the recorder output from the service node into results/RUN/
#   exp.sh run NAME [opts] -- <wlgen args>   deploy + wlgen + collect, RUN = <timestamp>-NAME
#   exp.sh down                   delete the service deployment
#
# Options (env or flags): NODE=node-4 CPUSET=2-5 TAG=latest WINDOW_MS=100 MEMWALK_MB=256
#   HOSTDIR=/mnt/extra/gordion   recorder output dir on the service node
#   WLGEN_NODE=node-4 WLGEN_CPUS=6,7   run the load generator on that node (pinned) instead of
#                                       node-0, so client stamps and server timestamps share a clock
#   STRESSOR=none|capacity-cut|aggressor   label recorded in meta.json (F9): "capacity-cut" = stress-ng
#                                       CPU hogs on part of the service's cores, "aggressor" = the
#                                       stage-3 HotelReservation aggressor pods (Phase B default)
# Phase B (F8): the service node must have a working cpufreq driver (the collect step records
# driver/governor/current frequency into meta.json); do not use SCHED_IDLE busy-loop fillers.
set -euo pipefail

BP=${BP:-/local/blueprint}
WIRING=$BP/examples/bigsvc/wiring
BUILD=$WIRING/build-k8s
REGISTRY=${BIGSVC_REGISTRY:-10.10.1.1:5000}
RESULTS=${RESULTS:-/local/results}
NODE=${NODE:-node-4}
CPUSET=${CPUSET:-2-5}
WLGEN_NODE=${WLGEN_NODE:-}
WLGEN_CPUS=${WLGEN_CPUS:-6,7}
TAG=${TAG:-latest}
WINDOW_MS=${WINDOW_MS:-100}
MEMWALK_MB=${MEMWALK_MB:-256}
RAW=${RAW:-1}
DISABLE=${DISABLE:-0}
HOSTDIR=${HOSTDIR:-/mnt/extra/gordion}
STRESSOR=${STRESSOR:-none}
export PATH=$PATH:/usr/local/go/bin:$HOME/go/bin

log() { echo "[$(date +%H:%M:%S)] $*" >&2; }

cmd_build() {
  local tag=${1:-$TAG}
  log "compiling kubernetes spec"
  (cd $WIRING && rm -rf build-k8s && BIGSVC_REGISTRY=$REGISTRY go run . -o build-k8s -w kubernetes 2>&1 | grep -iE "error|Successfully" )
  log "docker build"
  (cd $BUILD/bigsvc_kubernetes/big_ctr && docker build -q -t $REGISTRY/big_ctr:$tag . && docker push -q $REGISTRY/big_ctr:$tag)
  cp $BUILD/wlgen/wlgen_proc/wlgen_proc/wlgen_proc $WIRING/wlgen-$tag
  ls -la $WIRING/wlgen-$tag
  curl -s http://$REGISTRY/v2/big_ctr/tags/list; echo
}

# the load generator that matches the image tag (its dispatch table is generated with the service)
wlgen_bin() { if [ -x $WIRING/wlgen-$TAG ]; then echo $WIRING/wlgen-$TAG; else echo $BUILD/wlgen/wlgen_proc/wlgen_proc/wlgen_proc; fi; }

cmd_deploy() {
  local run=$1; shift
  ssh -o BatchMode=yes $NODE "sudo mkdir -p $HOSTDIR && sudo chmod 777 $HOSTDIR" >/dev/null
  kubectl delete deployment bigsvc --ignore-not-found --wait=true >/dev/null
  cat <<EOF | kubectl apply -f - >/dev/null
apiVersion: apps/v1
kind: Deployment
metadata:
  name: bigsvc
  labels: {app: bigsvc}
spec:
  replicas: 1
  selector: {matchLabels: {app: bigsvc}}
  template:
    metadata:
      labels: {app: bigsvc}
      annotations: {bigsvc/run: "$run"}
    spec:
      nodeSelector: {kubernetes.io/hostname: $NODE}
      containers:
      - name: big-ctr
        image: $REGISTRY/big_ctr:$TAG
        imagePullPolicy: Always
        env:
        - {name: BIG_SERVICE_GRPC_BIND_ADDR, value: "0.0.0.0:12345"}
        - {name: GORDION_OUT, value: "/data/$run.jsonl"}
        - {name: GORDION_WINDOW_MS, value: "$WINDOW_MS"}
        - {name: GORDION_RAW, value: "$RAW"}
        - {name: GORDION_DISABLE, value: "$DISABLE"}
        - {name: BIGSVC_CPUSET, value: "$CPUSET"}
        - {name: BIGSVC_MEMWALK_MB, value: "$MEMWALK_MB"}
        ports: [{containerPort: 12345}]
        volumeMounts: [{name: data, mountPath: /data}]
      volumes:
      - name: data
        hostPath: {path: $HOSTDIR, type: DirectoryOrCreate}
---
apiVersion: v1
kind: Service
metadata: {name: bigsvc}
spec:
  selector: {app: bigsvc}
  ports: [{name: grpc, port: 12345, targetPort: 12345}]
EOF
  kubectl rollout status deployment/bigsvc --timeout=180s >/dev/null
  local ip; ip=$(kubectl get svc bigsvc -o jsonpath='{.spec.clusterIP}')
  log "waiting for $ip:12345"
  for i in $(seq 1 120); do
    if nc -z -w 1 $ip 12345 2>/dev/null; then break; fi
    sleep 1
  done
  kubectl logs deployment/bigsvc | grep -E "calibrat|pinned" | head -3 >&2
  echo $ip
}

cmd_wlgen() {
  local run=$1; shift
  [ "${1:-}" = "--" ] && shift
  local ip; ip=$(kubectl get svc bigsvc -o jsonpath='{.spec.clusterIP}')
  mkdir -p $RESULTS/$run
  if [ -n "$WLGEN_NODE" ]; then
    log "wlgen on $WLGEN_NODE (cpus $WLGEN_CPUS) -> $ip:12345 : $*"
    local q; q=$(printf '%q ' "$@")
    scp -q -o BatchMode=yes $(wlgen_bin) $WLGEN_NODE:/tmp/wlgen-$TAG
    ssh -o BatchMode=yes $WLGEN_NODE "mkdir -p /tmp/wlres/$run && GORDION_CLIENT_OUT=/tmp/wlres/$run/client.jsonl GORDION_RAW=$RAW taskset -c $WLGEN_CPUS /tmp/wlgen-$TAG --big_service.grpc.dial_addr=$ip:12345 -out /tmp/wlres/$run/wlgen.jsonl $q 2>&1 | grep -vE 'ScopeMetrics|^$' | tail -3" >&2
    scp -q -o BatchMode=yes "$WLGEN_NODE:/tmp/wlres/$run/*" $RESULTS/$run/
  else
    log "wlgen -> $ip:12345 : $*"
    GORDION_CLIENT_OUT=$RESULTS/$run/client.jsonl GORDION_RAW=$RAW $(wlgen_bin) --big_service.grpc.dial_addr=$ip:12345 -out $RESULTS/$run/wlgen.jsonl "$@" 2>&1 | grep -vE "ScopeMetrics|^$" | tail -3 >&2
  fi
  tail -1 $RESULTS/$run/wlgen.jsonl
}

cmd_collect() {
  local run=$1
  mkdir -p $RESULTS/$run
  sleep 1
  scp -q -o BatchMode=yes $NODE:$HOSTDIR/$run.jsonl $RESULTS/$run/gordion.jsonl
  kubectl logs deployment/bigsvc > $RESULTS/$run/pod.log 2>/dev/null || true
  # F8: record the service node's cpufreq state (driver, governor, current kHz of the first service core)
  local c0=${CPUSET%%[-,]*}
  local cpufreq; cpufreq=$(ssh -o BatchMode=yes $NODE "cd /sys/devices/system/cpu/cpu$c0/cpufreq 2>/dev/null && echo \$(cat scaling_driver) \$(cat scaling_governor) \$(cat scaling_cur_freq)" 2>/dev/null || echo "none")
  local idle; idle=$(ssh -o BatchMode=yes $NODE "pgrep -c -f 'chrt --idle' 2>/dev/null || true")
  cat > $RESULTS/$run/meta.json <<EOF
{"run":"$run","node":"$NODE","cpuset":"$CPUSET","tag":"$TAG","window_ms":$WINDOW_MS,"memwalk_mb":$MEMWALK_MB,"raw":"$RAW","wlgen_node":"${WLGEN_NODE:-node-0}","wlgen_cpus":"$WLGEN_CPUS",
 "stressor":"$STRESSOR","cpufreq":"$cpufreq","sched_idle_fillers":"${idle:-0}",
 "image":"$(kubectl get deployment bigsvc -o jsonpath='{.spec.template.spec.containers[0].image}')",
 "collected":"$(date -Is)"}
EOF
  log "collected: $(wc -l < $RESULTS/$run/gordion.jsonl) recorder lines, $(wc -l < $RESULTS/$run/wlgen.jsonl) wlgen lines -> $RESULTS/$run"
}

cmd_run() {
  local name=$1; shift
  local run="$(date +%Y%m%d-%H%M%S)-$name"
  cmd_deploy $run >/dev/null
  sleep 2
  cmd_wlgen $run "$@"
  cmd_collect $run
  echo $run
}

cmd_down() { kubectl delete deployment bigsvc svc bigsvc --ignore-not-found; }

sub=${1:-}; shift || true
case "$sub" in
  build)   cmd_build "$@";;
  deploy)  cmd_deploy "$@";;
  wlgen)   cmd_wlgen "$@";;
  collect) cmd_collect "$@";;
  run)     cmd_run "$@";;
  down)    cmd_down;;
  *) sed -n 2,14p "$0"; exit 1;;
esac
