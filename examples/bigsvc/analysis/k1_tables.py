#!/usr/bin/env python3
"""Phase K1: the per-arm table of RESULTS-K section 1 from results/k1/out/k1_wide.csv (one row per probe arm; the cell's
'none' baseline in the first columns). Differences are paired (arm - none, ABAB order), mean and 95 % t-interval over the
repetitions; '~' marks an interval that contains 0 (not distinguishable). Server-side latency and CPU per request at
the unique-id knee are left out: the recorder undercounts on the saturated core there (RESULTS-K 1.9), the client
columns are used instead.

usage: k1_tables.py [results/k1/out/k1_wide.csv] > results/k1/k1_table.md
"""
import csv, math, sys

W = list(csv.DictReader(open(sys.argv[1] if len(sys.argv) > 1 else "results/k1/out/k1_wide.csv")))
ORDER = {"a": 0, "b": 1, "c": 2, "d": 3, "e": 4}
VIC = {"syn": "synthetic (4)", "uid": "unique-id (1)", "ps": "post-storage (1)"}
ARM = {"bt:full_switch": "bt full, switch", "bt:full_wakeup": "bt full, wake-up", "bt:full_both": "bt full, both",
       "bt:full_both:nostats": "bt full, both, stats off", "bt:empty_both": "bt empty, both", "bt:v5": "bt v5 (both + export)",
       "lb:full": "libbpf full, both"}


def g(w, k):
    try:
        v = float(w.get(k, ""))
        return v if math.isfinite(v) else None
    except (TypeError, ValueError):
        return None


def num(v, n=3):
    return "–" if v is None else f"{v:.{n}f}"


def kev(v):
    return "–" if v is None else (f"{v / 1e3:.0f}k" if v >= 1e4 else f"{v / 1e3:.1f}k")


def dci(w, k, n=3, scale=1.0, pct_of=None):
    d, lo, hi = g(w, k), g(w, k + "_lo"), g(w, k + "_hi")
    if d is None: return "–"
    if pct_of:
        if not pct_of: return "–"
        scale = 100.0 / pct_of; n = 1
    if pct_of and d * scale > 1000:          # report a blow-up as a ratio to the baseline
        return f"×{1 + d / pct_of:.0f} [×{1 + lo / pct_of:.0f}, ×{1 + hi / pct_of:.0f}]"
    t = f"{d * scale:+.{n}f} [{lo * scale:+.{n}f}, {hi * scale:+.{n}f}]" if lo is not None and hi is not None else f"{d * scale:+.{n}f}"
    return ("~" if lo is not None and hi is not None and lo <= 0 <= hi else "") + t


def aggr(w):
    a = w["aggr"]
    if a in ("", "none", "-"): return "none"
    return f"{a} @{w['aggr_cpus']}"


CELLS = ["a-syn-idle", "a-syn-r02", "a-syn-r05", "a-syn-knee", "a-uid-idle", "a-uid-r02", "a-uid-r05", "a-uid-knee",
         "b-syn-hog1", "b-syn-hogk", "b-syn-duty20", "b-syn-switch", "b-syn-sib", "b-uid-hog1", "b-uid-duty20", "b-uid-switch", "b-uid-sib",
         "c-syn-noaggr", "c-syn-switch", "c-uid-noaggr", "c-uid-switch", "d-syn", "d-uid", "e-ps-r02", "e-ps-r02-hog", "e-ps-r05", "e-ps-r05-hog"]
cells = sorted({w["cell"] for w in W}, key=lambda c: (CELLS.index(c) if c in CELLS else 99, c))
print("| cell | victim (cores) | offered req/s | aggressor | arm | switches/s, wake-ups/s (all CPUs) | ns/event switch (+sibling prog) / wake-up | BPF cores | reader cores | Δ busy cores, node | Δ victim cores | Δ CPU/request % | Δ p99 server % | Δ p99 client % |")
print("|---|---|---|---|---|---|---|---|---|---|---|---|---|---|")
for c in cells:
    none = next(w for w in W if w["cell"] == c and w["arm"] == "none")
    knee_uid = c == "a-uid-knee"
    for w in sorted((w for w in W if w["cell"] == c and w["arm"] != "none"), key=lambda w: list(ARM).index(w["arm"]) if w["arm"] in ARM else 99):
        sw, sib, wk = g(w, "ns_ev:switch"), g(w, "ns_ev:switch_sib"), g(w, "ns_ev:wakeup")
        ns = "–" if sw is None and wk is None else f"{num(sw, 0)}{'' if sib is None else ' (+' + num(sib, 0) + ')'} / {num(wk, 0)}"
        if w["bpf_stats"] != "on": ns = "stats off"
        ev = f"{kev(g(w, 'ev_s:switch'))}, {kev(g(w, 'ev_s:wakeup'))}" if w["bpf_stats"] == "on" else f"({kev(g(none, 'rate_cs_all'))}, {kev(g(none, 'rate_wake_all'))})"
        cpr = "†" if knee_uid else dci(w, "d_cpu_per_req_ms", pct_of=g(none, "cpu_per_req_ms"))
        p99s = "†" if knee_uid else dci(w, "d_p99_ms", pct_of=g(none, "p99_ms"))
        p99c = dci(w, "d_client_lat_p99_ms", pct_of=g(none, "client_lat_p99_ms")) if g(none, "client_lat_p99_ms") else "–"
        print(f"| {c} | {VIC[w['victim']]} | {w['rate']} | {aggr(w)} | {ARM.get(w['arm'], w['arm'])} | {ev} | {ns} | {num(g(w, 'bpf_cores'))} | "
              f"{num(g(w, 'reader_cores'))} | {dci(w, 'd_busy_all_cores')} | {dci(w, 'd_victim_cores')} | {cpr} | {p99s} | {p99c} |")
