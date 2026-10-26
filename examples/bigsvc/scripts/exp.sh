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
# Options (env or flags): NODE=node-3 CPUSET=2-9 TAG=latest WINDOW_MS=100 MEMWALK_MB=256
set -euo pipefail

BP=${BP:-/local/blueprint}
WIRING=$BP/examples/bigsvc/wiring
BUILD=$WIRING/build-k8s
REGISTRY=${BIGSVC_REGISTRY:-10.10.1.1:5000}
RESULTS=${RESULTS:-/local/results}
NODE=${NODE:-node-3}
CPUSET=${CPUSET:-2-9}
TAG=${TAG:-latest}
WINDOW_MS=${WINDOW_MS:-100}
MEMWALK_MB=${MEMWALK_MB:-256}
RAW=${RAW:-1}
DISABLE=${DISABLE:-0}
HOSTDIR=/mnt/extra/gordion
export PATH=$PATH:/usr/local/go/bin:$HOME/go/bin

log() { echo "[$(date +%H:%M:%S)] $*" >&2; }

cmd_build() {
  local tag=${1:-$TAG}
  log "compiling kubernetes spec"
  (cd $WIRING && rm -rf build-k8s && BIGSVC_REGISTRY=$REGISTRY go run . -o build-k8s -w kubernetes 2>&1 | grep -iE "error|Successfully" )
  log "docker build"
  (cd $BUILD/bigsvc_kubernetes/big_ctr && docker build -q -t $REGISTRY/big_ctr:$tag . && docker push -q $REGISTRY/big_ctr:$tag)
  ls -la $BUILD/wlgen/wlgen_proc/wlgen_proc/wlgen_proc
  curl -s http://$REGISTRY/v2/big_ctr/tags/list; echo
}

wlgen_bin() { echo $BUILD/wlgen/wlgen_proc/wlgen_proc/wlgen_proc; }

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
  log "wlgen -> $ip:12345 : $*"
  GORDION_CLIENT_OUT=$RESULTS/$run/client.jsonl GORDION_RAW=$RAW $(wlgen_bin) --big_service.grpc.dial_addr=$ip:12345 -out $RESULTS/$run/wlgen.jsonl "$@" 2>&1 | grep -vE "ScopeMetrics|^$" | tail -3 >&2
  tail -1 $RESULTS/$run/wlgen.jsonl
}

cmd_collect() {
  local run=$1
  mkdir -p $RESULTS/$run
  sleep 1
  scp -q -o BatchMode=yes $NODE:$HOSTDIR/$run.jsonl $RESULTS/$run/gordion.jsonl
  kubectl logs deployment/bigsvc > $RESULTS/$run/pod.log 2>/dev/null || true
  cat > $RESULTS/$run/meta.json <<EOF
{"run":"$run","node":"$NODE","cpuset":"$CPUSET","tag":"$TAG","window_ms":$WINDOW_MS,"memwalk_mb":$MEMWALK_MB,"raw":"$RAW",
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
