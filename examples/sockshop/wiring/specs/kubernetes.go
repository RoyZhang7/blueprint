package specs
import (
	"os"
	"github.com/blueprint-uservices/blueprint/blueprint/pkg/coreplugins/namespaceutil"
	"github.com/blueprint-uservices/blueprint/blueprint/pkg/wiring"
	"github.com/blueprint-uservices/blueprint/examples/sockshop/workflow/cart"
	"github.com/blueprint-uservices/blueprint/examples/sockshop/workflow/catalogue"
	"github.com/blueprint-uservices/blueprint/examples/sockshop/workflow/frontend"
	"github.com/blueprint-uservices/blueprint/examples/sockshop/workflow/order"
	"github.com/blueprint-uservices/blueprint/examples/sockshop/workflow/payment"
	"github.com/blueprint-uservices/blueprint/examples/sockshop/workflow/queuemaster"
	"github.com/blueprint-uservices/blueprint/examples/sockshop/workflow/shipping"
	"github.com/blueprint-uservices/blueprint/examples/sockshop/workflow/user"
	"github.com/blueprint-uservices/blueprint/examples/sockshop/workload/workloadgen"
	"github.com/blueprint-uservices/blueprint/plugins/clientpool"
	"github.com/blueprint-uservices/blueprint/plugins/cmdbuilder"
	"github.com/blueprint-uservices/blueprint/plugins/goproc"
	"github.com/blueprint-uservices/blueprint/plugins/gotests"
	"github.com/blueprint-uservices/blueprint/plugins/grpc"
	"github.com/blueprint-uservices/blueprint/plugins/http"
	"github.com/blueprint-uservices/blueprint/plugins/kubernetes"
	"github.com/blueprint-uservices/blueprint/plugins/kubernetes/kubepod"
	"github.com/blueprint-uservices/blueprint/plugins/linuxcontainer"
	"github.com/blueprint-uservices/blueprint/plugins/mongodb"
	"github.com/blueprint-uservices/blueprint/plugins/mysql"
	"github.com/blueprint-uservices/blueprint/plugins/opentelemetry"
	"github.com/blueprint-uservices/blueprint/plugins/retries"
	"github.com/blueprint-uservices/blueprint/plugins/simple"
	"github.com/blueprint-uservices/blueprint/plugins/workflow"
	"github.com/blueprint-uservices/blueprint/plugins/workload"
	"github.com/blueprint-uservices/blueprint/plugins/zipkin"
)
// Kubernetes mirrors the Docker spec but emits per-pod Kubernetes
// manifests via plugins/kubernetes instead of docker-compose.
//
// Workaround: kubernetes.NewApplication's high-level
// AddContainerToApplication API uses containers[0] as the pod name,
// which collides with the container's own name via spec.Define and
// destroys the original container definition (fatal for shared
// containers like zipkin.ctr). We call kubepod.NewKubePod directly
// with a distinct <ctr>_pod name and attach pods to the Application
// via namespaceutil.AddNodeTo.
var Kubernetes = cmdbuilder.SpecOption{
	Name:        "kubernetes",
	Description: "Deploys each service in a Kubernetes Pod with gRPC. Mirrors the docker spec's topology.",
	Build:       makeKubernetesSpec,
}
func makeKubernetesSpec(spec wiring.WiringSpec) ([]string, error) {
	registry := os.Getenv("SOCKSHOP_REGISTRY")
	if registry == "" {
		registry = "docclabgroup"
	}
	trace_collector := zipkin.Collector(spec, "zipkin")
	applyDefaults := func(serviceName string, useHTTP ...bool) {
		retries.AddRetries(spec, serviceName, 3)
		clientpool.Create(spec, serviceName, 10)
		opentelemetry.Instrument(spec, serviceName, trace_collector)
		if len(useHTTP) > 0 && useHTTP[0] {
			http.Deploy(spec, serviceName)
		} else {
			grpc.Deploy(spec, serviceName)
		}
		goproc.Deploy(spec, serviceName)
		linuxcontainer.Deploy(spec, serviceName)
		gotests.Test(spec, serviceName)
	}
	user_db := mongodb.Container(spec, "user_db")
	user_service := workflow.Service[user.UserService](spec, "user_service", user_db)
	applyDefaults(user_service)
	payment_service := workflow.Service[payment.PaymentService](spec, "payment_service", "500")
	applyDefaults(payment_service)
	cart_db := mongodb.Container(spec, "cart_db")
	cart_service := workflow.Service[cart.CartService](spec, "cart_service", cart_db)
	applyDefaults(cart_service)
	shipqueue := simple.Queue(spec, "shipping_queue")
	shipdb := mongodb.Container(spec, "shipping_db")
	shipping_service := workflow.Service[shipping.ShippingService](spec, "shipping_service", shipqueue, shipdb)
	applyDefaults(shipping_service)
	queue_master := workflow.Service[queuemaster.QueueMaster](spec, "queue_master", shipqueue, shipping_service)
	goproc.AddToProcess(spec, "shipping_proc", queue_master)
	order_db := mongodb.Container(spec, "order_db")
	order_service := workflow.Service[order.OrderService](spec, "order_service", user_service, cart_service, payment_service, shipping_service, order_db)
	applyDefaults(order_service)
	catalogue_db := mysql.Container(spec, "catalogue_db")
	catalogue_service := workflow.Service[catalogue.CatalogueService](spec, "catalogue_service", catalogue_db)
	applyDefaults(catalogue_service)
	frontend_service := workflow.Service[frontend.Frontend](spec, "frontend", user_service, catalogue_service, cart_service, order_service)
	applyDefaults(frontend_service, true)
	wlgen := workload.Generator[workloadgen.SimpleWorkload](spec, "wlgen", frontend_service)
	// Application with zero initial containers (workaround; see top-of-file note).
	appName := kubernetes.NewApplication(spec, "sockshop_k8s", registry)
	// One pod per container, with a distinct <ctr>_pod name so the
	// pod's spec.Define does NOT clobber the underlying Container node.
	podPairs := [][2]string{
		{"frontend_pod", "frontend_ctr"},
		{"user_pod", "user_ctr"},
		{"payment_pod", "payment_ctr"},
		{"cart_pod", "cart_ctr"},
		{"shipping_pod", "shipping_ctr"},
		{"order_pod", "order_ctr"},
		{"catalogue_pod", "catalogue_ctr"},
		{"user_db_pod", "user_db.ctr"},
		{"cart_db_pod", "cart_db.ctr"},
		{"shipping_db_pod", "shipping_db.ctr"},
		{"order_db_pod", "order_db.ctr"},
		{"catalogue_db_pod", "catalogue_db.ctr"},
		{"zipkin_pod", "zipkin.ctr"},
	}
	for _, p := range podPairs {
		podName, containerName := p[0], p[1]
		kubepod.NewKubePod(spec, podName, registry, containerName)
		namespaceutil.AddNodeTo[kubernetes.Application](spec, appName, podName)
	}
	return []string{appName, wlgen, "gotests"}, nil
}
