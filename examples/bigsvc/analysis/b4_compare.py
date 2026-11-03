#!/usr/bin/env python3
"""B4/B5: the old service-level scorer (D indexed by lambda) against the service dictionary, mix-corrected
(Phase E: D[rho; mix] = Fbar + h(rho) K, columns *_mix) on a mix-ramp run, window by window; the Phase B-D
per-endpoint form (D_e = FL_e + W(rho, avgS), columns *_new) is kept for the comparison.

Phase E (section 1): per one-second window Fbar, K and rho (with /k) come from the window's own arrival shares,
D_mix = Fbar + h(rho) K, %ext_mix = (Lat - D_mix) / Lat with Lat = the pooled p50 (same statistic as the sweep's
W_sweep, D7). Guards (1.3): rho outside the profiled range -> n/a; Lat - Fbar above the profiled W_max -> n/a only
when W_max exceeds 3x the floor's own noise (else the wait guard is off); guard_mix = ''/'rho'/'wait', counts printed.
Truth for a stressed sweep (1.5, --truth): the stressed sweep's own pooled p50 per rho bin against the dictionary's
pooled p50 (both above the unstressed floor; nothing is refit to the stressed run), truth %ext = (p50_s - D) / p50_s;
the Phase D definition (W_s above the stressed run's own refit floor) is kept as truth_ext_phaseD for the record.
--fbar mean|median: Fbar = sum %_e FL_e (section 6) or the share-weighted median of FL_e. --smooth-s W (= --smooth).

Old scorer (Gordion Algorithm 1, stage-3 plan): with the service-level latency Lat (p50 of
all requests in the window, smoothed) and the profiled curve D(lambda) from the default-mix
sweep,  %ext_old = clamp01((Lat - D(lambda)) / Lat),  y = clamp01(tanh(k (Lat - p) / sigma))
with p, sigma the baseline (floor-step) latency statistics and k = 0.07.
New scorer: per endpoint,  %ext_e = clamp01((Lat_e - FL_e - W(rho, avgS)) / Lat_e),
aggregated over the window's endpoints weighted by their completions; same y.
2x2 cell: slow (y >= 0.5) x extrinsic (%ext >= 0.5); a window is a false positive when it
is called extrinsic with no aggressor present (B4), or when %ext misses the ground truth
band (B5, --truth from a5_compare's stressed sweep).

Caller view (load-generator reports) by default; --server uses the SUT's recorder stream
(Lat_e per GORDION_LAT: server-side tap -> return minus blocked by default; with the Phase D admission
gate the gate wait is part of it).

Phase D (section 2): the service-level latency and both %ext series are smoothed with Algorithm 1's causal
Gaussian (sigma = W/3 over the past W seconds, --smooth W, default 30; partial-kernel renormalisation while
the history is shorter than W) before the 0.5 threshold; the raw (unsmoothed) labels are kept as well
(columns *_raw). G2's shape test is also wired into a cell of its own (cell_shape: origin = ext when the
slope of Lat_e - D_e[rho] on FL_e is > 0 with t > 2, int when |t| < 1, else n/a), and the Q3 table reports
old cell accuracy / new strict (n/a = wrong) / new when it speaks / new abstained, raw and smoothed.

Usage: b4_compare.py RAMP_RUN --dict A2_RUN [--dict A2_RUN2] --costs costs.json [--k 0.07]
                     [--truth STRESS_SWEEP] [--settle 5] [--server] [--out DIR]
"""
import argparse, json, os, sys
from collections import defaultdict
import numpy as np
sys.path.insert(0, os.path.dirname(__file__))
from analyze import load_run
from dictionary import (EP_A, EP_N, EP_F50, EP_NS, EP_P50, EP_X50, assign, avgS_of_window, client_curve, client_floors,
                        client_model, costs_of_run, fit_service_dict, lat_samples, notation, rho_series, settled_windows)

try:
    import matplotlib; matplotlib.use("Agg"); import matplotlib.pyplot as plt
except Exception:
    plt = None


def causal_gauss(xs, ts, W):
    """Algorithm 1's smoothing: causal Gaussian over the past W seconds (sigma = W/3), renormalised over the
    samples present (partial kernel during warm-up); NaN samples are skipped (they carry no weight)."""
    xs = np.asarray(xs, float); ts = np.asarray(ts, float); out = np.full(len(xs), np.nan)
    if W <= 0:
        return xs.copy()
    sig = W / 3.0
    for i in range(len(xs)):
        m = (ts <= ts[i]) & (ts > ts[i] - W) & np.isfinite(xs)
        if m.any():
            w = np.exp(-0.5 * ((ts[i] - ts[m]) / sig) ** 2)
            out[i] = float(np.sum(w * xs[m]) / np.sum(w))
    return out


def q3(rows, tc, key, label):
    """Q3 table row: cell accuracy (strict: n/a counts as wrong), accuracy on the windows where the scorer speaks,
    and the abstained fraction."""
    strict = np.mean([r[key] == c for r, c in zip(rows, tc)])
    spoke = [(r, c) for r, c in zip(rows, tc) if not r[key].endswith("/n-a")]
    when = np.mean([r[key] == c for r, c in spoke]) if spoke else float("nan")
    abst = 1 - len(spoke) / len(rows)
    print(f"   Q3 {label:34s} strict {strict * 100:5.1f} %   when it speaks {when * 100:5.1f} %   abstained {abst * 100:5.1f} %   (n={len(rows)})")
    return strict, when, abst


def clamp01(x):
    return float(min(1.0, max(0.0, x)))


def shape_t(resid_ms, fl_ms, min_n=8):
    """G2 shape test on one window: OLS of r_e = Lat_e - D_e[rho] on FL_e across the endpoints seen.
    Returns (slope, t, n, origin) with origin 'ext' when slope > 0 and t > 2 (multiplicative in-handler
    contention), 'int' otherwise; None with fewer than min_n endpoints (HotelReservation: n/a)."""
    es = [e for e in resid_ms if e in fl_ms and np.isfinite(resid_ms[e])]
    if len(es) < min_n:
        return None
    x = np.array([fl_ms[e] for e in es]); y = np.array([resid_ms[e] for e in es])
    X = np.c_[np.ones(len(x)), x]
    beta, *_ = np.linalg.lstsq(X, y, rcond=None)
    res = y - X @ beta
    dof = max(1, len(x) - 2)
    s2 = float(res @ res) / dof
    sxx = float(np.sum((x - x.mean()) ** 2))
    se = np.sqrt(s2 / sxx) if sxx > 0 else np.inf
    t = float(beta[1] / se) if se > 0 and np.isfinite(se) else 0.0
    return float(beta[1]), t, len(es), ("ext" if beta[1] > 0 and t > 2 else "int")


def service_curve(R, settle, c_ms, fl, nb=24):
    """Old dictionary: service-level client p50 per lambda bin (req/s) from the profiling sweep,
    plus the baseline p/sigma at the floor step."""
    # reports are 1 s (load generator) or 100 ms (caller-side recorder): aggregate per second
    sec = defaultdict(list)
    for r in R["reports"]:
        if r.get("step", -9) >= 0 and r["all"][0] > 0:
            sec[(r["t"] - 1) // 1000].append(r)
    reps = [dict(step=rs[0]["step"], n=sum(r["all"][0] for r in rs), p50=sum(r["all"][1] * r["all"][0] for r in rs) / sum(r["all"][0] for r in rs)) for rs in sec.values()]
    lam = np.array([r["n"] for r in reps], float)  # completions per second ~ lambda
    lat = np.array([r["p50"] / 1e6 for r in reps])
    edges = np.linspace(0, lam.max() * 1.02, nb + 1); mids = (edges[:-1] + edges[1:]) / 2
    b = np.clip(np.digitize(lam, edges) - 1, 0, nb - 1)
    D = np.array([np.median(lat[b == i]) if np.sum(b == i) >= 5 else np.nan for i in range(nb)])
    ok = ~np.isnan(D)
    fs = min(r["step"] for r in reps)
    base = np.array([r["p50"] / 1e6 for r in reps if r["step"] == fs])
    return dict(lam=mids[ok], D=D[ok], p=float(np.median(base)), sigma=float(np.std(base)) or 1e-3)


def server_service_curve(R, settle, nb=24):
    """Old dictionary on the SUT's own stream (Phase C, --server): service-level Lat (n-weighted median of
    all endpoints' Lat_e samples per second) per lambda bin, lambda = arrivals per second at the tap."""
    W, step_of, _ = settled_windows(R, settle)
    sec = defaultdict(lambda: [[], 0, None])
    for w, s in zip(W, step_of):
        k = w["t"] // 1000
        for e in w["ep"]:
            x, _ = lat_samples(w, e)
            sec[k][0].extend((x / 1e6).tolist())
        sec[k][1] += w["a"]; sec[k][2] = s
    reps = [dict(step=v[2], n=v[1], p50=float(np.median(v[0]))) for v in sec.values() if v[0]]
    lam = np.array([r["n"] for r in reps], float); lat = np.array([r["p50"] for r in reps])
    edges = np.linspace(0, lam.max() * 1.02, nb + 1); mids = (edges[:-1] + edges[1:]) / 2
    b = np.clip(np.digitize(lam, edges) - 1, 0, nb - 1)
    D = np.array([np.median(lat[b == i]) if np.sum(b == i) >= 5 else np.nan for i in range(nb)])
    ok = ~np.isnan(D)
    fs = min(r["step"] for r in reps)
    base = np.array([r["p50"] for r in reps if r["step"] == fs])
    return dict(lam=mids[ok], D=D[ok], p=float(np.median(base)), sigma=float(np.std(base)) or 1e-3)


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("run"); ap.add_argument("--dict", action="append", required=True)
    ap.add_argument("--costs", default=None); ap.add_argument("--k", type=float, default=0.07); ap.add_argument("--truth", default=None)
    ap.add_argument("--settle", type=float, default=5.0); ap.add_argument("--server", action="store_true"); ap.add_argument("--out", default=None)
    ap.add_argument("--ext-thresh", type=float, default=0.5); ap.add_argument("--y-thresh", type=float, default=0.5)
    ap.add_argument("--truth-ramp", default=None, help="B-bigsvc-stress: the no-hog twin of this ramp; truth per second = (Lat - Lat_nohog at the same second since step 0) / Lat")
    ap.add_argument("--smooth", "--smooth-s", dest="smooth", type=float, default=30.0, help="Algorithm 1 causal Gaussian window W in seconds (sigma = W/3); 0 = off")
    ap.add_argument("--fbar", choices=("mean", "median", "mixture"), default="mixture", help="Fbar of a mix: the median of the mixture of the endpoints' floor distributions (Phase F default), sum %%_e FL_e (Phase E), or the share-weighted median of FL_e")
    ap.add_argument("--dict-json", default=None, help="where to store the service dictionary (h, FL_e, c_e); default OUT/service_dict.json")
    a = ap.parse_args()
    out = a.out or a.run; os.makedirs(out, exist_ok=True)
    if a.server:
        # Phase C: the dictionary from the SUT's own recorder stream (Lat_e per GORDION_LAT, unstamped
        # by default), not from the caller's RTTs
        from analyze import analyze_profile, combine_models
        profs = [analyze_profile(load_run(d), d, a.settle, a.costs, quiet=True) for d in a.dict]
        P = dict(model=combine_models(profs), fl=profs[0]["fl"], c_ms=profs[0]["c_ms"], names=profs[0]["names"], k=profs[0]["k"], viol=profs[0]["viol"])
    else:
        P = client_model(a.dict, a.settle, a.costs, load_run)
    model, fl, c_ms, names, k = P["model"], P["fl"], P["c_ms"], P["names"], P["k"]
    R0 = load_run(a.dict[0])
    old = server_service_curve(R0, a.settle) if a.server else service_curve(R0, a.settle, c_ms, fl)
    # Phase E: the service dictionary from the first profiling run (the SUT's own stream)
    SD = fit_service_dict(R0, a.settle, a.costs, fbar=a.fbar)
    SD.save(a.dict_json or os.path.join(out, "service_dict.json"))
    R = load_run(a.run)
    # rho' = utilisation of the executing slots (k - 1 under the admission gate, GORDION_GATE_CAP overrides): the knee of a
    # gated service sits near rho' = 1, i.e. rho = (k - 1) / k
    gated = str(R["meta"].get("gate", "0")) == "1" and R.get("start") is not None and R["start"].get("lang") != "cpp"
    slots = int(R["meta"].get("gate_cap") or 0) or (k - 1 if gated else k)
    W, step_of, _ = settled_windows(R, a.settle, settle_mix=0)  # the mix moves every second during the ramp
    rs = rho_series(W, c_ms, 1.0)
    rho_sec = defaultdict(list); S_sec = defaultdict(list); a_sec = defaultdict(int); sh_sec = defaultdict(lambda: defaultdict(float))
    for w, r in zip(W, rs):
        s = w["t"] // 1000
        rho_sec[s].append(r); S_sec[s].append(avgS_of_window(w, c_ms)); a_sec[s] += w["a"]
        for e, v in w["ep"].items():
            sh_sec[s][e] += v[EP_A]
    mix_of_sec = {}
    for m in R["mixes"]:
        mix_of_sec[m["t"] // 1000] = m
    truth = None
    if a.truth:
        Rs = load_run(a.truth)
        if a.server:
            cc = analyze_profile(Rs, a.truth, a.settle, a.costs, quiet=True)["fit"]
        else:
            cc = client_curve(Rs, a.settle, c_ms, fl, viol=P["viol"])
        ok = ~np.isnan(cc["W50"])
        truth = dict(rho=cc["mids"][ok], Ws=cc["W50"][ok], avgS=cc["avgS"])
        # section 1.5: the stressed sweep's own pooled p50 per rho bin (its overloaded steps dropped, floors NOT refit)
        # against the dictionary's pooled p50 at the same rho: everything above the unstressed floor is in the truth
        SDs = fit_service_dict(Rs, a.settle, a.costs, fbar=a.fbar)
        truth["p50_s"] = (SDs.mids, SDs.p50); truth["sd_s"] = SDs
    rows = []
    control = None
    if a.truth_ramp:
        # the no-hog twin ramp, per second since its step 0: service-level n-weighted median of Lat_e
        Rc = load_run(a.truth_ramp)
        Wc, _, _ = settled_windows(Rc, a.settle, settle_mix=0)
        t0c = min(s["t"] for s in Rc["steps"] if s["step"] == 0) // 1000
        cs = defaultdict(list)
        if a.server:
            for w in Wc:
                for e in w["ep"]:
                    x, _ = lat_samples(w, e)
                    if len(x):
                        cs[w["t"] // 1000 - t0c].extend((x / 1e6).tolist())
        else:
            for r in Rc["reports"]:
                if r.get("step", -9) >= 0:
                    for e, v in r["ep"].items():
                        if v[0] >= 1:
                            cs[r["t"] // 1000 - 1 - t0c].extend([v[1] / 1e6] * v[0])
        control = {s: float(np.median(v)) for s, v in cs.items() if v}
        t0 = min(s["t"] for s in R["steps"] if s["step"] == 0) // 1000
    # per-second windows from the caller's reports (or the server's recorder stream)
    # service-level Lat of a second must be the SAME statistic as the old curve's D(lambda): the pooled median of
    # all requests (server mode: pooled samples; caller mode: the n-weighted median of the endpoints' p50s, the
    # closest the generator's reports allow). The n-weighted MEAN of per-endpoint medians used before sits far
    # above the pooled median on a log-spread mix and made the old scorer read 0.4-0.6 at the profiled mix itself.
    def wmedian(ep):
        ps = sorted((p, n) for p, n in ep.values()); tot = sum(n for _, n in ps); acc = 0
        for p, n in ps:
            acc += n
            if acc >= tot / 2:
                return p
        return ps[-1][0]
    if a.server:
        per_sec = defaultdict(lambda: defaultdict(list))
        for w in W:
            for e in w["ep"]:
                x, _ = lat_samples(w, e)
                if len(x):
                    per_sec[w["t"] // 1000][e].extend((x / 1e6).tolist())
        secs = sorted(per_sec)
        get = lambda s: {e: (float(np.median(v)), len(v)) for e, v in per_sec[s].items() if len(v)}
        pooled = lambda s: float(np.median(np.concatenate([np.asarray(v) for v in per_sec[s].values() if len(v)])))
    else:
        rep_sec = {r["t"] // 1000 - 1: r for r in R["reports"] if r.get("step", -9) >= 0}
        secs = sorted(rep_sec)
        get = lambda s: {e: (v[1] / 1e6, v[0]) for e, v in rep_sec[s]["ep"].items() if v[0] >= 1 and e in fl}
        pooled = lambda s: wmedian(get(s))
    lat_s = None
    cur_mix = None
    recent = []  # (second, {e: (p50, n)}) for the shape test's 3 s pool (G2)
    for s in secs:
        if s not in rho_sec:
            continue
        ep = get(s)
        if not ep:
            continue
        n_tot = sum(n for _, n in ep.values())
        lat = pooled(s)  # service-level latency this second: pooled median, the old curve's statistic
        lam = a_sec[s]; rho = float(np.mean(rho_sec[s])); S = float(np.nanmean(S_sec[s]))
        D_old = float(np.interp(lam, old["lam"], old["D"]))
        ext_old = clamp01((lat - D_old) / lat)  # raw; the smoothed version is computed on the series below
        y = clamp01(np.tanh(a.k * (lat - old["p"]) / old["sigma"]))
        Wp = model.predict(rho, S)
        # Phase E: the service dictionary at this second's mix (rho with /k)
        D_mix, mx = SD.predict(rho / k, sh_sec[s])
        fb_mean, fb_mix = SD.fbar_both(sh_sec[s])
        g_mix = SD.guard(rho / k, lat, mx["Fbar"])
        ext_mix = clamp01((lat - D_mix) / lat) if not g_mix else float("nan")
        ext_new = sum(clamp01((p - fl[e] - Wp) / p) * n for e, (p, n) in ep.items()) / n_tot
        D_new = sum((fl[e] + Wp) * n for e, (p, n) in ep.items()) / n_tot  # service-level D_e[rho] of this second's mix
        # G1 guard, in the scorer: rho outside the profiled range, or the observed wait (Lat_e - FL_e,
        # n-weighted over the window's endpoints) above the largest profiled W -> %ext is not a number,
        # only the slowness axis of the cell is reported
        w_obs = sum((p - fl[e]) * n for e, (p, n) in ep.items()) / n_tot
        usable = model.usable(rho, S) and w_obs <= model.wmax
        if not usable:
            ext_new = float("nan")
        # G2 shape test: residual r_e = Lat_e - D_e[rho] against FL_e across the endpoints seen in the
        # last 3 s (pooled, n-weighted); slope significantly > 0 (t > 2) = extrinsic on the origin axis
        recent.append((s, ep)); recent = [(s2, e2) for s2, e2 in recent if s2 > s - 3]
        pool = defaultdict(lambda: [0.0, 0])
        for _, e2 in recent:
            for e, (p, n) in e2.items():
                pool[e][0] += p * n; pool[e][1] += n
        resid = {e: v[0] / v[1] - fl[e] - Wp for e, v in pool.items() if v[1] > 0 and e in fl}
        shape = shape_t(resid, fl)
        t_ext = None; lat_twin = None; t_ext_old = None
        if truth is not None:
            Ws = float(np.interp(rho, truth["rho"], truth["Ws"])); t_ext_old = clamp01((Ws - Wp) / (Ws + np.mean([fl[e] for e in ep])))
            p50s = float(np.interp(rho / k, truth["p50_s"][0], truth["p50_s"][1]))
            t_ext = clamp01((p50s - SD.p50_at(rho / k)) / p50s)
        elif control is not None:
            off = s - t0
            near = [control[o] for o in (off, off - 1, off + 1) if o in control]
            if near:
                lat_twin = float(np.mean(near))
                t_ext = clamp01((lat - lat_twin) / lat)
        if s in mix_of_sec:
            cur_mix = mix_of_sec[s]
        rows.append(dict(t=s, lam=lam, rho=rho, u=rho / k, avgS=S, lat=lat, D_old=D_old, D_new=D_new, lat_twin=lat_twin,
                         ext_old_raw=ext_old, ext_new_raw=ext_new, y_raw=y,
                         usable=int(usable), ramp=(cur_mix or {}).get("ramp", 0.0), t_ext=t_ext, shape=shape,
                         Fbar=mx["Fbar"], K=mx["K"], D_mix=D_mix, ext_mix_raw=ext_mix, guard_mix=g_mix, t_ext_old=t_ext_old, fb_mean=fb_mean, fb_mix=fb_mix))
    # Algorithm 1's smoothing (causal Gaussian, W = --smooth s) on the service-level latency (-> y, and the old
    # %ext recomputed from the smoothed latency as Algorithm 1 does) and on the new scorer's %ext series; the
    # cells are labelled from the smoothed series, the *_raw columns keep the unsmoothed ones
    tt = np.array([r["t"] for r in rows], float)
    lat_sm = causal_gauss([r["lat"] for r in rows], tt, a.smooth)
    ext_new_sm = causal_gauss([r["ext_new_raw"] for r in rows], tt, a.smooth)
    ext_mix_sm = causal_gauss([r["ext_mix_raw"] for r in rows], tt, a.smooth)
    def cells(r, y, eo, en):
        slow = "slow" if y >= a.y_thresh else "fast"
        c_old = slow + "/" + ("ext" if eo >= a.ext_thresh else "int")
        c_new = slow + "/" + ("n-a" if not r["usable"] or not np.isfinite(en) else ("ext" if en >= a.ext_thresh else "int"))
        sh = r["shape"]
        origin = "n-a" if sh is None else ("ext" if (sh[0] > 0 and sh[1] > 2) else ("int" if abs(sh[1]) < 1 else "n-a"))
        return c_old, c_new, slow + "/" + origin
    def cell_mix(r, y, em):
        slow = "slow" if y >= a.y_thresh else "fast"
        return slow + "/" + ("n-a" if r["guard_mix"] or not np.isfinite(em) else ("ext" if em >= a.ext_thresh else "int"))
    for r, ls, es, em in zip(rows, lat_sm, ext_new_sm, ext_mix_sm):
        r["lat_s"] = ls
        r["y"] = clamp01(np.tanh(a.k * (ls - old["p"]) / old["sigma"]))
        r["ext_old"] = clamp01((ls - r["D_old"]) / ls)
        r["ext_new"] = es if r["usable"] else float("nan")
        r["ext_mix"] = em if not r["guard_mix"] else float("nan")
        r["cell_old"], r["cell_new"], r["cell_shape"] = cells(r, r["y"], r["ext_old"], r["ext_new"])
        r["cell_old_raw"], r["cell_new_raw"], _ = cells(r, r["y_raw"], r["ext_old_raw"], r["ext_new_raw"] if r["usable"] else float("nan"))
        r["cell_mix"] = cell_mix(r, r["y"], r["ext_mix"]); r["cell_mix_raw"] = cell_mix(r, r["y_raw"], r["ext_mix_raw"])
    with open(os.path.join(out, "b4_windows.csv"), "w") as f:
        f.write("t,lambda,rho,util,avgS_ms,lat_ms,D_old_ms,ext_old,ext_new,y,usable,ramp,truth_ext,cell_old,cell_new,shape_slope,shape_t,shape_n,shape_origin,"
                "D_new_ms,lat_twin_ms,lat_s_ms,ext_old_raw,ext_new_raw,y_raw,cell_old_raw,cell_new_raw,cell_shape,"
                "Fbar_ms,K_ms,D_mix_ms,ext_mix,ext_mix_raw,cell_mix,cell_mix_raw,guard_mix,truth_ext_phaseD,fbar_mean_ms,fbar_mix_ms,rho_slot\n")
        na = lambda v: "n/a" if v is None or (isinstance(v, float) and np.isnan(v)) else f"{v:.4f}"
        for r in rows:
            sh = r["shape"]
            f.write(f"{r['t']},{r['lam']},{r['rho']:.4f},{r['u']:.4f},{r['avgS']:.4f},{r['lat']:.4f},{r['D_old']:.4f},{r['ext_old']:.4f},"
                    f"{na(r['ext_new'])},{r['y']:.4f},{r['usable']},{r['ramp']:.3f},"
                    f"{'' if r['t_ext'] is None else f'{r['t_ext']:.4f}'},{r['cell_old']},{r['cell_new']},"
                    f"{'' if sh is None else f'{sh[0]:.4f}'},{'' if sh is None else f'{sh[1]:.2f}'},{'' if sh is None else sh[2]},{'' if sh is None else sh[3]},"
                    f"{r['D_new']:.4f},{'' if r['lat_twin'] is None else f'{r['lat_twin']:.4f}'},{r['lat_s']:.4f},{r['ext_old_raw']:.4f},{na(r['ext_new_raw'])},{r['y_raw']:.4f},"
                    f"{r['cell_old_raw']},{r['cell_new_raw']},{r['cell_shape']},"
                    f"{r['Fbar']:.4f},{r['K']:.4f},{r['D_mix']:.4f},{na(r['ext_mix'])},{na(r['ext_mix_raw'])},{r['cell_mix']},{r['cell_mix_raw']},{r['guard_mix']},"
                    f"{'' if r['t_ext_old'] is None else f'{r['t_ext_old']:.4f}'},{r['fb_mean']:.4f},{r['fb_mix']:.4f},{r['u'] * k / slots:.4f}\n")
    n = len(rows)
    print(f"{os.path.basename(a.run)}: {n} one-second windows; dictionary {model.describe()}; old curve D(lambda) from {os.path.basename(a.dict[0])}, baseline p={old['p']:.3f} ms sigma={old['sigma']:.3f} ms, k={a.k}; smoothing: causal Gaussian W={a.smooth:g} s")
    print(f"   service dictionary D[rho; mix] = Fbar + h(rho) K from {SD.describe()}")
    print(f"   rho' = rho x k / slots with slots = {slots} (k = {k}{', gate cap ' + str(slots) if gated else ''}): windows at rho' {min(r['u'] for r in rows) * k / slots:.2f}-{max(r['u'] for r in rows) * k / slots:.2f}")
    gr = sum(1 for r in rows if r["guard_mix"] == "rho"); gw = sum(1 for r in rows if r["guard_mix"] == "wait")
    print(f"   guards (mix): rho guard fired on {gr} of {n} windows (profiled rho [{SD.rho_min:.2f}, {SD.rho_max:.2f}]; windows at rho {min(r['u'] for r in rows):.2f}-{max(r['u'] for r in rows):.2f}); "
          f"wait guard {'on' if SD.wait_guard else 'OFF'} (W_max {SD.wmax:.3f} ms vs 3 x floor noise {3 * SD.noise:.3f} ms), fired on {gw}")
    for lbl, key in (("old D[lambda]", "ext_old"), ("new D_e[rho, avgS]", "ext_new"), ("mix D[rho; mix]", "ext_mix")):
        v = np.array([r[key] for r in rows]); v = v[np.isfinite(v)]
        if len(v):
            print(f"   {lbl:20s}: %ext median={np.median(v):.3f} IQR=[{np.percentile(v, 25):.3f},{np.percentile(v, 75):.3f}] max={v.max():.3f}; windows with %ext >= {a.ext_thresh}: {np.mean(v >= a.ext_thresh) * 100:.1f}% (of {len(v)} with a number)")
    sh = [r["shape"] for r in rows if r["shape"] is not None]
    if sh:
        print(f"   G2 shape test (3 s pool, >= 8 endpoints): {len(sh)} of {n} windows testable; origin 'ext' (slope > 0, t > 2) in {sum(1 for x in sh if x[3] == 'ext')} windows; "
              f"slope median {np.median([x[0] for x in sh]):+.3f} ms/ms, t median {np.median([x[1] for x in sh]):+.1f}")
    else:
        print(f"   G2 shape test: n/a (fewer than 8 endpoints per window)")
    for key in ("cell_old", "cell_new", "cell_shape", "cell_old_raw", "cell_new_raw", "cell_mix", "cell_mix_raw"):
        c = defaultdict(int)
        for r in rows:
            c[r[key]] += 1
        print(f"   2x2 cells {key}: " + ", ".join(f"{k2}={v}" for k2, v in sorted(c.items())))
    # by ramp phase (thirds of the ramp)
    ph = defaultdict(lambda: ([], [], []))
    for r in rows:
        q = "before" if r["ramp"] <= 0 else ("ramp 0-50%" if r["ramp"] < 0.5 else ("ramp 50-100%" if r["ramp"] < 1 else "after"))
        ph[q][0].append(r["ext_old"]); ph[q][1].append(r["ext_new"]); ph[q][2].append(r["ext_mix"])
    print("   phase            n   %ext old (median)   %ext new (median)   %ext mix (median)  windows >= thresh old / mix")
    for q in ("before", "ramp 0-50%", "ramp 50-100%", "after"):
        if ph[q][0]:
            with np.errstate(invalid="ignore"):
                eo = np.array(ph[q][0]); em = np.array(ph[q][2])
                print(f"   {q:14s} {len(ph[q][0]):4d}   {np.nanmedian(ph[q][0]):8.3f}            {np.nanmedian(ph[q][1]):8.3f}   (new: n/a in {int(np.sum(np.isnan(ph[q][1])))})"
                      f"   {np.nanmedian(em) if np.isfinite(em).any() else float('nan'):8.3f} (mix: n/a in {int(np.sum(np.isnan(em)))})"
                      f"   {np.mean(eo >= a.ext_thresh) * 100:5.1f} % / {np.mean(em[np.isfinite(em)] >= a.ext_thresh) * 100 if np.isfinite(em).any() else float('nan'):5.1f} %")
    if truth is not None or control is not None:
        rows = [r for r in rows if r["t_ext"] is not None]
        if truth is not None:
            # section 1.5: what the recomputed truth changed
            for lbl, key in (("Phase D (W_s above the stressed run's own floor)", "t_ext_old"), ("Phase E (p50_s above the unstressed floor)", "t_ext")):
                cnt = defaultdict(int)
                for r in rows:
                    cnt[("slow" if r["y"] >= a.y_thresh else "fast") + "/" + ("ext" if r[key] >= a.ext_thresh else "int")] += 1
                print(f"   truth cells, {lbl}: " + ", ".join(f"{c}={v}" for c, v in sorted(cnt.items())))
        T = np.array([(r["ext_new"], r["ext_old"], r["t_ext"]) for r in rows if r["usable"]])
        Tm = np.array([(r["ext_mix"], r["t_ext"]) for r in rows if not r["guard_mix"]])
        if len(Tm):
            err = Tm[:, 0] - Tm[:, 1]
            print(f"   vs ground truth (mix, scored windows n={len(Tm)}): err median={np.median(err):+.3f} IQR=[{np.percentile(err, 25):+.3f},{np.percentile(err, 75):+.3f}] |err| p90={np.percentile(np.abs(err), 90):.3f}; "
                  f"within +-0.15: {np.mean(np.abs(err) < 0.15) * 100:.0f}%")
        if len(T):
            for i, lbl in ((0, "new"), (1, "old")):
                err = T[:, i] - T[:, 2]
                print(f"   vs ground truth ({lbl}, usable windows n={len(T)}): err median={np.median(err):+.3f} IQR=[{np.percentile(err, 25):+.3f},{np.percentile(err, 75):+.3f}] |err| p90={np.percentile(np.abs(err), 90):.3f}; "
                      f"within +-0.15: {np.mean(np.abs(err) < 0.15) * 100:.0f}%")
        # G3: 2x2 cell accuracy, truth cell = slowness x (truth %ext >= threshold); n/a cells count as wrong
        tc = [("slow" if r["y"] >= a.y_thresh else "fast") + "/" + ("ext" if r["t_ext"] >= a.ext_thresh else "int") for r in rows]
        acc_old = np.mean([r["cell_old"] == c for r, c in zip(rows, tc)]); acc_new = np.mean([r["cell_new"] == c for r, c in zip(rows, tc)])
        acc_new_u = np.mean([r["cell_new"] == c for r, c in zip(rows, tc) if r["usable"]]) if any(r["usable"] for r in rows) else float("nan")
        cnt = defaultdict(int)
        for c in tc:
            cnt[c] += 1
        print(f"   G3 2x2 cell accuracy vs truth: old {acc_old * 100:.0f}%, new {acc_new * 100:.0f}% (new on usable windows only: {acc_new_u * 100:.0f}%); truth cells " +
              ", ".join(f"{c}={v}" for c, v in sorted(cnt.items())))
        # Q3 table (Phase D): smoothed (Algorithm 1) and raw labels; cell_shape = origin from the G2 shape test
        tc_raw = [("slow" if r["y_raw"] >= a.y_thresh else "fast") + "/" + ("ext" if r["t_ext"] >= a.ext_thresh else "int") for r in rows]
        for key, lbl, t in (("cell_old", "old D(lambda), smoothed", tc), ("cell_new", "new D_e[rho], smoothed", tc), ("cell_mix", "mix D[rho; mix], smoothed", tc),
                            ("cell_shape", "new + G2 shape rule, smoothed y", tc),
                            ("cell_old_raw", "old D(lambda), raw", tc_raw), ("cell_new_raw", "new D_e[rho], raw", tc_raw), ("cell_mix_raw", "mix D[rho; mix], raw", tc_raw)):
            q3(rows, t, key, lbl)
    print(f"   guard: {sum(1 for r in rows if not r['usable'])} of {n} windows outside the profiled W range")
    if plt and rows:
        t0 = rows[0]["t"]
        fig, ax = plt.subplots(2, 1, figsize=(12, 7), sharex=True)
        ts = [r["t"] - t0 for r in rows]
        ax[0].plot(ts, [r["ext_old"] for r in rows], "-", lw=1, label="%ext old: (Lat - D(lambda))/Lat")
        ax[0].plot(ts, [r["ext_new"] for r in rows], "-", lw=1, label="%ext new: (Lat_e - FL_e - W(rho, avgS))/Lat_e")
        ax[0].plot(ts, [r["ext_mix"] for r in rows], "-", lw=1.2, label="%ext mix: (Lat - D[rho; mix])/Lat")
        if truth is not None:
            ax[0].plot(ts, [r["t_ext"] for r in rows], "k--", lw=1, label="ground truth (stressed sweep)")
        ax[0].axhline(a.ext_thresh, color="0.5", lw=0.6, ls=":"); ax[0].set_ylabel("%ext"); ax[0].legend(fontsize=7); ax[0].set_title("mix ramp: old vs new scorer, per second")
        ax[1].plot(ts, [r["ramp"] for r in rows], "-", color="C2", label="ramp fraction (default -> contrasting mix)")
        ax[1].plot(ts, [r["u"] for r in rows], "-", color="C3", label="rho")
        ax[1].plot(ts, [r["avgS"] / max(1e-6, rows[0]["avgS"]) for r in rows], "-", color="C4", label="avgS / avgS at start")
        ax[1].set_xlabel("seconds"); ax[1].legend(fontsize=7)
        for x in ax:
            x.grid(alpha=.3)
        fig.tight_layout(); notation(fig, f"run {os.path.basename(a.run)}; old curve from {os.path.basename(a.dict[0])}; cell = (y >= {a.y_thresh}) x (%ext >= {a.ext_thresh})")
        fig.savefig(os.path.join(out, "b4.png"), dpi=130); print("wrote", os.path.join(out, "b4.png"))


if __name__ == "__main__":
    main()
