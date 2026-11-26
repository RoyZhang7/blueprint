#!/usr/bin/env python3
"""Phase G Addendum E.4: the per-scenario table of a twin / stressed pair, per step of the schedule.

Per step: served (twin / stressed), server-side p50 (twin -> stressed, ratio), I_g (twin -> stressed; I = cores busy from
the cgroup / (lambda cbar), g from the uniform dictionary), phi and phi_sibling (stressed), R_self (twin -> stressed, ms per
s), the waker split of the stressed run (own-futex / idle, ms per s), the truth share and E (medians over the step's
served seconds of verdict.csv), the C.1 rows and the proposed sibling rule's rows, and the perf counters (victim IPC and
LLC misses per request twin -> stressed; aggressor-CPU IPC and LLC misses per s).

usage: e4_table.py --label L --twin TWIN --stressed ST --g-dict service_dict.json [--k 4] [--md out.md]
"""
import argparse, collections, csv, json, os
import numpy as np


def f(x):
    try: return float(x)
    except Exception: return np.nan


def per_step(path, key_step="step"):
    out = collections.defaultdict(list)
    if not os.path.exists(path): return out
    for r in csv.DictReader(open(path)):
        s = int(f(r.get(key_step, -1))) if r.get(key_step, "") not in ("", None) else -1
        if s >= 0: out[s].append(r)
    return out


def settled(rows):
    return rows[50:] if len(rows) > 60 else rows


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--label", required=True); ap.add_argument("--twin", required=True); ap.add_argument("--stressed", required=True)
    ap.add_argument("--g-dict", required=True); ap.add_argument("--k", type=int, default=4); ap.add_argument("--md", default=None)
    a = ap.parse_args(); k = a.k
    D = json.load(open(a.g_dict)); gm = np.array(D["rho"], float); gv = np.array([np.nan if v is None else v for v in D["g"]], float); ok = np.isfinite(gv)
    g_at = lambda u: float(np.interp(u, gm[ok], gv[ok]))
    curve = {}
    for tag, d in (("tw", a.twin), ("st", a.stressed)):
        curve[tag] = {int(f(r["step"])): r for r in csv.DictReader(open(os.path.join(d, "svc_curve.csv")))}
    g2 = {tag: per_step(os.path.join(d, "g2", "g2_windows.csv")) for tag, d in (("tw", a.twin), ("st", a.stressed))}
    pf = {tag: per_step(os.path.join(d, "g2", "perf_windows.csv")) for tag, d in (("tw", a.twin), ("st", a.stressed))}
    V = per_step(os.path.join(a.stressed, "g2", "verdict.csv"))
    hdr = ["step (req/s)", "served tw / st", "p50 tw → st (ms)", "I_g tw → st", "φ", "φ_sibling", "R_self tw → st (ms/s)", "W own-futex / idle (ms/s, st)", "truth ext (med)", "E (med)",
           "C.1 rows (1/2/3/4/6/7)", "sibling-rule rows", "victim IPC tw → st", "victim LLC miss/req tw → st", "aggr-CPU IPC st", "aggr-CPU LLC Mmiss/s tw → st"]
    lines = [f"**{a.label}** — twin `{os.path.basename(a.twin)}`, stressed `{os.path.basename(a.stressed)}`", "", "| " + " | ".join(hdr) + " |", "|" + "---|" * len(hdr)]
    def ig(tag, s):
        # the scorer's form per second (g2_verdict.py): I_g = mean(U) k / (lambda_s cbar g(rho)), with ONE g (the uniform
        # dictionary's) for both runs -- the verdict's own I_g column uses the pair's dictionary g, which absorbs the mix's inflation
        c = curve[tag].get(s); w = g2[tag].get(s, [])
        if not c or not w: return np.nan
        cb = f(c["avgS_ms"]); u = f(c["rho"]) / k; secs = collections.defaultdict(list)
        for r in w: secs[int(f(r["t"]) // 1000)].append(r)
        vals = []
        for t_, rs in secs.items():
            if len(rs) < 8: continue
            lam = sum(f(r["a"]) for r in rs) * 1000.0 / sum(f(r["dt_ms"]) for r in rs); U = np.nanmean([f(r["U"]) for r in rs])
            if lam > 0 and np.isfinite(U): vals.append(U * k / (lam * cb * g_at(u) / 1000))
        vals = vals[5:] if len(vals) > 10 else vals
        return float(np.nanmedian(vals)) if vals else np.nan
    def rate(tag, s, key):
        w = settled(g2[tag].get(s, []))
        return float(np.nansum([f(r[key]) for r in w]) / (sum(f(r["dt_ms"]) for r in w) / 1000.0)) if w else np.nan
    def share(tag, s, key):
        w = settled(g2[tag].get(s, []))
        return float(np.nansum([f(r[key]) for r in w]) / (k * sum(f(r["dt_ms"]) for r in w))) if w else np.nan
    def pmed(tag, s, key):
        w = settled(pf[tag].get(s, [])); v = [f(r[key]) for r in w]
        return float(np.nanmedian(v)) if w and np.isfinite(v).any() else np.nan
    def prate(tag, s, key):
        w = settled(pf[tag].get(s, []))
        return float(np.nansum([f(r[key]) for r in w]) / (sum(f(r["dt_ms"]) for r in w) / 1000.0)) if w else np.nan
    fmt = lambda x, p=2: ("—" if not np.isfinite(x) else f"{x:.{p}f}")
    for s in sorted(curve["tw"]):
        ct, cs = curve["tw"][s], curve["st"].get(s)
        if cs is None: continue
        vs = V.get(s, []); rows = collections.Counter(int(f(r["row"])) for r in vs); rows4 = collections.Counter(int(f(r["row_e4"])) for r in vs)
        rr = lambda C: "/".join(str(C.get(x, 0)) for x in (1, 2, 3, 4, 6, 7))
        p50t, p50s = f(ct["p50_ms"]), f(cs["p50_ms"])
        vals = [f"{s} ({int(f(ct['rate_target']))})", f"{f(ct['lambda'])/f(ct['rate_target']):.2f} / {f(cs['lambda'])/f(cs['rate_target']):.2f}", f"{p50t:.2f} → {p50s:.2f} (×{p50s/p50t:.2f})",
                f"{fmt(ig('tw', s))} → {fmt(ig('st', s))}", fmt(share("st", s, "occ_foreign_ms")), fmt(share("st", s, "occ_sib_foreign_ms")),
                f"{fmt(rate('tw', s, 'R_self_ms'), 0)} → {fmt(rate('st', s, 'R_self_ms'), 0)}", f"{fmt(rate('st', s, 'W_own_futex_ms'), 0)} / {fmt(rate('st', s, 'W_idle_ms'), 0)}",
                (fmt(np.median([f(r["truth_ext"]) for r in vs])) if vs else "— (no truth)"), (fmt(np.median([f(r["E"]) for r in vs])) if vs else "—"),
                (rr(rows) if vs else "—"), (rr(rows4) if vs else "—"),
                f"{fmt(pmed('tw', s, 'v_ipc'))} → {fmt(pmed('st', s, 'v_ipc'))}", f"{fmt(pmed('tw', s, 'v_llc_misses_per_req'), 0)} → {fmt(pmed('st', s, 'v_llc_misses_per_req'), 0)}",
                fmt(pmed("st", s, "a_ipc")), f"{fmt(prate('tw', s, 'a_llc_misses') / 1e6)} → {fmt(prate('st', s, 'a_llc_misses') / 1e6)}"]
        lines.append("| " + " | ".join(vals) + " |")
    n = sum(len(v) for v in V.values()); c1 = collections.Counter(int(f(r["row"])) for v in V.values() for r in v); c4 = collections.Counter(int(f(r["row_e4"])) for v in V.values() for r in v)
    cells = np.mean([r["cell"] == r["truth_cell"] for v in V.values() for r in v]) if n else np.nan
    lines.append(""); lines.append(f"Served seconds with a truth: {n}; C.1 rows " + ", ".join(f"{k_}×{v_}" for k_, v_ in sorted(c1.items())) + "; sibling rule rows " + ", ".join(f"{k_}×{v_}" for k_, v_ in sorted(c4.items())) + f"; cells vs truth {100*cells:.1f} %; ext-truth seconds {sum(1 for v in V.values() for r in v if r['truth'] == 'ext')}.")
    out = "\n".join(lines); print(out)
    if a.md: open(a.md, "w", encoding="utf-8").write(out + "\n")


if __name__ == "__main__":
    main()
