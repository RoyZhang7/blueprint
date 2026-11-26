#!/usr/bin/env python3
"""Phase G section 2.9: the Addendum C verdict rule on every recorder window (100 ms nominal) of a scenario, with the
per-second verdict's thresholds and NO Algorithm-1 smoothing; the truth is held at the second (verdict.csv).

Per window w (the recorder's window, dt = its measured length): Lat_w = pooled p50 of the window's server-side Lat_e
samples (processing stream, GORDION_LAT); lambda_w = arrivals / dt; rho_w from rho_series (the +-0.5 s centred mean of
dictionary.py, part of the rho estimate, not the 5 s causal Gaussian); D_mix_w = Fbar(mix_w) + h(rho_w) K(mix_w) from
the scenario's own service_dict.json; E_w = clamp01((Lat_w - D_mix_w) / Lat_w); y_w = clamp01(tanh(k (Lat_w - p) / sigma))
with p, sigma of the old curve's floor step (b4_compare's server_service_curve on the dictionary run). Probe signals from
g2_windows.csv scaled to ms per second (x 1000 / dt): R_foreign, R_self, R_other, Q (Go: rt mutex wait; C++: futex
sleep), G; phi_w = occ_foreign / (dt k); I_g = U k / (lambda_w cbar_w g(rho_w) / 1000); slot busy = U k / slots.
Rule (C.1 order, the thresholds of verdict_thresholds.json): 6 (not slow or E not >>) -> 1 (phi > 0.05) -> 3 (I_g >>)
-> 4 (Q >>) -> 2 (slot busy >= 0.9) -> 7. Row 5 (G) is not in the C.1 order and is not applied (as in g2_verdict.py).
Outputs <out>/window_verdict.csv (one row per scored window) and <out>/window_summary.json: share of windows whose row
equals the second's row, the row flip rate per second (window-to-window changes / scored seconds), cells vs the truth
cell at window level, and the per-second figures beside them.

usage: g2_window.py --run STRESSED --dict-run DICTRUN --dict-json STRESSED/b4g2/service_dict.json
                    --g2 STRESSED/g2/g2_windows.csv --verdict STRESSED/g2/verdict.csv
                    --thresholds STRESSED/g2/verdict_thresholds.json --k 4 [--slots 3] [--go] [--label L] [--out DIR]
"""
import argparse, collections, csv, json, os, sys
import numpy as np
sys.path.insert(0, os.path.dirname(__file__))
from analyze import load_run
from dictionary import EP_A, lat_samples, mix_stats, rho_series, avgS_of_window, settled_windows
from b4_compare import server_service_curve, clamp01


class DictJSON:
    """ServiceDict rebuilt from its saved JSON (the same object the per-second scorer stored): predict and guard only."""
    def __init__(self, path):
        D = json.load(open(path)); self.k = D["k"]; self.mids = np.array(D["rho"], float); self.h = np.array(D["h"], float)
        half = float(self.mids[1] - self.mids[0]) / 2 if len(self.mids) > 1 else 0.0
        self.rho_min = float(self.mids.min() - half); self.rho_max = float(self.mids.max() + half)
        self.fl = D["fl_ms"]; self.c_ms = D["c_ms"]; self.fbar = D.get("fbar", "mixture"); self.wmax = float(D["wmax_ms"]); self.wait_guard = bool(D.get("wait_guard"))
        self.flq = {e: np.array(q, float) for e, q in (D.get("floor_quantiles_ms") or {}).items()} or None
        g = D.get("g"); self.g = np.array([np.nan if v is None else v for v in g], float) if g else None

    def predict(self, rho, shares):
        m = mix_stats(shares, self.c_ms, self.fl, self.fbar, self.flq)
        return m["Fbar"] + float(np.interp(rho, self.mids, self.h)) * m["K"], m

    def g_at(self, rho):
        if self.g is None or not np.isfinite(self.g).any(): return 1.0
        m = np.isfinite(self.g); return float(np.interp(rho, self.mids[m], self.g[m]))

    def guard(self, rho, lat, Fbar):
        if rho < self.rho_min or rho > self.rho_max: return "rho"
        if self.wait_guard and lat - Fbar > self.wmax: return "wait"
        return ""


def f(x):
    try: return float(x)
    except Exception: return np.nan


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", required=True); ap.add_argument("--dict-run", required=True); ap.add_argument("--dict-json", required=True)
    ap.add_argument("--g2", required=True); ap.add_argument("--verdict", required=True); ap.add_argument("--thresholds", required=True)
    ap.add_argument("--k", type=int, required=True); ap.add_argument("--slots", type=int, default=None); ap.add_argument("--go", action="store_true")
    ap.add_argument("--label", default="scenario"); ap.add_argument("--out", default=None); ap.add_argument("--settle", type=float, default=5.0)
    ap.add_argument("--kk", type=float, default=0.07, help="Algorithm 1's k in y = tanh(k (Lat - p) / sigma)")
    ap.add_argument("--floor-ms", type=float, default=10.0); ap.add_argument("--phi-far", type=float, default=0.05)
    a = ap.parse_args(); k = a.k; slots = a.slots or max(1, k - 1)
    SD = DictJSON(a.dict_json); T = json.load(open(a.thresholds))
    R0 = load_run(a.dict_run); old = server_service_curve(R0, a.settle)
    R = load_run(a.run); W, step_of, _ = settled_windows(R, a.settle, settle_mix=0)
    rs = rho_series(W, SD.c_ms, 1.0)
    G2 = {int(f(r["t"])): r for r in csv.DictReader(open(a.g2))}
    has_occ = any(f(r.get("occ_self_ms", "0")) > 0 for r in G2.values())
    V = {int(f(r["t"])): r for r in csv.DictReader(open(a.verdict))}
    medI, iqI = T["I_med"], T["I_iqr"]; medQ, iqQ = T["Q_med"], T["Q_iqr"]; E_far_t, E_ok_t = T["E_far"], T["E_ok"]; R_far_t, R_ok_t = T["R_far"], T["R_ok"]
    rows = []
    for w, st, rho_k in zip(W, step_of, rs):
        s = w["t"] // 1000; v = V.get(s); g = G2.get(w["t"])
        if v is None or g is None: continue
        xs = [lat_samples(w, e)[0] for e in w["ep"]]; xs = [x for x in xs if len(x)]
        if not xs: continue
        lat = float(np.median(np.concatenate(xs)) / 1e6); dt = f(g["dt_ms"]) or w.get("w", 100); dt = dt if np.isfinite(dt) and dt > 0 else w.get("w", 100)
        shares = {e: v_[EP_A] for e, v_ in w["ep"].items()}; lam = w["a"] * 1000.0 / dt; u = rho_k / k; cb = avgS_of_window(w, SD.c_ms)
        D_mix, mx = SD.predict(u, shares); E = clamp01((lat - D_mix) / lat) if lat > 0 else np.nan
        y = clamp01(np.tanh(a.kk * (lat - old["p"]) / old["sigma"])); guard = SD.guard(u, lat, mx["Fbar"])
        sc = 1000.0 / dt
        Rf = (f(g["R_foreign_ms"]) or 0) * sc; Rs = (f(g["R_self_ms"]) or 0) * sc; Ro = (f(g["R_other_ms"]) or 0) * sc
        Q = ((f(g["Q_mutex_ms"]) or 0) if a.go else (f(g["Q_futex_ms"]) or 0)) * sc; Gm = (f(g["G_ms"]) or 0) * sc; U = f(g["U"])
        phi = (f(g.get("occ_foreign_ms", "0")) or 0) / (dt * k) if has_occ else np.nan
        exp_cores = lam * cb * SD.g_at(u) / 1000; Ig = U * k / exp_cores if exp_cores and np.isfinite(exp_cores) and exp_cores > 0 else np.nan; slot = U * k / slots
        I_far = Ig > medI + max(3 * iqI, 0.05); I_ok = Ig <= medI + max(iqI, 0.05)
        Q_far = Q > medQ + max(3 * iqQ, a.floor_ms); Q_ok = Q <= medQ + max(iqQ, a.floor_ms)
        E_far = E >= E_far_t; slow = y >= 0.5
        if has_occ: phi_far = phi > a.phi_far; phi_ok = phi <= a.phi_far
        else: phi_far = False; phi_ok = True
        if not slow or not E_far: row = 6
        elif phi_far: row = 1
        elif phi_ok and I_far: row = 3
        elif phi_ok and I_ok and Q_far: row = 4
        elif phi_ok and I_ok and Q_ok and slot >= 0.9: row = 2
        else: row = 7
        Rf_far = Rf > R_far_t; Rf_ok = Rf <= R_ok_t
        if Rf_far: row_v0 = 1
        elif not slow or not E_far: row_v0 = 6
        elif Rf_ok and I_far: row_v0 = 3
        elif Rf_ok and I_ok and Q_far: row_v0 = 4
        elif Rf_ok and I_ok and slot >= 0.9: row_v0 = 2
        else: row_v0 = 7
        cell = ("slow" if slow else "fast") + "/" + ("ext" if E >= 0.5 else "int")
        rows.append(dict(t_ms=w["t"], dt_ms=int(dt), sec=s, step=st, lam=round(lam, 1), rho=round(u, 4), lat_ms=round(lat, 4), D_mix_ms=round(D_mix, 4), E=round(E, 4), y=round(y, 4), guard=guard,
                         R_foreign_ms_s=round(Rf, 3), R_self_ms_s=round(Rs, 3), R_other_ms_s=round(Ro, 3), phi_cpuset=(round(phi, 4) if np.isfinite(phi) else ""), I_g=(round(Ig, 4) if np.isfinite(Ig) else ""),
                         Q_ms_s=round(Q, 3), G_ms_s=round(Gm, 3), slot_busy=round(slot, 4), row=row, row_v0=row_v0, cell=cell,
                         row_sec=int(v["row"]), row_v0_sec=int(v["row_v0"]), cell_sec=v["cell"], E_sec=round(f(v["E"]), 4), truth=v["truth"], truth_cell=v["truth_cell"], truth_ext=f(v["truth_ext"])))
    if not rows: sys.exit(f"{a.label}: no window scored")
    out = a.out or os.path.dirname(a.verdict); os.makedirs(out, exist_ok=True)
    with open(os.path.join(out, "window_verdict.csv"), "w", newline="") as fh:
        wr = csv.DictWriter(fh, fieldnames=list(rows[0].keys())); wr.writeheader(); wr.writerows(rows)
    json.dump(dict(lam=[float(x) for x in old["lam"]], D=[float(x) for x in old["D"]], p=old["p"], sigma=old["sigma"], source=os.path.basename(a.dict_run)), open(os.path.join(out, "oldcurve.json"), "w"), indent=1)
    # statistics
    n = len(rows); secs = sorted(set(r["sec"] for r in rows)); dur = sum(r["dt_ms"] for r in rows) / 1000.0
    same = sum(1 for r in rows if r["row"] == r["row_sec"]); same0 = sum(1 for r in rows if r["row_v0"] == r["row_v0_sec"])
    flips = sum(1 for p_, q_ in zip(rows, rows[1:]) if q_["t_ms"] - p_["t_ms"] <= 2.5 * max(p_["dt_ms"], q_["dt_ms"]) and p_["row"] != q_["row"])
    pairs = sum(1 for p_, q_ in zip(rows, rows[1:]) if q_["t_ms"] - p_["t_ms"] <= 2.5 * max(p_["dt_ms"], q_["dt_ms"]))
    vs = [V[s] for s in secs]; sflips = sum(1 for p_, q_ in zip(vs, vs[1:]) if int(f(q_["t"])) - int(f(p_["t"])) <= 2 and int(p_["row"]) != int(q_["row"]))
    cells_w = sum(1 for r in rows if r["cell"] == r["truth_cell"]); cells_s = sum(1 for r in rows if r["cell_sec"] == r["truth_cell"])
    agree = lambda key: sum(1 for r in rows if (r["truth"] == "ext" and r[key] in (1, 3)) or (r["truth"] == "slow-int" and r[key] in (2, 4, 5, 6, 7)) or (r["truth"] == "fast" and r[key] == 6))
    rowdist = collections.Counter(r["row"] for r in rows); rowdist_s = collections.Counter(int(v["row"]) for v in vs)
    conf = collections.Counter((r["row_sec"], r["row"]) for r in rows)
    same_by_sec = {}
    for rid in sorted(rowdist_s):
        ws = [r for r in rows if r["row_sec"] == rid]; same_by_sec[rid] = (sum(1 for r in ws if r["row"] == rid), len(ws))
    summ = dict(label=a.label, n_windows=n, n_seconds=len(secs), dur_s=dur, dt_median_ms=float(np.median([r["dt_ms"] for r in rows])),
                same_as_second=same / n, same_as_second_v0=same0 / n, flips=flips, pairs=pairs, flip_rate_per_s=flips / dur, flips_sec=sflips, flip_rate_sec_per_s=sflips / len(secs),
                cells_window=cells_w / n, cells_second=cells_s / n, class_window=agree("row") / n, class_second=agree("row_sec") / n,
                rows_window={str(k_): v_ for k_, v_ in sorted(rowdist.items())}, rows_second={str(k_): v_ for k_, v_ in sorted(rowdist_s.items())},
                same_by_second_row={str(k_): v_ for k_, v_ in same_by_sec.items()}, confusion_sec_to_window={f"{k_[0]}->{k_[1]}": v_ for k_, v_ in sorted(conf.items())},
                has_occ=has_occ, guard_windows=collections.Counter(r["guard"] for r in rows), E_window_median=float(np.median([r["E"] for r in rows])), E_second_median=float(np.median([r["E_sec"] for r in rows])),
                old_lam_max=float(max(old["lam"])), old_p=old["p"], old_sigma=old["sigma"])
    json.dump(summ, open(os.path.join(out, "window_summary.json"), "w"), indent=1)
    print(f"{a.label}: {n} windows (median {summ['dt_median_ms']:.0f} ms) in {len(secs)} served seconds; row == second's row on {100*same/n:.1f} % (section-0 rule {100*same0/n:.1f} %); "
          f"flips {flips} over {dur:.0f} s = {flips/dur:.2f} per s (per-second verdict: {sflips} over {len(secs)} s = {sflips/len(secs):.3f} per s); cells vs truth at window level {100*cells_w/n:.1f} % (per second {100*cells_s/n:.1f} %); "
          f"class agreement window {100*agree('row')/n:.1f} % (second {100*agree('row_sec')/n:.1f} %); rows window {dict(rowdist)} vs second {dict(rowdist_s)}; same-by-second-row {same_by_sec}")
    print("wrote", os.path.join(out, "window_verdict.csv"))


if __name__ == "__main__":
    main()
