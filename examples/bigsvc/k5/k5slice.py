#!/usr/bin/env python3
"""Phase K5: copy the recorder's windows between two byte offsets (an arm's rec_bytes) into a file, whole lines only.
usage: k5slice.py RECORDER B0 B1 OUT"""
import sys
rec, b0, b1, out = sys.argv[1], int(sys.argv[2]), int(sys.argv[3]), sys.argv[4]
with open(rec, "rb") as f:
    f.seek(b0); d = f.read(b1 - b0)
if b0 > 0:
    i = d.find(b"\n"); d = d[i + 1:] if i >= 0 else b""
j = d.rfind(b"\n"); d = d[:j + 1] if j >= 0 else b""
open(out, "wb").write(d)
n = d.count(b"\n")
print(f"{out}: {n} lines from bytes {b0}-{b1}")
