#!/usr/bin/env python3
"""Phase G Addendum F: per-request run-queue delay by holder and softirq delay, joined from probe v5's per-resume records
(<run>/probe-resume.jsonl: {"r":tid,"ns","d","a","s","o","c","w","t"} per resume, "w" = 1 after a sleep / 0 after a
preemption; {"k":tid,"ns","v","dl","cl","t"} per wake-up that happened inside a softirq on the cpuset, dl = raise ->
wake-up ns, cl = the class running at the raise: 0 own, 1 aggressor, 2 root, 3 other, 4 idle) and the recorder's raw
samples (raw = total ns, rawp = processing, rawq = queue, rawt = thread id, rawe = completion time in us since the
epoch, image g6+). A request spans [end - total, end] on its thread; the arrival wake-up (the thread's last post-sleep
resume before the span, within 20 ms) is the delay between the request's packet waking the thread and the thread
running, which the recorder's clock (tap at message begin) does not contain.

Two latency streams per request:
  server  = total (tap -> end), the recorder's stream; the resumes inside the span are its run-queue delays;
  arrival = rq_pre + softirq_pre + total, the same request measured from its wake-up (what the caller sees of this hop
            apart from the wire); the arrival resume's holder split is added to rq_*.
Composition (additive, ms): oncpu = proc - rq_proc (processing wall time minus preemption gaps); blocked' = blocked -
rq_blk - softirq (the store calls minus the wake-up delays the probe saw in them); unattributed = stream total - oncpu -
rq_all - softirq - blocked' (the clamping residue). Tail = requests >= the step's p95 of the stream (per variant).
Output <run>/f/requests.csv, the per-step table, and with --csv the Addendum F CSV (columns run, step, req_s, variant,
total_ms, oncpu_ms, rq_foreign_ms, rq_self_ms, rq_other_ms, softirq_ms, blocked_ms, unattributed_ms, + softirq_aggr_ms,
rq_pre_ms, n; variant = <stream>:<twin|stressed|growth|share>-<med|mean>).

usage: g7_tail.py STRESSED --twin TWIN --svc unique-id-service --run-label A [--q 0.95] [--csv out.csv] [--md out.md]
"""
import argparse, bisect, collections, csv, json, os, sys
import numpy as np
sys.path.insert(0, os.path.dirname(__file__))
from analyze import load_run
from dictionary import assign

COLS = ["total_ms", "oncpu_ms", "rq_foreign_ms", "rq_self_ms", "rq_other_ms", "softirq_ms", "blocked_ms", "unattributed_ms"]
EXTRA = ["softirq_aggr_ms", "rq_pre_ms"]


def load_resumes(d):
    R = collections.defaultdict(list); K = collections.defaultdict(list); start = None
    p = os.path.join(d, "probe-resume.jsonl")
    if not os.path.exists(p): return {}, {}, None
    for line in open(p):
        try: o = json.loads(line)
        except Exception: continue
        if o.get("event") == "start": start = o; continue
        if "r" in o:
            if "h" in o:
                h = o["h"]; d_ = o["d"]; a = d_ if h == 1 else 0; s = d_ if h == 0 else 0; oo = d_ if h in (2, 3) else 0
            else: a, s, oo = o["a"], o["s"], o["o"]
            R[o["r"]].append((o["ns"], o["d"], a, s, oo, o.get("w", 1)))
        elif "k" in o:
            K[o["k"]].append((o["ns"], o["dl"], o["cl"]))
    for t in R: R[t].sort()
    for t in K: K[t].sort()
    return R, K, start


def requests_of(d, svc, R, K, offset_ns):
    Rn = load_run(d, svc=svc); W = Rn["windows"]
    step_of = [(-1 if s is None else s) for s in assign(W, Rn["steps"], "step")]; rate = {s["step"]: s["rate"] for s in Rn["steps"]}
    Rt = {t: np.array([x[0] for x in v]) for t, v in R.items()}; Kt = {t: np.array([x[0] for x in v]) for t, v in K.items()}
    reqs = []
    for w, st in zip(W, step_of):
        if st < 0 or "rawe" not in w or "rawt" not in w: continue
        for e in w["raw"]:
            tot = w["raw"][e]; proc = w.get("rawp", {}).get(e, []); que = w.get("rawq", {}).get(e, []); tid = w["rawt"].get(e, []); end = w["rawe"].get(e, [])
            for i in range(min(len(tot), len(tid), len(end))):
                e_ns = end[i] * 1000 - offset_ns; s_ns = e_ns - tot[i]; t = int(tid[i])
                rqf = rqs = rqo = rqp = rqb = 0.0; sirq = sirq_a = 0.0; pre = (0.0, 0.0, 0.0, 0.0); spre = spre_a = 0.0
                if t in Rt:
                    arr = Rt[t]; lo = bisect.bisect_left(arr, s_ns); hi = bisect.bisect_right(arr, e_ns)
                    for x in R[t][lo:hi]:
                        rqf += x[2]; rqs += x[3]; rqo += x[4]
                        if x[5] == 1: rqb += x[1]
                        else: rqp += x[1]
                    if lo > 0:
                        x = R[t][lo - 1]
                        if x[5] == 1 and s_ns - x[0] <= 20e6: pre = (x[1], x[2], x[3], x[4])
                if t in Kt:
                    arr = Kt[t]; lo = bisect.bisect_left(arr, s_ns); hi = bisect.bisect_right(arr, e_ns)
                    for x in K[t][lo:hi]:
                        sirq += x[1]
                        if x[2] == 1: sirq_a += x[1]
                    if lo > 0 and s_ns - K[t][lo - 1][0] <= 20e6:
                        spre = K[t][lo - 1][1]; spre_a = spre if K[t][lo - 1][2] == 1 else 0.0
                total = tot[i] / 1e6; pr = (proc[i] if i < len(proc) else tot[i]) / 1e6; blk = max(0.0, total - pr); qu = (que[i] if i < len(que) else 0) / 1e6
                rq_all = (rqf + rqs + rqo) / 1e6; sirq /= 1e6; sirq_a /= 1e6; rqp /= 1e6; rqb /= 1e6
                oncpu = max(0.0, pr - rqp); blk2 = max(0.0, blk - rqb - sirq)
                r = dict(t=w["t"], step=st, req_s=rate.get(st, 0), ep=e, tid=t, total_ms=total, proc_ms=pr, queue_ms=qu, blocked_raw_ms=blk,
                         rq_foreign_ms=rqf / 1e6, rq_self_ms=rqs / 1e6, rq_other_ms=rqo / 1e6, rq_proc_ms=rqp, rq_blk_ms=rqb, softirq_ms=sirq, softirq_aggr_ms=sirq_a,
                         oncpu_ms=oncpu, blocked_ms=blk2, unattributed_ms=max(0.0, total - oncpu - rq_all - sirq - blk2),
                         rq_pre_ms=pre[0] / 1e6, rq_pre_foreign_ms=pre[1] / 1e6, rq_pre_self_ms=pre[2] / 1e6, rq_pre_other_ms=pre[3] / 1e6, softirq_pre_ms=spre / 1e6, softirq_pre_aggr_ms=spre_a / 1e6)
                # the arrival stream: the same request from its wake-up
                r["arr_total_ms"] = r["rq_pre_ms"] + r["softirq_pre_ms"] + total
                r["arr_rq_foreign_ms"] = r["rq_foreign_ms"] + r["rq_pre_foreign_ms"]; r["arr_rq_self_ms"] = r["rq_self_ms"] + r["rq_pre_self_ms"]; r["arr_rq_other_ms"] = r["rq_other_ms"] + r["rq_pre_other_ms"]
                r["arr_softirq_ms"] = sirq + r["softirq_pre_ms"]; r["arr_softirq_aggr_ms"] = sirq_a + r["softirq_pre_aggr_ms"]; r["arr_oncpu_ms"] = oncpu; r["arr_blocked_ms"] = blk2
                r["arr_unattributed_ms"] = max(0.0, r["arr_total_ms"] - oncpu - r["arr_rq_foreign_ms"] - r["arr_rq_self_ms"] - r["arr_rq_other_ms"] - r["arr_softirq_ms"] - blk2)
                reqs.append(r)
    return reqs, rate


def tail_stats(reqs, q, prefix):
    out = {}
    cols = [prefix + c for c in COLS] + [prefix + "softirq_aggr_ms", "rq_pre_ms"]
    for st in sorted(set(r["step"] for r in reqs)):
        g = [r for r in reqs if r["step"] == st]; tot = np.array([r[prefix + "total_ms"] for r in g]); p95 = np.percentile(tot, 100 * q)
        tail = [r for r in g if r[prefix + "total_ms"] >= p95]
        out[st] = dict(n=len(g), n_tail=len(tail), p95=p95, p50=float(np.median(tot)), req_s=g[0]["req_s"],
                       med={c: float(np.median([r[c] for r in tail])) for c in cols}, mean={c: float(np.mean([r[c] for r in tail])) for c in cols})
    return out


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("stressed"); ap.add_argument("--twin", required=True); ap.add_argument("--svc", default=None); ap.add_argument("--run-label", default="A")
    ap.add_argument("--q", type=float, default=0.95); ap.add_argument("--csv", default=None); ap.add_argument("--md", default=None)
    a = ap.parse_args()
    data = {}; info = []
    for lab, d in (("twin", a.twin), ("stressed", a.stressed)):
        R, K, start = load_resumes(d)
        if start is None: sys.exit(f"{d}: no probe-resume.jsonl")
        reqs, rate = requests_of(d, a.svc, R, K, start["offset_ns"])
        if not reqs: sys.exit(f"{d}: no requests with rawe/rawt (image < g6 or RAW=0)")
        os.makedirs(os.path.join(d, "f"), exist_ok=True)
        with open(os.path.join(d, "f", "requests.csv"), "w", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=list(reqs[0].keys())); w.writeheader(); w.writerows(reqs)
        covered = np.mean([(r["rq_foreign_ms"] + r["rq_self_ms"] + r["rq_other_ms"] + r["rq_pre_ms"]) > 0 for r in reqs])
        line = f"{lab}: {os.path.basename(d)}: {len(reqs)} requests on {len(set(r['tid'] for r in reqs))} threads; {sum(len(v) for v in R.values())} resumes, {sum(len(v) for v in K.values())} softirq wake-ups; requests with a resume in the span or at arrival: {100*covered:.1f} %"
        print(line); info.append(line)
        data[lab] = {"server": tail_stats(reqs, a.q, ""), "arrival": tail_stats(reqs, a.q, "arr_")}
    rows = []; lines = []
    for stream, prefix in (("server", ""), ("arrival", "arr_")):
        hdr = ["step (req/s)", "variant", "n tail / n", "p50 / p95 (ms)"] + [c.replace("_ms", "") for c in COLS] + ["softirq by aggr", "rq at arrival"]
        lines += [f"**{a.run_label} — stream {stream}** (tail = requests ≥ the step's p95 of this stream)", "", "| " + " | ".join(hdr) + " |", "|" + "---|" * len(hdr)]
        cols = [prefix + c for c in COLS] + [prefix + "softirq_aggr_ms", "rq_pre_ms"]
        for st in sorted(data["stressed"][stream]):
            if st not in data["twin"][stream]: continue
            T, S = data["twin"][stream][st], data["stressed"][stream][st]
            for lab, D in (("twin", T), ("stressed", S)):
                for kind in ("med", "mean"):
                    v = D[kind]; rows.append(dict(run=a.run_label, step=st, req_s=D["req_s"], variant=f"{stream}:{lab}-{kind}", n=D["n_tail"], **{c: round(v[prefix + c], 4) for c in COLS}, softirq_aggr_ms=round(v[prefix + "softirq_aggr_ms"], 4), rq_pre_ms=round(v["rq_pre_ms"], 4)))
                    lines.append("| " + " | ".join([f"{st} ({D['req_s']})", f"{lab} {kind}", f"{D['n_tail']} / {D['n']}", f"{D['p50']:.3f} / {D['p95']:.3f}"] + [f"{v[c]:.3f}" for c in cols]) + " |")
            for kind in ("med", "mean"):
                g = {c: S[kind][c] - T[kind][c] for c in cols}; dt = g[prefix + "total_ms"]
                rows.append(dict(run=a.run_label, step=st, req_s=S["req_s"], variant=f"{stream}:growth-{kind}", n=S["n_tail"], **{c: round(g[prefix + c], 4) for c in COLS}, softirq_aggr_ms=round(g[prefix + "softirq_aggr_ms"], 4), rq_pre_ms=round(g["rq_pre_ms"], 4)))
                rows.append(dict(run=a.run_label, step=st, req_s=S["req_s"], variant=f"{stream}:share-{kind}", n=S["n_tail"], **{c: (round(g[prefix + c] / dt, 4) if abs(dt) > 1e-9 else float("nan")) for c in COLS}, softirq_aggr_ms=(round(g[prefix + "softirq_aggr_ms"] / dt, 4) if abs(dt) > 1e-9 else float("nan")), rq_pre_ms=(round(g["rq_pre_ms"] / dt, 4) if abs(dt) > 1e-9 else float("nan"))))
                lines.append("| " + " | ".join([f"{st}", f"growth {kind}", "", f"p95 ×{S['p95'] / T['p95']:.2f}, p50 ×{S['p50'] / T['p50']:.2f}"] + [f"{g[c]:+.3f}" for c in cols]) + " |")
                lines.append("| " + " | ".join([f"{st}", f"share {kind}", "", ""] + [(f"{g[c] / dt:+.2f}" if abs(dt) > 1e-9 else "—") for c in cols]) + " |")
        lines.append("")
    out = "\n".join(info) + "\n\n" + "\n".join(lines); print("\n".join(lines))
    if a.md: open(a.md, "w", encoding="utf-8").write(out + "\n")
    if a.csv:
        with open(a.csv, "w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=["run", "step", "req_s", "variant"] + COLS + EXTRA + ["n"]); w.writeheader(); w.writerows(rows)


if __name__ == "__main__":
    main()
