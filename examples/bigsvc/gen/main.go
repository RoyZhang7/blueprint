// Command gen generates the BigService workflow (K spin endpoints plus
// families of "complex" endpoints, each replicated over a parameter range)
// and the matching workload dispatch table.
//
// Usage (from examples/bigsvc/gen):
//
//	go run . -k 120 -fl-min 0.2 -fl-max 20                       # 120 spin + default families
//	go run . -k 120 -families "" -violators                      # the original 123-endpoint service
//	go run . -k 120 -families "memwalk=4,256;alloc=1,16" -family-floors 1,4
//
// Families (parameter meaning; every family is replicated once per
// (parameter value × -family-floors value)):
//
//	memwalk=<MB,...>    pointer chase over a working set of <MB> (cache level)
//	stream=<MB,...>     sequential read over a <MB> block (memory bandwidth)
//	alloc=<MB,...>      <MB> of short-lived garbage per request, then spin (GC)
//	mutex=<ms,...>      spin, with the last min(<ms>, floor) ms under a private lock
//	lockshare=<n,...>   <n> endpoints share ONE lock, each holds it for floor/2
//	sleep=<ms,...>      blocking wait of <ms> (no CPU) followed by spin(floor)
//	fanout=<g,...>      <g> goroutines each spin(floor) in parallel (CPU = g×floor)
//	varln=<sigma,...>   per-request work = floor × exp(sigma·N(0,1)) (median = floor)
//	varbi=<p,...>       per-request work = 10×floor with probability <p>, else floor
//	syscall=<frac,...>  <frac> of the floor spent in real syscalls (kernel CPU)
//	mixed=<1>           spin ⅓ + pointer chase ⅓ (64 MB) + 1 MB alloc + spin ⅓
//
// It writes:
//
//	../workflow/bigsvc/service.go          the BigService interface + impl
//	../workflow/bigsvc/floors.json         endpoint table (floors, cpu, kind, param)
//	../workload/workloadgen/dispatch.go    endpoint table + Call() switch
package main

import (
	"bytes"
	"encoding/json"
	"flag"
	"fmt"
	"math"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"text/template"
)

type endpoint struct {
	Name     string  `json:"name"`
	FloorMs  float64 `json:"floor_ms"` // latency floor (ms) the endpoint is calibrated to
	CpuMs    float64 `json:"cpu_ms"`   // expected CPU time per request (ms); differs from FloorMs for sleep/fanout/var*
	Kind     string  `json:"kind"`
	Param    float64 `json:"param"` // family parameter (see package doc)
	Group    int     `json:"group"` // lock group for lockshare (0 otherwise)
	Violator bool    `json:"violator"`
}

// DefaultFamilies is used when -families is "default".
const DefaultFamilies = "memwalk=0.25,4,64,256;stream=1,8,64;alloc=0.0625,1,4,16;mutex=0.1,0.5,2;" +
	"lockshare=2,4;sleep=1,5,20;fanout=2,4,8;varln=0.3,0.7;varbi=0.1;syscall=0.5,1;mixed=1"

func main() {
	k := flag.Int("k", 120, "number of normal (spin) endpoints")
	violators := flag.Bool("violators", false, "add the three original planted violators (VioMemWalk, VioMutex, VioAlloc)")
	families := flag.String("families", "default", "family spec \"name=p1,p2;name=...\", \"default\", or \"\" for none")
	famFloors := flag.String("family-floors", "1,4", "floor(s) in ms at which every family replica is instantiated")
	flMin := flag.Float64("fl-min", 0.2, "smallest spin floor in ms")
	flMax := flag.Float64("fl-max", 20, "largest spin floor in ms")
	outWorkflow := flag.String("out-workflow", "../workflow/bigsvc", "workflow package dir")
	outWorkload := flag.String("out-workload", "../workload/workloadgen", "workload package dir")
	flag.Parse()

	var eps []endpoint
	for i := 0; i < *k; i++ {
		frac := 0.0
		if *k > 1 {
			frac = float64(i) / float64(*k-1)
		}
		fl := round3(*flMin * math.Pow(*flMax / *flMin, frac))
		eps = append(eps, endpoint{Name: fmt.Sprintf("Ep%03d", i), FloorMs: fl, CpuMs: fl, Kind: "spin"})
	}
	if *violators {
		eps = append(eps,
			endpoint{Name: "VioMemWalk", FloorMs: 4.0, CpuMs: 4.0, Kind: "memwalk", Param: 0, Violator: true}, // Param 0 → BIGSVC_MEMWALK_MB
			endpoint{Name: "VioMutex", FloorMs: 2.0, CpuMs: 2.0, Kind: "mutex", Param: 2.0, Violator: true},
			endpoint{Name: "VioAlloc", FloorMs: 1.0, CpuMs: 1.0, Kind: "alloc", Param: 4.0, Violator: true},
		)
	}
	spec := *families
	if spec == "default" {
		spec = DefaultFamilies
	}
	fam, err := expandFamilies(spec, parseFloats(*famFloors))
	must(err)
	eps = append(eps, fam...)

	must(os.MkdirAll(*outWorkflow, 0755))
	must(os.MkdirAll(*outWorkload, 0755))
	must(render(serviceTmpl, eps, filepath.Join(*outWorkflow, "service.go")))
	must(render(dispatchTmpl, eps, filepath.Join(*outWorkload, "dispatch.go")))
	js, _ := json.MarshalIndent(eps, "", "  ")
	must(os.WriteFile(filepath.Join(*outWorkflow, "floors.json"), js, 0644))
	fmt.Printf("generated %d endpoints (%d spin, %d complex)\n", len(eps), *k, len(eps)-*k)
}

// expandFamilies turns "memwalk=4,256;alloc=1" × floors into endpoints.
func expandFamilies(spec string, floors []float64) ([]endpoint, error) {
	var out []endpoint
	group := 0
	for _, part := range strings.Split(spec, ";") {
		part = strings.TrimSpace(part)
		if part == "" {
			continue
		}
		name, plist, ok := strings.Cut(part, "=")
		if !ok {
			return nil, fmt.Errorf("family %q: want name=p1,p2", part)
		}
		params := parseFloats(plist)
		if len(params) == 0 {
			return nil, fmt.Errorf("family %q: no parameters", name)
		}
		for _, p := range params {
			for _, f := range floors {
				base := fmt.Sprintf("%s%s_F%s", famPrefix[name], num(p), num(f))
				switch name {
				case "memwalk", "stream", "alloc", "syscall", "mixed":
					out = append(out, endpoint{Name: base, FloorMs: f, CpuMs: f, Kind: name, Param: p, Violator: true})
				case "mutex":
					out = append(out, endpoint{Name: base, FloorMs: f, CpuMs: f, Kind: name, Param: math.Min(p, f), Violator: true})
				case "lockshare":
					group++
					for j := 0; j < int(p); j++ {
						out = append(out, endpoint{Name: fmt.Sprintf("%s_%d", base, j), FloorMs: f, CpuMs: f, Kind: name, Param: p, Group: group, Violator: true})
					}
				case "sleep":
					out = append(out, endpoint{Name: base, FloorMs: round3(p + f), CpuMs: f, Kind: name, Param: p, Violator: true})
				case "fanout":
					out = append(out, endpoint{Name: base, FloorMs: f, CpuMs: round3(p * f), Kind: name, Param: p, Violator: true})
				case "varln":
					out = append(out, endpoint{Name: base, FloorMs: f, CpuMs: round3(f * math.Exp(p*p/2)), Kind: name, Param: p, Violator: true})
				case "varbi":
					out = append(out, endpoint{Name: base, FloorMs: f, CpuMs: round3(f * (1 + 9*p)), Kind: name, Param: p, Violator: true})
				default:
					return nil, fmt.Errorf("unknown family %q", name)
				}
			}
		}
	}
	return out, nil
}

var famPrefix = map[string]string{
	"memwalk": "MemWalk", "stream": "Stream", "alloc": "Alloc", "mutex": "Mutex", "lockshare": "LockShare",
	"sleep": "Sleep", "fanout": "Fanout", "varln": "VarLn", "varbi": "VarBi", "syscall": "Sys", "mixed": "Mixed",
}

// num renders a float as an identifier fragment: 0.25 → "0P25", 4 → "4".
// The decimal marker must be upper case: protoc-gen-go capitalises a lower-case
// letter that follows a digit ("0p25" → "0P25"), which would break the
// Blueprint-generated conversions that use the raw method name.
func num(x float64) string {
	s := strconv.FormatFloat(x, 'f', -1, 64)
	return strings.ReplaceAll(s, ".", "P")
}

func parseFloats(s string) []float64 {
	var out []float64
	for _, t := range strings.Split(s, ",") {
		t = strings.TrimSpace(t)
		if t == "" {
			continue
		}
		v, err := strconv.ParseFloat(t, 64)
		must(err)
		out = append(out, v)
	}
	return out
}

func round3(x float64) float64 { return math.Round(x*1000) / 1000 }

func must(err error) {
	if err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
}

func render(tmpl string, eps []endpoint, path string) error {
	t := template.Must(template.New("t").Parse(tmpl))
	var buf bytes.Buffer
	if err := t.Execute(&buf, eps); err != nil {
		return err
	}
	return os.WriteFile(path, buf.Bytes(), 0644)
}

var serviceTmpl = `// Code generated by examples/bigsvc/gen. DO NOT EDIT.
package bigsvc

import (
	"context"
)

// Endpoints lists every endpoint in declaration order with its configured floor.
var Endpoints = []EndpointSpec{
{{- range .}}
	{Name: "{{.Name}}", FloorMs: {{.FloorMs}}, CpuMs: {{.CpuMs}}, Kind: "{{.Kind}}", Param: {{.Param}}, Group: {{.Group}}, Violator: {{.Violator}}},
{{- end}}
}

// BigService is a single service with one endpoint per method. Every endpoint
// takes and returns an int64 so that marshalling cost is identical across
// endpoints; the endpoints differ only in the work they do.
type BigService interface {
{{- range .}}
	{{.Name}}(ctx context.Context, x int64) (int64, error)
{{- end}}
}
{{range $i, $e := .}}
func (s *BigServiceImpl) {{$e.Name}}(ctx context.Context, x int64) (int64, error) {
	return s.run({{$i}}, x)
}
{{end}}`

var dispatchTmpl = `// Code generated by examples/bigsvc/gen. DO NOT EDIT.
package workloadgen

import (
	"context"
	"fmt"

	"github.com/blueprint-uservices/blueprint/examples/bigsvc/workflow/bigsvc"
)

// Endpoints mirrors bigsvc.Endpoints (names, floors, kinds, violator flags).
var Endpoints = []string{
{{- range .}}
	"{{.Name}}",
{{- end}}
}

var Floors = []float64{
{{- range .}}
	{{.FloorMs}},
{{- end}}
}

var CpuMs = []float64{
{{- range .}}
	{{.CpuMs}},
{{- end}}
}

var Kinds = []string{
{{- range .}}
	"{{.Kind}}",
{{- end}}
}

var Params = []float64{
{{- range .}}
	{{.Param}},
{{- end}}
}

var IsViolator = []bool{
{{- range .}}
	{{.Violator}},
{{- end}}
}

// Call invokes endpoint i of svc.
func Call(ctx context.Context, svc bigsvc.BigService, i int, x int64) (int64, error) {
	switch i {
{{- range $i, $e := .}}
	case {{$i}}:
		return svc.{{$e.Name}}(ctx, x)
{{- end}}
	}
	return 0, fmt.Errorf("no endpoint %d", i)
}
`
