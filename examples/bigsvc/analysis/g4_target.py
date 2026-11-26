#!/usr/bin/env python3
"""Phase G section 4: the controller target. In every probe-v3 run with an aggressor, is the holder with the largest
share of phi (the foreign CPU time on the cpuset by class: aggressor cgroup / root / other) the aggressor cgroup, on the
seconds where phi >= 0.05? And on the row-1 seconds of the verdict, is holder_cgroup the aggressor? Reads g2_windows.csv
(occ_* columns) and verdict.csv.

usage: g4_target.py --row "label|<run>" [...]   (run dir holds g2/g2_windows.csv and g2/verdict.csv)
"""
import argparse, csv, os
import numpy as np


def f(x):
    try: return float(x)
    except Exception: return np.nan


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--row", action="append", required=True); ap.add_argument("--phi-far", type=float, default=0.05)
    a = ap.parse_args()
    print("| run | seconds with φ ≥ 0.05 | largest foreign holder = aggressor cgroup | root largest | other largest | row-1 seconds | holder_cgroup = aggressor on them |")
    print("|---|---|---|---|---|---|---|")
    for spec in a.row:
        lab, run = spec.split("|", 1); g2 = os.path.join(run, "g2", "g2_windows.csv"); vd = os.path.join(run, "g2", "verdict.csv")
        S = {}
        for r in csv.DictReader(open(g2)):
            s = int(f(r["t"]) // 1000); d = S.setdefault(s, [0.0, 0.0, 0.0, 0.0])
            d[0] += f(r.get("occ_foreign_ms", "0")) or 0; d[1] += f(r.get("occ_root_ms", "0")) or 0; d[2] += f(r.get("occ_other_ms", "0")) or 0; d[3] += f(r.get("occ_self_ms", "0")) or 0
        k = None
        try:
            import json; k = json.load(open(os.path.join(run, "meta.json"))).get("cpuset", "")
            k = len([c for part in str(k).split(",") for c in (range(int(part.split("-")[0]), int(part.split("-")[1]) + 1) if "-" in part else [part]) if str(c) != ""]) if k else None
        except Exception: k = None
        k = k or 1
        n = agg = root = oth = 0
        for s, (fo, ro, ot, se) in S.items():
            phi = fo / (1000.0 * k)
            if phi >= a.phi_far:
                n += 1; m = max(fo, ro, ot)
                if m == fo: agg += 1
                elif m == ro: root += 1
                else: oth += 1
        r1 = h = 0
        if os.path.exists(vd):
            for r in csv.DictReader(open(vd)):
                if r.get("row") == "1": r1 += 1; h += int(r.get("holder_cgroup", "").startswith("gordion-aggr"))
        print(f"| {lab} | {n} | {agg} | {root} | {oth} | {r1} | {h} |")


if __name__ == "__main__":
    main()
