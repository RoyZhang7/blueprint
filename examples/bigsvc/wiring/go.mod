module github.com/blueprint-uservices/blueprint/examples/bigsvc/wiring

go 1.22

require github.com/blueprint-uservices/blueprint/blueprint v0.0.0-20240619221802-d064c5861c1e

require github.com/blueprint-uservices/blueprint/plugins v0.0.0-20240619221802-d064c5861c1e

require (
	github.com/blueprint-uservices/blueprint/examples/bigsvc/workflow v0.0.0
	github.com/blueprint-uservices/blueprint/examples/bigsvc/workload v0.0.0
)

replace github.com/blueprint-uservices/blueprint/examples/bigsvc/workflow => ../workflow

replace github.com/blueprint-uservices/blueprint/examples/bigsvc/workload => ../workload
