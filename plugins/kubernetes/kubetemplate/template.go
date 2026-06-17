package kubetemplate

import (
	"bytes"
	"html/template"
	"os"
	"strings"

	"github.com/blueprint-uservices/blueprint/plugins/linux"
)

func ExecuteTemplate(name string, body string, args any) (string, error) {
	return newTemplateExecutor(args).exec(name, body, args)
}

func ExecuteTemplateToFile(name string, body string, args any, filename string) error {
	f, err := os.OpenFile(filename, os.O_CREATE|os.O_RDWR, 0755)
	if err != nil {
		return err
	}
	defer f.Close()

	code, err := ExecuteTemplate(name, body, args)
	if err != nil {
		return err
	}
	_, err = f.WriteString(code)
	return err
}

type templateExecutor struct {
	Funcs template.FuncMap
}

func newTemplateExecutor(args any) *templateExecutor {
	e := &templateExecutor{
		Funcs: template.FuncMap{},
	}

	e.Funcs["EnvVarName"] = e.EnvVarName
	e.Funcs["Title"] = e.TitleCase
	e.Funcs["K8sName"] = e.K8sName

	return e
}

func (e *templateExecutor) exec(name string, body string, args any) (string, error) {
	t, err := template.New(name).Funcs(e.Funcs).Parse(body)
	if err != nil {
		return "", err
	}
	buf := &bytes.Buffer{}
	err = t.Execute(buf, args)
	return buf.String(), err
}

func (e *templateExecutor) EnvVarName(name string) (string, error) {
	return linux.EnvVar(name), nil
}

func (e *templateExecutor) TitleCase(arg string) (string, error) {
	return strings.Title(arg), nil
}
// K8sName converts an arbitrary identifier into a Kubernetes resource
// name acceptable to RFC 1123 / DNS-1035: lowercase alphanumerics and
// hyphens, starts and ends with alphanumeric. Underscores and dots
// (common in Blueprint node names) become hyphens. Does not enforce
// length limits.
func (e *templateExecutor) K8sName(arg string) (string, error) {
	out := strings.Map(func(r rune) rune {
		switch {
		case r >= 'a' && r <= 'z', r >= '0' && r <= '9', r == '-':
			return r
		case r >= 'A' && r <= 'Z':
			return r + ('a' - 'A')
		default:
			return '-'
		}
	}, arg)
	return strings.Trim(out, "-"), nil
}
