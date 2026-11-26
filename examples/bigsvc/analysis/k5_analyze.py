#!/usr/bin/env python3
"""Phase K5 (the overhead ladder) and K6 (signatures) analysis (RESULTS-K 5 and 6). Input: results/k5 (and results/k6)
as the queue leaves them on node-0: cells.txt, <cell>/ctl/cell.jsonl (one "arm" record per arm and repetition),
<cell>/ctl/<arm>-r<rep>-agent.jsonl and -score.jsonl, <cell>/ugen.jsonl or wlgen.jsonl (the generator's per-second
records), recorders/ (the two recorder streams, sliced per arm by the byte offsets in the arm record).
Per arm: the two frames of K5 (per request: victim cgroup CPU per client completion, server p50/p99 from the recorder's
windows where the interceptor timed them, client p99; per tick: agent CPU, BPF program time per tick, scorer CPU per
window, memory). Rung 1: paired differences full - none over the repetitions (mean, 95 % t interval; "~" = not
distinguishable). Rungs 2-4: each arm against the (mid, switch) twins of its service (rung 0 and the repeats), the
twins' spread as the resolution. Reduction: component x {CPU per request, p99 ratio, ms per tick}; the rung-2 sum against
rung 1. Gates. K6: per run the medians of the new columns against its twin, expected vs measured.
usage: k5_analyze.py results/k5 [--k6 results/k6] [--out results/k5]
"""
import argparse, csv, json, math, os, sys
from collections import defaultdict

try:
    import numpy as np
except ImportError:
    np = None

HZ = 100.0
RUNG_OF = {"b": 5, "bp": 5, "k1": 5, "dc": 5, "bq": 5, "twin": 0, "r0": 0, "r0b": 0, "r0c": 0, "r1": 1, "r2": 2, "r3": 3, "r4": 4, "sweep": -1, "c1": 6, "c2": 6, "c3": 6, "c4": 6, "c5": 6, "c6": 6}


def jl(p):
    if not os.path.exists(p): return []
    out = []
    for l in open(p):
        try: out.append(json.loads(l))
        except Exception: pass
    return out


def pct(xs, q):
    xs = sorted(x for x in xs if x is not None)
    if not xs: return None
    k = (len(xs) - 1) * q; f = int(k); c = min(f + 1, len(xs) - 1)
    return xs[f] + (xs[c] - xs[f]) * (k - f)


def med(xs): return pct(xs, 0.5)


def ci(xs):
    """mean and 95 % t interval (K1's rule): n = 2..5 use the t table"""
    xs = [x for x in xs if x is not None and not (isinstance(x, float) and math.isnan(x))]
    n = len(xs)
    if n == 0: return None, None, None, 0
    m = sum(xs) / n
    if n == 1: return m, None, None, 1
    sd = math.sqrt(sum((x - m) ** 2 for x in xs) / (n - 1)); t = {2: 12.71, 3: 4.30, 4: 3.18, 5: 2.78, 6: 2.57, 7: 2.45, 8: 2.36, 9: 2.31, 10: 2.26}.get(n, 2.0)
    h = t * sd / math.sqrt(n)
    return m, m - h, m + h, n


def cells_of(root):
    out = []
    p = os.path.join(root, "cells.txt")
    if not os.path.exists(p): return out
    sp = os.path.join(root, "superseded.txt")   # cells rerun later (first word of each line: the cell id; the rest says why)
    skip = {l.split()[0] for l in open(sp) if l.strip() and not l.startswith("#")} if os.path.exists(sp) else set()
    for l in open(p):
        f = l.split()
        if len(f) < 8 or f[0] in skip: continue
        cell, name, svc, sched, nb, arms, reps, meas = f[:8]
        pre = name.split("-")[0]
        out.append(dict(cell=cell, name=name, svc=svc, sched=sched, nb=nb, arms=arms.split(","), reps=int(reps), measure=int(meas), rung=RUNG_OF.get(pre, 9), dir=os.path.join(root, cell)))
    return out


def generator(c):
    """per-second client records -> list of (t_ms, done, p99_ms)"""
    d = c["dir"]
    if c["svc"] == "uid":
        return [(r["t"], r["done"], r["lat_p99_us"] / 1000.0) for r in jl(os.path.join(d, "ugen.jsonl")) if "done" in r and "event" not in r]
    return [(r["t"], r["all"][0], r["all"][3] / 1e6) for r in jl(os.path.join(d, "wlgen.jsonl")) if "all" in r and r.get("rate", 0) > 0]


def recorder_windows(root, c, a):
    """the recorder's windows of this arm, by the byte offsets the controller recorded"""
    b0, b1 = a.get("rec_bytes") or [None, None]
    if b0 is None or b1 is None or b1 <= b0: return []
    rd = os.path.join(root, "recorders")
    files = [f for f in os.listdir(rd)] if os.path.isdir(rd) else []
    path = None
    sp = os.path.join(root, "specs", c["cell"] + ".json")   # the recorder this cell's controller read (the redo added a second rec run)
    if os.path.exists(sp):
        base = os.path.basename((json.load(open(sp)).get("victim") or {}).get("recorder") or "")
        for d in (rd, c["dir"]):
            if base and os.path.exists(os.path.join(d, base)): path = os.path.join(d, base); break
    if path is None:
        key = {"uid": "unique-id-service", "rec": "recommendation", "syn": "-syn"}[c["svc"]]
        cand = [f for f in files if key in f and ("-k5-" in f or "-k6-" in f or "-i2-" in f)]   # i2: Phase I
        if not cand: return []
        path = os.path.join(rd, sorted(cand)[-1])
    with open(path, "rb") as f:
        f.seek(b0); d = f.read(b1 - b0)
    if b0: d = d[d.find(b"\n") + 1:]
    out = []
    for l in d.split(b"\n"):
        if b'"all"' not in l: continue
        try: out.append(json.loads(l))
        except Exception: pass
    return out


def arm_metrics(root, c, a):
    dur = a["dur_ms"] / 1000.0
    m = dict(cell=c["cell"], name=c["name"], svc=c["svc"], nb=c["nb"], rung=c["rung"], arm=a["arm"], rep=a["rep"], dur_s=dur, intercept=a.get("intercept"), reads=a.get("reads"), scorer=a.get("scorer"))
    st = a["stat"]; vc = [int(x) for x in str(c.get("vcpus", "")).split(",") if x]
    busy = lambda cpus=None: sum(sum(v[i] for i in (0, 1, 2, 5, 6, 7)) for k, v in st.items() if cpus is None or int(k) in cpus) / HZ / dur
    m["node_busy_cores"] = busy(); m["node_softirq_cores"] = sum(v[6] for v in st.values()) / HZ / dur
    m["victim_cores"] = a["victim_us"] / 1e6 / dur if a.get("victim_us") is not None else None
    m["aggr_cores"] = a["aggr_us"] / 1e6 / dur if a.get("aggr_us") is not None else None
    m["kubepods_cores"] = a["kubepods_us"] / 1e6 / dur if a.get("kubepods_us") is not None else None
    m["system_cores"] = a["system_us"] / 1e6 / dur if a.get("system_us") is not None else None
    si = a.get("sirq", {})
    m["net_rx_per_s"] = sum(si.get("NET_RX", {}).values()) / dur if si.get("NET_RX") else None
    m["softirq_victim_cpus_cores"] = None
    ag = a.get("agent_self") or {}
    m["agent_cores"] = (ag["cpu_ns"] / 1e9 / (ag["dur_ms"] / 1000.0)) if ag.get("dur_ms") else ((a["pids_ns"].get("agent") or 0) / 1e9 / dur if a.get("pids_ns", {}).get("agent") else 0.0)
    progs = ag.get("progs", {})
    m["bpf_cores"] = sum(v[0] for v in progs.values()) / 1e9 / (ag["dur_ms"] / 1000.0) if ag.get("dur_ms") and progs else 0.0
    m["bpf_events_s"] = sum(v[1] for v in progs.values()) / (ag["dur_ms"] / 1000.0) if ag.get("dur_ms") and progs else 0.0
    m["bpf_ns_per_event"] = (m["bpf_cores"] * 1e9 / m["bpf_events_s"]) if m["bpf_events_s"] else None
    m["bpf_progs"] = ";".join(f"{k}:{v[0] / max(1, v[1]):.0f}ns" for k, v in progs.items())
    m["agent_rss_kb"] = ag.get("rss_kb"); m["map_memlock_kb"] = (ag.get("map_memlock") or 0) / 1024.0; m["rb_lost"] = ag.get("lost")
    sc = a.get("scorer_self") or {}
    m["scorer_cores"] = (sc["cpu_ns"] / 1e9 / dur) if sc.get("windows") else ((a["pids_ns"].get("scorer") or 0) / 1e9 / dur if a.get("pids_ns", {}).get("scorer") else 0.0)
    m["scorer_ms_per_window"] = (sc["cpu_ns"] / sc["windows"] / 1e6) if sc.get("windows") else None
    if a.get("scorer") == "replay" and not sc.get("windows"):
        # replay rows keep the recorded timestamps, so the controller's time filter finds none: each row carries the scorer's own CPU (cpu_ns)
        R = [d for d in jl(os.path.join(c["dir"], "ctl", f"{a['arm']}-r{a['rep']:02d}-score.jsonl")) if "row" in d and d.get("cpu_ns") is not None]
        if len(R) > 1:
            R = R[1:]   # the first window carries the dictionary load
            m["scorer_ms_per_window"] = sum(d["cpu_ns"] for d in R) / len(R) / 1e6
            m["scorer_cores"] = m["scorer_ms_per_window"] * 10 / 1000.0   # at one window per 100 ms tick
            sc = dict(windows=len(R))
    m["scorer_windows"] = sc.get("windows"); m["scorer_rows"] = json.dumps(sc.get("rows")) if sc.get("rows") else None
    m["agents_cores"] = m["agent_cores"] + m["scorer_cores"]
    ss = a.get("schedstat") or {}   # Phase I 4: tasks run per second on each victim CPU (/proc/schedstat pcount)
    if ss and vc: m["victim_switch_s"] = sum(ss.get(str(c), [0])[0] for c in vc) / dur / len(vc)
    m["affinity"] = json.dumps(a.get("affinity")) if a.get("affinity") else None
    if (a.get("files") or {}).get("agent"):
        T = [d for d in jl(os.path.join(c["dir"], "ctl", os.path.basename(a["files"]["agent"]))) if "mhz" in d]
        mz = sorted(sum(v[0] for v in d["mhz"].values()) / len(d["mhz"]) for d in T if d["mhz"])
        if mz: m["agent_mhz"] = mz[len(mz) // 2]
    m["ms_per_tick"] = (m["agents_cores"] + m["bpf_cores"]) * 100.0   # a 100 ms tick: cores x 100 ms
    g = [x for x in c["gen"] if a["t0"] <= x[0] < a["t1"]]
    if g:
        m["client_rps"] = sum(x[1] for x in g) / len(g); m["client_p99_ms"] = med([x[2] for x in g]); m["client_seconds"] = len(g)
        m["cpu_per_req_ms"] = (m["victim_cores"] * 1000.0 / m["client_rps"]) if m["victim_cores"] is not None and m["client_rps"] else None
    W = recorder_windows(root, c, a)
    for w in W:   # 5.8: the recorder's production mode writes "lat" = [n timed, p50, p90, p99] (same positions) and "nc"
        if w.get("lat"):
            w["all"] = w["lat"]; w["_n"] = w.get("nc", w["lat"][0])
        elif w.get("all"):
            w["_n"] = w["all"][0]
    W = [w for w in W if w.get("all") and w["all"][0]]
    if W:
        n = sum(w["all"][0] for w in W)
        nreq = sum(w["_n"] for w in W)   # every completion (the timed ones are 1 in N in production mode)
        m["srv_windows"] = len(W); m["srv_rps"] = nreq / (len(W) * (W[0].get("w", 100) / 1000.0))
        wp = sorted((w["all"][1] / 1e6, w["all"][0]) for w in W); acc = 0
        for v, k in wp:
            acc += k
            if acc >= n / 2: m["srv_p50_ms"] = v; break
        wp = sorted((w["all"][3] / 1e6, w["all"][0]) for w in W); acc = 0
        for v, k in wp:
            acc += k
            if acc >= n / 2: m["srv_p99_ms"] = v; break   # completion-weighted median of the window p99s
        m["srv_p99_max_ms"] = max(w["all"][3] for w in W) / 1e6
        cpu = [w["cpu"] for w in W if w.get("cpu") is not None]
        if cpu and nreq: m["srv_cpu_per_req_ms"] = sum(cpu) / nreq / 1e6
    return m


def load(root):
    cells = cells_of(root); rows = []
    for c in cells:
        rec = jl(os.path.join(c["dir"], "ctl", "cell.jsonl"))
        hdr = [r for r in rec if r.get("event") == "cell"]
        c["vcpus"] = hdr[0]["victim"]["cpus"] if hdr else ""
        c["gen"] = generator(c)
        for a in rec:
            if a.get("event") != "arm": continue
            rows.append(arm_metrics(root, c, a))
    return cells, rows


def fmt(v, n=3):
    if v is None or (isinstance(v, float) and math.isnan(v)): return "—"
    return f"{v:.{n}f}" if isinstance(v, float) else str(v)


def pctd(a, b):
    return (a - b) / b * 100.0 if a is not None and b not in (None, 0) else None


def gate(m, limit):
    """K1's resolution rule on the 95 % interval: pass if its top is within the limit, fail if its bottom is over it"""
    if m[0] is None: return None
    if m[1] is None: return "pass" if m[0] <= limit else "fail"
    return "pass" if m[2] <= limit else "fail" if m[1] > limit else "undecided"


def rung1(rows):
    out = []
    by = defaultdict(lambda: defaultdict(dict))
    for r in rows:
        if r["rung"] != 1: continue
        by[(r["svc"], r["name"].split("-")[-1], r["nb"])][r["rep"]][r["arm"]] = r
    for (svc, load, nb), reps in sorted(by.items()):
        d_cpu, d_p99, d_srvp99, agents, bpf, tick = [], [], [], [], [], []
        for rep, arms in reps.items():
            n, f = arms.get("none"), arms.get("full")
            if not n or not f: continue
            d_cpu.append(pctd(f.get("cpu_per_req_ms"), n.get("cpu_per_req_ms"))); d_p99.append(pctd(f.get("client_p99_ms"), n.get("client_p99_ms")))
            agents.append(f["agents_cores"]); bpf.append(f["bpf_cores"]); tick.append(f["ms_per_tick"])
        mc = ci(d_cpu); mp = ci(d_p99)
        out.append(dict(svc=svc, load=load, nb=nb, n=mc[3], d_cpu_per_req_pct=mc[0], d_cpu_lo=mc[1], d_cpu_hi=mc[2], d_client_p99_pct=mp[0], d_p99_lo=mp[1], d_p99_hi=mp[2],
                        agents_cores=ci(agents)[0], bpf_cores=ci(bpf)[0], ms_per_tick=ci(tick)[0],
                        full_srv_p99_ms=ci([a["full"].get("srv_p99_ms") for a in reps.values() if "full" in a])[0],
                        none_cpu_per_req_ms=ci([a["none"].get("cpu_per_req_ms") for a in reps.values() if "none" in a])[0],
                        gate_cpu=gate(mc, 5.0), gate_p99=gate(mp, 2.0), gate_tick=(ci(agents)[0] is not None and ci(agents)[0] <= 0.01)))
    return out


def setb(rows):
    """5.7: each set-B arm (defB, defBt) against the twin of the same rep in the b- cells, 95 % t intervals"""
    out = []
    by = defaultdict(lambda: defaultdict(dict))
    for r in rows:
        if r["rung"] != 5: continue
        by[(r["svc"], r["name"].split("-")[-1], r["nb"])][(r["cell"], r["rep"])][r["arm"]] = r   # 5.7 and 5.8 cells share rep numbers
    for (svc, load, nb), reps in sorted(by.items()):
        names = sorted({k for a in reps.values() for k in a} - {"none"})   # Phase I: every arm of the cell
        for arm in names:
            tw = [a["none"] for a in reps.values() if "none" in a and arm in a]   # the twins of this arm's own cells
            if not tw: continue
            dc, dp, ag, bp, tk = [], [], [], [], []
            for a in reps.values():
                n, f = a.get("none"), a.get(arm)
                if not n or not f: continue
                dc.append(pctd(f.get("cpu_per_req_ms"), n.get("cpu_per_req_ms"))); dp.append(pctd(f.get("client_p99_ms"), n.get("client_p99_ms")))
                ag.append(f["agents_cores"]); bp.append(f["bpf_cores"]); tk.append(f["ms_per_tick"])
            mc, mp = ci(dc), ci(dp)
            # in-handler p99 (the recorder's windows): the twin has its timers off, so the reference is the timers-only arm
            # ("intercept") of the same rep; its own value is reported too
            ds = [pctd(a[arm].get("srv_p99_ms"), a["intercept"].get("srv_p99_ms")) for a in reps.values() if arm in a and "intercept" in a and arm != "intercept"]
            msv = ci(ds)
            ex = dict(srv_p99_ms=ci([a[arm].get("srv_p99_ms") for a in reps.values() if arm in a])[0],
                      d_srv_p99_pct=msv[0], d_srv_p99_lo=msv[1], d_srv_p99_hi=msv[2], gate_srv_p99=gate(msv, 2.0),
                      client_p99_ms=ci([a[arm].get("client_p99_ms") for a in reps.values() if arm in a])[0],
                      victim_switch_s=ci([a[arm].get("victim_switch_s") for a in reps.values() if arm in a])[0],
                      twin_switch_s=ci([t.get("victim_switch_s") for t in tw])[0],
                      agent_mhz=ci([a[arm].get("agent_mhz") for a in reps.values() if arm in a])[0],
                      affinity=next((a[arm].get("affinity") for a in reps.values() if arm in a and a[arm].get("affinity")), None))
            out.append(dict(ex, svc=svc, load=load, nb=nb, arm=arm, n=mc[3], d_cpu_per_req_pct=mc[0], d_cpu_lo=mc[1], d_cpu_hi=mc[2],
                            d_client_p99_pct=mp[0], d_p99_lo=mp[1], d_p99_hi=mp[2], agents_cores=ci(ag)[0], bpf_cores=ci(bp)[0], ms_per_tick=ci(tk)[0],
                            twin_cpu_per_req_ms=ci([t.get("cpu_per_req_ms") for t in tw])[0], twin_victim_cores=ci([t.get("victim_cores") for t in tw])[0],
                            twin_p99_ms=ci([t.get("client_p99_ms") for t in tw])[0], gate_cpu=gate(mc, 5.0), gate_p99=gate(mp, 2.0)))
    return out


def ladder(rows):
    """rungs 2-4 against the (mid, switch) twins of the service"""
    twins = defaultdict(list)
    for r in rows:
        if r["rung"] == 0 and r["arm"] == "none": twins[(r["svc"], r["nb"])].append(r)
    out = []
    for r in rows:
        if r["rung"] not in (2, 3, 4): continue
        tw = twins.get((r["svc"], r["nb"]), [])
        tc = [t.get("cpu_per_req_ms") for t in tw if t.get("cpu_per_req_ms")]; tp = [t.get("client_p99_ms") for t in tw if t.get("client_p99_ms")]
        c0 = sum(tc) / len(tc) if tc else None; p0 = sum(tp) / len(tp) if tp else None
        res_c = (max(tc) - min(tc)) / c0 * 100.0 if len(tc) > 1 and c0 else None; res_p = (max(tp) - min(tp)) / p0 * 100.0 if len(tp) > 1 and p0 else None
        dc = pctd(r.get("cpu_per_req_ms"), c0); dp = pctd(r.get("client_p99_ms"), p0)
        out.append(dict(rung=r["rung"], svc=r["svc"], component=r["name"].split("-", 2)[-1], nb=r["nb"], cpu_per_req_ms=r.get("cpu_per_req_ms"), twin_cpu_per_req_ms=c0, d_cpu_per_req_pct=dc, twins_spread_cpu_pct=res_c,
                        distinguishable_cpu=(dc is not None and res_c is not None and abs(dc) > res_c), client_p99_ms=r.get("client_p99_ms"), twin_p99_ms=p0, p99_ratio=(r["client_p99_ms"] / p0 if r.get("client_p99_ms") and p0 else None),
                        twins_spread_p99_pct=res_p, agent_cores=r["agent_cores"], scorer_cores=r["scorer_cores"], bpf_cores=r["bpf_cores"], ms_per_tick=r["ms_per_tick"], bpf_ns_per_event=r.get("bpf_ns_per_event"),
                        bpf_events_s=r.get("bpf_events_s"), agent_rss_kb=r.get("agent_rss_kb"), map_memlock_kb=r.get("map_memlock_kb"), scorer_ms_per_window=r.get("scorer_ms_per_window"), twins=len(tw)))
    return out


PROD_SOURCES = ("probe", "reads", "sampler", "intercept", "scorer-replay")


def additivity(lad, r1):
    """rung-2 rows summed per service against rung 1 (mid, switch)"""
    out = []
    for svc in ("uid", "rec"):
        # only the sources the rung-1 "full" arm runs (probe switch+rq, runtime reads, sampler, interceptor, scorer); perf and resctrl are not in it
        parts = [x for x in lad if x["rung"] == 2 and x["svc"] == svc and x["nb"] == "switch" and x["component"] in PROD_SOURCES]
        full = [x for x in r1 if x["svc"] == svc and x["load"] == "mid" and x["nb"] == "switch"]
        if not parts or not full: continue
        f = full[0]
        s_cpu = sum(x["d_cpu_per_req_pct"] or 0 for x in parts); s_tick = sum(x["ms_per_tick"] or 0 for x in parts)
        out.append(dict(svc=svc, rung2_sum_cpu_per_req_pct=s_cpu, rung1_cpu_per_req_pct=f["d_cpu_per_req_pct"], rung1_cpu_lo=f["d_cpu_lo"], rung1_cpu_hi=f["d_cpu_hi"],
                        gap_cpu_pct=(f["d_cpu_per_req_pct"] - s_cpu) if f["d_cpu_per_req_pct"] is not None else None, rung2_sum_ms_per_tick=s_tick, rung1_ms_per_tick=f["ms_per_tick"],
                        gap_ms_per_tick=(f["ms_per_tick"] - s_tick) if f["ms_per_tick"] is not None else None, components=";".join(f"{x['component']}:{fmt(x['d_cpu_per_req_pct'], 1)}" for x in parts)))
    return out


# ------------------------------------------------------------------------------------------------------------ K6
def agent_summary(path, holders_self=0):
    """medians over the ticks of an agent file: phi, R_foreign (ms/s), phi_sibling, softirq total and while-current (ms/s), CPI, MPKI, CMT MB per cgroup"""
    T = [d for d in jl(path) if "k" in d]
    st = [d for d in jl(path) if d.get("event") == "start"]
    if not T: return {}
    hold = st[0].get("holders", []) if st else []
    out = dict(ticks=len(T))
    phi, rf, rs, sib, sirq, sirqc, on = [], [], [], [], [], [], []
    for d in T:
        cg = (d.get("cg") or [None])[0]
        if not cg: continue
        occ = cg["occ"]; tot = sum(occ); idle = len(occ) - 1
        if tot:
            phi.append(sum(x for i, x in enumerate(occ) if i not in (holders_self, idle)) / tot); on.append(occ[holders_self] / tot)
        R = cg["R"]; rf.append(sum(x for i, x in enumerate(R) if i != holders_self) / 1e6 * 10); rs.append(R[holders_self] / 1e6 * 10)
        if "sib" in cg:
            s = cg["sib"]; ts = sum(s)
            if ts: sib.append(sum(x for i, x in enumerate(s) if i not in (holders_self, idle)) / ts)
        if "sirq" in cg: sirq.append(cg["sirq"] / 1e6 * 10); sirqc.append(cg["sirqc"] / 1e6 * 10)
    out.update(phi=med(phi), on_cpu_share=med(on), R_foreign_ms_s=med(rf), R_self_ms_s=med(rs), phi_sibling=med(sib) if sib else None, softirq_ms_s=med(sirq) if sirq else None, softirq_while_current_ms_s=med(sirqc) if sirqc else None)
    perf = defaultdict(lambda: [0, 0, 0]); cmt = defaultdict(list)
    for d in T:
        for n, v in (d.get("perf") or {}).items():
            for i in range(3): perf[n][i] += v[i]
        for n, v in (d.get("cmt") or {}).items(): cmt[n].append(sum(v) / 1048576.0)
    for n, v in perf.items():
        if v[1]: out[f"cpi:{n}"] = v[0] / v[1]; out[f"mpki:{n}"] = v[2] / v[1] * 1000.0
    for n, v in cmt.items(): out[f"cmt_mb:{n}"] = med(v)
    smp = [d["smp"] for d in T if d.get("smp")]
    if smp:
        names = smp[0].keys()
        for n in names: out[f"usage_cores:{n}"] = med([s[n][0] / 1e5 for s in smp if n in s])   # usage_usec per 100 ms -> cores
    return out


def k6(root6, dicts):
    cells = cells_of(root6); out = []
    for c in cells:
        rec = jl(os.path.join(c["dir"], "ctl", "cell.jsonl")); arms = [r for r in rec if r.get("event") == "arm"]
        if not arms: continue
        a = arms[0]; ag = a.get("files", {}).get("agent") or ""
        p = os.path.join(c["dir"], "ctl", os.path.basename(ag)) if ag else ""
        s = agent_summary(p) if p and os.path.exists(p) else {}
        g = generator(c); gg = [x for x in g if a["t0"] <= x[0] < a["t1"]]
        lam = sum(x[1] for x in gg) / len(gg) if gg else None
        d = dicts.get(c["svc"])
        vk = next((r["victim"].get("k") for r in rec if r.get("event") == "cell"), None)
        if d and vk is not None and vk != d["k"]: d = None   # C5: recommendation on 4 cores; the dictionary is the 2-core one
        Ig = dcpi = dmpki = None
        u = s.get(f"usage_cores:{c['svc']}")
        if d and lam:
            rho = lam * d["cbar_ms"] / d["k"] / 1000.0
            st = d["steps"]

            def at(key):   # the dictionary's column at this rho (linear between bins, flat outside)
                if rho <= st[0]["rho"]: return st[0].get(key)
                if rho >= st[-1]["rho"]: return st[-1].get(key)
                for i in range(1, len(st)):
                    if rho <= st[i]["rho"]:
                        f = (rho - st[i - 1]["rho"]) / (st[i]["rho"] - st[i - 1]["rho"])
                        a0, a1 = st[i - 1].get(key), st[i].get(key)
                        return None if a0 is None or a1 is None else a0 * (1 - f) + a1 * f
            if u is not None: Ig = u / (lam * d["cbar_ms"] / 1000.0 * at("g"))
            # A4: the counter column against the mix baseline at the run's rho (columns only, no verdict)
            cv, mv = s.get(f"cpi:{c['svc']}"), s.get(f"mpki:{c['svc']}")
            if cv is not None and at("cpi") is not None: dcpi = cv - at("cpi")
            if mv is not None and at("mpki") is not None: dmpki = mv - at("mpki")
        row = dict(cell=c["cell"], name=c["name"], svc=c["svc"], nb=c["nb"], client_rps=lam, client_p99_ms=med([x[2] for x in gg]) if gg else None, I_g=Ig, dCPI=dcpi, dMPKI=dmpki,
                   cpu_per_req_ms=(u * 1000.0 / lam) if u is not None and lam else None)
        row.update(s); out.append(row)
    # each run against its own twin (no dictionary for the synthetic service, none at C5's 4 cores): CPU per request ratio
    by = {r["name"]: r for r in out}
    def mean_of(names):
        v = [by[n]["cpu_per_req_ms"] for n in names if n in by and by[n]["cpu_per_req_ms"] is not None]
        return sum(v) / len(v) if v else None
    twin = {"c1-uid-sib": ("c6-uid-twin1", "c6-uid-twin2"), "c2-uid-net": ("c6-uid-twin1", "c6-uid-twin2"), "c6-uid-twin2": ("c6-uid-twin1",),
            "c3-syn-memwalk30": ("c3-syn-uniform-twin",), "c4-syn-memwalk30-cache": ("c3-syn-memwalk30",), "c5-res1": ("c5-twin-S0",), "c3-syn-memwalk30-128mb": ("c3-syn-uniform128-twin",)}
    for r in out:
        t = mean_of(twin.get(r["name"], ()))
        r["twin"] = "+".join(twin.get(r["name"], ())); r["cpu_per_req_vs_twin"] = r["cpu_per_req_ms"] / t if t and r["cpu_per_req_ms"] is not None else None
    return out


def write_csv(path, rows):
    if not rows: return
    keys = []
    for r in rows:
        for k in r:
            if k not in keys: keys.append(k)
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=keys); w.writeheader()
        for r in rows: w.writerow({k: (fmt(v, 5) if isinstance(v, float) else v) for k, v in r.items()})


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("root"); ap.add_argument("--k6", default=None); ap.add_argument("--out", default=None)
    a = ap.parse_args(); out = a.out or a.root; os.makedirs(out, exist_ok=True)
    cells, rows = load(a.root)
    write_csv(os.path.join(out, "k5_arms.csv"), rows)
    r1 = rung1(rows); write_csv(os.path.join(out, "k5_rung1.csv"), r1)
    lad = ladder(rows); write_csv(os.path.join(out, "k5_ladder.csv"), lad)
    add = additivity(lad, r1); write_csv(os.path.join(out, "k5_additivity.csv"), add)
    sb = setb(rows); write_csv(os.path.join(out, "k5_setb.csv"), sb)
    print(f"{len(cells)} cells, {len(rows)} arm records")
    print("\nrung 1 (full - none, paired, n reps): d CPU/req %, d client p99 %, agents cores, BPF cores, ms per tick, gates")
    for r in r1:
        print(f"  {r['svc']:>3} {r['load']:>4} {r['nb']:>6}: n={r['n']} dCPU {fmt(r['d_cpu_per_req_pct'], 1)} [{fmt(r['d_cpu_lo'], 1)}, {fmt(r['d_cpu_hi'], 1)}] %  dp99 {fmt(r['d_client_p99_pct'], 1)} [{fmt(r['d_p99_lo'], 1)}, {fmt(r['d_p99_hi'], 1)}] %"
              f"  agents {fmt(r['agents_cores'], 4)} bpf {fmt(r['bpf_cores'], 4)} cores  {fmt(r['ms_per_tick'], 2)} ms/tick  gates cpu {r['gate_cpu']} p99 {r['gate_p99']} tick {r['gate_tick']}")
    print("\nladder (rungs 2-4 vs the (mid, switch) twins): component, d CPU/req % (twins' spread), p99 ratio, agent / scorer / bpf cores, ms per tick")
    for r in sorted(lad, key=lambda x: (x["rung"], x["svc"], x["component"])):
        print(f"  r{r['rung']} {r['svc']:>3} {r['component']:<14} {r['nb']:>6}: dCPU {fmt(r['d_cpu_per_req_pct'], 1):>6} % (spread {fmt(r['twins_spread_cpu_pct'], 1)} %, {'distinct' if r['distinguishable_cpu'] else 'not distinguishable'})"
              f"  p99 x{fmt(r['p99_ratio'], 3)}  agent {fmt(r['agent_cores'], 4)} scorer {fmt(r['scorer_cores'], 4)} bpf {fmt(r['bpf_cores'], 4)}  {fmt(r['ms_per_tick'], 2)} ms/tick  {fmt(r['bpf_ns_per_event'], 0)} ns/ev")
    if sb:
        print("\nset B (5.7): arm - twin, paired per rep: d CPU/req %, d client p99 % [95 %], twin, agents, BPF, ms/tick, gates")
        for r in sb:
            print(f"  {r['svc']:>3} {r['load']:>4} {r['nb']:>6} {r['arm']:>5}: n={r['n']} dCPU {fmt(r['d_cpu_per_req_pct'], 1)} [{fmt(r['d_cpu_lo'], 1)}, {fmt(r['d_cpu_hi'], 1)}] %"
                  f"  dp99 {fmt(r['d_client_p99_pct'], 1)} [{fmt(r['d_p99_lo'], 1)}, {fmt(r['d_p99_hi'], 1)}] %  twin {fmt(r['twin_cpu_per_req_ms'], 4)} ms/req at {fmt(r['twin_victim_cores'], 3)} cores, p99 {fmt(r['twin_p99_ms'], 2)} ms"
                  f"  agents {fmt(r['agents_cores'], 4)} bpf {fmt(r['bpf_cores'], 4)}  {fmt(r['ms_per_tick'], 2)} ms/tick  gates cpu {r['gate_cpu']} p99 {r['gate_p99']}")
    print("\nadditivity: rung-2 sum vs rung 1")
    for r in add: print(f"  {r['svc']}: CPU/req sum {fmt(r['rung2_sum_cpu_per_req_pct'], 1)} % vs rung 1 {fmt(r['rung1_cpu_per_req_pct'], 1)} [{fmt(r['rung1_cpu_lo'], 1)}, {fmt(r['rung1_cpu_hi'], 1)}] -> gap {fmt(r['gap_cpu_pct'], 1)}; ms/tick sum {fmt(r['rung2_sum_ms_per_tick'], 2)} vs {fmt(r['rung1_ms_per_tick'], 2)}; {r['components']}")
    if a.k6:
        dicts = {}
        for svc in ("uid", "rec"):
            p = os.path.join(a.root, f"dict-{svc}.json")
            if os.path.exists(p): dicts[svc] = json.load(open(p))
        rows6 = k6(a.k6, dicts); write_csv(os.path.join(out, "k6_runs.csv"), rows6)
        print("\nK6 runs: phi, R_foreign, phi_sibling, softirq total / while-current, CPI, MPKI, CMT, I_g")
        for r in rows6:
            print(f"  {r['name']:<22}: phi {fmt(r.get('phi'))} Rf {fmt(r.get('R_foreign_ms_s'), 1)} ms/s  sib {fmt(r.get('phi_sibling'))}  sirq {fmt(r.get('softirq_ms_s'), 2)}/{fmt(r.get('softirq_while_current_ms_s'), 2)} ms/s"
                  f"  CPI {fmt(r.get('cpi:' + r['svc']), 2)} MPKI {fmt(r.get('mpki:' + r['svc']), 3)} CMT {fmt(r.get('cmt_mb:' + r['svc']), 1)} MB  I_g {fmt(r.get('I_g'), 3)}"
                  f"  cpu/req {fmt(r.get('cpu_per_req_ms'), 4)} ms (x{fmt(r.get('cpu_per_req_vs_twin'), 3)} vs twin)  p99 {fmt(r.get('client_p99_ms'), 3)} ms")


if __name__ == "__main__":
    main()
