#!/bin/bash
# Phase K4 (node-4, root): drive HarvestContainers the way simple-mitigation-1/pkg/actuators/hc does, and nothing else.
# Never reads any /proc/idlecpu file (they dereference NULL when their list is unset, RESULTS-K 0b.3); only writes cpulist.
#
#   k4node.sh hc-load                      Listener up (its ExecStartPre loads qidlecpu, Monitor on HC_MONITOR_CPU), cpulist <- $PCPUS
#   k4node.sh hc-unload                    Balancer off, Listener stopped, qidlecpu removed
#   k4node.sh hc-on SECONDARY_POD_UID      confine the Secondary pod's cpusets to $PCPUS+$SCPUS (originals saved), Primary and
#                                          Secondary messages to the Listener, Balancer as transient unit hc-balancer-dyn
#   k4node.sh hc-on-nosec                  K4.1b "HC only": Primary message and the Balancer as in hc-on, no Secondary message and
#                                          no cpuset change (the module services the Balancer's requests with no task to move)
#   k4node.sh hc-off                       Balancer SIGUSR1 (then stop), Reset message, cpusets restored
#   k4node.sh sec-pin SECONDARY_POD_UID    the Secondary pod's own allocation: cpuset.cpus <- $SCPUS (pod, then containers)
#   k4node.sh status                       module / units / kthread placement (no procfs read)
# Env: PCPUS=4,6,8,10 SCPUS=12 BAL_CPU=24 BAL_BIN=/local/HarvestContainers/TestFramework/bin/balancer TIC=1 LOW_TIC=1 ACBF=0.33
#      MINSEC=0 LOGDIR=/mnt/extra/logs/hc/k4-<ms>
#      K4.2: IRQ_AWARE=1 makes hc-on / hc-on-nosec write HC's IRQ procfs path as the authors' make_irq_aware.sh does
#      (irqlist <- the IRQs of IRQ_DEV=eno2, irqaffinity <- IRQ_CPUS=12,14, irqcontrol <- 1), before the Balancer starts;
#      every IRQ's smp_affinity_list is saved first and restored by hc-off / hc-unload (the module never restores it).
#      Write only: the IRQ procfs readers are never used (RESULTS-K 0b.3). More than 64 IRQs (irqList[64]) is refused.
set -u
D=$(cd "$(dirname "$0")" && pwd)
PCPUS=${PCPUS:-4,6,8,10}; SCPUS=${SCPUS:-12}; BAL_CPU=${BAL_CPU:-24}; BAL_BIN=${BAL_BIN:-/local/HarvestContainers/TestFramework/bin/balancer}
TIC=${TIC:-1}; LOW_TIC=${LOW_TIC:-1}; ACBF=${ACBF:-0.33}; MINSEC=${MINSEC:-0}
STATE=/var/tmp/k4/cpuset-orig.txt; IRQSTATE=/var/tmp/k4/irq-orig.txt
IRQ_AWARE=${IRQ_AWARE:-0}; IRQ_DEV=${IRQ_DEV:-eno2}; IRQ_CPUS=${IRQ_CPUS:-12,14}
mkdir -p /var/tmp/k4
log() { echo "[k4node $(date -u +%T)] $*"; }
loaded() { lsmod | grep -q '^qidlecpu '; }
bal_active() { case "$(systemctl is-active hc-balancer-dyn 2>/dev/null)" in active|activating|reloading) return 0;; esac; return 1; }

irq_on() {
  local irqs n first
  irqs=$(awk -v d="$IRQ_DEV" 'index($0, d) {sub(":", "", $1); print $1}' /proc/interrupts | paste -sd,)
  n=$(echo "$irqs" | tr , '\n' | grep -c .)
  [ "$n" -ge 1 ] && [ "$n" -le 64 ] || { log "IRQ path NOT written: $IRQ_DEV has $n IRQs (module holds 64)"; return 1; }
  if [ ! -s $IRQSTATE ]; then for i in ${irqs//,/ }; do echo "$i $(cat /proc/irq/$i/smp_affinity_list)" >> $IRQSTATE; done; fi
  echo "$irqs" > /proc/idlecpu/irqlist && echo "$IRQ_CPUS" > /proc/idlecpu/irqaffinity && echo 1 > /proc/idlecpu/irqcontrol || { log "IRQ path write failed"; return 1; }
  first=${irqs%%,*}
  log "IRQ path: $n IRQs of $IRQ_DEV ($first..${irqs##*,}) -> $IRQ_CPUS; irq $first now $(cat /proc/irq/$first/smp_affinity_list) (effective $(cat /proc/irq/$first/effective_affinity_list)), was $(awk -v i=$first '$1==i {print $2}' $IRQSTATE)"
}
irq_off() {
  [ -s $IRQSTATE ] || return 0
  loaded && echo 0 > /proc/idlecpu/irqcontrol
  local bad=0
  while read -r i aff; do echo "$aff" > /proc/irq/$i/smp_affinity_list 2>/dev/null || bad=$((bad + 1)); done < $IRQSTATE
  log "IRQ affinities restored: $(wc -l < $IRQSTATE) IRQs, $bad failed; e.g. $(head -1 $IRQSTATE | cut -d' ' -f1) -> $(cat /proc/irq/$(head -1 $IRQSTATE | cut -d' ' -f1)/smp_affinity_list)"
  : > $IRQSTATE
}

pod_dir() {   # pod UID -> its cgroup v2 directory (systemd driver: dashes become underscores)
  local u=${1//-/_}
  ls -d /sys/fs/cgroup/kubepods.slice/kubepods-pod${u}.slice /sys/fs/cgroup/kubepods.slice/kubepods-*.slice/kubepods-*-pod${u}.slice 2>/dev/null | head -1
}

status() {
  echo "module: $(loaded && echo loaded || echo absent); listener: $(systemctl is-active hc-listener); balancer-dyn: $(systemctl is-active hc-balancer-dyn 2>/dev/null); static balancer: $(systemctl is-active hc-balancer)"
  ps -eLo pid,psr,pcpu,comm | awk '$4=="check_idle_cpus" || $4=="balancer" || $4=="listener" {print "  " $0}'
}

case ${1:-status} in
  hc-load)
    if ! loaded; then systemctl start hc-listener; sleep 3; fi
    loaded || { log "qidlecpu did not load"; exit 1; }
    systemctl is-active -q hc-listener || systemctl start hc-listener
    echo "$PCPUS" > /proc/idlecpu/cpulist        # write only, as the actuator's syncMonitorCPUList
    log "loaded, cpulist <- $PCPUS"; status ;;
  hc-unload)
    irq_off
    if bal_active; then systemctl kill -s SIGUSR1 hc-balancer-dyn; sleep 2; bal_active && systemctl stop hc-balancer-dyn; fi
    systemctl stop hc-listener 2>/dev/null
    if loaded; then for i in 1 2 3 4 5; do rmmod qidlecpu 2>/dev/null && break; sleep 1; done; fi
    loaded && { log "qidlecpu still loaded"; exit 1; }
    log "unloaded"; status ;;
  hc-on)
    uid=$2; pd=$(pod_dir "$uid"); [ -n "$pd" ] || { log "no cgroup for pod $uid"; exit 1; }
    : > $STATE
    for d in "$pd" $(ls -d "$pd"/*.scope 2>/dev/null); do        # parents first, as hc.go confine()
      [ -f "$d/cpuset.cpus" ] || continue
      echo "$d $(cat $d/cpuset.cpus)" >> $STATE
      echo "$PCPUS,$SCPUS" > "$d/cpuset.cpus" || log "write $d/cpuset.cpus failed"
    done
    log "confined: $(wc -l < $STATE) cgroup dirs -> $PCPUS,$SCPUS"
    python3 $D/k4send.py "{\"ctr_type\":\"Primary\",\"cpuList\":\"$PCPUS\",\"LOWIDLEFREQ_THRESHOLD\":\"$ACBF\",\"LOW_TIC\":\"$LOW_TIC\",\"targetIdleCores\":\"$TIC\",\"static_targetIdleCores\":\"$TIC\",\"minSecondaryCores\":\"$MINSEC\"}" || exit 1
    python3 $D/k4send.py "{\"ctr_type\":\"Secondary\",\"pod_id\":\"pod$uid\",\"parent\":\"any\"}" || exit 1
    [ "$IRQ_AWARE" = 1 ] && irq_on
    LOGDIR=${LOGDIR:-/mnt/extra/logs/hc/k4-$(date +%s%3N)}; mkdir -p $LOGDIR
    systemd-run --unit=hc-balancer-dyn --collect -p CPUAffinity=$BAL_CPU -p WorkingDirectory=$LOGDIR \
      --setenv=LOWIDLEFREQ_THRESHOLD=$ACBF --setenv=LOW_TIC=$LOW_TIC $BAL_BIN 0 $TIC $MINSEC $PCPUS $SCPUS
    sleep 1; bal_active || { log "balancer did not start"; journalctl -u hc-balancer-dyn -n 20 --no-pager; exit 1; }
    echo $LOGDIR > /var/tmp/k4/logdir.txt
    log "HC on: Primary $PCPUS, Secondary pod $uid on $SCPUS, Balancer $BAL_BIN on CPU $BAL_CPU, log $LOGDIR"; status ;;
  hc-on-nosec)
    : > $STATE
    python3 $D/k4send.py "{\"ctr_type\":\"Primary\",\"cpuList\":\"$PCPUS\",\"LOWIDLEFREQ_THRESHOLD\":\"$ACBF\",\"LOW_TIC\":\"$LOW_TIC\",\"targetIdleCores\":\"$TIC\",\"static_targetIdleCores\":\"$TIC\",\"minSecondaryCores\":\"$MINSEC\"}" || exit 1
    [ "$IRQ_AWARE" = 1 ] && irq_on
    LOGDIR=${LOGDIR:-/mnt/extra/logs/hc/k4-$(date +%s%3N)}; mkdir -p $LOGDIR
    systemd-run --unit=hc-balancer-dyn --collect -p CPUAffinity=$BAL_CPU -p WorkingDirectory=$LOGDIR \
      --setenv=LOWIDLEFREQ_THRESHOLD=$ACBF --setenv=LOW_TIC=$LOW_TIC $BAL_BIN 0 $TIC $MINSEC $PCPUS $SCPUS
    sleep 1; bal_active || { log "balancer did not start"; journalctl -u hc-balancer-dyn -n 20 --no-pager; exit 1; }
    echo $LOGDIR > /var/tmp/k4/logdir.txt
    log "HC on, no Secondary: Primary $PCPUS, Balancer $BAL_BIN on CPU $BAL_CPU, log $LOGDIR"; status ;;
  hc-off)
    if bal_active; then
      systemctl kill -s SIGUSR1 hc-balancer-dyn
      for i in $(seq 1 50); do bal_active || break; sleep 0.2; done
      bal_active && systemctl stop hc-balancer-dyn
    fi
    loaded && python3 $D/k4send.py '{"ctr_type":"Reset"}'
    if [ -s $STATE ]; then   # an empty original (inherit) is refused on write (ENOSPC): write the parent's effective set instead
      while read -r d orig; do [ -d "$d" ] || continue; [ -n "$orig" ] || orig=$(cat "$(dirname $d)/cpuset.cpus.effective"); echo "$orig" > "$d/cpuset.cpus"; done < $STATE; : > $STATE
    fi
    irq_off
    log "HC off (balancer $(systemctl is-active hc-balancer-dyn 2>/dev/null), cpusets restored)"; status ;;
  sec-pin)
    uid=$2; pd=$(pod_dir "$uid"); [ -n "$pd" ] || { log "no cgroup for pod $uid"; exit 1; }
    for d in "$pd" $(ls -d "$pd"/*.scope 2>/dev/null); do [ -f "$d/cpuset.cpus" ] && echo "$SCPUS" > "$d/cpuset.cpus"; done
    log "Secondary pod $uid pinned to $SCPUS: $(cat $pd/cpuset.cpus.effective)" ;;
  status) status ;;
  *) echo "usage: $0 hc-load|hc-unload|hc-on UID|hc-on-nosec|hc-off|sec-pin UID|status"; exit 2 ;;
esac
