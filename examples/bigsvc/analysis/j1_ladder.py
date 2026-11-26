#!/usr/bin/env python3
"""Phase J1 (RESULTS-J 1): the unpinned overhead ladder as overhead_cells.csv rows. Each ladder cell runs "none,A1,none,A2,...,none"
per rep: an arm's reference is the mean of the twin before and the twin after it (paired), the difference in CPU per request (the
pod cgroup's usage over the arm / the client's completions) and in client p99 (median of the per-second p99s) is taken per rep,
and the rows carry the mean over reps with the spread (min-max over the 3 reps; n = reps). k5_analyze's arm_metrics does the
per-arm arithmetic. Runs on node-0.
usage: j1_ladder.py /local/results/j1 OUT_DIR
"""
import csv, json, os, statistics, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import k5_analyze as K

ROOT, OUT = sys.argv[1:3]
os.makedirs(OUT, exist_ok=True)
PLAT = "Ice Lake 8360Y (clnode270 node-3, r650; unpinned, cpu.max, production clock)"
SVC = {"uid": "unique-id", "rec": "recommendation"}
ROWS = {   # arm -> (row, component label, mode)
    "j-probe": (1, "probe: switch program on all CPUs, cgroup-filtered (run_delay, on-CPU per CPU)", "default"),
    "j-holder": (2, "probe: + holder state on every switch (phi_sibling) [cumulative 1-2]", "default"),
    "j-wake": (3, "probe: + wake-up programs [cumulative 1-3]", "default"),
    "j-sirq": (4, "probe: + softirq programs [cumulative 1-4]", "default"),
    "j-count": (5, "interceptor: count only (per-thread counters, g9)", "default"),
    "j-slot": (6, "interceptor: count + slot (per-thread)", "default"),
    "j-full": (7, "interceptor: count + slot + timers on every request (per-thread)", "default"),
    "j-s8": (8, "interceptor: count + slot + 1-in-8 timers (per-thread)", "default"),
    "j-perf": (9, "perf cgroup group on all CPUs: cycles, instructions, LLC misses, 3 stalls, ref-cycles, LLC refs (8 events)", "diagnostic"),
    "j-rdt": (10, "resctrl CMT/MBM armed (RMID on the victim's switches), no reads", "default"),
    "j-clock": (11, "per-CPU clock events (cycles / ref-cycles) on all 144 CPUs", "default"),
    "j-reads": (12, "sampler + the recorder's window reads", "default"),
    "j-scorer": (13, "scorer alone (replay of recorded inputs at the tick)", "default"),
    "j-all": (14, "all on (rows 1-4, 7, 9-13), every request timed, scorer on-node", "default"),
    "j-all-s8": (14, "all on (rows 1-4, 8, 9-13), 1-in-8 timers, scorer on-node", "default"),
    "Bp-J": (15, "Phase I set B' as it runs unpinned (mask = every CPU, no cgroup filter; no msr clock read)", "default"),
}


def load(root):
    cells = K.cells_of(root); rows = []
    for c in cells:
        rec = K.jl(os.path.join(c["dir"], "ctl", "cell.jsonl"))
        hdr = [r for r in rec if r.get("event") == "cell"]
        c["vcpus"] = ""   # unpinned: "all" is not a CPU list
        c["k"] = (hdr[0]["victim"].get("k") if hdr else None)
        c["gen"] = K.generator(c)
        arms = [a for a in rec if a.get("event") == "arm"]
        for i, a in enumerate(arms):
            m = K.arm_metrics(root, c, a); m["seq"] = i; m["cell_name"] = c["name"]; rows.append(m)
        c["_arms"] = [r for r in rows if r["cell"] == c["cell"]]
    return cells, rows


def paired(cell_rows):
    """arm -> list over reps of (d_cpu_pct, d_p99_pct, arm_metrics, twin_mean_cpu, twin_mean_p99)"""
    out = {}
    seq = sorted(cell_rows, key=lambda r: r["seq"])
    for i, r in enumerate(seq):
        if r["arm"] == "none": continue
        tw = [seq[j] for j in (i - 1, i + 1) if 0 <= j < len(seq) and seq[j]["arm"] == "none"]
        if not tw: continue
        tc = [t["cpu_per_req_ms"] for t in tw if t.get("cpu_per_req_ms")]; tp = [t["client_p99_ms"] for t in tw if t.get("client_p99_ms")]
        if not tc or not tp or not r.get("cpu_per_req_ms"): continue
        mc, mp = sum(tc) / len(tc), sum(tp) / len(tp)
        out.setdefault(r["arm"], []).append((K.pctd(r["cpu_per_req_ms"], mc), K.pctd(r["client_p99_ms"], mp), r, mc, mp))
    return out


def spread(xs):
    xs = [x for x in xs if x is not None]
    if not xs: return (None, None, None, 0)
    return (sum(xs) / len(xs), min(xs), max(xs), len(xs))


def pmux_check(cell, arm, rep):
    """j-perf arms: the share of ticks whose time_running equals time_enabled (no multiplexing)"""
    p = os.path.join(cell["dir"], "ctl", f"{arm}-r{rep:02d}-agent.jsonl")
    if not os.path.exists(p): return None
    ok = n = 0
    for d in K.jl(p):
        pm = d.get("pmux")
        if not pm: continue
        for v in pm.values():
            if not v[0]: continue   # a group with no enabled time this tick (the idle aggressor cgroup)
            n += 1
            if abs(v[0] - v[1]) <= 0.001 * v[0]: ok += 1
    return (ok / n) if n else None


cells, rows = load(ROOT)
out, md = [], []
r3 = lambda v: round(v, 3) if v is not None else None
for c in cells:
    P = paired(c["_arms"])
    twins = [r for r in c["_arms"] if r["arm"] == "none" and r.get("cpu_per_req_ms")]
    tw_cpu = statistics.median(r["cpu_per_req_ms"] for r in twins) if twins else None
    tw_p99 = statistics.median(r["client_p99_ms"] for r in twins) if twins else None
    tw_rps = statistics.median(r["client_rps"] for r in twins if r.get("client_rps")) if twins else None
    md.append(f"\n### {c['name']} ({c['cell']}; {SVC.get(c['svc'], c['svc'])}, k {c.get('k')}, {c['reps']} x {c['measure']} s): twins CPU/req {K.fmt(tw_cpu, 4)} ms, client p99 {K.fmt(tw_p99, 3)} ms, {K.fmt(tw_rps, 0)} req/s\n")
    md.append("| row | arm | CPU/req % [min, max] | delta us/req [min, max] | p99 % [min, max] | n | agent cores | bpf ns/event | notes |\n|---|---|---|---|---|---|---|---|---|")
    for arm, L in sorted(P.items(), key=lambda kv: ROWS.get(kv[0], (99,))[0]):
        row, comp, mode = ROWS.get(arm, (99, arm, "default"))
        if c["name"].startswith("uid-count-g8") and arm == "j-count": comp = "interceptor: count only (shared counters, g8: the before build)"; row = 5
        sc = spread([x[0] for x in L]); sp = spread([x[1] for x in L])
        ag = spread([x[2].get("agent_cores") for x in L]); bpf = spread([x[2].get("bpf_ns_per_event") for x in L])
        notes = []
        if arm == "j-perf":
            pm = [pmux_check(c, arm, x[2]["rep"]) for x in L]; pm = [v for v in pm if v is not None]
            if pm: notes.append(f"pmux time_running = time_enabled on {100 * sum(pm) / len(pm):.1f} % of ticks")
        if ag[0]: notes.append(f"agent {ag[0]:.3f} cores")
        if bpf[0]: notes.append(f"BPF {bpf[0]:.0f} ns/event")
        sc_abs = spread([x[2].get("scorer_cores") for x in L])
        if sc_abs[0]: notes.append(f"scorer {sc_abs[0]:.3f} cores")
        # J0'': the difference in microseconds per request beside the percentage (arm minus the paired twins' mean, per rep)
        dus = spread([(x[2]["cpu_per_req_ms"] - x[3]) * 1000.0 for x in L]); arm_us = spread([x[2]["cpu_per_req_ms"] * 1000.0 for x in L])
        notes.append(f"twin CPU/req {K.fmt(tw_cpu, 4)} ms at {K.fmt(tw_rps, 0)} req/s; arm abs {K.fmt(arm_us[0], 1)} us; delta {K.fmt(dus[0], 2)} us [{K.fmt(dus[1], 2)}, {K.fmt(dus[2], 2)}]")
        load = os.environ.get("J_LOAD", "mid"); setname = os.environ.get("J_SET", "J1")
        out.append(dict(platform=PLAT, service=SVC.get(c["svc"], c["svc"]), neighbor=os.environ.get("J_NEIGHBOR", "none"), load=load, component=comp, mode=mode,
                        cpu_per_req_pct=r3(sc[0]), cpu_lo=r3(sc[1]), cpu_hi=r3(sc[2]), p99_pct=r3(sp[0]), p99_lo=r3(sp[1]), p99_hi=r3(sp[2]), n=sc[3],
                        interval=f"mean over {sc[3]} reps, [min, max]; each rep paired to the twins before and after (60 s arms)", set=f"{setname} row {row}",
                        source_cell=c["cell"], source_file=f"results/{os.path.basename(ROOT.rstrip('/'))} (analysis/j1_ladder.py)", notes="; ".join(notes),
                        delta_us=r3(dus[0]), delta_us_lo=r3(dus[1]), delta_us_hi=r3(dus[2]), arm_us=r3(arm_us[0]), twin_us=r3(tw_cpu * 1000.0 if tw_cpu else None)))
        md.append(f"| {row} | {arm} | {K.fmt(sc[0], 2)} [{K.fmt(sc[1], 2)}, {K.fmt(sc[2], 2)}] | {K.fmt(dus[0], 2)} us [{K.fmt(dus[1], 2)}, {K.fmt(dus[2], 2)}] | {K.fmt(sp[0], 2)} [{K.fmt(sp[1], 2)}, {K.fmt(sp[2], 2)}] | {sc[3]} | {K.fmt(ag[0], 3)} | {K.fmt(bpf[0], 0)} | {'; '.join(notes[:-1])} |")
K.write_csv(os.path.join(OUT, "j1_ladder.csv"), out)
open(os.path.join(OUT, "j1_ladder.md"), "w").write("\n".join(md) + "\n")
print("\n".join(md)); print(f"\n{len(out)} rows -> {OUT}/j1_ladder.csv")
