#!/usr/bin/env python3
"""Phase G section 2.11: the old methods per 100 ms window, on the windows, truth and conventions of section 2.9.

Per window w of window_verdict.csv (g2_window.py: raw pooled-p50 Lat_w, lambda_w, rho_w, y_w against the old curve's floor
step, E_w from D[rho; mix], the Phase F guard evaluated on the raw window):
  - D(lambda): D_old_w = interp(lambda_w) on the dictionary run's own curve (oldcurve.json, np.interp holds D past the
    last bin), ext_old_w = clamp01((Lat_w - D_old_w) / Lat_w), cell_old_w = slow(y_w >= 0.5) x ext(ext_old_w >= 0.5);
  - Phase F scorer: cell_mix_w = slow(y_w) x ("n-a" if the rho or wait guard fires on the window else ext(E_w >= 0.5)).
Truth cell held at the second; the per-second values are cell_old / cell_mix of b4_windows.csv on the same seconds
(the section 2.10 numbers), and each method's window-vs-second agreement is cell_w == cell_sec (n-a is a value).

usage: g2_oldcols_window.py --row "label|RUN/g2/window_verdict.csv|RUN/b4g2/b4_windows.csv|RUN/g2/oldcurve.json" [--row ...] [--md out.md] [--csv out.csv]
"""
import argparse, collections, csv, json
import numpy as np


def clamp01(x):
    return float(min(1.0, max(0.0, x)))


def pct(x):
    return f"{100*x:.0f} %" if np.isfinite(x) else "n/a"


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--row", action="append", required=True); ap.add_argument("--md", default=None); ap.add_argument("--csv", default=None)
    ap.add_argument("--y-thresh", type=float, default=0.5); ap.add_argument("--ext-thresh", type=float, default=0.5)
    a = ap.parse_args()
    hdr = ["scenario", "windows", "seconds", "D(λ) cell, windows", "D(λ) cell, seconds", "D(λ) window = second", "λ past the old curve, windows",
           "Phase F strict, windows", "Phase F strict, seconds", "Phase F when it speaks, windows", "Phase F when it speaks, seconds",
           "Phase F abstained, windows (ρ / wait)", "Phase F abstained, seconds (ρ / wait)", "Phase F window = second",
           "guarded windows of unguarded seconds (ρ / wait)", "unguarded windows of guarded seconds"]
    lines = ["| " + " | ".join(hdr) + " |", "|" + "---|" * len(hdr)]; recs = []
    for spec in a.row:
        lab, wpath, bpath, opath = spec.split("|"); W = list(csv.DictReader(open(wpath))); B = {int(float(r["t"])): r for r in csv.DictReader(open(bpath))}; O = json.load(open(opath))
        lam_c = np.array(O["lam"], float); D_c = np.array(O["D"], float); lam_max = float(lam_c.max())
        rows = [(w, B.get(int(w["sec"]))) for w in W]; rows = [(w, b) for w, b in rows if b is not None]; n = len(rows); secs = sorted(set(int(w["sec"]) for w, _ in rows))
        co_w = []; cm_w = []; co_s = []; cm_s = []; tc = []; gw = []; gs = []; clamped = 0; lat_ratio = []
        for w, b in rows:
            lat = float(w["lat_ms"]); lam = float(w["lam"]); y = float(w["y"]); E = float(w["E"]); g = w["guard"]
            slow = "slow" if y >= a.y_thresh else "fast"
            D_old = float(np.interp(lam, lam_c, D_c)); eo = clamp01((lat - D_old) / lat) if lat > 0 else 0.0
            co_w.append(slow + "/" + ("ext" if eo >= a.ext_thresh else "int")); clamped += lam > lam_max
            cm_w.append(slow + "/" + ("n-a" if g else ("ext" if E >= a.ext_thresh else "int")))
            co_s.append(b["cell_old"]); cm_s.append(b["cell_mix"]); tc.append(w["truth_cell"]); gw.append(g); gs.append(b["guard_mix"])
            if g == "wait" and not b["guard_mix"]: lat_ratio.append(lat / float(b["lat_s_ms"]))
        # per second (unweighted, the section 2.10 statistic) on the same seconds
        S = {s: B[s] for s in secs}; tcs = {int(w["sec"]): w["truth_cell"] for w, _ in rows}
        old_s = np.mean([S[s]["cell_old"] == tcs[s] for s in secs]); strict_s = np.mean([S[s]["cell_mix"] == tcs[s] for s in secs])
        sp_s = [s for s in secs if not S[s]["cell_mix"].endswith("/n-a")]; when_s = np.mean([S[s]["cell_mix"] == tcs[s] for s in sp_s]) if sp_s else np.nan
        ab_s = collections.Counter(S[s]["guard_mix"] for s in secs if S[s]["cell_mix"].endswith("/n-a"))
        # per window
        old_w = np.mean([c == t for c, t in zip(co_w, tc)]); strict_w = np.mean([c == t for c, t in zip(cm_w, tc)])
        sp_w = [(c, t) for c, t in zip(cm_w, tc) if not c.endswith("/n-a")]; when_w = np.mean([c == t for c, t in sp_w]) if sp_w else np.nan
        ab_w = collections.Counter(g for g, c in zip(gw, cm_w) if c.endswith("/n-a"))
        agree_old = np.mean([x == y_ for x, y_ in zip(co_w, co_s)]); agree_mix = np.mean([x == y_ for x, y_ in zip(cm_w, cm_s)])
        g_in_ung = collections.Counter(g for g, s in zip(gw, gs) if g and not s); ung_in_g = sum(1 for g, s in zip(gw, gs) if s and not g); n_gsec_w = sum(1 for s in gs if s)
        vals = [lab, n, len(secs), pct(old_w), pct(old_s), pct(agree_old), f"{clamped} of {n}" + (f" (λ > {lam_max:.0f})" if clamped else ""),
                pct(strict_w), pct(strict_s), pct(when_w), pct(when_s),
                f"{pct(1 - len(sp_w) / n)} ({ab_w.get('rho', 0)} / {ab_w.get('wait', 0)})", f"{pct(1 - len(sp_s) / len(secs))} ({ab_s.get('rho', 0)} / {ab_s.get('wait', 0)})", pct(agree_mix),
                f"{g_in_ung.get('rho', 0) + g_in_ung.get('wait', 0)} ({g_in_ung.get('rho', 0)} / {g_in_ung.get('wait', 0)})", f"{ung_in_g} of {n_gsec_w}"]
        lines.append("| " + " | ".join(str(x) for x in vals) + " |"); recs.append(dict(zip(hdr, vals)))
        if lat_ratio: print(f"NOTE {lab}: {len(lat_ratio)} wait-guarded windows in unguarded seconds, Lat_w / Lat_s (5 s-smoothed) median {np.median(lat_ratio):.2f}, p10 {np.percentile(lat_ratio, 10):.2f}, p90 {np.percentile(lat_ratio, 90):.2f}")
        cw = collections.Counter((s, g) for g, s in zip(gw, gs)); print(f"NOTE {lab}: guard (second -> window) {dict(sorted(cw.items()))}")
    out = "\n".join(lines); print(out)
    if a.md: open(a.md, "w", encoding="utf-8").write(out + "\n")
    if a.csv:
        with open(a.csv, "w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=hdr); w.writeheader(); w.writerows(recs)


if __name__ == "__main__":
    main()
