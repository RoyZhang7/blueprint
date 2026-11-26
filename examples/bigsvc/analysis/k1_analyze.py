#!/usr/bin/env python3
"""Phase K1: per-cell, per-arm overhead metrics from the controller's records (ctl/cell.jsonl) and the victim's recorder.

Per arm-rep (one measured interval of ~60 s): for every BPF program of the arm, events/s (run_cnt), ns/event
(run_time_ns / run_cnt) and cores (run_time_ns / interval); system-wide CPU by class in cores -- victim cgroup, aggressor
cgroup, probe userspace (reader: probe.py + bpftrace or the libbpf loader), kernel / root cgroup (root usage minus its
child cgroups), other cgroups, softirq and idle (/proc/stat, all CPUs), busy cores on the victim's cpuset; victim p50 /
p95 / p99 (server-side recorder stream: synthetic = the processing stream of Phase G, SocialNetwork = tap -> end) and
throughput over the interval; victim CPU per request (cgroup usage / requests). Event rates without the probe come from
the cell's rate passes (context switches and wake-ups on the cpuset and system-wide).
Per cell and arm: mean over reps and a 95 % t-interval; for every arm other than "none", the paired difference to the
"none" arm of the same round (ABAB) with its 95 % t-interval and a flag "distinguishable" (interval excludes 0).

usage: k1_analyze.py RESULTS_K1_DIR [--out results/k1]
Writes <out>/k1_cells.csv (one row per cell x arm x metric: mean, ci_lo, ci_hi, diff, diff_lo, diff_hi, distinguishable),
<out>/k1_wide.csv (one row per cell x arm, the main metrics as columns), figures figK1-cores-vs-events.png and
figK1-p99.png.
"""
import argparse, csv, glob, json, math, os, sys
import numpy as np
sys.path.insert(0, os.path.dirname(__file__))

T95 = {1: 12.706, 2: 4.303, 3: 3.182, 4: 2.776, 5: 2.571, 6: 2.447, 7: 2.365, 8: 2.306, 9: 2.262, 10: 2.228}


def ci(xs):
    xs = [x for x in xs if x is not None and np.isfinite(x)]
    n = len(xs)
    if n == 0: return (np.nan, np.nan, np.nan, 0)
    m = float(np.mean(xs))
    if n == 1: return (m, np.nan, np.nan, 1)
    h = T95.get(n - 1, 1.96) * float(np.std(xs, ddof=1)) / math.sqrt(n)
    return (m, m - h, m + h, n)


def hook_of(p, impl, sw_ids):
    n = p["name"]
    if impl == "lb":
        return {"on_switch": "switch", "on_wakeup": "wakeup", "on_wakeup_new": "wakeup_new", "on_exit": "exit",       # K1 full libbpf probe
                "lean_switch": "switch", "lean_wakeup": "wakeup", "lean_wakeup_new": "wakeup_new",              # K1 addendum lean probe
                "e_switch": "switch", "e_wakeup": "wakeup", "e_wakeup_new": "wakeup_new"}.get(n, n)           # K1 addendum empty programs
    if n == "sched_switch": return "switch" if p["id"] == min(sw_ids) else "switch_sib"
    if n.startswith("sched_wakeup_n"): return "wakeup_new"
    if n == "sched_wakeup": return "wakeup"
    if n.startswith("sched_process_e"): return "exit"
    if n.startswith("interval"): return "export"
    return "other:" + n


def victim_lat(rec_path, victim, t0, t1):
    """per-request server-side latency samples (ms) and request count in [t0, t1] (s, unix)."""
    xs = []; n = 0
    if not os.path.exists(rec_path): return xs, 0, 0.0
    for line in open(rec_path):
        if not line.startswith('{"t"'): continue
        try: w = json.loads(line)
        except Exception: continue
        t = w["t"] / 1000.0
        if t < t0 or t + w.get("w", 100) / 1000.0 > t1: continue
        n += w.get("n", w.get("a", 0))
        raw = w.get("raw") or {}
        if victim == "syn":
            from dictionary import lat_samples
            for e in w.get("ep", {}):
                s = lat_samples(w, e)[0]
                if len(s): xs.extend((np.asarray(s) / 1e6).tolist())
        else:
            for e, v in raw.items(): xs.extend([x / 1e6 for x in v])
    return xs, n, t1 - t0


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("root"); ap.add_argument("--out", default="results/k1")
    a = ap.parse_args(); os.makedirs(a.out, exist_ok=True)
    os.environ.setdefault("GORDION_LAT", "unstamped")
    rows = []; wide = []
    runs = sorted(d for d in glob.glob(os.path.join(a.root, "2026*-k1-*")) if os.path.exists(os.path.join(d, "ctl", "cell.jsonl")) and not d.endswith("ABORTED"))
    latest = {}
    for d in runs:                         # a cell re-run supersedes the earlier run of the same cell (K1 deviation: the first queue's load ended early)
        latest[json.load(open(os.path.join(d, "spec.json")))["cell"]] = d
    superseded = sorted(set(runs) - set(latest.values())); runs = sorted(latest.values())
    if superseded: print("superseded runs (not analysed):", [os.path.basename(x) for x in superseded])
    dropped = []
    for d in runs:
        spec = json.load(open(os.path.join(d, "spec.json"))); cell = spec["cell"]
        if cell.startswith("t-"): continue
        recs = [json.loads(l) for l in open(os.path.join(d, "ctl", "cell.jsonl"))]
        arms = [r for r in recs if r["event"] == "arm"]; rates = [r for r in recs if r["event"] == "rates"]
        rate = {k: float(np.mean([r[k] for r in rates])) for k in ("cs_cpuset", "cs_all", "wake_on_cpuset", "wake_to_cpuset", "wake_all")} if rates else {}
        cpuset = [int(x) for x in spec["cpuset"].split(",")]
        rp = os.path.join(d, "lat_reduced.json"); reduced = json.load(open(rp)) if os.path.exists(rp) else None
        up = os.path.join(d, "ugen.jsonl")         # client-side view (unique-id): done/s and backlog per second
        ugen = []
        if os.path.exists(up):
            for l in open(up):
                if '"done"' not in l or '"event"' in l: continue
                try: ugen.append(json.loads(l))
                except Exception: pass               # the last line can be cut when the generator is stopped
        per = {}   # arm -> list of metric dicts (by rep)
        for r in arms:
            dur = r["dur_s"]; impl = r["arm"].split(":")[0]
            sw = [p["id"] for p in r["progs"] if p["name"] == "sched_switch"] or [0]
            m = {}
            ev = cores = 0.0; bh = {}
            for p in r["progs"]:
                h = hook_of(p, impl, sw); e = p["run_cnt"] / dur; c = p["run_time_ns"] / dur / 1e9
                bh.setdefault(h, [0.0, 0.0]); bh[h][0] += e; bh[h][1] += c
                if h != "export": ev += e
                cores += c
            for h, (e, c) in bh.items():
                m[f"ev_s:{h}"] = e; m[f"ns_ev:{h}"] = (c * 1e9 / e) if e else np.nan; m[f"cores:{h}"] = c
            m["bpf_events_s"] = ev; m["bpf_cores"] = cores; m["bpf_ns_per_event"] = (cores * 1e9 / ev) if ev else np.nan
            hz = 100.0
            st = r["stat"]; tot = lambda i, sel=None: sum(v[i] for c, v in st.items() if (sel is None or int(c) in sel)) / hz / dur
            busy_all = sum(tot(i) for i in (0, 1, 2, 5, 6, 7)); m["busy_all_cores"] = busy_all; m["idle_cores"] = tot(3) + tot(4); m["softirq_cores"] = tot(6); m["irq_cores"] = tot(5)
            m["busy_cpuset_cores"] = sum(tot(i, set(cpuset)) for i in (0, 1, 2, 5, 6, 7)); m["softirq_cpuset_cores"] = tot(6, set(cpuset))
            cg = r["cg"]; f = lambda k: (cg[k] / 1e6 / dur) if cg.get(k) is not None else np.nan
            m["victim_cores"] = f("victim"); m["aggr_cores"] = f("aggr"); m["reader_cores"] = r["reader_cpu_ns"] / 1e9 / dur
            m["kernel_root_cores"] = f("root") - f("children"); m["other_cg_cores"] = f("children") - f("victim") - f("aggr") - m["reader_cores"]
            red = reduced.get(r["arm"], {}).get(str(r["rep"])) if reduced is not None else None
            if red is not None:                       # k1/k1_reduce.py output (computed on node-0 from the recorder file)
                n, dd = red["requests"], red["dur_s"]
                m["p50_ms"], m["p95_ms"], m["p99_ms"] = ((red[k] if red[k] is not None else np.nan) for k in ("p50_ms", "p95_ms", "p99_ms"))
                m["lat_samples"] = red["n_samples"]
            else:
                xs, n, dd = victim_lat(os.path.join(d, "recorder.jsonl"), spec["victim"], r["t0"], r["t1"])
                if xs:
                    q = np.percentile(xs, [50, 95, 99]); m["p50_ms"], m["p95_ms"], m["p99_ms"] = (float(v) for v in q)
                else: m["p50_ms"] = m["p95_ms"] = m["p99_ms"] = np.nan
                m["lat_samples"] = len(xs)
            m["throughput_rps"] = n / dd if dd else np.nan
            m["cpu_per_req_ms"] = (m["victim_cores"] * 1000.0 / m["throughput_rps"]) if n else np.nan
            g = [u for u in ugen if r["t0"] * 1000 <= u["t"] < r["t1"] * 1000]
            if g:
                m["client_done_rps"] = float(np.mean([u["done"] for u in g])); m["client_backlog_growth_per_s"] = (g[-1]["backlog"] - g[0]["backlog"]) / max(1, len(g) - 1)
                m["client_lat_p99_ms"] = float(np.median([u["lat_p99_us"] for u in g])) / 1000.0
            m["attach_s"] = r["attach_s"]; m["alive"] = 1.0 if r["alive"] else 0.0
            if spec["rate"] and (m["throughput_rps"] < 0.9 * spec["rate"] or m["throughput_rps"] > 1.1 * spec["rate"]) and spec["victim"] != "ps":
                dropped.append((cell, r["arm"], r["rep"], round(m["throughput_rps"], 1)))   # load not running (or collapsed) during the interval
            per.setdefault(r["arm"], {})[r["rep"]] = m
        none = per.get("none", {})
        for arm, reps in per.items():
            keys = sorted(set(k for m in reps.values() for k in m))
            hook = "none" if arm == "none" else arm.split(":")[1] if arm.startswith("bt") else "full_both"
            fac = dict(cell=cell, run=os.path.basename(d), victim=spec["victim"], rate=spec["rate"], aggr=(spec["aggr"] or {}).get("method", "none") if spec.get("aggr") else "none",
                       aggr_cpus=(spec["aggr"] or {}).get("cpus", "") if spec.get("aggr") else "", arm=arm, impl=arm.split(":")[0], program=("none" if arm == "none" else "v5" if "v5" in arm else "lean" if "lean" in arm else "empty" if "empty" in arm else "full"),
                       hookset=("none" if arm == "none" else "switch" if arm in ("lb:lean", "lb:empty_switch") else "both" if ("both" in arm or arm.startswith("lb") or "v5" in arm) else "switch" if "switch" in arm else "wakeup"), bpf_stats=("off" if arm.endswith("nostats") else "on"))
            fac.update({f"rate_{k}": round(v, 1) for k, v in rate.items()})
            w = dict(fac)
            for k in keys:
                vals = [reps[r_].get(k) for r_ in sorted(reps)]
                mm, lo, hi, n = ci(vals)
                if arm != "none" and none:
                    diffs = [reps[r_].get(k) - none[r_].get(k) for r_ in sorted(reps) if r_ in none and reps[r_].get(k) is not None and none[r_].get(k) is not None and np.isfinite(reps[r_].get(k)) and np.isfinite(none[r_].get(k))]
                    dm, dlo, dhi, dn = ci(diffs)
                else: dm = dlo = dhi = np.nan; dn = 0
                dist = "" if not np.isfinite(dlo) else ("yes" if (dlo > 0 or dhi < 0) else "not distinguishable")
                rows.append(dict(fac, metric=k, n=n, mean=mm, ci_lo=lo, ci_hi=hi, diff_vs_none=dm, diff_lo=dlo, diff_hi=dhi, distinguishable=dist))
                w[k] = mm; w[k + "_lo"] = lo; w[k + "_hi"] = hi
                if k in ("busy_all_cores", "busy_cpuset_cores", "victim_cores", "aggr_cores", "kernel_root_cores", "other_cg_cores", "p50_ms", "p95_ms", "p99_ms", "throughput_rps", "cpu_per_req_ms", "reader_cores", "softirq_cores", "idle_cores", "client_done_rps", "client_lat_p99_ms", "bpf_cores"):
                    w["d_" + k] = dm; w["d_" + k + "_lo"] = dlo; w["d_" + k + "_hi"] = dhi; w["d_" + k + "_dist"] = dist
            wide.append(w)
    if dropped: print("arm-reps outside 0.9-1.1 x offered rate (kept, flagged):", dropped)
    if not rows: sys.exit("no cells")
    with open(os.path.join(a.out, "k1_cells.csv"), "w", newline="") as fh:
        wr = csv.DictWriter(fh, fieldnames=list(rows[0].keys())); wr.writeheader(); wr.writerows(rows)
    allk = []
    for w in wide:
        for k in w:
            if k not in allk: allk.append(k)
    with open(os.path.join(a.out, "k1_wide.csv"), "w", newline="") as fh:
        wr = csv.DictWriter(fh, fieldnames=allk); wr.writeheader(); wr.writerows(wide)
    print(f"{len(runs)} runs, {len(wide)} cell x arm rows, {len(rows)} metric rows -> {a.out}")
    figures(wide, a.out)


def figures(wide, out):
    import matplotlib; matplotlib.use("Agg"); import matplotlib.pyplot as plt
    from matplotlib.lines import Line2D
    f = lambda w, k: (float(w[k]) if w.get(k) not in (None, "", "nan") and np.isfinite(float(w[k])) else np.nan) if isinstance(w.get(k), str) else (w.get(k) if w.get(k) is not None else np.nan)
    sets = [("switch", "bt", "full", "#2A6F97", "switch hook, bpftrace, full"), ("wakeup", "bt", "full", "#D85A30", "wake-up hook, bpftrace, full"),
            ("both", "bt", "full", "#1B2430", "both hooks, bpftrace, full"), ("both", "bt", "empty", "#B8860B", "both hooks, bpftrace, empty"),
            ("both", "bt", "v5", "#7F77DD", "v5 probe (bpftrace, + per-resume export)"), ("both", "lb", "full", "#1D9E75", "both hooks, libbpf, full"),
            ("switch", "lb", "empty", "#C9A227", "switch hook, libbpf, empty"), ("both", "lb", "empty", "#8C6D1F", "both hooks, libbpf, empty"),
            ("switch", "lb", "lean", "#2E8B57", "lean libbpf, switch only"), ("both", "lb", "lean", "#0B4F3C", "lean libbpf + wake-up program")]
    mk = {"syn": "o", "uid": "s", "ps": "^"}
    fig, axs = plt.subplots(1, 2, figsize=(15, 6.2))
    for hs, impl, prog, col, lab in sets:
        pts = [w for w in wide if w["hookset"] == hs and w["impl"] == impl and w["program"] == prog and w["bpf_stats"] == "on" and np.isfinite(f(w, "bpf_events_s")) and f(w, "bpf_events_s") > 0]
        if not pts: continue
        x = np.array([f(w, "bpf_events_s") for w in pts]) / 1e3
        for ax, key in ((axs[0], "bpf_cores"), (axs[1], "d_busy_all_cores")):
            y = np.array([f(w, key) for w in pts]); ok = np.isfinite(y)
            for w, xi, yi in zip(pts, x, y):
                if np.isfinite(yi): ax.plot(xi, yi, mk[w["victim"]], color=col, ms=6, alpha=0.85)
            if ok.sum() >= 2:
                if key == "bpf_cores":   # through the origin: slope = ns per event
                    s = float(np.sum(x[ok] * y[ok]) / np.sum(x[ok] ** 2)); xx = np.linspace(0, x[ok].max(), 20); ax.plot(xx, s * xx, "-", color=col, lw=1.4, label=f"{lab}: {s * 1e6:.0f} ns/event")
                else:
                    ax.plot([], [], "o", color=col, label=lab)   # (b): points only -- a fit across victims of different size means nothing
    axs[0].set_xlabel("hook events per second, all CPUs (thousands)"); axs[0].set_ylabel("cores spent inside the BPF programs (bpf_stats run time)")
    axs[0].set_title("(a) BPF program time; line = least squares through 0", fontsize=9.5, loc="left")
    axs[1].set_xlabel("hook events per second, all CPUs (thousands)"); axs[1].set_ylabel("Δ busy cores, whole node (arm − paired 'none')")
    axs[1].set_title("(b) node-wide cost incl. the userspace reader (points; the reader's 0.1-0.25 cores is the floor)", fontsize=9.5, loc="left")
    for ax in axs:
        ax.grid(alpha=0.3)
        h, l = ax.get_legend_handles_labels()
        h += [Line2D([], [], marker=m, ls="", color="#555555", label=v) for v, m in (("synthetic (4 cores)", "o"), ("unique-id (1 core)", "s"), ("post-storage (1 core)", "^"))]
        ax.legend(handles=h, fontsize=7.5, loc="upper left")
    fig.suptitle("Fig. K1-1 — eBPF scheduler-hook cost vs event rate, one line per hook set and program (clnode170 node-3, Haswell 2.0 GHz, kernel 6.12; bpftrace 0.14, libbpf 1.4.7)", x=0.01, ha="left", fontsize=10)
    fig.tight_layout(rect=(0, 0, 1, 0.95)); fig.savefig(os.path.join(out, "figK1-cores-vs-events.png"), dpi=130); plt.close(fig)
    # Fig. 2: p99 on / off per cell and arm (paired difference / none mean), server side; unique-id also client side
    cells = sorted(set(w["cell"] for w in wide if np.isfinite(f(w, "p99_ms"))), key=lambda c: ({"a": 0, "b": 1, "c": 2, "d": 3, "e": 4}[c[0]], c))
    fig, ax = plt.subplots(figsize=(16, 6.4)); ticks = []
    colmap = {("switch", "bt", "full"): "#2A6F97", ("wakeup", "bt", "full"): "#D85A30", ("both", "bt", "full"): "#1B2430", ("both", "bt", "empty"): "#B8860B", ("both", "bt", "v5"): "#7F77DD", ("both", "lb", "full"): "#1D9E75",
              ("switch", "lb", "empty"): "#C9A227", ("both", "lb", "empty"): "#8C6D1F", ("switch", "lb", "lean"): "#2E8B57", ("both", "lb", "lean"): "#0B4F3C"}
    clip = 3.0
    for xi, c in enumerate(cells):
        none = next((w for w in wide if w["cell"] == c and w["arm"] == "none"), None)
        arms = sorted([w for w in wide if w["cell"] == c and w["arm"] != "none" and w["bpf_stats"] == "on"], key=lambda w: w["arm"])
        for j, w in enumerate(arms):
            col = colmap.get((w["hookset"], w["impl"], w["program"]), "#999999"); xo = xi + (j - (len(arms) - 1) / 2) * 0.13
            for key, base, mkr in (("d_p99_ms", "p99_ms", "o"), ("d_client_lat_p99_ms", "client_lat_p99_ms", "D")):
                d, lo, hi, b0 = f(w, key), f(w, key + "_lo"), f(w, key + "_hi"), (f(none, base) if none else np.nan)
                if not (np.isfinite(d) and np.isfinite(b0) and b0 > 0): continue
                rr, rl, rh = 1 + d / b0, 1 + lo / b0, 1 + hi / b0
                if rr > clip:
                    ax.annotate(f"×{rr:.0f}", (xo, clip), xytext=(xo + 0.15, clip - 0.15), fontsize=8, color=col, arrowprops=dict(arrowstyle="->", color=col)); rr = clip; rl = rh = np.nan
                ax.errorbar(xo, rr, yerr=[[rr - rl] if np.isfinite(rl) else [0], [rh - rr] if np.isfinite(rh) else [0]], fmt=mkr, color=col, ms=4.5, capsize=2, mfc=("white" if mkr == "D" else col))
        ticks.append((xi, c))
    ax.axhline(1.0, color="#888888", lw=0.8); ax.set_ylim(0.6, clip + 0.1)
    ax.set_xticks([t[0] for t in ticks]); ax.set_xticklabels([t[1] for t in ticks], rotation=60, ha="right", fontsize=8)
    ax.set_ylabel("victim p99, arm ÷ paired 'none' (95 % CI of the paired difference)")
    ax.legend(handles=[Line2D([], [], marker="o", ls="", color=c_, label=l) for (k, c_), l in zip(colmap.items(), ("switch, bpftrace full", "wake-up, bpftrace full", "both, bpftrace full", "both, bpftrace empty", "v5 probe", "both, libbpf full",
                                                                                                        "switch, libbpf empty", "both, libbpf empty", "lean libbpf", "lean libbpf + wake-up"))]
              + [Line2D([], [], marker="o", ls="", color="#555555", label="server-side p99 (recorder)"), Line2D([], [], marker="D", ls="", color="#555555", mfc="white", label="client-side p99 (unique-id load generator)")], fontsize=7.5, ncol=4, loc="upper right")
    ax.set_title("Fig. K1-2 — victim p99 with the probe ÷ without, per cell and arm (5 × 60 s each, ABAB with 'none'); values above ×3 are clipped and labelled", fontsize=10, loc="left")
    ax.grid(alpha=0.3, axis="y"); fig.tight_layout(); fig.savefig(os.path.join(out, "figK1-p99.png"), dpi=130); plt.close(fig)
    print("figures written")

if __name__ == "__main__":
    main()
