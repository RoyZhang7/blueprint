#!/usr/bin/env python3
"""Phase K4.2e: HC's idle-core reserve (targetIdleCores = static_targetIdleCores = LOW_TIC = 1, 2, 3) at the shipped
placement, equal CPU weights, 5 000 req/s; HC + burner runs res1..res3 between standalone twins S0 (start) and S1 (end).

Per reserve: harvest fraction (lent / unused), the Secondary's use of Primary cores, HC's own cores, the Primary's server
p95 / p99 and client p99 against S0, S1 and their mean, its run-queue wait split own threads / the Secondary / other, the
share of its dispatches that follow a Secondary thread (a burner-held core); K4.1b's r05-H (reserve 1, other session) beside.
The headline: the smallest reserve whose server p99 is under x1.1 of both standalone twins, with its harvest fraction.
usage: k42e_analyze.py K42E_DIR [--k4b results/k4b/k4b_segments.csv] [--out results/k42e]
"""
import argparse, csv, json, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from k4_analyze import client, sampler, jl
from k4b_analyze import extra

P = [4, 6, 8, 10]
LAT = ("srv_p50_ms", "srv_p95_ms", "srv_p99_ms", "srv_p999_ms", "cl_p50_ms", "cl_p90_ms", "cl_p99_ms")


def fnum(x):
    try: return float(x)
    except Exception: return None


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("root"); ap.add_argument("--k4b", default="results/k4b/k4b_segments.csv"); ap.add_argument("--out", default="results/k42e")
    a = ap.parse_args(); os.makedirs(a.out, exist_ok=True); R = a.root
    W = {w["seg"]: w for w in json.load(open(os.path.join(R, "windows.json")))}
    rec = json.load(open(os.path.join(R, "recorder_reduced.json")))
    bp = os.path.join(R, "balancer_reduced.json"); bal = json.load(open(bp)) if os.path.exists(bp) else {}
    rows = []
    for s in jl(os.path.join(R, "segments.jsonl")):
        seg = s["seg"]; w = W[seg]; d = os.path.join(R, seg); rr = rec.get(seg, {})
        r = dict(seg=seg, name=s["name"], mode=s["mode"], reserve=s.get("reserve", "-"), rate=s["rate"], window_s=(w["t1_ms"] - w["t0_ms"]) / 1000.0,
                 srv_rps=rr["requests"] / rr["dur_s"] if rr else None, prim_cores_rec=rr.get("cpu_cores"), mhz=rr.get("mhz"),
                 srv_p50_ms=rr.get("tot_p50_ms"), srv_p95_ms=rr.get("tot_p95_ms"), srv_p99_ms=rr.get("tot_p99_ms"), srv_p999_ms=rr.get("tot_p999_ms"))
        sub = [rec[f"{s['name']}#{k}"] for k in range(10) if f"{s['name']}#{k}" in rec]
        for q in ("p95", "p99"):
            v = sorted(x[f"tot_{q}_ms"] for x in sub if x.get(f"tot_{q}_ms") is not None)
            if v: r[f"srv_{q}_sub_min"], r[f"srv_{q}_sub_max"] = v[0], v[-1]
        r.update(client(d, w)); r.update(sampler(d, w)); r.update(extra(d, w))
        b = bal.get(seg)
        if b: r.update(lent_bal=b["lent_mean"], rebalances_per_s=b["rebalances_per_s"], **{f"lent_share_{i}": v for i, v in enumerate(b["share"])})
        if r.get("prim_cores") is not None:
            r["unused"] = len(P) - r["prim_cores"]
            if b and s["mode"] == "H": r["harvest"] = r["lent_bal"] / r["unused"]
            if r.get("sec_on_p") is not None: r["used_frac"] = r["sec_on_p"] / r["unused"]
        r["hc_cores"] = ((r.get("mon_cores") or 0) + (r.get("bal_cores") or 0)) if s["mode"] == "H" else 0.0
        rows.append(r)
    keys = []
    for r in rows:
        for k in r:
            if k not in keys: keys.append(k)
    with open(os.path.join(a.out, "k42e_segments.csv"), "w", newline="") as fh:
        wr = csv.DictWriter(fh, fieldnames=keys); wr.writeheader(); wr.writerows(rows)
    S0 = next(r for r in rows if r["name"] == "S0"); S1 = next(r for r in rows if r["name"] == "S1")
    ref = None
    if os.path.exists(a.k4b):
        x = next((x for x in csv.DictReader(open(a.k4b)) if x["arm"] == "H" and str(x["rate"]) == "5000"), None)
        if x: ref = {k: (fnum(v) if k not in ("seg", "name", "arm") else v) for k, v in x.items()}; ref["reserve"] = "1 (K4.1b)"; ref["name"] = "K4.1b r05-H"
    out = []
    H = sorted([r for r in rows if r["mode"] == "H"], key=lambda r: int(r["reserve"])) + ([ref] if ref else [])
    for r in H:
        o = dict(reserve=r["reserve"], run=r["name"], harvest=r.get("harvest"), lent=r.get("lent_bal"), unused=r.get("unused"), sec_on_p=r.get("sec_on_p"),
                 used_frac=r.get("used_frac"), hc_cores=((r.get("mon_cores") or 0) + (r.get("bal_cores") or 0)), mon_cores=r.get("mon_cores"), bal_cores=r.get("bal_cores"),
                 rebalances_per_s=r.get("rebalances_per_s"))
        for k in ("srv_p95_ms", "srv_p99_ms", "cl_p99_ms", "srv_p50_ms"):
            o[k] = r.get(k)
            for lab, st in (("S0", S0), ("S1", S1)):
                o[f"{k.replace('_ms', '')}_vs_{lab}"] = (r[k] / st[k]) if r.get(k) is not None and st.get(k) else None
            m = (S0[k] + S1[k]) / 2 if S0.get(k) and S1.get(k) else None
            o[f"{k.replace('_ms', '')}_vs_Smean"] = (r[k] / m) if r.get(k) is not None and m else None
        for k in ("pr_wait_ms_s", "pr_wait_self_ms_s", "pr_wait_sec_ms_s", "pr_wait_other_ms_s", "pr_after_sec_share", "pr_after_sec_s", "pr_disp_s", "pr_preempt_s",
                  "p_mig_s", "srv_p99_sub_min", "srv_p99_sub_max", "srv_p95_sub_min", "srv_p95_sub_max", "probe_cores", "lent_share_0", "lent_share_1", "lent_share_2", "lent_share_3"):
            o[k] = r.get(k)
        out.append(o)
    for st in (S0, S1):
        out.append(dict(reserve="standalone", run=st["name"], **{k: st.get(k) for k in ("srv_p50_ms", "srv_p95_ms", "srv_p99_ms", "cl_p99_ms", "pr_wait_ms_s", "pr_wait_self_ms_s",
                                                                                      "srv_p99_sub_min", "srv_p99_sub_max", "srv_p95_sub_min", "srv_p95_sub_max")}))
    keys = []
    for r in out:
        for k in r:
            if k not in keys: keys.append(k)
    with open(os.path.join(a.out, "k42e_reserve.csv"), "w", newline="") as fh:
        wr = csv.DictWriter(fh, fieldnames=keys); wr.writeheader(); wr.writerows(out)
    under = [o for o in out if o["run"].startswith("res") and o.get("srv_p99_vs_S0") is not None and o["srv_p99_vs_S0"] < 1.1 and o["srv_p99_vs_S1"] < 1.1]
    if under:
        b0 = min(under, key=lambda o: int(o["reserve"]))
        print(f"HEADLINE: smallest reserve with server p99 under x1.1 of both twins: {b0['reserve']} (x{b0['srv_p99_vs_S0']:.2f} S0, x{b0['srv_p99_vs_S1']:.2f} S1), harvest {b0['harvest']:.2f}")
    else:
        print("HEADLINE: none of 1, 2, 3")
    for o in out:
        print({k: (round(v, 3) if isinstance(v, float) else v) for k, v in o.items() if k in ("reserve", "run", "harvest", "used_frac", "hc_cores", "srv_p95_vs_S0", "srv_p95_vs_S1",
              "srv_p99_vs_S0", "srv_p99_vs_S1", "cl_p99_vs_S0", "cl_p99_vs_S1", "pr_wait_self_ms_s", "pr_wait_sec_ms_s", "pr_after_sec_share", "srv_p99_ms", "srv_p95_ms")})
    figure(out, a.out)


def figure(out, outdir):
    import matplotlib; matplotlib.use("Agg"); import matplotlib.pyplot as plt
    H = [o for o in out if o["run"].startswith("res")]; H.sort(key=lambda o: int(o["reserve"])); x = [int(o["reserve"]) for o in H]
    fig, axs = plt.subplots(1, 2, figsize=(13, 5))
    ax = axs[0]
    for k, mk, lab in (("srv_p99_vs", "D", "server p99"), ("srv_p95_vs", "o", "server p95"), ("cl_p99_vs", "^", "client p99")):
        for tw, col in (("S0", "#D85A30"), ("S1", "#2A6F97")):
            ax.plot(x, [o.get(f"{k}_{tw}") for o in H], mk + "-", color=col, ms=7, lw=1, alpha=0.9, label=f"{lab} ÷ {tw}")
    ax.axhline(1.0, color="#888888", lw=0.8); ax.axhline(1.1, color="#1D9E75", lw=1.0, ls="--", label="×1.1")
    ax.set_xticks(x); ax.set_xlabel("reserve (targetIdleCores = static = LOW_TIC)"); ax.set_ylabel("HC + burner ÷ standalone twin")
    ax.set_title("(a) the Primary's latency against each standalone twin", fontsize=9.5, loc="left"); ax.legend(fontsize=7, ncol=2)
    ax = axs[1]
    ax.plot(x, [o.get("harvest") for o in H], "o-", color="#2A6F97", label="harvest (lent ÷ unused)")
    ax.plot(x, [o.get("used_frac") for o in H], "s-", color="#D85A30", label="Secondary used ÷ unused")
    ax.plot(x, [o.get("pr_after_sec_share") * 10 if o.get("pr_after_sec_share") is not None else None for o in H], "^-", color="#555555", label="dispatches on a burner-held core × 10")
    ax.set_xticks(x); ax.set_xlabel("reserve"); ax.set_ylim(0, 1); ax.set_ylabel("fraction")
    ax.set_title("(b) harvest and contention", fontsize=9.5, loc="left"); ax.legend(fontsize=7.5)
    fig.suptitle("Fig. K4-2e — HC's idle-core reserve at the shipped placement (5 000 req/s, equal CPU weights; one 300 s run per reserve)", x=0.01, ha="left", fontsize=10)
    fig.tight_layout(rect=(0, 0, 1, 0.93)); fig.savefig(os.path.join(outdir, "figK4-2e.png"), dpi=130); plt.close(fig)
    print("figure written")


if __name__ == "__main__":
    main()
