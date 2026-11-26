#!/usr/bin/env python3
"""Phase G G5.2 (Addendum D B4): the Go thread <-> request mapping. The recorder (image k123g5+) emits per window
"mchg":[n, changed] = requests completed and how many of them ended on another OS thread (M) than they began on. This
script reports, per load step and over time, the share of requests whose handler changed OS thread, which bounds the
error of attributing a thread's run-queue delay to the request it was serving.

usage: g5_mchg.py RUN [RUN ...]   (prints per step: requests, changed, share; and the per-window p50/p90 of the share)
"""
import sys, os, json
import numpy as np
sys.path.insert(0, os.path.dirname(__file__))
from analyze import load_run
from dictionary import assign


def main():
    for d in sys.argv[1:]:
        R = load_run(d); W = R["windows"]; st = assign(W, R["steps"], "step")
        if not any("mchg" in w for w in W): print(os.path.basename(d), ": no mchg field (image < k123g5)"); continue
        print(os.path.basename(d))
        keys = sorted({x for x in st if x is not None})
        groups = [(f"step {s}", [w for w, x in zip(W, st) if x == s and "mchg" in w]) for s in keys] if keys else []
        # ramps have no steps: split by 30 s segments
        if not groups:
            t0 = W[0]["t"]; segs = {}
            for w in W:
                if "mchg" in w: segs.setdefault(int((w["t"] - t0) / 30000), []).append(w)
            groups = [(f"{30*k}-{30*k+30} s", v) for k, v in sorted(segs.items())]
        for lab, ws in groups:
            n = sum(w["mchg"][0] for w in ws); c = sum(w["mchg"][1] for w in ws)
            sh = np.array([w["mchg"][1] / w["mchg"][0] for w in ws if w["mchg"][0] > 0])
            print(f"  {lab:12s}: requests {n:7d}, changed OS thread {c:7d} ({100*c/max(1,n):5.1f} %); per-window share p50 {100*np.median(sh) if len(sh) else float('nan'):5.1f} % p90 {100*np.percentile(sh, 90) if len(sh) else float('nan'):5.1f} %")


if __name__ == "__main__":
    main()
