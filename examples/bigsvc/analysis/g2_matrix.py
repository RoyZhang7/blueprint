#!/usr/bin/env python3
"""Phase G 2.5: the full confusion matrix under the C.1 rule (and the section-0 rule beside it) from the per-scenario
verdict.csv files, plus the cell accuracy and the decomposition accuracy per scenario.

usage: g2_matrix.py --row "label|verdict.csv" [--row ...] [--md out.md]
"""
import argparse, collections, csv
import numpy as np

NAMES = {1: "1 run-queue contention", 2: "2 own overload", 3: "3 service-time inflation", 4: "4 lock wait", 5: "5 GC", 6: "6 nothing to explain", 7: "7 no verdict"}
TRUTHS = ["ext", "slow-int", "fast"]


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--row", action="append", required=True); ap.add_argument("--md", default=None)
    a = ap.parse_args(); lines = []
    hdr = "| scenario | n | " + " | ".join(f"{t} → {NAMES[k].split()[0]}" for t in TRUTHS for k in (1, 2, 3, 4, 6, 7)) + " | class agreement | cells vs truth cell | E within ±0.15 (slow s) |"
    lines.append(hdr); lines.append("|" + "---|" * (hdr.count("|") - 1))
    for rule, key in (("C.1", "row"), ("§0", "row_v0")):
        lines.append(f"| **{rule} rule** |" + " |" * (hdr.count("|") - 2))
        for spec in a.row:
            lab, path = spec.split("|", 1); rows = list(csv.DictReader(open(path)))
            c = collections.Counter((int(r[key]), r["truth"]) for r in rows)
            ok = sum(1 for r in rows if (r["truth"] == "ext" and int(r[key]) in (1, 3)) or (r["truth"] == "slow-int" and int(r[key]) in (2, 4, 5, 6, 7)) or (r["truth"] == "fast" and int(r[key]) == 6))
            cells = np.mean([r["cell"] == r["truth_cell"] for r in rows]) if "cell" in rows[0] else np.nan
            slow = [r for r in rows if float(r["y"]) >= 0.5]; dec = np.mean([abs(float(r["E"]) - float(r["truth_ext"])) <= 0.15 for r in slow]) if slow else np.nan
            lines.append(f"| {lab} | {len(rows)} | " + " | ".join(str(c[(k, t)]) for t in TRUTHS for k in (1, 2, 3, 4, 6, 7)) + f" | {100*ok/len(rows):.0f} % | {100*cells:.0f} % | {100*dec:.0f} % ({len(slow)}) |")
    out = "\n".join(lines); print(out)
    if a.md: open(a.md, "w", encoding="utf-8").write(out + "\n")


if __name__ == "__main__":
    main()
