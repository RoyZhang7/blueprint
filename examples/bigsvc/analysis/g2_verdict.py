#!/usr/bin/env python3
"""Phase G section 2.4 / 2.5: the verdict rows (PHASEG-HANDOFF section 0) per window, against twin truth.

Inputs per scenario: the stressed run's b4_compare output (b4_windows.csv: per second t, lat_ms, D_mix_ms, ext_mix,
y, truth_ext, step, lambda, rho, avgS_ms = cbar) and its g2_probe output (g2/g2_windows.csv: per 100 ms, R_*, Q_*,
G_ms, U, preempt), the twin's g2 windows (thresholds), the service dictionary (g(rho)) and the cpuset size.

Per second: E = ext_mix (the Phase F residual share, smoothed 5 s), R_foreign / R_self / R_other ms per second,
I_g = cores busy / (lambda cbar g(rho) / 1000), Q = Q_mutex (Go) or Q_futex (C++) ms per second, G = GC CPU ms per
second, slot_busy = cores busy / slots. Thresholds from the TWIN's served seconds: "~" = within its per-step IQR
(median over steps), ">>" = beyond 3x; R_foreign's scale is the twin's R_self + R_other IQR (the natural run-queue noise).
Rows: 1 R_foreign >> 0 -> run-queue contention (aggressor cgroup); 5 G >> 0 -> GC; 2 E >> 0, R_foreign ~ 0, I ~ 1, Q ~ 0, slot busy ~ 1
-> own overload; 3 E >> 0, R_foreign ~ 0, I >> 1 -> service-time inflation; 4 E >> 0, R_foreign ~ 0, I ~ 1, Q >> 0 ->
lock wait; 6 E ~ 0 -> nothing to explain; 7 E >> 0 and none of the above -> unexplained (reported).
Truth per second from the twin: ext (truth_ext >= 0.5) | slow-int (y >= 0.5) | fast. Served steps only.

usage: g2_verdict.py --b4 STRESSED/b4g2/b4_windows.csv --g2 STRESSED/g2/g2_windows.csv --twin-g2 TWIN/g2/g2_windows.csv
                     --dict SWEEP/service_dict.json --k 4 [--slots 3] [--go] [--label NAME] [--out DIR] [--e-thresh 0.5]
"""
import argparse, csv, json, os, sys
import numpy as np


def f(x):
    try: return float(x)
    except Exception: return np.nan


def per_second(g2rows):
    S = {}
    for r in g2rows:
        s = int(f(r["t"]) // 1000); d = S.setdefault(s, dict(n=0, Rf=0.0, Rs=0.0, Ro=0.0, Qf=0.0, Qm=[], G=[], U=[], pre=0.0, thr=0.0))
        d["n"] += 1; d["Rf"] += f(r["R_foreign_ms"]) or 0; d["Rs"] += f(r["R_self_ms"]) or 0; d["Ro"] += f(r["R_other_ms"]) or 0
        d["Qf"] += f(r["Q_futex_ms"]) or 0; d["Qm"].append(f(r["Q_mutex_ms"])); d["G"].append(f(r["G_ms"])); d["U"].append(f(r["U"])); d["pre"] += f(r["preempt"]) or 0
        d["Of"] = d.get("Of", 0.0) + (f(r.get("occ_foreign_ms", "0")) or 0); d["Os"] = d.get("Os", 0.0) + (f(r.get("occ_self_ms", "0")) or 0)
        d["Oany"] = d.get("Oany", False) or (r.get("occ_self_ms", "") not in ("", "0", "0.0") and f(r.get("occ_self_ms", "0")) > 0)
        d["Sf"] = d.get("Sf", 0.0) + (f(r.get("occ_sib_foreign_ms", "0")) or 0); d["Sany"] = d.get("Sany", False) or (f(r.get("occ_sib_idle_ms", "0")) or 0) > 0 or (f(r.get("occ_sib_foreign_ms", "0")) or 0) > 0   # v4 (a)
        d["thr"] += f(r.get("nr_throttled", "0")) or 0
    out = {}
    for s, d in S.items():
        out[s] = dict(Rf=d["Rf"], Rs=d["Rs"], Ro=d["Ro"], Qf=d["Qf"], Qm=float(np.nansum(d["Qm"])), G=float(np.nansum(d["G"])), U=float(np.nanmean(d["U"])), pre=d["pre"], thr=d["thr"], n=d["n"], Of=d.get("Of", 0.0), Os=d.get("Os", 0.0), Oany=d.get("Oany", False), Sf=d.get("Sf", 0.0), Sany=d.get("Sany", False))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--b4", required=True); ap.add_argument("--g2", required=True); ap.add_argument("--twin-g2", required=True); ap.add_argument("--twin-b4", default=None)
    ap.add_argument("--dict", required=True); ap.add_argument("--k", type=int, required=True); ap.add_argument("--slots", type=int, default=None)
    ap.add_argument("--go", action="store_true", help="Go service: Q = rt mutex wait; else Q = probe futex sleep (C++)")
    ap.add_argument("--label", default="scenario"); ap.add_argument("--out", default=None); ap.add_argument("--e-thresh", type=float, default=None, help="fixed E threshold (e.g. 0.5, the Phase F cell rule); default: twin median + max(3 IQR, 0.15)")
    ap.add_argument("--floor-ms", type=float, default=10.0, help="absolute floor (ms per second) under which R_foreign and Q count as ~0 whatever the twin IQR; G uses 5x it")
    ap.add_argument("--phi-far", type=float, default=0.05, help="C.1: phi >> 0 when the aggressor cgroups hold more than this share of the cpuset's CPU time (the twin's phi is 0)")
    ap.add_argument("--aggr-name", default="gordion-aggr", help="name of the aggressor cgroup for holder_cgroup")
    ap.add_argument("--phi-fallback", action="store_true", help="no occupancy fields (probe < v3): trigger row 1 on R_foreign >> 0 or R_self >> R_self_twin (C.1 fallback)")
    ap.add_argument("--twin-steps", default=None, help="comma list of the twin's steps to take the thresholds from (default all; use the served ones)")
    ap.add_argument("--baseline", default=None, help="JSON {step: p50_ms} of the unstressed run; seconds whose b4 truth is undefined but whose step is listed get truth = (lat - p50_step) / lat (served-by-target steps the caller/server rule dropped)")
    a = ap.parse_args(); slots = a.slots or max(1, a.k - 1)
    D = json.load(open(a.dict)); gm = np.array(D["rho"]); gv = np.array([np.nan if v is None else v for v in (D.get("g") or [np.nan] * len(gm))], float)
    ok = np.isfinite(gv); g_at = (lambda r: float(np.interp(r, gm[ok], gv[ok]))) if ok.any() else (lambda r: 1.0)
    b4 = list(csv.DictReader(open(a.b4))); g2 = per_second(list(csv.DictReader(open(a.g2)))); tw = per_second(list(csv.DictReader(open(a.twin_g2))))
    twb4 = {int(f(r["t"])): r for r in csv.DictReader(open(a.twin_b4))} if a.twin_b4 else {}
    # thresholds from the twin: per-step IQR (median over steps) of Rs+Ro, Q, G; I_g band from the twin's I_g
    def twin_series():
        rows = []
        keep = set(int(x) for x in a.twin_steps.split(",")) if a.twin_steps else None
        for s, d in tw.items():
            r = twb4.get(s)
            if keep is not None and (r is None or r.get("step") in (None, "") or int(f(r["step"])) not in keep): continue
            lam = f(r["lambda"]) if r else np.nan; cb = f(r["avgS_ms"]) if r else np.nan; rho = f(r["rho"]) / a.k if r else np.nan
            exp_cores = lam * cb * g_at(rho) / 1000 if r and np.isfinite(lam) else np.nan
            ls_ = f(r.get("lat_s_ms", "nan")) if r else np.nan; ls_ = ls_ if np.isfinite(ls_) else (f(r["lat_ms"]) if r else np.nan); Dm = f(r["D_mix_ms"]) if r else np.nan
            Etw = min(1.0, max(0.0, (ls_ - Dm) / ls_)) if np.isfinite(ls_) and np.isfinite(Dm) and ls_ > 0 else np.nan
            rows.append(dict(step=int(f(r["step"])) if r and r.get("step") not in (None, "") else 0, Rn=d["Ro"], Rs=d["Rs"], Q=(d["Qm"] if a.go else d["Qf"]), G=d["G"], E=Etw, Ig=(d["U"] * a.k / exp_cores) if exp_cores and np.isfinite(exp_cores) else np.nan))
        return rows
    T = twin_series()
    def band(key):
        steps = sorted(set(r["step"] for r in T)); iq = []
        for st in steps:
            v = np.array([r[key] for r in T if r["step"] == st and np.isfinite(r[key])])
            if len(v) >= 5: iq.append(np.percentile(v, 75) - np.percentile(v, 25))
        v = np.array([r[key] for r in T if np.isfinite(r[key])])
        return (float(np.median(iq)) if iq else np.nan), (float(np.median(v)) if len(v) else np.nan)
    iqR, medR = band("Rn"); iqQ, medQ = band("Q"); iqG, medG = band("G"); iqI, medI = band("Ig"); iqE, medE = band("E"); iqRs, medRs = band("Rs")
    # R_foreign's noise scale: the twin's run-queue delay behind foreign-class holders (root + other: kernel threads, daemons), per-step IQR,
    # floored at floor_ms x k per second (1 % of the cpuset's time at the default); R_self's own band is kept for the crowding reading
    R_far_t = max(3 * (iqR if np.isfinite(iqR) else 0.0), a.floor_ms * a.k); R_ok_t = max((iqR if np.isfinite(iqR) else 0.0), a.floor_ms * a.k)
    # E: ">>" beyond 3x the twin's per-step IQR of the residual share with a floor of 0.15 (the Phase F decomposition tolerance); --e-thresh overrides
    E_far_t = a.e_thresh if a.e_thresh is not None else (medE if np.isfinite(medE) else 0.0) + max(3 * (iqE if np.isfinite(iqE) else 0.0), 0.15)
    E_ok_t = a.e_thresh / 2 if a.e_thresh is not None else (medE if np.isfinite(medE) else 0.0) + max((iqE if np.isfinite(iqE) else 0.0), 0.15) / 1.0
    thr = dict(R_foreign_noise_iqr=iqR, R_far=R_far_t, R_ok=R_ok_t, R_self_twin_med=medRs, R_self_twin_iqr=iqRs, Q_iqr=iqQ, Q_med=medQ, G_iqr=iqG, G_med=medG, I_iqr=iqI, I_med=medI, E_iqr=iqE, E_med=medE, E_far=E_far_t, E_ok=E_ok_t)
    print(f"{a.label}: thresholds from the twin -> foreign-class R noise IQR {iqR:.2f} ms/s (R_foreign >> beyond {R_far_t:.2f}, ~ below {R_ok_t:.2f}); R_self twin median {medRs:.1f} IQR {iqRs:.1f}; Q median {medQ:.3f} IQR {iqQ:.3f}; G median {medG:.3f} IQR {iqG:.3f}; I_g twin median {medI:.3f} IQR {iqI:.3f}; E median {medE:.3f} IQR {iqE:.3f} -> E >> beyond {E_far_t:.3f}, ~ below {E_ok_t:.3f}")
    base = {str(k): v for k, v in json.load(open(a.baseline)).items()} if a.baseline else {}
    rows = []
    for r in b4:
        s = int(f(r["t"])); d = g2.get(s)
        if d is None: continue
        truth_src = "twin"
        if not np.isfinite(f(r["truth_ext"])):
            if r.get("step") in base and np.isfinite(f(r["lat_ms"])):
                r = dict(r); r["truth_ext"] = str(min(1.0, max(0.0, (f(r["lat_ms"]) - base[r["step"]]) / f(r["lat_ms"])))); truth_src = "step-baseline (served by target)"
            else:
                continue
        lam = f(r["lambda"]); cb = f(r["avgS_ms"]); rho = f(r["rho"]) / a.k
        exp_cores = lam * cb * g_at(rho) / 1000; Ig = d["U"] * a.k / exp_cores if exp_cores else np.nan
        Q = d["Qm"] if a.go else d["Qf"]; y = f(r["y"]); slot = d["U"] * a.k / slots
        # E = the residual share, unguarded (the b4 ext_mix column is NaN where the Phase F guards fired; row 2 lives exactly there)
        ls_ = f(r.get("lat_s_ms", "nan")); ls_ = ls_ if np.isfinite(ls_) else f(r["lat_ms"]); Dm = f(r["D_mix_ms"])
        E = min(1.0, max(0.0, (ls_ - Dm) / ls_)) if np.isfinite(ls_) and np.isfinite(Dm) and ls_ > 0 else f(r["ext_mix"])
        # ">>" = beyond 3x the twin's per-step IQR AND above an absolute floor (a zero IQR must not make every burst ">>"):
        # R_foreign 10 ms per second (1 % of one CPU), Q 10 ms/s, G 50 ms/s of CPU (5 % of a core)
        Rf_far = d["Rf"] > R_far_t; Rf_ok = d["Rf"] <= R_ok_t
        occ_share = d["Of"] / (1000.0 * a.k) if d["Of"] else 0.0            # v3 probe: share of the cpuset's time held by the aggressor cgroup in this second
        phi_sib = d["Sf"] / (1000.0 * a.k) if d.get("Sany") else float("nan")   # v4 (a): the same share on the cpuset's SMT siblings (NaN before v4 / without --sibling-cpuset)
        Rs_far = d["Rs"] > medRs + max(3 * iqRs, a.floor_ms * a.k)          # crowding: own-thread queueing far above the twin's
        I_far = Ig > medI + max(3 * iqI, 0.05); I_ok = Ig <= medI + max(iqI, 0.05)   # the aggressor lowers I_g on Go services: below the band counts as ~1
        Q_far = Q > medQ + max(3 * iqQ, a.floor_ms); Q_ok = Q <= medQ + max(iqQ, a.floor_ms)
        G_far = d["G"] > medG + max(3 * iqG, 5 * a.floor_ms)
        E_far = E >= E_far_t; E_ok = E < E_ok_t
        slow = y >= 0.5                      # the slowness axis first (Phase F): the origin rows 2/3/4/7 are read on slow windows only
        # section 0 rule (kept as row_v0)
        if Rf_far: row_v0 = 1
        elif G_far: row_v0 = 5
        elif not slow or not E_far: row_v0 = 6
        elif Rf_ok and I_far: row_v0 = 3
        elif Rf_ok and I_ok and Q_far: row_v0 = 4
        elif Rf_ok and I_ok and slot >= 0.9: row_v0 = 2
        else: row_v0 = 7
        # Addendum C.1: phi = aggressor cgroups' share of the cpuset's CPU time this second (probe v3); fallback where the probe has no O* fields
        has_occ = d.get("Oany", False)
        phi = occ_share if has_occ else float("nan")
        if has_occ:
            phi_far = phi > a.phi_far; phi_ok = phi <= a.phi_far
        elif a.phi_fallback:
            phi_far = Rf_far or Rs_far; phi_ok = not phi_far
        else:
            phi_far = False; phi_ok = True        # no aggressor cgroup in the run and no fields: phi is 0 by construction
        why = []
        if not slow or not E_far: row = 6
        elif phi_far: row = 1
        elif phi_ok and I_far: row = 3
        elif phi_ok and I_ok and Q_far: row = 4
        elif phi_ok and I_ok and Q_ok and slot >= 0.9: row = 2
        else:
            row = 7
            if not phi_ok: why.append("phi between")
            if not I_ok and not I_far: why.append("I_g between")
            if not Q_ok and not Q_far: why.append("Q between")
            if phi_ok and I_ok and Q_ok and slot < 0.9: why.append(f"slot busy {slot:.2f} < 0.9")
        holder = ""
        if phi_far: holder = a.aggr_name if has_occ else ("aggressor (R_foreign)" if Rf_far else "self (crowding)")
        elif Rf_far: holder = a.aggr_name + " (R_foreign only)"
        row_occ = row
        # Addendum E.4 (a), proposed rule for the SMT-sibling case: the aggressor holds the siblings, not the cpuset (phi ~ 0,
        # phi_sibling >> 0), and the service's CPU per request is inflated (I_g >> 1): service-time inflation by the sibling's
        # holder -> row 3 with the holder named; everything else as the C.1 rule.
        sib_far = np.isfinite(phi_sib) and phi_sib > a.phi_far
        if slow and E_far and phi_ok and sib_far and I_far: row_e4 = 3; holder_sib = a.aggr_name + " (SMT sibling)"
        else: row_e4 = row; holder_sib = ""
        truth = "ext" if f(r["truth_ext"]) >= 0.5 else ("slow-int" if y >= 0.5 else "fast")
        rows.append(dict(t=s, step=r.get("step"), lam=lam, rho=rho, lat_ms=f(r["lat_ms"]), D_mix_ms=f(r["D_mix_ms"]), E=E, E_guarded=f(r["ext_mix"]), y=y, truth_ext=f(r["truth_ext"]), truth=truth,
                         R_foreign_ms=d["Rf"], R_self_ms=d["Rs"], R_other_ms=d["Ro"], I_g=Ig, Q_ms=Q, G_ms=d["G"], slot_busy=slot, preempt=d["pre"], occ_foreign_share=occ_share, phi_cpuset=phi, holder_cgroup=holder, phi_sibling=phi_sib, row_e4=row_e4, holder_sibling=holder_sib,
                         R_self_far=int(Rs_far), G_far=int(G_far), row=row, row_v0=row_v0, why7="; ".join(why), cell_mix=r.get("cell_mix", ""), truth_src=truth_src,
                         cell=("slow" if slow else "fast") + "/" + ("ext" if E >= 0.5 else "int"), truth_cell=("slow" if slow else "fast") + "/" + ("ext" if f(r["truth_ext"]) >= 0.5 else "int")))
    out = a.out or os.path.join(os.path.dirname(os.path.dirname(a.b4)), "g2"); os.makedirs(out, exist_ok=True)
    with open(os.path.join(out, "verdict.csv"), "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0].keys())); w.writeheader(); w.writerows(rows)
    json.dump(thr, open(os.path.join(out, "verdict_thresholds.json"), "w"), indent=1)
    names = {1: "1 run-queue contention", 2: "2 own overload", 3: "3 service-time inflation (I_g>>1)", 4: "4 lock wait", 5: "5 GC", 6: "6 nothing to explain", 7: "7 no verdict"}
    truths = ["ext", "slow-int", "fast"]
    print(f"{a.label}: {len(rows)} seconds scored (served steps with twin truth); phi {'from probe v3 occupancy' if any(np.isfinite(r['phi_cpuset']) for r in rows) else ('FALLBACK R_foreign/R_self' if a.phi_fallback else '0 by construction (no aggressor cgroup, no O* fields)')}")
    for lab_, key in (("C.1 rule (row 1 = E >> 0 and phi >> 0)", "row"), ("section 0 rule (row 1 = R_foreign >> 0)", "row_v0")):
        print(f"-- {lab_}")
        print(f"   {'verdict row':30s} " + " ".join(f"{t:>9s}" for t in truths) + "   total")
        for k_ in sorted(names):
            c = [sum(1 for r in rows if r[key] == k_ and r["truth"] == t) for t in truths]
            if sum(c): print(f"   {names[k_]:30s} " + " ".join(f"{x:9d}" for x in c) + f"   {sum(c)}")
        print(f"   {'total':30s} " + " ".join(f"{sum(1 for r in rows if r['truth'] == t):9d}" for t in truths))
        ok = sum(1 for r in rows if (r["truth"] == "ext" and r[key] in (1, 3)) or (r["truth"] == "slow-int" and r[key] in (2, 4, 5, 6, 7)) or (r["truth"] == "fast" and r[key] == 6))
        print(f"   agreement with the truth class (ext -> 1/3, slow-int -> 2/4/5/6/7, fast -> 6): {100*ok/len(rows):.1f} %  ({ok} of {len(rows)})")
    gc = sum(r["G_far"] for r in rows); print(f"G >> 0 alongside: {gc} seconds")
    # cells (Phase F): slow x ext with %ext = E, against the truth cell
    cc = sum(1 for r in rows if r["cell"] == r["truth_cell"]); print(f"cells (y >= 0.5 x E >= 0.5) against the truth cell: {100*cc/len(rows):.1f} % ({cc} of {len(rows)}); scorer cells: " + ", ".join(f"{k} {v}" for k, v in sorted(__import__('collections').Counter(r['cell'] for r in rows).items())) + "; truth cells: " + ", ".join(f"{k} {v}" for k, v in sorted(__import__('collections').Counter(r['truth_cell'] for r in rows).items())))
    w7 = [r["why7"] for r in rows if r["row"] == 7]
    if w7: print(f"row 7 reasons ({len(w7)}): " + "; ".join(f"{k} x{v}" for k, v in __import__('collections').Counter(w7).most_common()))
    if any(np.isfinite(r["phi_sibling"]) for r in rows):
        ps = np.array([r["phi_sibling"] for r in rows if np.isfinite(r["phi_sibling"])]); print(f"phi_sibling (v4): median {np.median(ps):.3f}, p10 {np.percentile(ps, 10):.3f}, p90 {np.percentile(ps, 90):.3f}; proposed sibling rule (row_e4) rows: " + ", ".join(f"{k} x{v}" for k, v in sorted(__import__('collections').Counter(r['row_e4'] for r in rows).items())) + f"; row_e4 != row on {sum(1 for r in rows if r['row_e4'] != r['row'])} s")
    if any(np.isfinite(r["phi_cpuset"]) for r in rows):
        ph = np.array([r["phi_cpuset"] for r in rows if np.isfinite(r["phi_cpuset"])]); print(f"phi_cpuset: median {np.median(ph):.3f}, p10 {np.percentile(ph, 10):.3f}, p90 {np.percentile(ph, 90):.3f}; holder_cgroup on row-1 seconds: " + ", ".join(f"{k} x{v}" for k, v in __import__('collections').Counter(r['holder_cgroup'] for r in rows if r['row'] == 1).items()))
    print(f"R_self far above the twin (crowding): {sum(r['R_self_far'] for r in rows)} of {len(rows)} seconds")
    # decomposition accuracy (Phase F statistic): E against the twin truth share
    err = np.array([r["E"] - r["truth_ext"] for r in rows]); slow_rows = [r for r in rows if r["y"] >= 0.5]
    err_s = np.array([r["E"] - r["truth_ext"] for r in slow_rows]) if slow_rows else np.array([np.nan])
    print(f"decomposition E vs truth: all seconds median {np.median(err):+.3f}, within +-0.15 on {100*np.mean(np.abs(err) <= 0.15):.0f} %; slow seconds ({len(slow_rows)}) median {np.nanmedian(err_s):+.3f}, within +-0.15 on {100*np.nanmean(np.abs(err_s) <= 0.15):.0f} %")
    # row-1 magnitude: on the seconds where row 1 fired, the truth share
    r1 = [r["truth_ext"] for r in rows if r["row"] == 1]
    if r1: print(f"row 1 seconds: truth share median {np.median(r1):.2f}, >= 0.5 on {100*np.mean(np.array(r1) >= 0.5):.0f} %")
    sig = lambda key: f"median {np.nanmedian([r[key] for r in rows]):.3f}, p90 {np.nanpercentile([r[key] for r in rows], 90):.3f}"
    print(f"signals over the scored seconds: R_foreign ms/s {sig('R_foreign_ms')}; R_self {sig('R_self_ms')}; R_other {sig('R_other_ms')}; I_g {sig('I_g')}; Q {sig('Q_ms')}; G {sig('G_ms')}; slot busy {sig('slot_busy')}; E {sig('E')}")
    print("wrote", os.path.join(out, "verdict.csv"))


if __name__ == "__main__":
    main()
