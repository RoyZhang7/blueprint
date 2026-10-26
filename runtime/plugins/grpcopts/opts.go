// Package grpcopts is a process-wide registry of extra grpc.ServerOption
// values. Generated gRPC servers (plugins/grpc) call ServerOptions() when
// they construct their grpc.Server, so runtime libraries that need a hook at
// the transport layer (for example the gordion recorder's arrival tap) can
// Register one without the code generator knowing about them.
//
// Register must be called before the servers start; Blueprint builds every
// node (constructors) before invoking Run on any of them, so registering from
// a constructor is safe.
package grpcopts

import (
	"sync"

	"google.golang.org/grpc"
)

var (
	mu   sync.Mutex
	opts []grpc.ServerOption
)

// Register appends server options that every generated gRPC server in this
// process will be created with.
func Register(o ...grpc.ServerOption) {
	mu.Lock()
	defer mu.Unlock()
	opts = append(opts, o...)
}

// ServerOptions returns a copy of the registered options.
func ServerOptions() []grpc.ServerOption {
	mu.Lock()
	defer mu.Unlock()
	out := make([]grpc.ServerOption, len(opts))
	copy(out, opts)
	return out
}
