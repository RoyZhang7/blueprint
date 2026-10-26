package bigsvc

import (
	"fmt"
	"log/slog"
	"os"
	"runtime"
	"strconv"
	"strings"

	"golang.org/x/sys/unix"
)

// parseCPUList parses "2-9,12" style lists.
func parseCPUList(s string) ([]int, error) {
	var cpus []int
	for _, part := range strings.Split(s, ",") {
		part = strings.TrimSpace(part)
		if part == "" {
			continue
		}
		if lo, hi, ok := strings.Cut(part, "-"); ok {
			a, err1 := strconv.Atoi(lo)
			b, err2 := strconv.Atoi(hi)
			if err1 != nil || err2 != nil || a > b {
				return nil, fmt.Errorf("bad cpu range %q", part)
			}
			for c := a; c <= b; c++ {
				cpus = append(cpus, c)
			}
		} else {
			c, err := strconv.Atoi(part)
			if err != nil {
				return nil, fmt.Errorf("bad cpu %q", part)
			}
			cpus = append(cpus, c)
		}
	}
	if len(cpus) == 0 {
		return nil, fmt.Errorf("empty cpu list %q", s)
	}
	return cpus, nil
}

// applyCPUSet pins every existing thread of this process to the given CPUs
// (threads created later inherit the mask from their creator) and sets
// GOMAXPROCS to the number of CPUs. This gives taskset-like behaviour inside
// a distroless container where no taskset binary is available.
func applyCPUSet(list string) error {
	cpus, err := parseCPUList(list)
	if err != nil {
		return err
	}
	var set unix.CPUSet
	for _, c := range cpus {
		set.Set(c)
	}
	tasks, err := os.ReadDir("/proc/self/task")
	if err != nil {
		return fmt.Errorf("read /proc/self/task: %w", err)
	}
	pinned := 0
	for _, t := range tasks {
		tid, err := strconv.Atoi(t.Name())
		if err != nil {
			continue
		}
		if err := unix.SchedSetaffinity(tid, &set); err != nil {
			return fmt.Errorf("sched_setaffinity(%d): %w", tid, err)
		}
		pinned++
	}
	runtime.GOMAXPROCS(len(cpus))
	slog.Info("bigsvc pinned", "cpuset", list, "threads", pinned, "gomaxprocs", len(cpus))
	return nil
}
