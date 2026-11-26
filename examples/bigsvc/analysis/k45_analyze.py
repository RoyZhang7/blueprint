#!/usr/bin/env python3
"""Phase K4.5: Gordion's scorer (Phase G, the Addendum C.1 rule) on HC's interference at node-4.

Runs (k4/queue-k45.sh): D, the dictionary sweep (standalone, 1 000 -> 12 000 req/s in 45 s steps); S0 and S1, the 5 000
req/s twins before and after the arms; HC + burner at reserves 2, 3 and 1 (5 000 req/s); T7, the 7 000 req/s twin; reserve
1 at 7 000 (res1x7); the free controller, TIC 3 and LOW_TIC 1, at 7 000 (free7); stall7, reserve 1 at 7 000 with the 60 s
Observation Phase Balancer, when it ran.

prep    per run, an analysis directory in the layout load_run reads: recommendation.jsonl (the Primary's recorder windows of
        the run, cut on node-0 by k45split.py), meta.json, wlgen.jsonl, and probe.jsonl = the lean probe's 100 ms records
        with usage_usec added (the Primary pod's cgroup CPU from k4sample.py's 1 s pcg, interpolated to each record's time),
        which g2_probe.py turns into U. Nothing in the data directory is changed.
score   the Phase G chain with GORDION_SVC=recommendation, as Phase G iii: r3_costs.py on D (--lo 0.03 --variant average);
        b4_compare.py --server --smooth 5 on every run with D as the dictionary; g2_probe.py; g2_verdict.py --k 4 --slots 4
        (the gate is off) --go --aggr-name hc-secondary-burner per run against its twin. Thresholds come from the twin's
        seconds; the truth per second is (Lat - p50_twin) / Lat with the twin's pooled step p50 as the baseline, as iii's
        step baseline. The 5 000 req/s arms against S0 and, as the check, S1; the 7 000 req/s runs against T7; the twins
        against each other.
d90     the p50 and p90 dictionaries of g_d90.py (build / per_second / score) on every scored run against its twin, on the
        settled seconds of the measured window.
report  per run: verdict rows and the amount E per served second, the holder named, phi against cores lent / 4, p50 and p99
        ratios; RESULTS-K 4.5's expected readings beside the measured ones; the free controller's timeline (target from
        dynamic.log, phi, E and the verdict per second) and the verdict by the target in force.

usage: k45_analyze.py DATA RUNS [--out results/k45] [--skip-chain]
  DATA  the unpacked ~/k45-data.tgz (or ~/k45-early.tgz: early/<seg>-recommendation.jsonl.gz instead of <seg>/...)
  RUNS  where the analysis directories go (large: the recorder windows of every run)
Writes OUT/costs-recommendation-K45-avg.json, OUT/service_dict.json, k45_segments.csv, k45_verdicts.csv, k45_expected.csv,
k45_free7_timeline.csv, k45_free7_by_target.csv, k45_d90.csv, figK4-5a.png and figK4-5b.png.
"""
import argparse, bisect, csv, gzip, json, os, shutil, subprocess, sys
from collections import Counter
import numpy as np
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
from k4_analyze import client, sampler, jl
from k4b_analyze import extra
from k42f_analyze import clock_offset, adjustments, summarise

P = [4, 6, 8, 10]; K = len(P)
AGGR = "hc-secondary-burner"
ENV = dict(os.environ, GORDION_SVC="recommendation", GORDION_NOMINAL_MHZ="2000", PYTHONIOENCODING="utf-8")   # node-4 pinned at 2.0 GHz; no clock normalisation (GORDION_FREQ_NORM 0)
# run -> twins it is scored against (the first is the primary comparison)
TWINS = {"res1": ["S0", "S1"], "res2": ["S0", "S1"], "res3": ["S0", "S1"], "S1": ["S0"], "S0": ["S1"],
         "res1x7": ["T7"], "free7": ["T7"], "stall7": ["T7"]}
# RESULTS-K 4.5, the expected readings (from K4.2e: the same configuration and probe; twins' p50 0.125 ms), and its
# "where row 1 would fire" line for 7 000 req/s at reserve 1 (K4.1b's r07-H)
EXPECTED = {
    "res1": dict(phi=0.37, lent4=0.45, p50=1.15, E=0.13, p99_a=1.72, p99_b=1.60, Rself=500, Rforeign=33, row="6 on most seconds"),
    "res2": dict(phi=0.23, lent4=0.24, p50=1.06, E=0.06, p99_a=1.32, p99_b=1.23, Rself=237, Rforeign=12, row="6"),
    "res3": dict(phi=0.11, lent4=0.10, p50=1.02, E=0.02, p99_a=1.10, p99_b=1.03, Rself=144, Rforeign=3, row="6"),
    "res1x7": dict(phi=0.33, p50=1.28, E=0.22, row="1"),
}


def fnum(x):
    try:
        v = float(x); return v if np.isfinite(v) else None
    except Exception: return None


def windows_of(data, segs):
    """the measured window per run, as k4reduce.py: [start of hrgen step 0, last per-second record of the schedule], ms"""
    p = os.path.join(data, "windows.json")
    if os.path.exists(p): return {w["seg"]: w for w in json.load(open(p)) if "#" not in w["seg"]}
    out = {}
    for s in segs:
        t0 = t1 = None
        for r in jl(os.path.join(data, s["seg"], "wlgen.jsonl")):
            if r.get("event") == "step" and r.get("step") == 0 and t0 is None: t0 = r["t"]
            if "all" in r and r.get("step", -1) >= 0 and r.get("rate", 0) > 0: t1 = r["t"]
        out[s["seg"]] = dict(seg=s["seg"], name=s["name"], mode=s["mode"], rate=s["rate"], t0_ms=t0, t1_ms=t1)
    return out


# ----------------------------------------------------------------------------------------------------------- prep
def prep(data, runs, s):
    seg = s["seg"]; src = os.path.join(data, seg); dst = os.path.join(runs, seg)
    gz = [p for p in (os.path.join(src, "recommendation.jsonl.gz"), os.path.join(data, "early", seg + "-recommendation.jsonl.gz")) if os.path.exists(p)]
    if not gz: print(f"prep {s['name']}: no recorder file, skipped"); return None
    os.makedirs(dst, exist_ok=True); rec = os.path.join(dst, "recommendation.jsonl")
    if not os.path.exists(rec) or os.path.getmtime(rec) < os.path.getmtime(gz[0]):
        with gzip.open(gz[0], "rt") as fi, open(rec, "w") as fo: shutil.copyfileobj(fi, fo)
    shutil.copyfile(os.path.join(src, "wlgen.jsonl"), os.path.join(dst, "wlgen.jsonl"))
    meta = dict(run=seg, testbed="hotelreservation", sut="recommendation", node="node-4", cpuset=",".join(map(str, P)), tag="gordion7",
                window_ms=100, raw="1", gate="0", gate_cap="", stressor=("hc-secondary-burner" if s["mode"] == "H" else "none"), k45=s)
    json.dump(meta, open(os.path.join(dst, "meta.json"), "w"), indent=1)
    smp = [(x["t"], x["pcg"]) for x in jl(os.path.join(src, "sample.jsonl")) if x.get("kind") == "sec" and x.get("pcg") is not None]
    ts = np.array([a for a, _ in smp], float); us = np.array([b for _, b in smp], float); n = m = 0
    with open(os.path.join(dst, "probe.jsonl"), "w") as fo:
        for line in open(os.path.join(src, "probe.jsonl")):
            try: o = json.loads(line)
            except Exception: continue
            if "Rself" in o:
                n += 1
                if len(ts) >= 2 and ts[0] <= o["t"] <= ts[-1]: o["usage_usec"] = float(np.interp(o["t"], ts, us)); m += 1
            fo.write(json.dumps(o) + "\n")
    print(f"prep {s['name']}: {os.path.getsize(rec) / 1e6:.0f} MB of recorder windows, {n} probe windows ({m} with usage_usec from {len(ts)} sampler seconds)")
    return dst


# ----------------------------------------------------------------------------------------------------------- chain
def call(script, args, log):
    with open(log, "w", encoding="utf-8") as fh:
        p = subprocess.run([sys.executable, os.path.join(HERE, script)] + args, stdout=fh, stderr=subprocess.STDOUT, env=ENV)
    if p.returncode: raise SystemExit(f"{script} failed ({p.returncode}), see {log}")


def baseline(rd, costs):
    """the twin's pooled p50 per step over its settled windows (Phase G iii's step baseline)"""
    from analyze import load_run
    from dictionary import fit_service_dict
    SD = fit_service_dict(load_run(rd, svc="recommendation"), 5.0, costs, wait_rule=False)
    b = {str(k): float(v["p50"]) for k, v in SD.steps.items() if np.isfinite(v["p50"])}
    json.dump(b, open(os.path.join(rd, "baseline.json"), "w")); return b


def chain(runs, segs, by, out, skip):
    D = by.get("D")
    if D is None: raise SystemExit("no D run")
    Dd = os.path.join(runs, D["seg"]); costs = os.path.join(out, "costs-recommendation-K45-avg.json")
    if not skip or not os.path.exists(costs):
        call("r3_costs.py", [Dd, "--svc", "recommendation", "--lo", "0.03", "--variant", "average", "--out-costs", costs], os.path.join(Dd, "r3_costs.txt"))
    for s in segs:
        rd = os.path.join(runs, s["seg"])
        if not os.path.exists(os.path.join(rd, "recommendation.jsonl")): continue
        if not (skip and os.path.exists(os.path.join(rd, "b4g2", "b4_windows.csv"))):
            os.makedirs(os.path.join(rd, "b4g2"), exist_ok=True)
            call("b4_compare.py", [rd, "--dict", Dd, "--costs", costs, "--server", "--smooth", "5", "--out", os.path.join(rd, "b4g2")], os.path.join(rd, "b4g2", "b4.txt"))
        if not (skip and os.path.exists(os.path.join(rd, "g2", "g2_windows.csv"))):
            os.makedirs(os.path.join(rd, "g2"), exist_ok=True)
            call("g2_probe.py", [rd, "--svc", "recommendation"], os.path.join(rd, "g2", "g2_probe.txt"))
        print(f"chain {s['name']}: b4 + g2 done")
    shutil.copyfile(os.path.join(Dd, "b4g2", "service_dict.json"), os.path.join(out, "service_dict.json"))
    base = {}
    for name in sorted({t for v in TWINS.values() for t in v}):
        if name in by and os.path.exists(os.path.join(runs, by[name]["seg"], "recommendation.jsonl")):
            base[name] = baseline(os.path.join(runs, by[name]["seg"]), costs); print(f"baseline {name}: {base[name]}")
    pairs = []
    for name, tw in TWINS.items():
        if name not in by or not os.path.exists(os.path.join(runs, by[name]["seg"], "g2", "g2_windows.csv")): continue
        for t in tw:
            if t not in base: continue
            A = os.path.join(runs, by[name]["seg"]); T = os.path.join(runs, by[t]["seg"]); o = os.path.join(A, f"g2-vs-{t}"); os.makedirs(o, exist_ok=True)
            call("g2_verdict.py", ["--b4", os.path.join(A, "b4g2", "b4_windows.csv"), "--g2", os.path.join(A, "g2", "g2_windows.csv"),
                                   "--twin-g2", os.path.join(T, "g2", "g2_windows.csv"), "--twin-b4", os.path.join(T, "b4g2", "b4_windows.csv"),
                                   "--dict", os.path.join(Dd, "b4g2", "service_dict.json"), "--k", str(K), "--slots", str(K), "--go",
                                   "--aggr-name", AGGR, "--baseline", os.path.join(T, "baseline.json"), "--label", f"{name} vs {t}", "--out", o],
                 os.path.join(o, "verdict.txt"))
            pairs.append((name, t, o)); print(f"verdict {name} vs {t}: {o}")
    return costs, pairs


# ----------------------------------------------------------------------------------------------------------- summaries
def vrows(o):
    return list(csv.DictReader(open(os.path.join(o, "verdict.csv")))) if os.path.exists(os.path.join(o, "verdict.csv")) else []


def vsum(o):
    R = vrows(o)
    if not R: return {}
    thr = json.load(open(os.path.join(o, "verdict_thresholds.json")))
    col = lambda k: np.array([fnum(r[k]) if fnum(r[k]) is not None else np.nan for r in R], float)
    E, tr, y, phi, Rs, Rf, Ig = col("E"), col("truth_ext"), col("y"), col("phi_cpuset"), col("R_self_ms"), col("R_foreign_ms"), col("I_g")
    rows = Counter(int(r["row"]) for r in R)
    q = lambda v, p: float(np.nanpercentile(v, p)) if np.isfinite(v).any() else None
    out = dict(n=len(R), **{f"row{k}": rows.get(k, 0) for k in range(1, 8)}, row1_share=rows.get(1, 0) / len(R),
               E_p10=q(E, 10), E_p25=q(E, 25), E_med=q(E, 50), E_p75=q(E, 75), E_p90=q(E, 90), E_far=thr.get("E_far"), E_ok=thr.get("E_ok"),
               E_twin_med=thr.get("E_med"), E_twin_iqr=thr.get("E_iqr"), truth_med=q(tr, 50), slow_share=float(np.nanmean(y >= 0.5)),
               phi_mean=float(np.nanmean(phi)) if np.isfinite(phi).any() else None, phi_med=q(phi, 50), phi_far_share=float(np.nanmean(phi > 0.05)) if np.isfinite(phi).any() else 0.0,
               R_self_med=q(Rs, 50), R_self_mean=float(np.nanmean(Rs)), R_foreign_med=q(Rf, 50), R_foreign_mean=float(np.nanmean(Rf)), R_self_twin_med=thr.get("R_self_twin_med"),
               R_far=thr.get("R_far"), I_g_med=q(Ig, 50), R_self_far=sum(int(r["R_self_far"]) for r in R),
               holder_row1="; ".join(f"{k or '(none)'} x{v}" for k, v in Counter(r["holder_cgroup"] for r in R if r["row"] == "1").most_common()),
               why7="; ".join(f"{k} x{v}" for k, v in Counter(r["why7"] for r in R if r["row"] == "7").most_common()))
    return out


def ideal(o, b4csv, base):
    """the C.1 rule with the dictionary replaced by the twin: E_twin = (Lat_s - p50_twin) / Lat_s on the same smoothed
    latency, row 1 where the second is slow, E_twin >= max(3 x the twin's E IQR, 0.15) and phi > 0.05"""
    R = vrows(o)
    if not R or not os.path.exists(b4csv): return {}
    thr = json.load(open(os.path.join(o, "verdict_thresholds.json"))); far = max(3 * (thr.get("E_iqr") or 0.0), 0.15)
    ls = {int(r["t"]): fnum(r["lat_s_ms"]) for r in csv.DictReader(open(b4csv))}; p = base.get("0")
    Et, r1 = [], 0
    for r in R:
        l = ls.get(int(r["t"]))
        if l is None or p is None: continue
        e = min(1.0, max(0.0, (l - p) / l)); Et.append(e)
        r1 += int((fnum(r["y"]) or 0) >= 0.5 and e >= far and (fnum(r["phi_cpuset"]) or 0) > 0.05)
    return dict(E_vs_twin_med=float(np.median(Et)) if Et else None, E_vs_twin_far=far, row1_if_D_were_twin=r1,
                D_med_ms=float(np.nanmedian([fnum(r["D_mix_ms"]) or np.nan for r in R])), twin_p50_ms=p)


ROWCOL = {1: "#D85A30", 6: "#9AA5B1"}; OTHERCOL = "#2A6F97"


def verdict_panel(ax, ts, E, rows, thr, twin, label_rows=True):
    """E per second as a thin line, each second's verdict as a marker on it, and the twin's band (E ~ 0, up to the row-1
    threshold) shaded with the threshold on its top edge: the amount and the verdict on one axis"""
    ax.axhspan(0, thr, color="#E8EBEE", lw=0, zorder=0)
    ax.axhline(thr, color="#1B2430", lw=0.9, zorder=1)
    ax.plot(ts, E, color="#1B2430", lw=0.7, alpha=0.55, zorder=2)
    for k in sorted(set(rows)):
        m = [r == k for r in rows]
        ax.scatter([t for t, x in zip(ts, m) if x], [e for e, x in zip(E, m) if x], s=9, color=ROWCOL.get(k, OTHERCOL), lw=0, zorder=3,
                   label=(f"row {k}" if k in ROWCOL else "other rows (2–5, 7)") if label_rows else None)


def band_handles(plt):
    return {"the twin's band, E ≈ 0 (row 6)": plt.Rectangle((0, 0), 1, 1, color="#E8EBEE"),
            "row-1 threshold: the twin's median E + max(3 IQR, 0.15)": plt.Line2D([], [], color="#1B2430", lw=0.9)}


def figure_a(vr, outdir):
    import matplotlib; matplotlib.use("Agg"); import matplotlib.pyplot as plt
    order = [x for x in ("S0", "S1", "res3", "res2", "res1", "T7", "res1x7", "free7", "stall7") if x in vr]
    emax = max((fnum(r["E"]) or 0.0) for n in order for r in vr[n][0])
    fig, axs = plt.subplots(len(order), 1, figsize=(13, 1.35 * len(order) + 1.2), sharex=True)
    axs = np.atleast_1d(axs); seen = {}
    for ax, name in zip(axs, order):
        R, twin, sm = vr[name]
        t0 = int(R[0]["t"]); ts = [int(r["t"]) - t0 for r in R]; E = [fnum(r["E"]) or 0.0 for r in R]; rows = [int(r["row"]) for r in R]
        verdict_panel(ax, ts, E, rows, sm.get("E_far") or 0.15, twin)
        ax.set_ylim(-0.01, max(0.4, emax + 0.03)); ax.set_ylabel("E", fontsize=8)
        ax.set_title(f"{name} against {twin}: row 1 on {sm['row1']} of {sm['n']} s; E median {sm['E_med']:.2f}, row-1 threshold {sm.get('E_far') or 0.15:.3f}; φ {sm['phi_mean'] or 0:.2f}",
                     fontsize=8.5, loc="left")
        for h, l in zip(*ax.get_legend_handles_labels()): seen.setdefault(l, h)
    seen.update(band_handles(plt))
    axs[0].legend(seen.values(), seen.keys(), fontsize=7, loc="upper right", ncol=3)
    axs[-1].set_xlabel("seconds of the measured window (scored seconds; 5 s settle)")
    fig.suptitle("Fig. K4-5a — the scorer's amount E per served second and its verdict, every run (C.1 rule; φ = the burner's share of cores 4–10)",
                 x=0.01, ha="left", fontsize=10)
    fig.subplots_adjust(left=0.05, right=0.99, top=0.95, bottom=0.05, hspace=0.42); fig.savefig(os.path.join(outdir, "figK4-5a.png"), dpi=130); plt.close(fig)


def changes(path, t_end):
    """the reserve target per change point (k45_free7_target.csv: raw_s, wall_s, target, reconstructed from balancer.log
    as idleCoresCount - coreDifference, main.c:175/191), as adjustments() tuples (wall_t, seconds in force, target, NaN)"""
    R = [(float(r["wall_s"]), int(r["target"])) for r in csv.DictReader(open(path))]
    return [(t, (R[i + 1][0] if i + 1 < len(R) else t_end) - t, v, float("nan")) for i, (t, v) in enumerate(R)]   # no deficit rate in balancer.log


def free_timeline(runs, data, seg, w, off, o, alt=None):
    """per scored second of the free controller: the target in force (dynamic.log), phi, E, the verdict"""
    allt = adjustments(os.path.join(data, seg), off) or (changes(alt, w["t1_ms"] / 1000.0) if alt else []); ts = [a[0] for a in allt]
    R = vrows(o); out = []
    for r in R:
        t = int(r["t"]); i = bisect.bisect_right(ts, t + 0.5) - 1          # in force at the middle of the second
        tgt = allt[i][2] if i >= 0 else None
        out.append(dict(t=t, t_from_window_s=t - w["t0_ms"] / 1000.0, target=tgt, phi=fnum(r["phi_cpuset"]), E=fnum(r["E"]), row=int(r["row"]),
                        lat_ms=fnum(r["lat_ms"]), truth=fnum(r["truth_ext"]), y=fnum(r["y"]), R_self_ms=fnum(r["R_self_ms"])))
    return allt, out


def by_target(tl, lags=(0, 1, 2, 3, 4, 5)):
    """the verdict and E grouped by the target in force L seconds before (the 5 s smoothing lags the latency)"""
    tg = {x["t"]: x["target"] for x in tl}; res = []
    for L in lags:
        g = {}
        for x in tl:
            v = tg.get(x["t"] - L)
            if v is None: continue
            d = g.setdefault(v, [0, 0, []]); d[0] += 1; d[1] += int(x["row"] == 1); d[2].append(x["E"] if x["E"] is not None else np.nan)
        for v, (n, r1, E) in sorted(g.items()):
            res.append(dict(lag_s=L, target=v, seconds=n, row1=r1, row1_share=r1 / n, E_med=float(np.nanmedian(E)), E_mean=float(np.nanmean(E))))
    return res


def figure_b(allt, tl, w, s, outdir, label, source="dynamic.log"):
    import matplotlib; matplotlib.use("Agg"); import matplotlib.pyplot as plt
    t0 = w["t0_ms"] / 1000.0
    fig, ax = plt.subplots(3, 1, figsize=(13, 8.0), sharex=True, gridspec_kw=dict(height_ratios=[1, 1, 1.6], hspace=0.3))
    xs = [a[0] - t0 for a in allt]; ys = [a[2] for a in allt]
    ax[0].step(xs, ys, where="post", color="#D85A30", lw=1.2); ax[0].set_ylim(0.5, 3.5); ax[0].set_yticks([1, 2, 3]); ax[0].set_ylabel("target")
    ax[0].set_title(f"(a) the reserve target in force (floor 1, cap 3), from {source}", fontsize=9, loc="left")
    tt = [x["t_from_window_s"] for x in tl]
    ax[1].plot(tt, [x["phi"] for x in tl], color="#2A6F97", lw=1.0); ax[1].axhline(0.05, color="#1B2430", lw=0.9, ls=":")
    ax[1].set_ylabel("φ"); ax[1].set_title("(b) φ: the burner's share of cores 4–10 per second (probe; dotted: 0.05, φ ≫ 0 above it)", fontsize=9, loc="left")
    verdict_panel(ax[2], tt, [x["E"] or 0.0 for x in tl], [x["row"] for x in tl], s.get("E_far") or 0.15, "T7")
    ax[2].plot(tt, [x["truth"] for x in tl], color="#1D9E75", lw=0.9, ls="--", label="truth = (Lat − twin p50) ÷ Lat, per second", zorder=2)
    h, l = ax[2].get_legend_handles_labels(); hb = band_handles(plt)
    ax[2].set_ylabel("share of Lat"); ax[2].legend(h + list(hb.values()), l + list(hb.keys()), fontsize=7.5, loc="upper right", ncol=2)
    ax[2].set_title(f"(c) the amount E per second (5 s causal smoothing) and the verdict on each second, against the T7 twin (row-1 threshold {s.get('E_far') or 0.15:.3f})",
                    fontsize=9, loc="left")
    for a_ in ax:
        for x0, x1 in floor_spans(allt, t0): a_.axvspan(x0, x1, color="#D85A30", alpha=0.08, lw=0)
    ax[2].set_xlabel("seconds from the start of the measured window (shaded: target 1, the floor)"); ax[2].set_xlim(0, (w["t1_ms"] - w["t0_ms"]) / 1000.0)
    fig.suptitle(f"Fig. K4-5b — {label}: HC's free reserve controller at 7 000 req/s, and what the scorer reads each second", x=0.01, ha="left", fontsize=10)
    fig.subplots_adjust(left=0.06, right=0.99, top=0.93, bottom=0.07); fig.savefig(os.path.join(outdir, "figK4-5b.png"), dpi=130); plt.close(fig)


def floor_spans(allt, t0):
    sp = []; cur = None
    for t, _, v, _ in allt:
        if v == 1 and cur is None: cur = t
        elif v != 1 and cur is not None: sp.append((cur - t0, t - t0)); cur = None
    if cur is not None and allt: sp.append((cur - t0, allt[-1][0] - t0))
    return sp


# ----------------------------------------------------------------------------------------------------------- d90
def d90(runs, by, costs):
    from analyze import load_run
    from dictionary import settled_windows
    import g_d90
    dct = g_d90.build(load_run(os.path.join(runs, by["D"]["seg"]), svc="recommendation"), costs, 5.0)
    cache = {}
    def settled(name):
        if name not in cache:
            R = load_run(os.path.join(runs, by[name]["seg"]), svc="recommendation"); W, _, _ = settled_windows(R, 5.0); cache[name] = dict(R, windows=W)
        return cache[name]
    res = []
    for name, tw in TWINS.items():
        if name not in by or not os.path.exists(os.path.join(runs, by[name]["seg"], "recommendation.jsonl")): continue
        t = tw[0]
        if t not in by or not os.path.exists(os.path.join(runs, by[t]["seg"], "recommendation.jsonl")): continue
        for q in g_d90.QS:
            sc = g_d90.score(g_d90.per_second(settled(name), dct, q), g_d90.per_second(settled(t), dct, q), dct, q, 5.0)
            ok = [o for o in sc if o["truth"] is not None and np.isfinite(o["truth"])]
            e = np.array([o["ext"] for o in ok]); tr = np.array([o["truth"] for o in ok]); g = np.array([o["guard"] for o in ok])
            res.append(dict(run=name, twin=t, q=q, seconds=len(ok), lat_med_ms=float(np.median([o["lat"] for o in ok])), D_med_ms=float(np.median([o["D"] for o in ok])),
                            ext_med=float(np.median(e)), ext_p90=float(np.percentile(e, 90)), truth_med=float(np.median(tr)), ext_ge_05=float(np.mean(e >= 0.5)),
                            truth_ge_05=float(np.mean(tr >= 0.5)), slow_share=float(np.mean([o["y"] >= 0.5 for o in ok])), guard_share=float(np.mean(g)),
                            err_med=float(np.median(e - tr)), within_015=float(np.mean(np.abs(e - tr) <= 0.15))))
            print("d90", {k: (round(v, 3) if isinstance(v, float) else v) for k, v in res[-1].items()})
        cache.pop(name, None)
    return res


# ----------------------------------------------------------------------------------------------------------- main
def wcsv(path, rows):
    keys = []
    for r in rows:
        for k in r:
            if k not in keys: keys.append(k)
    with open(path, "w", newline="", encoding="utf-8") as fh:
        wr = csv.DictWriter(fh, fieldnames=keys, extrasaction="ignore"); wr.writeheader()
        for r in rows: wr.writerow({k: (round(v, 5) if isinstance(v, float) else v) for k, v in r.items()})


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("data"); ap.add_argument("runs"); ap.add_argument("--out", default="results/k45")
    ap.add_argument("--skip-chain", action="store_true", help="reuse b4g2/ and g2/ outputs already in RUNS")
    ap.add_argument("--free7-target", default=None, help="free7's reserve target per change point, from balancer.log (its dynamic.log was not written)")
    a = ap.parse_args(); os.makedirs(a.out, exist_ok=True); os.makedirs(a.runs, exist_ok=True)
    segs = jl(os.path.join(a.data, "segments.jsonl")); by = {s["name"]: s for s in segs}; W = windows_of(a.data, segs)
    for s in segs: prep(a.data, a.runs, s)
    costs, pairs = chain(a.runs, segs, by, a.out, a.skip_chain)
    # the segment table (as K4.2f's), with the twins per run
    rp = os.path.join(a.data, "recorder_reduced.json"); rec = json.load(open(rp)) if os.path.exists(rp) else {}
    bp = os.path.join(a.data, "balancer_reduced.json"); bal = json.load(open(bp)) if os.path.exists(bp) else {}
    off = clock_offset(a.data) if os.path.exists(os.path.join(a.data, "node4-clocks.txt")) else None
    rows = []
    for s in segs:
        seg = s["seg"]; w = W[seg]; d = os.path.join(a.data, seg); rr = rec.get(seg, {})
        r = dict(seg=seg, name=s["name"], mode=s["mode"], tic=s.get("tic"), low_tic=s.get("low_tic"), rate=s["rate"], schedule=s.get("schedule"),
                 balancer_bin=s.get("balancer_bin"), window_s=(w["t1_ms"] - w["t0_ms"]) / 1000.0 if w.get("t1_ms") else None,
                 srv_rps=rr["requests"] / rr["dur_s"] if rr else None, srv_err=rr.get("errors"), srv_p50_ms=rr.get("tot_p50_ms"), srv_p95_ms=rr.get("tot_p95_ms"),
                 srv_p99_ms=rr.get("tot_p99_ms"), srv_p999_ms=rr.get("tot_p999_ms"))
        sub = [rec[f"{s['name']}#{k}"] for k in range(10) if f"{s['name']}#{k}" in rec]
        for q in ("p50", "p95", "p99"):
            v = sorted(x[f"tot_{q}_ms"] for x in sub if x.get(f"tot_{q}_ms") is not None)
            if v: r[f"srv_{q}_sub_min"], r[f"srv_{q}_sub_max"] = v[0], v[-1]
        if w.get("t0_ms"): r.update(client(d, w)); r.update(sampler(d, w)); r.update(extra(d, w))
        b = bal.get(seg)
        if b: r.update(lent_bal=b["lent_mean"], rebalances_per_s=b["rebalances_per_s"], **{f"lent_share_{i}": v for i, v in enumerate(b["share"])})
        if r.get("prim_cores") is not None:
            r["unused"] = K - r["prim_cores"]
            if b and s["mode"] == "H": r["harvest"] = r["lent_bal"] / r["unused"]; r["lent4"] = r["lent_bal"] / K
        r["phi_window"] = r["pr_occ_secondary"] / K if r.get("pr_occ_secondary") is not None else None
        r["hc_cores"] = ((r.get("mon_cores") or 0) + (r.get("bal_cores") or 0)) if s["mode"] == "H" else 0.0
        if s["mode"] == "H" and off is not None:
            allt = adjustments(d, off)
            if not allt and s["name"] == "free7" and a.free7_target: allt = changes(a.free7_target, w["t1_ms"] / 1000.0); r["target_source"] = "balancer.log (dynamic.log not written)"
            tr = [x for x in allt if w["t0_ms"] / 1000.0 <= x[0] <= w["t1_ms"] / 1000.0]; sm = summarise(tr)
            for k in ("mean", "changes_per_min", "cycle_s", "n_cycles", "above_thr", "last"): r[f"target_{k}"] = sm.get(k)
            if r.get("target_source"): r["target_above_thr"] = None
            for v, x in (sm.get("share") or {}).items(): r[f"target_share_{v}"] = x
            r["target_first_30"] = "".join(str(v) for _, _, v, _ in allt[:30])
        rows.append(r)
    nm = {r["name"]: r for r in rows}
    for r in rows:
        for j, t in enumerate(TWINS.get(r["name"], [])[:2]):
            T = nm.get(t)
            if not T: continue
            for k in ("srv_p50_ms", "srv_p95_ms", "srv_p99_ms", "srv_p999_ms", "cl_p99_ms"):
                if r.get(k) and T.get(k): r[k.replace("_ms", "") + f"_vs_{t}"] = r[k] / T[k]
    wcsv(os.path.join(a.out, "k45_segments.csv"), rows)
    # the verdicts
    vout, vr = [], {}
    for name, t, o in pairs:
        s_ = vsum(o)
        if not s_: continue
        r = nm.get(name, {}); T = nm.get(t, {})
        bse = json.load(open(os.path.join(a.runs, by[t]["seg"], "baseline.json")))
        s_.update(ideal(o, os.path.join(a.runs, by[name]["seg"], "b4g2", "b4_windows.csv"), bse))
        x = dict(run=name, twin=t, rate=r.get("rate"), mode=r.get("mode"), tic=r.get("tic"), low_tic=r.get("low_tic"), **s_,
                 lent4=r.get("lent4"), phi_window=r.get("phi_window"), harvest=r.get("harvest"),
                 p50_ratio=(r["srv_p50_ms"] / T["srv_p50_ms"]) if r.get("srv_p50_ms") and T.get("srv_p50_ms") else None,
                 p99_ratio=(r["srv_p99_ms"] / T["srv_p99_ms"]) if r.get("srv_p99_ms") and T.get("srv_p99_ms") else None,
                 own_wait_ms_s=r.get("pr_wait_self_ms_s"), burner_wait_ms_s=r.get("pr_wait_sec_ms_s"), cl_err=r.get("cl_err"), cl_err_warmup=r.get("cl_err_warmup"))
        vout.append(x)
        if t == TWINS[name][0]: vr[name] = (vrows(o), t, s_)
        print("verdict", {k: (round(v, 3) if isinstance(v, float) else v) for k, v in x.items() if k in (
            "run", "twin", "n", "row1", "row6", "row2", "row3", "row4", "row7", "E_med", "E_p90", "E_far", "truth_med", "slow_share", "phi_mean", "lent4",
            "p50_ratio", "p99_ratio", "R_self_med", "R_foreign_med", "holder_row1", "D_med_ms", "twin_p50_ms", "E_vs_twin_med", "row1_if_D_were_twin")})
    wcsv(os.path.join(a.out, "k45_verdicts.csv"), vout)
    # expected against measured (RESULTS-K 4.5's table and its 7 000 req/s line)
    ex = []
    V = {(x["run"], x["twin"]): x for x in vout}
    for name, e in EXPECTED.items():
        if name not in nm: continue
        t0n = TWINS[name][0]; m = V.get((name, t0n), {}); m2 = V.get((name, TWINS[name][1]), {}) if len(TWINS[name]) > 1 else {}
        ex.append(dict(run=name, twin=t0n,
                       phi_expected=e.get("phi"), phi_measured=m.get("phi_mean"), lent4_expected=e.get("lent4"), lent4_measured=nm[name].get("lent4"),
                       p50_ratio_expected=e.get("p50"), p50_ratio_measured=m.get("p50_ratio"), E_expected=e.get("E"), E_measured_median=m.get("E_med"),
                       E_threshold=m.get("E_far"), p99_ratio_expected=e.get("p99_a"), p99_ratio_measured=m.get("p99_ratio"),
                       p99_ratio_check_expected=e.get("p99_b"), p99_ratio_check_measured=m2.get("p99_ratio"),
                       R_self_expected=e.get("Rself"), R_self_measured=nm[name].get("pr_wait_self_ms_s"), R_foreign_expected=e.get("Rforeign"),
                       R_foreign_measured=nm[name].get("pr_wait_sec_ms_s"), row_expected=e.get("row"),
                       rows_measured=f"1: {m.get('row1', 0)}, 6: {m.get('row6', 0)}, other: {m.get('n', 0) - m.get('row1', 0) - m.get('row6', 0)} of {m.get('n', 0)}"))
    wcsv(os.path.join(a.out, "k45_expected.csv"), ex)
    for x in ex: print("expected", {k: (round(v, 3) if isinstance(v, float) else v) for k, v in x.items()})
    if vr: figure_a(vr, a.out)
    # the free controller
    if "free7" in by and off is not None and "free7" in vr:
        o = os.path.join(a.runs, by["free7"]["seg"], "g2-vs-T7"); w = W[by["free7"]["seg"]]
        allt, tl = free_timeline(a.runs, a.data, by["free7"]["seg"], w, off, o, a.free7_target)
        wcsv(os.path.join(a.out, "k45_free7_timeline.csv"), tl); bt = by_target(tl); wcsv(os.path.join(a.out, "k45_free7_by_target.csv"), bt)
        for x in bt: print("free7 by target", {k: (round(v, 3) if isinstance(v, float) else v) for k, v in x.items()})
        src = "dynamic.log" if adjustments(os.path.join(a.data, by["free7"]["seg"]), off) else "balancer.log: idle cores − coreDifference per rebalance (dynamic.log was not written)"
        figure_b(allt, tl, w, vr["free7"][2], a.out, "free7 (TIC 3, LOW_TIC 1)", src)
    # the p90 dictionary
    res = d90(a.runs, by, costs)
    if res: wcsv(os.path.join(a.out, "k45_d90.csv"), res)
    print("done")


if __name__ == "__main__":
    main()
