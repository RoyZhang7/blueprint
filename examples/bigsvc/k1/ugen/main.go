// ugen: open-loop load generator for SocialNetwork's UniqueIdService (Phase K1). Speaks Thrift framed transport +
// strict binary protocol by hand (no thrift library): ComposeUniqueId(1: i64 req_id, 2: PostType post_type,
// 3: map<string,string> carrier). Poisson arrivals at the scheduled rate are queued to a pool of persistent connections
// (the server is TThreadedServer: one thread per connection); one request in flight per connection.
//
// usage: ugen -addr 10.0.0.1:9090 -schedule "2000:60,4000:60" [-conns 64] [-warmup 5] -out ugen.jsonl
// Output: one JSON line per second {t, rate, sent, done, err, backlog, lat_p50_us, lat_p99_us, svc_p50_us} (lat = from the
// scheduled arrival, i.e. including client-side queueing; svc = send -> reply) and a final summary line.
package main

import (
	"bufio"
	"encoding/binary"
	"encoding/json"
	"flag"
	"fmt"
	"io"
	"math/rand"
	"net"
	"os"
	"sort"
	"strconv"
	"strings"
	"sync"
	"sync/atomic"
	"time"
)

type step struct {
	rate float64
	secs float64
}

func parseSchedule(s string) []step {
	var out []step
	for _, p := range strings.Split(s, ",") {
		kv := strings.SplitN(strings.TrimSpace(p), ":", 2)
		if len(kv) != 2 {
			continue
		}
		r, _ := strconv.ParseFloat(kv[0], 64)
		d, _ := strconv.ParseFloat(kv[1], 64)
		out = append(out, step{r, d})
	}
	return out
}

func request(seq int32, reqID int64) []byte {
	name := "ComposeUniqueId"
	b := make([]byte, 0, 64)
	b = binary.BigEndian.AppendUint32(b, 0) // frame length placeholder
	b = binary.BigEndian.AppendUint32(b, 0x80010001)
	b = binary.BigEndian.AppendUint32(b, uint32(len(name)))
	b = append(b, name...)
	b = binary.BigEndian.AppendUint32(b, uint32(seq))
	b = append(b, 10)                     // i64
	b = binary.BigEndian.AppendUint16(b, 1)
	b = binary.BigEndian.AppendUint64(b, uint64(reqID))
	b = append(b, 8) // i32 (enum)
	b = binary.BigEndian.AppendUint16(b, 2)
	b = binary.BigEndian.AppendUint32(b, 0)
	b = append(b, 13) // map
	b = binary.BigEndian.AppendUint16(b, 3)
	b = append(b, 11, 11)
	b = binary.BigEndian.AppendUint32(b, 0)
	b = append(b, 0) // stop
	binary.BigEndian.PutUint32(b[0:4], uint32(len(b)-4))
	return b
}

type rec struct {
	arr, sent, done time.Time
	err            bool
}

func main() {
	addr := flag.String("addr", "127.0.0.1:9090", "service address")
	sched := flag.String("schedule", "1000:10", "rate:seconds,...")
	conns := flag.Int("conns", 64, "persistent connections")
	warm := flag.Float64("warmup", 0, "seconds at the first step's rate before the schedule (not separated in the output)")
	outp := flag.String("out", "ugen.jsonl", "output")
	flag.Parse()
	steps := parseSchedule(*sched)
	if *warm > 0 && len(steps) > 0 {
		steps = append([]step{{steps[0].rate, *warm}}, steps...)
	}
	out, err := os.Create(*outp)
	if err != nil {
		panic(err)
	}
	w := bufio.NewWriter(out)
	defer func() { w.Flush(); out.Close() }()
	queue := make(chan time.Time, 1<<20)
	results := make(chan rec, 1<<16)
	var wg sync.WaitGroup
	var reqID int64
	for i := 0; i < *conns; i++ {
		c, err := net.DialTimeout("tcp", *addr, 5*time.Second)
		if err != nil {
			fmt.Fprintln(os.Stderr, "dial:", err)
			os.Exit(1)
		}
		wg.Add(1)
		go func(c net.Conn) {
			defer wg.Done()
			br := bufio.NewReaderSize(c, 4096)
			hdr := make([]byte, 4)
			var seq int32
			for arr := range queue {
				seq++
				id := atomic.AddInt64(&reqID, 1)
				sent := time.Now()
				r := rec{arr: arr, sent: sent}
				if _, err := c.Write(request(seq, id)); err != nil {
					r.err = true
				} else if _, err := io.ReadFull(br, hdr); err != nil {
					r.err = true
				} else {
					n := binary.BigEndian.Uint32(hdr)
					body := make([]byte, n)
					if _, err := io.ReadFull(br, body); err != nil || n < 4 || binary.BigEndian.Uint32(body[0:4])&0xff != 2 {
						r.err = true
					}
				}
				r.done = time.Now()
				results <- r
			}
		}(c)
	}
	// collector: per-second lines
	var collWG sync.WaitGroup
	collWG.Add(1)
	var sentTotal int64
	go func() {
		defer collWG.Done()
		enc := json.NewEncoder(w)
		var lat, svc []float64
		var done, errs int
		var all, allSvc []float64
		tick := time.NewTicker(time.Second)
		defer tick.Stop()
		flush := func(t time.Time) {
			sort.Float64s(lat)
			sort.Float64s(svc)
			q := func(a []float64, p float64) float64 {
				if len(a) == 0 {
					return 0
				}
				return a[int(p*float64(len(a)-1))]
			}
			enc.Encode(map[string]interface{}{"t": t.UnixMilli(), "done": done, "err": errs, "sent": atomic.LoadInt64(&sentTotal), "backlog": len(queue),
				"lat_p50_us": q(lat, .5), "lat_p99_us": q(lat, .99), "svc_p50_us": q(svc, .5), "svc_p99_us": q(svc, .99)})
			lat, svc, done, errs = lat[:0], svc[:0], 0, 0
		}
		for {
			select {
			case r, ok := <-results:
				if !ok {
					flush(time.Now())
					sort.Float64s(all)
					sort.Float64s(allSvc)
					q := func(a []float64, p float64) float64 {
						if len(a) == 0 {
							return 0
						}
						return a[int(p*float64(len(a)-1))]
					}
					enc.Encode(map[string]interface{}{"event": "end", "n": len(all), "lat_p50_us": q(all, .5), "lat_p99_us": q(all, .99), "svc_p50_us": q(allSvc, .5), "svc_p99_us": q(allSvc, .99)})
					return
				}
				if r.err {
					errs++
					continue
				}
				done++
				l := float64(r.done.Sub(r.arr).Microseconds())
				s := float64(r.done.Sub(r.sent).Microseconds())
				lat = append(lat, l)
				svc = append(svc, s)
				if len(all) < 5_000_000 {
					all = append(all, l)
					allSvc = append(allSvc, s)
				}
			case t := <-tick.C:
				flush(t)
			}
		}
	}()
	// arrivals
	rng := rand.New(rand.NewSource(time.Now().UnixNano()))
	next := time.Now()
	for _, st := range steps {
		end := time.Now().Add(time.Duration(st.secs * float64(time.Second)))
		if st.rate <= 0 {
			time.Sleep(time.Until(end))
			next = time.Now()
			continue
		}
		for {
			next = next.Add(time.Duration(rng.ExpFloat64() / st.rate * float64(time.Second)))
			if next.After(end) {
				break
			}
			if d := time.Until(next); d > 0 {
				time.Sleep(d)
			}
			queue <- next
			atomic.AddInt64(&sentTotal, 1)
		}
		next = end
	}
	close(queue)
	wg.Wait()
	close(results)
	collWG.Wait()
}
