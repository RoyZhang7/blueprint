#!/usr/bin/env python3
"""Phase G section 4 (G4, Addendum D.6): the Addendum C verdict rule in the scorer, applied to every row of the Phase F
results table. The wait guard is replaced by the verdict; the OOR guard (rho outside the profiled range) is kept.

Per row: b4_compare into <run>/b4g4 (current code: service dictionary with g(rho), truth as in Phase F: twin ramp or
step baseline), then per served second: E = (Lat_s - D[rho; mix]) / Lat_s unguarded, y, cores busy from the
recorder's process CPU, I_g = cores busy / (lambda cbar g(rho)), slot busy = cores busy / slots, Q = the Go recorder's
runtime wait (rt mutex wait if present, else the G-B scheduler-latency p90; none on C++), and the verdict row in the
Addendum C order with phi UNAVAILABLE (pre-probe runs: row 1 cannot fire, stated): 6 E ~ 0; 3 I_g >> 1; 4 Q >> 0;
2 slot busy >= thr; 7 otherwise. Seconds on unserved steps (truth undefined) read "saturation in front of the tap; no
verdict" (row 0) and are counted apart; OOR seconds abstain (row -1). Thresholds from the twin's seconds as in
g2_verdict.py. "Before" = Phase F's b4g scoring (wait guard on) recomputed from <run>/b4g/b4_windows.csv.

usage: g4_rescore.py --row "label|svc|run|dict|costs|truth|twin|k|slots|go" [--row ...] [--out results/g4]
                     [--smooth 5] [--slot-thresh 0.9] [--force]
   truth = ramp (twin ramp given in `twin`) | step (step baseline; twin = the dictionary sweep)
Writes <run>/b4g4/verdict.csv, <out>/g4_rows.csv and <out>/g4_table.md.
"""
import argparse, csv, json, os, subprocess, sys
import numpy as np
sys.path.insert(0, os.path.dirname(__file__))
from analyze import load_run

HERE = os.path.dirname(os.path.abspath(__file__))
NAMES = {-1: "OOR (abstain)", 0: "0 saturation; no verdict", 2: "2 own overload", 3: "3 service-time inflation", 4: "4 lock wait", 6: "6 nothing to explain", 7: "7 no verdict"}


def f(x):
    try: return float(x)
    except Exception: return np.nan


def b4(run, dict_, costs, svc, extra, out="b4g4", smooth=5, force=False):
    od = os.path.join(run, out)
    if not force and os.path.exists(os.path.join(od, "b4_windows.csv")): return od
    cmd = [sys.executable, os.path.join(HERE, "b4_compare.py"), run, "--dict", dict_, "--server", "--smooth-s", str(smooth), "--out", od] + (["--costs", costs] if costs else []) + extra
    env = dict(os.environ); env["GORDION_SVC"] = svc or ""
    if not svc: env.pop("GORDION_SVC", None)
    r = subprocess.run(cmd, capture_output=True, text=True, env=env)
    if r.returncode != 0: print("b4_compare failed:", run, r.stderr[-800:]); sys.exit(1)
    return od


def per_second(run, svc):
    """cores busy, Go runtime wait (rt mutex ms, sched p90 ms) per second from the recorder windows."""
    R = load_run(run, svc=svc or None); W = R["windows"]; S = {}
    for w in W:
        s = int(w["t"] // 1000); d = S.setdefault(s, dict(cpu=0.0, mw=0.0, sp90=[], n=0))
        d["cpu"] += (w.get("cpu") or 0) / 1e9; d["n"] += 1
        if w.get("rt"): d["mw"] += w["rt"][2] / 1e6
        if w.get("sched"): d["sp90"].append(w["sched"][1] / 1e6)
    has_rt = any(w.get("rt") for w in W); has_sched = any(w.get("sched") for w in W)
    return {s: dict(cores=d["cpu"], mw=d["mw"], sp90=(max(d["sp90"]) if d["sp90"] else 0.0)) for s, d in S.items()}, has_rt, has_sched


def series(b4rows, ps, k, slots, g_at, go, has_rt, has_sched):
    out = []
    for r in b4rows:
        s = int(f(r["t"])); d = ps.get(s)
        if d is None: continue
        lam = f(r["lambda"]); cb = f(r["avgS_ms"]); rho = f(r["rho"]) / k
        ls_ = f(r.get("lat_s_ms", "nan")); ls_ = ls_ if np.isfinite(ls_) else f(r["lat_ms"]); Dm = f(r["D_mix_ms"])
        E = min(1.0, max(0.0, (ls_ - Dm) / ls_)) if np.isfinite(ls_) and np.isfinite(Dm) and ls_ > 0 else np.nan
        exp_cores = lam * cb * g_at(rho) / 1000 if np.isfinite(lam) and np.isfinite(cb) else np.nan
        Ig = d["cores"] / exp_cores if exp_cores and np.isfinite(exp_cores) and exp_cores > 0 else np.nan
        Q = (d["mw"] if has_rt else (d["sp90"] if has_sched else 0.0)) if go else 0.0
        out.append(dict(t=s, step=r.get("step", ""), lam=lam, rho=rho, lat_ms=f(r["lat_ms"]), D_mix_ms=Dm, E=E, E_guarded=f(r.get("ext_mix", "nan")), ext_old=f(r.get("ext_old", "nan")),
                        y=f(r["y"]), truth_ext=f(r["truth_ext"]), guard=r.get("guard_mix", ""), usable=r.get("usable", "1"), cores=d["cores"], I_g=Ig, Q_ms=Q, slot_busy=d["cores"] / slots))
    return out


def band(T, key):
    steps = sorted(set(r["step"] for r in T)); iq = []
    for st in steps:
        v = np.array([r[key] for r in T if r["step"] == st and np.isfinite(r[key])])
        if len(v) >= 5: iq.append(np.percentile(v, 75) - np.percentile(v, 25))
    v = np.array([r[key] for r in T if np.isfinite(r[key])])
    return (float(np.median(iq)) if iq else np.nan), (float(np.median(v)) if len(v) else np.nan)


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--row", action="append", required=True); ap.add_argument("--out", default="results/g4")
    ap.add_argument("--smooth", type=int, default=5); ap.add_argument("--slot-thresh", type=float, default=0.9); ap.add_argument("--force", action="store_true"); ap.add_argument("--floor-ms", type=float, default=10.0)
    a = ap.parse_args(); os.makedirs(a.out, exist_ok=True); table = []; md = []
    for spec in a.row:
        lab, svc, run, dict_, costs, truth, twin, k, slots, go = [x.strip() for x in spec.split("|")]
        k = int(k); slots = int(slots); go = go == "go"
        extra = ["--truth-ramp", twin] if truth == "ramp" else ["--truth", run]
        od = b4(run, dict_, costs, svc, extra, smooth=a.smooth, force=a.force)
        twin_run = twin if truth == "ramp" else dict_
        tod = b4(twin_run, dict_, costs, svc, [], smooth=a.smooth, force=a.force)
        D = json.load(open(os.path.join(od, "service_dict.json"))); gm = np.array(D["rho"]); gv = np.array([np.nan if v is None else v for v in (D.get("g") or [np.nan] * len(gm))], float)
        ok = np.isfinite(gv); g_at = (lambda r: float(np.interp(r, gm[ok], gv[ok]))) if ok.any() else (lambda r: 1.0)
        ps, has_rt, has_sched = per_second(run, svc); tps, trt, tsched = per_second(twin_run, svc)
        rows = series(list(csv.DictReader(open(os.path.join(od, "b4_windows.csv")))), ps, k, slots, g_at, go, has_rt, has_sched)
        T = [r for r in series(list(csv.DictReader(open(os.path.join(tod, "b4_windows.csv")))), tps, k, slots, g_at, go, trt, tsched) if r["guard"] != "rho"]
        iqI, medI = band(T, "I_g"); iqQ, medQ = band(T, "Q_ms"); iqE, medE = band(T, "E")
        E_far_t = (medE if np.isfinite(medE) else 0.0) + max(3 * (iqE if np.isfinite(iqE) else 0.0), 0.15)
        I_far_t = medI + max(3 * iqI, 0.05); I_ok_t = medI + max(iqI, 0.05); Q_far_t = medQ + max(3 * iqQ, a.floor_ms); Q_ok_t = medQ + max(iqQ, a.floor_ms)
        qsrc = ("rt mutex wait" if has_rt else ("sched p90 (G-B)" if has_sched else "none")) if go else "none (C++)"
        # Phase F "before": the b4g scoring with the wait guard
        before = {}
        bg = os.path.join(run, "b4g", "b4_windows.csv")
        if os.path.exists(bg):
            for r in csv.DictReader(open(bg)): before[int(f(r["t"]))] = r
        out_rows = []; cnt = dict(served=0, oor=0, sat=0, cell_ok=0, cell_n=0, dec_ok=0, dec_n=0, bf_strict=0, bf_speak_ok=0, bf_speak_n=0, bf_abst=0, bf_n=0, sat_dlam_ext=0)
        per = {}
        for r in rows:
            slow = r["y"] >= 0.5; tdef = np.isfinite(r["truth_ext"])
            if r["guard"] == "rho": row = -1; cnt["oor"] += 1
            elif not tdef:
                row = 0; cnt["sat"] += 1
                if np.isfinite(r["ext_old"]) and r["ext_old"] >= 0.5: cnt["sat_dlam_ext"] += 1
            else:
                cnt["served"] += 1
                E_far = r["E"] >= E_far_t; I_far = r["I_g"] > I_far_t; I_ok = r["I_g"] <= I_ok_t; Q_far = r["Q_ms"] > Q_far_t; Q_ok = r["Q_ms"] <= Q_ok_t
                why = ""
                if not slow or not E_far: row = 6
                elif I_far: row = 3
                elif I_ok and Q_far: row = 4
                elif I_ok and Q_ok and r["slot_busy"] >= a.slot_thresh: row = 2
                else:
                    row = 7; why = "; ".join(w for w, c in (("I_g between", not I_ok and not I_far), ("Q between", not Q_ok and not Q_far), (f"slot busy {r['slot_busy']:.2f} < {a.slot_thresh}", I_ok and Q_ok and r["slot_busy"] < a.slot_thresh)) if c)
                r["why7"] = why
                truth = "ext" if r["truth_ext"] >= 0.5 else ("slow-int" if slow else "fast")
                cell = ("slow" if slow else "fast") + "/" + ("ext" if r["E"] >= 0.5 else "int"); tcell = ("slow" if slow else "fast") + "/" + ("ext" if r["truth_ext"] >= 0.5 else "int")
                r["truth"] = truth; r["cell"] = cell; r["truth_cell"] = tcell
                cnt["cell_n"] += 1; cnt["cell_ok"] += int(cell == tcell)
                if slow: cnt["dec_n"] += 1; cnt["dec_ok"] += int(abs(r["E"] - r["truth_ext"]) <= 0.15)
                per[(truth, row)] = per.get((truth, row), 0) + 1
                b = before.get(r["t"])
                if b is not None:
                    bc = b.get("cell_mix", ""); cnt["bf_n"] += 1
                    if bc.endswith("n-a"): cnt["bf_abst"] += 1
                    else:
                        cnt["bf_speak_n"] += 1; okb = (bc == tcell); cnt["bf_speak_ok"] += int(okb); cnt["bf_strict"] += int(okb)
            r["row"] = row; out_rows.append(r)
        with open(os.path.join(od, "verdict.csv"), "w", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=["t", "step", "lam", "rho", "lat_ms", "D_mix_ms", "E", "E_guarded", "ext_old", "y", "truth_ext", "truth", "guard", "cores", "I_g", "Q_ms", "slot_busy", "row", "why7", "cell", "truth_cell"], extrasaction="ignore"); w.writeheader(); w.writerows(out_rows)
        json.dump(dict(E_far=E_far_t, I_far=I_far_t, I_ok=I_ok_t, Q_far=Q_far_t, Q_ok=Q_ok_t, I_med=medI, I_iqr=iqI, E_med=medE, E_iqr=iqE, Q_src=qsrc, g_available=bool(ok.any()), slot_thresh=a.slot_thresh), open(os.path.join(od, "verdict_thresholds.json"), "w"), indent=1)
        n = cnt["served"]; truths = ["ext", "slow-int", "fast"]
        agree = sum(per.get((t, rw), 0) for t in truths for rw in ((3,) if t == "ext" else ((2, 4, 6, 7) if t == "slow-int" else (6,))))
        g4c = 100 * cnt["cell_ok"] / max(1, cnt["cell_n"]); dec = 100 * cnt["dec_ok"] / max(1, cnt["dec_n"])
        bf = (100 * cnt["bf_strict"] / cnt["bf_n"], 100 * cnt["bf_speak_ok"] / max(1, cnt["bf_speak_n"]), 100 * cnt["bf_abst"] / cnt["bf_n"]) if cnt["bf_n"] else (np.nan, np.nan, np.nan)
        dist = " · ".join(f"{t}: " + ", ".join(f"{per[(t, rw)]}→{rw}" for rw in sorted(set(rw for (tt, rw) in per if tt == t))) for t in truths if any(tt == t for (tt, _) in per))
        print(f"== {lab}: served {n} s, OOR {cnt['oor']}, saturation {cnt['sat']} (D(λ) said ext on {cnt['sat_dlam_ext']}); Phase F cells strict/speaks/abstained {bf[0]:.1f}/{bf[1]:.1f}/{bf[2]:.1f}; G4 cells {g4c:.1f} % (no wait guard, OOR kept); decomposition within ±0.15 on slow s {dec:.0f} % ({cnt['dec_n']}); verdict {dist}; pattern agreement {100*agree/max(1,n):.0f} %; Q source {qsrc}; g {'yes' if ok.any() else 'NO (I_g = I)'}; thresholds E≫{E_far_t:.2f} I_g≫{I_far_t:.2f} Q≫{Q_far_t:.1f} ms/s slot≥{a.slot_thresh}")
        table.append(dict(row=lab, served=n, oor=cnt["oor"], saturation=cnt["sat"], sat_dlam_ext=cnt["sat_dlam_ext"], f_strict=bf[0], f_speaks=bf[1], f_abst=bf[2], g4_cells=g4c, dec=dec, dec_n=cnt["dec_n"], agree=100 * agree / max(1, n), q_src=qsrc, **{f"{t}_to_{rw}": per.get((t, rw), 0) for t in truths for rw in (2, 3, 4, 6, 7)}))
        md.append(f"| {lab} | {n} | {cnt['oor']} | {cnt['sat']} ({cnt['sat_dlam_ext']}) | {bf[0]:.1f} / {bf[1]:.1f} / {bf[2]:.1f} | **{g4c:.1f}** | {dec:.0f} % ({cnt['dec_n']}) | {dist} | {100*agree/max(1,n):.0f} % | {qsrc} |")
    with open(os.path.join(a.out, "g4_rows.csv"), "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(table[0].keys())); w.writeheader(); w.writerows(table)
    hdr = "| row | served s | OOR | saturation: no verdict (D(λ) ext) | Phase F cells strict / speaks / abstained | G4 cells (OOR kept, wait guard off) | E within ±0.15 (slow s) | verdict rows per truth class | pattern agreement | Q source |\n|---|---|---|---|---|---|---|---|---|---|\n"
    open(os.path.join(a.out, "g4_table.md"), "w", encoding="utf-8").write(hdr + "\n".join(md) + "\n"); print("wrote", os.path.join(a.out, "g4_table.md"))


if __name__ == "__main__":
    main()
