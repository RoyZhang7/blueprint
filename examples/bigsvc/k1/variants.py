#!/usr/bin/env python3
"""Phase K1: generate the bpftrace program variants from the probe template (probe/gordion_sched.bt.tmpl, v5) without
changing any block's logic. Writes <out>/<name>.bt.tmpl for probe.py --tmpl:

  full_switch / full_wakeup / full_both  -- the "full" program of the K1 spec: occupancy per cgroup, run-queue delay by
      holder, waker class, wake-to-run delay, the per-window export (interval:ms:100 + interval:s:1), sched_process_exit
      pruning. Taken from v5 minus the Addendum F additions (per-resume printf, softirq hooks, the softirq-wake-up branch
      and the @wk bookkeeping) and minus the futex syscall hooks (not part of the spec's full program). sched_wakeup_new
      is attached to the wake-up block beside sched_wakeup (the spec's hook set). Blocks not in the hook set are dropped;
      maps that the kept blocks read but no kept block writes are declared by a never-taken assignment in the 100 ms
      block (bpftrace 0.14 rejects undefined maps), and the export prints only the fields the kept blocks produce.
  empty_switch / empty_wakeup / empty_both -- the same attach points with an empty body.
  v5 -- the Addendum F probe as it stands (with per-resume export, futex and softirq hooks), for reference.

usage: variants.py --tmpl probe/gordion_sched.bt.tmpl --out k1/progs
"""
import argparse, os, re

ap = argparse.ArgumentParser(); ap.add_argument("--tmpl", required=True); ap.add_argument("--out", required=True)
a = ap.parse_args()
src = open(a.tmpl, encoding="utf-8").read()
os.makedirs(a.out, exist_ok=True)
BS = chr(92)

# split into top-level blocks: header line + body up to a "}" at column 0 (or a one-line block)
lines = src.split("\n"); blocks = []; i = 0
while i < len(lines):
    l = lines[i]
    if not l.strip() or l.startswith("//"):
        i += 1; continue
    if l.rstrip().endswith("}") and "{" in l:
        blocks.append((l.split("{")[0].strip(), l)); i += 1; continue
    j = i + 1
    while j < len(lines) and lines[j] != "}": j += 1
    blocks.append((l.strip(), "\n".join(lines[i:j + 1]))); i = j + 1


def kind(h):
    if h.startswith("tracepoint:sched:sched_switch") and "__SIBFILTER__" in h: return "switch_sib"
    if h.startswith("tracepoint:sched:sched_switch"): return "switch"
    if h.startswith("tracepoint:sched:sched_wakeup"): return "wakeup"
    if h.startswith("tracepoint:sched:sched_process_exit"): return "exit"
    if h.startswith("tracepoint:irq:"): return "softirq"
    if h.startswith("tracepoint:syscalls:"): return "futex"
    if h.startswith("interval:ms:100"): return "win"
    if h.startswith("interval:s:1"): return "sec"
    return "other:" + h


B = {}
for h, body in blocks: B.setdefault(kind(h), []).append(body)
assert set(B) >= {"switch", "switch_sib", "wakeup", "exit", "win", "sec"}, sorted(B)


def strip_f(body):
    """remove the Addendum F additions from a block (per-resume printf, @wk, the softirq wake-up branch)."""
    out = []; skip = 0
    for l in body.split("\n"):
        s = l.strip()
        if skip:
            skip += s.count("{") - s.count("}")
            continue
        if s.startswith('printf("{' + BS + '"r' + BS + '"') or s.startswith('printf("{' + BS + '"k' + BS + '"'): continue
        if s.startswith("if (@sev[cpu] != 0) {"):
            skip = 1; continue
        l = l.replace(" @wk[$pp] = 0;", "").replace(" @wk[args->pid] = 1;", "").replace(" delete(@wk[$x]);", "")
        out.append(l)
    return "\n".join(out)


switch = strip_f(B["switch"][0]); switch_sib = B["switch_sib"][0]; exitb = strip_f(B["exit"][0]); sec = B["sec"][0]
wakeup = strip_f(B["wakeup"][0]).replace("tracepoint:sched:sched_wakeup", "tracepoint:sched:sched_wakeup,\ntracepoint:sched:sched_wakeup_new", 1)
# the spec's full program has no futex tracking: @infutex is never set, so the futex branch of the switch block never
# fires and every sleep is "other" (as on the Go services, where futex sleeps are parked Ms anyway)

Q1 = ["Rself:@R[0]", "Raggr:@R[1]", "Rroot:@R[2]", "Rother:@R[3]", "Lself:@RL[0]", "Laggr:@RL[1]", "Lroot:@RL[2]", "Lother:@RL[3]",
      "nself:@Rn[0]", "naggr:@Rn[1]", "nroot:@Rn[2]", "nother:@Rn[3]", "preempt:@preempt", "multi:@multi", "mig:@mig",
      "Oself:@occw[0]", "Oaggr:@occw[1]", "Oroot:@occw[2]", "Oother:@occw[3]", "Oidle:@occw[4]", "Sself:@occs[0]", "Saggr:@occs[1]", "Sroot:@occs[2]", "Sother:@occs[3]", "Sidle:@occs[4]"]
Q2 = ["Qlock:@Qlock", "Qlockn:@Qlockn", "Qidle:@Qidle", "Qidlen:@Qidlen"] + [f"Wf{c}:@Wf[{c}]" for c in range(5)] + [f"Wo{c}:@Wo[{c}]" for c in range(5)] + ["Wfn0:@Wfn[0]", "Wfn4:@Wfn[4]", "Won0:@Won[0]", "Won4:@Won[4]"]
WIN_ZERO_MAPS = ["R", "RL", "Rn", "occw", "occs", "Wf", "Wo", "Wfn", "Won"]
SCALARS = ["Qlock", "Qlockn", "Qidle", "Qidlen", "preempt", "multi", "mig"]


def maps_written(text):
    w = set(re.findall(r"@(\w+)(?:\[[^\]]*\])?\s*(?:\+=|=(?!=))", text))
    return w


def maps_read(text):
    return set(re.findall(r"@(\w+)", text))


def win_block(kept_text):
    wr = maps_written(kept_text) | set(SCALARS)        # the scalars are assigned (= 0) in the window block itself
    def avail(expr):
        m = re.match(r"@(\w+)", expr); return m.group(1) in wr
    q1 = [f for f in Q1 if avail(f.split(":")[1])]; q2 = [f for f in Q2 if avail(f.split(":")[1])]
    def pf(tag, fields):
        fmt = "{" + BS + '"' + tag + BS + '":%llu' + "".join("," + BS + '"' + f.split(":")[0] + BS + '":%llu' for f in fields) + "}" + BS + "n"
        args = ", ".join(["@wseq"] + [f.split(":")[1] for f in fields])
        return f'  printf("{fmt}",\n         {args});'
    first = pf("q", q1).replace('":%llu', '":%llu,' + BS + '"ns' + BS + '":%llu', 1).replace("@wseq,", "@wseq, nsecs,", 1)
    zero = " ".join(f"zero(@{m});" for m in WIN_ZERO_MAPS if m in wr)
    body = ["interval:ms:100", "{", "  @wseq += 1;", first, pf("q2", q2), f"  {zero} " + " ".join(f"@{s} = 0;" for s in SCALARS)]
    # declare maps that kept blocks read but never write (bpftrace 0.14: "Undefined map"); never taken (@wseq is never 0 here)
    missing = sorted((maps_read(kept_text) - maps_written(kept_text)) - set(SCALARS) - {"wseq"})
    for m in missing:
        keyed = re.search(r"@" + m + r"\[", kept_text) is not None
        body.append(f"  if (@wseq == 0) {{ @{m}{'[0]' if keyed else ''} = 0; }}")
    body.append("}")
    return "\n".join(body), missing


HDR = "// Gordion Phase K1 variant {name} of probe v5 (generated by k1/variants.py; block logic unchanged, see its docstring)\n"
variants = {"full_switch": [switch, switch_sib, exitb], "full_wakeup": [wakeup, exitb], "full_both": [switch, switch_sib, wakeup, exitb]}
report = {}
for name, kept in variants.items():
    kt = "\n".join(kept) + "\n" + sec
    wb, missing = win_block(kt)
    text = HDR.format(name=name) + "\n\n".join(kept + [wb, sec]) + "\n"
    open(os.path.join(a.out, name + ".bt.tmpl"), "w", encoding="utf-8", newline="\n").write(text); report[name] = missing
EMPTY = {"empty_switch": ["tracepoint:sched:sched_switch { }"], "empty_wakeup": ["tracepoint:sched:sched_wakeup, tracepoint:sched:sched_wakeup_new { }"],
         "empty_both": ["tracepoint:sched:sched_switch { }", "tracepoint:sched:sched_wakeup, tracepoint:sched:sched_wakeup_new { }"]}
for name, kept in EMPTY.items():
    open(os.path.join(a.out, name + ".bt.tmpl"), "w", encoding="utf-8", newline="\n").write(HDR.format(name=name) + "\n".join(kept) + "\n")
open(os.path.join(a.out, "v5.bt.tmpl"), "w", encoding="utf-8", newline="\n").write(src)
for k, v in report.items(): print(f"{k}: declared-only maps {v}")
print("wrote", sorted(os.listdir(a.out)))
