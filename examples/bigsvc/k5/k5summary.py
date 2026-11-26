#!/usr/bin/env python3
"""Phase K5: one line per arm record of a cell.jsonl (the queue's test phase and quick looks)."""
import json, sys
for l in open(sys.argv[1]):
    d = json.loads(l)
    if d["event"] == "arm":
        ag = d.get("agent_self") or {}
        print(f"  arm {d['arm']} r{d['rep']}: {d['dur_ms']} ms, victim {d['victim_us']} us, aggr {d['aggr_us']} us, rec bytes {d['rec_bytes']}, "
              f"agent cpu {ag.get('cpu_ns')} ns progs {list(ag.get('progs', {}).items())} lost {ag.get('lost')}, scorer {d['scorer_self']}, pids {d['pids_ns']}, "
              f"NET_RX {[d['sirq'].get('NET_RX', {}).get(c) for c in list(d['sirq'].get('NET_RX', {}))[:3]]}")
    elif d["event"] in ("error", "neighbor", "end", "stopped"):
        print("  ", json.dumps(d)[:400])
