#!/usr/bin/env python3
"""Phase G section 4 (Addendum D.6): row 7 at the slot-busy margin. Over the G2 verdict files (C.1 rule), what a lower
slot-busy threshold does: the row-7 seconds whose only failed condition was "slot busy < 0.9" move to row 2 when their
slot busy clears the lower threshold; nothing else moves (rows 1/3/4 are tested first, row 6 does not depend on it).
Reports, per threshold, the conversions by truth class across all files.

usage: g4_slot.py --row "label|verdict.csv" [...] [--thresholds 0.9,0.85,0.8,0.75,0.7]
"""
import argparse, csv, re


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--row", action="append", required=True); ap.add_argument("--thresholds", default="0.9,0.85,0.8,0.75,0.7")
    a = ap.parse_args(); ths = [float(x) for x in a.thresholds.split(",")]
    print("| scenario | row-7 s (slot margin) | " + " | ".join(f"→ row 2 at slot ≥ {t:g} (ext / slow-int / fast)" for t in ths) + " |")
    print("|---|---|" + "---|" * len(ths))
    tot = {t: [0, 0, 0] for t in ths}; tot7 = 0
    for spec in a.row:
        lab, path = spec.split("|", 1); rows = list(csv.DictReader(open(path)))
        m7 = [r for r in rows if r["row"] == "7" and "slot busy" in r.get("why7", "")]
        tot7 += len(m7); cells = []
        for t in ths:
            c = [0, 0, 0]
            for r in m7:
                mm = re.search(r"slot busy ([0-9.]+)", r["why7"]); sb = float(mm.group(1)) if mm else float(r["slot_busy"])
                if sb >= t: c[["ext", "slow-int", "fast"].index(r["truth"])] += 1
            for i in range(3): tot[t][i] += c[i]
            cells.append(f"{c[0]} / {c[1]} / {c[2]}")
        print(f"| {lab} | {len(m7)} | " + " | ".join(cells) + " |")
    print(f"| **all** | {tot7} | " + " | ".join(f"{tot[t][0]} / {tot[t][1]} / {tot[t][2]}" for t in ths) + " |")


if __name__ == "__main__":
    main()
