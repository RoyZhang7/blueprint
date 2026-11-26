#!/usr/bin/env python3
"""Phase K5: the Gordion scorer as an on-node process at the recorder's tick (RESULTS-K 5, rung 1 "scorer on-node at
100 ms"; rung 2 "scorer alone replaying recorded inputs"). Dependency-free (the nodes have no numpy); it measures its
own CPU per tick, which is what K5 reads from it. The arithmetic is the Phase G chain's for a single-endpoint service:
  D(rho) from the service's own sweep (pooled p50 per rho bin, interpolated; the mix term is trivial for one endpoint),
  E = (Lat - D) / Lat with a 5 s causal Gaussian over the ticks, the C.1 rule in its order (6, 1, 3, 4, 2, 5, 7) on
  phi (the agent's occupancy by holder), R_foreign, I_g = U / (lambda cbar g(rho)), Q (Go: rt mutex wait), slot busy.
  Row 3 (inflation) carries a sub-rule on the neighbors' MBM (Phase I 5.1, stop-point-3 decision 2): the largest
  mbm_total of any monitored cgroup other than the victim, in GB/s over the window; "bw" above --mbm-bw (10 GB/s: a
  bandwidth neighbor, mitigation MBA), "cap" between --mbm-cap (1 GB/s) and that (a cache-capacity neighbor, CAT), ""
  otherwise. Written as "sub" with "nb_mbm" on every row.
Inputs per tick: the recorder's window line (GORDION_OUT) and the agent's tick line (k5agent --out), matched by time.
  --follow      tail both files as they grow (the on-node mode)
  --replay      read both files from the start and process them at their own pace (one tick per recorded window)
  --fit SWEEP_RECORDER [--fit-agent SWEEP_AGENT] --dict OUT.json
                fit the dictionary from a sweep's recorder stream (and the agent's perf/cmt columns for A4) and exit
usage: k5score.py --recorder F --agent F --dict D.json --out F [--k 2 --slots 1 --go --tick-ms 100 --follow|--replay]
"""
import argparse, json, math, os, resource, sys, time

ap = argparse.ArgumentParser()
ap.add_argument("--recorder"); ap.add_argument("--agent"); ap.add_argument("--dict"); ap.add_argument("--out")
ap.add_argument("--k", type=int, default=1); ap.add_argument("--slots", type=int, default=None); ap.add_argument("--go", action="store_true")
ap.add_argument("--tick-ms", type=int, default=100); ap.add_argument("--follow", action="store_true"); ap.add_argument("--replay", action="store_true")
ap.add_argument("--fit"); ap.add_argument("--fit-agent"); ap.add_argument("--fit-from-t", type=int, default=0, help="fit: skip windows before this unix ms"); ap.add_argument("--fit-to-t", type=int, default=0, help="fit: skip windows from this unix ms on (0: none)"); ap.add_argument("--fit-min-windows", type=int, default=10, help="fit: keep a rho bin only with this many windows (Phase I: 50, as RESULTS-K 4.5.1)"); ap.add_argument("--fit-cg", default="victim"); ap.add_argument("--victim", default="victim")
ap.add_argument("--fit-rates", default="", help="J3 (step 12's refit): bin the sweep by its offered-rate steps (comma list of req/s) instead of by each window's rho: a window goes to the nearest step by its arrival rate, the bin's rho is the step's mean rho, and a step whose completions are under --fit-guard of its arrivals (overload: the queue, not the service) is dropped from the dictionary and listed in \"dropped_steps\"")
ap.add_argument("--fit-guard", type=float, default=0.98)
ap.add_argument("--smooth-s", type=float, default=5.0); ap.add_argument("--e-far", type=float, default=0.15); ap.add_argument("--phi-far", type=float, default=0.05)
ap.add_argument("--floor-ms", type=float, default=10.0, help="per second; R_foreign and Q read ~0 under it"); ap.add_argument("--i-far", type=float, default=0.15)
ap.add_argument("--secs", type=float, default=0)
ap.add_argument("--cpuset-file", default=None, help="step 8: k = the number of CPUs in this cpuset file (the pod cgroup's cpuset.cpus.effective), for the fit and the scoring")
ap.add_argument("--sirq-base", type=float, default=None, help="step 8: the victim CPUs' quiet softirq share (/proc/stat, from the agent's sampler); with --sirq-rise, row 8 \"interrupt load on our CPUs, no latency harm\" when I_g is flagged with E under its threshold")
ap.add_argument("--sirq-rise", type=float, default=0.10)
ap.add_argument("--irq-far", type=float, default=0.10, help="step 9: phi_irq (the victim CPUs' softirq share above the quiet baseline) over this reads row 9, capacity loss to interrupts")
ap.add_argument("--mask", default="", help="offline replay: comma list of signals to withhold from the rule (phi,Rf,Ig,Q,slot,mbm) or 'all': the dictionary-only verdict (rows 6 / 7)")
ap.add_argument("--fast", action="store_true", help="replay without the tick sleep (offline analysis)")
ap.add_argument("--mbm-bw", type=float, default=10.0, help="row-3 sub-rule: neighbor mbm_total GB/s at or above which the neighbor is a bandwidth neighbor")
ap.add_argument("--mbm-cap", type=float, default=1.0, help="row-3 sub-rule: neighbor mbm_total GB/s above which (and below --mbm-bw) it is a capacity neighbor")
a = ap.parse_args()


def cpu_ns():
    r = resource.getrusage(resource.RUSAGE_SELF)
    return int((r.ru_utime + r.ru_stime) * 1e9)


def cpuset_k(path):
    """the number of CPUs in a cpuset list file (step 8: k read from the pod's cpuset)"""
    n = 0
    for p in open(path).read().strip().split(","):
        if not p: continue
        lo, _, hi = p.partition("-"); n += int(hi or lo) - int(lo) + 1
    return n


def fit(rec_path, agent_path, out_path, k, cg, from_t=0, to_t=0, min_windows=10):
    """Per rho bin (24 over the sweep's range): completion-weighted median of the window p50s, CPU per request (the
    recorder's cpu field / completions), and, with the agent file, CPI, MPKI and LLC occupancy per bin. cbar = CPU per
    request over the windows of the lowest tenth of the load (the floor steps)."""
    W = []
    for l in open(rec_path):
        try: d = json.loads(l)
        except Exception: continue
        if d.get("lat"): d["all"] = d["lat"]   # the recorders' production mode (RESULTS-K 5.8): "all" is written but empty
        if "all" not in d or not d["all"][0] or d["t"] < from_t or (to_t and d["t"] >= to_t): continue
        W.append(d)
    if not W: sys.exit("fit: no windows")
    agent = {}
    if agent_path:
        for l in open(agent_path):
            if '"k":' not in l: continue
            try: d = json.loads(l)
            except Exception: continue
            agent[d["t"] // 100] = d
    rate = [w["a"] / (w["w"] / 1000.0) for w in W]
    lo = sorted(rate)[max(0, int(0.1 * len(rate)) - 1)]
    low = [w for w, r in zip(W, rate) if r <= lo and w.get("cpu")]
    nc = lambda w: w.get("nc", w["all"][0])   # completions: in production mode all[0] counts only the timed 1 in N
    cbar = sum(w["cpu"] for w in low) / max(1, sum(nc(w) for w in low)) / 1e6 if low else 0.05
    rho = [r * cbar / k / 1000.0 for r in rate]
    rmax = max(rho) * 1.05 + 1e-9
    dropped = []
    if a.fit_rates:
        # J3: step binning. Each window joins the nearest offered-rate step by its arrival rate; the overload guard drops a step
        # whose completions fall under fit_guard of its arrivals (RESULTS-I 11c: per-window-rho bins smear the knee, a window of
        # an overloaded step with fewer arrivals than average lands in a lower bin carrying the step's queue)
        R = sorted(float(x) for x in a.fit_rates.split(",") if x)
        nb = len(R); bins = [[] for _ in range(nb)]
        for w, r, lam in zip(W, rho, rate):
            b = min(range(nb), key=lambda i: abs(R[i] - lam)); bins[b].append((w, r))
        kept = []
        for b in range(nb):
            ws = bins[b]
            if not ws: continue
            arr = sum(w["a"] for w, _ in ws); comp = sum(nc(w) for w, _ in ws)
            if arr and comp < a.fit_guard * arr: dropped.append(dict(rate=R[b], windows=len(ws), served_over_offered=round(comp / arr, 4))); continue
            kept.append(ws)
        bins = kept; nb = len(bins)
    else:
        nb = 24
        bins = [[] for _ in range(nb)]
        for w, r in zip(W, rho):
            b = min(nb - 1, int(r / rmax * nb))
            bins[b].append((w, r))
    steps = []
    for b in range(nb):
        ws = bins[b]
        if len(ws) < min_windows: continue
        pairs = sorted((w["all"][1] / 1e6, w["all"][0]) for w, _ in ws)
        tot = sum(n for _, n in pairs); acc = 0; p50 = pairs[-1][0]
        for v, n in pairs:
            acc += n
            if acc >= tot / 2: p50 = v; break
        cpu = sum(w.get("cpu", 0) for w, _ in ws); n = sum(nc(w) for w, _ in ws)
        u = cpu / max(1, n) / 1e6
        st = dict(rho=sum(r for _, r in ws) / len(ws), p50_ms=p50, u_ms=u, g=(u / cbar if cbar else 1.0), windows=len(ws))
        st["_ws"] = ws
        if agent:
            cy = ins = mi = 0; occ = []
            for w, _ in ws:
                t = agent.get(w["t"] // 100 + 1) or agent.get(w["t"] // 100)
                if not t: continue
                p = t.get("perf", {}).get(cg)
                if p: cy += p[0]; ins += p[1]; mi += p[2]
                c = t.get("cmt", {}).get(cg)
                if c: occ.append(sum(c))
            if ins: st["cpi"] = cy / ins; st["mpki"] = mi / ins * 1000.0; st["ins_per_req"] = ins / max(1, n); st["miss_per_req"] = mi / max(1, n)
            if occ: st["llc_kb"] = sorted(occ)[len(occ) // 2] / 1024.0
        steps.append(st)
    # step 8: the E threshold per bin = the p95 of |E| over the sweep's windows that fall in the bin, floored at 0.03, with E
    # the statistic the scorer tests: each window's (p50_w - D_bin) / p50_w smoothed by the same 5 s causal Gaussian over the
    # sweep's window sequence ("e_thr_raw" keeps the unsmoothed p95 beside it)
    nw = max(1, int(a.smooth_s * 1000 / a.tick_ms)); sg = nw / 3.0; gw = [math.exp(-0.5 * (i / sg) ** 2) for i in range(nw)]
    binp = {}
    for st in steps:
        for w, _ in st["_ws"]: binp[w["t"]] = (st["p50_ms"], st)
    seq = sorted(W, key=lambda w: w["t"]); hist = []; eraw = {}; esm = {}
    for w in seq:
        if w["t"] not in binp or not w["all"][1]: continue
        p50b, st = binp[w["t"]]; e = max(0.0, min(1.0, (w["all"][1] / 1e6 - p50b) / (w["all"][1] / 1e6)))
        hist.append(e)
        if len(hist) > nw: del hist[0]
        num = den = 0.0
        for i, v in enumerate(reversed(hist)): num += gw[i] * v; den += gw[i]
        eraw.setdefault(id(st), []).append(abs((w["all"][1] / 1e6 - p50b) / (w["all"][1] / 1e6))); esm.setdefault(id(st), []).append(num / den)
    for st in steps:
        for key, src in (("e_thr", esm), ("e_thr_raw", eraw)):
            ae = sorted(src.get(id(st), []))
            st[key] = max(0.03, ae[min(len(ae) - 1, int(0.95 * (len(ae) - 1)))]) if ae else 0.03
        del st["_ws"]
    flo = [w["all"][1] / 1e6 for w, r in zip(W, rate) if r <= lo]
    flo.sort()
    p = flo[len(flo) // 2] if flo else steps[0]["p50_ms"]
    q1, q3 = (flo[len(flo) // 4], flo[3 * len(flo) // 4]) if len(flo) >= 4 else (p, p)
    D = dict(form="K5 single-endpoint D(rho): pooled p50 per rho bin" if not a.fit_rates else "K5 single-endpoint D(rho): pooled p50 per offered-rate step (J3 step binning, overload guard)", cbar_ms=cbar, k=k, floor_p_ms=p, floor_sigma_ms=max(1e-3, (q3 - q1) / 1.349),
             steps=steps, source=os.path.basename(rec_path), windows=len(W), e_thr="p95 of the scorer's smoothed |E| per rho bin over the sweep's windows, floored at 0.03 (step 8); e_thr_raw: unsmoothed",
             binning=("steps" if a.fit_rates else "rho"), fit_rates=a.fit_rates, fit_guard=a.fit_guard, dropped_steps=dropped)
    json.dump(D, open(out_path, "w"), indent=1)
    print(f"fit: {len(W)} windows, cbar {cbar:.4f} ms, k {k}, {len(steps)} bins, rho {steps[0]['rho']:.2f}..{steps[-1]['rho']:.2f}, floor p {p:.3f} ms, E_thr {min(s['e_thr'] for s in steps):.3f}-{max(s['e_thr'] for s in steps):.3f}")


if a.cpuset_file: a.k = cpuset_k(a.cpuset_file)
if a.fit:
    fit(a.fit, a.fit_agent, a.dict, a.k, a.fit_cg, a.fit_from_t, a.fit_to_t, a.fit_min_windows); sys.exit(0)

D = json.load(open(a.dict))
ST = D["steps"]; CBAR = D["cbar_ms"]; K = a.k; SLOTS = a.slots or max(1, K - 1)
RHO = [s["rho"] for s in ST]


def interp(key, rho):
    if rho <= RHO[0]: return ST[0].get(key, 1.0)
    if rho >= RHO[-1]: return ST[-1].get(key, 1.0), True
    for i in range(1, len(RHO)):
        if rho <= RHO[i]:
            f = (rho - RHO[i - 1]) / (RHO[i] - RHO[i - 1])
            return ST[i - 1].get(key, 1.0) * (1 - f) + ST[i].get(key, 1.0) * f
    return ST[-1].get(key, 1.0)


def dget(key, rho):
    v = interp(key, rho)
    return (v[0], True) if isinstance(v, tuple) else (v, False)


# causal Gaussian over the past W seconds (sigma = W/3), as b4_compare's smoothing
NW = max(1, int(a.smooth_s * 1000 / a.tick_ms)); SIG = NW / 3.0
GW = [math.exp(-0.5 * (i / SIG) ** 2) for i in range(NW)]
hist = []
hold = [None]            # holder names from the agent's start record
vcpus = [[]]             # the victim's CPUs (step 8: the softirq share from the sampler's /proc/stat lines)
vidx = [0]


def smooth(x, h=hist):
    h.append(x)
    if len(h) > NW: del h[0]
    num = den = 0.0
    for i, v in enumerate(reversed(h)):
        num += GW[i] * v; den += GW[i]
    return num / den


hist_irq = []   # step 9: phi_irq smoothed as E is (one CPU and 100 ms ticks quantize the raw share in steps of 0.1)
hist_rho = []   # step 11c: rho smoothed as E is, for the amount (per-window rho and I_g are noisy and D is convex at the knee: the median of per-window D' overshoots)
hist_ig = []    # step 11: the corrected I_g smoothed the same way, the gate on row 9 (a slowed core inflates its softirq time too: C1' read row 9 on half its windows ungated)


class Tail:
    """follow mode: start at the end of the file (the recorder's and the agent's streams predate the scorer), keeping an
    agent file's start record (the holder names); replay mode: from the beginning"""
    def __init__(self, path, replay, keep_start=False):
        self.path = path; self.f = None; self.replay = replay; self.buf = ""; self.keep_start = keep_start

    def lines(self):
        if self.f is None:
            if not self.path or not os.path.exists(self.path): return []
            self.f = open(self.path, "r")
            if not self.replay:
                first = self.f.readline() if self.keep_start else ""
                self.f.seek(0, 2)
                return [first] if first.startswith('{"event":"start"') else []
        out = []
        while True:
            l = self.f.readline()
            if not l: break
            if not l.endswith("\n"): self.buf += l; break
            l = self.buf + l; self.buf = ""
            out.append(l)
        return out


MASK = set(["phi", "Rf", "Ig", "Q", "slot", "mbm", "irq"]) if a.mask == "all" else set(x for x in a.mask.split(",") if x)
fo = open(a.out, "w")
rec = Tail(a.recorder, a.replay); ag = Tail(a.agent, a.replay, keep_start=True)
fo.write(json.dumps(dict(event="start", t=int(time.time() * 1000), dict=a.dict, k=K, slots=SLOTS, go=a.go, mode="replay" if a.replay else "follow", pid=os.getpid())) + "\n"); fo.flush()
agent_by_t = {}          # tick index (t // tick_ms) -> agent record
pend_rec = []
last_cpu = cpu_ns(); n = 0; t_start = time.time(); rows = {}
tick_s = a.tick_ms / 1000.0
next_t = time.monotonic() + tick_s
while True:
    now = time.monotonic()                      # one tick per loop in both modes: replay runs at the recorded pace
    if next_t > now and not a.fast: time.sleep(next_t - now)
    next_t += tick_s
    for l in ag.lines():
        if '"k":' in l:
            try: d = json.loads(l)
            except Exception: continue
            agent_by_t[d["t"] // a.tick_ms] = d
        elif '"start"' in l and hold[0] is None:
            try:
                s = json.loads(l); hold[0] = s.get("holders"); vidx[0] = hold[0].index(a.victim) if a.victim in hold[0] else 0
                vcpus[0] = [str(c) for g in (s.get("cg") or []) if g.get("victim") for c in g.get("cpus", [])]
            except Exception: pass
    new = rec.lines()
    if a.replay and not new and not pend_rec:
        break
    pend_rec.extend(new)
    if a.replay: todo, pend_rec = pend_rec[:1], pend_rec[1:]
    else: todo, pend_rec = pend_rec[:], []
    for l in todo:
        if '"all"' not in l and '"lat"' not in l: continue
        try: w = json.loads(l)
        except Exception: continue
        n += 1
        al = w.get("all") or [0, 0]; wl = w.get("w", a.tick_ms) / 1000.0
        if w.get("lat"): al = w["lat"]          # production mode: [n timed, p50, p90, p99] from the histogram
        nn = al[0]
        lam = w["a"] / wl
        rho = lam * CBAR / K / 1000.0
        lat = al[1] / 1e6 if nn else 0.0
        Dv, past = dget("p50_ms", rho)
        E_raw = max(0.0, min(1.0, (lat - Dv) / lat)) if lat > 0 else 0.0
        E = smooth(E_raw)
        y = max(0.0, math.tanh(0.07 * (lat - D["floor_p_ms"]) / D["floor_sigma_ms"]))
        g, _ = dget("g", rho)
        t = agent_by_t.get(w["t"] // a.tick_ms + 1) or agent_by_t.get(w["t"] // a.tick_ms)
        phi = Rf = Ig = U = Rd = None; holder = ""; nb_mbm = None; sub = ""; sirq = None; phi_irq = None; sirq_ns = 0.0; Ig_raw = None
        if t and t.get("cg"):
            c = t["cg"][vidx[0]] if vidx[0] < len(t["cg"]) else t["cg"][0]
            occ = c["occ"]; tot = sum(occ)
            self_i = vidx[0]; idle = len(occ) - 1; root = len(occ) - 3
            foreign = [x for i, x in enumerate(occ) if i not in (self_i, idle)]
            phi = (sum(foreign) / tot) if tot else 0.0
            if tot:
                hi = max((i for i in range(len(occ)) if i not in (self_i, idle)), key=lambda i: occ[i])
                holder = hold[0][hi] if hold[0] and hi < len(hold[0]) else str(hi)
            Rf = sum(x for i, x in enumerate(c["R"]) if i != self_i) / 1e6
            Rd = c.get("Rrd", 0) / 1e6                # the victim threads' total run_delay in the window (F_RD, or the split's share)
            smp = t.get("smp", {}).get(a.victim)
            if smp: U = smp[0] * 1000.0            # usage_usec -> ns
            mb = [sum(v) for n, v in (t.get("mbm") or {}).items() if n != a.victim]
            if mb:
                nb_mbm = max(mb) / wl / 1e9
                sub = "bw" if nb_mbm >= a.mbm_bw else ("cap" if nb_mbm > a.mbm_cap else "")
            ps = t.get("pst") or {}
            shares = [(ps[c][2] / (ps[c][0] + ps[c][3]), ps[c][2], ps[c][0] + ps[c][3]) for c in vcpus[0] if c in ps and ps[c][0] + ps[c][3] > 0]
            if shares:
                sirq = sum(x[0] for x in shares) / len(shares)      # the victim CPUs' softirq share of the window ([busy, irq, softirq, idle] jiffies), per CPU
                if a.sirq_base is not None:
                    phi_irq = smooth(max(0.0, sum(max(0.0, x[0] - a.sirq_base) for x in shares) / len(shares)), hist_irq)   # step 9: the share above the quiet baseline, smoothed as E
                    sirq_ns = sum(max(0.0, x[1] - a.sirq_base * x[2]) for x in shares) * 1e7              # its time (jiffies of 10 ms above the baseline)
        if U is None and w.get("cpu") is not None: U = w["cpu"]
        if MASK:                                  # offline: withhold signals from the rule (the dictionary-only verdict with "all")
            if "phi" in MASK: phi = None; holder = ""
            if "Rf" in MASK: Rf = None
            if "Ig" in MASK: U = None
            if "mbm" in MASK: nb_mbm = None; sub = ""
            if "irq" in MASK: phi_irq = None; sirq_ns = 0.0
        Ig_s = None; rho_s = smooth(rho, hist_rho)
        if U is not None and (nn or w["a"]):   # I_g needs only lambda (arrivals, counted without the timers) and U
            Ig_raw = (U / 1e6) / max(1e-9, lam * wl * CBAR * g)   # CPU ms in the window / expected ms
            Ig = ((U - sirq_ns) / 1e6) / max(1e-9, lam * wl * CBAR * g)   # step 9: the victim CPUs' softirq time above baseline subtracted
            Ig_s = smooth(Ig, hist_ig)
        Q = (w.get("rt", [0, 0, 0])[2] / 1e6) if a.go and "Q" not in MASK else 0.0
        G = (w.get("rt", [0, 0, 0])[0] / 1e6) if a.go else 0.0
        slot = 0.0 if "slot" in MASK else ((w["slot"] / 1e9 / wl / SLOTS) if w.get("slot") is not None else ((U / 1e9 / wl / SLOTS) if U is not None else 0.0))
        fl = a.floor_ms * wl
        thr = dget("e_thr", rho)[0] if "e_thr" in ST[0] else a.e_far   # step 8: the sweep's own threshold at this rho
        E_ok = E < thr; phi_ok = phi is not None and phi > a.phi_far; Rf_ok = Rf is not None and Rf > fl
        # step 11: row 9 needs the smoothed corrected I_g within i_far of 1; a far I_g with a raised softirq share is service-time inflation that slowed the softirq path too (C1')
        irq_hi = phi_irq is not None and phi_irq > a.irq_far and not (Ig_s is not None and Ig_s > 1 + a.i_far)   # step 11b: the scorer's own I_g flag (1 + i_far) on the smoothed corrected I_g
        # step 9: the capacity-loss amount = D at the effective load k(1 - phi - phi_irq) minus D at the load; target = the CPUs' IRQ affinity
        rho_eff = rho / max(1e-6, 1.0 - (phi or 0.0) - (phi_irq or 0.0)); D_eff, past_eff = dget("p50_ms", rho_eff); amount = D_eff - Dv
        I_hi = Ig is not None and Ig > 1 + a.i_far; I_ok = Ig is not None and abs(Ig - 1) <= a.i_far
        Q_hi = Q > fl; Q_ok = not Q_hi
        if not nn: row = 7                       # no latency sample in the window: no verdict (as without the probe)
        elif irq_hi: row = 9                     # 9: capacity loss to interrupts (step 9); amount = D(rho_eff) - D(rho), target the IRQ affinity
        elif E_ok: row = 6
        elif phi_ok: row = 1
        elif I_hi: row = 3
        elif I_ok and Q_hi: row = 4
        elif I_ok and Q_ok and slot >= 0.9: row = 2
        else: row = 7
        # step 11b: the amount on every attributing row: rows 1 / 9 from D at k(1 - phi - phi_irq), row 3 from D' = I_g * D(I_g * rho) (RESULTS-I 5.0's amount check)
        # step 11c: the amount on the 5 s-smoothed rho and I_g (RESULTS-I 5.0's check is at the arm's lambda and I_g): row 3 D' = I_g_s D(I_g_s rho_s) - D(rho_s), rows 1 / 9 D(rho_s / (1 - phi - phi_irq)) - D(rho_s)
        D_s, _ = dget("p50_ms", rho_s)
        if row == 3 and Ig_s is not None:
            Dp, _ = dget("p50_ms", rho_s * Ig_s); amount = Ig_s * Dp - D_s; amount_src = "D'-D"
        elif row in (1, 9):
            De, _ = dget("p50_ms", rho_s / max(1e-6, 1.0 - (phi or 0.0) - (phi_irq or 0.0))); amount = De - D_s; amount_src = "D(rho_eff)-D"
        else: amount = None; amount_src = ""
        if G > 5 * fl and row != 6: row5 = 1
        else: row5 = 0
        rows[row] = rows.get(row, 0) + 1
        now_cpu = cpu_ns()
        fo.write(json.dumps(dict(t=w["t"], row=row, g5=row5, E=round(E, 4), Er=round(E_raw, 4), y=round(y, 3), lat=round(lat, 4), rho=round(rho, 4), D=round(Dv, 4), past=int(past),
                                 phi=(round(phi, 4) if phi is not None else None), holder=holder, Rf=(round(Rf, 4) if Rf is not None else None), Ig=(round(Ig, 3) if Ig is not None else None),
                                 Q=round(Q, 4), G=round(G, 4), slot=round(slot, 3), n=nn, a=w["a"], Rd=(round(Rd, 4) if Rd is not None else None), cpu_ns=now_cpu - last_cpu,
                                 sub=sub, nb_mbm=(round(nb_mbm, 3) if nb_mbm is not None else None), thr=round(thr, 4), sirq=(round(sirq, 3) if sirq is not None else None), phi_irq=(round(phi_irq, 4) if phi_irq is not None else None), Ig_raw=(round(Ig_raw, 3) if Ig_raw is not None else None), Ig_s=(round(Ig_s, 4) if Ig_s is not None else None), rho_eff=round(rho_eff, 4), past_eff=int(past_eff), amount=(round(amount, 4) if amount is not None else None), amount_src=amount_src, target=("irq-affinity" if row == 9 else (holder if row == 1 else (sub or "service") if row == 3 else ""))), separators=(",", ":")) + "\n")
        last_cpu = now_cpu
        if n % 10 == 0: fo.flush()
    fo.flush()
    if a.secs and time.time() - t_start >= a.secs: break
fo.write(json.dumps(dict(event="end", t=int(time.time() * 1000), windows=n, rows=rows, cpu_ns=cpu_ns())) + "\n")
fo.close()
