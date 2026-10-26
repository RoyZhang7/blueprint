package bigsvc

import (
	"context"
	"fmt"
	"sync"
	"testing"
	"time"
)

// TestCalibration checks that a spin endpoint, run alone, takes about its
// configured floor, and reports how much it slows down when 8 run in parallel
// (which should be ~1x on an idle machine with >= 8 cores).
func TestCalibration(t *testing.T) {
	t.Setenv("BIGSVC_MEMWALK_MB", "64")
	s, err := NewBigServiceImpl(context.Background())
	if err != nil {
		t.Fatal(err)
	}
	for _, i := range []int{0, 30, 60, 90, 119} {
		if i >= len(Endpoints) || Endpoints[i].Kind != "spin" {
			continue
		}
		best := time.Hour
		for rep := 0; rep < 5; rep++ {
			t0 := time.Now()
			s.run(i, 1)
			if d := time.Since(t0); d < best {
				best = d
			}
		}
		want := Endpoints[i].FloorMs
		got := float64(best.Nanoseconds()) / 1e6
		fmt.Printf("seq  %s configured=%.3fms measured=%.3fms ratio=%.2f\n", Endpoints[i].Name, want, got, got/want)
		if got/want > 1.15 || got/want < 0.85 {
			t.Errorf("%s: measured %.3f ms vs configured %.3f ms", Endpoints[i].Name, got, want)
		}
	}
	// 8-way parallel
	i := 90
	var wg sync.WaitGroup
	t0 := time.Now()
	for g := 0; g < 8; g++ {
		wg.Add(1)
		go func() { defer wg.Done(); s.run(i, 1) }()
	}
	wg.Wait()
	par := float64(time.Since(t0).Nanoseconds()) / 1e6
	fmt.Printf("par8 %s configured=%.3fms measured=%.3fms ratio=%.2f\n", Endpoints[i].Name, Endpoints[i].FloorMs, par, par/Endpoints[i].FloorMs)
}
