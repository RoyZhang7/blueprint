package gordion

import (
	"context"
	"encoding/json"
	"os"
	"strings"
	"testing"
	"time"

	"github.com/blueprint-uservices/blueprint/runtime/plugins/grpcopts"
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
