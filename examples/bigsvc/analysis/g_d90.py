#!/usr/bin/env python3
"""Phase G A.3 (side track): a p90 dictionary D90 = Fbar90 + h90(rho) K from the existing profiling sweep, scored on the
mix-shift rows against the twin-run p90 truth, inside the guard range; the p50 pipeline run through the same code is
the reference. Fbar90 = the 0.9 quantile of the window mix's mixture of per-endpoint floor distributions (the 41
quantile points per endpoint from the sweep's lowest step, share-weighted); h90(rho) = (pooled p90 of the sweep's rho
bin - Fbar90 of the bin's mix) / K of the bin's mix; Lat90 = pooled p90 of the second's samples; %ext90 =
(Lat90 - D90) / Lat90 with 5 s causal smoothing; truth90 = (Lat90 - Lat90_twin at the same second since start) / Lat90.
Cells: slow = (Latq - Fbarq) / Latq >= 0.5, ext = %ext >= 0.5; guards as in Phase F (rho outside the profiled range;
wait beyond the profiled max when that max exceeds 3x the floor noise of the same quantile).

usage: g_d90.py --dict SWEEP --row "label|RUN|TWIN" [--row ...] [--costs c.json] [--out DIR] [--smooth 5]
Writes DIR/d90_rows.csv (one line per row and quantile) and DIR/figG-D90-<label>.png for the first row.
"""
import argparse, csv, os, sys
import numpy as np
import matplotlib; matplotlib.use("Agg"); import matplotlib.pyplot as plt
sys.path.insert(0, os.path.dirname(__file__))
from analyze import load_run
from dictionary import (EP_A, costs_of_run, cpuset_size, lat_samples, rho_series, settled_windows, overloaded_steps,
                        floor_quantiles_lowest_step, floors_lowest_step, floor_noise_lowest_step, assign)
from b4_compare import causal_gauss

QS = (0.5, 0.9)


def wquant(pts, wts, q):
    o = np.argsort(pts); c = np.cumsum(wts[o]); return float(pts[o][min(len(pts) - 1, int(np.searchsorted(c, q * c[-1])))])


def mix_q(shares, flq, fl, q):
    tot = float(sum(shares.values())); sh = {e: v / tot for e, v in shares.items() if v > 0}
    pts, wts = [], []
    for e, v in sh.items():
        qq = flq.get(e)
        if qq is None or len(qq) == 0: qq = np.array([fl.get(e, 0.0)])
        pts.append(np.asarray(qq, float)); wts.append(np.full(len(qq), v / len(qq)))
    return wquant(np.concatenate(pts), np.concatenate(wts), q)


def mix_K(shares, c_ms):
    tot = float(sum(shares.values())); sh = {e: v / tot for e, v in shares.items() if v > 0}
    cb = sum(v * c_ms[e] for e, v in sh.items()); c2 = sum(v * c_ms[e] ** 2 for e, v in sh.items())
    return c2 / (2 * cb) if cb > 0 else np.nan


def build(Rd, costs, settle, nbins=24):
    k = cpuset_size(Rd["meta"].get("cpuset", "0-7")); c_ms, _ = costs_of_run(Rd, costs); names = Rd["run"]["endpoints"]
    W, step_of, _ = settled_windows(Rd, settle)
    fl = dict(zip(names, Rd["run"]["floors_ms"])) if "floors_ms" in Rd["run"] else {e: 0.0 for e in names}
    fl_meas = {r[0]: r[2] for r in floors_lowest_step(W, step_of, names, fl)} if False else None
    flq = floor_quantiles_lowest_step(W, step_of, names, fl)
    over = overloaded_steps(Rd, W, step_of)
    keep = [i for i, s in enumerate(step_of) if s not in over]
    W = [W[i] for i in keep]; step_of = [step_of[i] for i in keep]
    rho = rho_series(W, c_ms, 1.0) / k
    edges = np.linspace(0, 1.05, nbins + 1); mids = (edges[:-1] + edges[1:]) / 2
    low = min(step_of)
    D = {}
    for q in QS:
        pq = np.full(nbins, np.nan); Fq = np.full(nbins, np.nan); Kb = np.full(nbins, np.nan)
        for b in range(nbins):
            ws = [w for w, r in zip(W, rho) if edges[b] <= r < edges[b + 1]]
            if len(ws) < 20: continue
            samp = np.concatenate([lat_samples(w, e)[0] for w in ws for e in w["ep"]]) / 1e6
            if len(samp) < 50: continue
            sh = {}
            for w in ws:
                for e, v in w["ep"].items(): sh[e] = sh.get(e, 0) + v[EP_A]
            pq[b] = np.quantile(samp, q); Fq[b] = mix_q(sh, flq, fl, q); Kb[b] = mix_K(sh, c_ms)
        h = (pq - Fq) / Kb
        ok = np.isfinite(h)
        # floor noise at this quantile: second-to-second spread of the pooled q over the lowest step's windows
        lowq = [np.quantile(np.concatenate([lat_samples(w, e)[0] for e in w["ep"]]) / 1e6, q) for w, s in zip(W, step_of) if s == low and w["n"] > 5]
        noise = float(np.percentile(lowq, 95) - np.percentile(lowq, 5)) if len(lowq) > 10 else 0.0
        wmax = float(np.nanmax(pq - Fq)) if ok.any() else np.nan
        D[q] = dict(mids=mids[ok], h=h[ok], rho_min=float(mids[ok].min()), rho_max=float(mids[ok].max()), wmax=wmax, noise=noise, guard_on=wmax > 3 * noise)
    return dict(k=k, c_ms=c_ms, fl=fl, flq=flq, D=D, label=os.path.basename(Rd["dir"]))


def per_second(R, dct, q):
    """pooled quantile q, rho, Fbar_q(mix), K(mix) per second of a run."""
    W = R["windows"]; k = dct["k"]; c_ms = dct["c_ms"]
    rho_w = rho_series(W, c_ms, 1.0) / k
    secs = {}
    for w, r in zip(W, rho_w):
        s = w["t"] // 1000
        d = secs.setdefault(s, dict(samp=[], sh={}, rho=[], a=0))
        for e, v in w["ep"].items():
            x, _ = lat_samples(w, e); d["samp"].append(x); d["sh"][e] = d["sh"].get(e, 0) + v[EP_A]
        d["rho"].append(r); d["a"] += w["a"]
    rows = []
    for s in sorted(secs):
        d = secs[s]; samp = np.concatenate(d["samp"]) / 1e6 if d["samp"] else np.array([])
        if len(samp) < 5 or d["a"] == 0: continue
        rows.append(dict(t=s, lat=float(np.quantile(samp, q)), rho=float(np.mean(d["rho"])), F=mix_q(d["sh"], dct["flq"], dct["fl"], q), K=mix_K(d["sh"], c_ms), lam=d["a"]))
    return rows


def score(rows_run, rows_twin, dct, q, smooth):
    Dq = dct["D"][q]; t0 = rows_run[0]["t"]; tw = {r["t"] - rows_twin[0]["t"]: r["lat"] for r in rows_twin} if rows_twin else {}
    out = []
    for r in rows_run:
        h = float(np.interp(r["rho"], Dq["mids"], Dq["h"])); D = r["F"] + h * r["K"]
        oor = r["rho"] < Dq["rho_min"] - 0.02 or r["rho"] > Dq["rho_max"] + 0.02
        ext = min(1.0, max(0.0, (r["lat"] - D) / r["lat"])); y = min(1.0, max(0.0, (r["lat"] - r["F"]) / r["lat"]))
        near = [tw[dt] for dt in (r["t"] - t0 - 1, r["t"] - t0, r["t"] - t0 + 1) if dt in tw]
        truth = min(1.0, max(0.0, (r["lat"] - np.mean(near)) / r["lat"])) if near else None
        out.append(dict(t=r["t"], lat=r["lat"], D=D, F=r["F"], rho=r["rho"], ext_raw=ext, y_raw=y, truth_raw=truth, oor=oor))
    ts = np.array([o["t"] for o in out], float)
    ext_s = causal_gauss([o["ext_raw"] for o in out], ts, smooth); lat_s = causal_gauss([o["lat"] for o in out], ts, smooth)
    y_s = causal_gauss([o["y_raw"] for o in out], ts, smooth)
    tr = [o["truth_raw"] if o["truth_raw"] is not None else np.nan for o in out]; tr_s = causal_gauss(tr, ts, smooth)
    for o, e, l, yy, t in zip(out, ext_s, lat_s, y_s, tr_s):
        o["ext"] = e; o["y"] = yy; o["truth"] = t
        o["wait_guard"] = Dq["guard_on"] and (l - o["F"] > Dq["wmax"])
        o["guard"] = o["oor"] or o["wait_guard"]
    return out


def cells(o, key):
    if o["guard"] and key == "ext": return "n/a"
    v = o[key]; return ("slow" if o["y"] >= 0.5 else "fast") + "/" + ("ext" if v >= 0.5 else "int")


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--dict", required=True); ap.add_argument("--row", action="append", required=True)
    ap.add_argument("--costs", default=None); ap.add_argument("--out", default=None); ap.add_argument("--smooth", type=float, default=5.0); ap.add_argument("--settle", type=float, default=5.0)
    a = ap.parse_args(); out = a.out or os.path.dirname(a.dict.rstrip("/")) + "/g1"; os.makedirs(out, exist_ok=True)
    dct = build(load_run(a.dict), a.costs, a.settle)
    for q in QS:
        Dq = dct["D"][q]; print(f"dictionary {dct['label']} q={q}: h over rho {Dq['rho_min']:.2f}..{Dq['rho_max']:.2f}, W{int(q*100)}max {Dq['wmax']:.2f} ms, floor noise {Dq['noise']:.2f} ms, wait guard {'on' if Dq['guard_on'] else 'off'}; h at rho 0.5 {np.interp(0.5, Dq['mids'], Dq['h']):.2f}, 0.65 {np.interp(0.65, Dq['mids'], Dq['h']):.2f}")
    res = []; first = None
    for spec in a.row:
        lab, run, twin = (spec.split("|") + [""] * 3)[:3]
        R = load_run(run); Rt = load_run(twin) if twin else None
        for q in QS:
            rr = per_second(R, dct, q); rt = per_second(Rt, dct, q) if Rt else []
            sc = score(rr, rt, dct, q, a.smooth)
            scored = [o for o in sc if o["truth"] is not None and np.isfinite(o["truth"])]
            tc = [cells(o, "truth") for o in scored]; cc = [cells(o, "ext") for o in scored]
            strict = np.mean([x == y for x, y in zip(cc, tc)]) if scored else np.nan
            spoke = [(x, y) for x, y in zip(cc, tc) if x != "n/a"]; acc = np.mean([x == y for x, y in spoke]) if spoke else np.nan
            abst = 1 - len(spoke) / len(scored) if scored else np.nan
            err = np.array([o["ext"] - o["truth"] for o in scored if not o["guard"]])
            inrange = [o for o in scored if not o["guard"]]
            res.append(dict(row=lab, q=q, n=len(scored), strict=strict, when_speaks=acc, abstained=abst, truth_ext=sum(1 for x in tc if x.endswith("/ext")), truth_slow=sum(1 for x in tc if x.startswith("slow")),
                            err_median=float(np.median(err)) if len(err) else np.nan, err_within_015=float(np.mean(np.abs(err) <= 0.15)) if len(err) else np.nan, in_guard_range=len(inrange),
                            lat_med=float(np.median([o["lat"] for o in scored])) if scored else np.nan, D_med=float(np.median([o["D"] for o in scored])) if scored else np.nan))
            print(f"{lab:40s} q={q}: n {len(scored):4d} in guard range {len(inrange):4d}  truth slow {res[-1]['truth_slow']:4d} ext {res[-1]['truth_ext']:4d} | strict {100*strict:5.1f} speaks {100*acc:5.1f} abst {100*abst:5.1f} | err median {res[-1]['err_median']:+.3f}, within 0.15: {100*res[-1]['err_within_015']:.0f} %")
            if first is None: first = (lab, {})
            if first[0] == lab: first[1][q] = sc
    with open(os.path.join(out, "d90_rows.csv"), "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(res[0].keys())); w.writeheader(); w.writerows(res)
    # figure: first row, p50 (left) and p90 (right): Lat, D, twin; below ext vs truth
    lab, scs = first
    fig, axs = plt.subplots(2, 2, figsize=(13, 6.5), sharex="col")
    for j, q in enumerate(QS):
        sc = scs[q]; ts = np.array([o["t"] for o in sc]) - sc[0]["t"]
        a0 = axs[0, j]; a0.plot(ts, [o["lat"] for o in sc], color="#1B2430", lw=1.2, label=f"observed p{int(q*100)}")
        a0.plot(ts, [o["D"] for o in sc], color="#2A6F97", lw=1.6, label=f"D{int(q*100)}[ρ; mix] = F̄{int(q*100)} + h{int(q*100)}(ρ)·K")
        a0.plot(ts, [o["lat"] - (o["lat"] * o["truth"] if o["truth"] is not None and np.isfinite(o["truth"]) else np.nan) for o in sc], color="#1D9E75", lw=1.0, ls="--", label="twin (no aggressor)")
        a0.set_ylabel("latency (ms)"); a0.set_title(f"{'ab'[j]} · p{int(q*100)}", loc="left"); a0.legend(fontsize=8, loc="upper left")
        a1 = axs[1, j]; a1.plot(ts, [o["ext"] for o in sc], color="#2A6F97", lw=1.6, label=f"%ext{int(q*100)}, 5 s")
        a1.plot(ts, [o["truth"] for o in sc], color="#1D9E75", lw=1.6, ls="--", label="truth = (observed − twin) ÷ observed, 5 s")
        g = np.array([o["guard"] for o in sc]); a1.fill_between(ts, 0, 1, where=g, color="#DDD", step="mid", label="guard: abstained")
        a1.axhline(0.5, color="grey", ls=":"); a1.set_ylim(0, 1); a1.set_xlabel("time (s)"); a1.set_ylabel("share E"); a1.legend(fontsize=8, loc="upper left")
    fig.suptitle(f"Fig. G-D90 — {lab}: the p50 dictionary (left) and the p90 dictionary (right) on the same run; server-side Lat_e (gate wait in), "
                 f"pooled per second; dictionary from {dct['label']}" + chr(10) + "signal: %ext_q = (Lat_q − D_q) ÷ Lat_q against the twin-run truth at the same quantile; grey = guard range exceeded", x=0.01, ha="left", fontsize=10)
    fig.tight_layout(rect=(0, 0, 1, 0.93)); fn = os.path.join(out, f"figG-D90-{lab.replace(' ', '_').replace('+', 'plus')}.png"); fig.savefig(fn, dpi=130); plt.close(fig); print("wrote", fn)


if __name__ == "__main__":
    main()
