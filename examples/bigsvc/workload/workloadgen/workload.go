// Package workloadgen is an open-loop, mixed-endpoint load generator for
// BigService.
//
// Arrivals are Poisson at the current target rate; each arrival picks an
// endpoint from the current mix and runs in its own goroutine, so the offered
// load does not depend on the service's response time. The mix can be fixed
// (uniform or from a file) or resampled from a Dirichlet distribution every
// -resample seconds; the rate can be a single value or a schedule of steps.
//
// Everything the analysis needs is written as JSON lines to -out:
//
//	{"event":"run","t":..,"k":123,"endpoints":[..],"floors_ms":[..],"cpu_ms":[..],"kinds":[..],"params":[..],"violators":[..],"args":{..}}
//	{"event":"step","t":..,"step":0,"rate":100}
//	{"event":"mix","t":..,"mix":3,"w":[..]}                 (weights per endpoint, sum 1)
//	{"t":..,"step":0,"mix":3,"rate":100,"sent":..,"done":..,"err":..,"inflight":..,
//	 "all":[n,p50,p90,p99,mean],"ep":{"Ep000":[n,p50,p90,mean],..}}   (every second, ns)
//	{"event":"end","t":..,"sent":..,"done":..,"err":..}
package workloadgen

import (
	"bufio"
	"context"
	"encoding/json"
	"flag"
	"fmt"
	"math"
	"math/rand"
	"os"
	"sort"
	"strconv"
	"strings"
	"sync"
	"sync/atomic"
	"time"

	"github.com/blueprint-uservices/blueprint/examples/bigsvc/workflow/bigsvc"
)

// BigWorkload is the workload-generator interface Blueprint instantiates.
type BigWorkload interface {
	ImplementsBigWorkload(context.Context) error
}

var (
	fRate       = flag.Float64("rate", 100, "target arrival rate (req/s) when -schedule is empty")
	fSchedule   = flag.String("schedule", "", "rate schedule 'rate:seconds,rate:seconds,...' (overrides -rate/-duration)")
	fDuration   = flag.Float64("duration", 60, "seconds to run when -schedule is empty")
	fMix        = flag.String("mix", "uniform", "uniform | dirichlet | file:<path> | single:<Name> | weights:<Name=w,...>")
	fAlpha      = flag.Float64("alpha", 1.0, "Dirichlet concentration for -mix dirichlet")
	fResample   = flag.Float64("resample", 10, "seconds between mix resamples (dirichlet)")
	fViolatorW  = flag.Float64("violator-weight", 1.0, "relative weight of violator endpoints (0 excludes them)")
	fSeed       = flag.Int64("seed", 1, "random seed")
	fOut        = flag.String("out", "stdout", "output file for JSON lines")
	fTimeout    = flag.Duration("timeout", 10*time.Second, "per-request timeout")
	fMaxInfl    = flag.Int("max-inflight", 50000, "drop arrivals beyond this many in flight (counted as err)")
	fWarmup     = flag.Float64("warmup", 0, "seconds of warm-up at the first rate before step 0 starts (reported as step -1)")
	fRateJitter = flag.Bool("poisson", true, "Poisson arrivals (false = deterministic spacing)")
)

type workloadGen struct {
	BigWorkload
	svc bigsvc.BigService
}

// NewBigWorkload is the constructor Blueprint looks for.
func NewBigWorkload(ctx context.Context, svc bigsvc.BigService) (BigWorkload, error) {
	return &workloadGen{svc: svc}, nil
}

func (w *workloadGen) ImplementsBigWorkload(context.Context) error { return nil }

type step struct {
	rate float64
	dur  time.Duration
}

func parseSchedule(s string) ([]step, error) {
	var out []step
	for _, part := range strings.Split(s, ",") {
		part = strings.TrimSpace(part)
		if part == "" {
			continue
		}
		rs := strings.SplitN(part, ":", 2)
		if len(rs) != 2 {
			return nil, fmt.Errorf("bad schedule item %q", part)
		}
		r, err := strconv.ParseFloat(rs[0], 64)
		if err != nil {
			return nil, err
		}
		d, err := strconv.ParseFloat(rs[1], 64)
		if err != nil {
			return nil, err
		}
		out = append(out, step{rate: r, dur: time.Duration(d * float64(time.Second))})
	}
	if len(out) == 0 {
		return nil, fmt.Errorf("empty schedule")
	}
	return out, nil
}

// mixer produces endpoint indices according to a weight vector.
type mixer struct {
	w   []float64 // normalised weights
	cdf []float64
}

func newMixer(w []float64) *mixer {
	sum := 0.0
	for _, v := range w {
		sum += v
	}
	m := &mixer{w: make([]float64, len(w)), cdf: make([]float64, len(w))}
	acc := 0.0
	for i, v := range w {
		m.w[i] = v / sum
		acc += m.w[i]
		m.cdf[i] = acc
	}
	m.cdf[len(w)-1] = 1.0
	return m
}

func (m *mixer) pick(r *rand.Rand) int {
	u := r.Float64()
	return sort.SearchFloat64s(m.cdf, u)
}

// baseWeights gives 1 to normal endpoints and violator-weight to violators.
func baseWeights() []float64 {
	w := make([]float64, len(Endpoints))
	for i := range w {
		if IsViolator[i] {
			w[i] = *fViolatorW
		} else {
			w[i] = 1
		}
	}
	return w
}

func dirichlet(r *rand.Rand, alpha float64, base []float64) []float64 {
	w := make([]float64, len(base))
	for i := range w {
		if base[i] <= 0 {
			continue
		}
		// Gamma(alpha,1) via Marsaglia-Tsang (alpha>=1) or boost for alpha<1
		w[i] = gamma(r, alpha) * base[i]
	}
	return w
}

func gamma(r *rand.Rand, a float64) float64 {
	if a < 1 {
		return gamma(r, a+1) * math.Pow(r.Float64(), 1/a)
	}
	d := a - 1.0/3
	c := 1 / math.Sqrt(9*d)
	for {
		x := r.NormFloat64()
		v := 1 + c*x
		if v <= 0 {
			continue
		}
		v = v * v * v
		u := r.Float64()
		if u < 1-0.0331*x*x*x*x || math.Log(u) < 0.5*x*x+d*(1-v+math.Log(v)) {
			return d * v
		}
	}
}

func parseMix(r *rand.Rand) (*mixer, bool, error) {
	base := baseWeights()
	spec := *fMix
	switch {
	case spec == "uniform":
		return newMixer(base), false, nil
	case spec == "dirichlet":
		return newMixer(dirichlet(r, *fAlpha, base)), true, nil
	case strings.HasPrefix(spec, "single:"):
		name := strings.TrimPrefix(spec, "single:")
		w := make([]float64, len(Endpoints))
		for i, n := range Endpoints {
			if n == name {
				w[i] = 1
				return newMixer(w), false, nil
			}
		}
		return nil, false, fmt.Errorf("unknown endpoint %q", name)
	case strings.HasPrefix(spec, "weights:"):
		w := make([]float64, len(Endpoints))
		idx := map[string]int{}
		for i, n := range Endpoints {
			idx[n] = i
		}
		for _, kv := range strings.Split(strings.TrimPrefix(spec, "weights:"), ",") {
			p := strings.SplitN(kv, "=", 2)
			if len(p) != 2 {
				return nil, false, fmt.Errorf("bad weight %q", kv)
			}
			i, ok := idx[p[0]]
			if !ok {
				return nil, false, fmt.Errorf("unknown endpoint %q", p[0])
			}
			v, err := strconv.ParseFloat(p[1], 64)
			if err != nil {
				return nil, false, err
			}
			w[i] = v
		}
		return newMixer(w), false, nil
	case strings.HasPrefix(spec, "file:"):
		b, err := os.ReadFile(strings.TrimPrefix(spec, "file:"))
		if err != nil {
			return nil, false, err
		}
		var w []float64
		if err := json.Unmarshal(b, &w); err != nil {
			return nil, false, err
		}
		if len(w) != len(Endpoints) {
			return nil, false, fmt.Errorf("mix file has %d weights, want %d", len(w), len(Endpoints))
		}
		return newMixer(w), false, nil
	}
	return nil, false, fmt.Errorf("unknown -mix %q", spec)
}

// perEndpoint accumulates one second of client-side latencies.
type perEndpoint struct {
	mu   sync.Mutex
	lat  []int64
	errs int64
}

type stats struct {
	eps  []perEndpoint
	sent int64 // atomic
	done int64 // atomic
	errs int64 // atomic
	infl int64 // atomic
}

func (w *workloadGen) Run(ctx context.Context) error {
	r := rand.New(rand.NewSource(*fSeed))
	var out *bufio.Writer
	if *fOut == "" || *fOut == "stdout" {
		out = bufio.NewWriterSize(os.Stdout, 1<<20)
	} else {
		f, err := os.OpenFile(*fOut, os.O_CREATE|os.O_WRONLY|os.O_TRUNC, 0644)
		if err != nil {
			return err
		}
		defer f.Close()
		out = bufio.NewWriterSize(f, 1<<20)
	}
	defer out.Flush()
	emit := func(v any) {
		b, _ := json.Marshal(v)
		out.Write(b)
		out.WriteByte('\n')
	}

	var steps []step
	if *fSchedule != "" {
		var err error
		if steps, err = parseSchedule(*fSchedule); err != nil {
			return err
		}
	} else {
		steps = []step{{rate: *fRate, dur: time.Duration(*fDuration * float64(time.Second))}}
	}
	if *fWarmup > 0 {
		steps = append([]step{{rate: steps[0].rate, dur: time.Duration(*fWarmup * float64(time.Second))}}, steps...)
	}

	mix, resample, err := parseMix(r)
	if err != nil {
		return err
	}
	k := len(Endpoints)
	st := &stats{eps: make([]perEndpoint, k)}

	args := map[string]any{}
	flag.VisitAll(func(f *flag.Flag) { args[f.Name] = f.Value.String() })
	emit(map[string]any{"event": "run", "t": time.Now().UnixMilli(), "k": k, "endpoints": Endpoints,
		"floors_ms": Floors, "cpu_ms": CpuMs, "kinds": Kinds, "params": Params, "violators": IsViolator, "args": args})

	mixID := 0
	emit(map[string]any{"event": "mix", "t": time.Now().UnixMilli(), "mix": mixID, "w": mix.w})

	// 1 s reporter
	curStep := int64(-1)
	curRate := new(float64)
	stopRep := make(chan struct{})
	repDone := make(chan struct{})
	var curMix atomic.Int64
	go func() {
		defer close(repDone)
		tk := time.NewTicker(time.Second)
		defer tk.Stop()
		for {
			select {
			case <-stopRep:
				return
			case <-tk.C:
				w.report(st, emit, atomic.LoadInt64(&curStep), curMix.Load(), *curRate)
			}
		}
	}()

	firstStep := 0
	if *fWarmup > 0 {
		firstStep = -1
	}
	for si, s := range steps {
		stepID := int64(si + firstStep)
		atomic.StoreInt64(&curStep, stepID)
		*curRate = s.rate
		emit(map[string]any{"event": "step", "t": time.Now().UnixMilli(), "step": stepID, "rate": s.rate, "dur_s": s.dur.Seconds()})
		stepEnd := time.Now().Add(s.dur)
		nextResample := time.Now().Add(time.Duration(*fResample * float64(time.Second)))
		next := time.Now()
		for {
			if ctx.Err() != nil {
				return w.finish(st, emit, stopRep, repDone)
			}
			now := time.Now()
			if now.After(stepEnd) {
				break
			}
			if resample && now.After(nextResample) {
				mix = newMixer(dirichlet(r, *fAlpha, baseWeights()))
				mixID++
				curMix.Store(int64(mixID))
				emit(map[string]any{"event": "mix", "t": now.UnixMilli(), "mix": mixID, "w": mix.w})
				nextResample = now.Add(time.Duration(*fResample * float64(time.Second)))
			}
			// next arrival
			var gap time.Duration
			if *fRateJitter {
				gap = time.Duration(r.ExpFloat64() / s.rate * float64(time.Second))
			} else {
				gap = time.Duration(float64(time.Second) / s.rate)
			}
			next = next.Add(gap)
			sleepUntil(next)
			if time.Since(next) > 2*time.Second {
				// generator fell too far behind (should not happen); resync instead of bursting
				next = time.Now()
			}
			i := mix.pick(r)
			atomic.AddInt64(&st.sent, 1)
			if atomic.LoadInt64(&st.infl) >= int64(*fMaxInfl) {
				atomic.AddInt64(&st.errs, 1)
				st.eps[i].mu.Lock()
				st.eps[i].errs++
				st.eps[i].mu.Unlock()
				continue
			}
			atomic.AddInt64(&st.infl, 1)
			go w.one(ctx, st, i, r.Int63())
		}
	}
	return w.finish(st, emit, stopRep, repDone)
}

func sleepUntil(t time.Time) {
	for {
		d := time.Until(t)
		if d <= 0 {
			return
		}
		if d > 150*time.Microsecond {
			time.Sleep(d - 100*time.Microsecond)
		} else {
			// short busy-wait for precision
			for time.Now().Before(t) {
			}
			return
		}
	}
}

func (w *workloadGen) one(ctx context.Context, st *stats, i int, x int64) {
	cctx, cancel := context.WithTimeout(ctx, *fTimeout)
	t0 := time.Now()
	_, err := Call(cctx, w.svc, i, x)
	lat := time.Since(t0).Nanoseconds()
	cancel()
	atomic.AddInt64(&st.infl, -1)
	atomic.AddInt64(&st.done, 1)
	e := &st.eps[i]
	e.mu.Lock()
	if err != nil {
		e.errs++
		atomic.AddInt64(&st.errs, 1)
	} else {
		e.lat = append(e.lat, lat)
	}
	e.mu.Unlock()
}

func pct(sorted []int64, p float64) int64 {
	n := len(sorted)
	if n == 0 {
		return 0
	}
	rank := p * float64(n-1)
	lo := int(rank)
	hi := lo + 1
	if hi >= n {
		return sorted[n-1]
	}
	return sorted[lo] + int64(float64(sorted[hi]-sorted[lo])*(rank-float64(lo)))
}

func (w *workloadGen) report(st *stats, emit func(any), stepID, mixID int64, rate float64) {
	var all []int64
	ep := map[string][]int64{}
	for i := range st.eps {
		e := &st.eps[i]
		e.mu.Lock()
		lat := e.lat
		errs := e.errs
		e.lat = nil
		e.errs = 0
		e.mu.Unlock()
		if len(lat) == 0 && errs == 0 {
			continue
		}
		sort.Slice(lat, func(a, b int) bool { return lat[a] < lat[b] })
		var sum int64
		for _, v := range lat {
			sum += v
		}
		mean := int64(0)
		if len(lat) > 0 {
			mean = sum / int64(len(lat))
		}
		ep[Endpoints[i]] = []int64{int64(len(lat)), pct(lat, .5), pct(lat, .9), mean, errs}
		all = append(all, lat...)
	}
	sort.Slice(all, func(a, b int) bool { return all[a] < all[b] })
	var sum int64
	for _, v := range all {
		sum += v
	}
	mean := int64(0)
	if len(all) > 0 {
		mean = sum / int64(len(all))
	}
	emit(map[string]any{
		"t": time.Now().UnixMilli(), "step": stepID, "mix": mixID, "rate": rate,
		"sent": atomic.LoadInt64(&st.sent), "done": atomic.LoadInt64(&st.done), "err": atomic.LoadInt64(&st.errs),
		"inflight": atomic.LoadInt64(&st.infl),
		"all":      []int64{int64(len(all)), pct(all, .5), pct(all, .9), pct(all, .99), mean},
		"ep":       ep,
	})
}

func (w *workloadGen) finish(st *stats, emit func(any), stopRep, repDone chan struct{}) error {
	// let in-flight requests drain (bounded)
	deadline := time.Now().Add(*fTimeout + time.Second)
	for atomic.LoadInt64(&st.infl) > 0 && time.Now().Before(deadline) {
		time.Sleep(50 * time.Millisecond)
	}
	close(stopRep)
	<-repDone
	w.report(st, emit, -2, -1, 0)
	emit(map[string]any{"event": "end", "t": time.Now().UnixMilli(),
		"sent": atomic.LoadInt64(&st.sent), "done": atomic.LoadInt64(&st.done), "err": atomic.LoadInt64(&st.errs)})
	return nil
}
