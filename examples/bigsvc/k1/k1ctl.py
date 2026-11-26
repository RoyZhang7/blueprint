#!/usr/bin/env python3
"""Phase K1 cell controller (runs on the service node as root). For one cell: starts the cell's aggressor (own cgroup),
measures the event rates once without any probe (perf stat, per CPU), then runs the arms interleaved ABAB for --reps
rounds: start the arm's loader, wait until its BPF programs are attached (new ids in `bpftool prog show`), settle, sample,
measure --measure seconds, sample, stop. Rates are measured again at the end. Writes <out>/cell.jsonl:
  {"event":"rates", ...}                                   per-CPU context switches and wake-ups (perf stat -A), start/end
  {"event":"arm", arm, rep, t0, t1, dur_s, progs:[{id,name,type,run_cnt,run_time_ns}], stat:{cpu: {user,..,softirq}},
   cg:{victim,aggr,root,children}, reader_cpu_ns, stats_enabled}  -- all as deltas over the measured interval
Arms: none | bt:<variant>[:nostats] (probe.py --tmpl <progs>/<variant>.bt.tmpl) | lb:full (the libbpf probe).
bpf_stats are enabled for the whole cell (kernel.bpf_stats_enabled=1) except during :nostats arms; restored to the
value found at the end. Aggressor methods: matrixprod | duty<N> (--cpu-load N --cpu-load-slice 10) | switch.

usage: k1ctl.py --spec cell.json   (keys: cell, svc_frag, cpuset, sibling, arms, reps, measure, settle, out, aggr:{method,cpus,n}|null)
"""
import argparse, json, os, signal, subprocess, sys, time

P = "/var/tmp/gordion/probe"; PROGS = "/var/tmp/gordion/k1/progs"; BPFTOOL = "/var/tmp/k1build/bpftool"; LB = "/var/tmp/k1build/src"
AGG = "/sys/fs/cgroup/gordion-aggr"; PROBE_CPU = os.environ.get("PROBE_CPU", "53")

ap = argparse.ArgumentParser(); ap.add_argument("--spec", required=True)
S = json.load(open(ap.parse_args().spec))
out = S["out"]; os.makedirs(out, exist_ok=True); log = open(os.path.join(out, "cell.jsonl"), "a")


def emit(o):
    log.write(json.dumps(o, separators=(",", ":")) + "\n"); log.flush()


def sh(cmd, **kw):
    return subprocess.run(cmd, shell=True, capture_output=True, text=True, **kw).stdout


def cpus(cs):
    r = []
    for p in cs.split(","):
        if not p: continue
        lo, _, hi = p.partition("-"); r += list(range(int(lo), int(hi or lo) + 1))
    return r


def victim_cgroup(frag):
    for _ in range(60):
        cid = sh(f"docker ps -q --no-trunc --filter name={frag} | head -1").strip()
        if cid:
            cg = sh(f"find /sys/fs/cgroup/kubepods.slice -maxdepth 3 -type d -name '*{cid[:12]}*' | head -1").strip()
            if cg: return cg
        time.sleep(1)
    sys.exit(f"no container/cgroup for {frag}")


def cg_usage(path):
    try:
        for l in open(os.path.join(path, "cpu.stat")):
            if l.startswith("usage_usec"): return int(l.split()[1])
    except Exception:
        return None


def proc_stat():
    d = {}
    for l in open("/proc/stat"):
        if l.startswith("cpu") and l[3] != " ":
            f = l.split(); d[int(f[0][3:])] = [int(x) for x in f[1:9]]   # user nice system idle iowait irq softirq steal (USER_HZ)
    return d


def prog_ids():
    try: return {p["id"]: p for p in json.loads(sh(f"{BPFTOOL} prog show -j") or "[]")}
    except Exception: return {}


def tree(pid):
    pids = [pid]; i = 0
    while i < len(pids):
        pids += [int(x) for x in sh(f"pgrep -P {pids[i]}").split()]; i += 1
    return pids


def tree_cpu_ns(pids):
    tot = 0; hz = os.sysconf("SC_CLK_TCK")
    for p in pids:
        try:
            f = open(f"/proc/{p}/stat").read().rsplit(")", 1)[1].split(); tot += (int(f[11]) + int(f[12])) * 1e9 / hz
        except Exception: pass
    return tot


def snap(new_ids, vcg, pids):
    children = [os.path.join("/sys/fs/cgroup", d) for d in os.listdir("/sys/fs/cgroup") if os.path.isfile(os.path.join("/sys/fs/cgroup", d, "cpu.stat"))]
    allp = prog_ids()
    return dict(t=time.time(), stat=proc_stat(), cg=dict(victim=cg_usage(vcg), aggr=cg_usage(AGG) if os.path.isdir(AGG) else 0, root=cg_usage("/sys/fs/cgroup"),
                children=sum(cg_usage(c) or 0 for c in children)),
                progs={i: dict(name=allp[i].get("name", ""), type=allp[i].get("type", ""), run_cnt=allp[i].get("run_cnt", 0), run_time_ns=allp[i].get("run_time_ns", 0)) for i in new_ids if i in allp},
                reader=tree_cpu_ns(pids))


def delta(a, b):
    st = {c: [y - x for x, y in zip(a["stat"][c], b["stat"][c])] for c in b["stat"] if c in a["stat"]}
    cg = {k: (b["cg"][k] - a["cg"][k]) if (a["cg"][k] is not None and b["cg"][k] is not None) else None for k in b["cg"]}
    pr = [dict(id=i, name=v["name"], type=v["type"], run_cnt=v["run_cnt"] - a["progs"].get(i, {}).get("run_cnt", 0), run_time_ns=v["run_time_ns"] - a["progs"].get(i, {}).get("run_time_ns", 0)) for i, v in b["progs"].items()]
    return dict(dur_s=b["t"] - a["t"], stat=st, cg=cg, progs=pr, reader_cpu_ns=b["reader"] - a["reader"])


def rates(tag, cs, dur=15):
    """event rates without the probe: a separate 15 s bpftrace counting run (perf on this node has no tracepoint support):
    context switches per CPU, wake-ups per CPU where they execute and per target CPU."""
    prog = ("tracepoint:sched:sched_switch { @cs[cpu] = count(); }\n"
            "tracepoint:sched:sched_wakeup, tracepoint:sched:sched_wakeup_new { @wk[cpu] = count(); @wt[args->target_cpu] = count(); }\n"
            f"interval:s:{dur} {{ print(@cs); print(@wk); print(@wt); exit(); }}\n")
    f = os.path.join(out, "rates.bt"); open(f, "w").write(prog)
    o = sh(f"taskset -c {PROBE_CPU} bpftrace -f json -q {f}")
    maps = {}
    for l in o.splitlines():
        try: j = json.loads(l)
        except Exception: continue
        if j.get("type") == "map": maps.update({k: {int(c): v for c, v in d.items()} for k, d in j["data"].items()})
    cset = set(cpus(cs)); g = lambda m, sel: sum(v for c, v in maps.get(m, {}).items() if sel(c)) / dur
    emit(dict(event="rates", tag=tag, t=time.time(), dur_s=dur, cs_cpuset=g("@cs", lambda c: c in cset), cs_all=g("@cs", lambda c: True),
              wake_on_cpuset=g("@wk", lambda c: c in cset), wake_to_cpuset=g("@wt", lambda c: c in cset), wake_all=g("@wk", lambda c: True),
              per_cpu={k: v for k, v in maps.items()}))


# K1 addendum arms: lb:<variant> -> (object, programs)
LEAN_ARMS = {"lean": ("k1lean.bpf.o", "switch"), "lean_wake": ("k1lean.bpf.o", "switch,wakeup"),
             "empty_switch": ("k1empty.bpf.o", "switch"), "empty_both": ("k1empty.bpf.o", "switch,wakeup")}


def start_arm(arm, vcg):
    parts = arm.split(":")
    if parts[0] == "none": return None
    f = os.path.join(out, f"{arm.replace(':', '_')}-{int(time.time())}")
    if parts[0] == "bt":
        cmd = ["taskset", "-c", PROBE_CPU, "python3", f"{P}/probe.py", "--tmpl", f"{PROGS}/{parts[1]}.bt.tmpl", "--cpuset", S["cpuset"], "--svc-cgroup", vcg, "--aggr-cgroup", AGG, "--out", f + "-probe.jsonl"]
        if S.get("sibling"): cmd[cmd.index("--svc-cgroup"):cmd.index("--svc-cgroup")] = ["--sibling-cpuset", S["sibling"]]
    elif len(parts) > 1 and parts[1] in LEAN_ARMS:            # K1 addendum: lean / empty libbpf (k1lean loader)
        obj, progs = LEAN_ARMS[parts[1]]
        cmd = ["taskset", "-c", PROBE_CPU, f"{LB}/k1lean", f"{LB}/{obj}", "--progs", progs, "--cpuset", S["cpuset"], "--svc-cgroup", vcg, "--aggr-cgroup", AGG, "--out", f + "-probe.jsonl"]
    else:
        cmd = ["taskset", "-c", PROBE_CPU, f"{LB}/k1probe", f"{LB}/k1probe.bpf.o", "--cpuset", S["cpuset"], "--sibling", S.get("sibling") or "", "--svc-cgroup", vcg, "--aggr-cgroup", AGG, "--out", f + "-probe.jsonl"]
    return subprocess.Popen(cmd, stdout=open(f + ".log", "w"), stderr=subprocess.STDOUT, start_new_session=True)


def stop_arm(p):
    if p is None: return
    try: os.killpg(p.pid, signal.SIGINT)
    except Exception: pass
    try: p.wait(timeout=20)
    except Exception:
        try: os.killpg(p.pid, signal.SIGKILL)
        except Exception: pass
    for _ in range(20):                                      # bpftrace exits after its wrapper; wait for the programs to go
        if not sh("pgrep -x bpftrace").strip() and not sh("pgrep -f k1probe.bpf.o").strip(): break
        time.sleep(0.5)


def aggr_start(a):
    if not a: return None
    os.makedirs(AGG, exist_ok=True)
    m = a["method"]; n = a.get("n", 1)
    if m == "switch": args = f"--switch {n}"
    elif m.startswith("duty"): args = f"--cpu {n} --cpu-method matrixprod --cpu-load {m[4:]} --cpu-load-slice 10"
    else: args = f"--cpu {n} --cpu-method {m}"
    p = subprocess.Popen(["sh", "-c", f"echo $$ > {AGG}/cgroup.procs; exec taskset -c {a['cpus']} stress-ng {args} -t 7200"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
    time.sleep(2); return p


def aggr_stop(p):
    if p is None: return
    try: os.killpg(p.pid, signal.SIGTERM)
    except Exception: pass
    sh("pkill -x stress-ng"); time.sleep(1)


stats0 = open("/proc/sys/kernel/bpf_stats_enabled").read().strip()
vcg = victim_cgroup(S["svc_frag"])
emit(dict(event="start", t=time.time(), spec=S, victim_cgroup=vcg, bpf_stats_found=stats0, freq=sh("cat /sys/devices/system/cpu/cpu1/cpufreq/scaling_min_freq /sys/devices/system/cpu/cpu1/cpufreq/scaling_max_freq").split()))
ag = aggr_start(S.get("aggr"))
try:
    rates("start", S["cpuset"])
    for rep in range(S["reps"]):
        for arm in S["arms"]:
            nostats = arm.endswith(":nostats"); base = arm[:-8] if nostats else arm
            open("/proc/sys/kernel/bpf_stats_enabled", "w").write("0" if nostats else "1")
            before = set(prog_ids()); p = start_arm(base, vcg); new = set()
            if p is not None:
                t_start = time.time(); stable = 0; last = -1
                while time.time() - t_start < 40:
                    time.sleep(0.5); new = set(prog_ids()) - before
                    if new and len(new) == last: stable += 1
                    else: stable = 0
                    last = len(new)
                    if stable >= 4 or p.poll() is not None: break
                attach_s = time.time() - t_start
            else: attach_s = 0.0
            time.sleep(S.get("settle", 3))
            pids = tree(p.pid) if p is not None else []
            a = snap(new, vcg, pids); time.sleep(S["measure"]); b = snap(new, vcg, pids)
            d = delta(a, b); alive = p is None or p.poll() is None; stop_arm(p)
            emit(dict(event="arm", cell=S["cell"], arm=arm, rep=rep, t0=a["t"], t1=b["t"], attach_s=attach_s, n_progs=len(new), alive=alive, stats_enabled=not nostats, **d))
            time.sleep(S.get("gap", 2))
    rates("end", S["cpuset"])
finally:
    aggr_stop(ag)
    open("/proc/sys/kernel/bpf_stats_enabled", "w").write(stats0)
    sh("pkill -INT -f /var/tmp/gordion/probe/probe.py; pkill -INT -f k1probe.bpf.o"); time.sleep(2); sh("pkill -x bpftrace")
    emit(dict(event="end", t=time.time()))
