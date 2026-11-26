#!/usr/bin/env python3
"""Phase G Addendum E.1: the amount of run-queue contention from phi instead of from E, per served second.

Three amounts, each a share of the second's Lat (the 5 s-smoothed server-side Lat_e p50 the cell is labelled from):
  (a) E = (Lat - D[rho; mix]) / Lat                           -- the Phase G amount (verdict.csv E, unguarded);
  (b) naive, signal only: (R_foreign / lambda) / Lat          -- the aggressor-held run-queue delay of the service's
      threads this second (probe, ms per second) spread over the second's requests, as a share of Lat;
  (c) capacity counterfactual: (D[rho / (1 - phi); mix] - D[rho; mix]) / Lat -- the dictionary at the same mix with the
      cpuset shrunk by the aggressor cgroups' share phi: the wait the service would show if it had only (1 - phi) of
      its cores, with no extra queueing beyond the dictionary's own h(rho). rho / (1 - phi) past the dictionary's last
      bin is held there (np.interp) and counted ("past the profile": the queue model stops).
Scored against the truth share (twin, truth_ext) within +-0.15 on the slow seconds (y >= 0.5) and on every second, and
as the cell slow x (amount >= 0.5) against the truth cell. The no-aggressor rows are the zero control: phi = 0 there,
so (c) = 0 and (b) = the foreign-class noise.

usage: e1_amount.py --row "label|RUN/g2/verdict.csv|RUN/b4g2/b4_windows.csv|RUN/b4g2/service_dict.json|k" [--row ...]
                    [--md out.md] [--csv out.csv] [--fig out.png --fig-rows "label1,label2"] [--dump DIR]
"""
import argparse, csv, json, os
import numpy as np


def f(x):
    try: return float(x)
    except Exception: return np.nan


def clamp01(x):
    return float(min(1.0, max(0.0, x))) if np.isfinite(x) else np.nan


class Dict:
    def __init__(self, path):
        D = json.load(open(path)); self.mids = np.array(D["rho"], float); self.h = np.array(D["h"], float)
        half = float(self.mids[1] - self.mids[0]) / 2 if len(self.mids) > 1 else 0.0; self.rho_max = float(self.mids.max() + half); self.rho_min = float(self.mids.min() - half)

    def hat(self, u):
        return float(np.interp(u, self.mids, self.h))

    def hat_lin(self, u):
        """h continued linearly past the last two bins (the (c') variant: what a straight continuation of the profile gives)."""
        if u <= self.mids[-1] or len(self.mids) < 2: return self.hat(u)
        sl = (self.h[-1] - self.h[-2]) / (self.mids[-1] - self.mids[-2]); return float(self.h[-1] + sl * (u - self.mids[-1]))


def score(rows):
    """rows: list of dicts with truth_ext, y, truth_cell, amounts a/b/c."""
    out = {}
    slow = [r for r in rows if r["y"] >= 0.5]
    for key in ("a", "b", "c", "cl"):
        v = np.array([r[key] for r in rows]); t = np.array([r["truth_ext"] for r in rows]); ok = np.isfinite(v)
        vs = np.array([r[key] for r in slow]); ts = np.array([r["truth_ext"] for r in slow]); oks = np.isfinite(vs)
        out[key] = dict(within_all=float(np.mean(np.abs(v[ok] - t[ok]) <= 0.15)) if ok.any() else np.nan,
                        within_slow=float(np.mean(np.abs(vs[oks] - ts[oks]) <= 0.15)) if oks.any() else np.nan,
                        cell=float(np.mean([(("slow" if r["y"] >= 0.5 else "fast") + "/" + ("ext" if r[key] >= 0.5 else "int")) == r["truth_cell"] for r in rows if np.isfinite(r[key])])),
                        bias_ext=float(np.median([r[key] - r["truth_ext"] for r in rows if r["truth"] == "ext" and np.isfinite(r[key])])) if any(r["truth"] == "ext" for r in rows) else np.nan,
                        n=int(ok.sum()), n_slow=int(oks.sum()))
    return out


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--row", action="append", required=True); ap.add_argument("--md"); ap.add_argument("--csv"); ap.add_argument("--fig"); ap.add_argument("--fig-rows", default=""); ap.add_argument("--dump")
    a = ap.parse_args()
    hdr = ["scenario", "n (slow)", "ext-truth s", "φ on ext-truth s, median (p10–p90)", "ρ/(1−φ) past the profile",
           "(a) E: ±0.15 slow / all", "(a) cell", "(b) R_foreign/λ ÷ Lat: ±0.15 slow / all", "(b) cell", "(c) D(ρ/(1−φ)) − D(ρ): ±0.15 slow / all", "(c) cell", "(c′) h continued linearly: ±0.15 slow / all", "(c′) cell",
           "median (c) − E on ext-truth s", "median (c′) − E on ext-truth s", "median (b) − E on ext-truth s"]
    lines = ["| " + " | ".join(hdr) + " |", "|" + "---|" * len(hdr)]; recs = []; series = {}
    for spec in a.row:
        lab, vpath, bpath, dpath, k = spec.split("|"); k = int(k); V = list(csv.DictReader(open(vpath))); B = {int(f(r["t"])): r for r in csv.DictReader(open(bpath))}; SD = Dict(dpath)
        rows = []; past = 0
        for v in V:
            b = B.get(int(f(v["t"])))
            if b is None: continue
            lat = f(b.get("lat_s_ms")); lat = lat if np.isfinite(lat) else f(v["lat_ms"]); u = f(v["rho"]); lam = f(v["lam"]); K = f(b["K_ms"]); Fb = f(b["Fbar_ms"])
            phi = f(v.get("phi_cpuset")); phi = phi if np.isfinite(phi) else 0.0; phi = min(phi, 0.95)
            E = f(v["E"]); Rf = f(v["R_foreign_ms"])
            bb = clamp01((Rf / lam) / lat) if lam > 0 and lat > 0 else np.nan
            u2 = u / (1 - phi); past += u2 > SD.rho_max
            Dc = Fb + SD.hat(u2) * K; D0 = Fb + SD.hat(u) * K; cc = clamp01((Dc - D0) / lat) if lat > 0 else np.nan
            Dl = Fb + SD.hat_lin(u2) * K; cl = clamp01((Dl - (Fb + SD.hat_lin(u) * K)) / lat) if lat > 0 else np.nan   # both ends on the continued curve
            rows.append(dict(t=int(f(v["t"])), step=v["step"], lam=lam, rho=u, phi=phi, lat=lat, D=D0, Dc=Dc, a=E, b=bb, c=cc, cl=cl, Dl=Dl, y=f(v["y"]), truth_ext=f(v["truth_ext"]), truth=v["truth"], truth_cell=v["truth_cell"], row=int(f(v["row"])), past=int(u2 > SD.rho_max)))
        S = score(rows); ext = [r for r in rows if r["truth"] == "ext"]; ph = np.array([r["phi"] for r in ext]) if ext else np.array([np.nan])
        fmt = lambda key: f"{100*S[key]['within_slow']:.0f} % / {100*S[key]['within_all']:.0f} %"
        vals = [lab, f"{len(rows)} ({sum(1 for r in rows if r['y'] >= 0.5)})", len(ext), (f"{np.median(ph):.2f} ({np.percentile(ph, 10):.2f}–{np.percentile(ph, 90):.2f})" if ext else "—"), f"{past} of {len(rows)}",
                fmt("a"), f"{100*S['a']['cell']:.0f} %", fmt("b"), f"{100*S['b']['cell']:.0f} %", fmt("c"), f"{100*S['c']['cell']:.0f} %", fmt("cl"), f"{100*S['cl']['cell']:.0f} %",
                (f"{np.median([r['c'] - r['a'] for r in ext]):+.2f}" if ext else "—"), (f"{np.median([r['cl'] - r['a'] for r in ext]):+.2f}" if ext else "—"), (f"{np.median([r['b'] - r['a'] for r in ext]):+.2f}" if ext else "—")]
        lines.append("| " + " | ".join(str(x) for x in vals) + " |"); recs.append(dict(zip(hdr, vals))); series[lab] = rows
        if a.dump:
            os.makedirs(a.dump, exist_ok=True); fn = os.path.join(a.dump, lab.split()[0].replace("'", "p").replace(".", "") + "-e1.csv")
            with open(fn, "w", newline="") as fh: w = csv.DictWriter(fh, fieldnames=list(rows[0].keys())); w.writeheader(); w.writerows(rows)
        # where (c) matches E and where it stops: on ext-truth seconds, by step
        steps = sorted(set(r["step"] for r in ext), key=lambda s: f(s))
        print(f"{lab}: {len(rows)} s, {len(ext)} ext-truth; per step on ext-truth seconds (step: n, phi, rho, rho/(1-phi), truth, E, (b), (c), past-profile):")
        for st in steps:
            g = [r for r in ext if r["step"] == st]
            print(f"   {st}: n {len(g)}, phi {np.median([r['phi'] for r in g]):.2f}, rho {np.median([r['rho'] for r in g]):.2f} -> {np.median([r['rho']/(1-r['phi']) for r in g]):.2f} (max {SD.rho_max:.2f}), truth {np.median([r['truth_ext'] for r in g]):.2f}, E {np.median([r['a'] for r in g]):.2f}, (b) {np.nanmedian([r['b'] for r in g]):.2f}, (c) {np.median([r['c'] for r in g]):.2f}, (c') {np.median([r['cl'] for r in g]):.2f}, past {sum(r['past'] for r in g)}")
    out = "\n".join(lines); print(out)
    if a.md: open(a.md, "w", encoding="utf-8").write(out + "\n")
    if a.csv:
        with open(a.csv, "w", newline="", encoding="utf-8") as fh: w = csv.DictWriter(fh, fieldnames=hdr); w.writeheader(); w.writerows(recs)
    if a.fig:
        import matplotlib; matplotlib.use("Agg"); import matplotlib.pyplot as plt
        labs = [l for l in a.fig_rows.split(",") if l] or list(series)[:2]; labs = [next(k for k in series if k.startswith(l.strip())) for l in labs]
        fig, axs = plt.subplots(3 * len(labs), 1, figsize=(13, 5.0 * len(labs)), gridspec_kw={"height_ratios": [2.0, 2.0, 0.9] * len(labs), "hspace": 0.55}, squeeze=False); axs = axs[:, 0]
        for i, lab in enumerate(labs):
            R = series[lab]; t0 = R[0]["t"]; ts = np.array([r["t"] - t0 for r in R])
            ax = axs[3 * i]; ax.plot(ts, [r["lat"] for r in R], color="#333333", lw=1.0, label="observed p50 (server-side Lat_e, processing stream, 5 s-smoothed)")
            ax.plot(ts, [r["D"] for r in R], color="#2A6F97", lw=1.5, label="D[ρ; mix]"); ax.plot(ts, [r["Dc"] for r in R], color="#D85A30", lw=1.3, ls="-.", label="D[ρ ÷ (1 − φ); mix] (capacity counterfactual)")
            ax.set_ylim(0, np.nanpercentile([r["lat"] for r in R], 98) * 1.25); ax.set_ylabel("latency (ms)"); ax.legend(loc="upper left", fontsize=8); ax.set_title(lab, loc="left", fontsize=10)
            ax = axs[3 * i + 1]; ax.plot(ts, [r["truth_ext"] for r in R], color="#1D9E75", lw=1.8, ls="--", label="truth share (twin)")
            ax.plot(ts, [r["a"] for r in R], color="#2A6F97", lw=1.4, label="(a) E = (Lat − D) ÷ Lat"); ax.plot(ts, [r["b"] for r in R], color="#8F8E87", lw=1.1, label="(b) (R_foreign ÷ λ) ÷ Lat")
            ax.plot(ts, [r["c"] for r in R], color="#D85A30", lw=1.4, ls="-.", label="(c) (D[ρ ÷ (1 − φ)] − D[ρ]) ÷ Lat, h held past the profile"); ax.plot(ts, [r["cl"] for r in R], color="#B8860B", lw=1.1, ls=":", label="(c′) same, h continued linearly"); ax.axhline(0.5, color="#999999", ls=":"); ax.set_ylim(0, 1); ax.set_ylabel("share of Lat"); ax.legend(loc="upper left", fontsize=8, ncol=2, framealpha=0.95)
            ax = axs[3 * i + 2]; tr = np.array([r["truth_ext"] for r in R]); ph = np.array([r["phi"] for r in R]); pa = np.array([r["past"] for r in R])
            ax.fill_between(ts, 2.1, 2.9, where=tr >= 0.5, color="#1D9E75", step="mid"); ax.fill_between(ts, 1.1, 1.9, where=ph > 0.05, color="#D85A30", step="mid"); ax.fill_between(ts, 0.1, 0.9, where=pa > 0, color="#8F8E87", step="mid")
            ax.set_yticks([0.5, 1.5, 2.5]); ax.set_yticklabels(["ρ ÷ (1 − φ) past the profile", "φ > 0.05", "truth: ext"]); ax.set_ylim(0, 3)
            if i == len(labs) - 1: ax.set_xlabel("time since the first served second (s)")
        fig.suptitle("Fig. G-E1 — the amount of run-queue contention per second: E from the dictionary, the probe's foreign run-queue delay, and the capacity counterfactual D[ρ ÷ (1 − φ)]\n(server-side Lat_e p50, processing stream, 5 s-smoothed; φ = aggressor cgroups' share of the cpuset from the probe's occupancy; truth = twin at the same step)", x=0.01, ha="left", fontsize=10)
        fig.savefig(a.fig, dpi=120, bbox_inches="tight"); print("wrote", a.fig)


if __name__ == "__main__":
    main()
