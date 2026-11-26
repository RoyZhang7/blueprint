#!/bin/bash
# Probe v4 unit test, runs on the service node (sudo). Victim = a CPU-bound loop in /sys/fs/cgroup/gordion-victim pinned
# to CPU $V; aggressor = a CPU-bound loop in /sys/fs/cgroup/gordion-aggr, first on the victim's SMT sibling $S (20 s), then
# on the victim's own CPU (15 s). Expected per 100 ms window: phase 1 Oaggr ~ 0 and Saggr ~ 100 ms (phi 0, phi_sibling 1);
# phase 2 Oaggr ~ 50 ms, Saggr 0, R_foreign ~ 50 ms (the v2/v3 unit test). Leaves nothing running; removes gordion-victim.
# usage: probe-unit-v4.sh [victim-cpu=1] [sibling-cpu=29] [probe-cpu=53]
set -u
V=${1:-1}; S=${2:-29}; PC=${3:-53}; P=$(cd "$(dirname "$0")" && pwd); OUT=/var/tmp/gordion/unit-v4-probe.jsonl
sudo mkdir -p /sys/fs/cgroup/gordion-victim /sys/fs/cgroup/gordion-aggr /var/tmp/gordion
sudo bash -c 'echo $$ > /sys/fs/cgroup/gordion-victim/cgroup.procs; exec taskset -c '$V' timeout 42 sh -c "while :; do :; done"' > /dev/null 2>&1 &
sleep 1
sudo nohup setsid taskset -c $PC python3 $P/probe.py --cpuset $V --sibling-cpuset $S --svc-cgroup /sys/fs/cgroup/gordion-victim --aggr-cgroup /sys/fs/cgroup/gordion-aggr --out $OUT > $OUT.log 2>&1 < /dev/null &
sleep 4
echo "phase 1: aggressor on the sibling CPU $S (20 s)"
sudo bash -c 'echo $$ > /sys/fs/cgroup/gordion-aggr/cgroup.procs; exec taskset -c '$S' timeout 20 sh -c "while :; do :; done"' > /dev/null 2>&1 &
sleep 21
echo "phase 2: aggressor on the victim CPU $V (15 s)"
sudo bash -c 'echo $$ > /sys/fs/cgroup/gordion-aggr/cgroup.procs; exec taskset -c '$V' timeout 15 sh -c "while :; do :; done"' > /dev/null 2>&1 &
sleep 16
sudo pkill -INT -f "probe.py --cpuset $V --sibling-cpuset $S"; sleep 3
sudo pkill -f "timeout 42 sh -c" 2>/dev/null; sleep 1
tail -1 $OUT.log
python3 - $OUT <<'PY'
import json, sys
R = [json.loads(l) for l in open(sys.argv[1]) if l.strip()]
W = [r for r in R if "Rself" in r]; t0 = W[0]["t"] if W else 0
print(f"{len(W)} windows; start {R[0].get('version')} cpuset {R[0].get('cpuset')} siblings {R[0].get('sibling_cpuset')}")
print(f"{'t (s)':>6s} {'Oself':>6s} {'Oaggr':>6s} {'Oroot':>6s} {'Oidle':>6s} | {'Saggr':>6s} {'Sidle':>6s} | {'Rself':>6s} {'Raggr':>6s} | {'Wown':>5s} {'Widle':>5s}   (ms per 100 ms window, mean over 5 s)")
b = 5000
for k in range(0, (W[-1]["t"] - t0) // b + 1):
    m = [r for r in W if t0 + k * b <= r["t"] < t0 + (k + 1) * b]
    if not m: continue
    f = lambda key: sum(r.get(key, 0) for r in m) / len(m) / 1e6
    print(f"{k * 5:6d} {f('Oself'):6.1f} {f('Oaggr'):6.1f} {f('Oroot'):6.1f} {f('Oidle'):6.1f} | {f('Saggr'):6.1f} {f('Sidle'):6.1f} | {f('Rself'):6.1f} {f('Raggr'):6.1f} | {f('Wf0') + f('Wo0'):5.1f} {f('Wf4') + f('Wo4'):5.1f}")
end = [r for r in R if r.get("event") == "end"]; print("stderr:", (end[0].get("stderr", "")[-200:] if end else "no end record"))
PY
sudo rmdir /sys/fs/cgroup/gordion-victim 2>/dev/null; echo "leftover: $(pgrep -af 'probe.py|bpftrace|while :' | grep -v pgrep | wc -l) processes"
