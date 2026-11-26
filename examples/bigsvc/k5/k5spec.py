#!/usr/bin/env python3
"""Phase K5: writes a cell spec for k5ctl.py from the arm catalog (RESULTS-K 5). Arms by name; "reads" arms depend on
the service (Go: runtime/metrics q and g; C++: cpu and mhz).
usage: k5spec.py --cell NAME --out SPEC.json --svc uid|rec|syn --pod-uid UID --cpus 1 --sib 29 --k 1 --slots 1 [--go]
         --ctl PATH --recorder PATH --dict PATH --ctl-out DIR --nb none|switch|sib|cache|net [--nb-cpus CPUS] [--nb-n N]
         --arms none,full --reps 5 --measure 30 [--settle 3] [--holder NAME=CG_OR_POD_UID ...]
         [--replay-recorder F --replay-agent F] [--reader-cpu 53] [--bin /var/tmp/k5]
"""
import argparse, json

ap = argparse.ArgumentParser()
for k in ("cell", "out", "svc", "pod_uid", "cpus", "ctl", "recorder", "dict", "ctl_out", "arms"): ap.add_argument("--" + k.replace("_", "-"), required=(k in ("cell", "out", "svc", "pod_uid", "cpus", "ctl_out", "arms")))
ap.add_argument("--sib", default=""); ap.add_argument("--k", type=int, default=1); ap.add_argument("--slots", type=int, default=1); ap.add_argument("--go", action="store_true")
ap.add_argument("--nb", default="none"); ap.add_argument("--nb-cpus", default=""); ap.add_argument("--nb-n", type=int, default=0)
ap.add_argument("--reps", type=int, default=1); ap.add_argument("--measure", type=int, default=300); ap.add_argument("--settle", type=int, default=3); ap.add_argument("--gap", type=int, default=2)
ap.add_argument("--holder", action="append", default=[]); ap.add_argument("--replay-recorder"); ap.add_argument("--replay-agent")
ap.add_argument("--reader-cpu", type=int, default=53); ap.add_argument("--bin", default="/var/tmp/k5")
ap.add_argument("--sirq-base", type=float, default=None, help="step 8: the victim CPUs' quiet softirq share for the scorer's row 8")
a = ap.parse_args()

GO = a.go
READS_ALL = "cpu,mhz,q,g" if GO else "cpu,mhz"
LEAN = dict(probe="switch", rq=1)
ALLFLAGS = dict(probe="switch,wakeup,softirq", rq=1, sib=1, timer=1)
BP_AGENT = dict(probe="switch", rq=0, rd=1, sib=1, resctrl=1, resctrl_read=1, sampler=1)   # set B' (5.8), the agent part
BP = dict(intercept="full", reads=("q,g" if GO else "cpu,mhz"), agent=BP_AGENT, scorer="on")
RDT_NBR = ",".join(["aggr"] + [h.partition("=")[0] for h in a.holder])   # every holder's cgroup, not the victim's
CAT = {
    # rung 0 / the paired twin
    "none": dict(intercept="off", reads="none"),
    # rung 1: production mode
    "full": dict(intercept="full", reads=READS_ALL, agent=dict(LEAN, sampler=1), scorer="on"),
    # rung 2: one source at a time
    "probe": dict(agent=dict(LEAN)),
    "reads": dict(reads=("q,g" if GO else "cpu,mhz")),
    "sampler": dict(agent=dict(sampler=1)),
    "intercept": dict(intercept="full"),
    "scorer-replay": dict(scorer="replay"),
    "perf-armed": dict(agent=dict(perf=1, perf_read=0)),
    "rdt-armed": dict(agent=dict(resctrl=1, resctrl_read=0)),
    "sirq": dict(agent=dict(probe="softirq")),
    # rung 3: inside the probe, cumulative ("probe" is the second step: phi + run_delay)
    "phi": dict(agent=dict(probe="switch", rq=0)),
    "sib": dict(agent=dict(LEAN, sib=1)),
    "wake": dict(agent=dict(probe="switch,wakeup", rq=1, sib=1)),
    "wsirq": dict(agent=dict(probe="switch,wakeup,softirq", rq=1, sib=1)),
    "timer": dict(agent=dict(ALLFLAGS)),
    # rung 4: per signal elsewhere
    "q": dict(reads="q"), "g": dict(reads="g"), "cpu": dict(reads="cpu"), "mhz": dict(reads="mhz"),
    "count": dict(intercept="count"), "slot": dict(intercept="slot"),
    "perf-read": dict(agent=dict(perf=1, perf_read=1)), "rdt-read": dict(agent=dict(resctrl=1, resctrl_read=1)),
    # §5.7: default set B (probe with phi and phi_sibling, no holder split; CMT; interceptor count + slot; the window
    # reads; sampler; scorer on-node, which also sums the victim threads' run_delay from /proc), and B + the timers
    # (as measured in 5.7 the scorer also summed run_delay from /proc; that read is gone, see defBp)
    "defB": dict(intercept="count,slot", reads=("q,g" if GO else "cpu,mhz"), agent=dict(probe="switch", rq=0, sib=1, resctrl=1, resctrl_read=1, sampler=1), scorer="on"),
    "defBt": dict(intercept="full", reads=("q,g" if GO else "cpu,mhz"), agent=dict(probe="switch", rq=0, sib=1, resctrl=1, resctrl_read=1, sampler=1), scorer="on"),
    # 5.8: set B' = B + the sampled histogram timers (the recorder's production mode) + run_delay in the probe (F_RD)
    "defBp": dict(intercept="full", reads=("q,g" if GO else "cpu,mhz"), agent=dict(probe="switch", rq=0, rd=1, sib=1, resctrl=1, resctrl_read=1, sampler=1), scorer="on"),
    # Phase I (RESULTS-I 2.2): set B''s probe alone (phi, phi_sibling, run_delay; no holder split), the K1-style cell
    "probeBp": dict(agent=dict(probe="switch", rq=0, rd=1, sib=1)),
    # Phase I 4 (stop-point-2 decision 1): B' minus one part at a time, and the two fixes. "fixedReads": the C++
    # recorder reads cpu only (its mhz read falls back to /proc/cpuinfo, ~3 ms per window on 144 CPUs) and the agent
    # reads the victim CPUs' APERF/MPERF; Go's reads (q,g) had no mhz. "rdtNbr": monitor groups for the neighbors only,
    # the victim stays in the default group (no RMID write on its switches)
    "Bp-noRdt": dict(BP, agent=dict(BP_AGENT, resctrl=0, resctrl_read=0)),
    "Bp-noTimers": dict(BP, intercept="count,slot"),
    "Bp-noReads": dict(BP, reads="none"),
    "Bp-noSampler": dict(BP, agent=dict(BP_AGENT, sampler=0)),
    "Bp-noScorer": dict(BP, scorer="off"),
    "Bp-fixedReads": dict(BP, reads=("q,g" if GO else "cpu"), agent=dict(BP_AGENT, mhz_cpus="victim")),
    "Bp-rdtNbr": dict(BP, agent=dict(BP_AGENT, resctrl_cg=RDT_NBR)),
    "Bp-fix2": dict(BP, reads=("q,g" if GO else "cpu"), agent=dict(BP_AGENT, mhz_cpus="victim", resctrl_cg=RDT_NBR)),
    # the dictionary sweep and K6: everything that reads, scorer offline
    "sweep": dict(intercept="full", reads=READS_ALL, agent=dict(LEAN, perf=1, perf_read=1, resctrl=1, resctrl_read=1, sampler=1)),
    # Phase I 4: the sweep without the recorder's mhz read (the agent reads the victim CPUs' APERF/MPERF)
    "sweepF": dict(intercept="full", reads=("cpu,q,g" if GO else "cpu"), agent=dict(LEAN, perf=1, perf_read=1, resctrl=1, resctrl_read=1, sampler=1, mhz_cpus="victim")),
    "all": dict(intercept="full", reads=READS_ALL, agent=dict(ALLFLAGS, perf=1, perf_read=1, resctrl=1, resctrl_read=1, sampler=1)),
    # Phase I step 4: K6's "all" without the recorder's clock read (the agent reads APERF/MPERF), with the stall counters
    "sigF": dict(intercept="full", reads=("cpu,q,g" if GO else "cpu"), agent=dict(ALLFLAGS, perf=1, perf_read=1, perf_td=1, resctrl=1, resctrl_read=1, sampler=1, mhz_cpus="victim")),
}
# Phase I step 5 (RESULTS-I 6): sigF with a mitigation on the resctrl ctrl groups k5ctl makes per arm: CAT (the victim's
# own ways, the neighbor the rest) and MBA (the neighbor's domain throttled)
for _w in (4, 6): CAT[f"cat{_w}"] = dict(CAT["sigF"], mitig=dict(cat_ways=_w))
for _p in (90, 50, 30, 10): CAT[f"mba{_p}"] = dict(CAT["sigF"], mitig=dict(mba_pct=_p))
# Phase I step 8 (RESULTS-I 9): the corrected production set plus the probe's softirq programs (the contention-grade C2 cell)
CAT["Bp-sirq"] = dict(CAT["Bp-fixedReads"], agent=dict(BP_AGENT, mhz_cpus="victim", probe="switch,softirq"))
# Phase I step 7 (RESULTS-I 8): sigF with the scorer on-node (rows per window beside every counter column)
CAT["sigS"] = dict(CAT["sigF"], scorer="on")
# the two-step policy test (RESULTS-I 6.1): the victim's own 6 ways and the neighbor throttled to 30 % together
CAT["cat6mba30"] = dict(CAT["sigF"], mitig=dict(cat_ways=6, mba_pct=30))
# Phase I step 6 (RESULTS-I 7): the escalation arm. The corrected production set B' (Bp-fixedReads) runs throughout; while
# the on-node scorer flags a window (I_g > 1 + i_far or E >= e_far) k5ctl runs a second agent with the diagnostic programs
# (softirq programs, perf counters with the stall columns, the holder split) and stops it hold_s seconds after the last flag.
CAT["Bp-esc"] = dict(CAT["Bp-fixedReads"], escalate=dict(agent=dict(probe="switch,softirq", rq=1, sib=0, perf=1, perf_read=1, perf_td=1, resctrl=0, sampler=0),
                                                        on=dict(i_far=0.15, e_far=0.15), hold_s=5))
# Phase J1 (RESULTS-J 1): the unpinned ladder on the r650 at the production clock. The probe runs cgroup-filtered on every CPU
# (--cgf), rows 1-4 cumulative; the interceptor rows 5-8 use the per-thread recorder (sn-gordion:g9; "sample" = 1 is every
# request timed, 8 the production 1-in-8); perf is the cgroup group on every CPU with ref-cycles and LLC references (8 events);
# the clock comes from per-CPU cycles / ref-cycles events on all CPUs (--clock-cpus all; msr retired); "j-reads" is the
# sampler plus the recorder's window reads; "j-scorer" the scorer alone replaying recorded inputs; "j-all" everything at once.
J1P = dict(cgf=1, rd=1, rq=0, timer=1)
J1_READS = "q,g" if GO else "cpu"
J1_ALL_AGENT = dict(J1P, probe="switch,wakeup,softirq", sib=1, hold=1, perf=1, perf_read=1, perf_td=1, perf_ref=1, resctrl=1, resctrl_read=1, sampler=1, clock_cpus="all")
CAT.update({
    "j-probe": dict(agent=dict(J1P, probe="switch", sib=0, hold=0)),                               # 1. run_delay, on-CPU per CPU, filtered
    "j-holder": dict(agent=dict(J1P, probe="switch", sib=1, hold=1)),                              # 2. + the holder state on every switch (phi_sibling)
    "j-wake": dict(agent=dict(J1P, probe="switch,wakeup", sib=1, hold=1)),                         # 3. + wake-up programs
    "j-sirq": dict(agent=dict(J1P, probe="switch,wakeup,softirq", sib=1, hold=1)),                 # 4. + softirq programs
    "j-count": dict(intercept="count"),                                                            # 5. interceptor count only
    "j-slot": dict(intercept="count,slot"),                                                        # 6. + slot
    "j-full": dict(intercept="full", sample=1),                                                    # 7. + timers on every request
    "j-s8": dict(intercept="full", sample=8),                                                      # 8. 1-in-8 timers instead
    "j-perf": dict(agent=dict(perf=1, perf_read=1, perf_td=1, perf_ref=1)),                        # 9. perf cgroup group, 8 events, every CPU
    "j-rdt": dict(agent=dict(resctrl=1, resctrl_read=0)),                                          # 10. resctrl CMT/MBM armed
    "j-clock": dict(agent=dict(clock_cpus="all")),                                                 # 11. per-CPU clock events, all CPUs
    "j-reads": dict(reads=J1_READS, agent=dict(sampler=1)),                                        # 12. sampler + the recorder's window reads
    "j-scorer": dict(scorer="replay"),                                                             # 13. scorer alone (replay of recorded inputs)
    "j-all": dict(intercept="full", sample=1, reads=J1_READS, agent=dict(J1_ALL_AGENT), scorer="on"),      # 14. all on, every request timed
    "j-all-s8": dict(intercept="full", sample=8, reads=J1_READS, agent=dict(J1_ALL_AGENT), scorer="on"),   # 14'. all on, 1-in-8 timers
    "Bp-J": dict(BP, reads=J1_READS, agent=dict(BP_AGENT, timer=1)),                               # 15. Phase I's set B' as it runs unpinned (mask = every CPU, no cgroup filter; the msr clock read retired)
})
arms = []
for n in a.arms.split(","):
    if n not in CAT: raise SystemExit(f"unknown arm {n}")
    d = dict(name=n, intercept="off", reads="none", agent=None, scorer="off", mitig=None, escalate=None); d.update(CAT[n]); arms.append(d)
# the neighbor cgroup's CPUs for the agent's per-CPU counters: the victim's, their siblings and the neighbor's own (step 5
# reads the neighbor's progress as its instructions per second; in step 4 the stream's CPUs were not in this list)
_hc = []
for _c in (a.cpus.split(",") + (a.sib.split(",") if a.sib else []) + (a.nb_cpus.split(",") if a.nb_cpus else [])):
    if _c and _c not in _hc: _hc.append(_c)
holders = [dict(name="aggr", cg="/sys/fs/cgroup/gordion-aggr", cpus=",".join(_hc))]
for h in a.holder:
    name, _, v = h.partition("=")
    holders.append(dict(name=name, pod_uid=v) if "/" not in v else dict(name=name, cg=v))
spec = dict(cell=a.cell, out=a.ctl_out, bin=a.bin, reader_cpu=a.reader_cpu,
            victim=dict(name=a.svc, pod_uid=a.pod_uid, cpus=a.cpus, sib=a.sib, k=a.k, slots=a.slots, go=GO, ctl=a.ctl, recorder=a.recorder, dict=a.dict),
            holders=holders, nb=dict(kind=a.nb, cpus=a.nb_cpus, n=a.nb_n or None), arms=arms, reps=a.reps, measure=a.measure, settle=a.settle, gap=a.gap, sirq_base=a.sirq_base,
            replay=(dict(recorder=a.replay_recorder, agent=a.replay_agent) if a.replay_recorder else None))
json.dump(spec, open(a.out, "w"), indent=1)
print(f"{a.cell}: {len(arms)} arms x {a.reps} reps x {a.measure} s, nb {a.nb}")
