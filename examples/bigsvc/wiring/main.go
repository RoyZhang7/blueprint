// Package main compiles the BigSvc application: one service with 100+
// endpoints instrumented with the Gordion per-endpoint interceptor, plus an
// open-loop mixed-endpoint workload generator.
//
// Usage:
//
//	go run . -o build -w kubernetes      # k8s manifests + container image dirs
//	go run . -o build -w proc            # bare processes, for local smoke tests
package main

import (
	"github.com/blueprint-uservices/blueprint/examples/bigsvc/wiring/specs"
	"github.com/blueprint-uservices/blueprint/plugins/cmdbuilder"
	"github.com/blueprint-uservices/blueprint/plugins/workflow/workflowspec"
)

func main() {
	workflowspec.AddModule("github.com/blueprint-uservices/blueprint/examples/bigsvc/workflow")
	workflowspec.AddModule("github.com/blueprint-uservices/blueprint/examples/bigsvc/workload")

	cmdbuilder.MakeAndExecute(
		"BigSvc",
		specs.Kubernetes,
		specs.Proc,
	)
}
