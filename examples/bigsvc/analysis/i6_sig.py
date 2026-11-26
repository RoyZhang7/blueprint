#!/usr/bin/env python3
"""Phase I step 4 (RESULTS-I 5): the Ice-Lake-only signatures from queue i6, one source at a time against a twin.
Per cell (arm sigF, one 300 s run): the client rate and p99, the recorder's p50 and p99 (k5_analyze.arm_metrics),
CPU per request (the sampler's usage per client completion), and from the agent's 100 ms ticks for the victim's cgroup:
CPI, MPKI, the stall columns (memory-bound = STALLS_MEM_ANY / cycles, DRAM-bound = STALLS_L3_MISS / cycles,
core-bound = (STALLS_TOTAL - STALLS_MEM_ANY) / cycles), llc_occupancy (median tick, both L3 domains summed), mbm_total
(bytes over the run / its length), phi, R_foreign, phi_sibling, softirq; for the neighbor's cgroup (gordion-aggr): its
usage, occupancy and mbm. The amount check against the cell's dictionary: rho = lambda cbar / k, I_g = U / (lambda cbar
g(rho)), D(rho), and D' = I_g Fbar + h(I_g rho) I_g K = I_g D(I_g rho) (RESULTS-K 6.3), each against the measured p50.
usage: i6_sig.py results/i6 --dict uid=PATH --dict uniform=PATH --dict memwalk128=PATH --dict memwalk24=PATH [--out DIR]
"""
import argparse, json, os
import numpy as np
from k5_analyze import load, jl, med, fmt, agent_summary, write_csv
from dictionary import ServiceDict

TWIN = {   # each cell against the mean of these (its twin); the order controls against the first twin
    "c6-uid-twin-b": ("c6-uid-twin-a",), "c1-uid-sib": ("c6-uid-twin-a", "c6-uid-twin-b"),
    "c6-syn-uniform-b": ("c6-syn-uniform-a",), "c3-syn-memwalk128-a": ("c6-syn-uniform-a", "c6-syn-uniform-b"),
    "c3-syn-memwalk128-b": ("c3-syn-memwalk128-a",),
    "c7-syn-memwalk128-stream2": ("c3-syn-memwalk128-a", "c3-syn-memwalk128-b"), "c7-syn-memwalk128-stream4": ("c3-syn-memwalk128-a", "c3-syn-memwalk128-b"),
    "c7-syn-memwalk128-stream8": ("c3-syn-memwalk128-a", "c3-syn-memwalk128-b"),
    "c3-syn-memwalk24-b": ("c3-syn-memwalk24-a",), "c4-syn-memwalk24-cache": ("c3-syn-memwalk24-a", "c3-syn-memwalk24-b"),
    # step 5 (queue i8): multi-arm cells read as NAME:ARM; every arm against the twins, the mitigated arms also against the
    # cell's own unmitigated arm (sigF) for the recovery columns
    "m5-syn-memwalk24-twin-b": ("m5-syn-memwalk24-twin-a",), "m5-syn-memwalk128-twin-b": ("m5-syn-memwalk128-twin-a",),
    # queue i9: the two-step policy test (CAT 6 + MBA 30 together) under stream x4, twins before and after
    "m6-syn-memwalk128-twin-b": ("m6-syn-memwalk128-twin-a",),
    "m6-syn-memwalk128-stream4:sigF": ("m6-syn-memwalk128-twin-a", "m6-syn-memwalk128-twin-b"),
    "m6-syn-memwalk128-stream4:cat6mba30": ("m6-syn-memwalk128-twin-a", "m6-syn-memwalk128-twin-b"),
    # queue i10 (step 7, node-4): HC reserve 1 and 3 against the standalone twins before and after
    "c5-rec-twin-S1": ("c5-rec-twin-S0",), "c5-rec-res1": ("c5-rec-twin-S0", "c5-rec-twin-S1"), "c5-rec-res3": ("c5-rec-twin-S0", "c5-rec-twin-S1"),
    # queue i11 (step 8): the contention-grade C2 (NIC RX-queue IRQs on CPU 1, the stream from node-1), twins before and after
    "c2-uid-twin-b": ("c2-uid-twin-a",), "c2-uid-net-r1": ("c2-uid-twin-a", "c2-uid-twin-b"), "c2-uid-net-r2": ("c2-uid-twin-a", "c2-uid-twin-b"), "c2-uid-net-r3": ("c2-uid-twin-a", "c2-uid-twin-b"),
}
for _a in ("sigF", "cat4", "cat6", "mba90", "mba50", "mba30", "mba10"):
    TWIN[f"m5-syn-memwalk24-cache:{_a}"] = ("m5-syn-memwalk24-twin-a", "m5-syn-memwalk24-twin-b")
    TWIN[f"m5-syn-memwalk128-stream4:{_a}"] = ("m5-syn-memwalk128-twin-a", "m5-syn-memwalk128-twin-b")
SOURCE_OF = lambda name: name.rsplit(":", 1)[0] + ":sigF" if ":" in name and not name.endswith(":sigF") else None


def dict_of(name):
    for key in ("uid", "uniform", "memwalk128", "memwalk24", "rec"):
        if key in name: return key
    return None


class UidDict:
    """k5score's single-endpoint dictionary: pooled p50 per rho bin, g per bin"""
    def __init__(self, d):
        self.k = d["k"]; self.cbar = d["cbar_ms"]; st = d["steps"]
        self.r = np.array([s["rho"] for s in st]); self.p = np.array([s["p50_ms"] for s in st]); self.gg = np.array([s.get("g") or 1.0 for s in st])
        self.rho_max = float(self.r[-1])

    def D(self, rho): return float(np.interp(rho, self.r, self.p))

    def g(self, rho): return float(np.interp(rho, self.r, self.gg))


class SynDict:
    """bigsvc's D[rho; mix] at its sweep mix: Fbar_sweep + h(rho) K_sweep (fit_service_dict's JSON)"""
    def __init__(self, d):
        n = len(d["rho"]); z = [0.0] * n
        self.sd = ServiceDict(d["k"], d["rho"], d["p50_sweep_ms"], z, z, d["W_sweep_ms"], d["h"], d["fl_ms"], d["c_ms"], list(d["c_ms"]), d["Fbar_sweep_ms"], d["K_sweep_ms"],
                              d["cbar_sweep_ms"], d["wmax_ms"], d["floor_noise_ms"], d["noise_steps"], d["fbar"], None, d["overloaded_steps"], d["source"], d["costs"],
                              g=d.get("g"), u=d.get("u_sweep_ms"), anchors=d.get("step_anchors"), step_interp=d.get("step_interp"))
        self.k = d["k"]; self.cbar = d["cbar_sweep_ms"]; self.F = d["Fbar_sweep_ms"]; self.K = d["K_sweep_ms"]; self.rho_max = self.sd.rho_max

    def D(self, rho): return self.F + self.sd.h_at(rho) * self.K

    def g(self, rho): return float(self.sd.g_at(rho))


def ticks(path, v):
    """the stall, counter and RDT columns over an agent file's ticks, for the victim v and the neighbor aggr"""
    T = [d for d in jl(path) if "k" in d]
    out = {}
    if not T: return out
    for n, tag in ((v, "v"), ("aggr", "nb")):
        P = np.zeros(6); n6 = 0
        for d in T:
            x = (d.get("perf") or {}).get(n)
            if x and len(x) >= 6: P += np.array(x[:6], float); n6 += 1
            elif x and len(x) >= 3: P[:3] += np.array(x[:3], float)
        cyc, ins, llc, tot, mem, l3 = P
        if ins: out[f"{tag}_cpi"] = cyc / ins; out[f"{tag}_mpki"] = llc / ins * 1000.0; out[f"{tag}_ins_s"] = ins / (len(T) * 0.1)   # progress: instructions per second
        if cyc and n6:
            out[f"{tag}_mem_bound"] = mem / cyc; out[f"{tag}_dram_bound"] = l3 / cyc; out[f"{tag}_core_bound"] = (tot - mem) / cyc
        occ = [sum(d["cmt"][n]) / 1048576.0 for d in T if n in (d.get("cmt") or {})]
        if occ: out[f"{tag}_occ_mb"] = med(occ)
        mb = [sum(d["mbm"][n]) for d in T if n in (d.get("mbm") or {})]
        if mb: out[f"{tag}_mbm_gbps"] = sum(mb) / (len(mb) * 0.1) / 1e9
        us = [d["smp"][n][0] / 1e5 for d in T if n in (d.get("smp") or {})]
        if us: out[f"{tag}_cores"] = sum(us) / len(us)
    mz = [sum(x[0] for x in d["mhz"].values()) / len(d["mhz"]) for d in T if d.get("mhz")]
    if mz: out["mhz"] = med(mz)
    return out


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("root"); ap.add_argument("--dict", action="append", default=[]); ap.add_argument("--out")
    a = ap.parse_args(); out = a.out or os.path.join(a.root, "an"); os.makedirs(out, exist_ok=True)
    D = {}
    for s in a.dict:
        k, _, p = s.partition("="); d = json.load(open(p)); D[k] = UidDict(d) if "steps" in d else SynDict(d)
    cells, rows = load(a.root)
    R = []
    for r in rows:
        if r["name"].startswith("sweep"): continue
        c = next(c for c in cells if c["cell"] == r["cell"])
        recs = jl(os.path.join(c["dir"], "ctl", "cell.jsonl"))
        arm = next(x for x in recs if x.get("event") == "arm" and x["arm"] == r["arm"] and x["rep"] == r["rep"])
        if not (arm.get("files") or {}).get("agent"): continue   # arms without the agent (none, intercept) carry no signature
        p = os.path.join(c["dir"], "ctl", os.path.basename(arm["files"]["agent"]))
        nm = r["name"] + (f":{r['arm']}" if len(c["arms"]) > 1 else "")
        mit = next((m for m in recs if m.get("event") == "mitig" and m.get("mitig") == arm.get("mitig")), None) if arm.get("mitig") else None
        x = dict(name=nm, nb=r["nb"], arm=r["arm"], lam=r.get("client_rps"), client_p99_ms=r.get("client_p99_ms"), p50_ms=r.get("srv_p50_ms"), p99_ms=r.get("srv_p99_ms"),
                 windows=r.get("srv_windows"), mitig=json.dumps(arm.get("mitig")) if arm.get("mitig") else None,
                 schemata=" / ".join(f"{k}: {v['schemata']} ({v['tasks']} tasks)" for k, v in (mit or {}).get("groups", {}).items()) if mit else None)
        x.update(ticks(p, r["svc"])); s = agent_summary(p)
        x.update(phi=s.get("phi"), R_foreign_ms_s=s.get("R_foreign_ms_s"), phi_sibling=s.get("phi_sibling"), softirq_ms_s=s.get("softirq_ms_s"))
        x["cpu_per_req_ms"] = x["v_cores"] * 1000.0 / x["lam"] if x.get("v_cores") is not None and x["lam"] else None
        dd = D.get(dict_of(r["name"]))
        if dd and x["lam"] and x.get("v_cores") is not None:
            rho = x["lam"] * dd.cbar / dd.k / 1000.0; Ig = x["v_cores"] / (x["lam"] * dd.cbar / 1000.0 * dd.g(rho))
            x.update(rho=rho, I_g=Ig, D_ms=dd.D(rho), D_Ig_ms=dd.D(rho * Ig), Dp_ms=Ig * dd.D(rho * Ig), rho_Ig=rho * Ig, rho_max=dd.rho_max)
            if x["p50_ms"]: x.update(D_over_p50=x["D_ms"] / x["p50_ms"], Dp_over_p50=x["Dp_ms"] / x["p50_ms"])
        R.append(x)
    by = {x["name"]: x for x in R}
    keys = ["cpu_per_req_ms", "p50_ms", "v_cpi", "v_mpki", "v_mem_bound", "v_dram_bound", "v_core_bound", "v_occ_mb", "v_mbm_gbps", "mhz"]
    for x in R:
        tw = [by[n] for n in TWIN.get(x["name"], ()) if n in by]
        x["twin"] = "+".join(TWIN.get(x["name"], ()))
        for k in keys:
            v = [t[k] for t in tw if t.get(k) is not None]
            if v and x.get(k) is not None and np.mean(v): x[f"d_{k}"] = x[k] / float(np.mean(v)) - 1.0
    # step 5: recovery = 1 - (mitigated - twin) / (source - twin) on p50 and I_g; neighbor cost = its progress vs unmitigated
    for x in R:
        src = by.get(SOURCE_OF(x["name"]) or "")
        tw = [by[n] for n in TWIN.get(x["name"], ()) if n in by]
        if not src or not tw: continue
        for k in ("p50_ms", "I_g"):
            t = float(np.mean([w[k] for w in tw if w.get(k) is not None])) if any(w.get(k) is not None for w in tw) else None
            if t is not None and src.get(k) is not None and x.get(k) is not None and src[k] != t:
                x[f"recovery_{k}"] = 1.0 - (x[k] - t) / (src[k] - t)
        if src.get("nb_ins_s") and x.get("nb_ins_s") is not None: x["nb_progress"] = x["nb_ins_s"] / src["nb_ins_s"] - 1.0
        if src.get("nb_mbm_gbps") and x.get("nb_mbm_gbps") is not None: x["nb_mbm_vs_src"] = x["nb_mbm_gbps"] / src["nb_mbm_gbps"] - 1.0
    write_csv(os.path.join(out, "i6_sig.csv"), R)
    print("cell: lambda, p50 / p99 (client p99) ms, CPU/req ms, CPI, MPKI, mem / DRAM / core-bound, occ MB, mbm GB/s | neighbor cores, occ MB, mbm GB/s | phi, phi_sib, Rf, sirq | MHz")
    for x in R:
        print(f"{x['name']:<27} {fmt(x['lam'], 0)} {fmt(x['p50_ms'], 3)}/{fmt(x['p99_ms'], 3)} ({fmt(x['client_p99_ms'], 2)}) cpu {fmt(x['cpu_per_req_ms'], 4)} CPI {fmt(x.get('v_cpi'), 3)} MPKI {fmt(x.get('v_mpki'), 3)}"
              f" mem {fmt(x.get('v_mem_bound'), 3)} dram {fmt(x.get('v_dram_bound'), 3)} core {fmt(x.get('v_core_bound'), 3)} occ {fmt(x.get('v_occ_mb'), 1)} mbm {fmt(x.get('v_mbm_gbps'), 3)}"
              f" | nb {fmt(x.get('nb_cores'), 2)} {fmt(x.get('nb_occ_mb'), 1)} {fmt(x.get('nb_mbm_gbps'), 2)} | phi {fmt(x.get('phi'), 3)} sib {fmt(x.get('phi_sibling'), 3)} Rf {fmt(x.get('R_foreign_ms_s'), 2)}"
              f" sirq {fmt(x.get('softirq_ms_s'), 2)} | {fmt(x.get('mhz'), 0)}")
    print()
    print("vs twin (ratio - 1, %): CPU/req, p50, CPI, MPKI, mem, DRAM, core, occ, mbm, MHz")
    for x in R:
        if not x["twin"]: continue
        print(f"{x['name']:<27} vs {x['twin']:<45} " + " ".join(f"{k.replace('v_', '')} {fmt(100 * x[f'd_{k}'], 1) if x.get(f'd_{k}') is not None else '-'}" for k in keys))
    if any(x.get("recovery_p50_ms") is not None for x in R):
        print()
        print("step 5: mitigation, schemata; victim recovery on p50 and I_g (1 = back at the twin), occupancy MB, MPKI, DRAM-bound; neighbor progress and mbm vs unmitigated")
        for x in R:
            if x.get("recovery_p50_ms") is None: continue
            print(f"{x['name']:<36} {x.get('mitig') or '-':<18} {x.get('schemata') or ''}")
            print(f"{'':<36} p50 {fmt(x['p50_ms'], 2)} ms rec {fmt(x['recovery_p50_ms'], 2)}  I_g {fmt(x.get('I_g'), 3)} rec {fmt(x.get('recovery_I_g'), 2)}  occ {fmt(x.get('v_occ_mb'), 1)} MPKI {fmt(x.get('v_mpki'), 3)} dram {fmt(x.get('v_dram_bound'), 3)}"
                  f"  | nb progress {fmt(100 * x['nb_progress'], 1) if x.get('nb_progress') is not None else '-'} % mbm {fmt(x.get('nb_mbm_gbps'), 1)} GB/s ({fmt(100 * x['nb_mbm_vs_src'], 1) if x.get('nb_mbm_vs_src') is not None else '-'} %) occ {fmt(x.get('nb_occ_mb'), 1)} MB")
    print()
    print("amount check: rho, I_g, D(rho), D(I_g rho), D' = I_g D(I_g rho), measured p50, D/p50, D'/p50, profiled rho max")
    for x in R:
        if x.get("I_g") is None: continue
        print(f"{x['name']:<27} rho {fmt(x['rho'], 3)} I_g {fmt(x['I_g'], 3)} D {fmt(x['D_ms'], 3)} D(I_g rho) {fmt(x['D_Ig_ms'], 3)} D' {fmt(x['Dp_ms'], 3)} p50 {fmt(x['p50_ms'], 3)}"
              f" D/p50 {fmt(x.get('D_over_p50'), 2)} D'/p50 {fmt(x.get('Dp_over_p50'), 2)} (rho*I_g {fmt(x['rho_Ig'], 3)}, max {fmt(x['rho_max'], 3)})")


if __name__ == "__main__":
    main()
