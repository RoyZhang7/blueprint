#!/bin/bash
# Phase K4.1b (node-4, root): the lean libbpf probe of the K1 addendum (k1lean.bpf.o, sched_switch program only) on the
# Primary's cores. Service class = the Primary's container cgroup; aggressor class = the Secondary's (burner) container
# cgroup when there is one. The reader is pinned to CPU 53 (socket 1, otherwise idle). The queue sets
# kernel.bpf_stats_enabled=1 for the whole of K4.1b; the sampler reads the program's run time from the loader's fdinfo.
#   k4probe.sh start SEG PRIMARY_UID [SECONDARY_UID]   -> /var/tmp/k4/SEG-probe.jsonl and SEG-probe-hist.jsonl
#   k4probe.sh stop SEG
set -u
B=/var/tmp/k4/bin; O=/var/tmp/k4; PCPUS=${PCPUS:-4,6,8,10}; PROBE_CPU=${PROBE_CPU:-53}
pod_dir() {
  local u=${1//-/_}
  ls -d /sys/fs/cgroup/kubepods.slice/kubepods-pod${u}.slice /sys/fs/cgroup/kubepods.slice/kubepods-*.slice/kubepods-*-pod${u}.slice 2>/dev/null | head -1
}
ctr_scope() {   # pod dir -> the scope of its non-pause container
  local d p
  for d in "$1"/*.scope; do
    for p in $(cat $d/cgroup.procs 2>/dev/null); do [ "$(cat /proc/$p/comm 2>/dev/null)" != pause ] && { echo $d; return; }; done
  done
}
case ${1:-} in
  start)
    seg=$2; pd=$(pod_dir $3); [ -n "$pd" ] || { echo "no cgroup for Primary pod $3"; exit 1; }
    svc=$(ctr_scope $pd); aggr=""
    if [ -n "${4:-}" ]; then sd=$(pod_dir $4); [ -n "$sd" ] && aggr=$(ctr_scope $sd); fi
    [ -n "$svc" ] || { echo "no Primary container scope in $pd"; exit 1; }
    nohup taskset -c $PROBE_CPU $B/k1lean $B/k1lean.bpf.o --progs switch --cpuset $PCPUS --svc-cgroup $svc ${aggr:+--aggr-cgroup $aggr} \
      --out $O/$seg-probe.jsonl > $O/$seg-probe.log 2>&1 < /dev/null &
    echo $! > $O/probe.pid; sleep 2
    if grep -q "^prog lean_switch" $O/$seg-probe.log; then echo "probe on: pid $(cat $O/probe.pid), svc $svc, aggr ${aggr:-none}"
    else echo "probe FAILED:"; cat $O/$seg-probe.log; exit 1; fi ;;
  stop)
    pid=$(cat $O/probe.pid 2>/dev/null || true)
    if [ -n "$pid" ]; then
      kill -INT $pid 2>/dev/null; for i in $(seq 1 20); do kill -0 $pid 2>/dev/null || break; sleep 0.5; done
      kill -0 $pid 2>/dev/null && kill -9 $pid
    fi
    rm -f $O/probe.pid; echo "probe off: $(grep -c '^{"t"' $O/$2-probe.jsonl 2>/dev/null) windows" ;;
  *) echo "usage: $0 start SEG PRIMARY_UID [SECONDARY_UID] | stop SEG"; exit 2 ;;
esac
