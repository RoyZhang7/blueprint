#!/usr/bin/env python3
"""Phase K4: send one message to HC's Listener (127.0.0.1:10101) the way pkg/actuators/hc/listener.go does: compact JSON,
half-close, read up to 4 bytes, expect ACK. usage: k4send.py '<json object>'"""
import json, socket, sys

msg = json.loads(sys.argv[1])
s = socket.create_connection(("127.0.0.1", 10101), timeout=5)
s.sendall(json.dumps(msg, separators=(",", ":"), sort_keys=True).encode())
s.shutdown(socket.SHUT_WR)
r = b""
while len(r) < 4:
    c = s.recv(4 - len(r))
    if not c: break
    r += c
reply = r.rstrip(b"\x00").decode(errors="replace")
print(f"{msg.get('ctr_type')}: {reply}")
sys.exit(0 if reply == "ACK" else 1)
