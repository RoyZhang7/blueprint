#!/usr/bin/env python3
"""Phase I step 2 (RESULTS-I 2.3): the dictionaries and their counter column from queue i2's sweep cells.

usage: i2_dict.py RESULTS_DIR [--out DIR] [--k5score ~/k5/k5score.py] [--settle-s 3]

For every sweep cell in RESULTS_DIR/cells.txt (names sweep-*), per step of the generator's schedule (the first
--settle-s seconds of each step dropped):
  rate offered and served, rho (the recorder's process CPU over the step's length / k), the recorder's p50 and p99
  (completion-weighted medians of the window values), CPU per request, and from the agent's 100 ms ticks for the
  victim's cgroup: CPI, MPKI, instructions per request, llc_occupancy (median tick, both L3 domains summed) and
  mbm_total (bytes over the step / its length).
Steps: wlgen's "step" events (recommendation, bigsvc); for unique-id (ugen has none) each second's completions are
labelled with the nearest scheduled rate and contiguous runs form the steps.
Writes i2_counters.csv. Fits, both with a floor of 50 windows per rho bin (RESULTS-K 4.5.1):
  bigsvc mixes: dictionary.fit_service_dict(min_bin_windows=50, step_interp=True) -> dict-syn-<mix>.json
  unique-id, recommendation: k5score.py --fit --fit-min-windows 50 (bins interpolated linearly) -> dict-<svc>.json
"""
import argparse, csv, glob, json, os, subprocess, sys

HERE = os.path.dirname(os.path.abspath(__file__))
K = {"uid": 1, "rec": 2, "syn": 4}


def jl(p):
    out = []
    for l in open(p, errors="replace"):
        try: out.append(json.loads(l))
        except Exception: pass
    return out


def wmed(pairs):
    """completion-weighted median of (value, weight)"""
    pairs = sorted(p for p in pairs if p[1] > 0)
    tot = sum(w for _, w in pairs); acc = 0
    for v, w in pairs:
        acc += w
        if acc >= tot / 2: return v
    return None


def steps_wlgen(path):
    R = jl(path)
    st = [r for r in R if r.get("event") == "step"]
    end = [r["t"] for r in R if r.get("event") == "end"]
    out = []
    for i, s in enumerate(st):
        t1 = st[i + 1]["t"] if i + 1 < len(st) else (end[0] if end else s["t"] + 45000)
        if out and out[-1]["rate"] == s.get("rate"):   # the warm-up runs at the first rate: merge it into the first step
            out[-1]["t1"] = t1; continue
        out.append(dict(rate=s.get("rate"), t0=s["t"], t1=t1))
    return out


def steps_ugen(path, sched):
    rates = [float(x.split(":")[0]) for x in sched.split(",")]
    R = [r for r in jl(path) if "done" in r and "event" not in r]
    out = []
    for r in R:
        lab = min(rates, key=lambda x: abs(x - r["done"]))
        if out and out[-1]["rate"] == lab: out[-1]["t1"] = r["t"]
        else: out.append(dict(rate=lab, t0=r["t"] - 1000, t1=r["t"]))
    return [s for s in out if s["t1"] - s["t0"] >= 10000]   # drop the transitions


def agent_path(root, cell):
    """the sweep arm's agent file (arm sweep in step 2, sweepF in the Nov 18 re-sweeps)"""
    g = sorted(glob.glob(os.path.join(root, cell, "ctl", "sweep*-r00-agent.jsonl")))
    return g[0] if g else os.path.join(root, cell, "ctl", "sweep-r00-agent.jsonl")


def victim_k(root, cell, svc):
    """step 8: k from the cell header's victim (the pod's cpuset), the K table only when the header has none"""
    hdr = [r for r in jl(os.path.join(root, cell, "ctl", "cell.jsonl")) if r.get("event") == "cell"]
    v = hdr[0].get("victim") if hdr else None
    if isinstance(v, str):
        try: v = eval(v)
        except Exception: v = None
    return int(v["k"]) if isinstance(v, dict) and v.get("k") else K[svc]


def recorder_path(root, cell):
    hdr = [r for r in jl(os.path.join(root, cell, "ctl", "cell.jsonl")) if r.get("event") == "cell"]
    base = os.path.basename(hdr[0]["victim"]["recorder"]) if hdr else ""
    for d in (os.path.join(root, "recorders"), os.path.join(root, cell)):
        if base and os.path.exists(os.path.join(d, base)): return os.path.join(d, base)
    return None


def counters(root, cell, svc, sched, settle):
    d = os.path.join(root, cell)
    steps = steps_ugen(os.path.join(d, "ugen.jsonl"), sched) if svc == "uid" else steps_wlgen(os.path.join(d, "wlgen.jsonl"))
    rp = recorder_path(root, cell)
    W = [w for w in jl(rp) if "t" in w and (w.get("lat") or w.get("all"))] if rp else []
    T = [t for t in jl(agent_path(root, cell)) if "k" in t and "perf" in t]
    rows = []
    for s in steps:
        a, b = s["t0"] + settle * 1000, s["t1"]
        ws = [w for w in W if a <= w["t"] < b]; ts = [t for t in T if a <= t["t"] < b]
        dur = (b - a) / 1000.0
        r = dict(cell=cell, svc=svc, rate=s["rate"], seconds=round(dur, 1), windows=len(ws), ticks=len(ts))
        if ws:
            L = [(w.get("lat") or w["all"]) for w in ws]
            nc = sum(w.get("nc", (w.get("lat") or w["all"])[0]) for w in ws)
            cpu = sum(w.get("cpu", 0) for w in ws)
            span = len(ws) * ws[0].get("w", 100) / 1000.0
            r.update(served_s=round(nc / span, 1), rho=round(cpu / 1e9 / span / K[svc], 4),
                     p50_ms=round(wmed([(x[1] / 1e6, x[0]) for x in L]), 4), p99_ms=round(wmed([(x[3] / 1e6, x[0]) for x in L]), 3),
                     cpu_per_req_ms=round(cpu / max(1, nc) / 1e6, 5))
        if ts:
            cy = sum(t["perf"].get(svc, [0, 0, 0])[0] for t in ts); ins = sum(t["perf"].get(svc, [0, 0, 0])[1] for t in ts)
            mi = sum(t["perf"].get(svc, [0, 0, 0])[2] for t in ts)
            occ = sorted(sum(t.get("cmt", {}).get(svc, [0])) for t in ts)
            mbm = sum(sum(t.get("mbm", {}).get(svc, [0])) for t in ts)
            tdur = len(ts) * 0.1
            r.update(cpi=round(cy / ins, 3) if ins else None, mpki=round(mi / ins * 1000, 3) if ins else None,
                     llc_occupancy_mb=round(occ[len(occ) // 2] / 2**20, 2), mbm_total_gbps=round(mbm / tdur / 1e9, 4))
            if r.get("served_s") and ins: r["ins_per_req"] = round(ins / tdur / r["served_s"], 0)   # per second of each source, then the ratio
        rows.append(r)
    return rows


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("root"); ap.add_argument("--out"); ap.add_argument("--k5score", default=os.path.expanduser("~/k5/k5score.py"))
    ap.add_argument("--settle-s", type=float, default=3.0); a = ap.parse_args()
    out = a.out or os.path.join(a.root, "dict"); os.makedirs(out, exist_ok=True)
    cells = [l.split() for l in open(os.path.join(a.root, "cells.txt")) if l.split()[1].startswith("sweep-")]
    rows = []
    for c in cells:
        cell, name, svc, sched = c[:4]
        rs = counters(a.root, cell, svc, sched, a.settle_s); rows += [dict(r, mix=name.replace("sweep-syn-", "") if svc == "syn" else svc) for r in rs]
    keys = ["mix", "svc", "rate", "served_s", "rho", "p50_ms", "p99_ms", "cpu_per_req_ms", "cpi", "mpki", "ins_per_req", "llc_occupancy_mb", "mbm_total_gbps", "windows", "ticks", "seconds", "cell"]
    with open(os.path.join(out, "i2_counters.csv"), "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=keys, extrasaction="ignore"); w.writeheader(); [w.writerow(r) for r in rows]
    for r in rows: print(" ".join(f"{k}={r.get(k)}" for k in keys[:14]))
    # fits
    os.environ.setdefault("GORDION_NOMINAL_MHZ", "2400")
    sys.path.insert(0, HERE)
    from analyze import load_run
    from dictionary import fit_service_dict
    for c in cells:
        cell, name, svc, sched = c[:4]
        d = os.path.join(a.root, cell)
        arm = [r for r in jl(os.path.join(d, "ctl", "cell.jsonl")) if r.get("event") == "arm"]
        rp = recorder_path(a.root, cell)
        if not rp:
            print(f"fit {name}: no recorder file (unique-id and recommendation are fetched at the queue's restore)"); continue
        if svc in ("uid", "rec"):
            cmd = [sys.executable, a.k5score, "--fit", rp, "--fit-agent", agent_path(a.root, cell), "--fit-cg", svc,
                   "--dict", os.path.join(out, f"dict-{svc}.json"), "--k", str(victim_k(a.root, cell, svc)), "--fit-from-t", str(arm[0]["t0"]), "--fit-to-t", str(arm[0]["t1"]), "--fit-min-windows", "50"]
            print(f"fit {svc}:", subprocess.run(cmd, capture_output=True, text=True).stdout.strip())
        else:
            mix = name.replace("sweep-syn-", ""); rd = os.path.join(out, "run-" + mix); os.makedirs(rd, exist_ok=True)
            for src, dst in ((rp, "gordion.jsonl"), (os.path.join(d, "wlgen.jsonl"), "wlgen.jsonl")):
                p = os.path.join(rd, dst)
                if os.path.lexists(p): os.remove(p)
                os.symlink(os.path.abspath(src), p)
            json.dump(dict(run=cell, cpuset="1,3,5,7", nominal_mhz="2400", memwalk_mb=128 if mix.endswith("-128") else 256), open(os.path.join(rd, "meta.json"), "w"))
            try:
                SD = fit_service_dict(load_run(rd), 5.0, None, fbar="mixture", min_bin_windows=50, step_interp=True, label=f"syn-{mix}")
                SD.save(os.path.join(out, f"dict-syn-{mix}.json")); print(f"fit syn {mix}: {SD.describe()}")
            except SystemExit as e:
                print(f"fit syn {mix}: FAILED {e}")


if __name__ == "__main__":
    main()
