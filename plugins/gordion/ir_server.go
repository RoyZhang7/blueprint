package gordion

import (
	"fmt"
	"reflect"

	"github.com/blueprint-uservices/blueprint/blueprint/pkg/blueprint"
	"github.com/blueprint-uservices/blueprint/blueprint/pkg/coreplugins/service"
	"github.com/blueprint-uservices/blueprint/blueprint/pkg/ir"
	"github.com/blueprint-uservices/blueprint/plugins/golang"
	"github.com/blueprint-uservices/blueprint/plugins/golang/gocode"
)

// ServerWrapper is the Blueprint IR node for the server-side Gordion wrapper.
type ServerWrapper struct {
	golang.Service
	golang.GeneratesFuncs
	golang.Instantiable

	InstanceName  string
	Wrapped       golang.Service
	ServiceName   *ir.IRValue
	outputPackage string
}

func newServerWrapper(name string, server ir.IRNode, serviceName string) (*ServerWrapper, error) {
	serverNode, ok := server.(golang.Service)
	if !ok {
		return nil, blueprint.Errorf("gordion server wrapper requires %s to be a golang service but got %s", server.Name(), reflect.TypeOf(server).String())
	}
	return &ServerWrapper{
		InstanceName:  name,
		Wrapped:       serverNode,
		ServiceName:   &ir.IRValue{Value: serviceName},
		outputPackage: "gordion",
	}, nil
}

func (node *ServerWrapper) ImplementsGolangNode()    {}
func (node *ServerWrapper) ImplementsGolangService() {}
func (node *ServerWrapper) Name() string             { return node.InstanceName }
func (node *ServerWrapper) String() string {
	return node.Name() + " = GordionServer(" + node.Wrapped.Name() + ")"
}

func (node *ServerWrapper) AddInterfaces(builder golang.ModuleBuilder) error {
	return node.Wrapped.AddInterfaces(builder)
}

func (node *ServerWrapper) GetInterface(ctx ir.BuildContext) (service.ServiceInterface, error) {
	return node.Wrapped.GetInterface(ctx)
}

func (node *ServerWrapper) GenerateFuncs(builder golang.ModuleBuilder) error {
	iface, err := golang.GetGoInterface(builder, node)
	if err != nil {
		return err
	}
	if builder.Visited(iface.BaseName + ".gordion.server") {
		return nil
	}
	return generateServerWrapper(builder, iface, node.outputPackage)
}

func (node *ServerWrapper) AddInstantiation(builder golang.NamespaceBuilder) error {
	if builder.Visited(node.InstanceName) {
		return nil
	}
	iface, err := golang.GetGoInterface(builder, node.Wrapped)
	if err != nil {
		return err
	}
	constructor := &gocode.Constructor{
		Package: builder.Module().Info().Name + "/" + node.outputPackage,
		Func: gocode.Func{
			Name: fmt.Sprintf("New_%v_GordionServer", iface.BaseName),
			Arguments: []gocode.Variable{
				{Name: "ctx", Type: &gocode.UserType{Package: "context", Name: "Context"}},
				{Name: "server", Type: iface},
				{Name: "serviceName", Type: &gocode.BasicType{Name: "string"}},
			},
		},
	}
	return builder.DeclareConstructor(node.InstanceName, constructor, []ir.IRNode{node.Wrapped, node.ServiceName})
}
