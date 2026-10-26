// Package bigsvc implements a synthetic service with many endpoints for
// validating per-endpoint (virtual-service) latency dictionaries.
//
// Normal endpoints spin for a configured amount of CPU *work* (not wall time:
// a fixed number of iterations of a dependent integer kernel, calibrated once
// at start-up to the configured floor), so that interference shows up as
// latency inflation exactly as it would for real compute.
//
// Three planted violators break the "queueing depends on the mix only
// through utilisation" assumption in different ways:
//
//   - VioMemWalk: pointer-chases through a large (default 256 MB) array, so it
//     is memory-latency bound and sensitive to how many *other memory-bound*
//     requests (its own share) are in flight;
//   - VioMutex: serialises on an endpoint-private mutex, so its latency depends
//     on its own arrival rate;
//   - VioAlloc: allocates heavily, so GC assist and GC frequency scale with its
//     own share.
//
// Environment:
//
//	BIGSVC_CPUSET          pin every thread of the process to this CPU list
//	                       (e.g. "2-9" or "2,3,4") and set GOMAXPROCS to its size
//	BIGSVC_NS_PER_ITER     skip spin calibration and use this ns/iteration
//	BIGSVC_MEMWALK_MB      size of the memwalk array (default 256)
//	BIGSVC_CALIB_ITERS     iterations per calibration rep (default 20e6)
package bigsvc

import (
	"context"
	"fmt"
	"log/slog"
	"math/rand"
	"os"
	"runtime"
	"strconv"
	"sync"
	"time"
)

// EndpointSpec describes one generated endpoint.
type EndpointSpec struct {
	Name     string
	FloorMs  float64
	Kind     string
	Violator bool
}

// BigServiceImpl implements BigService.
type BigServiceImpl struct {
	BigService

	nsPerIter float64
	iters     []int64 // per endpoint: spin iterations (spin / mutex / alloc)

	memwalk      []uint32
	memwalkSteps int64
	mu           sync.Mutex // VioMutex's private lock
}

// NewBigServiceImpl calibrates the spin kernel and prepares the violators.
func NewBigServiceImpl(ctx context.Context) (*BigServiceImpl, error) {
	s := &BigServiceImpl{}
	if cs := os.Getenv("BIGSVC_CPUSET"); cs != "" {
		if err := applyCPUSet(cs); err != nil {
			return nil, err
		}
	}
	s.nsPerIter = calibrateSpin()
	s.iters = make([]int64, len(Endpoints))
	for i, e := range Endpoints {
		switch e.Kind {
		case "spin", "mutex", "alloc":
			s.iters[i] = int64(e.FloorMs * 1e6 / s.nsPerIter)
		}
	}
	mb := envInt("BIGSVC_MEMWALK_MB", 256)
	s.memwalk = makeCycle(mb << 20 / 4)
	nsPerStep := calibrateWalk(s.memwalk)
	for i, e := range Endpoints {
		if e.Kind == "memwalk" {
			s.memwalkSteps = int64(e.FloorMs * 1e6 / nsPerStep)
		}
		_ = i
	}
	slog.Info("bigsvc calibrated",
		"ns_per_iter", fmt.Sprintf("%.3f", s.nsPerIter),
		"memwalk_ns_per_step", fmt.Sprintf("%.1f", nsPerStep),
		"memwalk_steps", s.memwalkSteps,
		"memwalk_mb", mb,
		"endpoints", len(Endpoints),
		"gomaxprocs", runtime.GOMAXPROCS(0))
	fmt.Printf("{\"event\":\"bigsvc_calibration\",\"ns_per_iter\":%.4f,\"memwalk_ns_per_step\":%.2f,\"memwalk_steps\":%d,\"gomaxprocs\":%d}\n",
		s.nsPerIter, nsPerStep, s.memwalkSteps, runtime.GOMAXPROCS(0))
	return s, nil
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

func calibrateSpin() float64 {
	if v := os.Getenv("BIGSVC_NS_PER_ITER"); v != "" {
		if f, err := strconv.ParseFloat(v, 64); err == nil && f > 0 {
			return f
		}
	}
	n := int64(envInt("BIGSVC_CALIB_ITERS", 20_000_000))
	best := float64(1e18)
	for rep := 0; rep < 7; rep++ {
		t0 := time.Now()
		Sink += spin(n)
		d := float64(time.Since(t0).Nanoseconds()) / float64(n)
		if d < best {
			best = d
		}
	}
	return best
}

// makeCycle builds a random single-cycle permutation so that a chase visits
// every element with no short cycles (Sattolo's algorithm).
func makeCycle(n int) []uint32 {
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
	const steps = 200_000
	best := float64(1e18)
	for rep := 0; rep < 5; rep++ {
		t0 := time.Now()
		Sink += int64(walk(a, uint32(rep*7919)%uint32(len(a)), steps))
		d := float64(time.Since(t0).Nanoseconds()) / float64(steps)
		if d < best {
			best = d
		}
	}
	return best
}

// run executes endpoint i with payload x.
func (s *BigServiceImpl) run(i int, x int64) (int64, error) {
	switch Endpoints[i].Kind {
	case "spin":
		return spin(s.iters[i]) ^ x, nil
	case "memwalk":
		start := uint32(uint64(x) % uint64(len(s.memwalk)))
		return int64(walk(s.memwalk, start, s.memwalkSteps)), nil
	case "mutex":
		s.mu.Lock()
		r := spin(s.iters[i])
		s.mu.Unlock()
		return r ^ x, nil
	case "alloc":
		// ~4 MB of short-lived garbage per request, kept alive across the spin
		// so the GC has live data to scan.
		keep := make([][]byte, 0, 4096)
		var sum int64
		for j := 0; j < 4096; j++ {
			b := make([]byte, 1024)
			b[0] = byte(j)
			keep = append(keep, b)
			sum += int64(len(b))
		}
		r := spin(s.iters[i])
		sum += int64(len(keep))
		return r ^ sum ^ x, nil
	}
	return 0, fmt.Errorf("unknown endpoint kind %q", Endpoints[i].Kind)
}
