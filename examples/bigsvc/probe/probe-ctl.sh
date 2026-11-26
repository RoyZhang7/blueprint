#!/bin/bash
# Gordion Phase G probe controller, runs on the service node (needs sudo for bpftrace / perf).
# usage: probe-ctl.sh start <container-name-fragment> <cpuset> <out.jsonl> [aggr-cgroup-dir]
#        probe-ctl.sh stop <out.jsonl>
#        probe-ctl.sh aggr-cgroup            # create /sys/fs/cgroup/gordion-aggr for stress-ng (so R_foreign has a cgroup id)
#        probe-ctl.sh perf-start <container-name-fragment> <aggr-cpus> <out-prefix>   # v4 (E.3 c): perf stat counting mode,
#              cycles / instructions / LLC-loads / LLC-load-misses per 100 ms: the victim's cgroup on SVC_CPUSET (cgroup mode,
#              every thread incl. later ones) -> <out-prefix>-perf-victim.csv and the aggressor CPUs (system-wide on those CPUs) -> <out-prefix>-perf-aggr.csv
#        probe-ctl.sh perf-stop <out-prefix>
# The pod's cgroup is found from its container id (docker + systemd cgroup driver, cgroup v2). The probe's pid is kept
# in <out.jsonl>.pid; stop sends SIGINT so bpftrace detaches and the wrapper writes its "end" record.
# bpftrace's userspace (map prints) and perf are pinned to PROBE_CPU (default 9, off the SUT cores) since v3.2.
# v4: SIB_CPUSET=29,31,33,35 in the environment adds the sibling occupancy (phi_sibling) to the probe.
set -u
DIR=$(cd "$(dirname "$0")" && pwd)
PERF_EVENTS=${PERF_EVENTS:-cycles,instructions,LLC-loads,LLC-load-misses}
find_cid() { local frag=$1 cid=""; for i in $(seq 1 60); do cid=$(sudo docker ps -q --no-trunc --filter "name=$frag" | head -1); [ -n "$cid" ] && break; sleep 1; done; echo "$cid"; }
case "${1:-}" in
  start)
    frag=$2; cpuset=$3; out=$4; aggr=${5:-}
    cid=$(find_cid "$frag"); [ -z "$cid" ] && { echo "no container matching $frag"; exit 1; }
    cg=$(find /sys/fs/cgroup/kubepods.slice -maxdepth 3 -type d -name "*${cid:0:12}*" | head -1)
    [ -z "$cg" ] && { echo "no cgroup for $cid"; exit 1; }
    sudo nohup setsid taskset -c ${PROBE_CPU:-9} python3 $DIR/probe.py --cpuset "$cpuset" --svc-cgroup "$cg" ${aggr:+--aggr-cgroup "$aggr"} ${SIB_CPUSET:+--sibling-cpuset "$SIB_CPUSET"} --out "$out" > "$out.log" 2>&1 < /dev/null &
    sleep 4; pid=$(pgrep -f "probe.py --cpuset $cpuset --svc-cgroup $cg" | head -1); echo "$pid" > "$out.pid"
    echo "probe pid $pid on $cg -> $out (siblings: ${SIB_CPUSET:-none})"; tail -2 "$out.log"
    ;;
  stop)
    out=$2; pid=$(cat "$out.pid" 2>/dev/null)
    [ -n "$pid" ] && sudo kill -INT $pid 2>/dev/null
    for i in $(seq 1 15); do kill -0 $pid 2>/dev/null || break; sleep 1; done
    kill -0 $pid 2>/dev/null && sudo kill -TERM $pid
    tail -1 "$out.log"; wc -l "$out"
    ;;
  aggr-cgroup)
    sudo mkdir -p /sys/fs/cgroup/gordion-aggr && echo "/sys/fs/cgroup/gordion-aggr ($(stat -c %i /sys/fs/cgroup/gordion-aggr))"
    ;;
  perf-start)
    frag=$2; acpus=$3; out=$4
    cid=$(find_cid "$frag"); [ -z "$cid" ] && { echo "no container matching $frag"; exit 1; }
    vpid=$(sudo docker inspect -f '{{.State.Pid}}' $cid)
    # v4.1: the victim is counted in cgroup mode (-G, cgroup v2 path relative to /sys/fs/cgroup, system-wide on SVC_CPUSET):
    # -p <init pid> counted only the container's init process (the service is its child, started before perf attached)
    cg=$(find /sys/fs/cgroup/kubepods.slice -maxdepth 3 -type d -name "*${cid:0:12}*" | head -1); rel=${cg#/sys/fs/cgroup/}; vcpus=${SVC_CPUSET:-1,3,5,7}
    echo "start_ms $(date +%s%3N) victim_pid $vpid cid ${cid:0:12} victim_cgroup $rel victim_cpus $vcpus aggr_cpus $acpus events $PERF_EVENTS mode cgroup" > "$out-perf.meta"
    sudo nohup setsid taskset -c ${PROBE_CPU:-9} perf stat -e $PERF_EVENTS -a -C $vcpus -G $rel -I 100 -x, -o "$out-perf-victim.csv" > "$out-perf-victim.log" 2>&1 < /dev/null &
    sudo nohup setsid taskset -c ${PROBE_CPU:-9} perf stat -e $PERF_EVENTS -a -C $acpus -I 100 -x, -o "$out-perf-aggr.csv" > "$out-perf-aggr.log" 2>&1 < /dev/null &
    sleep 2; pgrep -f "perf stat -e $PERF_EVENTS" | tr '\n' ' ' > "$out-perf.pid"; echo "perf pids $(cat $out-perf.pid) victim pid $vpid, aggressor CPUs $acpus -> $out-perf-{victim,aggr}.csv"
    ;;
  perf-stop)
    out=$2; for p in $(cat "$out-perf.pid" 2>/dev/null); do sudo kill -INT $p 2>/dev/null; done; sleep 2
    for p in $(cat "$out-perf.pid" 2>/dev/null); do kill -0 $p 2>/dev/null && sudo kill -TERM $p; done
    echo "perf stopped: $(wc -l < "$out-perf-victim.csv" 2>/dev/null) victim lines, $(wc -l < "$out-perf-aggr.csv" 2>/dev/null) aggressor lines"
    ;;
  *) echo "usage: $0 start <frag> <cpuset> <out> [aggr-cgroup] | stop <out> | aggr-cgroup | perf-start <frag> <aggr-cpus> <out-prefix> | perf-stop <out-prefix>"; exit 1;;
esac
