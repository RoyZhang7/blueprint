#!/bin/bash
# Phase G Addendum E.2: read-only checklist of what the service node exposes for probe v4 and the counters.
# Run ON the service node (sudo for dmesg / cpuid only; nothing is mounted, installed or written):
#   bash e2_checks.sh [cpuset]          default cpuset 1,3,5,7 (the Gordion SUT cores on clnode170 node-3)
# Sections: resctrl (CMT / MBM / CAT), cgroup v2 cpu.pressure per pod, PMU (perf_event_paranoid, rdpmc, counters with
# SMT on), the SMT sibling map of the cpuset, the libbpf toolchain (clang, libbpf headers, bpftool) and its apt candidates.
CS=${1:-1,3,5,7}
cpus() { local out=(); for p in ${1//,/ }; do lo=${p%-*}; hi=${p#*-}; for ((c=lo; c<=hi; c++)); do out+=($c); done; done; echo "${out[@]}"; }
KR=$(uname -r)
echo "# e2_checks $(date -u +%FT%TZ) host=$(hostname) kernel=$KR cpuset=$CS"
echo "## resctrl"
echo "mounted: $(mount | grep -c ' /sys/fs/resctrl ') ($(mount | grep resctrl))"
echo "dir /sys/fs/resctrl: $(ls -d /sys/fs/resctrl 2>/dev/null || echo absent); entries: $(ls /sys/fs/resctrl 2>/dev/null | tr '\n' ' ')"
echo "info: $(ls /sys/fs/resctrl/info 2>/dev/null | tr '\n' ' ')"
[ -d /sys/fs/resctrl/info/L3_MON ] && echo "L3_MON features: $(cat /sys/fs/resctrl/info/L3_MON/mon_features | tr '\n' ' '); num_rmids $(cat /sys/fs/resctrl/info/L3_MON/num_rmids)"
[ -d /sys/fs/resctrl/info/L3 ] && echo "L3 CAT: cbm_mask $(cat /sys/fs/resctrl/info/L3/cbm_mask) num_closids $(cat /sys/fs/resctrl/info/L3/num_closids) min_cbm_bits $(cat /sys/fs/resctrl/info/L3/min_cbm_bits)"
echo "cpuinfo RDT flags: $(grep -o -w -E 'rdt_a|cqm|cqm_llc|cqm_occup_llc|cqm_mbm_total|cqm_mbm_local|cat_l3|cdp_l3|mba' /proc/cpuinfo | sort | uniq -c | tr '\n' ' ')"
echo "kernel config: $(grep -E '^CONFIG_X86_CPU_RESCTRL|^CONFIG_INTEL_RDT' /boot/config-$KR 2>/dev/null | tr '\n' ' ')"
echo "cmdline rdt=: $(grep -o 'rdt=[^ ]*' /proc/cmdline || echo none)"
echo "## cgroup v2 cpu.pressure"
echo "cgroup fs: $(stat -f -c %T /sys/fs/cgroup); psi system: $(head -1 /proc/pressure/cpu 2>/dev/null)"
echo "cgroup_controllers: $(cat /sys/fs/cgroup/cgroup.controllers); kubepods subtree: $(cat /sys/fs/cgroup/kubepods.slice/cgroup.subtree_control 2>/dev/null)"
echo "pod/container cpu.pressure files (depth<=3): $(find /sys/fs/cgroup/kubepods.slice -maxdepth 3 -name cpu.pressure 2>/dev/null | wc -l)"
f=$(find /sys/fs/cgroup/kubepods.slice -mindepth 3 -maxdepth 3 -name cpu.pressure 2>/dev/null | head -1)
[ -n "$f" ] && echo "sample $f ($(stat -c '%A %U' $f)) read by $(id -un): $(cat $f 2>/dev/null | tr '\n' ' ' || echo NO)"
[ -n "$f" ] && echo "cpu.stat of the same: $(cat $(dirname $f)/cpu.stat 2>/dev/null | tr '\n' ' ')"
echo "## PMU"
echo "perf_event_paranoid=$(cat /proc/sys/kernel/perf_event_paranoid) kptr_restrict=$(cat /proc/sys/kernel/kptr_restrict) nmi_watchdog=$(cat /proc/sys/kernel/nmi_watchdog)"
echo "rdpmc=$(cat /sys/devices/cpu/rdpmc 2>/dev/null || echo absent) (0 off, 1 with an open event, 2 always)"
echo "event sources: $(ls /sys/bus/event_source/devices/ | tr '\n' ' ')"
echo "cpu caps: $(for c in /sys/devices/cpu/caps/*; do echo -n "$(basename $c)=$(cat $c) "; done)"
echo "model: $(lscpu | grep 'Model name' | sed 's/.*: *//'); SMT control $(cat /sys/devices/system/cpu/smt/control)"
echo "dmesg PMU: $(sudo dmesg 2>/dev/null | grep -i -E 'PMU driver|generic registers|fixed-purpose|generic counters|counters:' | head -4 | tr '\n' ';')"
if [ -e /dev/cpu/0/cpuid ]; then
  echo "cpuid leaf 0xA: $(sudo python3 -c "
import os, struct
fd = os.open('/dev/cpu/0/cpuid', os.O_RDONLY); b = os.pread(fd, 16, 0xA); os.close(fd); a, bb, c, d = struct.unpack('<IIII', b)
print('version %d, programmable counters per logical CPU %d, width %d bits, fixed counters %d, width %d' % (a & 0xff, (a >> 8) & 0xff, (a >> 16) & 0xff, d & 0x1f, (d >> 5) & 0xff))
" 2>&1)"
else echo "cpuid: /dev/cpu/0/cpuid absent (modprobe cpuid would be needed; not done)"; fi
echo "perf tool: $(which perf || echo absent); linux-tools: $(dpkg -l 2>/dev/null | awk '/linux-tools/ {print $2"="$3}' | tr '\n' ' ')"
if which perf >/dev/null 2>&1; then echo "perf stat smoke (cycles,instructions,LLC-load-misses on 1 CPU, 0.2 s): $(sudo perf stat -e cycles,instructions,LLC-load-misses -C ${CS%%,*} -- sleep 0.2 2>&1 | grep -E 'cycles|instructions|LLC' | awk '{print $1, $2}' | tr '\n' ' ')"; fi
echo "## SMT sibling map of the cpuset"
for c in $(cpus $CS); do echo "cpu$c: siblings $(cat /sys/devices/system/cpu/cpu$c/topology/thread_siblings_list) core_id $(cat /sys/devices/system/cpu/cpu$c/topology/core_id) package $(cat /sys/devices/system/cpu/cpu$c/topology/physical_package_id) llc $(cat /sys/devices/system/cpu/cpu$c/cache/index3/shared_cpu_list 2>/dev/null | cut -c1-40)"; done
sib=""; for c in $(cpus $CS); do s=$(cat /sys/devices/system/cpu/cpu$c/topology/thread_siblings_list | tr ',' '\n' | grep -v "^$c$" | head -1); sib="$sib${sib:+,}$s"; done; echo "sibling cpuset of $CS: $sib"
echo "cpufreq of cpuset + siblings: $(for c in $(cpus $CS) ${sib//,/ }; do echo -n "cpu$c $(cat /sys/devices/system/cpu/cpu$c/cpufreq/scaling_governor) $(cat /sys/devices/system/cpu/cpu$c/cpufreq/scaling_min_freq)-$(cat /sys/devices/system/cpu/cpu$c/cpufreq/scaling_max_freq); "; done)"
c0=${CS%%,*}; echo "cpuidle cpu$c0 (name=disable): $(paste -d= <(cat /sys/devices/system/cpu/cpu$c0/cpuidle/state*/name) <(cat /sys/devices/system/cpu/cpu$c0/cpuidle/state*/disable) | tr '\n' ' ')"
echo "## libbpf toolchain"
echo "installed: clang=$(which clang || echo no) llvm-strip=$(which llvm-strip || echo no) bpftool=$(which bpftool || echo no) gcc=$(gcc --version 2>/dev/null | head -1) make=$(which make || echo no)"
echo "packages: $(dpkg -l 2>/dev/null | awk '/^ii/ && ($2 ~ /^(libbpf|clang|llvm|bpftool|libelf-dev|zlib1g-dev|linux-headers-)/) {print $2"="$3}' | tr '\n' ' ')"
echo "apt candidates: $(apt-cache policy clang libbpf-dev bpftool llvm libelf-dev linux-tools-common 2>/dev/null | awk '/^[a-z]/ {p=$1} /Candidate/ {print p $2}' | tr '\n' ' ')"
echo "apt simulated install (clang libbpf-dev): $(timeout 30 apt-get -s install clang libbpf-dev 2>&1 | grep -E '^(Inst|E:)' | head -3 | cut -c1-100 | tr '\n' ';')"
echo "bpftrace: $(bpftrace --version 2>&1); BTF vmlinux: $(ls /sys/kernel/btf/vmlinux 2>/dev/null || echo absent); headers: $(ls -d /usr/src/linux-headers-$KR 2>/dev/null || echo absent); bpf_helpers.h: $(ls /usr/include/bpf/bpf_helpers.h 2>/dev/null || echo absent)"
echo "## load now: $(cat /proc/loadavg); stray processes: $(pgrep -af 'stress-ng|bpftrace|probe.py' | grep -v pgrep | wc -l)"
