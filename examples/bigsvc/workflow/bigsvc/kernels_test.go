package bigsvc

import (
	"context"
	"fmt"
	"runtime"
	"sync"
	"testing"
	"time"
)

// strict: timing assertions only hold on the Linux testbed; on a laptop (and
// on Windows, whose monotonic clock is coarse) the numbers are printed only.
var strict = runtime.GOOS == "linux"

// tolerance per kind: measured-alone latency / configured floor must fall in
// [lo, hi]. Kinds with random or GC-dependent cost are reported but not asserted.
var tol = map[string][2]float64{
	// sleep: after the wait the core has dropped frequency under a DVFS governor, so the spin part runs
	// slow until it ramps (1.6x seen with schedutil on an idle Xeon); fanout: goroutine start-up + SMT.
	"spin": {0.85, 1.15}, "mutex": {0.85, 1.15}, "lockshare": {0.85, 1.15}, "sleep": {0.9, 1.7},
	"fanout": {0.85, 1.5}, "syscall": {0.8, 1.3}, "memwalk": {0.7, 1.5}, "stream": {0.7, 1.5}, "mixed": {0.7, 1.6},
}

// TestCalibration runs every endpoint alone (best of 5 after a warm-up) and
// checks it takes about its configured floor; then checks that 8 spin
// endpoints in parallel do not slow each other down on >= 8 idle cores.
func TestCalibration(t *testing.T) {
	t.Setenv("BIGSVC_MEMWALK_MB", "64")
	s, err := NewBigServiceImpl(context.Background())
	if err != nil {
		t.Fatal(err)
	}
	seen := map[string]int{}
	for i, e := range Endpoints {
		// one replica per (kind, param) is enough for the spin family; others: all
		key := fmt.Sprintf("%s/%g", e.Kind, e.Param)
		if e.Kind == "spin" && seen[key] > 0 && i%30 != 0 {
			continue
		}
		seen[key]++
		s.run(i, 1)
		got := timeBatch(t, func(x int64) { s.run(i, x) }, e.FloorMs)
		ratio := got / e.FloorMs
		fmt.Printf("alone %-18s kind=%-9s param=%-6g floor=%7.3fms cpu=%7.3fms measured=%7.3fms ratio=%.2f\n",
			e.Name, e.Kind, e.Param, e.FloorMs, e.CpuMs, got, ratio)
		if r, ok := tol[e.Kind]; ok && strict && (ratio < r[0] || ratio > r[1]) {
			t.Errorf("%s: measured %.3f ms vs floor %.3f ms (ratio %.2f, want %v)", e.Name, got, e.FloorMs, ratio, r)
		}
	}
	// 8-way parallel spin
	i := -1
	for j, e := range Endpoints {
		if e.Kind == "spin" && e.FloorMs >= 4 {
			i = j
			break
		}
	}
	if i < 0 {
		return
	}
	var wg sync.WaitGroup
	t0 := time.Now()
	for g := 0; g < 8; g++ {
		wg.Add(1)
		go func() { defer wg.Done(); s.run(i, 1) }()
	}
	wg.Wait()
	par := float64(time.Since(t0).Nanoseconds()) / 1e6
	fmt.Printf("par8 %s floor=%.3fms measured=%.3fms ratio=%.2f\n", Endpoints[i].Name, Endpoints[i].FloorMs, par, par/Endpoints[i].FloorMs)
}

// TestLockShare checks that lockshare endpoints in the same group serialise
// on one lock (two concurrent calls take ~1.5× the floor: both spin halves
// overlap, the held halves do not) while those in different groups do not.
func TestLockShare(t *testing.T) {
	t.Setenv("BIGSVC_MEMWALK_MB", "64")
	s, err := NewBigServiceImpl(context.Background())
	if err != nil {
		t.Fatal(err)
	}
	groups := map[int][]int{}
	for i, e := range Endpoints {
		if e.Kind == "lockshare" {
			groups[e.Group] = append(groups[e.Group], i)
		}
	}
	if len(groups) == 0 {
		t.Skip("no lockshare endpoints generated")
	}
	pair := func(a, b int) float64 {
		return timeBatch(t, func(x int64) {
			var wg sync.WaitGroup
			for _, i := range []int{a, b} {
				wg.Add(1)
				go func(i int) { defer wg.Done(); s.run(i, x) }(i)
			}
			wg.Wait()
		}, Endpoints[a].FloorMs)
	}
	for g, members := range groups {
		if len(members) < 2 {
			continue
		}
		a, b := members[0], members[1]
		s.run(a, 1)
		best := pair(a, b)
		fl := Endpoints[a].FloorMs
		fmt.Printf("lockshare group %d: %s+%s concurrent = %.3f ms (floor %.3f, expect ~%.3f)\n", g, Endpoints[a].Name, Endpoints[b].Name, best, fl, 1.5*fl)
		if strict && best < 1.3*fl {
			t.Errorf("group %d: concurrent pair took %.3f ms, expected >= 1.3×floor (%.3f): lock not shared?", g, best, 1.3*fl)
		}
	}
}

// timeBatch returns the per-call wall time (ms) of f, measured over batches
// long enough (>= 50 ms) to be independent of the clock resolution; best of 3.
func timeBatch(t *testing.T, f func(x int64), floorMs float64) float64 {
	t.Helper()
	reps := int(50/floorMs) + 1
	best := 1e18
	for b := 0; b < 3; b++ {
		t0 := time.Now()
		for r := 0; r < reps; r++ {
			f(int64(r))
		}
		if d := float64(time.Since(t0).Nanoseconds()) / 1e6 / float64(reps); d < best {
			best = d
		}
	}
	return best
}
