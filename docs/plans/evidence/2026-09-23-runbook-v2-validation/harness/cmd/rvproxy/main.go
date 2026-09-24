// rvproxy is the instrument-honesty pass-through proxy (HARNESS_SPEC.md §5).
//
// It forwards /v1/* to one upstream and can inject two artificial delays:
//
//	-pre-delay D : wait D after the client request body is fully read, before dispatching upstream;
//	-hold-at N -hold H : the N-th upstream SSE event that carries content is held for H before it
//	                     is forwarded (later events are forwarded as soon as they are read).
//
// Every request logs the delays ACTUALLY injected (measured on this host's monotonic clock), so
// the harness's attribution can be checked against ground truth.
package main

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"flag"
	"io"
	"log"
	"net"
	"net/http"
	"os"
	"os/signal"
	"strings"
	"sync/atomic"
	"syscall"
	"time"

	"rvharness/internal/rv"
)

var hopHeaders = map[string]bool{"Connection": true, "Keep-Alive": true, "Proxy-Authenticate": true,
	"Proxy-Authorization": true, "Te": true, "Trailer": true, "Transfer-Encoding": true, "Upgrade": true,
	"Content-Length": true}

type proxy struct {
	upstream string
	client   *http.Client
	pre      time.Duration
	holdAt   int
	hold     time.Duration
	log      *rv.JSONL
	reqs     atomic.Int64
}

type proxyRecord struct {
	RID         string `json:"rid"`
	Status      int    `json:"status"`
	PreDelayNs  int64  `json:"pre_delay_actual_ns"`
	HoldNs      int64  `json:"hold_actual_ns"`
	HeldEvent   int    `json:"held_event,omitempty"`
	Events      int    `json:"events"`
	ContentEvts int    `json:"content_events"`
	Err         string `json:"err,omitempty"`
}

func main() {
	listen := flag.String("listen", ":9000", "listen address")
	upstream := flag.String("upstream", "http://127.0.0.1:8080", "upstream base URL")
	pre := flag.Duration("pre-delay", 0, "delay before upstream dispatch")
	holdAt := flag.Int("hold-at", 0, "hold the N-th content-bearing SSE event (0 = off)")
	hold := flag.Duration("hold", 30*time.Millisecond, "hold duration for -hold-at")
	logPath := flag.String("log", "proxy.jsonl", "per-request JSONL (actual injected delays)")
	hb := flag.Duration("hb", 100*time.Microsecond, "timer heartbeat (0=off)")
	gc := flag.Int("gc", -1, "GOGC percent (-1 = off; bounded by -memlimit)")
	memlimit := flag.String("memlimit", "auto", "Go soft memory limit: auto (60% of RAM), none, or bytes")
	flag.Parse()
	if _, err := rv.ConfigureGC(*gc, *memlimit); err != nil {
		log.Fatal(err)
	}
	if err := rv.StartHeartbeat(*hb); err != nil {
		log.Fatal(err)
	}
	l, err := rv.NewJSONL(*logPath, 1<<16)
	if err != nil {
		log.Fatal(err)
	}
	tr := &http.Transport{MaxIdleConns: 0, MaxIdleConnsPerHost: 1 << 20, IdleConnTimeout: 600 * time.Second,
		DisableCompression: true, ForceAttemptHTTP2: false}
	p := &proxy{upstream: strings.TrimRight(*upstream, "/"), pre: *pre, holdAt: *holdAt, hold: *hold, log: l,
		client: &http.Client{Transport: tr, CheckRedirect: func(*http.Request, []*http.Request) error { return http.ErrUseLastResponse }}}
	srv := &http.Server{Handler: p, ReadHeaderTimeout: 60 * time.Second, IdleTimeout: 600 * time.Second,
		ErrorLog: log.New(io.Discard, "", 0)}
	ln, err := net.Listen("tcp", *listen)
	if err != nil {
		log.Fatal(err)
	}
	go func() {
		if err := srv.Serve(ln); err != nil && !errors.Is(err, http.ErrServerClosed) {
			log.Fatal(err)
		}
	}()
	log.Printf("rvproxy %s %s -> %s pre-delay=%v hold-at=%d hold=%v", rv.BuildSHA, *listen, p.upstream, p.pre, p.holdAt, p.hold)
	sig := make(chan os.Signal, 1)
	signal.Notify(sig, syscall.SIGINT, syscall.SIGTERM)
	<-sig
	ctx, cancel := context.WithTimeout(context.Background(), 20*time.Second)
	defer cancel()
	srv.Shutdown(ctx)
	l.Close()
	log.Printf("rvproxy done: %d requests", p.reqs.Load())
}

func (p *proxy) ServeHTTP(w http.ResponseWriter, r *http.Request) {
	p.reqs.Add(1)
	rec := proxyRecord{RID: r.Header.Get("x-request-id")}
	defer func() { p.log.Write(&rec) }()
	body, err := io.ReadAll(r.Body)
	if err != nil {
		rec.Err, rec.Status = "body_read", 400
		http.Error(w, "bad body", 400)
		return
	}
	if p.pre > 0 {
		t0 := time.Now()
		rv.SleepUntil(t0.Add(p.pre))
		rec.PreDelayNs = int64(time.Since(t0))
	}
	up, _ := http.NewRequestWithContext(r.Context(), r.Method, p.upstream+r.URL.RequestURI(), bytes.NewReader(body))
	for k, vs := range r.Header {
		if !hopHeaders[k] {
			up.Header[k] = vs
		}
	}
	resp, err := p.client.Do(up)
	if err != nil {
		rec.Err, rec.Status = "upstream", 502
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(502)
		json.NewEncoder(w).Encode(map[string]any{"error": map[string]any{"message": "upstream error", "type": "server_error", "code": "upstream"}})
		return
	}
	defer resp.Body.Close()
	for k, vs := range resp.Header {
		if !hopHeaders[k] {
			w.Header()[k] = vs
		}
	}
	w.Header().Set("x-rv-disposition", "ALLOW")
	w.Header().Set("x-rv-stages", "proxy:E")
	rec.Status = resp.StatusCode
	if !strings.HasPrefix(resp.Header.Get("Content-Type"), "text/event-stream") {
		if cl := resp.Header.Get("Content-Length"); cl != "" {
			w.Header().Set("Content-Length", cl)
		}
		w.WriteHeader(resp.StatusCode)
		io.Copy(w, resp.Body)
		return
	}
	w.WriteHeader(resp.StatusCode)
	fl, _ := w.(http.Flusher)
	if fl != nil {
		fl.Flush()
	}
	sr := rv.NewSSEReader(resp.Body, 32<<10)
	var ch rv.Chunk
	buf := make([]byte, 0, 1024)
	for {
		data, ev, err := sr.Next()
		if err != nil {
			if err != io.EOF {
				rec.Err = "upstream_stream"
			}
			return
		}
		rec.Events++
		if string(data) != "[DONE]" && rv.ParseChunk(data, &ch) == nil &&
			((ch.ContentPresent && len(ch.Content) > 0) || len(ch.Tools) > 0) {
			rec.ContentEvts++
			if p.holdAt > 0 && rec.ContentEvts == p.holdAt {
				t0 := time.Now()
				rv.SleepUntil(t0.Add(p.hold))
				rec.HoldNs = int64(time.Since(t0))
				rec.HeldEvent = rec.ContentEvts
			}
		}
		buf = buf[:0]
		if len(ev) > 0 {
			buf = append(buf, "event: "...)
			buf = append(buf, ev...)
			buf = append(buf, '\n')
		}
		for _, line := range bytes.Split(data, []byte{'\n'}) {
			buf = append(buf, "data: "...)
			buf = append(buf, line...)
			buf = append(buf, '\n')
		}
		buf = append(buf, '\n')
		if _, err := w.Write(buf); err != nil {
			rec.Err = "client_write"
			return
		}
		if fl != nil {
			fl.Flush()
		}
	}
}
