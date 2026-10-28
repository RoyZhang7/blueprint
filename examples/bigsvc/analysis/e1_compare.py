#!/usr/bin/env python3
"""E1/B3: does the wait W depend on the mix beyond rho, and does the avgS input fix it?

Compares, per step, the caller-observed wait W = median_e(Lat_e - FL_e) of several
sweeps that target the same utilisations with mixes whose mean service time avgS
differs. Under "rho is enough" the W(u) curves coincide; FIFO theory scales W with
E[S^2]/E[S]; the design's second input is avgS = sum %_e c_e (F2).

With --fit, builds W(rho, avgS) from the given sweeps (caller view) and reports the
near-knee gap (u >= 0.6) of each sweep's observed W against three predictors:
  (1) W(rho) from the --uniform sweep alone, (2) W(rho, avgS) fitted on all sweeps
  (in-sample), (3) leave-one-out: each sweep predicted from the others. With
  --shift-run RANDOM_RUN also reports the simpler alternative: W profiled at one mix with
  the error bound at +-1 std of the mix shift (std of avgS over that run's windows).

Optionally (--onnode RUN) checks the server-side caller-stamp stream of a run whose load
generator shared the service's clock: x50 + q50 is the admission wait seen from inside
the service, compared with the caller's W.

Usage: e1_compare.py --uniform RUN --short RUN --long RUN [--onnode RUN] [--fit] [--shift-run RUN]
                     [--out DIR] [--settle 5] [--costs costs.json]
"""
import argparse, os, sys
from collections import defaultdict
import numpy as np
sys.path.insert(0, os.path.dirname(__file__))
from analyze import load_run
from dictionary import (EP_A, EP_N, EP_P50, EP_Q50, EP_X50, WaitModel, avgS_of_window, client_curve, client_floors,
                        costs_of_run, notation, rho_series, settled_windows)

try:
    import matplotlib; matplotlib.use("Agg"); import matplotlib.pyplot as plt
except Exception:
    plt = None


def per_step(R, settle, costs_file):
    names = R["run"]["endpoints"]; viol = dict(zip(names, R["run"]["violators"]))
    c_ms, _ = costs_of_run(R, costs_file)
    fl = client_floors(R, settle)
    cc = client_curve(R, settle, c_ms, fl, viol=viol)
    k = cc["k"]
    W, step_of, _ = settled_windows(R, settle)
    rs = rho_series(W, c_ms, 1.0)
    step_t0 = {s["step"]: s["t"] for s in R["steps"]}
    arr = defaultdict(int)
    for w in W:
        for e, v in w["ep"].items():
            arr[e] += v[EP_A]
    tot = sum(arr.values()); sh = {e: arr[e] / tot for e in arr}
    ES = sum(sh[e] * c_ms[e] for e in sh); ES2 = sum(sh[e] * c_ms[e] ** 2 for e in sh)
    rows = []
    for s in sorted(set(step_of)):
        idx = [i for i, st in enumerate(step_of) if st == s]
        ws = [W[i] for i in idx]
        rho = float(np.mean(rs[idx])); S = float(np.nanmean([avgS_of_window(w, c_ms) for w in ws]))
        lam = sum(w["a"] for w in ws) / sum((w["w"] / 1000.0) for w in ws)
        srv_q, srv_x = [], []
        for w in ws:
            for e, v in w["ep"].items():
                if v[EP_N] > 0 and not viol[e]:
                    srv_q.extend([v[EP_Q50] / 1e6] * v[EP_N])
                    if len(v) > EP_X50:
                        srv_x.extend([v[EP_X50] / 1e6] * v[EP_N])
        cw, cg = [], []
        for r in R["reports"]:
            if r.get("step", -9) != s or r["t"] < step_t0[s] + (settle + 1) * 1000:
                continue
            for e, v in r["ep"].items():
                if v[0] >= 1 and not viol[e]:
                    cw.extend([v[1] / 1e6 - fl[e]] * v[0]); cg.extend([v[1] / 1e6 / fl[e]] * v[0])
        rows.append(dict(step=s, rate=R["steps"][s]["rate"] if s < len(R["steps"]) else None, lam=lam, rho=rho, u=rho / k, avgS=S,
                         W=float(np.median(cw)) if cw else np.nan, g=float(np.median(cg)) if cg else np.nan, n=len(cw),
                         srv_q=float(np.median(srv_q)) if srv_q else np.nan, srv_x=float(np.median(srv_x)) if srv_x else np.nan))
    return dict(k=k, ES=ES, M=ES2 / ES, rows=rows, n_ep=len(sh), curve=cc, avgS=cc["avgS"])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--uniform", required=True); ap.add_argument("--short"); ap.add_argument("--long"); ap.add_argument("--onnode")
    ap.add_argument("--fit", action="store_true"); ap.add_argument("--shift-run", default=None)
    ap.add_argument("--out", default=None); ap.add_argument("--settle", type=float, default=5.0); ap.add_argument("--costs", default=None)
    a = ap.parse_args()
    out = a.out or a.uniform; os.makedirs(out, exist_ok=True)
    runs = {}
    for name in ("uniform", "short", "long", "onnode"):
        d = getattr(a, name)
        if d:
            runs[name] = per_step(load_run(d), a.settle, a.costs)
    for name, P in runs.items():
        print(f"\n== {name}: {P['n_ep']} endpoints, mix avgS={P['ES']:.2f} ms, E[S2]/E[S]={P['M']:.2f} ms, k={P['k']}")
        print("   step  rate   lambda    u    avgS   caller W (ms)   g(diag) | server-side q50   x50(pre)")
        for r in P["rows"]:
            print(f"   {r['step']:3d}  {r['rate']!s:>5}  {r['lam']:7.0f}  {r['u']:5.2f}  {r['avgS']:5.2f}  {r['W']:8.2f}      {r['g']:6.2f} |  {r['srv_q']:7.3f}  {r['srv_x']:7.3f}")
    base = runs["uniform"]; ug = np.array([r["u"] for r in base["rows"]])

    def interp_W(P, u):
        us = np.array([r["u"] for r in P["rows"]]); Ws = np.array([r["W"] for r in P["rows"]]); ok = ~np.isnan(Ws)
        return float(np.interp(u, us[ok], Ws[ok])) if ok.sum() >= 2 and us[ok].min() <= u <= us[ok].max() else np.nan

    with open(os.path.join(out, "e1_matched.csv"), "w") as f:
        f.write("u," + ",".join(f"W_{n}" for n in runs) + "\n")
        print("\n== W at matched utilisation (ms); FIFO theory ratio W_long/W_short ~ M_long/M_short = "
              + (f"{runs['long']['M'] / runs['short']['M']:.2f}" if "short" in runs and "long" in runs else "n/a"))
        print("   u    " + "  ".join(f"{n:>9}" for n in runs) + ("   long/short" if "short" in runs and "long" in runs else ""))
        for u in ug:
            vals = {n: interp_W(P, u) for n, P in runs.items()}
            f.write(f"{u:.3f}," + ",".join(f"{vals[n]:.3f}" for n in runs) + "\n")
            ratio = vals["long"] / vals["short"] if "short" in vals and "long" in vals and vals["short"] > 0.05 else np.nan
            print(f"   {u:4.2f} " + "  ".join(f"{vals[n]:9.2f}" for n in runs) + (f"   {ratio:6.2f}" if not np.isnan(ratio) else ""))
    if "onnode" in runs:
        print("\n== on-node run: caller W vs server-side pre (x50) + queue (q50) per step")
        for r in runs["onnode"]["rows"]:
            print(f"   u={r['u']:4.2f}  client W={r['W']:7.2f}  server x50+q50={r['srv_x'] + r['srv_q']:7.2f}")

    # ---- F2: W(rho, avgS)
    sweeps = {n: P for n, P in runs.items() if n != "onnode"}
    if a.fit and len(sweeps) >= 2:
        k = base["k"]

        def model_of(names):
            m = WaitModel(k)
            for n in names:
                c = sweeps[n]["curve"]
                m.add(c["avgS"], c["mids"], c["W50"], c["W90"], c["wmax"], c["rho_max"], n)
            return m

        full = model_of(list(sweeps)); only_u = model_of(["uniform"])
        print(f"\n== F2: W(rho, avgS) fitted on {list(sweeps)} (avgS levels {', '.join(f'{s:.2f}' for s in full.avgS_levels)} ms); near-knee gap = observed W / predicted W at u >= 0.6")
        print("   sweep     u    avgS   W obs   (1) W(rho) uniform only   (2) W(rho,avgS) in-sample   (3) leave-one-out")
        gaps = defaultdict(list)
        for n, P in sweeps.items():
            loo = model_of([m for m in sweeps if m != n]) if len(sweeps) > 2 else None
            for r in P["rows"]:
                if r["u"] < 0.6 or np.isnan(r["W"]) or r["W"] < 0.5 or r["rho"] > full.rho_max or r["step"] in P["curve"]["overloaded"]:
                    continue  # the wait regime of the profiled range only; overloaded steps are not profiled data
                p1 = only_u.predict(r["rho"]); p2 = full.predict(r["rho"], r["avgS"]); p3 = loo.predict(r["rho"], r["avgS"]) if loo else np.nan
                gaps[1].append(r["W"] / p1); gaps[2].append(r["W"] / p2)
                if loo:
                    gaps[3].append(r["W"] / p3)
                print(f"   {n:8s} {r['u']:4.2f}  {r['avgS']:5.2f}  {r['W']:7.2f}   {p1:7.2f} ({r['W'] / p1:4.2f}x)        {p2:7.2f} ({r['W'] / p2:4.2f}x)          " + (f"{p3:7.2f} ({r['W'] / p3:4.2f}x)" if loo else "    n/a"))
        for i, lbl in ((1, "W(rho) from the uniform sweep"), (2, "W(rho, avgS) in-sample"), (3, "W(rho, avgS) leave-one-out")):
            if gaps[i]:
                g = np.array(gaps[i])
                print(f"   gap summary {lbl}: obs/pred median={np.median(g):.2f}, range [{g.min():.2f}, {g.max():.2f}], |log ratio| mean={np.mean(np.abs(np.log(g))):.2f}")
        if a.shift_run:
            Rr = load_run(a.shift_run); c_ms, _ = costs_of_run(Rr, a.costs)
            Wr, _, _ = settled_windows(Rr, a.settle)
            S = np.array([avgS_of_window(w, c_ms) for w in Wr]); S = S[~np.isnan(S)]
            s_ref = base["avgS"]; sd = float(np.std(S))
            print(f"\n== alternative: W profiled at one mix (uniform, avgS={s_ref:.2f} ms) with the error bound at +-1 std of the mix shift;"
                  f" mix shift over {os.path.basename(a.shift_run)}: avgS mean={S.mean():.2f}, std={sd:.2f} ms ({sd / s_ref * 100:.0f}% of the profiled mix)")
            print("   u    W(rho)   W(rho, S-1sd)   W(rho, S+1sd)   bound = max |W(rho,S+-sd) - W(rho)|")
            with open(os.path.join(out, "e1_bound.csv"), "w") as f:
                f.write("u,W_ref,W_minus,W_plus,bound_ms\n")
                for u in ug:
                    rho = u * k
                    if rho > full.rho_max:
                        continue
                    w0 = full.predict(rho, s_ref); wm = full.predict(rho, s_ref - sd); wp = full.predict(rho, s_ref + sd)
                    b = max(abs(wm - w0), abs(wp - w0))
                    f.write(f"{u:.3f},{w0:.4f},{wm:.4f},{wp:.4f},{b:.4f}\n")
                    print(f"   {u:4.2f} {w0:8.2f} {wm:12.2f} {wp:15.2f}      {b:8.2f} ms ({b / w0 * 100 if w0 > 0.05 else float('nan'):.0f}%)")
    if plt:
        fig, ax = plt.subplots(1, 2, figsize=(11, 4.8))
        for n, P in runs.items():
            us = [r["u"] for r in P["rows"]]
            ax[0].plot(us, [r["W"] for r in P["rows"]], "o-", label=f"{n} (avgS={P['ES']:.1f} ms)")
            ax[1].plot([r["lam"] for r in P["rows"]], [r["W"] for r in P["rows"]], "o-", label=n)
        ax[0].set_xlabel("rho/k"); ax[0].set_ylabel("caller W = Lat_e - FL_e (ms)"); ax[0].set_yscale("log"); ax[0].legend(fontsize=8); ax[0].set_title("W vs rho, by mix")
        ax[1].set_xlabel("lambda (req/s)"); ax[1].set_ylabel("caller W (ms)"); ax[1].set_yscale("log"); ax[1].legend(fontsize=8); ax[1].set_title("W vs lambda, by mix (the index being replaced)")
        for x in ax:
            x.grid(alpha=.3)
        fig.tight_layout(); notation(fig, "caller view; W = median over normal endpoints; one curve per sweep (mix)")
        fig.savefig(os.path.join(out, "e1.png"), dpi=130)
        print(f"figure: {os.path.join(out, 'e1.png')}")


if __name__ == "__main__":
    main()
