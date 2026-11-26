#!/usr/bin/env python3
"""Phase K4.1b: four arms per load (S standalone, B burner on core 12 with HC unloaded, C HC loaded with no Secondary,
H HC + burner), the Primary's latency against S, and the decompositions HC presence = C / S and lending = H / C.

Inputs (a K4.1b results directory fetched from node-0): segments.jsonl, windows.json and recorder_reduced.json (k4reduce.py
with --sub 10: main windows and "<name>#k" 30 s sub-windows), balancer_reduced.json, and per run wlgen.jsonl, sample.jsonl
(k4sample.py) and probe.jsonl (the lean libbpf probe, 100 ms windows on the Primary's cores).
Per run, besides K4.1's metrics (analysis/k4_analyze.py):
  probe   Primary dispatches per second (sum of the per-holder counts), the share of them that followed a Secondary thread
          on that core ("ran on a lent core": the burner always wants more than it owns, so a lent core is held by it),
          the Primary's run-queue wait behind the Secondary (time-share split) in ms per second and us per request,
          occupancy of the four cores by class in cores, preemptions of Primary threads per second
  sched   the Primary threads' migrations and involuntary / voluntary switches per second (/proc/<tid>/sched)
  cost    the probe's program time in cores, events per second and ns per event (bpf_stats via fdinfo), its loader's CPU
  weight  cpu.weight of both pods
usage: k4b_analyze.py K4B_DIR [--out results/k4b]
"""
import argparse, csv, json, os, statistics as st, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from k4_analyze import client, sampler, jl

P = [4, 6, 8, 10]
ARMS = ("S", "B", "C", "H")
ARMNAME = {"S": "standalone", "B": "burner on core 12, HC unloaded", "C": "HC loaded, no Secondary", "H": "HC + burner"}


def extra(d, w):
    o = {}
    L = jl(os.path.join(d, "sample.jsonl")); t0, t1 = w["t0_ms"], w["t1_ms"]
    if L and L[0].get("event") == "start":
        pw, sw = L[0].get("primary_weight") or {}, L[0].get("secondary_weight") or {}
        o["w_primary_pod"] = pw.get("pod"); o["w_primary_parent"] = (pw.get("parent") or "") + ":" + str(pw.get("parent_weight"))
        o["w_primary_scopes"] = "/".join(sorted(set((pw.get("scopes") or {}).values())))
        o["w_secondary_pod"] = sw.get("pod"); o["w_secondary_parent"] = ((sw.get("parent") or "") + ":" + str(sw.get("parent_weight"))) if sw else ""
        o["w_secondary_scopes"] = "/".join(sorted(set((sw.get("scopes") or {}).values()))) if sw else ""
    sec = [x for x in L if x.get("kind") == "sec" and t0 <= x["t"] <= t1]
    if len(sec) >= 2:
        dt = (sec[-1]["t"] - sec[0]["t"]) / 1000.0
        pt = [x.get("pthr") or {} for x in sec[1:]]
        o["p_mig_s"] = sum(x.get("mig", 0) for x in pt) / dt
        o["p_inv_s"] = sum(x.get("inv", 0) for x in pt) / dt
        o["p_vol_s"] = sum(x.get("vol", 0) for x in pt) / dt
        o["p_threads"] = st.median(x.get("n", 0) for x in pt)
        a, b = sec[0].get("probe") or {}, sec[-1].get("probe") or {}
        if a and b:
            rt = sum(b[k][0] - a[k][0] for k in b if k in a); rc = sum(b[k][1] - a[k][1] for k in b if k in a)
            o["probe_cores"] = rt / 1e9 / dt; o["probe_events_s"] = rc / dt; o["probe_ns_event"] = rt / rc if rc else None
        if sec[0].get("probe_self_ns") is not None and sec[-1].get("probe_self_ns") is not None:
            o["probe_loader_cores"] = (sec[-1]["probe_self_ns"] - sec[0]["probe_self_ns"]) / 1e9 / dt
    W = [x for x in jl(os.path.join(d, "probe.jsonl")) if "Rself" in x and t0 < x["t"] <= t1]
    if W:
        dt = len(W) * 0.1
        S = {k: sum(x[k] for x in W) for k in W[0] if k != "t"}
        n = S["nself"] + S["naggr"] + S["nroot"] + S["nother"]
        R = S["Rself"] + S["Raggr"] + S["Rroot"] + S["Rother"]
        o.update(pr_windows=len(W), pr_disp_s=n / dt, pr_after_sec_s=S["naggr"] / dt, pr_after_sec_share=(S["naggr"] / n) if n else None,
                 pr_wait_ms_s=R / 1e6 / dt, pr_wait_sec_ms_s=S["Raggr"] / 1e6 / dt, pr_wait_sec_share=(S["Raggr"] / R) if R else None,
                 pr_wait_other_ms_s=(S["Rroot"] + S["Rother"]) / 1e6 / dt, pr_wait_self_ms_s=S["Rself"] / 1e6 / dt,
                 pr_occ_primary=S["Oself"] / 1e9 / dt, pr_occ_secondary=S["Oaggr"] / 1e9 / dt, pr_occ_other=(S["Oroot"] + S["Oother"]) / 1e9 / dt,
                 pr_occ_idle=S["Oidle"] / 1e9 / dt, pr_preempt_s=S["preempt"] / dt, pr_mig_waits_s=S["mig"] / dt, pr_multi_s=S["multi"] / dt)
    return o


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("root"); ap.add_argument("--out", default="results/k4b"); a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True); R = a.root
    W = {w["seg"]: w for w in json.load(open(os.path.join(R, "windows.json")))}
    rec = json.load(open(os.path.join(R, "recorder_reduced.json")))
    bp = os.path.join(R, "balancer_reduced.json"); bal = json.load(open(bp)) if os.path.exists(bp) else {}
    rows = []
    for s in jl(os.path.join(R, "segments.jsonl")):
        seg = s["seg"]; w = W[seg]; d = os.path.join(R, seg); rr = rec.get(seg, {})
        r = dict(seg=seg, name=s["name"], arm=s["mode"], rate=s["rate"], window_s=(w["t1_ms"] - w["t0_ms"]) / 1000.0,
                 srv_rps=rr["requests"] / rr["dur_s"] if rr else None, srv_err=rr.get("errors"), prim_cores_rec=rr.get("cpu_cores"), mhz=rr.get("mhz"),
                 srv_p50_ms=rr.get("tot_p50_ms"), srv_p95_ms=rr.get("tot_p95_ms"), srv_p99_ms=rr.get("tot_p99_ms"), srv_p999_ms=rr.get("tot_p999_ms"),
                 srv_mean_ms=rr.get("tot_mean_ms"), srv_q_p99_ms=rr.get("q_p99_ms"))
        sub = [rec[f"{s['name']}#{k}"] for k in range(10) if f"{s['name']}#{k}" in rec]
        for q in ("p95", "p99"):
            v = sorted(x[f"tot_{q}_ms"] for x in sub if x.get(f"tot_{q}_ms") is not None)
            if v: r[f"srv_{q}_sub_med"], r[f"srv_{q}_sub_min"], r[f"srv_{q}_sub_max"] = st.median(v), v[0], v[-1]
        r.update(client(d, w)); r.update(sampler(d, w)); r.update(extra(d, w))
        b = bal.get(seg)
        if b: r.update(lent_bal=b["lent_mean"], rebalances_per_s=b["rebalances_per_s"], **{f"lent_share_{i}": v for i, v in enumerate(b["share"])})
        if r.get("prim_cores") is not None:
            r["unused"] = len(P) - r["prim_cores"]
            if b and s["mode"] == "H": r["harvest"] = r["lent_bal"] / r["unused"]
            if r.get("sec_on_p") is not None: r["used_frac"] = r["sec_on_p"] / r["unused"]
        if r.get("pr_wait_sec_ms_s") is not None and r.get("srv_rps"):
            r["pr_wait_sec_us_req"] = r["pr_wait_sec_ms_s"] * 1000.0 / r["srv_rps"]
        rows.append(r)
    keys = []
    for r in rows:
        for k in r:
            if k not in keys: keys.append(k)
    with open(os.path.join(a.out, "k4b_segments.csv"), "w", newline="") as fh:
        wr = csv.DictWriter(fh, fieldnames=keys); wr.writeheader(); wr.writerows(rows)
    by = {(r["rate"], r["arm"]): r for r in rows}
    lat = ("srv_p50_ms", "srv_p95_ms", "srv_p99_ms", "srv_p999_ms", "cl_p50_ms", "cl_p90_ms", "cl_p99_ms")
    ratio = lambda x, y, k: (x[k] / y[k]) if x and y and x.get(k) is not None and y.get(k) else None
    arms, dec = [], []
    for rate in sorted(set(r["rate"] for r in rows)):
        S = by.get((rate, "S"))
        for arm in ARMS:
            x = by.get((rate, arm))
            if not x: continue
            o = dict(rate=rate, arm=arm, arm_name=ARMNAME[arm], seg=x["seg"])
            for k in lat: o[k] = x.get(k); o[k.replace("_ms", "") + "_vs_S"] = ratio(x, S, k)
            for k in ("prim_cores", "unused", "lent_bal", "harvest", "sec_on_p", "used_frac", "sec_cores", "mon_cores", "bal_cores", "rebalances_per_s",
                      "pr_after_sec_share", "pr_after_sec_s", "pr_disp_s", "pr_wait_sec_ms_s", "pr_wait_sec_us_req", "pr_wait_sec_share", "pr_wait_ms_s",
                      "pr_occ_primary", "pr_occ_secondary", "pr_preempt_s", "p_mig_s", "p_inv_s", "p_vol_s", "probe_cores", "probe_ns_event",
                      "probe_events_s", "probe_loader_cores", "srv_p99_sub_min", "srv_p99_sub_max", "srv_p95_sub_min", "srv_p95_sub_max",
                      "w_primary_pod", "w_secondary_pod", "w_primary_parent", "w_secondary_parent"):
                o[k] = x.get(k)
            arms.append(o)
        C, H, B = by.get((rate, "C")), by.get((rate, "H")), by.get((rate, "B"))
        for k in lat:
            dec.append(dict(rate=rate, metric=k, standalone=S.get(k) if S else None, burner_only=B.get(k) if B else None, hc_only=C.get(k) if C else None,
                            hc_burner=H.get(k) if H else None, presence_C_over_S=ratio(C, S, k), lending_H_over_C=ratio(H, C, k),
                            burner_B_over_S=ratio(B, S, k), total_H_over_S=ratio(H, S, k)))
    for nm, data in (("k4b_arms.csv", arms), ("k4b_decomp.csv", dec)):
        with open(os.path.join(a.out, nm), "w", newline="") as fh:
            wr = csv.DictWriter(fh, fieldnames=list(data[0].keys())); wr.writeheader(); wr.writerows(data)
    for o in arms:
        print(o["rate"], o["arm"], {k: (round(v, 3) if isinstance(v, float) else v) for k, v in o.items() if k in (
            "srv_p50_vs_S", "srv_p95_vs_S", "srv_p99_vs_S", "cl_p99_vs_S", "harvest", "used_frac", "pr_after_sec_share", "pr_wait_sec_us_req", "probe_cores")})
    figure(arms, dec, out=a.out)


def figure(arms, dec, out):
    import matplotlib; matplotlib.use("Agg"); import matplotlib.pyplot as plt
    rates = sorted(set(o["rate"] for o in arms)); x = list(range(len(rates)))
    col = {"B": "#9AA5B1", "C": "#2A6F97", "H": "#D85A30"}
    fig, axs = plt.subplots(1, 3, figsize=(17, 5.4))
    ax = axs[0]
    for j, k in enumerate(("srv_p95_vs_S", "srv_p99_vs_S")):
        for i2, arm in enumerate(("B", "C", "H")):
            ys = [next((o[k] for o in arms if o["rate"] == r and o["arm"] == arm), None) for r in rates]
            ax.plot([xi + (i2 - 1) * 0.22 + (j - 0.5) * 0.08 for xi in x], ys, "o" if j == 0 else "D", color=col[arm], ms=7, ls="",
                    label=ARMNAME[arm] if j == 1 else None)
    ax.plot([], [], "o", color="#555555", label="server p95"); ax.plot([], [], "D", color="#555555", label="server p99")
    ax.axhline(1.0, color="#888888", lw=0.8); ax.axhline(1.04, color="#1D9E75", lw=1.0, ls="--", label="paper's bound (+4 %)")
    ax.set_xticks(x); ax.set_xticklabels([f"{r} req/s" for r in rates]); ax.set_ylabel("arm ÷ standalone twin")
    ax.set_title("(a) each arm against the standalone twin", fontsize=9.5, loc="left"); ax.legend(fontsize=7.5, loc="upper left")
    ax = axs[1]
    mets = (("srv_p50_ms", "p50"), ("srv_p95_ms", "p95"), ("srv_p99_ms", "p99"), ("cl_p99_ms", "client p99"))
    for j, (k, lab) in enumerate(mets):
        for i2, (key, c, mk, nm) in enumerate((("presence_C_over_S", "#2A6F97", "o", "HC presence (C ÷ S)"), ("lending_H_over_C", "#D85A30", "s", "lending (H ÷ C)"))):
            ys = [next((dd[key] for dd in dec if dd["rate"] == r and dd["metric"] == k), None) for r in rates]
            ax.plot([xi + (j - 1.5) * 0.18 + (i2 - 0.5) * 0.06 for xi in x], ys, mk, color=c, ms=6, ls="", label=nm if j == 0 else None)
        for xi in x:
            ax.annotate(lab, (xi + (j - 1.5) * 0.18, 0.84), ha="center", fontsize=6.5, rotation=90, color="#555555")
    ax.axhline(1.0, color="#888888", lw=0.8); ax.set_ylim(0.8, None)
    ax.set_xticks(x); ax.set_xticklabels([f"{r} req/s" for r in rates]); ax.set_ylabel("ratio")
    ax.set_title("(b) per metric (p50, p95, p99 server; client p99): presence and lending", fontsize=9.5, loc="left"); ax.legend(fontsize=7.5, loc="upper left")
    ax = axs[2]
    H = [next((o for o in arms if o["rate"] == r and o["arm"] == "H"), None) for r in rates]
    for j, (k, c, lab) in enumerate((("unused", "#9AA5B1", "Primary unused (4 − busy)"), ("lent_bal", "#2A6F97", "lent by HC"), ("sec_on_p", "#D85A30", "Secondary ran on Primary cores"))):
        ax.bar([xi + (j - 1) * 0.26 for xi in x], [(h.get(k) or 0) if h else 0 for h in H], 0.24, color=c, label=lab)
    for xi, h in zip(x, H):
        if h and h.get("harvest") is not None:
            txt = f"harvest {h['harvest']:.2f}, used {h['used_frac']:.2f}"
            if h.get("pr_after_sec_share") is not None: txt += f"\n{100 * h['pr_after_sec_share']:.0f} % of Primary dispatches after the Secondary"
            ax.annotate(txt, (xi, (h.get("unused") or 0) + 0.1), ha="center", fontsize=7)
    ax.set_xticks(x); ax.set_xticklabels([f"{r} req/s" for r in rates]); ax.set_ylabel("cores (of the Primary's 4)"); ax.set_ylim(0, 4.4)
    ax.set_title("(c) HC + burner: cores and contention", fontsize=9.5, loc="left"); ax.legend(fontsize=7.5, loc="upper right")
    fig.suptitle("Fig. K4b-1 — HC with equal CPU weights (Primary and burner both BestEffort): recommendation on cores 4, 6, 8, 10; one 300 s run per arm",
                 x=0.01, ha="left", fontsize=10)
    fig.tight_layout(rect=(0, 0, 1, 0.94)); fig.savefig(os.path.join(out, "figK4b-1.png"), dpi=130); plt.close(fig)
    print("figure written")


if __name__ == "__main__":
    main()
