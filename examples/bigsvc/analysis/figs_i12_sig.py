#!/usr/bin/env python3
"""Step 11: the milder C2 arm (queue i12) and its twins as signatures.csv rows in the i6_sig format, for figC's C2 row from
the r650: I_g corrected and raw, phi, phi_irq, R_foreign, phi_sibling, softirq total and while current, the softirq share,
CPU per request, in-handler p50 / p99, client p50 / p99, own occupancy and MBM, the aggressor's occupancy and MBM (the
receiver pod netrecv as monitored by the agent, if present), CPI / MPKI / DRAM- / core-bound (absent: arm Bp-sirq carries no
counters, written as n/a). Values per cell = medians over the arm; ratio vs the two twins' mean. Runs on node-0.
usage: figs_i12_sig.py /local/results/i12 FINAL_REPLAY_DIR OUT.csv
"""
import csv, json, os, sys, statistics
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from k5_analyze import jl, agent_summary
from i6_sig import ticks

R, RP, OUT = sys.argv[1:4]
cells = {c.split("-i2-", 1)[1]: c for c in os.listdir(R) if "-i2-c2m-uid-" in c}


def arm_of(cell):
    recs = jl(os.path.join(R, cell, "ctl", "cell.jsonl")); return next(r for r in recs if r.get("event") == "arm")


def score_meds(path):
    W = [w for w in jl(path) if "row" in w]; m = lambda k: (statistics.median(v) if (v := [w[k] for w in W if w.get(k) is not None]) else None)
    return dict(I_g=m("Ig"), I_g_raw=m("Ig_raw"), phi_irq=m("phi_irq"), sirq=m("sirq"), rho_eff=m("rho_eff"))


def client(cell, t0, t1):
    v = [x for x in jl(os.path.join(R, cell, "ugen.jsonl")) if isinstance(x, dict) and x.get("lat_p50_us") and t0 <= x.get("t", 0) < t1]
    return dict(client_p50_ms=statistics.median(x["lat_p50_us"] for x in v) / 1000, client_p99_ms=statistics.median(x["lat_p99_us"] for x in v) / 1000, served=statistics.median(x["done"] for x in v))


def srv(cell, t0, t1):
    recs = jl(os.path.join(R, cell, "ctl", "cell.jsonl")); hdr = next(r for r in recs if r.get("event") == "cell"); v = hdr["victim"]
    if isinstance(v, str): v = eval(v)
    p = os.path.join(R, "recorders", os.path.basename(v["recorder"])); W = [w for w in jl(p) if t0 <= w.get("t", 0) < t1 and w.get("lat") and w["lat"][0]]
    return dict(srv_p50_ms=statistics.median(w["lat"][1] for w in W) / 1e6, srv_p99_ms=statistics.median(w["lat"][3] for w in W) / 1e6, cpu_per_req_ms=sum(w.get("cpu", 0) for w in W) / max(1, sum(w.get("nc", w["a"]) for w in W)) / 1e6)


def columns(name):
    cell = cells[name]; a = arm_of(cell); ag = os.path.join(R, cell, "ctl", os.path.basename(a["files"]["agent"])); sc = os.path.join(R, cell, "ctl", os.path.basename(a["files"]["scorer"]))
    s = agent_summary(ag); t = ticks(ag, "uid"); x = dict(cell=cell)
    x.update(srv(cell, a["t0"], a["t1"])); x.update(client(cell, a["t0"], a["t1"])); x.update(score_meds(sc))
    x.update(phi=s.get("phi"), R_foreign_ms_s=s.get("R_foreign_ms_s"), phi_sibling=s.get("phi_sibling"), softirq_ms_s=s.get("softirq_ms_s"), softirq_while_current_ms_s=s.get("softirq_while_current_ms_s"),
             own_occ_mb=t.get("v_occ_mb"), own_mbm_gbps=t.get("v_mbm_gbps"), mhz=t.get("mhz"))
    # the receiver pod (netrecv holder) as the aggressor's cgroup, if the agent monitored it
    T = [d for d in jl(ag) if "k" in d]
    occ = [sum(d["cmt"]["netrecv"]) / 1048576.0 for d in T if "netrecv" in (d.get("cmt") or {})]; mb = [sum(d["mbm"]["netrecv"]) for d in T if "netrecv" in (d.get("mbm") or {})]
    x.update(aggr_occ_mb=(statistics.median(occ) if occ else None), aggr_mbm_gbps=(sum(mb) / (len(mb) * 0.1) / 1e9 if mb else None))
    return x


SIG = [("CPU per request", "cpu_per_req_ms", "ratio"), ("I_g corrected (softirq subtracted)", "I_g", "ratio"), ("I_g raw", "I_g_raw", "ratio"), ("in-handler p50", "srv_p50_ms", "ratio"), ("in-handler p99", "srv_p99_ms", "ratio"),
       ("client p50", "client_p50_ms", "ratio"), ("client p99", "client_p99_ms", "ratio"), ("served req/s", "served", "ratio"), ("phi", "phi", "absolute"), ("phi_irq (softirq share above baseline)", "phi_irq", "absolute"),
       ("softirq share of the victim CPU", "sirq", "ratio"), ("R_foreign ms/s", "R_foreign_ms_s", "ratio"), ("phi_sibling", "phi_sibling", "absolute"), ("softirq ms/s", "softirq_ms_s", "ratio"), ("softirq while current ms/s", "softirq_while_current_ms_s", "ratio"),
       ("rho_eff", "rho_eff", "ratio"), ("own LLC occupancy MB", "own_occ_mb", "ratio"), ("own MBM GB/s", "own_mbm_gbps", "ratio"), ("aggressor LLC occupancy MB (netrecv)", "aggr_occ_mb", "absolute"), ("aggressor MBM GB/s (netrecv)", "aggr_mbm_gbps", "absolute"),
       ("clock MHz (APERF/MPERF)", "mhz", "ratio"), ("CPI", None, "n/a"), ("MPKI", None, "n/a"), ("DRAM-bound share", None, "n/a"), ("core-bound share", None, "n/a")]
tw = [columns("c2m-uid-twin-a"), columns("c2m-uid-twin-b")]; rows = []
for name, label in (("c2m-uid-net-r1", "C2 milder network neighbor, rep 1 (NIC RX IRQs on CPU 1, moderation off, 20 000 pps from node-1)"), ("c2m-uid-net-r2", "C2 milder network neighbor, rep 2"), ("c2m-uid-net-r3", "C2 milder network neighbor, rep 3"), ("c2m-uid-twin-b", "C6' twin vs twin (unique-id, milder C2 cells)")):
    x = columns(name); twins = tw if name != "c2m-uid-twin-b" else tw[:1]
    for sig, col, kind in SIG:
        if col is None:
            rows.append(dict(source=label, signal=sig, ratio_vs_twin="", platform="Ice Lake 8360Y (clnode270 node-3, r650)", run_id=x["cell"], value="", twin_value="", twin_run_id="+".join(t["cell"] for t in twins), kind="n/a (arm Bp-sirq carries no perf counters)", source_file="results/i12 (analysis/figs_i12_sig.py)", notes="B' + the softirq programs: no counter or stall columns"))
            continue
        v = x.get(col); tv = [t.get(col) for t in twins if t.get(col) is not None]; t_ = (sum(tv) / len(tv)) if tv else None
        ratio = (v / t_) if (kind == "ratio" and v is not None and t_ not in (None, 0)) else None
        rows.append(dict(source=label, signal=sig, ratio_vs_twin=(round(ratio, 4) if ratio is not None else ""), platform="Ice Lake 8360Y (clnode270 node-3, r650)", run_id=x["cell"], value=(round(v, 5) if v is not None else ""), twin_value=(round(t_, 5) if t_ is not None else ""),
                         twin_run_id="+".join(t["cell"] for t in twins), kind=(kind if ratio is not None else ("absolute" if kind == "absolute" else "ratio undefined (twin 0 or missing)")), source_file="results/i12 (analysis/figs_i12_sig.py)", notes=("the receiver pod's cgroup as the aggressor; the stream's sender is on node-1" if "aggressor" in sig else "")))
with open(OUT, "w", newline="") as f:
    w = csv.DictWriter(f, fieldnames=list(rows[0].keys())); w.writeheader(); w.writerows(rows)
print("rows", len(rows)); [print(r["source"][:40], r["signal"], r["value"], r["twin_value"], r["ratio_vs_twin"]) for r in rows if r["source"].endswith("rep 1") and r["kind"] != "n/a (arm Bp-sirq carries no perf counters)"]
