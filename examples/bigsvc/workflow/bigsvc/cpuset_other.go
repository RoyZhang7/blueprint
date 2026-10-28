//go:build !linux

package bigsvc

import "fmt"

// applyCPUSet is only implemented on Linux (sched_setaffinity).
func applyCPUSet(spec string) error {
	return fmt.Errorf("BIGSVC_CPUSET=%q: CPU pinning is only supported on linux", spec)
}
