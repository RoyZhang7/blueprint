#!/usr/bin/env python3
"""Phase K5 cell controller (runs on the victim's node as root; RESULTS-K 5). One cell = one victim under one neighbor
with a list of arms, run --reps times in order (K1's pairing: each repetition runs every arm once, "none" first).
Per arm: the recorder's parts are set through its control file (GORDION_CTL: "intercept=... reads=..."), the agent
(k5agent) is started with the arm's modules and flags, the scorer (k5score.py) in follow or replay mode; after --settle
seconds the interval is sampled at both ends: /proc/stat per CPU, cpu.stat of the victim, the aggressor and the pod
slices, /proc/softirqs of the victim's CPUs, the agent's own CPU and its BPF programs' run time and count
(bpf_stats), the scorer's CPU, and the byte offsets of the recorder's file (the analysis slices its windows by them).
Writes <out>/cell.jsonl ({"event":"arm", ...} per arm and repetition) and one agent/scorer file per arm.
Neighbors started here, in /sys/fs/cgroup/gordion-aggr: switch (stress-ng --switch, one worker per victim CPU, on the
victim's CPUs), sib (matrixprod on the SMT siblings), cache (stress-ng --cache x N on the given CPUs); "net" is the A5
pods, started by the queue; "none".
usage: k5ctl.py --spec cell.json
"""
import argparse, glob, json, os, signal, subprocess, sys, time

ap = argparse.ArgumentParser(); ap.add_argument("--spec", required=True)
S = json.load(open(ap.parse_args().spec))
B = S.get("bin", "/var/tmp/k5"); AGG = S.get("aggr_cg", "/sys/fs/cgroup/gordion-aggr"); RCPU = str(S.get("reader_cpu", 53))
out = S["out"]; os.makedirs(out, exist_ok=True); log = open(os.path.join(out, "cell.jsonl"), "a")
V = S["victim"]


import threading
_EL = threading.Lock()


def _emit0(o):
    log.write(json.dumps(o, separators=(",", ":")) + "\n"); log.flush()


def emit(o):
    with _EL: _emit0(o)


def sh(cmd):
    return subprocess.run(cmd, shell=True, capture_output=True, text=True).stdout


def cpus(cs):
    r = []
    for p in str(cs).replace("+", ",").split(","):
        if not p: continue
        lo, _, hi = p.partition("-"); r += list(range(int(lo), int(hi or lo) + 1))
    return r


def pod_dir(uid):
    u = uid.replace("-", "_")
    g = glob.glob(f"/sys/fs/cgroup/kubepods.slice/kubepods-pod{u}.slice") + glob.glob(f"/sys/fs/cgroup/kubepods.slice/kubepods-*.slice/kubepods-*-pod{u}.slice")
    return g[0] if g else None


def usage_us(path):
    try:
        for l in open(os.path.join(path, "cpu.stat")):
            if l.startswith("usage_usec"): return int(l.split()[1])
    except Exception:
        return None


def proc_stat():
    d = {}
    for l in open("/proc/stat"):
        if l.startswith("cpu") and l[3] != " ":
            f = l.split(); d[int(f[0][3:])] = [int(x) for x in f[1:9]]   # user nice system idle iowait irq softirq steal
    return d


def softirqs(cpul):
    d = {}
    lines = open("/proc/softirqs").read().split("\n")
    hdr = lines[0].split(); idx = {int(h[3:]): i for i, h in enumerate(hdr)}
    for l in lines[1:]:
        f = l.split()
        if not f: continue
        name = f[0].rstrip(":"); vals = f[1:]
        d[name] = {c: int(vals[idx[c]]) for c in cpul if c in idx and idx[c] < len(vals)}
    return d


def pid_cpu_ns(pid):
    try:
        f = open(f"/proc/{pid}/stat").read().rsplit(")", 1)[1].split()
        return (int(f[11]) + int(f[12])) * (1000000000 // os.sysconf("SC_CLK_TCK"))
    except Exception:
        return None


def pid_rss_kb(pid):
    try: return int(open(f"/proc/{pid}/statm").read().split()[1]) * os.sysconf("SC_PAGE_SIZE") // 1024
    except Exception: return None


def fsize(p):
    try: return os.path.getsize(p)
    except Exception: return None


def set_ctl(line):
    if not V.get("ctl"): return
    with open(V["ctl"], "w") as f: f.write(line + "\n")


def start_bg(cmd, logf):
    return subprocess.Popen(cmd, shell=True, stdout=open(logf, "a"), stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, preexec_fn=os.setsid)


def stop(p, sig=signal.SIGINT, wait=15):
    if p is None: return
    try: os.killpg(p.pid, sig)
    except Exception: pass
    for _ in range(wait * 10):
        if p.poll() is not None: return
        time.sleep(0.1)
    try: os.killpg(p.pid, signal.SIGKILL)
    except Exception: pass


def aggr_start(nb):
    kind = nb.get("kind", "none")
    if kind in ("none", "net"): return None
    os.makedirs(AGG, exist_ok=True)
    vc = cpus(V["cpus"]); n = nb.get("n") or len(vc)
    if kind == "switch": tcpus, args = V["cpus"], f"--switch {n}"
    elif kind == "sib": tcpus, args = nb.get("cpus") or V["sib"], f"--cpu {n} --cpu-method matrixprod"
    elif kind == "cache": tcpus, args = nb["cpus"], f"--cache {n}"
    elif kind == "stream": tcpus, args = nb["cpus"], f"--stream {n}"
    elif kind == "matrix": tcpus, args = nb["cpus"], f"--cpu {n} --cpu-method matrixprod"
    else: sys.exit(f"unknown neighbor {kind}")
    cmd = f"echo $$ > {AGG}/cgroup.procs; exec taskset -c {tcpus} stress-ng {args} --quiet"
    p = subprocess.Popen(["bash", "-c", cmd], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, preexec_fn=os.setsid)
    time.sleep(2)
    return p


def aggr_stop(p):
    if p is None: return
    stop(p, signal.SIGTERM, 10)
    for _ in range(20):
        try:
            if not open(f"{AGG}/cgroup.procs").read().strip(): break
        except Exception:
            break
        sh(f"kill -9 $(cat {AGG}/cgroup.procs) 2>/dev/null"); time.sleep(0.5)


RDT = "/sys/fs/resctrl"
MIT = {"victim": f"{RDT}/k5v", "aggr": f"{RDT}/k5n"}   # step 5: the victim's and the neighbor's ctrl groups


def cg_threads(d, depth=3):
    out = []
    try: out += [int(x) for x in open(f"{d}/cgroup.threads").read().split()]
    except Exception: pass
    if depth > 0:
        for e in os.scandir(d) if os.path.isdir(d) else []:
            if e.is_dir(follow_symlinks=False): out += cg_threads(e.path, depth - 1)
    return out


def l3_dom(cpu):
    try: return int(open(f"/sys/devices/system/cpu/cpu{cpu}/cache/index3/id").read())
    except Exception: return 0


def mitig_setup(m):
    """step 5 (RESULTS-I 6): m = {"cat_ways": N} gives the victim the lowest N ways of its L3 domain and the neighbor the
    rest; {"mba_pct": P} throttles the neighbor's domain to P % (the victim keeps the default). Ctrl groups k5v (victim
    cgroup's threads) and k5n (gordion-aggr's), created before the agent starts so its mon groups go inside them."""
    if not m: return None
    root = open(f"{RDT}/schemata").read().split("\n")
    l3 = next((l for l in root if l.strip().startswith("L3:")), ""); mb = next((l for l in root if l.strip().startswith("MB:")), "")
    doms = [x.split("=") for x in l3.strip()[3:].split(";") if x]; nbits = len(bin(int(doms[0][1], 16))) - 2
    vd = l3_dom(cpus(V["cpus"])[0])
    sch = {"victim": [], "aggr": []}
    if m.get("cat_ways"):
        w = int(m["cat_ways"]); vm = (1 << w) - 1; nm = ((1 << nbits) - 1) & ~vm
        sch["victim"].append("L3:" + ";".join(f"{d}={vm:x}" if int(d) == vd else f"{d}={full}" for d, full in doms))
        sch["aggr"].append("L3:" + ";".join(f"{d}={nm:x}" if int(d) == vd else f"{d}={full}" for d, full in doms))
    if m.get("mba_pct") and mb:
        mdoms = [x.split("=") for x in mb.strip()[3:].split(";") if x]
        sch["aggr"].append("MB:" + ";".join(f"{d}={int(m['mba_pct'])}" if int(d) == vd else f"{d}={v}" for d, v in mdoms))
    out = dict(mitig=m, l3_dom=vd, ways=nbits, groups={})
    for who, path in MIT.items():
        os.makedirs(path, exist_ok=True)
        for line in sch[who]:
            with open(f"{path}/schemata", "w") as f: f.write(line + "\n")
        src = V["cg"] if who == "victim" else AGG
        n = 0
        with open(f"{path}/tasks", "w") as f:
            for tid in cg_threads(src):
                try: f.write(f"{tid}\n"); f.flush(); n += 1
                except Exception: pass
        out["groups"][who] = dict(path=path, tasks=n, schemata=open(f"{path}/schemata").read().strip().replace("\n", " | "))
    emit(dict(event="mitig", t=int(time.time() * 1000), **out))
    return out


def mitig_teardown(mt):
    if not mt: return
    for who, path in MIT.items():
        for mg in os.scandir(f"{path}/mon_groups") if os.path.isdir(f"{path}/mon_groups") else []:
            try: os.rmdir(mg.path)
            except Exception: pass
        try: os.rmdir(path)   # moves its tasks back to the default group
        except Exception as e: emit(dict(event="mitig-teardown-error", who=who, err=str(e)))
    emit(dict(event="mitig-off", t=int(time.time() * 1000), left=[p for p in MIT.values() if os.path.isdir(p)]))


def agent_cmd(cfg, outf, mt=None):
    c = [f"taskset -c {RCPU} {B}/k5agent --obj {B}/k5probe.bpf.o --out {outf} --cpu {RCPU} --tick-ms {cfg.get('tick_ms', 100)}"]
    if mt: c.append(f"--rdt-parent {V['name']}={MIT['victim']},aggr={MIT['aggr']}")
    c.append(f"--cg {V['name']}={V['cg']},victim={V['cpus'].replace(',', '+')}")   # J1: "all" = every online CPU (unpinned victim)
    for h in S.get("holders", []):
        c.append(f"--cg {h['name']}={h['cg']}" + (f",cpus={h['cpus'].replace(',', '+')}" if h.get("cpus") else ""))
    if cfg.get("probe"): c.append(f"--probe {cfg['probe']}")
    for k in ("rq", "rd", "sib", "timer", "perf", "perf_read", "perf_td", "perf_ref", "resctrl", "resctrl_read", "resctrl_cg", "sampler", "rescan_s", "cgf", "hold", "clock_cpus"):
        if k in cfg: c.append(f"--{k.replace('_', '-')} {cfg[k]}")
    if cfg.get("mhz_cpus"):   # Phase I: the victim CPUs' clock from APERF/MPERF in the agent instead of the recorder's read
        c.append(f"--mhz-cpus {(V['cpus'] if cfg['mhz_cpus'] == 'victim' else cfg['mhz_cpus']).replace(',', '+')}")
    return " ".join(c)


class Escalator(threading.Thread):
    """Phase I step 6 (RESULTS-I 7): the escalation arm. Tails the on-node scorer's output; while a window is flagged
    (E >= e_far or I_g > 1 + i_far) a second agent with the diagnostic programs (arm["escalate"]["agent"]) runs beside the
    production agent, started at the first flagged window and stopped hold_s seconds after the last one. Each escalation
    writes its own agent file (<tag>-agentdiag-NN.jsonl); "esc" events mark every start and stop in cell.jsonl."""
    def __init__(self, cfg, sout, out, tag, mt):
        super().__init__(daemon=True)
        self.cfg = cfg; self.sout = sout; self.out = out; self.tag = tag; self.mt = mt
        self.on = cfg.get("on") or {}; self.hold = float(cfg.get("hold_s", 5)); self.stop_evt = threading.Event()
        self.proc = None; self.t_on = None; self.last_flag = None; self.count = 0; self.on_ms = 0.0; self.n_win = 0; self.n_flag = 0; self.files = []

    def flagged(self, w):   # step 8: on E only, against the window's own threshold (the sweep's E_thr at its rho, "thr" in the scorer's record)
        E = w.get("E"); thr = w.get("thr", self.on.get("e_far", 0.15))
        return E is not None and thr is not None and E >= float(thr)

    def _start(self, w):
        self.count += 1; f = os.path.join(self.out, f"{self.tag}-agentdiag-{self.count:02d}.jsonl"); self.files.append(f)
        self.proc = start_bg(agent_cmd(self.cfg["agent"], f, self.mt), f[:-6] + ".log"); self.t_on = time.time()
        emit(dict(event="esc", state="on", t=int(self.t_on * 1000), arm=self.tag, n=self.count, file=f, trigger=dict(t=w.get("t"), E=w.get("E"), Ig=w.get("Ig"), row=w.get("row"))))

    def _stop_diag(self, why):
        stop(self.proc); now = time.time(); self.on_ms += (now - self.t_on) * 1000.0
        emit(dict(event="esc", state="off", t=int(now * 1000), arm=self.tag, n=self.count, why=why, on_s=round(now - self.t_on, 1))); self.proc = None; self.t_on = None

    def _expired(self):
        return self.proc is not None and self.last_flag is not None and time.time() - self.last_flag > self.hold

    def run(self):
        f = None; buf = ""
        while not self.stop_evt.is_set():
            if f is None:
                if not os.path.exists(self.sout): time.sleep(0.2); continue
                f = open(self.sout)
            l = f.readline()
            if not l:
                if self._expired(): self._stop_diag("hold")
                time.sleep(0.1); continue
            if not l.endswith(chr(10)): buf += l; continue
            l = buf + l; buf = ""
            if '"row"' not in l: continue
            try: w = json.loads(l)
            except Exception: continue
            self.n_win += 1
            if self.flagged(w):
                self.n_flag += 1; self.last_flag = time.time()
                if self.proc is None and self.proc_allowed(): self._start(w)
            elif self._expired(): self._stop_diag("hold")
        if self.proc is not None: self._stop_diag("arm end")

    def proc_allowed(self):
        return not self.stop_evt.is_set()

    def finish(self):
        self.stop_evt.set(); self.join(timeout=30)

    def summary(self, t0=None, t1=None):
        return dict(count=self.count, on_ms=round(self.on_ms, 1), windows=self.n_win, flagged=self.n_flag, hold_s=self.hold, on=self.on, files=self.files,
                    agent_self=[agent_self(x, t0, t1) for x in self.files] if (t0 and t1) else None)



def victim_k():
    """step 8: the number of CPUs the victim's threads may run on (Cpus_allowed_list of its first thread), else None.
    J1: an unpinned victim's k is its CFS limit, cpu.max quota / period (rounded up), when the cgroup has one."""
    best = None
    try:
        q, p = open(os.path.join(V["cg"], "cpu.max")).read().split()
        if q != "max" and int(p) > 0: return max(1, -(-int(q) // int(p)))
    except Exception: pass
    try:
        for tid in cg_threads(V["cg"]):          # the pause process and unpinned helpers allow every CPU: take the smallest set
            for l in open(f"/proc/{tid}/status"):
                if l.startswith("Cpus_allowed_list:"):
                    n = 0
                    for p in l.split(":", 1)[1].strip().split(","):
                        lo, _, hi = p.partition("-"); n += int(hi or lo) - int(lo) + 1
                    if n and (best is None or n < best): best = n
    except Exception: pass
    return best


def scorer_cmd(arm, agent_out, outf):
    c = [f"taskset -c {RCPU} python3 {B}/k5score.py --dict {V['dict']} --out {outf} --k {V.get('k', 1)} --slots {V.get('slots', 1)} --victim {V['name']}"]
    if V.get("go"): c.append("--go")
    kk = victim_k()
    if kk: c.append(f"--k {kk}")   # step 8: k from the pod's cpuset = its threads' Cpus_allowed_list (the pods are pinned by affinity, not by a cgroup cpuset)
    if S.get("sirq_base") is not None: c.append(f"--sirq-base {S['sirq_base']}")      # step 8: the quiet softirq share
    if arm.get("scorer") == "replay":
        r = S["replay"]; c.append(f"--replay --recorder {r['recorder']} --agent {r['agent']}")
    else:
        c.append(f"--follow --recorder {V['recorder']}" + (f" --agent {agent_out}" if agent_out else ""))
    return " ".join(c)


def agent_self(path, t0, t1):
    """first and last "self" records inside [t0, t1] -> deltas"""
    first = last = None
    try:
        for l in open(path):
            if '"self"' not in l: continue
            d = json.loads(l)
            if d["t"] < t0 or d["t"] > t1: continue
            if first is None: first = d
            last = d
    except Exception:
        return None
    if not first or not last or last is first: return None
    progs = {k: [last["progs"][k][0] - first["progs"].get(k, [0, 0])[0], last["progs"][k][1] - first["progs"].get(k, [0, 0])[1]] for k in last["progs"]}
    return dict(dur_ms=last["t"] - first["t"], cpu_ns=last["cpu_ns"] - first["cpu_ns"], progs=progs, rss_kb=last["rss_kb"], map_memlock=last["map_memlock"],
                seq=last["seq"] - first["seq"], lost=last["lost"] - first["lost"], armed=last["armed"])


def scorer_cpu(path, t0, t1):
    n = 0; cpu = 0; rows = {}
    try:
        for l in open(path):
            if '"row"' not in l: continue
            d = json.loads(l)
            if d["t"] < t0 or d["t"] > t1: continue
            n += 1; cpu += d["cpu_ns"]; rows[d["row"]] = rows.get(d["row"], 0) + 1
    except Exception:
        return None
    return dict(windows=n, cpu_ns=cpu, rows=rows)


def schedstat(cpul):
    """per CPU in cpul: [pcount, run_delay ns] from /proc/schedstat (sched_info, kept without kernel.sched_schedstats)"""
    out = {}
    try:
        for l in open("/proc/schedstat"):
            f = l.split()
            if f and f[0].startswith("cpu") and f[0][3:].isdigit() and int(f[0][3:]) in cpul and len(f) >= 10:
                out[f[0][3:]] = [int(f[9]), int(f[8])]
    except Exception:
        pass
    return out


def affinity(pid):
    """Cpus_allowed_list of every thread of pid (Phase I 1d: the agent, its sampler and the scorer off the victim's CPUs)"""
    out = set()
    try:
        for t in os.listdir(f"/proc/{pid}/task"):
            for l in open(f"/proc/{pid}/task/{t}/status"):
                if l.startswith("Cpus_allowed_list:"): out.add(l.split(":", 1)[1].strip())
    except Exception:
        pass
    return sorted(out)


def snapshot(cpul, pids):
    return dict(t=int(time.time() * 1000), stat=proc_stat(), sirq=softirqs(cpul), sst=schedstat(cpul), victim_us=usage_us(V["cg"]), aggr_us=usage_us(AGG),
                kubepods_us=usage_us("/sys/fs/cgroup/kubepods.slice"), system_us=usage_us("/sys/fs/cgroup/system.slice"),
                pids={k: pid_cpu_ns(p) for k, p in pids.items() if p}, rec_bytes=fsize(V.get("recorder", "")))


def delta(a, b, cpul):
    st = {c: [b["stat"][c][i] - a["stat"][c][i] for i in range(8)] for c in b["stat"] if c in a["stat"]}
    si = {k: {c: b["sirq"][k][c] - a["sirq"][k].get(c, 0) for c in b["sirq"][k]} for k in b["sirq"] if k in a["sirq"]}
    return dict(t0=a["t"], t1=b["t"], dur_ms=b["t"] - a["t"], stat=st, sirq=si,
                victim_us=(b["victim_us"] - a["victim_us"]) if a["victim_us"] is not None and b["victim_us"] is not None else None,
                aggr_us=(b["aggr_us"] - a["aggr_us"]) if a["aggr_us"] is not None and b["aggr_us"] is not None else None,
                kubepods_us=(b["kubepods_us"] - a["kubepods_us"]) if a["kubepods_us"] is not None else None,
                system_us=(b["system_us"] - a["system_us"]) if a["system_us"] is not None else None,
                pids_ns={k: (b["pids"][k] - a["pids"][k]) if a["pids"].get(k) is not None and b["pids"].get(k) is not None else None for k in b["pids"]},
                rec_bytes=[a["rec_bytes"], b["rec_bytes"]],
                schedstat={c: [b["sst"][c][i] - a["sst"][c][i] for i in range(2)] for c in b.get("sst", {}) if c in a.get("sst", {})})


# ---------------------------------------------------------------- main
if V.get("pod_uid") and not V.get("cg"):
    V["cg"] = pod_dir(V["pod_uid"]) or sys.exit(f"no cgroup for pod {V['pod_uid']}")
for h in S.get("holders", []):
    if h.get("pod_uid") and not h.get("cg"): h["cg"] = pod_dir(h["pod_uid"]) or ""
S["holders"] = [h for h in S.get("holders", []) if h.get("cg")]
vc = list(range(os.cpu_count())) if V["cpus"] == "all" else cpus(V["cpus"]); sc = cpus(V.get("sib", "")); watch = sorted(set(vc + sc + [int(RCPU)]))   # J1: unpinned victim = every CPU
stats_before = open("/proc/sys/kernel/bpf_stats_enabled").read().strip()
sh("sysctl -q -w kernel.bpf_stats_enabled=1")
emit(dict(event="cell", cell=S["cell"], t=int(time.time() * 1000), victim=V, holders=S["holders"], nb=S.get("nb"), arms=S["arms"], reps=S["reps"],
          measure=S["measure"], settle=S.get("settle", 3), stats_before=stats_before, rates=dict(nproc=os.cpu_count())))
nbp = aggr_start(S.get("nb") or {})
emit(dict(event="neighbor", t=int(time.time() * 1000), kind=(S.get("nb") or {}).get("kind", "none"), procs=sh(f"cat {AGG}/cgroup.procs 2>/dev/null | wc -l").strip()))
try:
    for rep in range(S["reps"]):
        for ai, arm in enumerate(S["arms"]):
            if os.path.exists(os.path.join(out, "STOP")) or os.path.exists("/var/tmp/k5/STOP"): raise KeyboardInterrupt
            name = arm["name"]; tag = f"{name}-r{rep:02d}"
            set_ctl(f"intercept={arm.get('intercept', 'off')} reads={arm.get('reads', 'none')} sample={arm.get('sample', 8)}")   # J1: sample = 1 times every request, 8 the production 1-in-8
            agent = scorer = None; aout = sout = None
            mt = mitig_setup(arm.get("mitig"))
            if arm.get("agent"):
                aout = os.path.join(out, f"{tag}-agent.jsonl")
                agent = start_bg(agent_cmd(arm["agent"], aout, mt), os.path.join(out, f"{tag}-agent.log"))
                time.sleep(1.5)
                if agent.poll() is not None:
                    emit(dict(event="error", arm=name, rep=rep, what="agent exited", log=open(os.path.join(out, f"{tag}-agent.log")).read()[-800:]))
                    agent = None
            if arm.get("scorer") in ("on", "replay"):
                sout = os.path.join(out, f"{tag}-score.jsonl")
                scorer = start_bg(scorer_cmd(arm, aout, sout), os.path.join(out, f"{tag}-score.log"))
            esc = None
            if arm.get("escalate") and sout:
                esc = Escalator(arm["escalate"], sout, out, tag, mt); esc.start()
            time.sleep(max(1.5, S.get("settle", 3)))
            pids = dict(agent=agent.pid if agent else None, scorer=scorer.pid if scorer else None)
            # the real pids: the Popen is the shell; its child is the process we want
            for k in list(pids):
                if pids[k]:
                    ch = sh(f"pgrep -P {pids[k]}").split()
                    if ch: pids[k] = int(ch[0])
            aff = {k: affinity(p) for k, p in pids.items() if p}
            a = snapshot(watch, pids)
            time.sleep(S["measure"])
            b = snapshot(watch, pids)
            rss = {k: pid_rss_kb(p) for k, p in pids.items() if p}
            if esc: esc.finish()
            stop(scorer); stop(agent)
            mitig_teardown(mt)
            d = delta(a, b, watch)
            d.update(event="arm", cell=S["cell"], arm=name, rep=rep, intercept=arm.get("intercept", "off"), reads=arm.get("reads", "none"), agent_cfg=arm.get("agent"), mitig=arm.get("mitig"),
                     scorer=arm.get("scorer", "off"), agent_self=(agent_self(aout, a["t"], b["t"]) if aout else None), scorer_self=(scorer_cpu(sout, a["t"], b["t"]) if sout else None),
                     rss_kb=rss, files=dict(agent=aout, scorer=sout), affinity=aff, esc=(esc.summary(a["t"], b["t"]) if esc else None))
            emit(d)
            set_ctl("intercept=off reads=none")
            time.sleep(S.get("gap", 2))
except KeyboardInterrupt:
    emit(dict(event="stopped", t=int(time.time() * 1000)))
finally:
    aggr_stop(nbp)
    sh(f"sysctl -q -w kernel.bpf_stats_enabled={stats_before}")
    emit(dict(event="end", t=int(time.time() * 1000), leftover_agents=sh("pgrep -c k5agent").strip(), aggr_procs=sh(f"cat {AGG}/cgroup.procs 2>/dev/null | wc -l").strip()))
