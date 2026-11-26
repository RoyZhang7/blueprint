#!/usr/bin/env python3
"""Phase I step 6 (RESULTS-I 7): the escalation arm. Per arm and repetition of the dc-* cells of a queue-i9 results root:
the escalations (count, on-time, share of the scorer's windows flagged), the scorer's rows and MBM sub-rule, the victim's
CPU per request and client p99 (from k5_analyze's per-arm table, k5_arms.csv in --an), and, from the diagnostic agent's
files, the signature in the flagged windows (MPKI, memory-/DRAM-bound, own occupancy, neighbor mbm, softirq while current,
phi) beside the production agent's columns and a reference twin (the step-5 twins for memwalk128: --ref-mpki 1.10
--ref-dram 0.19 --ref-occ 50).
usage: i9_esc.py results/i9 [--an results/i9/an] [--out results/i9/an]
"""
import argparse, csv, json, os, sys
from collections import Counter
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from k5_analyze import load, jl, med, agent_summary, write_csv
from i6_sig import ticks


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("root"); ap.add_argument("--an"); ap.add_argument("--out")
    a = ap.parse_args(); an = a.an or os.path.join(a.root, "an"); out = a.out or an; os.makedirs(out, exist_ok=True)
    cells, rows = load(a.root)
    arms = {}
    p = os.path.join(an, "k5_arms.csv")
    if os.path.exists(p):
        for r in csv.DictReader(open(p)): arms[(r["cell"], r["arm"], int(float(r["rep"])))] = r
    R = []
    for c in cells:
        if not c["name"].startswith("dc-"): continue
        recs = jl(os.path.join(c["dir"], "ctl", "cell.jsonl")); v = c["svc"]
        for r in [x for x in recs if x.get("event") == "arm"]:
            k = (c["cell"], r["arm"], r["rep"]); ar = arms.get(k, {})
            x = dict(cell=c["name"], nb=c["nb"], arm=r["arm"], rep=r["rep"], cpu_per_req_ms=ar.get("cpu_per_req_ms"), client_p99_ms=ar.get("client_p99_ms"), agents_cores=ar.get("agents_cores"), bpf_cores=ar.get("bpf_cores"))
            # the scorer's windows: rows, flagged share, sub-rule
            sc = r.get("files", {}).get("scorer")
            if sc:
                W = [w for w in jl(os.path.join(c["dir"], "ctl", os.path.basename(sc))) if "row" in w]
                if W:
                    fl = [w for w in W if (w.get("E") is not None and w["E"] >= 0.15) or (w.get("Ig") is not None and w["Ig"] > 1.15)]
                    x.update(windows=len(W), flagged_share=len(fl) / len(W), rows=json.dumps(dict(Counter(w["row"] for w in W))), sub=json.dumps(dict(Counter(w.get("sub") or "" for w in W))),
                             E_med=med([w["E"] for w in W]), Ig_med=med([w["Ig"] for w in W if w.get("Ig") is not None]) if any(w.get("Ig") is not None for w in W) else None)
            # the production agent's columns
            ag = r.get("files", {}).get("agent")
            if ag:
                ap_ = os.path.join(c["dir"], "ctl", os.path.basename(ag)); t = ticks(ap_, v); s = agent_summary(ap_)
                x.update(prod_occ_mb=t.get("v_occ_mb"), prod_nb_mbm_gbps=t.get("nb_mbm_gbps"), prod_phi=s.get("phi"), prod_phi_sibling=s.get("phi_sibling"), prod_softirq_ms_s=s.get("softirq_ms_s"))
            # the escalations and the diagnostic agent's columns (its ticks are the flagged windows, plus the hold)
            e = r.get("esc")
            if e:
                x.update(esc_count=e.get("count"), esc_on_s=round((e.get("on_ms") or 0) / 1000.0, 1), esc_windows=e.get("windows"), esc_flagged=e.get("flagged"),
                         esc_agent_cores=sum((s_ or {}).get("cores", 0) or 0 for s_ in (e.get("agent_self") or [])) or None)
                T = {}; S = {}
                for f in e.get("files") or []:
                    fp = os.path.join(c["dir"], "ctl", os.path.basename(f))
                    if not os.path.exists(fp): continue
                    for kk, vv in ticks(fp, v).items(): T.setdefault(kk, []).append(vv)
                    for kk, vv in agent_summary(fp).items(): S.setdefault(kk, []).append(vv)
                def m(d, kk):
                    vals = [q for q in d.get(kk, []) if q is not None]; return med(vals) if vals else None
                x.update(diag_ticks=m(S, "ticks"), diag_cpi=m(T, "v_cpi"), diag_mpki=m(T, "v_mpki"), diag_mem_bound=m(T, "v_mem_bound"), diag_dram_bound=m(T, "v_dram_bound"), diag_core_bound=m(T, "v_core_bound"),
                         diag_occ_mb=m(T, "v_occ_mb"), diag_nb_mbm_gbps=m(T, "nb_mbm_gbps"), diag_nb_cores=m(T, "nb_cores"), diag_phi=m(S, "phi"), diag_R_foreign_ms_s=m(S, "R_foreign_ms_s"),
                         diag_softirq_ms_s=m(S, "softirq_ms_s"), diag_softirq_while_current_ms_s=m(S, "softirq_while_current_ms_s"))
            R.append(x)
    keys = []
    for x in R:
        for k in x:
            if k not in keys: keys.append(k)
    write_csv(os.path.join(out, "i9_esc.csv"), [{k: x.get(k) for k in keys} for x in R])
    print("cell, arm, rep: CPU/req ms, client p99 | windows, flagged share, rows, sub | esc count, on s | diag: MPKI, mem, DRAM, occ MB, nb mbm, sirq/while-current ms/s, phi")
    for x in R:
        f = lambda k, d=3: ("—" if x.get(k) is None else (f"{x[k]:.{d}f}" if isinstance(x[k], float) else str(x[k])))
        print(f"{x['cell']:28s} {x['arm']:14s} r{x['rep']}: cpu {f('cpu_per_req_ms', 4)} p99 {f('client_p99_ms', 2)} | win {f('windows')} flag {f('flagged_share', 2)} rows {x.get('rows', '—')} sub {x.get('sub', '—')} | esc {f('esc_count')} on {f('esc_on_s', 1)} s"
              + (f" | diag MPKI {f('diag_mpki')} mem {f('diag_mem_bound')} dram {f('diag_dram_bound')} occ {f('diag_occ_mb', 1)} nbmbm {f('diag_nb_mbm_gbps', 1)} sirq {f('diag_softirq_ms_s', 1)}/{f('diag_softirq_while_current_ms_s', 1)} phi {f('diag_phi')}" if x.get("esc_count") else ""))


if __name__ == "__main__":
    main()
