#!/usr/bin/env python3
"""Phase G section 2.9: the per-window sensitivity table over the scenarios, from each one's window_summary.json.

usage: g2_window_table.py --row "label|RUN/g2/window_summary.json" [--row ...] [--md out.md] [--csv out.csv]
"""
import argparse, csv, json


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--row", action="append", required=True); ap.add_argument("--md", default=None); ap.add_argument("--csv", default=None)
    a = ap.parse_args()
    hdr = ["scenario", "windows", "seconds", "median dt (ms)", "row = second's row", "flips per s (windows)", "flips per s (seconds)", "cells vs truth, windows", "cells vs truth, seconds",
           "class agreement, windows", "class agreement, seconds", "rows, windows (1/2/3/4/6/7)", "rows, seconds (1/2/3/4/6/7)"]
    lines = ["| " + " | ".join(hdr) + " |", "|" + "---|" * len(hdr)]; recs = []
    for spec in a.row:
        lab, path = spec.split("|", 1); S = json.load(open(path))
        rw = "/".join(str(S["rows_window"].get(str(k), 0)) for k in (1, 2, 3, 4, 6, 7)); rs = "/".join(str(S["rows_second"].get(str(k), 0)) for k in (1, 2, 3, 4, 6, 7))
        vals = [lab, S["n_windows"], S["n_seconds"], f"{S['dt_median_ms']:.0f}", f"{100*S['same_as_second']:.1f} %", f"{S['flip_rate_per_s']:.2f}", f"{S['flip_rate_sec_per_s']:.3f}",
                f"{100*S['cells_window']:.1f} %", f"{100*S['cells_second']:.1f} %", f"{100*S['class_window']:.1f} %", f"{100*S['class_second']:.1f} %", rw, rs]
        lines.append("| " + " | ".join(str(v) for v in vals) + " |"); recs.append(dict(zip(hdr, vals)))
    out = "\n".join(lines); print(out)
    if a.md: open(a.md, "w", encoding="utf-8").write(out + "\n")
    if a.csv:
        with open(a.csv, "w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=hdr); w.writeheader(); w.writerows(recs)


if __name__ == "__main__":
    main()
