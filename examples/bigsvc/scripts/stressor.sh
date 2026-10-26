#!/bin/bash
# CPU aggressor for A5: stress-ng hogs on a subset of the service's cores, run on the service node's host.
# usage: stressor.sh start [cpus=2-5] [hogs=4] | stop | status      (env NODE=node-3)
set -e
NODE=${NODE:-node-3}
case "${1:-}" in
  start)
    CPUS=${2:-2-5}; HOGS=${3:-4}
    ssh -o BatchMode=yes $NODE "which stress-ng >/dev/null || (sudo apt-get install -y -q stress-ng >/dev/null 2>&1); \
      sudo pkill -x stress-ng 2>/dev/null; sleep 0.5; \
      nohup sudo taskset -c $CPUS stress-ng --cpu $HOGS --cpu-method matrixprod -t 7200 > /tmp/stress-ng.log 2>&1 & \
      sleep 1; pgrep -c -x stress-ng"
    ;;
  stop)   ssh -o BatchMode=yes $NODE "sudo pkill -x stress-ng; sleep 0.5; pgrep -c -x stress-ng || echo 0";;
  status) ssh -o BatchMode=yes $NODE "pgrep -a -x stress-ng | head -3; pgrep -c -x stress-ng || echo 0";;
  *) echo "usage: $0 start [cpus] [hogs] | stop | status"; exit 1;;
esac
