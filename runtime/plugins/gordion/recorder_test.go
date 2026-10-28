package gordion

import (
	"context"
	"encoding/json"
	"os"
	"strings"
	"testing"
	"time"

	"github.com/blueprint-uservices/blueprint/runtime/plugins/grpcopts"
	"google.golang.org/grpc/metadata"
	"google.golang.org/grpc/tap"
)

func TestPauseAccounting(t *testing.T) {
	out := t.TempDir() + "/out.jsonl"
	t.Setenv("GORDION_OUT", out)
	t.Setenv("GORDION_WINDOW_MS", "20")
	t.Setenv("GORDION_RAW", "1")
	r, err := GetRecorder("svc", "Svc", []string{"A", "B"})
	if err != nil {
		t.Fatal(err)
	}
	// simulate the transport tap: arrival 3 ms before the handler starts
	tctx, err := tapHandle(context.Background(), &tap.Info{FullMethodName: "/grpc.Svc/B"})
	if err != nil {
		t.Fatal(err)
	}
	time.Sleep(3 * time.Millisecond)
	ctx, tok := r.Begin(tctx, 1)
	if tok.queue < int64(2*time.Millisecond) || tok.queue > int64(8*time.Millisecond) {
		t.Fatalf("queue=%v", time.Duration(tok.queue))
	}
	if len(grpcopts.ServerOptions()) != 1 {
		t.Fatalf("tap option not registered")
	}
	time.Sleep(2 * time.Millisecond)
	PauseBegin(ctx)
	PauseBegin(ctx) // nested call: still one blocked interval
	time.Sleep(5 * time.Millisecond)
	PauseEnd(ctx)
	PauseEnd(ctx)
	time.Sleep(1 * time.Millisecond)
	blocked := tok.st.pausedTotal
	if blocked < int64(4*time.Millisecond) || blocked > int64(10*time.Millisecond) {
		t.Fatalf("blocked=%v", time.Duration(blocked))
	}
	r.End(tok, false)
	time.Sleep(60 * time.Millisecond)
	r.Stop()
	b, err := os.ReadFile(out)
	if err != nil {
		t.Fatal(err)
	}
	s := string(b)
	if !strings.Contains(s, `"event":"start"`) || !strings.Contains(s, `"B":[1,1,`) || !strings.Contains(s, `"raw":{"B":[`) || !strings.Contains(s, `"rawq":{"B":[`) {
		t.Fatalf("unexpected output:\n%s", s)
	}
	// every line must be valid JSON
	for _, line := range strings.Split(strings.TrimSpace(s), "\n") {
		var v map[string]any
		if err := json.Unmarshal([]byte(line), &v); err != nil {
			t.Fatalf("bad json %v: %s", err, line)
		}
	}
}

func TestTapKey(t *testing.T) {
	for in, want := range map[string]string{"/grpc.Svc/M": "/Svc/M", "/Svc/M": "/Svc/M", "/a.b.Svc/M": "/Svc/M", "weird": "weird"} {
		if got := tapKeyOf(in); got != want {
			t.Fatalf("tapKeyOf(%q)=%q want %q", in, got, want)
		}
	}
}

func TestPct(t *testing.T) {
	s := []int64{10, 20, 30, 40, 50}
	if p := pct(s, 0.5); p != 30 {
		t.Fatalf("p50=%d", p)
	}
	if p := pct(s, 0.9); p != 46 {
		t.Fatalf("p90=%d", p)
	}
	if pct(nil, 0.5) != 0 {
		t.Fatal("empty")
	}
}

// TestStamp checks that a caller stamp travels through metadata into the pre stream.
func TestStamp(t *testing.T) {
	ctx := Stamp(context.Background())
	md, ok := metadata.FromOutgoingContext(ctx)
	if !ok || len(md.Get(sentKey)) != 1 {
		t.Fatalf("no stamp in outgoing metadata: %v", md)
	}
	in := metadata.NewIncomingContext(context.Background(), md)
	time.Sleep(2 * time.Millisecond)
	r, err := newRecorderOut("svc", []string{"A"}, "", false)
	if err != nil {
		t.Fatal(err)
	}
	_, tok := r.Begin(in, 0)
	if tok.pre < int64(2*time.Millisecond) || tok.pre > int64(time.Second) {
		t.Fatalf("pre = %d ns, want >= 2 ms", tok.pre)
	}
	_, tok2 := r.Begin(context.Background(), 0)
	if tok2.pre != -1 {
		t.Fatalf("pre without stamp = %d, want -1", tok2.pre)
	}
}

// TestFullLatency checks the f stream: for a stamped request the per-endpoint
// stats carry ns = 1 and f50 = pre + total >= x50; an unstamped request leaves
// ns = 0 so the analysis can tell an uninstrumented caller apart.
func TestFullLatency(t *testing.T) {
	out := t.TempDir() + "/full.jsonl"
	r, err := newRecorderOut("svc", []string{"A", "B"}, out, false)
	if err != nil {
		t.Fatal(err)
	}
	md, _ := metadata.FromOutgoingContext(Stamp(context.Background()))
	time.Sleep(2 * time.Millisecond)
	_, tok := r.Begin(metadata.NewIncomingContext(context.Background(), md), 0)
	time.Sleep(3 * time.Millisecond)
	r.End(tok, false)
	_, tok2 := r.Begin(context.Background(), 1)
	r.End(tok2, false)
	time.Sleep(150 * time.Millisecond)
	r.Stop()
	b, _ := os.ReadFile(out)
	var seen int
	for _, line := range strings.Split(strings.TrimSpace(string(b)), "\n") {
		var v struct {
			Ep map[string][]int64 `json:"ep"`
		}
		if err := json.Unmarshal([]byte(line), &v); err != nil {
			t.Fatalf("bad json %v: %s", err, line)
		}
		if a, ok := v.Ep["A"]; ok && a[1] == 1 {
			seen++
			if len(a) != 16 {
				t.Fatalf("ep stats len %d, want 16: %v", len(a), a)
			}
			ns, x50, f50, p50 := a[13], a[11], a[14], a[3]
			if ns != 1 || f50 < x50+p50 || x50 < int64(2*time.Millisecond) || p50 < int64(3*time.Millisecond) {
				t.Fatalf("ns=%d x50=%d p50=%d f50=%d", ns, x50, p50, f50)
			}
		}
		if bb, ok := v.Ep["B"]; ok && bb[1] == 1 && (bb[13] != 0 || bb[14] != 0) {
			t.Fatalf("unstamped request has ns/f50: %v", bb)
		}
	}
	if seen != 1 {
		t.Fatalf("stamped completion seen in %d windows, want 1", seen)
	}
}

// TestPreDoesNotAccumulate guards the window reset: a stamped request in one
// window must not reappear in later windows' rawx, and unstamped client-side
// completions must not contribute zeros to rawx.
func TestPreDoesNotAccumulate(t *testing.T) {
	out := t.TempDir() + "/pre.jsonl"
	t.Setenv("GORDION_OUT", out)
	t.Setenv("GORDION_WINDOW_MS", "20")
	t.Setenv("GORDION_RAW", "1")
	r, err := newRecorderOut("svc", []string{"A"}, out, false)
	if err != nil {
		t.Fatal(err)
	}
	md, _ := metadata.FromOutgoingContext(Stamp(context.Background()))
	_, tok := r.Begin(metadata.NewIncomingContext(context.Background(), md), 0)
	r.End(tok, false)
	c, _ := newRecorderOut("svc@client", []string{"A"}, out, false)
	for i := 0; i < 5; i++ {
		c.EndCall(c.BeginCall(0), false)
	}
	time.Sleep(150 * time.Millisecond) // several windows
	r.Stop()
	c.Stop()
	b, _ := os.ReadFile(out)
	n := strings.Count(string(b), `"rawx":{"A":[`)
	if n != 1 {
		t.Fatalf("rawx with samples appeared in %d windows, want exactly 1: %s", n, string(b))
	}
}
