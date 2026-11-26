#!/usr/bin/env python3
"""Phase K4.5, item 2: RESULTS-K 4.1b's stall test, read from the 7 000 req/s runs of K4.5.

stall7 is the deciding run (reserve 1, HC + burner, the 60 s Observation Phase Balancer bin2/obs/balancer: HC lends nothing
until 60 s after the Balancer starts, so the load arrives on four free cores). res1x7 (the shipped Balancer, which lends
before the load arrives) and free7 (start and cap 3: at most one core lent when the load arrives) are read the same way
beside it, with T7 (standalone) as the reference.

Per run, from the start of hrgen's warm-up: per second the client's completions, errors and requests in flight (hrgen's
per-second records), the Primary's arrivals and completions (its recorder windows), its CPU and run-queue wait (the lean
probe), and the cores HC has lent (k4sample.py's 0.2 s "aff" records: Primary cores in the burner's allowed sets).
A stall second = a second of offered load in which the client completed nothing. The outcomes, as RESULTS-K 4.1b states
them for stall7:
  A  no warm-up second without completions and warm-up errors at the burner-only / HC-only level (26-120): lending before
     the load is the trigger; the hypothesis holds
  B  a stall at the load start anyway: lending is not the trigger; the hypothesis fails
  C  a stall when lending begins: taking cores away under load trips it too; the hypothesis holds in a wider form
usage: k45_stall.py DATA RUNS [--out results/k45]   (DATA and RUNS as for k45_analyze.py; RUNS holds the split recorder)
Writes k45_stall.csv (one row per run), k45_stall_seconds.csv (every second of the first 120 s and around the first
lending) and figK4-5c.png.
"""
import argparse, bisect, csv, json, os, sys
import numpy as np
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
from k4_analyze import jl

RUNS7 = ("T7", "res1x7", "free7", "stall7")


def per_second(data, runs, seg):
    d = os.path.join(data, seg)
    W = jl(os.path.join(d, "wlgen.jsonl"))
    steps = [x for x in W if x.get("event") == "step"]
    t_load = min(x["t"] for x in steps if x.get("step") == -1) if any(x.get("step") == -1 for x in steps) else min(x["t"] for x in steps)
    t_meas = next((x["t"] for x in steps if x.get("step") == 0), None)
    sec = [x for x in W if "all" in x and "done" in x]
    cl = {}
    for p, x in zip(sec, sec[1:]):
        s = int((x["t"] - t_load) // 1000)
        cl[s] = dict(done=x["done"] - p["done"], err=x["err"] - p["err"], sent=x.get("sent", 0) - p.get("sent", 0), inflight=x["inflight"], step=x.get("step"))
    srv = {}
    rp = os.path.join(runs, seg, "recommendation.jsonl")
    if os.path.exists(rp):
        for line in open(rp):
            if not line.startswith('{"t":'): continue
            o = json.loads(line); s = int((o["t"] - t_load) // 1000)
            g = srv.setdefault(s, [0, 0]); g[0] += o.get("a", 0); g[1] += o.get("n", 0)
    pr = {}
    for o in jl(os.path.join(d, "probe.jsonl")):
        if "Rself" not in o: continue
        s = int((o["t"] - t_load) // 1000); g = pr.setdefault(s, [0.0, 0.0, 0.0])
        g[0] += o["Oself"] / 1e9; g[1] += o["Rself"] / 1e6; g[2] += o.get("Oaggr", 0) / 1e9
    aff = [(x["t"], len(x.get("lent") or [])) for x in jl(os.path.join(d, "sample.jsonl")) if x.get("kind") == "aff"]
    lent = {}
    for t, n in aff:
        s = int((t - t_load) // 1000); lent.setdefault(s, []).append(n)
    first_lend = next((t for t, n in aff if n > 0), None)
    return dict(t_load=t_load, t_meas=t_meas, cl=cl, srv=srv, pr=pr, lent={s: float(np.mean(v)) for s, v in lent.items()},
                first_lend_s=(first_lend - t_load) / 1000.0 if first_lend else None, aff_n=len(aff))


def summary(name, ps, warm):
    cl = ps["cl"]; ws = [s for s in cl if 0 <= s < warm]; ms = [s for s in cl if s >= warm]
    stall = lambda ss: [s for s in ss if cl[s]["done"] == 0 and cl[s]["sent"] > 0]
    out = dict(run=name, warmup_err=sum(cl[s]["err"] for s in ws), warmup_stall_s=len(stall(ws)), warmup_inflight_max=max((cl[s]["inflight"] for s in ws), default=None),
               window_err=sum(cl[s]["err"] for s in ms), window_stall_s=len(stall(ms)), window_inflight_max=max((cl[s]["inflight"] for s in ms), default=None),
               first_stall_s=(stall(sorted(cl)) or [None])[0], first_lend_s=ps["first_lend_s"],
               lent_at_load=ps["lent"].get(0), lent_mean_first10=float(np.mean([ps["lent"][s] for s in range(0, 10) if s in ps["lent"]])) if any(s in ps["lent"] for s in range(10)) else None)
    fl = ps["first_lend_s"]
    if fl is not None:
        near = [s for s in cl if fl - 2 <= s <= fl + 15]
        out.update(stall_near_lend_s=len(stall(near)), err_near_lend=sum(cl[s]["err"] for s in near), inflight_max_near_lend=max((cl[s]["inflight"] for s in near), default=None))
    return out


def outcome(r, ctrl=None):
    """RESULTS-K 4.1b's three outcomes for stall7, read only when the same-session control (res1x7: the shipped Balancer,
    lending before the load) reproduces the stall; otherwise the run cannot decide"""
    if r["run"] != "stall7": return ""
    if ctrl is not None and ctrl["warmup_stall_s"] == 0 and ctrl["window_stall_s"] == 0:
        return (f"not decided: the control res1x7 (lending {ctrl['lent_at_load']:.1f} cores when the load arrived) did not stall either "
                f"({ctrl['warmup_err']} warm-up errors, no second without completions); stall7 alone would read A")
    if r["warmup_stall_s"] > 0 and (r["first_lend_s"] is None or r["first_stall_s"] < r["first_lend_s"]): return "B: a stall at the load start anyway; lending is not the trigger"
    if r.get("stall_near_lend_s"): return "C: a stall when lending begins; taking cores away under load trips it too"
    if r["warmup_stall_s"] == 0 and r["warmup_err"] <= 120: return "A: no stall, warm-up errors at the burner-only / HC-only level; lending before the load is the trigger"
    return "none of the three as stated: no stall second, but warm-up errors above 120"


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("data"); ap.add_argument("runs"); ap.add_argument("--out", default="results/k45")
    a = ap.parse_args(); os.makedirs(a.out, exist_ok=True)
    segs = {s["name"]: s for s in jl(os.path.join(a.data, "segments.jsonl"))}
    rows, secs, P = [], [], {}
    for name in RUNS7:
        if name not in segs: continue
        s = segs[name]; ps = per_second(a.data, a.runs, s["seg"]); P[name] = ps; warm = s.get("warm", 30)
        r = summary(name, ps, warm); r["outcome"] = outcome(r, next((x for x in rows if x["run"] == "res1x7"), None))
        r["balancer_bin"] = s.get("balancer_bin"); r["reserve"] = s.get("reserve"); rows.append(r)
        print({k: (round(v, 2) if isinstance(v, float) else v) for k, v in r.items()})
        keep = set(range(-2, 121))
        if ps["first_lend_s"] is not None: keep |= set(range(int(ps["first_lend_s"]) - 5, int(ps["first_lend_s"]) + 20))
        for t in sorted(keep):
            c = ps["cl"].get(t); sv = ps["srv"].get(t); pr = ps["pr"].get(t)
            if c is None and sv is None: continue
            secs.append(dict(run=name, s_from_load=t, phase=("warm-up" if t < warm else "window"), client_done=c and c["done"], client_err=c and c["err"],
                             client_inflight=c and c["inflight"], primary_arrivals=sv and sv[0], primary_completions=sv and sv[1],
                             primary_cpu_cores=pr and round(pr[0], 3), primary_rq_wait_ms=pr and round(pr[1], 1), burner_on_primary_cores=pr and round(pr[2], 3),
                             lent_cores=ps["lent"].get(t)))
    for fn, rr in (("k45_stall.csv", rows), ("k45_stall_seconds.csv", secs)):
        if not rr: continue
        keys = []
        for r in rr:
            for k in r:
                if k not in keys: keys.append(k)
        with open(os.path.join(a.out, fn), "w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=keys); w.writeheader(); w.writerows(rr)
    if P: figure(P, segs, a.out)


def figure(P, segs, outdir):
    import matplotlib; matplotlib.use("Agg"); import matplotlib.pyplot as plt
    col = {"T7": "#1D9E75", "res1x7": "#D85A30", "free7": "#2A6F97", "stall7": "#1B2430"}
    names = [n for n in RUNS7 if n in P]
    fig, ax = plt.subplots(len(names), 2, figsize=(14, 1.9 * len(names) + 1.0), sharex=True, squeeze=False)
    for i, n in enumerate(names):
        ps = P[n]; ts = sorted(t for t in ps["cl"] if -2 <= t <= 150); lt = sorted(t for t in ps["lent"] if -5 <= t <= 150)
        what = "standalone" if segs[n]["mode"] == "S" else "reserve " + segs[n]["reserve"] + ", " + ("60 s Observation Phase Balancer" if "obs" in segs[n].get("balancer_bin", "") else "shipped Balancer")
        a0, a1 = ax[i, 0], ax[i, 1]
        a0.plot(ts, [ps["cl"][t]["done"] / 1000.0 for t in ts], color=col[n], lw=1.2); a0.set_ylim(0, 14); a0.set_ylabel("thousand / s")
        a0.set_title(f"{n} ({what}): client completions per second", fontsize=9, loc="left")
        a1.plot(lt, [ps["lent"][t] for t in lt], color=col[n], lw=1.2, drawstyle="steps-post"); a1.set_ylim(-0.2, 4); a1.set_ylabel("cores")
        if not lt: a1.text(0.5, 0.5, "HC unloaded: nothing lent", transform=a1.transAxes, ha="center", va="center", fontsize=9, color="#555555")
        a1.set_title(f"{n}: Primary cores lent to the burner (sampler, mean per second)", fontsize=9, loc="left")
        for a_ in (a0, a1): a_.axvline(segs[n].get("warm", 30), color="#888888", lw=0.8, ls=":")
    for a_ in ax[-1]: a_.set_xlabel("seconds from the start of the load (hrgen's warm-up; dotted: the measured window starts)")
    fig.suptitle("Fig. K4-5c — the 7 000 req/s start: client completions and HC's lending per second (RESULTS-K 4.1b's stall test)", x=0.01, ha="left", fontsize=10)
    fig.subplots_adjust(left=0.05, right=0.99, top=0.92, bottom=0.08, hspace=0.45, wspace=0.12); fig.savefig(os.path.join(outdir, "figK4-5c.png"), dpi=130); plt.close(fig)


if __name__ == "__main__":
    main()
