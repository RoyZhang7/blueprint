#!/usr/bin/env python3
"""c_e at operating load for a single-endpoint service (Phase F section 3 decision): the process CPU per request at the
step before the knee of the profiling sweep (knee = first step with served/target < 0.95, else the last step), from
survey.py's survey.csv; also the ratio to the lowest-step value (the wake-up effect that makes the per-request CPU
fall with load on the Go services). Usage: costs_from_survey.py RUN --endpoint NAME --out costs.json [--served-min 0.95]"""
import argparse, csv, json, os
import numpy as np


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("run"); ap.add_argument("--endpoint", required=True); ap.add_argument("--out", required=True)
    ap.add_argument("--served-min", type=float, default=0.95)
    a = ap.parse_args()
    rows = list(csv.DictReader(open(os.path.join(a.run, "survey.csv"))))
    bad = [i for i, r in enumerate(rows) if float(r["served"]) < a.served_min]
    knee = bad[0] if bad else len(rows) - 1
    op = rows[max(0, knee - 1)]
    c_op = float(op["cpu_req"]); c_low = float(rows[0]["cpu_req"])
    json.dump({"costs_ms": {a.endpoint: c_op}, "method": f"process CPU per request at step {op['step']} ({float(op['rate']):.0f} req/s target), the step before the knee (step {rows[knee]['step']}) of {os.path.basename(a.run)}",
               "lowest_step_ms": c_low, "ratio_op_over_lowest": c_op / c_low, "runs": [os.path.basename(a.run)]}, open(a.out, "w"), indent=1)
    print(f"{a.endpoint}: c_e(operating, step {op['step']}) = {c_op:.4f} ms; lowest step {c_low:.4f} ms; ratio {c_op / c_low:.2f}; wrote {a.out}")


if __name__ == "__main__":
    main()
