#!/bin/bash
# CPU aggressor for A5: stress-ng hogs on a subset of the service's cores, run on the service node's host.
# usage: stressor.sh start [cpus=2-5] [hogs=4] | stop | status
#   env NODE=node-3; Phase G: METHOD=matrixprod (CPU time-slicer, default) | stream (bandwidth, A.5 c iv) | cache (LLC capacity, D.4);
#   CGROUP=/sys/fs/cgroup/gordion-aggr -- the aggressor runs inside its own cgroup v2 so the scheduler probe can name it
#   as the holder of the CPU (R_foreign).
set -e
NODE=${NODE:-node-3}
METHOD=${METHOD:-matrixprod}
CG=${CGROUP:-/sys/fs/cgroup/gordion-aggr}
case "${1:-}" in
  start)
    CPUS=${2:-2-5}; HOGS=${3:-4}
    # METHOD=cache (Phase G D.4): LLC-capacity aggressor with reuse, stress-ng's cache stressor confined to level 3;
    # --cache-no-affinity because the stressor otherwise re-pins its workers across ALL online CPUs (it would land on the SUT cores)
    # METHOD=duty<N> (Addendum F.3): a CPU hog busy about N % of the time in ~10 ms bursts (stress-ng --cpu-load N --cpu-load-slice 10)
    if [ "$METHOD" = stream ]; then ARGS="--stream $HOGS"; elif [ "$METHOD" = cache ]; then ARGS="--cache $HOGS --cache-level 3 --cache-no-affinity"; elif [ "${METHOD#duty}" != "$METHOD" ]; then ARGS="--cpu $HOGS --cpu-method matrixprod --cpu-load ${METHOD#duty} --cpu-load-slice ${SLICE_MS:-10}"; else ARGS="--cpu $HOGS --cpu-method $METHOD"; fi
    ssh -o BatchMode=yes $NODE "which stress-ng >/dev/null || (sudo apt-get install -y -q stress-ng >/dev/null 2>&1); \
      sudo pkill -x stress-ng 2>/dev/null; sleep 0.5; sudo mkdir -p $CG; \
      sudo tee /tmp/gordion-aggr.sh >/dev/null <<'EOS'
#!/bin/sh
echo \$\$ > CGPATH/cgroup.procs
exec taskset -c CPUSPEC stress-ng STRESSARGS -t 7200
EOS
      sudo sed -i -e 's#CGPATH#$CG#' -e 's#CPUSPEC#$CPUS#' -e 's#STRESSARGS#$ARGS#' /tmp/gordion-aggr.sh; sudo chmod +x /tmp/gordion-aggr.sh; \
      nohup sudo /tmp/gordion-aggr.sh > /tmp/stress-ng.log 2>&1 & \
      sleep 1; pgrep -c -x stress-ng; echo cgroup: \$(cat $CG/cgroup.procs | wc -l) procs"
    ;;
  stop)   ssh -o BatchMode=yes $NODE "sudo pkill -x stress-ng; sleep 0.5; pgrep -c -x stress-ng || echo 0";;
  status) ssh -o BatchMode=yes $NODE "pgrep -a -x stress-ng | head -3; pgrep -c -x stress-ng || echo 0";;
  *) echo "usage: $0 start [cpus] [hogs] | stop | status"; exit 1;;
esac
