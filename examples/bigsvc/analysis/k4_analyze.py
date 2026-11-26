#!/usr/bin/env python3
"""Phase K4.1: harvest fraction and the Primary's latency under HarvestContainers vs its standalone twin.

Inputs (a K4 results directory fetched from node-0): segments.jsonl, windows.json (k4/k4reduce.py windows),
recorder_reduced.json (the Primary's server-side recorder pooled per measured window), balancer_reduced.json (HC's own
allocation record), knee_recorder.json (server side per knee step), and per segment wlgen.jsonl (hrgen) and sample.jsonl
(k4/k4sample.py).

Per segment, over the measured window (hrgen step 0, 300 s after a 30 s warm-up):
  lent_bal     time-average of the Primary cores in the Secondary's mask, from balancer.log (count after each rebalance)
  lent_thr     the same from the sampler: mean over Secondary threads of |Cpus_allowed & Primary cores| (0.2 s samples)
  lent_union   the sampler's union over threads (an upper bound: HC rewrites the threads' masks one at a time)
  prim_cores   the Primary pod's CPU (cgroup usage_usec); prim_cores_rec = recommendation's process CPU (recorder)
  unused       4 - prim_cores                               harvest = lent_bal / unused (K4.1 definition)
  sec_on_p     Secondary run time on threads whose last CPU is a Primary core (cores); used_frac = sec_on_p / unused
  server       tap -> return latency of every request (recorder raw samples), pooled p50 / p95 / p99 / p99.9
  client       hrgen end-to-end through the frontend: median of the per-second p50 / p90 / p99 (hrgen reports no p95)
usage: k4_analyze.py K4_DIR [--out results/k4]
"""
import argparse, csv, json, os, statistics as st

P = [4, 6, 8, 10]


def jl(p):
    out = []
    if not os.path.exists(p): return out
    for l in open(p):
        try: out.append(json.loads(l))
        except Exception: pass
    return out


def client(d, w):
    sec = [x for x in jl(os.path.join(d, "wlgen.jsonl")) if "all" in x and x.get("step", -9) >= 0 and x.get("rate", 0) > 0]
    if not sec: return {}
    dt = (sec[-1]["t"] - sec[0]["t"]) / 1000.0
    return dict(cl_done_rps=(sec[-1]["done"] - sec[0]["done"]) / dt, cl_err=sec[-1]["err"] - sec[0]["err"],
                cl_err_warmup=sec[0]["err"], cl_inflight_max=max(x["inflight"] for x in sec),
                cl_p50_ms=st.median(x["all"][1] for x in sec) / 1e6, cl_p90_ms=st.median(x["all"][2] for x in sec) / 1e6,
                cl_p99_ms=st.median(x["all"][3] for x in sec) / 1e6)


def sampler(d, w):
    L = jl(os.path.join(d, "sample.jsonl")); t0, t1 = w["t0_ms"], w["t1_ms"]
    sec = [x for x in L if x.get("kind") == "sec" and t0 <= x["t"] <= t1]
    aff = [x for x in L if x.get("kind") == "aff" and t0 <= x["t"] <= t1]
    o = {}
    if len(sec) >= 2:
        a, b = sec[0], sec[-1]; dt = (b["t"] - a["t"]) / 1000.0
        busy = lambda s, cpus: sum(s["stat"][str(c)][i] for c in cpus for i in (0, 1, 2, 5, 6, 7)) / 100.0
        allc = list(a["stat"])
        o["prim_cores"] = (b["pcg"] - a["pcg"]) / 1e6 / dt if a.get("pcg") is not None else None
        o["p_busy_stat"] = (busy(b, P) - busy(a, P)) / dt
        o["node_busy_cores"] = (busy(b, allc) - busy(a, allc)) / dt
        o["sec_cores"] = (b["scg"] - a["scg"]) / 1e6 / dt if a.get("scg") is not None else None
        o["mon_cores"] = (b["mon_ns"] - a["mon_ns"]) / 1e9 / dt if a.get("mon_ns") is not None and b.get("mon_ns") is not None else None
        o["bal_cores"] = (b["bal_ns"] - a["bal_ns"]) / 1e9 / dt if a.get("bal_ns") is not None and b.get("bal_ns") is not None else None
    if aff:
        dt = (aff[-1]["t"] - aff[0]["t"]) / 1000.0
        o["sec_on_p"] = sum(x["sec_p_ns"] for x in aff[1:]) / 1e9 / dt
        o["lent_union"] = st.mean(len(x["lent"]) for x in aff)
        per = []
        for x in aff:
            ms = [set(int(c) for c in m.split(",")) for m in x["masks"].values()]
            if ms: per.append(st.mean(len(m & set(P)) for m in ms))
        o["lent_thr"] = st.mean(per) if per else None
        o["aff_samples"] = len(aff)
    return o


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("root"); ap.add_argument("--out", default="results/k4"); a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True); R = a.root
    W = {w["seg"]: w for w in json.load(open(os.path.join(R, "windows.json")))}
    rec = json.load(open(os.path.join(R, "recorder_reduced.json"))); bal = json.load(open(os.path.join(R, "balancer_reduced.json")))
    rows = []
    for s in jl(os.path.join(R, "segments.jsonl")):
        seg = s["seg"]; w = W[seg]; d = os.path.join(R, seg)
        r = dict(seg=seg, name=s["name"], mode=s["mode"], rate=s["rate"], window_s=(w["t1_ms"] - w["t0_ms"]) / 1000.0)
        rr = rec.get(seg, {})
        r.update(srv_rps=rr["requests"] / rr["dur_s"] if rr else None, srv_err=rr.get("errors"), prim_cores_rec=rr.get("cpu_cores"), mhz=rr.get("mhz"),
                 srv_p50_ms=rr.get("tot_p50_ms"), srv_p95_ms=rr.get("tot_p95_ms"), srv_p99_ms=rr.get("tot_p99_ms"), srv_p999_ms=rr.get("tot_p999_ms"),
                 srv_mean_ms=rr.get("tot_mean_ms"), srv_q_p99_ms=rr.get("q_p99_ms"))
        r.update(client(d, w)); r.update(sampler(d, w))
        b = bal.get(seg)
        if b: r.update(lent_bal=b["lent_mean"], rebalances_per_s=b["rebalances_per_s"], **{f"lent_share_{i}": v for i, v in enumerate(b["share"])})
        if r.get("prim_cores") is not None:
            r["unused"] = len(P) - r["prim_cores"]
            if b: r["harvest"] = r["lent_bal"] / r["unused"]
            if r.get("sec_on_p") is not None: r["used_frac"] = r["sec_on_p"] / r["unused"]
        rows.append(r)
    keys = []
    for r in rows:
        for k in r:
            if k not in keys: keys.append(k)
    with open(os.path.join(a.out, "k4_segments.csv"), "w", newline="") as fh:
        wr = csv.DictWriter(fh, fieldnames=keys); wr.writeheader(); wr.writerows(rows)
    by = {r["name"]: r for r in rows}
    hv = []
    for lab, sn, hn in (("0.2", "r02-S", "r02-H"), ("0.5", "r05-S", "r05-H"), ("0.7", "r07-S", "r07-H"), ("0.5 as-is", "r05-S", "r05-Hasis")):
        S, H = by.get(sn), by.get(hn)
        if not (S and H): continue
        h = dict(load=lab, rate=H["rate"], hc_seg=H["seg"], standalone_seg=S["seg"], lent_bal=H.get("lent_bal"), lent_thr=H.get("lent_thr"), lent_union=H.get("lent_union"),
                 prim_cores_hc=H.get("prim_cores"), prim_cores_standalone=S.get("prim_cores"), unused=H.get("unused"), harvest=H.get("harvest"),
                 harvest_vs_standalone_unused=(H["lent_bal"] / (len(P) - S["prim_cores"])) if H.get("lent_bal") is not None and S.get("prim_cores") is not None else None,
                 sec_on_p=H.get("sec_on_p"), used_frac=H.get("used_frac"), sec_cores=H.get("sec_cores"), mon_cores=H.get("mon_cores"), bal_cores=H.get("bal_cores"))
        for k in ("srv_p50_ms", "srv_p95_ms", "srv_p99_ms", "srv_p999_ms", "cl_p50_ms", "cl_p90_ms", "cl_p99_ms"):
            h[k + "_standalone"] = S.get(k); h[k + "_hc"] = H.get(k)
            h[k.replace("_ms", "") + "_ratio"] = (H[k] / S[k]) if S.get(k) and H.get(k) is not None else None
        hv.append(h)
    with open(os.path.join(a.out, "k4_harvest.csv"), "w", newline="") as fh:
        wr = csv.DictWriter(fh, fieldnames=list(hv[0].keys())); wr.writeheader(); wr.writerows(hv)
    kr = os.path.join(R, "knee_recorder.json"); kj = json.load(open(os.path.join(R, "knee.json")))
    if os.path.exists(kr):
        k = json.load(open(kr)); cl = {s_["step"]: s_ for s_ in kj["steps"]}; kn = []
        for sname, v in sorted(k.items(), key=lambda kv: int(kv[0][5:])):
            stp = int(sname[5:]); c = cl.get(stp, {})
            # knee.json's p50_ms / p99_ms are hrgen ns / 1000, i.e. microseconds (k4knee.py's label); converted here
            kn.append(dict(step=stp, offered=c.get("rate"), client_served=c.get("served"), client_p50_ms=(c["p50_ms"] / 1000.0 if c else None), client_p99_ms=(c["p99_ms"] / 1000.0 if c else None),
                           srv_rps=v["requests"] / v["dur_s"], prim_cores_rec=v["cpu_cores"], srv_p50_ms=v["tot_p50_ms"], srv_p95_ms=v["tot_p95_ms"], srv_p99_ms=v["tot_p99_ms"], srv_q_p99_ms=v["q_p99_ms"]))
        with open(os.path.join(a.out, "k4_knee.csv"), "w", newline="") as fh:
            wr = csv.DictWriter(fh, fieldnames=list(kn[0].keys())); wr.writeheader(); wr.writerows(kn)
    for h in hv:
        print(h["load"], {k: (round(v, 3) if isinstance(v, float) else v) for k, v in h.items() if k not in ("hc_seg", "standalone_seg", "load")})
    sub = os.path.join(R, "sub_recorder.json")
    figure(hv, json.load(open(sub)) if os.path.exists(sub) else {}, a.out)


def figure(hv, sub, out):
    """Fig. K4-1: (a) the Primary's unused cores, what HC lent and what the Secondary ran on them; (b) server-side
    p50 / p95 / p99 and client p99, HC / standalone; (c) server p99 per 30 s sub-window (10 per segment)."""
    import matplotlib; matplotlib.use("Agg"); import matplotlib.pyplot as plt
    fig, axs = plt.subplots(1, 3, figsize=(17, 5.4))
    labs = [h["load"] + f"\n({h['rate']} req/s)" for h in hv]; x = list(range(len(hv)))
    ax = axs[0]
    for j, (k, col, lab) in enumerate((("unused", "#9AA5B1", "Primary unused (4 − Primary busy)"), ("lent_bal", "#2A6F97", "lent by HC (Balancer's mask, time-average)"),
                                       ("sec_on_p", "#D85A30", "Secondary's run time on Primary cores"))):
        ax.bar([i + (j - 1) * 0.26 for i in x], [h[k] for h in hv], 0.24, color=col, label=lab)
    for i, h in enumerate(hv):
        ax.annotate(f"harvest {h['harvest']:.2f}\nused {h['used_frac']:.2f}", (i, h["unused"] + 0.08), ha="center", fontsize=8.5)
    ax.set_xticks(x); ax.set_xticklabels(labs, fontsize=8.5); ax.set_ylabel("cores (of the Primary's 4)"); ax.set_ylim(0, 4)
    ax.set_title("(a) cores; harvest = lent ÷ unused", fontsize=9.5, loc="left"); ax.legend(fontsize=7.5, loc="upper right")
    ax = axs[1]
    for j, (k, col, mk, lab) in enumerate((("srv_p50_ratio", "#7F8C99", "o", "server p50"), ("srv_p95_ratio", "#2A6F97", "s", "server p95"),
                                          ("srv_p99_ratio", "#1B2430", "D", "server p99"), ("cl_p99_ratio", "#D85A30", "^", "client p99 (end to end)"))):
        ax.plot([i + (j - 1.5) * 0.12 for i in x], [h[k] for h in hv], mk, color=col, ms=7, ls="", label=lab)
    ax.axhline(1.0, color="#888888", lw=0.8); ax.axhline(1.04, color="#1D9E75", lw=1.0, ls="--", label="paper's bound (+4 %)")
    ax.set_xticks(x); ax.set_xticklabels(labs, fontsize=8.5); ax.set_ylabel("HC ÷ standalone twin"); ax.set_ylim(0.8, 2.7)
    ax.set_title("(b) the Primary's latency, HC ÷ standalone (pooled over 300 s)", fontsize=9.5, loc="left"); ax.legend(fontsize=7.5, loc="upper left")
    ax = axs[2]
    names = [("r02-S", "r02-H"), ("r05-S", "r05-H"), ("r07-S", "r07-H"), ("r05-S", "r05-Hasis")]
    for i, (sn, hn) in enumerate(names[:len(hv)]):
        for off, nm, col in ((-0.12, sn, "#9AA5B1"), (0.12, hn, "#2A6F97")):
            ys = [sub[f"{nm}#{k}"]["tot_p99_ms"] for k in range(10) if f"{nm}#{k}" in sub]
            if ys: ax.plot([i + off] * len(ys), ys, "o", color=col, ms=4, alpha=0.8)
    ax.plot([], [], "o", color="#9AA5B1", label="standalone twin"); ax.plot([], [], "o", color="#2A6F97", label="HC")
    ax.set_yscale("log"); ax.set_xticks(x); ax.set_xticklabels(labs, fontsize=8.5); ax.set_ylabel("server p99, ms (log)")
    ax.set_title("(c) server p99 of each 30 s sub-window (10 per run)", fontsize=9.5, loc="left"); ax.legend(fontsize=7.5, loc="upper left"); ax.grid(alpha=0.3, axis="y")
    fig.suptitle("Fig. K4-1 — HarvestContainers on node-4: Primary = recommendation on cores 4, 6, 8, 10; Secondary = CPU burner owning core 12 "
                 "(one 300 s run per arm; '0.5 as-is' = burner pinning its own workers)", x=0.01, ha="left", fontsize=10)
    fig.tight_layout(rect=(0, 0, 1, 0.94)); fig.savefig(os.path.join(out, "figK4-1.png"), dpi=130); plt.close(fig)
    print("figure written")


if __name__ == "__main__":
    main()
