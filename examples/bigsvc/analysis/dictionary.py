#!/usr/bin/env python3
"""Dictionary code shared by every analysis script.

Service dictionary, mix-corrected (Phase E; PHASED-HANDOFF.md section 6) -- the current form:

    Fbar  = sum_e %_e FL_e              pooled floor of the window's mix   (FL_e per-endpoint constant, from the sweep)
    cbar  = sum_e %_e c_e ;  c2bar = sum_e %_e c_e^2                        (c_e per-endpoint constant)
    K     = c2bar / (2 cbar)            residual-work factor of the mix (P-K)
    rho   = lambda cbar / (f/f_nom) / k offered utilisation (k = cores of the service; rho includes the /k)
    h(rho) = W_sweep(rho) / K(sweep mix)   stored curve, mix-free; W_sweep = pooled p50 - Fbar(sweep mix) per rho bin
    Fbar (Phase F) = median of the %_e-weighted mixture of the endpoints' floor distributions FL_e(q) (41 quantiles of
                     each endpoint's lowest-step samples); FL_e = FL_e(0.5) is the scalar floor in the tables
    D[rho; mix] = Fbar + h(rho) K(window)  the dictionary
    %ext = (Lat - D) / Lat               Lat = pooled server-side p50 of the window

One FIFO line; k only makes rho a fraction of capacity. Lat online and W_sweep offline are the same statistic
(pooled p50). The per-endpoint form D_e[rho] = FL_e + W(rho) of Phases B-D (fit_rule_a, WaitModel) is kept below
for the comparison columns (D_new) and is retired as a dictionary.

Notation of the older code (Phases B-D):
    Lat_e   per-endpoint latency (GORDION_LAT: unstamped = tap -> return minus downstream-blocked, the default)
    FL_e    floor latency of a single e request on a quiet system
    c_e     CPU time per request of e (feeds rho)
    %_e     share of e in the window's arrivals
    avgS    = sum_e %_e c_e  (= cbar)
    W       shared wait, one curve per service: median_e (Lat_e - FL_e) per rho bin (rule A)
"""
import json
import os
from collections import defaultdict

import numpy as np

NOMINAL_MHZ = float(os.environ.get("GORDION_NOMINAL_MHZ", "2100"))  # f_nom, the clock c_e is expressed at: apt186 2100 (E5-2450), clnode258 2400 (8360Y)
# Lat_e source (Phase C section 0): "unstamped" = the server-side within-service latency from the
# tap with the downstream-blocked time excluded (rawp / pp stream; handoff F4 withdrawn);
# "stamped" = the Phase B definition (caller send stamp -> handler return, rawf / f stream).
LAT_MODE = os.environ.get("GORDION_LAT", "unstamped")

# per-endpoint stats vector written by the recorder: [a, n, p50, p90, p99, mean, max,
# pp50, pp90, q50, q90, x50, x90, ns, f50, f90]; older runs stop after x90, the
# oldest after p99/mean/max (then x = 0).
(EP_A, EP_N, EP_P50, EP_P90, EP_P99, EP_MEAN, EP_MAX, EP_PP50, EP_PP90, EP_Q50, EP_Q90,
 EP_X50, EP_X90, EP_NS, EP_F50, EP_F90) = range(16)

NOTATION = (("Lat = pooled p50 of the within-service latency (tap -> return, downstream-blocked time excluded);  " if LAT_MODE == "unstamped" else
             "Lat = pooled p50 of the per-endpoint latency incl. admission wait;  ") + "FL_e = floor;  c_e = CPU/req;  "
            "Fbar = med(sum %_e FL_e(q)) (mixture median of the floor distributions);  cbar = sum %_e c_e;  K = c2bar/(2 cbar);  rho = lambda cbar/(f/f_nom)/k;  "
            "h(rho) = W_sweep(rho)/K(sweep);  D[rho; mix] = Fbar + h(rho) K")


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


def costs_from_idle_run(R, settle=2.0, nominal_mhz=None):
    """c_e from a single-endpoint idle run (B1): (process CPU over the loaded windows minus
    the process's idle baseline CPU per second, measured on the run's idle windows) divided
    by completions, expressed at the nominal clock when the recorder reports the cores'
    frequency ("mhz": c_e_nominal = c_e_measured * mhz / nominal, i.e. cycles / nominal
    clock, Gordion's kcyc normalisation). The fixed per-request transport cost is part of
    c_e. Returns {endpoint: ms} for the endpoints that had completions (several share pro rata)."""
    nominal = nominal_mhz or NOMINAL_MHZ
    idle = [w["cpu"] / window_len_s(w) for w in R["windows"] if w.get("a", 0) == 0 and w.get("n", 0) == 0 and "cpu" in w]
    base = float(np.median(idle)) if idle else 0.0  # ns of CPU per second with no requests
    busy = [w for w in R["windows"] if (w.get("a", 0) or w.get("n", 0)) and "cpu" in w]
    if not busy:
        return {}
    t0 = min(w["t"] for w in busy)
    tot_cpu, secs, n, cyc = 0.0, 0.0, defaultdict(int), 0.0
    for w in busy:
        if w["t"] < t0 + settle * 1000:
            continue
        tot_cpu += w["cpu"]; secs += window_len_s(w)
        cyc += w["cpu"] * ((clock_mhz(w) or nominal) / nominal)  # CPU ns scaled to the nominal clock (only with GORDION_FREQ_NORM=1)
        for e, v in w["ep"].items():
            n[e] += v[EP_N]
    tot = sum(n.values())
    if tot == 0:
        return {}
    scale = cyc / tot_cpu if tot_cpu > 0 else 1.0
    return {e: max(0.0, tot_cpu - base * secs) * scale / tot / 1e6 for e in n if n[e] > 0}


def costs_from_sweep(R, settle=5.0, nominal_mhz=None, min_windows=200):
    """Marginal c_e by regression over a mixed sweep (Cherkasova-style): per window,
    CPU_nominal = base * dt + sum_e c_e * a_e, least squares with c_e >= 0 (projected). The
    marginal cost at operating loads, as opposed to the idle-run cost which carries the
    per-request wake-up overhead of a lightly loaded process. Returns ({e: ms}, base ms/s)."""
    nominal = nominal_mhz or NOMINAL_MHZ
    W, step_of, _ = settled_windows(R, settle)
    names = R["run"]["endpoints"]
    rows, y = [], []
    for w in W:
        if "cpu" not in w:
            continue
        cpu = w["cpu"] * ((clock_mhz(w) or nominal) / nominal) / 1e6  # ms at nominal clock (only with GORDION_FREQ_NORM=1)
        rows.append([window_len_s(w)] + [w["ep"].get(e, [0])[EP_A] for e in names]); y.append(cpu)
    if len(rows) < min_windows:
        return {}, 0.0
    X = np.array(rows, float); y = np.array(y)
    beta, *_ = np.linalg.lstsq(X, y, rcond=None)
    for _ in range(5):  # project negative costs to 0 and refit the rest
        neg = [i for i in range(1, len(beta)) if beta[i] < 0]
        if not neg:
            break
        keep = [i for i in range(len(beta)) if i not in neg]
        b2, *_ = np.linalg.lstsq(X[:, keep], y, rcond=None)
        beta = np.zeros(len(beta)); beta[keep] = b2
    return {e: float(beta[i + 1]) for i, e in enumerate(names)}, float(beta[0])


def window_len_s(w):
    return w.get("w", 100) / 1000.0


FREQ_NORM = os.environ.get("GORDION_FREQ_NORM", "0") == "1"


def clock_mhz(w):
    """The window's clock for cost / capacity normalisation: the recorder's "mhz" only when GORDION_FREQ_NORM=1.
    On clnode258 (no cpufreq driver) the recorder falls back to /proc/cpuinfo, which under-reports idle cores (2.0 GHz
    median at 40 req/s, 3.1 GHz when busy) while the MSR pin holds every core at 3.1 GHz = f_nom; there the field must
    be ignored (GORDION_FREQ_NORM=0) or the costs of lightly loaded runs come out 20-35 % low."""
    return w.get("mhz") if FREQ_NORM else None


def rho_of_window(w, c_ms):
    """busy-seconds per second offered in this window: sum_e a_e * c_e / W. With
    GORDION_FREQ_NORM=1 and a per-window "mhz" field, c_e is taken at the nominal clock and the
    window's capacity scales with its actual clock: rho = sum_e a_e c_e / (W * mhz / nominal),
    i.e. the offered fraction of the cycles actually available (Gordion's frequency term)."""
    s = 0.0
    for e, v in w["ep"].items():
        s += v[EP_A] * c_ms.get(e, 0.0) / 1000.0
    cap = window_len_s(w)
    if FREQ_NORM and w.get("mhz"):
        cap *= w["mhz"] / NOMINAL_MHZ
    return s / cap


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
    with a stamp), or 'inproc' (no stamp: admission wait invisible). With GORDION_LAT=unstamped
    (the default since Phase C) the stamp is ignored and the samples are the processing stream
    (tap -> handler return minus downstream-blocked time; source 'unstamped')."""
    v = w["ep"].get(e)
    if LAT_MODE == "unstamped":
        rp = w.get("rawp")
        if rp and e in rp and len(rp[e]):
            return np.asarray(rp[e], float), "unstamped"
        if v is None or v[EP_N] == 0:
            return np.zeros(0), "none"
        return np.full(v[EP_N], float(v[EP_PP50])), "unstamped"
    rf = w.get("rawf")
    if rf and e in rf and len(rf[e]):
        return np.asarray(rf[e], float), "full"
    raw = w.get("raw")
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


def settled_windows(R, settle_s, by="step", settle_mix=None):
    """Windows after warm-up and after the first settle_s of each step (and settle_mix of each
    mix change; default = settle_s, use 0 for a ramp run whose mix moves every second)."""
    W = R["windows"]
    sm = settle_s if settle_mix is None else settle_mix
    step_of = assign(W, R["steps"], "step")
    step_t0 = {s["step"]: s["t"] for s in R["steps"]}
    mix_of = assign(W, R["mixes"], "mix") if R["mixes"] else [0] * len(W)
    mix_t0 = {m["mix"]: m["t"] for m in R["mixes"]} if R["mixes"] else {0: -1e18}
    keep = [i for i, (w, s, m) in enumerate(zip(W, step_of, mix_of))
            if s is not None and s >= 0 and m is not None
            and w["t"] >= step_t0[s] + settle_s * 1000 and w["t"] >= mix_t0[m] + sm * 1000]
    return [W[i] for i in keep], [step_of[i] for i in keep], [mix_of[i] for i in keep]


def overloaded_steps(R, windows, step_of, tol=0.95):
    """Steps whose served arrival rate fell below tol x the target: the service was past
    its knee there, so its W is not a profiled maximum (F5)."""
    rate = {s["step"]: s["rate"] for s in R["steps"]}
    served = {}
    for s in sorted(set(step_of)):
        ws = [w for w, st in zip(windows, step_of) if st == s]
        if rate.get(s):
            served[s] = sum(w["a"] for w in ws) / sum(window_len_s(w) for w in ws) / rate[s]
    if not served:
        return set()
    # a downstream SUT sees only a fraction of the generator's rate (its share of the call
    # graph): compare each step's served/target ratio with the lowest step's
    ref = served[min(served)]
    over = {s for s, v in served.items() if v < tol * ref}
    # Phase D: with the admission gate the in-process queue is bounded (gRPC's per-connection stream limit), so an
    # overloaded step shows a flat ~40 ms wait in the server stream while the backlog sits in the socket; the load
    # generator's own latency sees it. A step whose caller-side median latency exceeds OVERLOAD_CALLER x the
    # server-side median is overloaded (the in-process stream is truncated there).
    reports = R.get("reports") or []
    if reports:
        ratio = {}
        for s in sorted(set(step_of)):
            srv = [v for w, st in zip(windows, step_of) if st == s for v in [w["all"][1]] if v > 0]
            cl = [r["all"][1] for r in reports if r.get("step") == s and r["all"][0] > 0]
            if len(srv) >= 5 and len(cl) >= 5:
                ratio[s] = np.median(cl) / np.median(srv)
        if ratio:
            # relative to the lowest step: a downstream SUT's caller latency is the whole end-to-end path, so only a
            # *growth* of the caller/server ratio marks a backlog outside the SUT's process
            r0 = ratio[min(ratio)]
            over |= {s for s, v in ratio.items() if v > OVERLOAD_CALLER * r0}
    return over


OVERLOAD_CALLER = 3.0  # growth of (caller-side p50 / server-side p50) vs the lowest step above this = backlog outside the process


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


# ----------------------------------------------------------------------------- service dictionary (Phase E, section 6)
WAIT_GUARD_NOISE = 3.0  # the wait guard is on only if the profiled W_max exceeds this many times the floor's own noise
NOISE_RHO = 0.5         # (Phase E rule, retired in Phase F: the noise is the lowest step's own per-second spread)


FLOOR_Q = 41  # quantile points kept per endpoint for the "mixture" Fbar


def floor_quantiles_lowest_step(windows, step_of, names, fl):
    """Per endpoint, FLOOR_Q quantiles of its lowest-step Lat_e samples (the floor distribution); endpoints without
    samples get their FL_e as a single point."""
    fs = min(step_of)
    out = {}
    for e in names:
        acc = []
        for w, st in zip(windows, step_of):
            if st == fs:
                x, _ = lat_samples(w, e)
                acc.extend((x / 1e6).tolist())
        out[e] = np.quantile(acc, np.linspace(0, 1, FLOOR_Q)) if len(acc) >= 5 else np.array([fl[e]])
    return out


def mix_stats(shares, c_ms, fl, fbar="mean", flq=None):
    """Fbar, cbar, c2bar and K of a mix {e: share (or count)}. Fbar = sum %_e FL_e ("mean", section 6), the
    share-weighted median of FL_e ("median"), or the median of the mixture of the endpoints' floor distributions
    ("mixture": the quantile points of each endpoint weighted by its share -- the floor statistic that matches a
    pooled-median Lat whether the endpoints' latencies overlap or form separate modes)."""
    tot = float(sum(shares.values()))
    if tot <= 0:
        return dict(Fbar=np.nan, cbar=np.nan, c2=np.nan, K=np.nan)
    sh = {e: v / tot for e, v in shares.items() if v > 0}
    cb = sum(v * c_ms.get(e, 0.0) for e, v in sh.items()); c2 = sum(v * c_ms.get(e, 0.0) ** 2 for e, v in sh.items())
    K = c2 / (2 * cb) if cb > 0 else np.nan
    if fbar == "mixture" and flq:
        pts, wts = [], []
        for e, v in sh.items():
            q = flq.get(e)
            if q is None or len(q) == 0:
                q = np.array([fl.get(e, 0.0)])
            pts.append(np.asarray(q, float)); wts.append(np.full(len(q), v / len(q)))
        pts = np.concatenate(pts); wts = np.concatenate(wts)
        o = np.argsort(pts); c = np.cumsum(wts[o])
        F = float(pts[o][int(np.searchsorted(c, 0.5 * c[-1]))])
    elif fbar == "median":
        acc, F = 0.0, np.nan
        for e in sorted(sh, key=lambda e: fl.get(e, 0.0)):
            acc += sh[e]
            if acc >= 0.5:
                F = fl.get(e, 0.0); break
    else:
        F = sum(v * fl.get(e, 0.0) for e, v in sh.items())
    return dict(Fbar=float(F), cbar=float(cb), c2=float(c2), K=float(K))


class ServiceDict:
    """D[rho; mix] = Fbar(mix) + h(rho) K(mix) from one profiling sweep (fit_service_dict). Stores h per rho bin,
    FL_e, c_e, the sweep's own pooled p50 per bin (= the dictionary at the sweep mix), the profiled W_max and the
    floor noise for the guards (section 1.3): rho outside the profiled range -> n/a; Lat - Fbar above W_max -> n/a
    only if W_max > WAIT_GUARD_NOISE x noise (otherwise the wait guard is off: a floor-only service)."""

    def __init__(self, k, mids, p50, Fbar_b, K_b, W, h, fl, c_ms, names, Fbar_sweep, K_sweep, cbar_sweep, wmax, noise, noise_steps,
                 fbar, steps, overloaded, label, csrc, flq=None):
        self.flq = flq
        ok = np.isfinite(h)
        half = float(mids[1] - mids[0]) / 2 if len(mids) > 1 else 0.0
        self.k = k; self.mids = np.asarray(mids)[ok]; self.h = np.asarray(h)[ok]; self.W = np.asarray(W)[ok]; self.p50 = np.asarray(p50)[ok]
        self.Fbar_b = np.asarray(Fbar_b)[ok]; self.K_b = np.asarray(K_b)[ok]
        self.rho_min = float(self.mids.min() - half); self.rho_max = float(self.mids.max() + half)
        self.fl = fl; self.c_ms = c_ms; self.names = names; self.Fbar_sweep = Fbar_sweep; self.K_sweep = K_sweep; self.cbar_sweep = cbar_sweep
        self.wmax = float(wmax); self.noise = float(noise); self.noise_steps = noise_steps; self.fbar = fbar; self.steps = steps
        self.overloaded = overloaded; self.label = label; self.csrc = csrc
        self.wait_guard = bool(self.wmax > WAIT_GUARD_NOISE * self.noise)

    def h_at(self, rho):
        return float(np.interp(rho, self.mids, self.h))

    def p50_at(self, rho):
        """the sweep's own pooled p50 at rho: the dictionary evaluated at the sweep mix"""
        return float(np.interp(rho, self.mids, self.p50))

    def mix(self, shares):
        return mix_stats(shares, self.c_ms, self.fl, self.fbar, self.flq)

    def fbar_both(self, shares):
        """(Fbar mean, Fbar mixture median) of a mix, for the comparison columns"""
        return (mix_stats(shares, self.c_ms, self.fl, "mean")["Fbar"], mix_stats(shares, self.c_ms, self.fl, "mixture", self.flq)["Fbar"])

    def predict(self, rho, shares):
        m = self.mix(shares)
        return m["Fbar"] + self.h_at(rho) * m["K"], m

    def guard(self, rho, lat, Fbar):
        """'' (usable), 'rho' (outside the profiled range) or 'wait' (Lat - Fbar above the profiled W_max, wait guard on)"""
        if rho < self.rho_min or rho > self.rho_max:
            return "rho"
        if self.wait_guard and lat - Fbar > self.wmax:
            return "wait"
        return ""

    def describe(self):
        return (f"{self.label}: Fbar({self.fbar}) {self.Fbar_sweep:.3f} ms, cbar {self.cbar_sweep:.3f} ms, K {self.K_sweep:.3f} ms, {len(self.mids)} rho bins in "
                f"[{self.rho_min:.2f}, {self.rho_max:.2f}], W_max {self.wmax:.2f} ms, floor noise {self.noise:.3f} ms over steps {self.noise_steps} "
                f"-> wait guard {'on' if self.wait_guard else 'off'}; overloaded steps dropped {self.overloaded or 'none'}; c_e: {self.csrc}")

    def save(self, path):
        json.dump(dict(form="D[rho; mix] = Fbar + h(rho) K", fbar=self.fbar, k=self.k, rho=self.mids.round(4).tolist(), h=self.h.round(5).tolist(),
                       W_sweep_ms=self.W.round(4).tolist(), p50_sweep_ms=self.p50.round(4).tolist(), Fbar_sweep_ms=self.Fbar_sweep, K_sweep_ms=self.K_sweep,
                       cbar_sweep_ms=self.cbar_sweep, wmax_ms=self.wmax, floor_noise_ms=self.noise, noise_steps=self.noise_steps, wait_guard=self.wait_guard,
                       fl_ms=self.fl, c_ms=self.c_ms, overloaded_steps=self.overloaded, source=self.label, costs=self.csrc,
                       floor_quantiles_ms=({e: np.round(q, 5).tolist() for e, q in self.flq.items()} if self.flq else None)),
                  open(path, "w"), indent=1)


def floor_noise_lowest_step(windows, step_of, step):
    """IQR (p75 - p25) of the per-second pooled p50 over the windows of one step: the floor's own noise, robust to the
    idle-core wake-up outliers that a lightly loaded Go service shows second to second."""
    sec = defaultdict(list)
    for w, st in zip(windows, step_of):
        if st == step:
            for e in w["ep"]:
                x, _ = lat_samples(w, e)
                if len(x):
                    sec[w["t"] // 1000].extend((x / 1e6).tolist())
    p = np.array([np.median(v) for v in sec.values() if len(v) >= 5])
    return float(np.percentile(p, 75) - np.percentile(p, 25)) if len(p) >= 5 else 0.0


def fit_service_dict(R, settle=5.0, costs_file=None, fbar="mixture", nbins=24, min_n=20, smooth_s=1.0, label=None):
    """Section 1.1: h(rho) = W_sweep(rho) / K(sweep mix) from one profiling sweep, with W_sweep = pooled p50 of all
    requests in the rho bin minus Fbar of the bin's arrival mix; FL_e = per-endpoint median at the lowest step; c_e
    from the costs file (or the run's configured cpu_ms). Overloaded steps (served < target, caller/server ratio
    growth, or pooled wait > OVERLOAD_WAIT x cbar) are dropped. Floor noise = the lowest kept step's second-to-second
    spread of the pooled p50 (IQR), see floor_noise_lowest_step."""
    names = R["run"]["endpoints"]; cfg = dict(zip(names, R["run"]["floors_ms"]))
    k = cpuset_size(R["meta"].get("cpuset", "0-7"))
    c_ms, csrc = costs_of_run(R, costs_file)
    W, step_of, _ = settled_windows(R, settle)
    fl, _ = floors_lowest_step(W, step_of, names, cfg)
    flq = floor_quantiles_lowest_step(W, step_of, names, fl)  # FL_e(q): stored with the dictionary (Phase F: Fbar = mixture median)
    over = overloaded_steps(R, W, step_of)
    steps = {}
    for s in sorted(set(step_of)):
        ws = [w for w, st in zip(W, step_of) if st == s]
        samp = np.concatenate([lat_samples(w, e)[0] for w in ws for e in w["ep"]]) / 1e6
        arr = defaultdict(float)
        for w in ws:
            for e, v in w["ep"].items():
                arr[e] += v[EP_A]
        m = mix_stats(arr, c_ms, fl, fbar, flq)
        rho = float(np.mean([rho_of_window(w, c_ms) for w in ws])) / k
        steps[s] = dict(rho=rho, p50=float(np.median(samp)) if len(samp) else np.nan, n=len(samp), **m)
        if len(samp) and steps[s]["p50"] - m["Fbar"] > OVERLOAD_WAIT * m["cbar"]:
            over.add(s)
    keep = [i for i, s in enumerate(step_of) if s not in over]
    Wk = [W[i] for i in keep]
    rho = rho_series(Wk, c_ms, smooth_s) / k
    edges = np.linspace(0, 1.05, nbins + 1); mids = (edges[:-1] + edges[1:]) / 2
    bins = np.clip(np.digitize(rho, edges) - 1, 0, nbins - 1)
    pooled = [[] for _ in range(nbins)]; arrb = [defaultdict(float) for _ in range(nbins)]
    for i, w in enumerate(Wk):
        b = bins[i]
        for e, v in w["ep"].items():
            x, _ = lat_samples(w, e)
            if len(x):
                pooled[b].append(x / 1e6)
            arrb[b][e] += v[EP_A]
    p50 = np.full(nbins, np.nan); Fb = np.full(nbins, np.nan); Kb = np.full(nbins, np.nan)
    # the profiled range is the range of the steps actually run (plus one bin of slack): bins populated only by the
    # rho noise of the highest step's windows are not operating points
    kept_rho = [v["rho"] for s_, v in steps.items() if s_ not in over]
    top = (max(kept_rho) if kept_rho else 1.05) + (edges[1] - edges[0])
    for b in range(nbins):
        if pooled[b] and mids[b] <= top:
            x = np.concatenate(pooled[b])
            if len(x) >= min_n:
                m = mix_stats(arrb[b], c_ms, fl, fbar, flq)
                p50[b] = np.median(x); Fb[b] = m["Fbar"]; Kb[b] = m["K"]
    Wb = p50 - Fb
    with np.errstate(invalid="ignore", divide="ignore"):
        h = Wb / Kb
    arr_all = defaultdict(float)
    for w in Wk:
        for e, v in w["ep"].items():
            arr_all[e] += v[EP_A]
    ms = mix_stats(arr_all, c_ms, fl, fbar, flq)
    kept_steps = {s: v for s, v in steps.items() if s not in over and np.isfinite(v["p50"])}
    # floor noise (Phase F): the lowest kept step's own second-to-second spread of the pooled p50 (p95 - p5 over its
    # one-second windows). The Phase E rule (max - min of the per-step p50 over the steps at rho <= 0.5) counted a wait
    # that rises steadily from low load as noise (geo under the one-slot gate) and switched the wait guard off there.
    low = [min(kept_steps)] if kept_steps else []
    noise = floor_noise_lowest_step(W, step_of, low[0]) if low else 0.0
    return ServiceDict(k, mids, p50, Fb, Kb, Wb, h, fl, c_ms, names, ms["Fbar"], ms["K"], ms["cbar"], np.nanmax(Wb) if np.isfinite(Wb).any() else 0.0,
                       noise, sorted(low), fbar, steps, sorted(over), label or os.path.basename(R["dir"]), csrc, flq)
