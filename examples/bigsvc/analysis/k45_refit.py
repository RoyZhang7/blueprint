#!/usr/bin/env python3
"""RESULTS-K 4.5.1: re-score with the dictionary fit's two options (dictionary.py fit_service_dict: min_bin_windows,
step_interp; b4_compare.py --min-bin-windows N --step-interp), everything else as the original scoring.

k45    K4.5's two 7 000 req/s runs (reserve 1, the free controller) and their twin T7, against the sweep D: b4_compare with
       the options into <run>/b4g2-451, g2_verdict against T7 into <run>/g2-vs-T7-451 (T7's own b4 re-done too: its
       E sets the threshold). Before = the K4.5 outputs (<run>/b4g2, <run>/g2-vs-T7). Also D at every scored second of
       the 5 000 req/s runs under both fits.
phaseg the Phase G rows whose scored load was not a sweep step (k45_dictcheck.py): each row's original commands are
       re-run first with the default fit and must reproduce the stored verdict.csv second by second; then the same
       commands with the options. Outputs go to OUT/phaseg/<row>/{before,after}; nothing in the Phase G run directories
       is written.

usage: k45_refit.py k45 RUNS [--min-windows 50] [--out results/k45]
       k45_refit.py phaseg [--min-windows 50] [--out results/k45] [--work DIR]
"""
import argparse, csv, json, os, shutil, subprocess, sys
from collections import Counter
import numpy as np
HERE = os.path.dirname(os.path.abspath(__file__)); BIG = os.path.dirname(HERE); sys.path.insert(0, HERE)
ENV0 = dict(os.environ, GORDION_NOMINAL_MHZ="2000", GORDION_FREQ_NORM="0", GORDION_LAT="unstamped", PYTHONIOENCODING="utf-8")


def call(script, args, log, env):
    with open(log, "w", encoding="utf-8") as fh:
        p = subprocess.run([sys.executable, os.path.join(HERE, script)] + args, stdout=fh, stderr=subprocess.STDOUT, env=env, cwd=BIG)
    if p.returncode: raise SystemExit(f"{script} failed ({p.returncode}), see {log}")


def fnum(x):
    try: return float(x)
    except Exception: return np.nan


def vread(o):
    return list(csv.DictReader(open(os.path.join(o, "verdict.csv")))) if os.path.exists(os.path.join(o, "verdict.csv")) else []


def vsum(R, thr=None):
    E = np.array([fnum(r["E"]) for r in R]); D = np.array([fnum(r["D_mix_ms"]) for r in R]); rows = Counter(int(r["row"]) for r in R)
    out = dict(seconds=len(R), row1=rows.get(1, 0), row6=rows.get(6, 0), other=len(R) - rows.get(1, 0) - rows.get(6, 0),
               E_med=float(np.nanmedian(E)), E_p90=float(np.nanpercentile(E, 90)), D_med_ms=float(np.nanmedian(D)))
    if thr: out.update(E_threshold=thr.get("E_far"), twin_E_med=thr.get("E_med"), twin_E_iqr=thr.get("E_iqr"))
    return out


# ------------------------------------------------------------------------------------------------------------ K4.5
def k45(a):
    seg = {d.split("-k45-")[-1]: os.path.join(a.runs, d) for d in os.listdir(a.runs) if "-k45-" in d}
    D = seg["D"]; costs = os.path.join(BIG, "results", "k45", "costs-recommendation-K45-avg.json")
    env = dict(ENV0, GORDION_SVC="recommendation")
    opt = ["--min-bin-windows", str(a.min_windows), "--step-interp"]
    for n in ("T7", "res1x7", "free7"):
        o = os.path.join(seg[n], "b4g2-451"); os.makedirs(o, exist_ok=True)
        call("b4_compare.py", [seg[n], "--dict", D, "--costs", costs, "--server", "--smooth", "5", "--out", o] + opt, os.path.join(o, "b4.txt"), env)
    T = seg["T7"]; rows = []; secs = []
    for n in ("res1x7", "free7"):
        A = seg[n]; o = os.path.join(A, "g2-vs-T7-451"); os.makedirs(o, exist_ok=True)
        call("g2_verdict.py", ["--b4", os.path.join(A, "b4g2-451", "b4_windows.csv"), "--g2", os.path.join(A, "g2", "g2_windows.csv"),
                               "--twin-g2", os.path.join(T, "g2", "g2_windows.csv"), "--twin-b4", os.path.join(T, "b4g2-451", "b4_windows.csv"),
                               "--dict", os.path.join(A, "b4g2-451", "service_dict.json"), "--k", "4", "--slots", "4", "--go",
                               "--aggr-name", "hc-secondary-burner", "--baseline", os.path.join(T, "baseline.json"), "--label", f"{n} vs T7, 4.5.1 fit", "--out", o],
             os.path.join(o, "verdict.txt"), env)
        for tag, d in (("before", os.path.join(A, "g2-vs-T7")), ("after", o)):
            R = vread(d); thr = json.load(open(os.path.join(d, "verdict_thresholds.json")))
            rows.append(dict(run=n, fit=tag, **vsum(R, thr)))
            print(n, tag, {k: (round(v, 4) if isinstance(v, float) else v) for k, v in rows[-1].items()})
        B = {int(r["t"]): r for r in vread(os.path.join(A, "g2-vs-T7"))}; F = {int(r["t"]): r for r in vread(o)}
        for t in sorted(set(B) & set(F)):
            b, f = B[t], F[t]
            secs.append(dict(run=n, t=t, D_before_ms=fnum(b["D_mix_ms"]), D_after_ms=fnum(f["D_mix_ms"]), E_before=fnum(b["E"]), E_after=fnum(f["E"]),
                             row_before=int(b["row"]), row_after=int(f["row"]), phi=fnum(f["phi_cpuset"]), truth=fnum(f["truth_ext"])))
    # D at the 5 000 req/s runs' scored seconds under both fits (same per-second rho and mix as their b4 output)
    from analyze import load_run
    from dictionary import fit_service_dict
    os.environ.update(env)
    R0 = load_run(D, svc="recommendation")
    old = fit_service_dict(R0, 5.0, costs); new = fit_service_dict(R0, 5.0, costs, min_bin_windows=a.min_windows, step_interp=True)
    for n in ("res1", "res2", "res3", "S0", "S1", "T7"):
        b4 = list(csv.DictReader(open(os.path.join(seg[n], "b4g2", "b4_windows.csv"))))
        u = np.array([fnum(r["util"]) for r in b4 if r.get("step") not in ("", None) and int(fnum(r["step"])) >= 0])
        dd = np.array([new.p50_at(x) - old.p50_at(x) for x in u])
        print(f"D at {n}'s seconds (sweep mix): old median {np.median([old.p50_at(x) for x in u]):.4f} ms, new {np.median([new.p50_at(x) for x in u]):.4f} ms; "
              f"|new - old| max {np.max(np.abs(dd)):.5f} ms, seconds changed by > 0.0005 ms: {int(np.sum(np.abs(dd) > 0.0005))} of {len(u)}")
        rows.append(dict(run=n, fit="D only, sweep mix", seconds=len(u), D_old_med_ms=float(np.median([old.p50_at(x) for x in u])),
                         D_new_med_ms=float(np.median([new.p50_at(x) for x in u])), D_abs_change_max_ms=float(np.max(np.abs(dd))),
                         seconds_D_changed=int(np.sum(np.abs(dd) > 0.0005))))
    print("new fit anchors:", [(x["step"], round(x["rho"], 3), round(x["lo"], 3), round(x["hi"], 3), round(x["p50"], 4)) for x in new.anchors])
    print("new fit bins kept:", [(round(m, 3), int(w)) for m, w in zip(new.mids, new.nwin)], "| old:", [(round(m, 3), int(w)) for m, w in zip(old.mids, old.nwin)])
    wcsv(os.path.join(a.out, "k45_refit.csv"), rows); wcsv(os.path.join(a.out, "k45_refit_seconds.csv"), secs)
    thr = {(n, tag): json.load(open(os.path.join(seg[n], d, "verdict_thresholds.json")))["E_far"] for n in ("res1x7", "free7") for tag, d in (("before", "g2-vs-T7"), ("after", "g2-vs-T7-451"))}
    figure(secs, thr, rows, a.out)
    return rows, secs


def figure(secs, thr, rows, outdir):
    """Fig. K4-5d: the amount E per second and the verdict on each second, the K4.5 fit and the 4.5.1 fit, both 7 000 req/s
    runs against T7, with the twin's band (E ~ 0) and the row-1 threshold"""
    import matplotlib; matplotlib.use("Agg"); import matplotlib.pyplot as plt
    from k45_analyze import verdict_panel, band_handles
    S = {(r["run"], r["fit"]): r for r in rows if r.get("fit") in ("before", "after")}
    fig, axs = plt.subplots(4, 1, figsize=(13, 9.2), sharex=True); seen = {}
    for ax, (n, tag) in zip(axs, (("res1x7", "before"), ("res1x7", "after"), ("free7", "before"), ("free7", "after"))):
        X = [x for x in secs if x["run"] == n]; t0 = X[0]["t"]
        verdict_panel(ax, [x["t"] - t0 for x in X], [x["E_" + tag] for x in X], [x["row_" + tag] for x in X], thr[(n, tag)], "T7")
        sm = S[(n, tag)]; fit = "K4.5's fit (D from the 16-window bin)" if tag == "before" else "the 4.5.1 fit (50-window floor, step interpolation)"
        ax.set_ylim(-0.01, 0.7); ax.set_ylabel("E", fontsize=8)
        ax.set_title(f"{'reserve 1' if n == 'res1x7' else 'the free controller'} at 7 000 req/s, {fit}: row 1 on {sm['row1']} of {sm['seconds']} s; "
                     f"E median {sm['E_med']:.3f}; D median {sm['D_med_ms']:.4f} ms; threshold {thr[(n, tag)]:.3f}", fontsize=8.5, loc="left")
        for h, l in zip(*ax.get_legend_handles_labels()): seen.setdefault(l, h)
    seen.update(band_handles(plt)); axs[0].legend(seen.values(), seen.keys(), fontsize=7, loc="upper right", ncol=2)
    axs[-1].set_xlabel("seconds of the measured window (scored seconds)")
    fig.suptitle("Fig. K4-5d — RESULTS-K 4.5.1: the 7 000 req/s runs re-scored with the window floor and the step interpolation (against T7)", x=0.01, ha="left", fontsize=10)
    fig.subplots_adjust(left=0.05, right=0.99, top=0.93, bottom=0.06, hspace=0.42); fig.savefig(os.path.join(outdir, "figK4-5d.png"), dpi=130); plt.close(fig)


# ------------------------------------------------------------------------------------------------------------ Phase G
def phaseg(a):
    R = os.path.join(BIG, "results"); OFF = os.path.join(R, "20261111-012821-g2b-overhead-probe-off"); TR = OFF + "-trunc500"
    ON = os.path.join(R, "20261111-014041-g2b-overhead-probe-on"); VI = os.path.join(R, "20261111-050714-g2d-vi-mutex10")
    UT = os.path.join(R, "20261111-073357-g2f-uniform-twin"); MX = os.path.join(R, "20261111-074358-g2f-d5-mutex40")
    # row: (label, stored verdict dir, stressed run, dict sweep, twin run, the twin's dict sweep, extra verdict args, b4 args)
    spec = {
        "vi": ("vi VioMutex 10 %", os.path.join(VI, "g2"), VI, OFF, ON, OFF, ["--twin-steps", "0,1,2,3,4,5,6", "--baseline", os.path.join(VI, "b4g2", "baseline_uniform_same_rate.json")], []),
        "D.5": ("D.5 VioMutex 40 %", os.path.join(MX, "g2"), MX, UT, UT, UT, ["--baseline", os.path.join(UT, "baseline_p50.json")], []),
        # ii: its exact command is not on record; the closest reconstruction (391 of the 392 stored seconds reproduce): truth
        # from the full probe-off sweep's step p50s on steps 0-6, the twin's thresholds from the same steps
        "ii": ("ii served step past the profile", os.path.join(ON, "g2t"), ON, TR, ON, TR, ["--twin-steps", "0,1,2,3,4,5,6", "--baseline", "@ii_baseline"], []),
    }
    work = a.work or os.path.join(a.out, "phaseg-work"); out = []
    os.makedirs(work, exist_ok=True); iib = os.path.join(work, "ii_baseline_steps0-6.json")
    json.dump({k: v for k, v in json.load(open(os.path.join(ON, "b4g2t", "baseline_steps.json"))).items() if int(k) <= 6}, open(iib, "w"))
    spec["ii"] = spec["ii"][:6] + ([x if x != "@ii_baseline" else iib for x in spec["ii"][6]],) + spec["ii"][7:]
    for key, (lab, stored, ST, DS, TW, TWD, vargs, bargs) in spec.items():
        res = {}
        for tag, opt in (("before", []), ("after", ["--min-bin-windows", str(a.min_windows), "--step-interp"])):
            w = os.path.join(work, key.replace(".", ""), tag); os.makedirs(w, exist_ok=True)
            sb4, tb4 = os.path.join(w, "b4"), os.path.join(w, "twin-b4")
            call("b4_compare.py", [ST, "--dict", DS, "--server", "--smooth-s", "5", "--out", sb4] + bargs + opt, os.path.join(w, "b4.txt"), ENV0)
            if TW == ST and TWD == DS: tb4 = sb4
            else: call("b4_compare.py", [TW, "--dict", TWD, "--server", "--smooth-s", "5", "--out", tb4] + opt, os.path.join(w, "twin-b4.txt"), ENV0)
            call("g2_verdict.py", ["--b4", os.path.join(sb4, "b4_windows.csv"), "--g2", os.path.join(ST, "g2", "g2_windows.csv"),
                                   "--twin-g2", os.path.join(TW, "g2", "g2_windows.csv"), "--twin-b4", os.path.join(tb4, "b4_windows.csv"),
                                   "--dict", os.path.join(sb4, "service_dict.json"), "--k", "4", "--go", "--label", lab, "--out", os.path.join(w, "g2")] + vargs,
                 os.path.join(w, "verdict.txt"), ENV0)
            res[tag] = vread(os.path.join(w, "g2"))
        S = {int(r["t"]): r for r in vread(stored)}; B = {int(r["t"]): r for r in res["before"]}
        same = sum(1 for t in S if t in B and S[t]["row"] == B[t]["row"] and abs(fnum(S[t]["E"]) - fnum(B[t]["E"])) < 1e-6)
        repro = f"{same} of {len(S)} stored seconds reproduced (row and E)" + ("" if len(B) == len(S) else f"; {len(B)} seconds scored now")
        print(lab, "|", repro)
        A = {int(r["t"]): r for r in res["after"]}
        ch = Counter((int(B[t]["row"]), int(A[t]["row"])) for t in B if t in A and B[t]["row"] != A[t]["row"])
        dE = np.array([fnum(A[t]["E"]) - fnum(B[t]["E"]) for t in B if t in A])
        cls = lambda RR: Counter(int(r["row"]) for r in RR)
        cb, ca = cls(res["before"]), cls(res["after"])
        out.append(dict(row=lab, reproduction=repro, seconds=len(B), rows_before="; ".join(f"{k}: {v}" for k, v in sorted(cb.items())),
                        rows_after="; ".join(f"{k}: {v}" for k, v in sorted(ca.items())), seconds_row_changed=sum(ch.values()),
                        row_changes="; ".join(f"{x} -> {y} x{n}" for (x, y), n in sorted(ch.items())),
                        E_med_before=float(np.nanmedian([fnum(r["E"]) for r in res["before"]])), E_med_after=float(np.nanmedian([fnum(r["E"]) for r in res["after"]])),
                        E_abs_change_max=float(np.nanmax(np.abs(dE))) if len(dE) else np.nan, seconds_E_changed=int(np.sum(np.abs(dE) > 1e-4))))
        print("  ", {k: (round(v, 4) if isinstance(v, float) else v) for k, v in out[-1].items()})
    wcsv(os.path.join(a.out, "k45_refit_phaseg.csv"), out)


def wcsv(path, rows):
    keys = []
    for r in rows:
        for k in r:
            if k not in keys: keys.append(k)
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=keys); w.writeheader()
        for r in rows: w.writerow({k: (round(v, 5) if isinstance(v, float) else v) for k, v in r.items()})


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("cmd", choices=("k45", "phaseg")); ap.add_argument("runs", nargs="?")
    ap.add_argument("--min-windows", type=int, default=50); ap.add_argument("--out", default=os.path.join(BIG, "results", "k45")); ap.add_argument("--work", default=None)
    a = ap.parse_args()
    k45(a) if a.cmd == "k45" else phaseg(a)


if __name__ == "__main__":
    main()
