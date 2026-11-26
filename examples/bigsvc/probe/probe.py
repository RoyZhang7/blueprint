#!/usr/bin/env python3
"""Gordion Phase G probe wrapper (runs on the service node as root): generates the bpftrace program from the template,
runs it, and writes one JSON line per 100 ms window keyed by unix ms (the recorder's t clock) with the run-queue
delay per holder class, the sleep split, and the pod cgroup's cpu.stat (usage_usec, nr_periods, nr_throttled,
throttled_usec) read at the same instant; the per-second log2 histogram of runnable gaps and the per-thread R go to a
second file. v4 (Addendum E.3): --sibling-cpuset adds the occupancy of the cpuset's SMT siblings by holder class
(S* fields) and the waker split of the service threads' sleeps (Wf*/Wo* fields). v5 (Addendum F): the window record
arrives as two printf halves ("q" and "q2", merged here by their sequence number); the per-resume records ("r") and the
per-wake-up softirq records ("k") go to <out>-resume.jsonl with the unix-ms t added (one line each; the volume is a
few thousand per second on a Thrift server).

usage: probe.py --cpuset 1,3,5,7 --svc-cgroup /sys/fs/cgroup/kubepods.slice/.../docker-<id>.scope
                [--aggr-cgroup /sys/fs/cgroup/gordion-aggr] [--sibling-cpuset 29,31,33,35] --out /var/tmp/gordion/RUN-probe.jsonl
Stop with SIGINT/SIGTERM; the wrapper ends bpftrace and flushes. Its own CPU (bpftrace + this process) is in the
'self' record every second (utime+stime from /proc, for the overhead claim C6).
"""
import argparse, json, os, re, signal, subprocess, sys, time

VERSION = "v5"
ap = argparse.ArgumentParser()
ap.add_argument("--cpuset", required=True); ap.add_argument("--svc-cgroup", required=True); ap.add_argument("--aggr-cgroup", default="")
ap.add_argument("--sibling-cpuset", default="", help="v4: the SMT siblings of --cpuset; their occupancy by holder class is exported as S* (phi_sibling)")
ap.add_argument("--out", required=True); ap.add_argument("--bpftrace", default="bpftrace")
ap.add_argument("--tmpl", default=os.path.join(os.path.dirname(os.path.abspath(__file__)), "gordion_sched.bt.tmpl"))
a = ap.parse_args()


def cpus(cs):
    out = []
    for p in cs.split(","):
        if not p: continue
        lo, _, hi = p.partition("-"); out += list(range(int(lo), int(hi or lo) + 1))
    return out


def cgid(path):
    return os.stat(path).st_ino          # cgroup v2: kernfs inode number == kn->id == the bpf cgroup id


svc_id = cgid(a.svc_cgroup); aggr_id = cgid(a.aggr_cgroup) if a.aggr_cgroup and os.path.isdir(a.aggr_cgroup) else (1 << 64) - 1   # no aggressor: an id no cgroup has (0 means "unknown" in the program)
root_id = cgid("/sys/fs/cgroup")
sib = cpus(a.sibling_cpuset)
prog = (open(a.tmpl).read().replace("__CPUFILTER__", " || ".join(f"cpu == {c}" for c in cpus(a.cpuset)))
        .replace("__SIBFILTER__", " || ".join(f"cpu == {c}" for c in sib) if sib else "cpu == -1")
        .replace("__SVCID__", str(svc_id)).replace("__AGGRID__", str(aggr_id)).replace("__ROOTID__", str(root_id)))
bt_path = a.out + ".bt"; open(bt_path, "w").write(prog)
offset_ns = time.time_ns() - time.monotonic_ns()      # bpftrace nsecs is CLOCK_MONOTONIC
base = a.out.replace(".jsonl", "")
hist_out = open(base + "-hist.jsonl", "w"); res_out = open(base + "-resume.jsonl", "w")
out = open(a.out, "w")
meta = dict(version=VERSION, cpuset=a.cpuset, sibling_cpuset=a.sibling_cpuset, svc_cgroup=a.svc_cgroup, svc_id=svc_id, aggr_cgroup=a.aggr_cgroup, aggr_id=aggr_id, root_id=root_id, offset_ns=offset_ns, started=time.time())
out.write(json.dumps(dict(event="start", **meta)) + "\n"); out.flush()
res_out.write(json.dumps(dict(event="start", **meta)) + "\n")
env = dict(os.environ); env.setdefault("BPFTRACE_MAP_KEYS_MAX", "1048576")   # v3.1: thread-per-connection servers exceed the 4096 default
p = subprocess.Popen([a.bpftrace, "-f", "json", "-q", bt_path], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, bufsize=1, env=env)
stop = False


def on_sig(*_):
    global stop
    stop = True
    try: p.send_signal(signal.SIGINT)
    except Exception: pass


signal.signal(signal.SIGINT, on_sig); signal.signal(signal.SIGTERM, on_sig)


def cpu_stat():
    try:
        d = {}
        for line in open(os.path.join(a.svc_cgroup, "cpu.stat")):
            k, _, v = line.partition(" ")
            if k in ("usage_usec", "user_usec", "system_usec", "nr_periods", "nr_throttled", "throttled_usec"): d[k] = int(v)
        return d
    except Exception:
        return {}


def self_cpu():
    tot = 0
    for pid in (os.getpid(), p.pid):
        try:
            f = open(f"/proc/{pid}/stat").read().rsplit(")", 1)[1].split()
            tot += (int(f[11]) + int(f[12])) * (1e9 / os.sysconf("SC_CLK_TCK"))
        except Exception:
            pass
    return int(tot)


last_self = time.time(); n = 0; nres = 0; pending = {}
for line in p.stdout:
    line = line.strip()
    if not line: continue
    try: o = json.loads(line)
    except Exception: continue
    typ = o.get("type")
    if typ == "printf":
        data = o["data"]
        if data.startswith('{"r"') or data.startswith('{"k"'):
            # v5 per-resume / per-wake-up record: add t, keep the rest as emitted
            try:
                d = data.strip(); i = d.index('"ns":') + 5; j = d.index(",", i); ns = int(d[i:j])
                res_out.write(d[:-1] + ',"t":%d}\n' % int((ns + offset_ns) / 1e6)); nres += 1
            except Exception:
                pass
            continue
        try: rec = json.loads(data)
        except Exception: continue
        if "begin_ns" in rec: continue
        if "q2" in rec:                      # second half of a window record
            q = rec.pop("q2"); first = pending.pop(q, None)
            if first is None: continue
            first.update(rec); rec = first
        elif "q" in rec:                     # first half: wait for the second
            pending[rec["q"]] = rec; continue
        rec.pop("q", None)
        rec["t"] = int((rec.pop("ns") + offset_ns) / 1e6)
        rec.update(cpu_stat())
        out.write(json.dumps(rec, separators=(",", ":")) + "\n"); n += 1
        if n % 10 == 0: out.flush()
    elif typ in ("hist", "map"):
        hist_out.write(json.dumps(dict(t=int(time.time() * 1000), **{k: v for k, v in o.items() if k != "type"}, kind=typ), separators=(",", ":")) + "\n")
    if time.time() - last_self >= 1.0:
        out.write(json.dumps(dict(event="self", t=int(time.time() * 1000), cpu_ns=self_cpu(), resumes=nres), separators=(",", ":")) + "\n"); last_self = time.time()
    if stop: break
try:
    p.wait(timeout=10)
except Exception:
    p.kill()
err = p.stderr.read()
out.write(json.dumps(dict(event="end", t=int(time.time() * 1000), windows=n, resumes=nres, stderr=err[-2000:])) + "\n"); out.close(); hist_out.close(); res_out.close()
print(f"probe: {n} windows, {nres} resume/wake-up records -> {a.out}; stderr tail: {err[-300:]!r}", file=sys.stderr)
