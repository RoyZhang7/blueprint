#!/bin/bash
# Phase K5 item A5: the network-heavy neighbor (node-0). Two pods on the victim's node in namespace k5: k5net-recv (a UDP
# sink) and k5net-send (a UDP stream at PPS packets/s of SIZE bytes to the receiver's pod IP, plus BURN spinning
# workers), both pinned with taskset to CPUs outside the victim's cpuset. The stream's receive processing is steered to
# the victim's CPUs with RPS on the sender pod's host-side veth (the packets enter the host there), so the softirq
# lands on the victim's cores while no neighbor task runs on them (phi stays 0).
#   k5net.sh start NODE SEND_CPU RECV_CPU PPS SIZE BURN VICTIM_CPUS [SEND_NODE]   pods up, RPS set; prints the two pod UIDs
#       SEND_NODE (step 8, RESULTS-I 9): the sender on another node, so the stream enters NODE through its NIC (RX-queue IRQs)
#       instead of a veth; SENDERS=N (env) splits the rate over N sender processes with their own sockets
#   k5net.sh rate PPS                                                   change the rate (the sender pod restarts; RPS re-set)
#   k5net.sh calib NODE VICTIM_CPUS SECS                                softirq per victim CPU over SECS: /proc/softirqs
#                                                                       NET_RX per s and /proc/stat softirq share
#   k5net.sh stop                                                       namespace k5 deleted (RPS masks go with the veths)
set -u
NS=k5; ST=/local/results/k5/net-state.env
log() { echo "[$(date -u +%T)] $*" >&2; }
N() { ssh -n -o BatchMode=yes $1 "${@:2}"; }

pod_yaml() {   # name node cpu image command-args...
  local name=$1 node=$2 cpu=$3; shift 3
  cat <<EOF
apiVersion: v1
kind: Pod
metadata: {name: $name, namespace: $NS, labels: {app: $name}}
spec:
  nodeSelector: {kubernetes.io/hostname: $node}
  tolerations: [{key: dedicated, operator: Equal, value: special, effect: NoSchedule}]
  restartPolicy: Never
  terminationGracePeriodSeconds: 2
  containers:
    - name: main
      image: python:3.11-slim
      imagePullPolicy: IfNotPresent
      env: [{name: CPU, value: "$cpu"}, {name: CPUS, value: "${CPUS:-$cpu}"}, {name: SENDERS, value: "${SENDERS:-1}"}, {name: PPS, value: "${PPS:-0}"}, {name: SIZE, value: "${SIZE:-512}"}, {name: BURN, value: "${BURN:-0}"}, {name: TARGET, value: "${TARGET:-}"}]
      command: ["python3", "-u", "-c"]
      args:
        - |
$(sed 's/^/          /' <<< "$*")
EOF
}

RECV_PY='import os, socket
os.sched_setaffinity(0, {int(os.environ["CPU"])})
s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM); s.bind(("0.0.0.0", 9999))
s.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 8 << 20)
n = 0
while True:
    s.recv(65536); n += 1
    if n % 1000000 == 0: print("recv", n, flush=True)'

SEND_PY='import os, socket, time, multiprocessing as mp
cpu = int(os.environ["CPU"]); pps = float(os.environ["PPS"]); size = int(os.environ["SIZE"]); burn = int(os.environ["BURN"]); nsend = int(os.environ.get("SENDERS", "1") or 1)
cpus = [int(c) for c in os.environ.get("CPUS", str(cpu)).split(",")]
os.sched_setaffinity(0, set(cpus))
def spin():
    os.sched_setaffinity(0, {cpu})
    while True: pass
for _ in range(burn): mp.Process(target=spin, daemon=True).start()
def sender(i, rate):
    os.sched_setaffinity(0, {cpus[i % len(cpus)]})
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM); s.connect((os.environ["TARGET"], 9999))
    s.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 8 << 20)
    buf = b"k" * size; n = 0; t0 = time.perf_counter(); batch = 64
    while True:
        if rate <= 0: time.sleep(1); continue
        for _ in range(batch):
            try: s.send(buf)
            except OSError: pass
        n += batch
        due = t0 + n / rate
        d = due - time.perf_counter()
        if d > 0: time.sleep(d)
        if i == 0 and n % (int(rate) * 10 or 1) < batch: print("sent", n * nsend, round(time.perf_counter() - t0, 1), flush=True)
ps = [mp.Process(target=sender, args=(i, pps / nsend), daemon=True) for i in range(1, nsend)]
for p in ps: p.start()
sender(0, pps / nsend)'

veth_of() {   # node podIP -> host-side interface
  N $1 "ip route get $2 2>/dev/null | sed -n 's/.* dev \([^ ]*\) .*/\1/p' | head -1"
}

set_rps() {   # node iface cpus
  local mask; mask=$(python3 -c "
m=0
for p in '$3'.split(','):
    lo,_,hi=p.partition('-')
    for c in range(int(lo), int(hi or lo)+1): m |= 1<<c
print('%x' % m)")
  N $1 "echo $mask | sudo tee /sys/class/net/$2/queues/rx-0/rps_cpus >/dev/null; cat /sys/class/net/$2/queues/rx-0/rps_cpus"
}

case ${1:-} in
  start)
    node=$2 scpu=$3 rcpu=$4 pps=$5 size=$6 burn=$7 vcpus=$8 snode=${9:-$2}
    kubectl get ns $NS >/dev/null 2>&1 || kubectl create ns $NS >/dev/null
    PPS=0 SIZE=$size BURN=0 TARGET= pod_yaml k5net-recv $node $rcpu "$RECV_PY" | kubectl apply -f - >/dev/null
    kubectl -n $NS wait --for=condition=Ready pod/k5net-recv --timeout=120s >/dev/null || { log "receiver not ready"; exit 1; }
    rip=$(kubectl -n $NS get pod k5net-recv -o jsonpath='{.status.podIP}')
    PPS=$pps SIZE=$size BURN=$burn TARGET=$rip pod_yaml k5net-send $snode $scpu "$SEND_PY" | kubectl apply -f - >/dev/null
    kubectl -n $NS wait --for=condition=Ready pod/k5net-send --timeout=120s >/dev/null || { log "sender not ready"; exit 1; }
    sip=$(kubectl -n $NS get pod k5net-send -o jsonpath='{.status.podIP}')
    rif=$(veth_of $node $rip); m2=$(set_rps $node $rif $vcpus)
    if [ "$snode" = "$node" ]; then sif=$(veth_of $node $sip); m1=$(set_rps $node $sif $vcpus); else sif=remote; m1=none; fi
    echo "NODE=$node SNODE=$snode SCPU=$scpu RCPU=$rcpu SIZE=$size BURN=$burn VCPUS=$vcpus RIP=$rip SIP=$sip SIF=$sif RIF=$rif SENDERS=${SENDERS:-1} CPUS=${CPUS:-$scpu}" > $ST
    log "net neighbor: send $sip ($sif, CPU $scpu, $pps pps x $size B, burn $burn) -> recv $rip ($rif, CPU $rcpu); RPS $sif=$m1 $rif=$m2 -> CPUs $vcpus"
    echo "$(kubectl -n $NS get pod k5net-send -o jsonpath='{.metadata.uid}') $(kubectl -n $NS get pod k5net-recv -o jsonpath='{.metadata.uid}')" ;;
  rate)
    . $ST; pps=$2
    kubectl -n $NS delete pod k5net-send --wait=true >/dev/null 2>&1
    SENDERS=${SENDERS:-1} CPUS=${CPUS:-$SCPU} PPS=$pps SIZE=$SIZE BURN=$BURN TARGET=$RIP pod_yaml k5net-send ${SNODE:-$NODE} $SCPU "$SEND_PY" | kubectl apply -f - >/dev/null
    kubectl -n $NS wait --for=condition=Ready pod/k5net-send --timeout=120s >/dev/null
    sip=$(kubectl -n $NS get pod k5net-send -o jsonpath='{.status.podIP}')
    if [ "${SNODE:-$NODE}" = "$NODE" ]; then sif=$(veth_of $NODE $sip); m1=$(set_rps $NODE $sif $VCPUS); else sif=remote; m1=none; fi
    sed -i "s/SIP=.*/SIP=$sip SIF=$sif RIF=$RIF/" $ST
    log "rate $pps pps: sender $sip ($sif), RPS $m1"
    kubectl -n $NS get pod k5net-send -o jsonpath='{.metadata.uid}'; echo ;;
  calib)
    node=$2 vcpus=$3 secs=${4:-20}
    ssh -o BatchMode=yes $node "python3 - $vcpus $secs" <<'EOF'
import sys, time
cpul = []
for p in sys.argv[1].split(','):
    lo, _, hi = p.partition('-'); cpul += list(range(int(lo), int(hi or lo) + 1))
def sirq():
    L = open('/proc/softirqs').read().split('\n'); idx = {int(h[3:]): i for i, h in enumerate(L[0].split())}
    return {l.split()[0].rstrip(':'): {c: int(l.split()[1:][idx[c]]) for c in cpul} for l in L[1:] if l.split()}
def stat():
    return {int(l.split()[0][3:]): [int(x) for x in l.split()[1:9]] for l in open('/proc/stat') if l.startswith('cpu') and l[3] != ' '}
a, sa = sirq(), stat(); time.sleep(float(sys.argv[2])); b, sb = sirq(), stat()
hz = 100.0; T = float(sys.argv[2])
for c in cpul:
    d = {k: (b[k][c] - a[k][c]) / T for k in b}
    st = [sb[c][i] - sa[c][i] for i in range(8)]; tot = sum(st)
    print(f"cpu {c}: NET_RX {d.get('NET_RX', 0):.0f}/s NET_TX {d.get('NET_TX', 0):.0f}/s TIMER {d.get('TIMER', 0):.0f}/s SCHED {d.get('SCHED', 0):.0f}/s RCU {d.get('RCU', 0):.0f}/s; "
          f"softirq time {st[6] / hz / T * 100:.1f} % irq {st[5] / hz / T * 100:.1f} % busy {(tot - st[3] - st[4]) / hz / T * 100:.1f} %")
EOF
    ;;
  stop)
    kubectl delete ns $NS --wait=true >/dev/null 2>&1; rm -f $ST; log "net neighbor stopped (namespace $NS deleted)" ;;
  *) echo "usage: $0 start NODE SEND_CPU RECV_CPU PPS SIZE BURN VICTIM_CPUS | rate PPS | calib NODE VICTIM_CPUS SECS | stop"; exit 2 ;;
esac
