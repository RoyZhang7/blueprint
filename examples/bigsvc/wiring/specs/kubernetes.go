package specs

import (
	"os"

	"github.com/blueprint-uservices/blueprint/blueprint/pkg/coreplugins/namespaceutil"
	"github.com/blueprint-uservices/blueprint/blueprint/pkg/wiring"
	"github.com/blueprint-uservices/blueprint/examples/bigsvc/workflow/bigsvc"
	"github.com/blueprint-uservices/blueprint/examples/bigsvc/workload/workloadgen"
	"github.com/blueprint-uservices/blueprint/plugins/clientpool"
	"github.com/blueprint-uservices/blueprint/plugins/cmdbuilder"
	"github.com/blueprint-uservices/blueprint/plugins/goproc"
	"github.com/blueprint-uservices/blueprint/plugins/gordion"
	"github.com/blueprint-uservices/blueprint/plugins/grpc"
	"github.com/blueprint-uservices/blueprint/plugins/kubernetes"
	"github.com/blueprint-uservices/blueprint/plugins/kubernetes/kubepod"
	"github.com/blueprint-uservices/blueprint/plugins/linuxcontainer"
	"github.com/blueprint-uservices/blueprint/plugins/workflow"
	"github.com/blueprint-uservices/blueprint/plugins/workload"
)

// Kubernetes deploys big_service as one gRPC pod (image pushed to
// $BIGSVC_REGISTRY, default 10.10.1.1:5000) wrapped with the Gordion
// interceptor, and builds the workload generator as a bare process so it can
// be run from any cluster node against the service's ClusterIP.
var Kubernetes = cmdbuilder.SpecOption{
	Name:        "kubernetes",
	Description: "big_service in a Kubernetes pod (gRPC + gordion); workload generator as a bare process",
	Build:       makeKubernetesSpec,
}

func makeKubernetesSpec(spec wiring.WiringSpec) ([]string, error) {
	registry := os.Getenv("BIGSVC_REGISTRY")
	if registry == "" {
		registry = "10.10.1.1:5000"
	}

	big := workflow.Service[*bigsvc.BigServiceImpl](spec, "big_service")
	gordion.Instrument(spec, big)
	clientpool.Create(spec, big, 32)
	grpc.Deploy(spec, big)
	goproc.Deploy(spec, big)                   // big_proc
	bigCtr := linuxcontainer.Deploy(spec, big) // big_ctr

	wlgen := workload.Generator[workloadgen.BigWorkload](spec, "wlgen", big)

	// The default stdout metric collector prints every second; silence it.
	goproc.SetMetricPrintFrequency(spec, "big_proc", "240h")
	goproc.SetMetricPrintFrequency(spec, "wlgen.proc", "240h")

	// Same workaround as the sockshop kubernetes spec: create the application
	// empty and attach explicitly-named pods, so the pod's spec.Define does not
	// clobber the container definition.
	app := kubernetes.NewApplication(spec, "bigsvc", registry)
	kubepod.NewKubePod(spec, "big_pod", registry, bigCtr)
	namespaceutil.AddNodeTo[kubernetes.Application](spec, app, "big_pod")

	return []string{app, wlgen}, nil
}
