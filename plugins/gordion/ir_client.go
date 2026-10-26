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

// ClientWrapper is the Blueprint IR node for the client-side Gordion wrapper.
// It brackets every outgoing call with PauseBegin/PauseEnd so the calling
// request's blocked time is accounted.
type ClientWrapper struct {
	golang.Service
	golang.GeneratesFuncs
	golang.Instantiable

	InstanceName  string
	Wrapped       golang.Service
	CalleeName    *ir.IRValue
	outputPackage string
}

func newClientWrapper(name string, client ir.IRNode, calleeName string) (*ClientWrapper, error) {
	clientNode, ok := client.(golang.Service)
	if !ok {
		return nil, blueprint.Errorf("gordion client wrapper requires %s to be a golang service but got %s", client.Name(), reflect.TypeOf(client).String())
	}
	return &ClientWrapper{
		InstanceName:  name,
		Wrapped:       clientNode,
		CalleeName:    &ir.IRValue{Value: calleeName},
		outputPackage: "gordion",
	}, nil
}

func (node *ClientWrapper) ImplementsGolangNode()    {}
func (node *ClientWrapper) ImplementsGolangService() {}
func (node *ClientWrapper) Name() string             { return node.InstanceName }
func (node *ClientWrapper) String() string {
	return node.Name() + " = GordionClient(" + node.Wrapped.Name() + ")"
}

func (node *ClientWrapper) AddInterfaces(builder golang.ModuleBuilder) error {
	return node.Wrapped.AddInterfaces(builder)
}

func (node *ClientWrapper) GetInterface(ctx ir.BuildContext) (service.ServiceInterface, error) {
	return node.Wrapped.GetInterface(ctx)
}

func (node *ClientWrapper) GenerateFuncs(builder golang.ModuleBuilder) error {
	iface, err := golang.GetGoInterface(builder, node)
	if err != nil {
		return err
	}
	if builder.Visited(iface.BaseName + ".gordion.client") {
		return nil
	}
	return generateClientWrapper(builder, iface, node.outputPackage)
}

func (node *ClientWrapper) AddInstantiation(builder golang.NamespaceBuilder) error {
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
			Name: fmt.Sprintf("New_%v_GordionClient", iface.BaseName),
			Arguments: []gocode.Variable{
				{Name: "ctx", Type: &gocode.UserType{Package: "context", Name: "Context"}},
				{Name: "client", Type: iface},
				{Name: "calleeName", Type: &gocode.BasicType{Name: "string"}},
			},
		},
	}
	return builder.DeclareConstructor(node.InstanceName, constructor, []ir.IRNode{node.Wrapped, node.CalleeName})
}
