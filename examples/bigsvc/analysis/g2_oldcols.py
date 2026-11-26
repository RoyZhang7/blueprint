#!/usr/bin/env python3
"""Phase G section 2.10: the old-method columns beside the verdict rules on the same served seconds of each scenario.

Per scenario, on the seconds of verdict.csv (served steps with a twin truth), against the truth cell held there:
  - occupancy rule (C.1) and section-0 rule: class agreement (ext -> rows 1/3, slow-int -> 2/4/5/6/7, fast -> 6);
  - Phase G cell (slow x ext with E from D[rho; mix], no guard): cells vs truth;
  - D(lambda) (Gordion Algorithm 1: ext_old / cell_old of b4_windows.csv, the old curve from the dictionary run's own
    default-mix sweep, Algorithm-1 smoothing as the per-second rows): cells vs truth; seconds whose lambda lies past
    the old curve's last profiled bin (np.interp holds D at the end) are counted as "clamped" and reported;
  - Phase F scorer (D[rho; mix] + rho guard + wait guard, cell_mix of b4_windows.csv): strict (n/a = wrong), when it
    speaks, abstained (with the guard split).

usage: g2_oldcols.py --row "label|RUN/g2/verdict.csv|RUN/b4g2/b4_windows.csv|RUN/g2/oldcurve.json" [--row ...] [--md out.md] [--csv out.csv]
"""
import argparse, collections, csv, json
import numpy as np


def agree(rows, key):
    return np.mean([(r["truth"] == "ext" and int(r[key]) in (1, 3)) or (r["truth"] == "slow-int" and int(r[key]) in (2, 4, 5, 6, 7)) or (r["truth"] == "fast" and int(r[key]) == 6) for r in rows])


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--row", action="append", required=True); ap.add_argument("--md", default=None); ap.add_argument("--csv", default=None)
    a = ap.parse_args()
    hdr = ["scenario", "n", "occupancy rule, class", "§0 rule, class", "Phase G cell (no guard)", "D(λ) cell", "D(λ) ext on ext-truth s", "D(λ) ext on int-truth s", "λ past the old curve",
           "Phase F strict", "Phase F when it speaks", "Phase F abstained (ρ / wait)"]
    lines = ["| " + " | ".join(hdr) + " |", "|" + "---|" * len(hdr)]; recs = []; notes = []
    for spec in a.row:
        lab, vpath, bpath, opath = spec.split("|"); V = list(csv.DictReader(open(vpath))); B = {int(float(r["t"])): r for r in csv.DictReader(open(bpath))}; O = json.load(open(opath))
        rows = [(v, B.get(int(float(v["t"])))) for v in V]; miss = sum(1 for _, b in rows if b is None); rows = [(v, b) for v, b in rows if b is not None]; n = len(rows)
        tc = [v["truth_cell"] for v, _ in rows]
        g_cell = np.mean([v["cell"] == c for (v, _), c in zip(rows, tc)])
        old_ok = [b["ext_old"] not in ("", "nan") and np.isfinite(float(b["ext_old"])) for _, b in rows]
        old_cell = np.mean([b["cell_old"] == c for (_, b), c, ok in zip(rows, tc, old_ok) if ok]) if any(old_ok) else np.nan
        ext_t = [b for (v, b), ok in zip(rows, old_ok) if ok and v["truth"] == "ext"]; int_t = [b for (v, b), ok in zip(rows, old_ok) if ok and v["truth"] != "ext"]
        old_ext_on_ext = np.mean([b["cell_old"].endswith("/ext") for b in ext_t]) if ext_t else np.nan; old_ext_on_int = np.mean([b["cell_old"].endswith("/ext") for b in int_t]) if int_t else np.nan
        lam_max = O["lam_max"] if "lam_max" in O else max(O["lam"]); clamped = sum(1 for v, _ in rows if float(v["lam"]) > lam_max)
        strict = np.mean([b["cell_mix"] == c for (_, b), c in zip(rows, tc)]); spoke = [(b, c) for (_, b), c in zip(rows, tc) if not b["cell_mix"].endswith("/n-a")]
        when = np.mean([b == c for b, c in [(b_["cell_mix"], c_) for b_, c_ in spoke]]) if spoke else np.nan; abst = 1 - len(spoke) / n
        gm = collections.Counter(b["guard_mix"] for _, b in rows if b["cell_mix"].endswith("/n-a"))
        vals = [lab, n, f"{100*agree([v for v, _ in rows], 'row'):.0f} %", f"{100*agree([v for v, _ in rows], 'row_v0'):.0f} %", f"{100*g_cell:.0f} %",
                (f"{100*old_cell:.0f} %" if np.isfinite(old_cell) else "n/a"), (f"{100*old_ext_on_ext:.0f} % ({len(ext_t)})" if ext_t else "— (0)"), (f"{100*old_ext_on_int:.0f} % ({len(int_t)})" if int_t else "— (0)"),
                f"{clamped} of {n}" + (f" (λ > {lam_max:.0f})" if clamped else ""), f"{100*strict:.0f} %", (f"{100*when:.0f} %" if np.isfinite(when) else "n/a"),
                f"{100*abst:.0f} % ({gm.get('rho', 0)} / {gm.get('wait', 0)})"]
        lines.append("| " + " | ".join(str(x) for x in vals) + " |"); recs.append(dict(zip(hdr, vals)))
        if miss or not all(old_ok): notes.append(f"{lab}: {miss} seconds without a b4 row, {sum(1 for ok in old_ok if not ok)} without D(lambda)")
    out = "\n".join(lines); print(out)
    for s in notes: print("NOTE", s)
    if a.md: open(a.md, "w", encoding="utf-8").write(out + "\n")
    if a.csv:
        with open(a.csv, "w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=hdr); w.writeheader(); w.writerows(recs)


if __name__ == "__main__":
    main()
