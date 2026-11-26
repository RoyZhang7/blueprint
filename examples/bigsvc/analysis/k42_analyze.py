#!/usr/bin/env python3
"""Phase K4.2: HC moved towards the paper's set-up one change at a time, at 5 000 req/s, on K4.1b's configuration
(burner BestEffort, equal CPU weights, lean probe in every run). Steps (queue-k4.2): a Monitor and Balancer on one core
(CPU 26), b a + the 60 s Observation Phase, c a + BALANCE_INTERRUPTS + the IRQ procfs path, d a + b + c; per step an
HC + burner run (H) and an HC-only run (C); standalone twins S0 (start) and S1 (end).

Per step: harvest fraction (H), the Secondary's use of Primary cores, HC's own cores (Monitor + Balancer), the Primary's
p50 / p95 / p99 / p99.9 (server) and client p99, as ratios to the standalone (mean of S0 and S1; their spread is the
drift) and to K4.1b's HC + burner run at 5 000 req/s (results/k4b/k4b_segments.csv).
usage: k42_analyze.py K42_DIR [--k4b results/k4b/k4b_segments.csv] [--out results/k42]
"""
import argparse, csv, json, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from k4_analyze import client, sampler, jl
from k4b_analyze import extra

P = [4, 6, 8, 10]
STEP = {"a": "Monitor + Balancer on one core", "b": "a + 60 s Observation Phase", "c": "a + BALANCE_INTERRUPTS + IRQ procfs path", "d": "a + b + c"}
LAT = ("srv_p50_ms", "srv_p95_ms", "srv_p99_ms", "srv_p999_ms", "cl_p50_ms", "cl_p90_ms", "cl_p99_ms")


def fnum(x):
    try: return float(x)
    except Exception: return None


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("root"); ap.add_argument("--k4b", default="results/k4b/k4b_segments.csv"); ap.add_argument("--out", default="results/k42")
    a = ap.parse_args(); os.makedirs(a.out, exist_ok=True); R = a.root
    W = {w["seg"]: w for w in json.load(open(os.path.join(R, "windows.json")))}
    rec = json.load(open(os.path.join(R, "recorder_reduced.json")))
    bp = os.path.join(R, "balancer_reduced.json"); bal = json.load(open(bp)) if os.path.exists(bp) else {}
    rows = []
    for s in jl(os.path.join(R, "segments.jsonl")):
        seg = s["seg"]; w = W[seg]; d = os.path.join(R, seg); rr = rec.get(seg, {})
        r = dict(seg=seg, name=s["name"], mode=s["mode"], step=s.get("step", "-"), rate=s["rate"], window_s=(w["t1_ms"] - w["t0_ms"]) / 1000.0,
                 srv_rps=rr["requests"] / rr["dur_s"] if rr else None, srv_err=rr.get("errors"), prim_cores_rec=rr.get("cpu_cores"), mhz=rr.get("mhz"),
                 srv_p50_ms=rr.get("tot_p50_ms"), srv_p95_ms=rr.get("tot_p95_ms"), srv_p99_ms=rr.get("tot_p99_ms"), srv_p999_ms=rr.get("tot_p999_ms"),
                 srv_mean_ms=rr.get("tot_mean_ms"), srv_q_p99_ms=rr.get("q_p99_ms"))
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
        r["hc_cores"] = (r.get("mon_cores") or 0) + (r.get("bal_cores") or 0) if s["mode"] != "S" else 0.0
        if s["mode"] != "S":
            bl = os.path.join(d, "balancer", "ls.txt")
            r["balancer_log_bytes"] = next((int(l.split()[4]) for l in open(bl) if l.rstrip().endswith("balancer.log")), None) if os.path.exists(bl) else None
        rows.append(r)
    keys = []
    for r in rows:
        for k in r:
            if k not in keys: keys.append(k)
    with open(os.path.join(a.out, "k42_segments.csv"), "w", newline="") as fh:
        wr = csv.DictWriter(fh, fieldnames=keys); wr.writeheader(); wr.writerows(rows)
    S = [r for r in rows if r["mode"] == "S"]
    stand = {k: (sum(r[k] for r in S if r.get(k) is not None) / len([r for r in S if r.get(k) is not None])) if any(r.get(k) is not None for r in S) else None for k in LAT}
    drift = {k: (S[-1][k] / S[0][k]) if len(S) >= 2 and S[0].get(k) and S[-1].get(k) is not None else None for k in LAT}
    ref = None
    if os.path.exists(a.k4b):
        ref = next((x for x in csv.DictReader(open(a.k4b)) if x["arm"] == "H" and str(x["rate"]) == "5000"), None)
    out = []
    for st in ("a", "b", "c", "d"):
        for mode in ("H", "C"):
            x = next((r for r in rows if r["step"] == st and r["mode"] == mode), None)
            if not x: continue
            o = dict(step=st, step_name=STEP[st], arm=mode, seg=x["seg"])
            for k in LAT:
                o[k] = x.get(k)
                o[k.replace("_ms", "") + "_vs_S"] = (x[k] / stand[k]) if x.get(k) is not None and stand.get(k) else None
                rv = fnum(ref.get(k)) if ref else None
                o[k.replace("_ms", "") + "_vs_k41bH"] = (x[k] / rv) if mode == "H" and x.get(k) is not None and rv else None
            for k in ("prim_cores", "unused", "lent_bal", "harvest", "sec_on_p", "used_frac", "mon_cores", "bal_cores", "hc_cores", "rebalances_per_s",
                      "pr_after_sec_share", "pr_wait_sec_us_req", "probe_cores", "srv_p99_sub_min", "srv_p99_sub_max", "srv_p95_sub_min", "srv_p95_sub_max",
                      "balancer_log_bytes", "node_busy_cores"):
                o[k] = x.get(k)
            out.append(o)
    out.append(dict(step="S", step_name="standalone S0, S1 (mean; drift = S1 / S0)", arm="S", seg=" ".join(r["seg"] for r in S),
                    **{k: stand[k] for k in LAT}, **{k.replace("_ms", "") + "_vs_S": drift[k] for k in LAT}))
    if ref:
        out.append(dict(step="K4.1b", step_name="K4.1b HC + burner at 5 000 req/s (Monitor CPU 26, Balancer CPU 24)", arm="H", seg=ref["seg"],
                        **{k: fnum(ref.get(k)) for k in LAT}, harvest=fnum(ref.get("harvest")), used_frac=fnum(ref.get("used_frac")),
                        mon_cores=fnum(ref.get("mon_cores")), bal_cores=fnum(ref.get("bal_cores"))))
    keys = []
    for r in out:
        for k in r:
            if k not in keys: keys.append(k)
    with open(os.path.join(a.out, "k42_steps.csv"), "w", newline="") as fh:
        wr = csv.DictWriter(fh, fieldnames=keys); wr.writeheader(); wr.writerows(out)
    for o in out:
        print({k: (round(v, 3) if isinstance(v, float) else v) for k, v in o.items() if k in ("step", "arm", "harvest", "used_frac", "hc_cores", "srv_p95_vs_S", "srv_p99_vs_S",
              "srv_p95_vs_k41bH", "srv_p99_vs_k41bH", "cl_p99_vs_S", "srv_p99_ms", "balancer_log_bytes")})
    figure(out, a.out)


def figure(out, outdir):
    import matplotlib; matplotlib.use("Agg"); import matplotlib.pyplot as plt
    steps = ["a", "b", "c", "d"]; x = list(range(len(steps)))
    H = {o["step"]: o for o in out if o["arm"] == "H" and o["step"] in steps}; C = {o["step"]: o for o in out if o["arm"] == "C" and o["step"] in steps}
    fig, axs = plt.subplots(1, 3, figsize=(17, 5.2))
    ax = axs[0]
    for j, (k, col, lab) in enumerate((("unused", "#9AA5B1", "Primary unused"), ("lent_bal", "#2A6F97", "lent by HC"), ("sec_on_p", "#D85A30", "Secondary ran on Primary cores"))):
        ax.bar([xi + (j - 1) * 0.26 for xi in x], [(H[s].get(k) or 0) if s in H else 0 for s in steps], 0.24, color=col, label=lab)
    for xi, s in zip(x, steps):
        if s in H and H[s].get("harvest") is not None:
            ax.annotate(f"harvest {H[s]['harvest']:.2f}\nHC {H[s]['hc_cores']:.2f} cores", (xi, (H[s].get("unused") or 0) + 0.08), ha="center", fontsize=7.5)
    ax.set_xticks(x); ax.set_xticklabels(steps); ax.set_ylabel("cores"); ax.set_ylim(0, 4.2)
    ax.set_title("(a) HC + burner: cores (a one core; b + 60 s observation; c + IRQ; d all)", fontsize=9, loc="left"); ax.legend(fontsize=7.5, loc="upper right")
    ax = axs[1]
    for j, (k, mk, lab) in enumerate((("srv_p95_vs_S", "o", "server p95"), ("srv_p99_vs_S", "D", "server p99"), ("cl_p99_vs_S", "^", "client p99"))):
        ax.plot([xi + (j - 1) * 0.1 for xi in x], [H[s].get(k) if s in H else None for s in steps], mk, color="#D85A30", ms=7, ls="", label=f"HC + burner, {lab}")
        ax.plot([xi + (j - 1) * 0.1 + 0.04 for xi in x], [C[s].get(k) if s in C else None for s in steps], mk, color="#2A6F97", ms=6, ls="", mfc="white", label=f"HC only, {lab}")
    ax.axhline(1.0, color="#888888", lw=0.8); ax.axhline(1.1, color="#1D9E75", lw=1.0, ls="--", label="×1.1")
    ax.set_xticks(x); ax.set_xticklabels(steps); ax.set_ylabel("÷ standalone (mean of S0, S1)")
    ax.set_title("(b) the Primary's latency against the standalone twin", fontsize=9.5, loc="left"); ax.set_ylim(0.8, 7.8); ax.legend(fontsize=7, loc="upper left", ncol=3)
    ax = axs[2]
    for j, (k, mk, lab) in enumerate((("srv_p95_vs_k41bH", "o", "server p95"), ("srv_p99_vs_k41bH", "D", "server p99"), ("cl_p99_vs_k41bH", "^", "client p99"))):
        ax.plot([xi + (j - 1) * 0.1 for xi in x], [H[s].get(k) if s in H else None for s in steps], mk, color="#1B2430", ms=7, ls="", label=lab)
    ax.axhline(1.0, color="#888888", lw=0.8)
    ax.set_xticks(x); ax.set_xticklabels(steps); ax.set_ylabel("÷ K4.1b HC + burner at 5 000 req/s")
    ax.set_title("(c) HC + burner against K4.1b's (shipped placement)", fontsize=9.5, loc="left"); ax.legend(fontsize=7.5, loc="upper left")
    fig.suptitle("Fig. K4-2 — HC towards the paper's set-up, one change at a time (5 000 req/s, equal CPU weights; one 300 s run per arm)", x=0.01, ha="left", fontsize=10)
    fig.tight_layout(rect=(0, 0, 1, 0.93)); fig.savefig(os.path.join(outdir, "figK4-2.png"), dpi=130); plt.close(fig)
    print("figure written")


if __name__ == "__main__":
    main()
