#!/bin/bash
# Phase J1 follow-up chain on node-0, detached: waits for the queue's DONE-J1, then (1) the perf profile rerun as its own small
# queue (prep + profile + the trap restore; R=/local/results/j1-profile), (2) the ladder analysis (analysis/j1_ladder.py ->
# /local/results/j1/an), (3) the archive: j1, j1-profile, j1-try1, the code (~/k5, ~/i2, /local/k5build binaries, the analysis
# scripts) and a final rsync of /local/results to the project filesystem (the cluster expires 2026-11-20 13:32 UTC).
# Log: /local/results/chain-j1.log; ends with CHAIN-J1-DONE.
set -u
PROJ=/proj/tracing-pythia-PG0/royno7-j-archive-clnode270-2026-11-20; PROJI=/proj/tracing-pythia-PG0/royno7-i-archive-clnode270-2026-11-20
log() { echo "=== $(date -u +%H:%M:%S) $*"; }
log "chain-j1: waiting for DONE-J1 in /local/results/j1/queue-j1.log"
while ! grep -q "DONE-J1" /local/results/j1/queue-j1.log 2>/dev/null; do
  pgrep -f "queue-j1.sh all" > /dev/null || { sleep 30; grep -q "DONE-J1" /local/results/j1/queue-j1.log 2>/dev/null || { log "queue-j1 is gone without DONE-J1; continuing with what there is"; break; }; }
  sleep 60
done
log "queue done: $(tail -1 /local/results/j1/queue-j1.log)"
rsync -a --exclude 'recorders/*' /local/results/j1/ $PROJ/j1/ 2>> /local/results/chain-j1.log
# (1) the profile rerun (the PID with the most threads), prep + profile, then the trap restore; ~6 min
cp ~/i2/queue-j1.sh.new ~/i2/queue-j1.sh 2>/dev/null
mkdir -p /local/results/j1-profile
R=/local/results/j1-profile PHASES="prep profile" timeout 1500 bash ~/i2/queue-j1.sh all > /local/results/j1-profile/queue-j1-profile.log 2>&1
log "profile queue: $(grep -c . /local/results/j1-profile/profile-g8-report.txt 2>/dev/null) report lines; $(tail -1 /local/results/j1-profile/queue-j1-profile.log)"
rsync -a /local/results/j1-profile/ $PROJ/j1-profile/ 2>> /local/results/chain-j1.log
# (2) the analysis
cd /local/blueprint/examples/bigsvc/analysis && python3 j1_ladder.py /local/results/j1 /local/results/j1/an > /local/results/j1/an-j1_ladder.log 2>&1; log "analysis: $(tail -1 /local/results/j1/an-j1_ladder.log)"
# (3) archive: j1 (with recorders), j1-try1, the code, then all of /local/results
mkdir -p $PROJ/code
rsync -a /local/results/j1/ $PROJ/j1/ 2>> /local/results/chain-j1.log
rsync -a /local/results/j1-try1/ $PROJ/j1-try1/ 2>> /local/results/chain-j1.log
rsync -a ~/k5 ~/i2 /local/blueprint/examples/bigsvc/analysis $PROJ/code/ 2>> /local/results/chain-j1.log
cp /local/k5build/k5agent /local/k5build/k5probe.bpf.o $PROJ/code/ 2>/dev/null
cp /local/sn/gordion/src/gordion/Gordion.h $PROJ/code/Gordion.h.g9 2>/dev/null
docker save 10.10.1.1:5000/sn-gordion:g9 | gzip > $PROJ/code/sn-gordion-g9.tar.gz 2>> /local/results/chain-j1.log
tar -czf /local/results/j1-raw.tgz -C /local/results j1 j1-try1 j1-profile 2>> /local/results/chain-j1.log; sha256sum /local/results/j1-raw.tgz | tee $PROJ/j1-raw.tgz.sha256
cp /local/results/j1-raw.tgz $PROJ/
rsync -a /local/results/ $PROJI/results/ 2>> /local/results/chain-j1.log
log "archive: $(du -sh $PROJ | cut -f1) in $PROJ; $(du -sh $PROJI | cut -f1) in $PROJI"
log CHAIN-J1-DONE
