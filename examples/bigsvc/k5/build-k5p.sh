#!/bin/bash
# RESULTS-K 5.8 build on node-0 (RUNBOOK 17.1), run by queue-k5.sh defbp; synchronous, exits non-zero on any failure.
# Sources in ~/k5 beforehand: probe/ (F_RD), recorder.go and grpc.go (the Go recorder's production mode), Gordion.h
# (the C++ recorder's production mode). Steps:
#   1. clang-14 / llvm-14 from apt if missing (package lists before/after; the queue's restore purges dpkg-added.txt)
#   2. libbpf 1.4.7 static into /local/k5build/root
#   3. k5probe.bpf.o and k5agent
#   4. the recorder sources into the node-0 trees (the originals kept as *.pre-k5; the restore puts them back), then
#      hr-gordion:$HRTAG (hr.sh build, also hrgen) and sn-gordion:$SNTAG (docker build), pushed to the registry
set -u
HRTAG=${HRTAG:-gordion9}; SNTAG=${SNTAG:-g8}
K=$HOME/k5; B=/local/k5build; mkdir -p $B
log() { echo "=== $(date -u +%T) $*"; }
fail() { log "BUILD FAILED: $*"; exit 1; }
cd $B
if ! which clang-14 >/dev/null 2>&1; then
  log "apt: clang-14 llvm-14"
  dpkg -l | awk '/^ii/ {print $2}' | sort > $B/dpkg-before.txt
  sudo apt-get update -q > $B/apt-update.log 2>&1
  sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -q clang-14 llvm-14 > $B/apt.log 2>&1 || fail "apt"
  dpkg -l | awk '/^ii/ {print $2}' | sort > $B/dpkg-after.txt
  comm -13 $B/dpkg-before.txt $B/dpkg-after.txt > $B/dpkg-added.txt; log "packages added: $(wc -l < $B/dpkg-added.txt)"
fi
if [ ! -f $B/root/lib/libbpf.a ]; then
  log "libbpf 1.4.7"
  [ -d libbpf-1.4.7 ] || curl -sL https://github.com/libbpf/libbpf/archive/refs/tags/v1.4.7.tar.gz | tar -xz || fail "libbpf download"
  make -s -C libbpf-1.4.7/src BUILD_STATIC_ONLY=1 OBJDIR=$B/lb DESTDIR=$B/root INCLUDEDIR=/include LIBDIR=/lib UAPIDIR=/include install > $B/libbpf.log 2>&1 || fail "libbpf make"
fi
log "probe and agent"
rm -f $B/k5probe.bpf.o $B/k5agent
clang-14 -O2 -g -target bpf -D__TARGET_ARCH_x86 -I$B/root/include -I$K/probe -I/usr/include/x86_64-linux-gnu -c $K/probe/k5probe.bpf.c -o $B/k5probe.bpf.o 2>&1 | head -40
[ -f $B/k5probe.bpf.o ] || fail "probe"
gcc -O2 -Wall -o $B/k5agent $K/probe/k5agent.c -I$B/root/include -I$K/probe $B/root/lib/libbpf.a -lelf -lz 2>&1 | head -40
[ -x $B/k5agent ] || fail "agent"
HRT=/local/hr/hotelReservation; SNT=/local/sn/gordion
for f in $HRT/gordion/recorder.go $HRT/gordion/grpc.go $SNT/src/gordion/Gordion.h; do [ -f $f.pre-k5 ] || cp $f $f.pre-k5; done
tr -d '\r' < $K/recorder.go > $HRT/gordion/recorder.go
tr -d '\r' < $K/grpc.go > $HRT/gordion/grpc.go
tr -d '\r' < $K/Gordion.h > $SNT/src/gordion/Gordion.h
log "image hr-gordion:$HRTAG"
(cd $HRT/noisy-neighbors/dictionary && PATH=/usr/local/go1.25/bin:$PATH bash hr.sh build $HRTAG) > $B/build-hr.log 2>&1 || fail "hr image (see $B/build-hr.log)"
grep -q "\"$HRTAG\"" $B/build-hr.log || fail "hr-gordion:$HRTAG not in the registry's tag list"
log "image sn-gordion:$SNTAG"
(cd $SNT && docker build -t 10.10.1.1:5000/sn-gordion:$SNTAG . && docker push 10.10.1.1:5000/sn-gordion:$SNTAG) > $B/build-sn.log 2>&1 || fail "sn image (see $B/build-sn.log)"
log "BUILD-K5P-DONE"
