#!/usr/bin/env python3
"""Paper / talk figures for the rho-indexed service dictionary.

Reads the analysis outputs of Phases D-G (svc_curve.csv per sweep, b4_windows.csv per scored
run, service_dict.json per dictionary) and writes one PNG per figure. Axis titles are short,
legends name what each line is, and every figure states its runs in the file name only.

usage: paper_figs.py <data-root> <out-dir>
"""
import json, sys, os
import numpy as np, pandas as pd
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.patches import FancyArrowPatch

ROOT, OUT = sys.argv[1], sys.argv[2]
os.makedirs(OUT, exist_ok=True)

# ---- style -------------------------------------------------------------------------------
C = dict(obs='#1B2430', old='#D85A30', new='#2A6F97', truth='#1D9E75', grey='#8F8E87', floor='#B8B5AD',
         mix1='#2A6F97', mix2='#D85A30', mix3='#1D9E75', mix4='#7F77DD', shade='#F2D6CC', shade2='#D5E4EE')
plt.rcParams.update({'font.size': 11, 'axes.titlesize': 12, 'axes.labelsize': 11, 'legend.fontsize': 9.5,
                     'axes.spines.top': False, 'axes.spines.right': False, 'figure.dpi': 160,
                     'axes.grid': True, 'grid.alpha': 0.25, 'legend.frameon': False})
K_CORES = 4  # synthetic service

def p(*a): return os.path.join(ROOT, *a)
def save(fig, name):
    fig.savefig(os.path.join(OUT, name), bbox_inches='tight'); plt.close(fig); print('wrote', name)

# ---- data --------------------------------------------------------------------------------
MIXES = [  # name, dir, label, K (ms, from RESULTS-E), color
    ('short',   '20261102-064024-d-e1-short',   'short mix, c̄ 1.9 ms',    1.24, C['mix1']),
    ('uniform', '20261102-062826-d-a2-uniform', 'uniform mix, c̄ 4.3 ms',  5.10, C['mix2']),
    ('mid7',    '20261102-065751-d-e1-mid7',    'mid mix, c̄ 7.0 ms',      4.18, C['mix3']),
    ('long',    '20261102-064906-d-e1-long',    'long mix, c̄ 10.3 ms',    6.16, C['mix4']),
]
def sweep(d, k):
    s = pd.read_csv(p(d, 'svc_curve.csv'))
    s['served'] = s['lambda'] / s['rate_target']
    s = s[s['served'] >= 0.95].copy()
    s['rho_frac'] = s['rho'] / k
    s['W'] = s['p50_ms'] - s['p50_ms'].iloc[0]
    return s

twin   = pd.read_csv(p('20261102-070541-d-ramp-B-065-104/b4g/b4_windows.csv'))
stress = pd.read_csv(p('20261102-072510-d-ramp-B-065-104-stress1/b4g/b4_windows.csv'))
for b in (twin, stress):
    b['ts'] = b['t'] - b['t'].iloc[0]; b['rho_frac'] = b['rho'] / K_CORES
syn_dict = json.load(open(p('20261102-072510-d-ramp-B-065-104-stress1/b4g/service_dict.json')))
rec_dict = json.load(open(p('hr/20261103-073422-f3-rec-stress2/b4g/service_dict.json')))
rec2 = pd.read_csv(p('hr/20261103-073422-f3-rec-stress2/b4g/b4_windows.csv'))
sn_twin = pd.read_csv(p('sn/20261103-051559-b-sn-ramp-rev-k1c/b4g/b4_windows.csv'))
sn_str  = pd.read_csv(p('sn/20261103-051858-b-sn-ramp-rev-k1c-stress1/b4g/b4_windows.csv'))
for b in (sn_twin, sn_str): b['ts'] = b['t'] - b['t'].iloc[0]


# ---- F0: the mix-shift scenario itself, standalone ---------------------------------------------
def fig0():
    fig, axs = plt.subplots(2, 2, figsize=(11.0, 4.6), sharex='col', gridspec_kw={'hspace': 0.15, 'wspace': 0.22})
    for j, (b, title, costly, lam) in enumerate([(twin, 'synthetic service, 4 cores', 'share of the costly endpoints (Ep065–104)', 400),
                                                  (sn_twin, 'SocialNetwork post-storage, 1 core', 'share of ReadPosts (read-heavy mix)', 1000)]):
        a = axs[0, j]; a.plot(b['ts'], b['ramp'], color=C['obs'], lw=1.8); a.set_ylim(-0.05, 1.05)
        a.set_title(title, loc='left'); a.set_ylabel('costly share' if j == 0 else '')
        a.text(0.02, 0.9, costly, transform=a.transAxes, fontsize=9.5, color=C['grey'])
        a = axs[1, j]
        rho = b['rho_frac'] if 'rho_frac' in b else b['rho']
        a.plot(b['ts'], b['lambda'] / lam, color=C['grey'], lw=1.6, label=f'λ ÷ {lam} req/s (held fixed)')
        a.plot(b['ts'], rho / rho.iloc[:30].median(), color=C['new'], lw=1.8, label='ρ ÷ its starting value')
        a.set_xlabel('time (s)'); a.set_ylim(0.5, 2.2); a.legend(loc='upper left', fontsize=9)
        if j == 0: a.set_ylabel('relative to the start')
    fig.suptitle('the scenario: endpoint mix shifts at a fixed arrival rate; the offered work rises with it', x=0.01, y=1.0, ha='left', fontsize=11)
    save(fig, 'F0_mix_shift_scenario.png')

# ---- F1: the problem, four mixes by lambda --------------------------------------------------
def fig1():
    fig, ax = plt.subplots(figsize=(6.4, 3.6))
    for name, d, lab, K, col in MIXES:
        s = sweep(d, K_CORES)
        ax.plot(s['lambda'], s['W'].clip(lower=0.01), 'o-', ms=4, color=col, label=lab)
    ax.set_yscale('log'); ax.set_xlabel('arrival rate λ (req/s)'); ax.set_ylabel('wait above floor (ms)')
    ax.set_title('one service, four endpoint mixes, no neighbor', loc='left')
    ax.legend(title='profiling sweep', loc='lower right'); save(fig, 'F1_problem_lambda.png')

# ---- F1c: the same on a real service, two mixes by lambda (post-storage, k = 1, datastore calls blocked) --------
def app_knee(wl_path):
    """knee.py rule on hrgen's wlgen.jsonl: first step whose caller p50 exceeds twice the lowest step's, or whose
    served rate is below 95 % of the target, or that returns errors; the knee is the previous step's rate."""
    from collections import defaultdict
    steps, by = {}, defaultdict(list)
    for line in open(wl_path):
        r = json.loads(line)
        if r.get('event') == 'step' and r['step'] >= 0: steps[r['step']] = r['rate']
        elif 'event' not in r and r.get('step', -9) >= 0: by[r['step']].append(r)
    rows = []
    for st in sorted(by):
        rs = by[st][3:]
        if not rs or st not in steps: continue
        done = sum(r['all'][0] for r in rs) / len(rs); p50 = np.median([r['all'][1] for r in rs]) / 1e6
        errs = sum(sum(v[4] for v in r['ep'].values()) for r in rs)
        rows.append((steps[st], done, p50, errs))
    base = min(r[2] for r in rows); knee = rows[-1][0]
    for i, (rate, done, p50, errs) in enumerate(rows):
        if p50 > 2 * base or done < 0.95 * rate or errs > 0:
            knee = rows[i - 1][0] if i > 0 else rate; break
    return knee, rows

def fig1c():
    import glob
    runs = [('read-heavy mix (compose 5 %, home 80 %, utl 15 %)', 'sn/*-a3-read-heavy-k1c', C['mix1']),
            ('compose-heavy mix (compose 60 %, home 30 %, utl 10 %)', 'sn/*-a3-compose-heavy-k1c-cl14', C['mix2'])]
    data = []
    for lab, pat, col in runs:
        ds = sorted(glob.glob(p(pat)))
        if not ds or not os.path.exists(os.path.join(ds[-1], 'svc_curve.csv')): print('F1c: missing', pat); return
        s = sweep(os.path.relpath(ds[-1], ROOT), 1); knee, _ = app_knee(os.path.join(ds[-1], 'wlgen.jsonl'))
        data.append((lab, col, s, knee, os.path.basename(ds[-1])))
    floors = [d[2]['p50_ms'].iloc[0] for d in data]
    rel = abs(floors[0] - floors[1]) / max(floors)
    print(f'F1c floors: read-heavy {floors[0]:.3f} ms, compose-heavy {floors[1]:.3f} ms, relative difference {rel:.2f}')
    for lab, col, s, knee, name in data:
        fl = s['p50_ms'].iloc[0]; left = s[s['p50_ms'] > 1.5 * fl]
        print(f'  {name}: floor {fl:.3f} ms; application knee {knee} req/s; post-storage p50 > 1.5x floor first at '
              f'{int(left["rate_target"].iloc[0]) if len(left) else "no"} req/s; served steps to {int(s["rate_target"].iloc[-1])} req/s')
    if rel < 0.10: print('F1c: floors within 10 %, panel not drawn'); return
    fig, ax = plt.subplots(figsize=(6.4, 3.6))
    for i, (lab, col, s, knee, name) in enumerate(data):
        ax.plot(s['lambda'], s['p50_ms'], 'o-', ms=4, color=col, label=lab)
        ax.axhline(s['p50_ms'].iloc[0], color=col, lw=1, ls=':')
        ax.axvline(knee, color=col, lw=1, ls='--')
        ax.text(knee, 0.30 if i == 0 else 0.11, f'app knee {knee} req/s ', color=col, fontsize=8.5, va='center', ha='right')
    ax.set_yscale('log'); ax.set_xlabel('arrival rate λ (req/s)'); ax.set_ylabel('p50 latency (ms), log')
    ax.set_title('SocialNetwork post-storage, k = 1, two mixes, no neighbor', loc='left')
    ax.legend(title='profiling sweep (dotted: floor; dashed: application knee)', loc='center left'); save(fig, 'F1c_problem_lambda_poststorage.png')

# ---- F2b: the same failure on a real service (post-storage, floor-only, no neighbor) ----------
def fig2b():
    b = sn_twin
    fig, axs = plt.subplots(3, 1, figsize=(6.4, 6.0), sharex=True,
                            gridspec_kw={'height_ratios': [1, 2.2, 1.8], 'hspace': 0.12})
    a = axs[0]; a.plot(b['ts'], b['ramp'], color=C['grey']); a.set_ylabel('read-heavy share'); a.set_ylim(-0.05, 1.05)
    a.set_title('SocialNetwork post-storage: mix shift at 1000 req/s, no neighbor', loc='left')
    a = axs[1]
    a.plot(b['ts'], b['lat_ms'], color=C['obs'], lw=1.4, label='observed p50')
    a.plot(b['ts'], b['D_old_ms'], color=C['old'], lw=1.6, label='expected, D(λ)')
    a.fill_between(b['ts'], b['D_old_ms'], b['lat_ms'], where=b['lat_ms'] > b['D_old_ms'], color=C['shade'], label='gap read as contention')
    a.set_ylabel('latency (ms)'); a.legend(loc='upper left')
    a = axs[2]
    a.plot(b['ts'], b['ext_old'], color=C['old'], lw=1.6, label='score = (observed − D(λ)) ÷ observed')
    a.axhline(0.5, color=C['grey'], ls=':', label='extrinsic threshold')
    ext = b['ext_old'] >= 0.5
    a.fill_between(b['ts'], 0, 1, where=ext, color=C['shade'], step='mid', label=f'false alarm: labeled extrinsic, no neighbor present ({int(ext.sum())} of {len(b)} windows)')
    a.set_ylim(0, 1); a.set_ylabel('extrinsic share'); a.set_xlabel('time (s)'); a.legend(loc='upper left')
    save(fig, 'F2b_failure_lambda_poststorage.png')

# ---- F2: what the lambda-indexed scorer does with a mix shift (no neighbor) -------------------
def fig2():
    b = twin
    fig, axs = plt.subplots(3, 1, figsize=(6.4, 6.0), sharex=True,
                            gridspec_kw={'height_ratios': [1, 2.2, 1.8], 'hspace': 0.12})
    a = axs[0]; a.plot(b['ts'], b['ramp'], color=C['grey']); a.set_ylabel('costly share'); a.set_ylim(-0.05, 1.05)
    a.set_title('mix shift at fixed λ = 400 req/s, no neighbor', loc='left')
    a = axs[1]
    a.plot(b['ts'], b['lat_ms'], color=C['obs'], lw=1.4, label='observed p50')
    a.plot(b['ts'], b['D_old_ms'], color=C['old'], lw=1.6, label='expected, D(λ)')
    a.fill_between(b['ts'], b['D_old_ms'], b['lat_ms'], where=b['lat_ms'] > b['D_old_ms'], color=C['shade'], label='gap read as contention')
    a.set_ylabel('latency (ms)'); a.legend(loc='upper left')
    a = axs[2]
    a.plot(b['ts'], b['ext_old'], color=C['old'], lw=1.6, label='score = (observed − D(λ)) ÷ observed')
    a.axhline(0.5, color=C['grey'], ls=':', label='extrinsic threshold')
    ext = b['ext_old'] >= 0.5
    a.fill_between(b['ts'], 0, 1, where=ext, color=C['shade'], step='mid', label=f'false alarm: labeled extrinsic, no neighbor present ({int(ext.sum())} of {len(b)} windows)')
    a.set_ylim(0, 1); a.set_ylabel('extrinsic share'); a.set_xlabel('time (s)'); a.legend(loc='upper left')
    a.annotate('crosses 0.5 once the gap is half the latency', xy=(b['ts'][ext].iloc[0] if ext.any() else 90, 0.5), xytext=(8, 0.16), fontsize=9, color=C['grey'], arrowprops=dict(arrowstyle='->', color=C['grey']))
    save(fig, 'F2_failure_lambda.png')

# ---- F3: the collapse in three steps -----------------------------------------------------------
def fig3():
    fig, axs = plt.subplots(1, 3, figsize=(12.5, 3.6), sharey=False)
    for name, d, lab, K, col in MIXES:
        s = sweep(d, K_CORES); W = s['W'].clip(lower=0.01)
        axs[0].plot(s['lambda'], W, 'o-', ms=4, color=col, label=lab)
        axs[1].plot(s['rho_frac'], W, 'o-', ms=4, color=col, label=lab)
        axs[2].plot(s['rho_frac'], W / K, 'o-', ms=4, color=col, label=f'{lab.split(",")[0]}, K {K:.1f} ms')
    for a in axs: a.set_yscale('log')
    axs[0].set_xlabel('arrival rate λ (req/s)'); axs[0].set_ylabel('wait above floor W (ms)'); axs[0].set_title('a · indexed by λ', loc='left')
    axs[1].set_xlabel('offered utilization ρ'); axs[1].set_title('b · indexed by ρ', loc='left')
    axs[2].set_xlabel('offered utilization ρ'); axs[2].set_ylabel('W ÷ K'); axs[2].set_title('c · indexed by ρ, wait scaled by K', loc='left')
    for a in axs[1:]: a.axvline(0.65, color=C['grey'], ls=':', lw=1); a.text(0.655, a.get_ylim()[0]*1.5, 'knee', color=C['grey'], fontsize=9)
    axs[0].legend(loc='upper left'); axs[2].legend(loc='upper left')
    save(fig, 'F3_collapse_three_steps.png')

# ---- F4: h(rho) measured, synthetic and recommendation, vs the textbook load factor -------------
def fig4():
    fig, ax = plt.subplots(figsize=(6.4, 3.8))
    for dct, lab, col, k in [(syn_dict, 'synthetic service, k = 4 (cap 3)', C['mix2'], 4), (rec_dict, 'HR recommendation, k = 2 (cap 1)', C['mix1'], 2)]:
        rho = np.array(dct['rho']); h = np.array(dct['h']); rp = rho * k / (k - 1)
        m = h > 0
        ax.plot(rp[m], h[m], 'o-', ms=4, color=col, label=lab)
    x = np.linspace(0.05, 0.97, 100); ax.plot(x, x / (1 - x), ls='--', color=C['grey'], label='textbook ρ′ ÷ (1 − ρ′), single server')
    ax.set_yscale('log'); ax.set_xlabel("executing-slot utilization ρ′"); ax.set_ylabel('h(ρ) = W ÷ K'); ax.set_xlim(0, 1.05)
    ax.set_title('the load factor is measured per service, not assumed', loc='left'); ax.legend(loc='upper left')
    save(fig, 'F4_h_measured.png')

# ---- F5: the decomposition, schematic in the style of the lambda-indexed figure -----------------
def fig5():
    fig, ax = plt.subplots(figsize=(8.4, 5.2))
    x = np.linspace(0, 0.9, 200); h = 0.9 * x**2 / (1 - x)           # schematic load factor
    F1, K1 = 2.0, 2.0   # profiled mix
    F2, K2 = 3.2, 3.4   # online mix: costlier, lumpier
    D1 = F1 + h * K1; D2 = F2 + h * K2
    ax.plot(x, D1, color=C['grey'], lw=1.6, ls='--', label='profiled mix: F̄₁ + h(ρ)·K₁')
    ax.plot(x, D2, color=C['new'], lw=2.2, label="this window's mix: F̄₂ + h(ρ)·K₂")
    ax.axhline(F2, color=C['new'], lw=1, ls=':'); ax.axhline(F1, color=C['grey'], lw=1, ls=':')
    xo = 0.62; Do = F2 + np.interp(xo, x, h) * K2; Lat = Do + 3.6
    ax.plot([xo, xo], [0, Lat], color=C['grey'], lw=0.8, ls=':')
    ax.plot(xo, Lat, 'o', color=C['old'], ms=8); ax.plot(xo, Do, 'o', color=C['new'], ms=8)
    def brace(x0, y0, y1, label, col):
        ax.annotate('', xy=(x0, y1), xytext=(x0, y0), arrowprops=dict(arrowstyle='<->', color=col, lw=1.4))
        ax.text(x0 + 0.012, (y0 + y1) / 2, label, va='center', color=col, fontsize=10.5, bbox=dict(fc='white', ec='none', pad=1.5))
    brace(xo + 0.03, Do, Lat, 'extrinsic: Lat − D\n(a neighbor)', C['old'])
    brace(xo + 0.03, F2, Do, 'intrinsic wait: h(ρ)·K\n(own queue)', C['new'])
    brace(xo + 0.03, 0, F2, 'floor F̄ (this mix, zero wait)', C['grey'])
    ax.annotate('observed Lat', xy=(xo, Lat), xytext=(0.32, Lat + 0.6), color=C['old'], fontsize=10.5,
                arrowprops=dict(arrowstyle='->', color=C['old']))
    ax.annotate('expected D[ρ; mix]', xy=(xo, Do), xytext=(0.22, Do + 1.2), color=C['new'], fontsize=10.5,
                arrowprops=dict(arrowstyle='->', color=C['new']))
    ax.set_xticks([0, 0.2, 0.4, xo, 0.8]); ax.set_xticklabels(['0', '0.2', '0.4', 'ρ now', '0.8'])
    ax.text(0.02, 12.6, 'offline, once:  h(ρ) from one sweep;  FLₑ(q), cₑ per endpoint', color=C['grey'], fontsize=10)
    ax.text(0.02, 11.7, 'online, per window:  %ₑ, λ → ρ, F̄, K;  then D = F̄ + h(ρ)·K', color=C['new'], fontsize=10)
    ax.set_xlim(0, 0.95); ax.set_ylim(0, 13.5); ax.set_xlabel('offered utilization ρ'); ax.set_ylabel('latency (ms)')
    ax.set_yticks([]); ax.grid(False); ax.legend(loc='upper left', bbox_to_anchor=(0.0, 0.84))
    ax.set_title('one curve, moved by the mix: where the online latency is split', loc='left')
    save(fig, 'F5_decomposition_schematic.png')

# ---- F6: before vs after, same run, side by side -----------------------------------------------
def before_after(b, name, title, setup, tcol='lat_twin_ms'):
    b = b.copy(); b['truth_s'] = b['truth_ext'].rolling(5, center=True, min_periods=1).median()
    fig, axs = plt.subplots(3, 2, figsize=(12.5, 7.0), sharex=True,
                            gridspec_kw={'height_ratios': [2.2, 1.8, 0.7], 'hspace': 0.12, 'wspace': 0.08})
    for j, (lab, D, ext, cell, col) in enumerate([('indexed by λ', 'D_old_ms', 'ext_old', 'cell_old', C['old']),
                                                   ('indexed by ρ, mix-corrected', 'D_mix_ms', 'ext_mix', 'cell_mix', C['new'])]):
        a = axs[0, j]
        a.plot(b['ts'], b['lat_ms'], color=C['obs'], lw=1.3, label='observed p50')
        a.plot(b['ts'], b[D], color=col, lw=1.8, label='expected, ' + ('D(λ)' if j == 0 else 'D[ρ; mix]'))
        if tcol in b: a.plot(b['ts'], b[tcol], color=C['truth'], lw=1.2, ls='--', label='twin run: same ramp, no aggressor (truth baseline)')
        a.set_title(lab, loc='left'); a.legend(loc='upper left'); a.set_ylim(0, np.nanpercentile(b['lat_ms'], 98) * 1.15)
        if j == 0: a.set_ylabel('latency (ms)')
        a = axs[1, j]
        a.plot(b['ts'], b[ext], color=col, lw=1.8, label='score')
        a.plot(b['ts'], b['truth_s'], color=C['truth'], lw=2.0, ls='--', label='truth = (observed − twin) ÷ observed, 5 s median', zorder=5)
        a.axhline(0.5, color=C['grey'], ls=':'); a.set_ylim(0, 1)
        if j == 0: a.set_ylabel('extrinsic share')
        a.legend(loc='upper left')
        a = axs[2, j]
        isext = b[cell].astype(str).str.endswith('/ext'); isna = b[cell].astype(str).str.endswith('n-a')
        tr = b['truth_s'] >= 0.5
        a.fill_between(b['ts'], 1.1, 1.9, where=tr, color=C['truth'], step='mid', alpha=0.8)
        a.fill_between(b['ts'], 0.1, 0.9, where=isext, color=col, step='mid', alpha=0.9)
        a.fill_between(b['ts'], 0.1, 0.9, where=isna, color=C['floor'], step='mid')
        a.set_yticks([0.5, 1.5]); a.set_yticklabels(['scorer: ext', 'truth: ext']); a.set_ylim(0, 2); a.grid(False)
        a.set_xlabel('time (s)')
        if j == 1: a.text(b['ts'].iloc[-1], 0.5, '  grey = abstained\n  (guard: wait beyond\n  the profiled range)', va='center', fontsize=8.5, color=C['grey'])
    fig.suptitle(title, x=0.01, y=0.985, ha='left', fontsize=12)
    import textwrap; fig.text(0.01, 0.885, '\n'.join(textwrap.wrap(setup, 150)), ha='left', va='bottom', fontsize=9.5, color=C['grey'])
    fig.subplots_adjust(top=0.84)
    save(fig, name)

def fig6():
    before_after(stress, 'F6_before_after_synthetic.png', 'synthetic service, mix shift at 400 req/s with one aggressor',
                 'setup: the scenario is run twice, once with a CPU aggressor on one of the service’s four cores (scored here) and once without it (the twin). '
                 'Expected verdict: intrinsic until the aggressor’s share of the latency passes one half, then extrinsic; never extrinsic for the mix alone.')
    before_after(sn_str, 'F6b_before_after_poststorage.png', 'SocialNetwork post-storage, mix shift at 1000 req/s with one aggressor',
                 'setup: same scenario twice, with and without a CPU aggressor on the service’s core (the twin). The aggressor never reaches the p50 here, '
                 'so the truth sits at zero (green dashed, on the axis) and the expected verdict is intrinsic throughout: the 7× rise is the mix, not a neighbor.')

# ---- F7: the guard, with and without, per load step -------------------------------------------
def fig7():
    r = rec2.copy()
    offered = {0: 1000, 1: 2000, 2: 4000, 3: 6000, 4: 8000, 5: 10000, 6: 12000, 7: 14000, 8: 16000}
    cbar, slots = 0.080e-3, 1
    r['rp'] = r['step'].map(offered) * cbar / slots
    slow = r['y'] >= 0.5
    def cells(ext, guard=None):
        c = np.where(slow, 'slow', 'fast').astype(object)
        o = np.where(ext >= 0.5, 'ext', 'int').astype(object)
        out = np.char.add(np.char.add(c.astype(str), '/'), o.astype(str)).astype(object)
        if guard is not None: out[guard] = 'n/a'
        return pd.Series(out, index=r.index)
    r['truth_cell'] = np.where(r['truth_ext'].isna(), 'undefined', cells(r['truth_ext'].fillna(0)))
    ng = ((r['lat_ms'] - r['D_mix_ms']) / r['lat_ms']).clip(0, 1).rolling(5, center=True, min_periods=1).median()
    r['noguard'] = cells(ng)
    r['guard'] = cells(r['ext_mix'].fillna(0), r['cell_mix'].astype(str).str.endswith('n-a').values)
    r['old'] = cells(r['ext_old'])
    rows = [('truth', 'truth_cell'), ('D(λ), no guard', 'old'), ('D[ρ; mix], no guard', 'noguard'), ('D[ρ; mix], with guard', 'guard')]
    cats = ['fast/int', 'slow/int', 'slow/ext', 'fast/ext', 'n/a', 'undefined']
    cols = {'fast/int': '#D5E4EE', 'slow/int': C['new'], 'slow/ext': C['old'], 'fast/ext': '#F2B8A4', 'n/a': '#8F8E87', 'undefined': '#EEEDE8'}
    names = {'fast/int': 'fast/int', 'slow/int': 'slow/int', 'slow/ext': 'slow/ext', 'fast/ext': 'fast/ext', 'n/a': 'slow / n/a: guard abstained', 'undefined': 'truth undefined (served < 95 %)'}
    steps = [s_ for s_ in sorted(r['step'].unique()) if offered[s_] >= 8000]; xs = [offered[s_] * cbar / slots for s_ in steps]
    fig, axs = plt.subplots(len(rows), 1, figsize=(8.0, 5.2), sharex=True, gridspec_kw={'hspace': 0.35})
    for a in axs: a.set_xlim(0.5, 1.4)
    for a, (lab, col) in zip(axs, rows):
        for s_, x in zip(steps, xs):
            sub = r[r['step'] == s_][col].value_counts(); n = sub.sum(); bottom = 0
            for cat in cats:
                v = sub.get(cat, 0) / n
                if v > 0:
                    a.bar(x, v, bottom=bottom, width=0.09, color=cols[cat], edgecolor='white', linewidth=0.5, hatch=('//' if cat == 'undefined' else None)); bottom += v
        a.set_ylim(0, 1); a.set_yticks([]); a.set_ylabel(lab, rotation=0, ha='right', va='center', fontsize=10); a.grid(False)
        a.axvspan(14000 * cbar - 0.04, 16000 * cbar + 0.04, color='#F6F5F1', zorder=0)
    axs[-1].set_xlabel("offered executing-slot utilization ρ′, per load step"); axs[-1].set_xticks(xs); axs[-1].set_xticklabels([f'{x:.2f}' for x in xs])
    from matplotlib.patches import Patch
    axs[0].legend(handles=[Patch(facecolor=cols[c], label=names[c], hatch=('//' if c == 'undefined' else None)) for c in cats if c != 'fast/ext'], loc='upper left', bbox_to_anchor=(0, 1.75), ncol=5, fontsize=8.5, frameon=False)
    fig.suptitle('HR recommendation, one CPU aggressor per core, load stepped past the knee: share of windows per cell', x=0.01, y=1.04, ha='left', fontsize=11)
    fig.text(0.01, -0.03, 'expected: intrinsic at 0.64 and 0.8 (the aggressors cut capacity, not the wait), extrinsic at 0.96 (wait 4.9×), no verdict possible under collapse; steps below 0.64 are all fast/int for every row and are omitted', fontsize=8.5, color=C['grey'])
    save(fig, 'F7_guard_recommendation.png')

import traceback
for f in (fig0, fig1, fig1c, fig2, fig2b, fig3, fig4, fig5, fig6, fig7):
    try: f()
    except SystemExit: raise
    except Exception as ex: print(f.__name__, 'skipped:', ex)
