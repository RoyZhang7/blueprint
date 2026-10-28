#!/bin/bash
# Phase A2 driver (apt186 cluster, 2026-10-27). Prerequisites on node-4: SCHED_IDLE busy loops on the service cores
# (see RESULTS.md "Phase A2"), /local/results writable, wlgen pinned via WLGEN_NODE/WLGEN_CPUS.
# Final configuration: service on node-4 cores 2-5 with a SCHED_IDLE filler holding the frequency, load generator
# pinned on node-4 cores 1,6,7 (shared clock, no overlay path). K=123: uniform dictionary + E1 short/long mixes;
# families (184): uniform, random Dirichlet mixes, stressed uniform (2 hogs on cores 4-5).
export PATH=$PATH:/usr/local/go/bin:$HOME/go/bin
export NODE=node-4 CPUSET=2-5 WLGEN_NODE=node-4 WLGEN_CPUS=1,6,7
cd /local/blueprint/examples/bigsvc/scripts
ssh -o BatchMode=yes node-4 "sudo mkdir -p /local/results && sudo chown \$USER /local/results; pgrep -fc 'cpu-method int64' || (sudo nohup chrt --idle 0 taskset -c 2-5 stress-ng --cpu 4 --cpu-method int64 -t 0 >/dev/null 2>&1 &); sleep 2; echo node-4 filler=\$(pgrep -fc 'cpu-method int64') khz=\$(cat /sys/devices/system/cpu/cpu[2-5]/cpufreq/scaling_cur_freq | tr '\n' ' ')"
scp -q -o BatchMode=yes /local/results/mix-short.json /local/results/mix-long.json node-4:/local/results/
U="40:150,100:45,200:45,300:45,400:45,500:45,600:45,700:45,750:45,800:45,850:45,900:45"
echo "=== Q a2-uniform $(date +%H:%M:%S)"; TAG=k123 ./exp.sh run q-a2-uniform -- -mix uniform -warmup 10 -schedule "$U" 2>&1 | tail -1
echo "=== Q e1-short $(date +%H:%M:%S)";   TAG=k123 ./exp.sh run q-e1-short -- -mix file:/local/results/mix-short.json -warmup 10 -schedule "$(cat /local/results/sched-short)" 2>&1 | tail -1
echo "=== Q e1-long $(date +%H:%M:%S)";    TAG=k123 ./exp.sh run q-e1-long -- -mix file:/local/results/mix-long.json -warmup 10 -schedule "$(cat /local/results/sched-long)" 2>&1 | tail -1
UF="60:150,100:45,200:45,300:45,400:45,500:45,600:45,700:45,750:45,800:45,850:45,900:45"
echo "=== Q fam-a2-uniform $(date +%H:%M:%S)"; TAG=fam184 ./exp.sh run q-fam-a2-uniform -- -mix uniform -warmup 10 -schedule "$UF" 2>&1 | tail -1
echo "=== Q fam-a3-dirichlet $(date +%H:%M:%S)"; TAG=fam184 ./exp.sh run q-fam-a3-dirichlet -- -mix dirichlet -alpha 1 -resample 10 -warmup 10 -seed 7 -schedule "$(bash a3-schedule.sh 40 30 100 850 7)" 2>&1 | tail -1
echo "=== Q stressor on $(date +%H:%M:%S)"; NODE=node-4 bash stressor.sh start 4-5 2
echo "=== Q fam-a5-stress $(date +%H:%M:%S)"; TAG=fam184 ./exp.sh run q-fam-a5-stress -- -mix uniform -warmup 10 -schedule "60:120,100:45,200:45,300:45,400:45,500:45,600:45,700:45,800:45" 2>&1 | tail -1
echo "=== Q stressor off $(date +%H:%M:%S)"; NODE=node-4 bash stressor.sh stop
echo "=== DONEQ $(date +%H:%M:%S)"
