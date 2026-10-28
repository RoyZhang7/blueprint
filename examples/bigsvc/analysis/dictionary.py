#!/usr/bin/env python3
"""Rule-A per-endpoint dictionary (shared by every analysis script).

    D_e[rho, avgS] = FL_e + W(rho, avgS)                       (rule A, FIFO additive)

Notation (Phase B handoff, section 1):
    Lat_e   per-endpoint latency, including the admission wait (caller send stamp
            -> handler return; the recorder's f stream, or the caller's RTT)
    FL_e    floor latency of a single e request on a quiet system (y-axis floor)
    c_e     CPU time per request of e (feeds rho); = FL_e for pure-CPU handlers
    %_e     share of e in the window's arrivals
    rho     = lambda * sum_e %_e c_e   offered utilisation (busy-seconds/second); /k on k cores
    avgS    = sum_e %_e c_e            mean service time of the window's mix (second W input)
    W       shared wait, one curve per service: median_e (Lat_e - FL_e) per rho bin
    sigma_e std over windows of the per-endpoint residual Lat_e - FL_e - W (additive view)

Rule B (Lat_e = FL_e * g(rho)) is kept only as a diagnostic (dispersion of Lat_e/FL_e).
"""
import json
import os
from collections import defaultdict

import numpy as np

# per-endpoint stats vector written by the recorder: [a, n, p50, p90, p99, mean, max,
# pp50, pp90, q50, q90, x50, x90, ns, f50, f90]; older runs stop after x90, the
# oldest after p99/mean/max (then x = 0).
(EP_A, EP_N, EP_P50, EP_P90, EP_P99, EP_MEAN, EP_MAX, EP_PP50, EP_PP90, EP_Q50, EP_Q90,
 EP_X50, EP_X90, EP_NS, EP_F50, EP_F90) = range(16)

NOTATION = ("Lat_e = per-endpoint latency incl. admission wait;  FL_e = floor;  c_e = CPU/req;  "
            "rho = lambda*sum %_e*c_e (/k on k cores);  avgS = sum %_e*c_e;  W = median_e(Lat_e - FL_e);  "
            "D_e[rho] = FL_e + W")


# ----------------------------------------------------------------------------- inputs
def costs_of_run(R, costs_file=None):
    """c_e per endpoint (ms) for rho (F3): a costs file from the idle runs (B1) wins,
    then the generator's configured cpu_ms, then the configured floors."""
    names = R["run"]["endpoints"]
    if costs_file:
        c = json.load(open(costs_file))
        if isinstance(c, dict) and "costs_ms" in c:
            c = c["costs_ms"]
        missing = [e for e in names if e not in c]
        if missing:
            raise SystemExit(f"costs file {costs_file} lacks {len(missing)} endpoints, e.g. {missing[:3]}")
        return {e: float(c[e]) for e in names}, f"costs file {os.path.basename(costs_file)}"
    if "cpu_ms" in R["run"]:
        return dict(zip(names, R["run"]["cpu_ms"])), "configured cpu_ms"
    return dict(zip(names, R["run"]["floors_ms"])), "configured floors (no cpu_ms in run)"


def costs_from_idle_run(R, settle=2.0):
    """c_e from a single-endpoint idle run (B1): process CPU over the run / completions,
    minus nothing (the fixed transport cost per request is part of c_e). Returns
    {endpoint: ms} for the endpoints that had completions."""
    tot_cpu, n = 0.0, defaultdict(int)
    t0 = min(w["t"] for w in R["windows"])
    for w in R["windows"]:
        if w["t"] < t0 + settle * 1000 or "cpu" not in w:
            continue
        tot_cpu += w["cpu"]
        for e, v in w["ep"].items():
            n[e] += v[EP_N]
    tot = sum(n.values())
    return {e: tot_cpu / tot / 1e6 for e in n if n[e] > 0}  # all endpoints that ran share the CPU pro rata


def window_len_s(w):
    return w.get("w", 100) / 1000.0


def rho_of_window(w, c_ms):
    """busy-seconds per second offered in this window: sum_e a_e * c_e / W."""
    s = 0.0
    for e, v in w["ep"].items():
        s += v[EP_A] * c_ms.get(e, 0.0) / 1000.0
    return s / window_len_s(w)


def rho_series(windows, c_ms, smooth_s=1.0):
    """rho per window, smoothed with a centred rolling mean over +-smooth_s/2 of
    neighbouring windows (by time). The wait a request sees depends on the load of
    the last few hundred ms, not on the 100 ms window it completes in; a single
    100 ms window at 400 req/s has ~10% Poisson noise in rho, which near the knee
    maps to a large W error and smears overloaded windows into lower bins."""
    t = np.array([w["t"] for w in windows], float)
    r = np.array([rho_of_window(w, c_ms) for w in windows])
    if smooth_s <= 0 or len(r) < 3:
        return r
    half = smooth_s * 1000 / 2
    out = np.empty_like(r)
    lo = hi = 0
    for i in range(len(r)):
        while t[lo] < t[i] - half:
            lo += 1
        while hi < len(r) and t[hi] <= t[i] + half:
            hi += 1
        out[i] = r[lo:hi].mean()
    return out


def avgS_of_window(w, c_ms):
    """mean service time (ms) of the window's arrival mix: sum_e %_e c_e."""
    a = sum(v[EP_A] for v in w["ep"].values())
    if a == 0:
        return float("nan")
    return sum(v[EP_A] * c_ms.get(e, 0.0) for e, v in w["ep"].items()) / a


def share_of_window(w):
    tot = max(1, w["a"])
    return {e: v[EP_A] / tot for e, v in w["ep"].items()}


# ----------------------------------------------------------------------------- Lat_e (F4)
def lat_samples(w, e):
    """Per-request Lat_e samples (ns) of endpoint e in window w, including the admission
    wait where the caller stamped the request. Returns (samples, source) with source
    'full' (recorder f stream), 'approx' (total + the window's x50: pre-2026-10-28 runs
    with a stamp), or 'inproc' (no stamp: admission wait invisible)."""
    rf = w.get("rawf")
    if rf and e in rf and len(rf[e]):
        return np.asarray(rf[e], float), "full"
    raw = w.get("raw")
    v = w["ep"].get(e)
    x = v[EP_X50] if v is not None and len(v) > EP_X50 else 0
    if raw and e in raw and len(raw[e]):
        s = np.asarray(raw[e], float)
        return (s + x, "approx") if x > 0 else (s, "inproc")
    if v is None or v[EP_N] == 0:
        return np.zeros(0), "none"
    if len(v) > EP_F50 and v[EP_NS] > 0:
        return np.full(v[EP_NS], float(v[EP_F50])), "full"
    return np.full(v[EP_N], float(v[EP_P50] + x)), ("approx" if x > 0 else "inproc")


def stamp_coverage(windows):
    """Fraction of completions whose caller stamped the request (F4 flag). Older runs
    only carry x50, so a window counts as stamped when x50 > 0."""
    n = ns = 0
    for w in windows:
        for e, v in w["ep"].items():
            n += v[EP_N]
            if len(v) > EP_NS:
                ns += v[EP_NS]
            elif len(v) > EP_X50 and v[EP_X50] > 0:
                ns += v[EP_N]
    return ns / n if n else 0.0


# ----------------------------------------------------------------------------- run selection
def cpuset_size(cs):
    n = 0
    for part in str(cs).split(","):
        if "-" in part:
            a, b = part.split("-")
            n += int(b) - int(a) + 1
        elif part.strip():
            n += 1
    return n or 1


def assign(items, events, key):
    """Label each item (window/report) with the event (step/mix) active at its time."""
    ts = np.array([e["t"] for e in events])
    ids = [e[key] for e in events]
    out = []
    for w in items:
        i = np.searchsorted(ts, w["t"], side="right") - 1
        out.append(ids[i] if i >= 0 else None)
    return out


def settled_windows(R, settle_s, by="step"):
    """Windows after warm-up and after the first settle_s of each step (and mix)."""
    W = R["windows"]
    step_of = assign(W, R["steps"], "step")
    step_t0 = {s["step"]: s["t"] for s in R["steps"]}
    mix_of = assign(W, R["mixes"], "mix") if R["mixes"] else [0] * len(W)
    mix_t0 = {m["mix"]: m["t"] for m in R["mixes"]} if R["mixes"] else {0: -1e18}
    keep = [i for i, (w, s, m) in enumerate(zip(W, step_of, mix_of))
            if s is not None and s >= 0 and m is not None
            and w["t"] >= step_t0[s] + settle_s * 1000 and w["t"] >= mix_t0[m] + settle_s * 1000]
    return [W[i] for i in keep], [step_of[i] for i in keep], [mix_of[i] for i in keep]


def overloaded_steps(R, windows, step_of, tol=0.95):
    """Steps whose served arrival rate fell below tol x the target: the service was past
    its knee there, so its W is not a profiled maximum (F5)."""
    rate = {s["step"]: s["rate"] for s in R["steps"]}
    bad = set()
    for s in sorted(set(step_of)):
        ws = [w for w, st in zip(windows, step_of) if st == s]
        lam = sum(w["a"] for w in ws) / sum(window_len_s(w) for w in ws)
        if rate.get(s) and lam < tol * rate[s]:
            bad.add(s)
    return bad


# ----------------------------------------------------------------------------- rule A fit
def floors_lowest_step(windows, step_of, names, cfg):
    fs = min(step_of)
    out = {}
    for e in names:
        acc = []
        for w, s in zip(windows, step_of):
            if s == fs:
                x, _ = lat_samples(w, e)
                acc.extend(x.tolist())
        out[e] = (np.median(acc) / 1e6 if acc else cfg[e], len(acc))
    return {e: v[0] for e, v in out.items()}, {e: v[1] for e, v in out.items()}


def fit_rule_a(windows, step_of, names, c_ms, fl0, k, viol, nbins=24, min_n=20, iters=2, overloaded=(), smooth_s=1.0):
    """Fit the rule-A dictionary on a profiling sweep.

    W_b (per rho bin b): n-weighted median over normal endpoints' samples of Lat_e - FL_e.
    FL_e: Cherkasova-style, the median over ALL windows of Lat_e - W(rho_window) (iterated
    from the lowest-step floors fl0), robust to outliers at the floor step.
    Collapse (rule A): per bin, IQR and CV across normal endpoints of their median Lat_e - FL_e.
    Diagnostic (rule B): per bin CV across endpoints of median Lat_e / FL_e.
    sigma_e: std over windows of (per-window median Lat_e) - FL_e - W(rho).
    """
    edges = np.linspace(0, k * 1.05, nbins + 1)
    mids = (edges[:-1] + edges[1:]) / 2
    normal = [e for e in names if not viol[e]]
    avgS = float(np.nanmean([avgS_of_window(w, c_ms) for w in windows]))
    # samples per (endpoint, window): (lat_ms array)
    per = [{} for _ in windows]
    src = defaultdict(int)
    for i, w in enumerate(windows):
        for e in w["ep"]:
            x, s = lat_samples(w, e)
            if len(x):
                per[i][e] = x / 1e6
                src[s] += len(x)
    # overloaded steps (F5): served rate below target (passed in by the caller), or a step
    # whose median wait exceeds OVERLOAD_WAIT x avgS (an unbounded queue: M/M/k at u = 0.98
    # waits ~12 service times). They are not profiled data: their windows are dropped before
    # the fit (their rho overlaps the last in-range step's and would contaminate its bins),
    # and the guard later rejects any window whose predicted W exceeds the profiled maximum.
    overloaded = set(overloaded)
    for s in sorted(set(step_of)):
        xs = [x - fl0[e] for i, st in enumerate(step_of) if st == s for e, x in per[i].items() if not viol[e]]
        if xs and np.median(np.concatenate(xs)) > OVERLOAD_WAIT * avgS:
            overloaded.add(s)
    keep = [i for i, s in enumerate(step_of) if s not in overloaded]
    windows = [windows[i] for i in keep]; step_of = [step_of[i] for i in keep]; per = [per[i] for i in keep]
    rho = rho_series(windows, c_ms, smooth_s)
    bins = np.clip(np.digitize(rho, edges) - 1, 0, nbins - 1)
    fl = dict(fl0)
    W50 = W90 = None
    for it in range(iters + 1):
        pooled = [[] for _ in range(nbins)]
        pooled90 = [[] for _ in range(nbins)]
        for i, b in enumerate(bins):
            for e, x in per[i].items():
                if not viol[e]:
                    pooled[b].append(x - fl[e])
        W50 = np.full(nbins, np.nan); W90 = np.full(nbins, np.nan); Wn = np.zeros(nbins, int)
        for b in range(nbins):
            if pooled[b]:
                x = np.concatenate(pooled[b])
                if len(x) >= min_n:
                    W50[b] = np.median(x); W90[b] = np.percentile(x, 90); Wn[b] = len(x)
        if it == iters:
            break
        ok = ~np.isnan(W50)
        Wwin = np.interp(rho, mids[ok], W50[ok]) if ok.sum() >= 2 else np.zeros(len(rho))
        for e in names:
            acc = [x - Wwin[i] for i, d in enumerate(per) if e in d for x in (d[e],)]
            if acc:
                fl[e] = float(np.median(np.concatenate(acc)))
    inrange = np.ones(len(windows), bool)
    # collapse statistics across normal endpoints, per bin
    okb = ~np.isnan(W50)
    Wwin = np.interp(rho, mids[okb], W50[okb]) if okb.sum() >= 2 else np.zeros(len(rho))
    medA = np.full((len(names), nbins), np.nan); medB = np.full((len(names), nbins), np.nan)
    resid = {e: [] for e in names}
    for j, e in enumerate(names):
        byb = defaultdict(list)
        for i, d in enumerate(per):
            if e in d:
                byb[bins[i]].append(d[e])
                if inrange[i]:
                    resid[e].append(float(np.median(d[e])) - fl[e] - Wwin[i])
        for b, xs in byb.items():
            x = np.concatenate(xs)
            if len(x) >= min_n:
                medA[j, b] = np.median(x) - fl[e]; medB[j, b] = np.median(x) / fl[e]
    ni = [names.index(e) for e in normal]
    iqrA = np.nanpercentile(medA[ni], 75, axis=0) - np.nanpercentile(medA[ni], 25, axis=0)
    # CV is meaningful only in the wait regime (W50 above ~0.5 ms); nan elsewhere
    with np.errstate(invalid="ignore", divide="ignore"):
        cvA = np.where(W50 > 0.5, np.nanstd(medA[ni], axis=0) / np.abs(np.nanmean(medA[ni], axis=0)), np.nan)
        cvB = np.where(W50 > 0.5, np.nanstd(medB[ni], axis=0) / np.nanmean(medB[ni], axis=0), np.nan)
    sigma = {e: (float(np.std(resid[e])) if len(resid[e]) >= 5 else np.nan) for e in names}
    # per-endpoint deviation from the shared W (additive view), median over in-range bins
    good_b = np.array([b in set(bins[inrange]) for b in range(nbins)])
    devA = np.nanmedian((medA - W50[None, :])[:, good_b], axis=1) if good_b.any() else np.nanmedian(medA - W50[None, :], axis=1)
    # profiled maximum W for the guard (F5): the dictionary holds in-range steps only
    wmax = float(np.nanmax(W50))
    rho_max = float(mids[okb].max())
    return dict(mids=mids, edges=edges, W50=W50, W90=W90, Wn=Wn, fl=fl, fl0=fl0, rho=rho, bins=bins,
                medA=medA, medB=medB, iqrA=iqrA, cvA=cvA, cvB=cvB, sigma=sigma, devA=devA, sources=dict(src),
                wmax=wmax, rho_max=rho_max, avgS=avgS, k=k, overloaded=sorted(overloaded))


OVERLOAD_WAIT = 20.0  # a step whose median wait exceeds this many mean service times is past the knee


# ----------------------------------------------------------------------------- W(rho, avgS)
class WaitModel:
    """W(rho, avgS) from one or more profiled sweeps (F2). Each sweep contributes one
    curve W_r(rho) at its mean service time avgS_r. A query interpolates log W linearly
    in log avgS between the two bracketing curves (clamped at the ends); with one
    sweep this is plain W(rho). Carries the profiled maximum W for the guard (F5)."""

    def __init__(self, k):
        self.k = k
        self.curves = []  # (avgS, mids, W50, W90, wmax, rho_max, label)

    def add(self, avgS, mids, W50, W90, wmax, rho_max, label=""):
        ok = ~np.isnan(W50)
        self.curves.append((float(avgS), np.asarray(mids)[ok], np.asarray(W50)[ok], np.asarray(W90)[ok],
                            float(wmax), float(rho_max), label))
        self.curves.sort(key=lambda c: c[0])
        return self

    @property
    def wmax(self):
        return max(c[4] for c in self.curves)

    @property
    def rho_max(self):
        return max(c[5] for c in self.curves)

    @property
    def avgS_levels(self):
        return [c[0] for c in self.curves]

    def _one(self, c, rho, q):
        ys = c[2] if q == 50 else c[3]
        return float(np.interp(rho, c[1], ys))

    def predict(self, rho, avgS=None, q=50, use_avgS=True):
        cs = self.curves
        if len(cs) == 1 or avgS is None or not use_avgS or np.isnan(avgS):
            if len(cs) == 1 or avgS is None or np.isnan(avgS):
                # single curve, or no mix information: the curve closest to the median level
                c = cs[len(cs) // 2]
            else:
                c = min(cs, key=lambda c: abs(np.log(c[0]) - np.log(avgS)))
            return self._one(c, rho, q)
        if avgS <= cs[0][0]:
            return self._one(cs[0], rho, q)
        if avgS >= cs[-1][0]:
            return self._one(cs[-1], rho, q)
        for lo, hi in zip(cs[:-1], cs[1:]):
            if lo[0] <= avgS <= hi[0]:
                t = (np.log(avgS) - np.log(lo[0])) / (np.log(hi[0]) - np.log(lo[0]))
                a, b = max(self._one(lo, rho, q), 1e-6), max(self._one(hi, rho, q), 1e-6)
                return float(np.exp((1 - t) * np.log(a) + t * np.log(b)))
        return self._one(cs[-1], rho, q)

    def usable(self, rho, avgS=None, stressed_knee=None):
        """F5 guard: the predicted W must lie within the profiled range (below the
        profiled maximum) and, if the stressed knee is known, rho below it."""
        if self.predict(rho, avgS) > self.wmax or rho > self.rho_max:
            return False
        if stressed_knee is not None and rho > stressed_knee:
            return False
        return True

    def describe(self):
        return "; ".join(f"{c[6] or 'curve'}: avgS={c[0]:.2f} ms, {len(c[1])} bins, Wmax={c[4]:.2f} ms at rho<={c[5]:.2f}" for c in self.curves)


# ----------------------------------------------------------------------------- client view
def client_floors(R, settle):
    """FL_e from the caller's view: n-weighted median of the client p50 at the lowest step
    (same stream as the client Lat_e, so W = Lat_e - FL_e has no fixed transport offset)."""
    names = R["run"]["endpoints"]; cfg = dict(zip(names, R["run"]["floors_ms"]))
    rep_step = assign(R["reports"], R["steps"], "step")
    t0 = {s["step"]: s["t"] for s in R["steps"]}
    fs = min(s for s in rep_step if s is not None and s >= 0)
    acc = defaultdict(list)
    for r, st in zip(R["reports"], rep_step):
        if st == fs and r["t"] - 1000 >= t0[fs] + settle * 1000:
            for e, v in r["ep"].items():
                if v[0] >= 1:
                    acc[e].extend([v[1] / 1e6] * v[0])
    return {e: (float(np.median(acc[e])) if acc.get(e) else cfg[e]) for e in names}


def client_curve(R, settle, c_ms, fl, nb=16, viol=None):
    """From the load generator's 1 s per-endpoint reports: W_b = n-weighted median over
    normal endpoints of client p50 - FL_e, per rho bin (rho from the recorder's arrivals
    and c_e, smoothed over 1 s). Also per-endpoint medians L[e, b] (for the collapse test
    and the shape-test ground truth), the rule-B diagnostic g_b, the run's avgS, the
    profiled maximum W and the overloaded steps."""
    k = cpuset_size(R["meta"].get("cpuset", "0-7"))
    names = R["run"]["endpoints"]
    W, step_of, _ = settled_windows(R, settle)
    viol = viol or {e: False for e in names}
    rho_sec = defaultdict(list); S_sec = defaultdict(list)
    for w, r in zip(W, rho_series(W, c_ms, 1.0)):
        rho_sec[w["t"] // 1000].append(r); S_sec[w["t"] // 1000].append(avgS_of_window(w, c_ms))
    rho_sec = {s: float(np.mean(v)) for s, v in rho_sec.items()}
    S_sec = {s: float(np.nanmean(v)) for s, v in S_sec.items()}
    edges = np.linspace(0, k * 1.05, nb + 1); mids = (edges[:-1] + edges[1:]) / 2
    avgS = float(np.nanmean(list(S_sec.values())))
    rep_step = assign(R["reports"], R["steps"], "step")
    # overloaded steps (F5) are not profiled data: dropped before binning (see fit_rule_a)
    step_wait = defaultdict(list)
    for r, st in zip(R["reports"], rep_step):
        if st is not None and st >= 0:
            for e, v in r["ep"].items():
                if v[0] >= 1 and e in fl and not viol.get(e, False):
                    step_wait[st].extend([v[1] / 1e6 - fl[e]] * v[0])
    over = overloaded_steps(R, W, step_of)
    for s, xs in step_wait.items():
        if np.median(xs) > OVERLOAD_WAIT * avgS:
            over.add(s)
    addv = [[] for _ in range(nb)]; mulv = [[] for _ in range(nb)]
    per = defaultdict(lambda: [[] for _ in range(nb)])
    for r, st in zip(R["reports"], rep_step):
        if st is None or st < 0 or st in over:
            continue
        rho = rho_sec.get(r["t"] // 1000 - 1)
        if rho is None:
            continue
        b = int(np.clip(np.digitize(rho, edges) - 1, 0, nb - 1))
        for e, v in r["ep"].items():
            if v[0] >= 1 and e in fl:
                p = v[1] / 1e6
                per[e][b].extend([p] * v[0])
                if not viol.get(e, False):
                    addv[b].extend([p - fl[e]] * v[0]); mulv[b].extend([p / fl[e]] * v[0])
    W50 = np.array([np.median(x) if len(x) >= 50 else np.nan for x in addv])
    W90 = np.array([np.percentile(x, 90) if len(x) >= 50 else np.nan for x in addv])
    G = np.array([np.median(x) if len(x) >= 50 else np.nan for x in mulv])
    L = np.full((len(names), nb), np.nan)
    for i, e in enumerate(names):
        for b in range(nb):
            if len(per[e][b]) >= 15:
                L[i, b] = np.median(per[e][b])
    wmax = float(np.nanmax(W50))
    rho_max = float(mids[~np.isnan(W50)].max())
    return dict(k=k, names=names, mids=mids, W50=W50, W90=W90, G=G, L=L, wmax=wmax, rho_max=rho_max, overloaded=sorted(over),
                avgS=avgS, rho_sec=rho_sec, S_sec=S_sec)


def client_model(dict_runs, settle, costs_file=None, load_run=None):
    """WaitModel (caller view) from one or more profiling runs; FL_e, c_e, names, viol from
    the first. Several runs at different mixes give the avgS input (F2)."""
    model, first = None, None
    for d in dict_runs:
        R = load_run(d)
        c_ms, _ = costs_of_run(R, costs_file)
        fl = client_floors(R, settle)
        viol = dict(zip(R["run"]["endpoints"], R["run"]["violators"]))
        cc = client_curve(R, settle, c_ms, fl, viol=viol)
        if model is None:
            model = WaitModel(cc["k"])
            first = dict(fl=fl, c_ms=c_ms, names=R["run"]["endpoints"], viol=viol, k=cc["k"], curve=cc, run=d)
        model.add(cc["avgS"], cc["mids"], cc["W50"], cc["W90"], cc["wmax"], cc["rho_max"], os.path.basename(d))
    first["model"] = model
    return first


def client_segments(R, c_ms, settle):
    """(step, mix) segments of a random-composition run from the caller's view: rho (1 s
    smoothed, mean over the segment), avgS, and per endpoint (median client p50 ms, n, share)."""
    W, step_of, mix_of = settled_windows(R, settle)
    rs = rho_series(W, c_ms, 1.0)
    seg_rho = defaultdict(list); seg_S = defaultdict(list); seg_arr = defaultdict(lambda: defaultdict(int))
    for w, s, m, r in zip(W, step_of, mix_of, rs):
        seg_rho[(s, m)].append(r); seg_S[(s, m)].append(avgS_of_window(w, c_ms))
        for e, v in w["ep"].items():
            seg_arr[(s, m)][e] += v[EP_A]
    step_t0 = {s["step"]: s["t"] for s in R["steps"]}; mix_t0 = {m["mix"]: m["t"] for m in R["mixes"]} if R["mixes"] else {0: -1e18}
    rep_step = assign(R["reports"], R["steps"], "step"); rep_mix = assign(R["reports"], R["mixes"], "mix") if R["mixes"] else [0] * len(R["reports"])
    seg_rep = defaultdict(lambda: defaultdict(list))
    for r, s, m in zip(R["reports"], rep_step, rep_mix):
        if s is None or s < 0 or m is None or r["t"] - 1000 < step_t0[s] + settle * 1000 or r["t"] - 1000 < mix_t0[m] + settle * 1000:
            continue
        for e, v in r["ep"].items():
            if v[0] >= 1:
                seg_rep[(s, m)][e].extend([v[1] / 1e6] * v[0])
    out = []
    for key in sorted(seg_rho):
        if len(seg_rho[key]) < 10 or key not in seg_rep:
            continue
        tot = max(1, sum(seg_arr[key].values()))
        obs = {e: (float(np.median(v)), len(v), seg_arr[key][e] / tot) for e, v in seg_rep[key].items() if len(v) >= 10}
        out.append(dict(key=key, rho=float(np.mean(seg_rho[key])), avgS=float(np.nanmean(seg_S[key])), obs=obs))
    return out


# ----------------------------------------------------------------------------- F6 shape test
def shape_test(resid_ms, fl_ms, min_n=8):
    """Regress the per-endpoint residual r_e = Lat_e - FL_e - W(rho, avgS) on FL_e across
    endpoints. slope = multiplicative in-handler contention (unambiguously extrinsic);
    intercept (ms) = additive excess (capacity loss, or residual mix dependence of W).
    Returns (slope, intercept_ms, r2, n) or None when fewer than min_n endpoints."""
    es = [e for e in resid_ms if e in fl_ms and np.isfinite(resid_ms[e])]
    if len(es) < min_n:
        return None
    x = np.array([fl_ms[e] for e in es]); y = np.array([resid_ms[e] for e in es])
    X = np.c_[np.ones(len(x)), x]
    beta, *_ = np.linalg.lstsq(X, y, rcond=None)
    pred = X @ beta
    ss = np.sum((y - y.mean()) ** 2)
    r2 = 1 - np.sum((y - pred) ** 2) / ss if ss > 0 else 0.0
    return float(beta[1]), float(beta[0]), float(r2), len(es)


def ratio_stats(est, truth, floor_ms=0.5):
    """F7 wording: the ratio distribution where truth > floor, and |err| p90 everywhere."""
    est = np.asarray(est, float); truth = np.asarray(truth, float)
    err = est - truth
    out = dict(n=len(err), err_med=float(np.median(err)), err_iqr=(float(np.percentile(err, 25)), float(np.percentile(err, 75))),
               abs_p90=float(np.percentile(np.abs(err), 90)))
    m = truth > floor_ms
    if m.any():
        rel = est[m] / truth[m]
        out.update(ratio_n=int(m.sum()), ratio_med=float(np.median(rel)), ratio_iqr=(float(np.percentile(rel, 25)), float(np.percentile(rel, 75))),
                   within30=float(np.mean(np.abs(rel - 1) < 0.3)))
    return out


def fmt_ratio(s):
    t = f"n={s['n']}: err median={s['err_med']:+.2f} ms, IQR=[{s['err_iqr'][0]:+.2f},{s['err_iqr'][1]:+.2f}], |err| p90={s['abs_p90']:.2f} ms"
    if "ratio_med" in s:
        t += f"; ratio est/truth (truth>0.5 ms, n={s['ratio_n']}): median={s['ratio_med']:.2f}, IQR=[{s['ratio_iqr'][0]:.2f},{s['ratio_iqr'][1]:.2f}], within +-30%: {s['within30'] * 100:.0f}%"
    return t


# ----------------------------------------------------------------------------- F10 figures
def notation(fig, extra=""):
    """Stamp the notation block on a figure (every figure carries its notation)."""
    txt = NOTATION + (("\n" + extra) if extra else "")
    fig.subplots_adjust(bottom=0.2 if extra else 0.16)
    fig.text(0.01, 0.01, txt, fontsize=6.5, family="monospace", va="bottom", ha="left", wrap=True)
