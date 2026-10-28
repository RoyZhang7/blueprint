// Package bigsvc implements a synthetic service with many endpoints for
// validating per-endpoint (virtual-service) latency dictionaries.
//
// Normal ("spin") endpoints burn a configured amount of CPU *work* (a fixed
// number of iterations of a dependent integer kernel, calibrated once at
// start-up to the configured floor), so that interference shows up as latency
// inflation exactly as it would for real compute.
//
// Complex endpoints come in families, each replicated over a parameter range
// (see examples/bigsvc/gen). Each family breaks one assumption behind
// "latency depends on the mix only through utilisation ρ = Σ a_e·FL_e":
//
//	memwalk   pointer chase over a working set of Param MB: latency bound by
//	          cache/memory, sensitive to its own share and to LLC neighbours
//	stream    sequential read over Param MB: memory-bandwidth bound
//	alloc     Param MB garbage per request: GC assist/frequency scale with share
//	mutex     last min(Param, FL) ms under an endpoint-private lock: serialises
//	          on its own arrival rate
//	lockshare Param endpoints share one lock (Group), each holds it FL/2:
//	          cross-endpoint coupling through a resource the index does not see
//	sleep     blocking wait of Param ms then spin(FL): latency floor ≠ CPU floor
//	fanout    Param goroutines each spin(FL): CPU = Param×FL for a latency of FL
//	varln     per-request work FL·exp(Param·N(0,1)): input-dependent cost, median FL
//	varbi     per-request work 10×FL with probability Param: heavy tail
//	syscall   Param of the floor spent in real syscalls: kernel CPU, not user
//	mixed     spin + pointer chase (64 MB) + 1 MB alloc + spin: composite kernel
//
// Environment:
//
//	BIGSVC_CPUSET          pin every thread of the process to this CPU list
//	                       (e.g. "2-9" or "2,3,4") and set GOMAXPROCS to its size
//	BIGSVC_NS_PER_ITER     skip spin calibration and use this ns/iteration
//	BIGSVC_MEMWALK_MB      working set for memwalk endpoints with Param 0 (legacy VioMemWalk; default 256)
//	BIGSVC_CALIB_ITERS     iterations per calibration rep (default 20e6)
package bigsvc

import (
	"context"
	"encoding/json"
	"fmt"
	"log/slog"
	"math"
	"math/rand"
	"os"
	"runtime"
	"strconv"
	"sync"
	"syscall"
	"time"
)

// EndpointSpec describes one generated endpoint.
type EndpointSpec struct {
	Name     string
	FloorMs  float64 // latency floor the endpoint is calibrated to
	CpuMs    float64 // expected CPU time per request
	Kind     string
	Param    float64
	Group    int
	Violator bool
}

// BigServiceImpl implements BigService.
type BigServiceImpl struct {
	BigService

	nsPerIter float64
	iters     []int64 // per endpoint: spin iterations for the "floor" part of the work
	hold      []int64 // mutex / lockshare: spin iterations under the lock
	steps     []int64 // memwalk / stream / mixed: steps over the array
	sysCalls  []int64 // syscall: number of syscalls
	allocB    []int   // alloc / mixed: bytes of garbage per request

	legacyMB int              // BIGSVC_MEMWALK_MB, working set for memwalk endpoints with Param 0
	walks    map[int][]uint32 // pointer-chase arrays keyed by working-set size in KB
	streams  map[int][]uint64 // sequential-read blocks keyed by size in MB
	locks    []*sync.Mutex    // index: per-endpoint private lock (mutex) or group lock (lockshare)
	lockOf   []int            // endpoint -> index into locks
}

// NewBigServiceImpl calibrates the kernels and prepares every endpoint.
func NewBigServiceImpl(ctx context.Context) (*BigServiceImpl, error) {
	s := &BigServiceImpl{walks: map[int][]uint32{}, streams: map[int][]uint64{}}
	if cs := os.Getenv("BIGSVC_CPUSET"); cs != "" {
		if err := applyCPUSet(cs); err != nil {
			return nil, err
		}
	}
	n := len(Endpoints)
	s.iters = make([]int64, n)
	s.hold = make([]int64, n)
	s.steps = make([]int64, n)
	s.sysCalls = make([]int64, n)
	s.allocB = make([]int, n)
	s.lockOf = make([]int, n)

	s.nsPerIter = calibrateSpin()
	ms := func(x float64) int64 { return int64(x * 1e6 / s.nsPerIter) }

	// Allocate the shared arrays first (one per distinct size).
	s.legacyMB = envInt("BIGSVC_MEMWALK_MB", 256)
	for _, e := range Endpoints {
		switch e.Kind {
		case "memwalk":
			s.walks[s.walkKey(e.Param)] = nil
		case "mixed":
			s.walks[mixedWalkKB] = nil
		case "stream":
			s.streams[int(e.Param)] = nil
		}
	}
	walkNs := map[int]float64{}
	for kb := range s.walks {
		s.walks[kb] = makeCycle(kb << 10 / 4)
		walkNs[kb] = calibrateWalk(s.walks[kb])
	}
	streamNs := map[int]float64{}
	for mb := range s.streams {
		s.streams[mb] = makeBlock(mb << 20 / 8)
		streamNs[mb] = calibrateStream(s.streams[mb])
	}
	sysNs := 0.0
	for _, e := range Endpoints {
		if e.Kind == "syscall" {
			sysNs = calibrateSyscall()
			break
		}
	}

	groupLock := map[int]int{}
	for i, e := range Endpoints {
		switch e.Kind {
		case "spin", "sleep", "fanout", "varln", "varbi":
			// spin: the floor; sleep: the spin part after the wait; fanout: per
			// goroutine; var*: the median request.
			s.iters[i] = ms(e.FloorMs - sleepMs(e))
		case "memwalk":
			s.steps[i] = int64(e.FloorMs * 1e6 / walkNs[s.walkKey(e.Param)])
		case "stream":
			s.steps[i] = int64(e.FloorMs * 1e6 / streamNs[int(e.Param)])
		case "alloc":
			s.allocB[i] = int(e.Param * float64(1<<20))
			s.iters[i] = ms(e.FloorMs)
		case "mutex":
			s.hold[i] = ms(e.Param)
			s.iters[i] = ms(e.FloorMs - e.Param)
			s.locks = append(s.locks, &sync.Mutex{})
			s.lockOf[i] = len(s.locks) - 1
		case "lockshare":
			s.hold[i] = ms(e.FloorMs / 2)
			s.iters[i] = ms(e.FloorMs / 2)
			if _, ok := groupLock[e.Group]; !ok {
				s.locks = append(s.locks, &sync.Mutex{})
				groupLock[e.Group] = len(s.locks) - 1
			}
			s.lockOf[i] = groupLock[e.Group]
		case "syscall":
			s.sysCalls[i] = int64(e.FloorMs * e.Param * 1e6 / sysNs)
			s.iters[i] = ms(e.FloorMs * (1 - e.Param))
		case "mixed":
			s.iters[i] = ms(e.FloorMs / 3) // used twice
			s.steps[i] = int64(e.FloorMs / 3 * 1e6 / walkNs[mixedWalkKB])
			s.allocB[i] = 1 << 20
		default:
			return nil, fmt.Errorf("unknown endpoint kind %q", e.Kind)
		}
	}
	cal := map[string]any{"event": "bigsvc_calibration", "ns_per_iter": s.nsPerIter, "walk_ns_per_step": walkNs,
		"stream_ns_per_elem": streamNs, "syscall_ns": sysNs, "gomaxprocs": runtime.GOMAXPROCS(0), "endpoints": n}
	js, _ := json.Marshal(cal)
	fmt.Println(string(js))
	slog.Info("bigsvc calibrated", "ns_per_iter", fmt.Sprintf("%.3f", s.nsPerIter), "endpoints", n, "gomaxprocs", runtime.GOMAXPROCS(0))
	return s, nil
}

// mixedWalkKB is the working set (KB) of the pointer-chase part of "mixed".
const mixedWalkKB = 64 << 10

// walkKey maps a memwalk parameter (MB; 0 = legacy env size) to the KB key of
// its shared array.
func (s *BigServiceImpl) walkKey(paramMB float64) int {
	if paramMB <= 0 {
		return s.legacyMB << 10
	}
	return int(paramMB * 1024)
}

func sleepMs(e EndpointSpec) float64 {
	if e.Kind == "sleep" {
		return e.Param
	}
	return 0
}

func envInt(name string, def int) int {
	if v := os.Getenv(name); v != "" {
		if n, err := strconv.Atoi(v); err == nil && n > 0 {
			return n
		}
	}
	return def
}

// Sink defeats dead-code elimination: calibration results are stored here.
// (Without it the compiler removes the dead LCG chain from the calibration
// loop and times an empty counting loop, ~6x faster than the real kernel.)
var Sink int64

// spin runs a dependent integer kernel for n iterations. The result is
// returned so the compiler cannot elide the loop. Not inlined so that the
// calibration and the handler execute the identical function body.
//
//go:noinline
func spin(n int64) int64 {
	x := uint64(n) | 1
	for i := int64(0); i < n; i++ {
		x = x*6364136223846793005 + 1442695040888963407
		x ^= x >> 29
	}
	return int64(x)
}

// nsPer times kernel(n) for growing n until one call takes at least
// calibMin, then returns the best ns per unit over reps. Growing n makes the
// result independent of the clock resolution (Go's monotonic clock on Windows
// ticks at 0.5-16 ms; on Linux it is ns) and of short transients.
func nsPer(kernel func(n int64), n0 int64, reps int) float64 {
	const calibMin = 50 * time.Millisecond
	n := n0
	for {
		t0 := time.Now()
		kernel(n)
		if time.Since(t0) >= calibMin {
			break
		}
		n *= 2
	}
	best := float64(1e18)
	for rep := 0; rep < reps; rep++ {
		t0 := time.Now()
		kernel(n)
		if d := float64(time.Since(t0).Nanoseconds()) / float64(n); d < best {
			best = d
		}
	}
	return best
}

func calibrateSpin() float64 {
	if v := os.Getenv("BIGSVC_NS_PER_ITER"); v != "" {
		if f, err := strconv.ParseFloat(v, 64); err == nil && f > 0 {
			return f
		}
	}
	n := int64(envInt("BIGSVC_CALIB_ITERS", 20_000_000))
	return nsPer(func(n int64) { Sink += spin(n) }, n, 7)
}

// makeCycle builds a random single-cycle permutation so that a chase visits
// every element with no short cycles (Sattolo's algorithm). The array size is
// what sets the cache level the chase runs at. Sizes below 1 MB are passed as
// element counts computed by the caller (mb<<20/4 may be 0 for tiny sizes).
func makeCycle(n int) []uint32 {
	if n < 1024 {
		n = 1024
	}
	a := make([]uint32, n)
	for i := range a {
		a[i] = uint32(i)
	}
	r := rand.New(rand.NewSource(42))
	for i := n - 1; i > 0; i-- {
		j := r.Intn(i)
		a[i], a[j] = a[j], a[i]
	}
	return a
}

//go:noinline
func walk(a []uint32, start uint32, steps int64) uint32 {
	p := start
	for i := int64(0); i < steps; i++ {
		p = a[p]
	}
	return p
}

func calibrateWalk(a []uint32) float64 {
	// a random chase over anything larger than the caches misses on every step already
	rep := 0
	return nsPer(func(n int64) {
		rep++
		Sink += int64(walk(a, uint32(rep*7919)%uint32(len(a)), n))
	}, 200_000, 5)
}

func makeBlock(n int) []uint64 {
	if n < 1024 {
		n = 1024
	}
	a := make([]uint64, n)
	for i := range a {
		a[i] = uint64(i) * 0x9E3779B97F4A7C15
	}
	return a
}

// stream reads the block sequentially for n elements (wrapping), so the cost
// is set by memory bandwidth for blocks larger than the caches.
//
//go:noinline
func stream(a []uint64, n int64) uint64 {
	var sum uint64
	for n > 0 {
		m := int64(len(a))
		if m > n {
			m = n
		}
		for _, v := range a[:m] {
			sum += v
		}
		n -= m
	}
	return sum
}

func calibrateStream(a []uint64) float64 {
	return nsPer(func(n int64) { Sink += int64(stream(a, n)) }, int64(len(a))*2, 5)
}

// sysLoop issues n real syscalls (getpid is not cached by Go and not vDSO).
//
//go:noinline
func sysLoop(n int64) int64 {
	var acc int64
	for i := int64(0); i < n; i++ {
		acc += int64(syscall.Getpid())
	}
	return acc
}

func calibrateSyscall() float64 {
	return nsPer(func(n int64) { Sink += sysLoop(n) }, 200_000, 5)
}

// garbage allocates b bytes in 64 KB chunks, kept alive until it returns, so
// the GC has live data to scan.
//
//go:noinline
func garbage(b int) int64 {
	const chunk = 64 << 10
	keep := make([][]byte, 0, b/chunk+1)
	var sum int64
	for off := 0; off < b; off += chunk {
		c := make([]byte, chunk)
		c[0] = byte(off)
		keep = append(keep, c)
		sum += int64(len(c))
	}
	return sum + int64(len(keep))
}

// run executes endpoint i with payload x.
func (s *BigServiceImpl) run(i int, x int64) (int64, error) {
	e := &Endpoints[i]
	switch e.Kind {
	case "spin":
		return spin(s.iters[i]) ^ x, nil
	case "memwalk":
		a := s.walks[s.walkKey(e.Param)]
		start := uint32(uint64(x) % uint64(len(a)))
		return int64(walk(a, start, s.steps[i])), nil
	case "stream":
		return int64(stream(s.streams[int(e.Param)], s.steps[i])) ^ x, nil
	case "alloc":
		g := garbage(s.allocB[i])
		return spin(s.iters[i]) ^ g ^ x, nil
	case "mutex", "lockshare":
		r := spin(s.iters[i])
		l := s.locks[s.lockOf[i]]
		l.Lock()
		r ^= spin(s.hold[i])
		l.Unlock()
		return r ^ x, nil
	case "sleep":
		time.Sleep(time.Duration(e.Param * float64(time.Millisecond)))
		return spin(s.iters[i]) ^ x, nil
	case "fanout":
		g := int(e.Param)
		res := make([]int64, g)
		var wg sync.WaitGroup
		for j := 0; j < g; j++ {
			wg.Add(1)
			go func(j int) { defer wg.Done(); res[j] = spin(s.iters[i]) }(j)
		}
		wg.Wait()
		var r int64
		for _, v := range res {
			r ^= v
		}
		return r ^ x, nil
	case "varln":
		f := math.Exp(e.Param * rand.NormFloat64())
		return spin(int64(float64(s.iters[i])*f)) ^ x, nil
	case "varbi":
		n := s.iters[i]
		if rand.Float64() < e.Param {
			n *= 10
		}
		return spin(n) ^ x, nil
	case "syscall":
		r := sysLoop(s.sysCalls[i])
		return spin(s.iters[i]) ^ r ^ x, nil
	case "mixed":
		a := s.walks[mixedWalkKB]
		r := spin(s.iters[i])
		r ^= int64(walk(a, uint32(uint64(x)%uint64(len(a))), s.steps[i]))
		r ^= garbage(s.allocB[i])
		r ^= spin(s.iters[i])
		return r ^ x, nil
	}
	return 0, fmt.Errorf("unknown endpoint kind %q", e.Kind)
}
