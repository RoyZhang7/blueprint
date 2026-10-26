package specs

import (
	"github.com/blueprint-uservices/blueprint/blueprint/pkg/wiring"
	"github.com/blueprint-uservices/blueprint/examples/bigsvc/workflow/bigsvc"
	"github.com/blueprint-uservices/blueprint/examples/bigsvc/workload/workloadgen"
	"github.com/blueprint-uservices/blueprint/plugins/clientpool"
	"github.com/blueprint-uservices/blueprint/plugins/cmdbuilder"
	"github.com/blueprint-uservices/blueprint/plugins/goproc"
	"github.com/blueprint-uservices/blueprint/plugins/gordion"
	"github.com/blueprint-uservices/blueprint/plugins/grpc"
	"github.com/blueprint-uservices/blueprint/plugins/workflow"
	"github.com/blueprint-uservices/blueprint/plugins/workload"
)

// Proc builds big_service and the workload generator as two bare Go processes
// talking gRPC, for local smoke tests without containers.
var Proc = cmdbuilder.SpecOption{
	Name:        "proc",
	Description: "big_service (gRPC + gordion) and the workload generator as bare processes",
	Build:       makeProcSpec,
}

func makeProcSpec(spec wiring.WiringSpec) ([]string, error) {
	big := workflow.Service[*bigsvc.BigServiceImpl](spec, "big_service")
	gordion.Instrument(spec, big)
	clientpool.Create(spec, big, 32)
	grpc.Deploy(spec, big)
	bigProc := goproc.Deploy(spec, big)

	wlgen := workload.Generator[workloadgen.BigWorkload](spec, "wlgen", big)

	// The default stdout metric collector prints every second; silence it.
	goproc.SetMetricPrintFrequency(spec, "big_proc", "240h")
	goproc.SetMetricPrintFrequency(spec, "wlgen.proc", "240h")
	return []string{bigProc, wlgen}, nil
}
