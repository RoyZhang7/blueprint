#!/usr/bin/env python3
"""Phase K4 sampler (node-4, root). No HC procfs file is read.
Every 0.2 s ("aff" records): for every thread of the Secondary pod (all containers but pause), its Cpus_allowed_list and
its run time (/proc/<tid>/schedstat, ns) with the CPU it last ran on; "lent" = the Primary cores in the union of the
Secondary threads' allowed sets (what HC's Two-Phase Affinity has given it); "sec_p_ns" = the Secondary's run time in
this interval on threads whose last CPU is a Primary core.
Every 1 s ("sec" records): /proc/stat lines of all CPUs (USER_HZ), cpu.stat usage_usec of the Primary and Secondary pod
cgroups, and the run time of HC's Monitor kthread (check_idle_cpus) and Balancer.
K4.1b additions: the start record carries cpu.weight of both pods (pod slice, container scopes, QoS parent slice); every
"sec" record carries the Primary threads' migrations and switches in that second ("pthr": summed per-thread deltas of
se.nr_migrations, nr_involuntary_switches and nr_voluntary_switches from /proc/<tid>/sched) and, when the lean probe runs,
its programs' cumulative bpf_stats ("probe": {prog_id: [run_time_ns, run_cnt]}, from the loader's /proc/<pid>/fdinfo)
and the loader's own CPU ("probe_self_ns").
usage: k4sample.py --out F --secs N --primary-uid UID [--secondary-uid UID] [--pcpus 4,6,8,10]
"""
import argparse, glob, json, os, subprocess, time

ap = argparse.ArgumentParser()
ap.add_argument("--out", required=True); ap.add_argument("--secs", type=float, required=True)
ap.add_argument("--primary-uid", required=True); ap.add_argument("--secondary-uid", default="")
ap.add_argument("--pcpus", default="4,6,8,10")
a = ap.parse_args()
P = {int(c) for c in a.pcpus.split(",")}


def pod_dir(uid):
    if not uid: return None
    u = uid.replace("-", "_")
    g = glob.glob(f"/sys/fs/cgroup/kubepods.slice/kubepods-pod{u}.slice") + glob.glob(f"/sys/fs/cgroup/kubepods.slice/kubepods-*.slice/kubepods-*-pod{u}.slice")
    return g[0] if g else None


def usage(d):
    try:
        for l in open(os.path.join(d, "cpu.stat")):
            if l.startswith("usage_usec"): return int(l.split()[1])
    except Exception: pass
    return None


def cpulist(s):
    out = set()
    for p in s.strip().split(","):
        if not p: continue
        lo, _, hi = p.partition("-"); out.update(range(int(lo), int(hi or lo) + 1))
    return out


def sec_tids(d):
    tids = []
    for f in glob.glob(os.path.join(d, "*.scope", "cgroup.threads")):
        try: ts = [int(x) for x in open(f).read().split()]
        except Exception: continue
        for t in ts:
            try: comm = open(f"/proc/{t}/comm").read().strip()
            except Exception: continue
            if comm != "pause": tids.append(t)
    return tids


def ctr_tids(d):
    """threads of a pod's containers (pause excluded)"""
    return sec_tids(d) if d else []


def weights(d):
    if not d: return None
    w = lambda x: open(os.path.join(x, "cpu.weight")).read().strip() if os.path.exists(os.path.join(x, "cpu.weight")) else None
    par = os.path.dirname(d)
    return dict(pod=w(d), parent=os.path.basename(par), parent_weight=w(par),
                scopes={os.path.basename(x)[:40]: w(x) for x in glob.glob(os.path.join(d, "*.scope"))})


def thr_sched(tid):
    out = {}
    try:
        for l in open(f"/proc/{tid}/sched"):
            k, _, v = l.partition(":")
            k = k.strip()
            if k in ("se.nr_migrations", "nr_involuntary_switches", "nr_voluntary_switches"): out[k] = int(float(v.strip()))
    except Exception: return None
    return out


def probe_stats(pid):
    st = {}
    try:
        for fd in os.listdir(f"/proc/{pid}/fdinfo"):
            try: txt = open(f"/proc/{pid}/fdinfo/{fd}").read()
            except Exception: continue
            if "prog_id:" not in txt or "run_time_ns:" not in txt: continue    # program fds only (a link's fdinfo names its prog_id too)
            kv = dict((a.strip(), b.strip()) for a, _, b in (l.partition(":") for l in txt.splitlines()) if a)
            st[kv["prog_id"]] = [int(kv.get("run_time_ns", 0)), int(kv.get("run_cnt", 0))]
    except Exception: return None
    return st


def task_ns(pid):
    try: return int(open(f"/proc/{pid}/schedstat").read().split()[0])
    except Exception: return None


def pid_of(name):
    try: return int(subprocess.run(["pgrep", "-x", name], capture_output=True, text=True).stdout.split()[0])
    except Exception: return None


pd, sd = pod_dir(a.primary_uid), pod_dir(a.secondary_uid)
out = open(a.out, "w")
out.write(json.dumps(dict(event="start", t=int(time.time() * 1000), primary_dir=pd, secondary_dir=sd, pcpus=sorted(P),
                          primary_weight=weights(pd), secondary_weight=weights(sd))) + "\n")
mon, bal = pid_of("check_idle_cpus"), pid_of("balancer")
prev = {}; t_end = time.time() + a.secs; next_sec = 0.0; tids = []; tids_at = 0.0; ptids = []; pprev = {}; probe_pid = None
while time.time() < t_end:
    now = time.time()
    if now - tids_at > 2.0:
        tids = sec_tids(sd) if sd else []; ptids = ctr_tids(pd); tids_at = now
    if sd:
        union = set(); on_p = 0; tot = 0; masks = {}
        for t in tids:
            try:
                st = open(f"/proc/{t}/status").read()
                al = cpulist(st.split("Cpus_allowed_list:")[1].split("\n")[0])
                f = open(f"/proc/{t}/stat").read().rsplit(")", 1)[1].split()
                cpu = int(f[36]); ns = int(open(f"/proc/{t}/schedstat").read().split()[0])
            except Exception: continue
            union |= al; masks[t] = ",".join(str(c) for c in sorted(al))
            d = ns - prev.get(t, ns); prev[t] = ns; tot += d
            if cpu in P: on_p += d
        out.write(json.dumps(dict(kind="aff", t=int(now * 1000), lent=sorted(union & P), sec_p_ns=on_p, sec_ns=tot, masks=masks), separators=(",", ":")) + "\n")
    if now >= next_sec:
        next_sec = now + 1.0
        if bal is None: bal = pid_of("balancer")
        stat = {}
        for l in open("/proc/stat"):
            if l.startswith("cpu") and l[3] != " ":
                f = l.split(); stat[f[0][3:]] = [int(x) for x in f[1:9]]
        pt = dict(mig=0, inv=0, vol=0, n=0)
        for t in ptids:
            c = thr_sched(t)
            if not c: continue
            o = pprev.get(t); pprev[t] = c; pt["n"] += 1
            if o:
                pt["mig"] += c.get("se.nr_migrations", 0) - o.get("se.nr_migrations", 0)
                pt["inv"] += c.get("nr_involuntary_switches", 0) - o.get("nr_involuntary_switches", 0)
                pt["vol"] += c.get("nr_voluntary_switches", 0) - o.get("nr_voluntary_switches", 0)
        if probe_pid is None or not os.path.exists(f"/proc/{probe_pid}"): probe_pid = pid_of("k1lean")
        out.write(json.dumps(dict(kind="sec", t=int(now * 1000), stat=stat, pcg=usage(pd) if pd else None, scg=usage(sd) if sd else None,
                                  mon_ns=task_ns(mon) if mon else None, bal_ns=task_ns(bal) if bal else None, pthr=pt,
                                  probe=probe_stats(probe_pid) if probe_pid else None, probe_self_ns=task_ns(probe_pid) if probe_pid else None),
                             separators=(",", ":")) + "\n")
        out.flush()
    time.sleep(max(0.0, 0.2 - (time.time() - now)))
out.write(json.dumps(dict(event="end", t=int(time.time() * 1000))) + "\n"); out.close()
