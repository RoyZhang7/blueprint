#!/usr/bin/env python3
"""Phase K4.2f: HC's own reserve controller left free between a floor of 1 (LOW_TIC) and a cap of 3, then 4
(targetIdleCores = static_targetIdleCores = TIC, the start and the cap), at the shipped placement, equal CPU weights,
5 000 req/s; HC + burner runs cap3 (and cap4) after a standalone twin S0.

Per HC run: the target's trajectory from the Balancer's dynamic.log (one row per one-second adjustment; CLOCK_MONOTONIC_RAW
aligned to wall time by node4-clocks.txt), over the measured window its time share per value, mean, changes per minute,
dwell per value and cycle (time between arrivals at the top value); the harvest fraction (lent / unused), cores lent and
their time shares, the Secondary's use, HC's own cores, the Primary's server p50 / p95 / p99 and client p99 against S0,
its run-queue wait split own / Secondary / other, phi (the burner's share of the Primary's cores, probe Oaggr); K4.2e's
fixed reserves beside it (results/k42e/k42e_reserve.csv). Per target value in force, from the probe's 100 ms windows: the
share of the window, the Primary's wait behind its own threads and behind the burner, the burner's occupancy of the
Primary's cores. Writes k42f_segments.csv, k42f_target.csv (every adjustment), k42f_phases.csv and figK4-2f.png.
usage: k42f_analyze.py K42F_DIR [--k42e results/k42e/k42e_reserve.csv] [--out results/k42f]
"""
import argparse, csv, json, os, sys
from collections import Counter
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from k4_analyze import client, sampler, jl
from k4b_analyze import extra

P = [4, 6, 8, 10]
THR = 0.33   # LOWIDLEFREQ_THRESHOLD sent in the Primary message (k4node.sh ACBF)


def fnum(x):
    try: return float(x)
    except Exception: return None


def clock_offset(root):
    """wall - CLOCK_MONOTONIC_RAW on node-4, from the reduction's clock record."""
    for line in open(os.path.join(root, "node4-clocks.txt")):
        f = line.split()
        if f and f[0] == "clk": return float(f[1]) - float(f[3])
    raise SystemExit("no clk line in node4-clocks.txt")


def adjustments(d, off):
    """Every adjustment in the run's dynamic.log: (t_wall_s, elapsed_s, target, deficit_rate)."""
    p = os.path.join(d, "balancer", "dynamic.log")
    if not os.path.exists(p): return []
    out = []
    for r in csv.DictReader(open(p)):
        if not r.get("targetIdleCores"): continue
        out.append((int(r["startSec"]) + int(r["startNSec"]) / 1e9 + off, int(r["sample_time_elapsed"]) / 1e9,
                    int(r["targetIdleCores"]), float(r["deficit_rate"])))
    return out


def summarise(tr):
    if len(tr) < 2: return {}
    tot = sum(e for _, e, _, _ in tr); share = Counter()
    for _, e, v, _ in tr: share[v] += e / tot
    runs = []                                    # [value, seconds] per stretch at one value
    for _, e, v, _ in tr:
        if runs and runs[-1][0] == v: runs[-1][1] += e
        else: runs.append([v, e])
    inner = runs[1:-1] if len(runs) > 2 else runs   # the first and last stretches are cut by the window
    dwell = {v: sum(s for x, s in inner if x == v) / max(1, sum(1 for x, _ in inner if x == v)) for v in share}
    top = max(share)
    arr = [i for i in range(1, len(tr)) if tr[i][2] == top and tr[i - 1][2] != top]
    cycle = (tr[arr[-1]][0] - tr[arr[0]][0]) / (len(arr) - 1) if len(arr) >= 2 else None
    span = tr[-1][0] - tr[0][0]
    return dict(n_adj=len(tr), mean=sum(v * e for _, e, v, _ in tr) / tot,
                changes_per_min=60.0 * sum(1 for a, b in zip(tr, tr[1:]) if a[2] != b[2]) / span if span > 0 else None,
                cycle_s=cycle, n_cycles=max(0, len(arr) - 1), above_thr=sum(1 for *_, r in tr if r > THR) / len(tr),
                last=tr[-1][2], share={k: share[k] for k in sorted(share)}, dwell={k: dwell[k] for k in sorted(dwell)})


def phases(d, w, adj):
    """Per target value in force: the share of the window's 100 ms probe windows, the Primary's run-queue wait behind its own
    threads and behind the burner (ms/s), and the burner's occupancy of the Primary's cores (cores)."""
    import bisect
    ts = [1000.0 * a[0] for a in adj]; agg = {}
    for o in jl(os.path.join(d, "probe.jsonl")):
        if "Rself" not in o or not (w["t0_ms"] < o["t"] <= w["t1_ms"]): continue
        i = bisect.bisect_right(ts, o["t"] - 100) - 1          # the target in force over this 100 ms window
        if i < 0: continue
        g = agg.setdefault(adj[i][2], [0, 0.0, 0.0, 0.0])
        g[0] += 1; g[1] += o["Rself"]; g[2] += o.get("Raggr", 0); g[3] += o["Oaggr"]
    n = sum(g[0] for g in agg.values())
    return {v: dict(share=g[0] / n, own_wait_ms_s=g[1] / 1e6 / (g[0] / 10.0), burner_wait_ms_s=g[2] / 1e6 / (g[0] / 10.0),
                    burner_cores=g[3] / 1e9 / (g[0] / 10.0)) for v, g in sorted(agg.items())} if n else {}


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("root"); ap.add_argument("--k42e", default="results/k42e/k42e_reserve.csv")
    ap.add_argument("--out", default="results/k42f")
    a = ap.parse_args(); os.makedirs(a.out, exist_ok=True); R = a.root; off = clock_offset(R)
    W = {w["seg"]: w for w in json.load(open(os.path.join(R, "windows.json")))}
    rec = json.load(open(os.path.join(R, "recorder_reduced.json")))
    bp = os.path.join(R, "balancer_reduced.json"); bal = json.load(open(bp)) if os.path.exists(bp) else {}
    rows, traj = [], {}
    for s in jl(os.path.join(R, "segments.jsonl")):
        seg = s["seg"]; w = W[seg]; d = os.path.join(R, seg); rr = rec.get(seg, {})
        r = dict(seg=seg, name=s["name"], mode=s["mode"], tic=s.get("tic", "-"), low_tic=s.get("low_tic", "-"), rate=s["rate"],
                 window_s=(w["t1_ms"] - w["t0_ms"]) / 1000.0, srv_rps=rr["requests"] / rr["dur_s"] if rr else None,
                 srv_p50_ms=rr.get("tot_p50_ms"), srv_p95_ms=rr.get("tot_p95_ms"), srv_p99_ms=rr.get("tot_p99_ms"), srv_p999_ms=rr.get("tot_p999_ms"))
        sub = [rec[f"{s['name']}#{k}"] for k in range(10) if f"{s['name']}#{k}" in rec]
        for q in ("p50", "p95", "p99"):
            v = sorted(x[f"tot_{q}_ms"] for x in sub if x.get(f"tot_{q}_ms") is not None)
            if v: r[f"srv_{q}_sub_min"], r[f"srv_{q}_sub_max"] = v[0], v[-1]
        r.update(client(d, w)); r.update(sampler(d, w)); r.update(extra(d, w))
        b = bal.get(seg)
        if b: r.update(lent_bal=b["lent_mean"], rebalances_per_s=b["rebalances_per_s"], **{f"lent_share_{i}": v for i, v in enumerate(b["share"])})
        if r.get("prim_cores") is not None:
            r["unused"] = len(P) - r["prim_cores"]
            if b and s["mode"] == "H": r["harvest"] = r["lent_bal"] / r["unused"]
            if r.get("sec_on_p") is not None: r["used_frac"] = r["sec_on_p"] / r["unused"]
        r["phi"] = r["pr_occ_secondary"] / len(P) if r.get("pr_occ_secondary") is not None else None
        r["hc_cores"] = ((r.get("mon_cores") or 0) + (r.get("bal_cores") or 0)) if s["mode"] == "H" else 0.0
        if s["mode"] == "H":
            allt = adjustments(d, off); t0, t1 = w["t0_ms"] / 1000.0, w["t1_ms"] / 1000.0
            tr = [x for x in allt if t0 <= x[0] <= t1]; traj[s["name"]] = (allt, t0, t1)
            sm = summarise(tr)
            for k in ("n_adj", "mean", "changes_per_min", "cycle_s", "n_cycles", "above_thr", "last"): r[f"target_{k}"] = sm.get(k)
            for v, x in (sm.get("share") or {}).items(): r[f"target_share_{v}"] = x
            for v, x in (sm.get("dwell") or {}).items(): r[f"target_dwell_s_{v}"] = x
            r["target_first_30"] = "".join(str(v) for _, _, v, _ in allt[:30])
            r["phases"] = phases(d, w, allt)
        rows.append(r)
    S0 = next(r for r in rows if r["name"] == "S0")
    for r in rows:
        for k in ("srv_p50_ms", "srv_p95_ms", "srv_p99_ms", "srv_p999_ms", "cl_p99_ms"):
            r[k.replace("_ms", "") + "_vs_S0"] = (r[k] / S0[k]) if r.get(k) is not None and S0.get(k) else None
    keys = []
    for r in rows:
        for k in r:
            if k not in keys and k != "phases": keys.append(k)
    with open(os.path.join(a.out, "k42f_segments.csv"), "w", newline="") as fh:
        wr = csv.DictWriter(fh, fieldnames=keys, extrasaction="ignore"); wr.writeheader(); wr.writerows(rows)
    with open(os.path.join(a.out, "k42f_phases.csv"), "w", newline="") as fh:   # per target value in force, from the probe
        wr = csv.writer(fh); wr.writerow(["run", "target", "share_of_window", "own_wait_ms_s", "burner_wait_ms_s", "burner_cores"])
        for r in rows:
            for v, p in (r.pop("phases", None) or {}).items():
                wr.writerow([r["name"], v, round(p["share"], 4), round(p["own_wait_ms_s"], 2), round(p["burner_wait_ms_s"], 2), round(p["burner_cores"], 4)])
                print("phase", r["name"], "target", v, {k: round(x, 3) for k, x in p.items()})
    with open(os.path.join(a.out, "k42f_target.csv"), "w", newline="") as fh:   # every adjustment, both runs, t from the window start
        wr = csv.writer(fh); wr.writerow(["run", "t_from_window_s", "in_window", "target", "deficit_rate", "elapsed_s"])
        for name, (allt, t0, t1) in traj.items():
            for t, e, v, dr in allt: wr.writerow([name, round(t - t0, 3), int(t0 <= t <= t1), v, dr, round(e, 4)])
    ref = list(csv.DictReader(open(a.k42e))) if os.path.exists(a.k42e) else []
    for r in rows:
        print({k: (round(v, 3) if isinstance(v, float) else v) for k, v in r.items() if k in (
            "name", "tic", "low_tic", "harvest", "lent_bal", "lent_share_0", "lent_share_1", "lent_share_2", "lent_share_3", "sec_on_p", "phi", "hc_cores",
            "rebalances_per_s", "srv_p50_vs_S0", "srv_p95_vs_S0", "srv_p99_vs_S0", "cl_p99_vs_S0", "srv_p95_ms", "srv_p99_ms", "srv_p95_sub_min", "srv_p95_sub_max",
            "srv_p99_sub_min", "srv_p99_sub_max", "pr_wait_self_ms_s", "pr_wait_sec_ms_s", "pr_wait_other_ms_s", "target_mean", "target_changes_per_min",
            "target_cycle_s", "target_n_cycles", "target_above_thr", "target_last", "target_share_1", "target_share_2", "target_share_3", "target_share_4",
            "target_dwell_s_1", "target_dwell_s_2", "target_dwell_s_3", "target_dwell_s_4", "target_first_30", "cl_err", "cl_err_warmup")})
    for o in ref:
        if o.get("run", "").startswith("res"):
            print("K4.2e", o["run"], {k: round(fnum(o[k]), 3) for k in ("harvest", "srv_p95_vs_S0", "srv_p99_vs_S0", "pr_wait_self_ms_s") if fnum(o.get(k)) is not None})
    figure(rows, traj, ref, a.out)


def figure(rows, traj, ref, outdir):
    import matplotlib; matplotlib.use("Agg"); import matplotlib.pyplot as plt
    col = {"cap3": "#D85A30", "cap4": "#2A6F97"}
    fig = plt.figure(figsize=(14, 5.6)); gs = fig.add_gridspec(len(traj), 2, width_ratios=[1.6, 1], hspace=0.25, wspace=0.18)
    first = None
    for i, (name, (allt, t0, t1)) in enumerate(traj.items()):
        ax = fig.add_subplot(gs[i, 0], sharex=first); first = first or ax
        xs = [t - t0 for t, *_ in allt]; ys = [v for _, _, v, _ in allt]
        ax.step(xs, ys, where="post", color=col.get(name, "#555555"), lw=1.2)
        for x in (0, t1 - t0): ax.axvline(x, color="#888888", lw=0.8, ls=":")
        ax.set_yticks(range(1, max(ys) + 1)); ax.set_ylim(0.6, 4.4); ax.set_ylabel("target")
        ax.set_title(f"({'ab'[i]}) {name}: floor 1, cap {max(ys)}: the target after every one-second adjustment (dynamic.log)", fontsize=9, loc="left")
        if i < len(traj) - 1: ax.tick_params(labelbottom=False)
    first.figure.axes[-1].set_xlabel("seconds from the start of the measured window (dotted: its start and end)")
    ax = fig.add_subplot(gs[:, 1])
    res = [o for o in ref if o.get("run", "").startswith("res") and fnum(o.get("harvest")) is not None]
    for key, mk, lab in (("srv_p99_vs_S0", "D", "server p99"), ("srv_p95_vs_S0", "o", "server p95")):
        ax.plot([fnum(o["harvest"]) for o in res], [fnum(o[key]) for o in res], mk + "-", color="#9AA5B1", ms=6, lw=1, label=f"K4.2e fixed reserve 1-3, {lab}")
        for r in rows:
            if r["mode"] == "H" and r.get("harvest") is not None and r.get(key) is not None:
                ax.plot([r["harvest"]], [r[key]], mk, color=col.get(r["name"], "#555555"), ms=9, label=f"K4.2f {r['name']}, {lab}")
    for o in res:
        ax.annotate(f"reserve {o['reserve']}", (fnum(o["harvest"]), fnum(o["srv_p99_vs_S0"])), textcoords="offset points", xytext=(6, 6), fontsize=7.5, color="#555555")
    if res: ax.set_xlim(0.1, max(fnum(o["harvest"]) for o in res) + 0.12)
    ax.axhline(1.0, color="#888888", lw=0.8); ax.axhline(1.1, color="#1D9E75", lw=1.0, ls="--", label="×1.1")
    ax.set_xlabel("harvest fraction (lent ÷ unused)"); ax.set_ylabel("÷ the session's standalone twin S0")
    ax.set_title(f"({'abc'[len(traj)]}) where the free controller lands on the fixed-reserve curve", fontsize=9, loc="left"); ax.legend(fontsize=7, loc="upper left")
    fig.suptitle("Fig. K4-2f — HC's own reserve controller, floor 1 and cap 3 / 4 (5 000 req/s, equal CPU weights; one 300 s run each)", x=0.01, ha="left", fontsize=10)
    fig.subplots_adjust(left=0.05, right=0.99, top=0.88, bottom=0.1); fig.savefig(os.path.join(outdir, "figK4-2f.png"), dpi=130); plt.close(fig)
    print("figure written")


if __name__ == "__main__":
    main()
