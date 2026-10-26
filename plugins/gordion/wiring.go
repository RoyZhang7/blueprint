// Package gordion provides a Blueprint plugin that instruments a service with
// the Gordion per-endpoint timing interceptor.
//
// The plugin generates two wrappers:
//
//   - a server-side wrapper that, for every method (endpoint) of the service,
//     records the arrival and the within-service latency of each request and
//     feeds them to a per-process [runtime/plugins/gordion.Recorder], which emits
//     per-window (default 100 ms) per-endpoint p50/p90/p99 and arrival counts as
//     JSON lines (to stdout or the file named by GORDION_OUT);
//   - a client-side wrapper that marks the time a request spends blocked on
//     downstream calls, so that the server-side wrapper of the *calling* service
//     can report processing latency (total minus blocked) as well as total, and
//     that (when GORDION_CLIENT_OUT is set) records the caller-observed RTT per
//     callee endpoint. The RTT includes the callee's socket/admission queue,
//     which the callee's own server-side wrapper cannot observe when the
//     callee's runtime is CPU-saturated.
//
// Apply it before the RPC plugin so that it sits directly around the workflow
// service:
//
//	gordion.Instrument(spec, "my_service")
//	grpc.Deploy(spec, "my_service")
//
// For blocked-time accounting to be complete, instrument every service a
// handler calls (as with the opentelemetry plugin).
package gordion

import (
	"github.com/blueprint-uservices/blueprint/blueprint/pkg/blueprint"
	"github.com/blueprint-uservices/blueprint/blueprint/pkg/coreplugins/pointer"
	"github.com/blueprint-uservices/blueprint/blueprint/pkg/ir"
	"github.com/blueprint-uservices/blueprint/blueprint/pkg/wiring"
	"github.com/blueprint-uservices/blueprint/plugins/golang"
	"golang.org/x/exp/slog"
)

// Instrument wraps serviceName with the Gordion server-side recorder and the
// client-side blocked-time marker.
func Instrument(spec wiring.WiringSpec, serviceName string) {
	serverWrapper := serviceName + ".server.gordion"
	clientWrapper := serviceName + ".client.gordion"

	ptr := pointer.GetPointer(spec, serviceName)
	if ptr == nil {
		slog.Error("Unable to instrument " + serviceName + " with gordion as it is not a pointer")
		return
	}

	serverNext := ptr.AddDstModifier(spec, serverWrapper)
	spec.Define(serverWrapper, &ServerWrapper{}, func(ns wiring.Namespace) (ir.IRNode, error) {
		var wrapped golang.Service
		if err := ns.Get(serverNext, &wrapped); err != nil {
			return nil, blueprint.Errorf("gordion %s expected %s to be a golang.Service, but encountered %s", serverWrapper, serverNext, err)
		}
		return newServerWrapper(serverWrapper, wrapped, serviceName)
	})

	clientNext := ptr.AddSrcModifier(spec, clientWrapper)
	spec.Define(clientWrapper, &ClientWrapper{}, func(ns wiring.Namespace) (ir.IRNode, error) {
		var wrapped golang.Service
		if err := ns.Get(clientNext, &wrapped); err != nil {
			return nil, blueprint.Errorf("gordion %s expected %s to be a golang.Service, but encountered %s", clientWrapper, clientNext, err)
		}
		return newClientWrapper(clientWrapper, wrapped, serviceName)
	})
}
