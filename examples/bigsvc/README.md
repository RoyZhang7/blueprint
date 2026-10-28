# BigSvc — per-endpoint latency dictionary testbed (Phase A)

A synthetic Blueprint application with **one service and 100+ endpoints**
(Phase A ran 120 spin endpoints + 3 planted violators = 123 gRPC methods; the
generator now also emits *families* of complex endpoints replicated over a
parameter range, 184 methods by default — see "Endpoint families"), used to
validate that the profiled-alone dictionary `D(λ)` can be decomposed into
per-endpoint curves `D_e[ρ]` indexed by offered utilisation
`ρ = λ · Σ_e %_e FL_e` that remain valid under any endpoint mix.

Everything the design needs comes from a gRPC-level interceptor: the
[gordion plugin](../../plugins/gordion) wraps every method of the service and
emits, per 100 ms window, per-endpoint arrival counts and within-service
latency percentiles (runtime in [runtime/plugins/gordion](../../runtime/plugins/gordion)).

## Layout

```
gen/                 go run . -k 120 [-families ...]  -> regenerates workflow/bigsvc/service.go,
                                          floors.json, workload/workloadgen/dispatch.go
                     go run . -k 120 -families "" -violators   reproduces the Phase A service
workflow/bigsvc/     BigService: Ep000..Ep119 spin for a configured floor FL_e (log-spread
                     0.2–20 ms, fixed *work*, calibrated at start-up) + endpoint families
                     (kernels.go: memwalk, stream, alloc, mutex, lockshare, sleep, fanout,
                     varln, varbi, syscall, mixed). BIGSVC_CPUSET pins the process.
workload/            open-loop Poisson load generator: -rate / -schedule "r:s,r:s,..",
                     -mix uniform|dirichlet|single:Ep007|weights:..|file:.., -resample S,
                     -violator-weight W; JSON-lines log with 1 s client-side stats.
wiring/              specs: `kubernetes` (pod image -> $BIGSVC_REGISTRY, wlgen as bare proc),
                     `proc` (both as bare processes for smoke tests)
scripts/exp.sh       build / deploy / wlgen / collect / run driver (runs on node-0)
analysis/dictionary.py  shared rule-A model: c_e, ρ, avgS, Lat_e (incl. admission wait), W(ρ, avgS),
                     Cherkasova floors, W-range guard, shape test, ratio statistics, figure notation
analysis/analyze.py  server-side: FL_e, W(ρ), rule-A collapse (rule B diagnostic), σ_e, prediction, B1 costs
analysis/client_view.py / client_predict.py   the same from the caller's view (load-generator reports)
analysis/e1_compare.py   mix dependence of W and the W(ρ, avgS) fit;  a5_compare.py  extrinsic attribution
results/             copied run directories (gordion.jsonl, wlgen.jsonl, meta.json, csv, png)
```

## Endpoint families

`gen -families "name=p1,p2;..." -family-floors 1,4` instantiates every family
once per (parameter × floor). Each family breaks one assumption behind
"latency depends on the mix only through ρ = Σ a_e·FL_e"; `floors.json` and
the workload `run` event carry `floor_ms` (latency floor), `cpu_ms` (expected
CPU per request), `kind`, `param`, `group`.

| family (default params) | endpoint does | breaks / tests |
|---|---|---|
| `memwalk=0.25,4,64,256` MB | pointer chase over a working set of that size | cost set by the cache level, not the CPU; own-share (LLC) sensitivity vs working-set size |
| `stream=1,8,64` MB | sequential read over the block | memory-bandwidth bound: slowed by any bandwidth user, own or neighbour |
| `alloc=0.0625,1,4,16` MB | that much garbage per request, then spin | GC assist / GC frequency scale with the family's share; floor is GC-inflated |
| `mutex=0.1,0.5,2` ms | spin, last `p` ms under a private lock | serialisation on own arrival rate; effect grows with hold/floor |
| `lockshare=2,4` | `p` endpoints share ONE lock, hold FL/2 each | cross-endpoint coupling the index cannot see (depends on the group's share) |
| `sleep=1,5,20` ms | blocking wait, then spin(floor) | latency floor ≠ CPU floor: ρ from latency floors over-counts |
| `fanout=2,4,8` | `p` goroutines each spin(floor) | CPU = p×floor for a latency of floor: ρ from latency floors under-counts |
| `varln=0.3,0.7` | work = floor·exp(σ·N(0,1)) | input-dependent cost; profiled p50 ≠ mean; dictionary width |
| `varbi=0.1` | 10×floor with probability p | heavy tail inside one endpoint |
| `syscall=0.5,1` | that fraction of the floor in real syscalls | kernel CPU: visible to the handler timer, different contention surface |
| `mixed=1` | spin ⅓ + chase (64 MB) ⅓ + 1 MB alloc + spin ⅓ | composite kernel: does FL_e add up from parts |

ρ is always indexed by c_e (CPU per request: a `costs.json` from idle runs,
else the configured `cpu_ms`), never by the latency floors (handoff F3); FL_e
stays the floor of D_e. The sleep/fanout/var* families are where the two differ.

## Recorder output (one JSON line per window)

```
{"t":<unix ms>,"svc":"big_service","w":100,"a":<arrivals>,"n":<completions>,"e":<errors>,"drop":0,
 "cpu":<process CPU ns in the window>,
 "all":[n,p50,p90,p99,mean,max,pp50,pp90,q50,q90,x50,x90,ns,f50,f90],
 "raw":{"Ep000":[ns,...],...}, "rawp":{...}, "rawq":{...}, "rawx":{...}, "rawf":{...},   # GORDION_RAW=1
 "ep":{"Ep000":[a,n,p50,p90,p99,mean,max,pp50,pp90,q50,q90,x50,x90,ns,f50,f90],...}}
```
`p*` = total within-process latency (transport tap -> handler return), `pp*` =
processing latency (total minus time blocked on gordion-instrumented downstream
calls), `q*` = tap -> handler start, `x*` = caller send stamp -> tap (the
admission wait; the gordion client wrapper adds the stamp as gRPC metadata),
`ns` = completions whose caller stamped, `f*` = full latency of those (x + p):
this is Lat_e as the analysis defines it (handoff F4); where `ns` = 0 the caller
is not instrumented and the admission wait is invisible. Env: `GORDION_OUT`
(file or stdout), `GORDION_WINDOW_MS`, `GORDION_RAW`, `GORDION_DISABLE`,
`GORDION_CLIENT_OUT` (client-side recorder file).

## Running (node-0)

```
cd /local/blueprint/examples/bigsvc/scripts
./exp.sh build v2                              # compile spec, build+push image, build wlgen
TAG=v2 CPUSET=2-9 NODE=node-3 ./exp.sh run a2-uniform -- -mix uniform -warmup 10 \
    -schedule "40:150,100:45,200:45,400:45,600:45,800:45,1000:45,1200:45,1400:45,1500:45,1600:45,1700:45,1800:45"
TAG=v2 ./exp.sh run a3-dirichlet -- -mix dirichlet -alpha 1 -resample 10 -schedule "<random λ steps>"
```
Results land in `/local/results/<timestamp>-<name>/`; the recorder file is
written on the service node under `/mnt/extra/gordion/` and copied back.

```
python analysis/analyze.py results/<a2-run>                      # floors, W(ρ), rule-A collapse, σ_e
python analysis/analyze.py results/<a3-run> --dict results/<a2-run> [--dict <a2-run at another mix>]
                                                                 # prediction with the W-range guard and the shape test
python analysis/analyze.py --idle-costs results/<idle-run>... --out-costs costs.json   # B1: c_e per endpoint
python analysis/client_view.py results/<a2-run>                  # caller view of the collapse test
python analysis/client_predict.py results/<a3-run> --dict results/<a2-run> [--stressed-knee RHO]
python analysis/e1_compare.py --uniform R1 --short R2 --long R3 --fit [--shift-run R4]   # W(ρ, avgS)
python analysis/a5_compare.py <stress-random> --a2 <a2-run> --truth <stress-sweep> --control <nostress-random>
```
Every script takes `--costs costs.json`; every figure carries the notation block
(handoff F10). Rule B (Lat_e / FL_e) appears only as a diagnostic panel/column.

## Notes / deviations from the design text

* Cluster is CloudLab `c6525-25g` (AMD EPYC 7302P, 16 cores, SMT off on
  node-3, no cpufreq driver exposed -> no frequency scaling term needed);
  service pinned to 8 cores (`CPUSET=2-9`, so k = 8 for ρ/k).
* Latency is measured inside the gRPC handler (after unmarshalling), so FL_e
  for a spin endpoint equals its configured spin time almost exactly; the
  gRPC transport overhead is visible only client-side.
* Spin calibration must go through the real kernel: the first version timed a
  dead-code-eliminated empty loop and under-estimated ns/iteration 6×
  (fixed with `//go:noinline` + a global sink).
