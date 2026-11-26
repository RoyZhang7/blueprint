#!/usr/bin/env python3
"""Phase G Addendum E.3 (c) / E.4 (b): join the perf-stat counters (probe-ctl.sh perf-start: cycles, instructions,
LLC-loads, LLC-load-misses every 100 ms, counting mode; the victim process with all its threads, and the aggressor
CPUs system-wide) with the recorder's windows. New file <run>/g2/perf_windows.csv (nothing overwritten): per window
v_cycles, v_instructions, v_llc_loads, v_llc_misses, v_ipc, v_cycles_per_req, v_llc_misses_per_req, v_llc_miss_rate and
the same a_* for the aggressor CPUs. A perf interval is credited to the recorder window its end falls in; the perf clock
is the wrapper's start_ms (perf.meta) plus perf's elapsed seconds. Per-step medians printed.

usage: g2_perf.py RUN_DIR [--svc NAME]
"""
import argparse, csv, os, sys
import numpy as np
sys.path.insert(0, os.path.dirname(__file__))
from analyze import load_run
from dictionary import assign

EV = {"cycles": "cycles", "instructions": "instructions", "LLC-loads": "llc_loads", "LLC-load-misses": "llc_misses"}


def read_perf(path, start_ms):
    """perf stat -I 100 -x, : time,count,unit,event,run,pct,...; '<not counted>' / '<not supported>' -> NaN."""
    out = []
    if not os.path.exists(path): return out
    for line in open(path):
        if not line.strip() or line.startswith("#"): continue
        p = line.rstrip("\n").split(",")
        if len(p) < 4: continue
        try: t = float(p[0])
        except ValueError: continue
        ev = p[3].strip(); c = p[1].strip()
        cnt = float(c) if c.replace(".", "", 1).isdigit() else np.nan
        key = next((v for k, v in EV.items() if ev.startswith(k)), None)
        if key: out.append((start_ms + t * 1000.0, key, cnt))
    return out


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("run"); ap.add_argument("--svc", default=None)
    a = ap.parse_args(); d = a.run
    meta = {}
    mp = os.path.join(d, "perf.meta")
    if os.path.exists(mp):
        toks = open(mp).read().split(); meta = {toks[i]: toks[i + 1] for i in range(0, len(toks) - 1, 2)}
    start_ms = float(meta.get("start_ms", "nan"))
    if not np.isfinite(start_ms): sys.exit(f"{d}: no perf.meta start_ms")
    R = load_run(d, svc=a.svc); W = R["windows"]
    t = np.array([w["t"] for w in W], float); wn = np.array([w.get("w", 100) for w in W], float)
    dt = wn.copy(); dd = np.append(np.diff(t), np.nan); ok = np.isfinite(dd) & (dd >= 0.5 * wn) & (dd <= 3 * wn); dt[ok] = dd[ok]
    step_of = [(-1 if s is None else s) for s in assign(W, R["steps"], "step")]; rate = {s["step"]: s["rate"] for s in R["steps"]}
    V = read_perf(os.path.join(d, "perf-victim.csv"), start_ms); A = read_perf(os.path.join(d, "perf-aggr.csv"), start_ms)
    def bucket(recs):
        acc = {}
        if not recs: return acc
        ends = np.array([r[0] for r in recs]); idx = np.searchsorted(t, ends, side="right") - 1
        for (te, key, cnt), i in zip(recs, idx):
            if i < 0 or i >= len(t) or te >= t[i] + dt[i] + 1: continue
            acc.setdefault(i, {}).setdefault(key, 0.0); acc[i][key] += 0.0 if np.isnan(cnt) else cnt
        return acc
    bv, ba = bucket(V), bucket(A)
    rows = []
    for i, w in enumerate(W):
        v = bv.get(i, {}); g = ba.get(i, {}); n = max(w["a"], 0)
        def ipc(x): return x.get("instructions", np.nan) / x["cycles"] if x.get("cycles") else np.nan
        rows.append(dict(t=int(t[i]), dt_ms=int(dt[i]), step=step_of[i], a=w["a"],
                         v_cycles=v.get("cycles", np.nan), v_instructions=v.get("instructions", np.nan), v_llc_loads=v.get("llc_loads", np.nan), v_llc_misses=v.get("llc_misses", np.nan),
                         v_ipc=ipc(v), v_cycles_per_req=(v.get("cycles", np.nan) / n if n else np.nan), v_llc_misses_per_req=(v.get("llc_misses", np.nan) / n if n else np.nan),
                         v_llc_miss_rate=(v.get("llc_misses", np.nan) / v["llc_loads"] if v.get("llc_loads") else np.nan),
                         a_cycles=g.get("cycles", np.nan), a_instructions=g.get("instructions", np.nan), a_llc_loads=g.get("llc_loads", np.nan), a_llc_misses=g.get("llc_misses", np.nan),
                         a_ipc=ipc(g), a_llc_miss_rate=(g.get("llc_misses", np.nan) / g["llc_loads"] if g.get("llc_loads") else np.nan)))
    os.makedirs(os.path.join(d, "g2"), exist_ok=True)
    with open(os.path.join(d, "g2", "perf_windows.csv"), "w", newline="") as f:
        wr = csv.DictWriter(f, fieldnames=list(rows[0].keys())); wr.writeheader(); wr.writerows(rows)
    print(f"{os.path.basename(d)}: {len(W)} windows, {len(V)} victim perf samples, {len(A)} aggressor perf samples, victim pid {meta.get('victim_pid')}, aggressor CPUs {meta.get('aggr_cpus')}")
    print(f"{'step':>5s} {'rate':>5s} | {'v IPC':>6s} {'v kcyc/req':>10s} {'v LLCmiss/req':>13s} {'v missrate':>10s} {'v Mmiss/s':>9s} | {'a IPC':>6s} {'a Gcyc/s':>8s} {'a Mmiss/s':>9s} {'a missrate':>10s}")
    for s in sorted(set(step_of)):
        if s < 0: continue
        m = [r for r in rows if r["step"] == s]; m = m[50:] if len(m) > 60 else m
        if not m: continue
        med = lambda key: float(np.nanmedian([r[key] for r in m]))
        per_s = lambda key: float(np.nansum([r[key] for r in m])) / (sum(r["dt_ms"] for r in m) / 1000.0)
        print(f"{s:5d} {rate.get(s, 0):5d} | {med('v_ipc'):6.2f} {med('v_cycles_per_req') / 1e3:10.0f} {med('v_llc_misses_per_req'):13.0f} {med('v_llc_miss_rate'):10.3f} {per_s('v_llc_misses') / 1e6:9.2f} | {med('a_ipc'):6.2f} {per_s('a_cycles') / 1e9:8.2f} {per_s('a_llc_misses') / 1e6:9.2f} {med('a_llc_miss_rate'):10.3f}")
    print("(medians over the step's settled windows; per-s figures summed over them; victim = the service process, all threads; aggressor = everything on the aggressor CPUs)")


if __name__ == "__main__":
    main()
