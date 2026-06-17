package deploygen

import (
	"fmt"
	"os"
	"path/filepath"

	"github.com/blueprint-uservices/blueprint/blueprint/pkg/blueprint"
	"github.com/blueprint-uservices/blueprint/blueprint/pkg/ir"
	"github.com/blueprint-uservices/blueprint/plugins/kubernetes/kubetemplate"
	"github.com/blueprint-uservices/blueprint/plugins/linux"
	"golang.org/x/exp/slog"
)

type KubeDeploymentFile struct {
	Name            string
	WorkspaceDir    string
	FileName        string
	ServiceFilename string
	FilePath        string
	NumReplicas     int64
	Instances       map[string]*instance
	Namespace       string
}

type instance struct {
	InstanceName  string
	Image         string
	Ports         map[string]uint16
	Config        map[string]string
	CustomCommand []string
	Volumes       map[string]string
	CustomConf    map[string]string
}

func NewKubeDeploymentFile(workspaceName string, workspaceDir string, filename string, serviceFilename string) *KubeDeploymentFile {
	return &KubeDeploymentFile{
		Name:            workspaceName,
		WorkspaceDir:    workspaceDir,
		FileName:        filename,
		ServiceFilename: serviceFilename,
		FilePath:        filepath.Join(workspaceDir, filename),
		Instances:       make(map[string]*instance),
		// For now NumReplicas is fixed.
		NumReplicas: 1,
		Namespace:   os.Getenv("BLUEPRINT_K8S_NAMESPACE"),
	}
}

func (k *KubeDeploymentFile) Generate() error {
	slog.Info(fmt.Sprintf("Generating %v/%v", k.Name, k.FileName))
	err := kubetemplate.ExecuteTemplateToFile("kubedeployment", kubernetesTemplate, k, k.FilePath)
	if err != nil {
		return err
	}
	slog.Info("NUmber of instances: ", "num", len(k.Instances))
	serviceFilePath := filepath.Join(k.WorkspaceDir, k.ServiceFilename)
	slog.Info(fmt.Sprintf("Generating %v/%v", k.Name, k.ServiceFilename))
	return kubetemplate.ExecuteTemplateToFile("kubedeployment", kubernetesServiceTemplate, k, serviceFilePath)
}

func (k *KubeDeploymentFile) AddImageInstance(instanceName string, image string) error {
	return k.addInstance(instanceName, image)
}

func (k *KubeDeploymentFile) getInstance(instanceName string) (*instance, error) {
	instanceName = ir.CleanName(instanceName)
	if i, exists := k.Instances[instanceName]; exists {
		return i, nil
	} else {
		return nil, blueprint.Errorf("container instance with name %v not found", instanceName)
	}
}

func (k *KubeDeploymentFile) AddEnvVar(instanceName string, key string, val string) error {
	key = linux.EnvVar(key)
	instance, err := k.getInstance(instanceName)
	if err != nil {
		return err
	} else {
		instance.Config[key] = val
		return nil
	}
}

func (k *KubeDeploymentFile) ExposePort(instanceName string, portName string, port uint16) error {
	instance, err := k.getInstance(instanceName)
	if err != nil {
		return err
	} else {
		instance.Ports[portName] = port
		return nil
	}
}

func (k *KubeDeploymentFile) AddCustomConf(instanceName string, src string, dst string) error {
	instance, err := k.getInstance(instanceName)
	if err != nil {
		return err
	}
	instance.CustomConf[src] = dst
	return nil
}

func (k *KubeDeploymentFile) AddVolume(instanceName string, src string, dst string) error {
	instance, err := k.getInstance(instanceName)
	if err != nil {
		return err
	}
	instance.Volumes[src] = dst
	return nil
}

func (k *KubeDeploymentFile) SetCustomCommand(instanceName string, command []string) error {
	instance, err := k.getInstance(instanceName)
	if err != nil {
		return err
	}
	instance.CustomCommand = command
	return nil
}

func (k *KubeDeploymentFile) addInstance(instanceName string, image string) error {
	instanceName = ir.CleanName(instanceName)
	if _, exists := k.Instances[instanceName]; exists {
		return blueprint.Errorf("re-declaration of container instance %v of image %v", instanceName, image)
	}
	instance := &instance{
		InstanceName:  instanceName,
		Image:         image,
		Ports:         make(map[string]uint16),
		Config:        make(map[string]string),
		CustomCommand: []string{},
		Volumes:       make(map[string]string),
		CustomConf:    make(map[string]string),
	}
	k.Instances[instanceName] = instance
	return nil
}

var kubernetesTemplate = `
apiVersion: apps/v1
kind: Deployment
metadata:
  name: {{K8sName .Name}}
  {{- if .Namespace}}
  namespace: {{.Namespace}}
  {{- end}}
  labels:
    blueprint.service: {{K8sName .Name}}
spec:
  replicas: {{.NumReplicas}}
  selector:
    matchLabels:
      blueprint.service: {{K8sName .Name}}
  template:
    metadata:
      name: {{K8sName .Name}}
      labels:
        blueprint.service: {{K8sName .Name}}
    spec:
      containers:
      {{range $_, $decl := .Instances}}
        - name: {{K8sName .InstanceName}}
          image: {{.Image}}
          imagePullPolicy: Always
          {{- if .Config}}
          env:
          {{- range $name, $value := .Config}}
            - name: {{$name}}
              value: "{{$value}}"
          {{- end}}
          {{- end}}
          {{- if .Ports}}
          ports:
          {{- range $name, $port := .Ports}}
            - containerPort: {{$port}}
          {{- end}}
          {{- end}}
      {{- end}}
      restartPolicy: Always
      hostname: {{K8sName .Name}}
`

var kubernetesServiceTemplate = `
apiVersion: v1
kind: Service
metadata:
  name: {{K8sName .Name}}
  {{- if .Namespace}}
  namespace: {{.Namespace}}
  {{- end}}
spec:
  selector:
    blueprint.service: {{K8sName .Name}}
  ports:
  {{range $_, $decl := .Instances}}
  {{- range $name, $port := .Ports}}
    - name: {{K8sName $name}}
      port: {{$port}}
      targetPort: {{$port}}
  {{- end}}
  {{- end}}
`
