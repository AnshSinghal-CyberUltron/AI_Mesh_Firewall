// synthprov is a deterministic OpenAI-compatible chat provider + recorder for the runbook
// validation harness (HARNESS_SPEC.md §1).
//
// Timing contract (all offsets on this host's monotonic clock, measured from "request body
// fully read" = recv):
//   - token j (1-based) is scheduled at recv + TTFT + (j-1)*ITL from ONE absolute schedule per
//     stream (no drift accumulation). emit_ns[j] = the instant token j was ready and handed to
//     Write (pre-write). recv_to_first_ns / recv_to_last_ns are the pre-write instants of the
//     first/last content token. Using pre-write instants means any time a gateway spends not
//     reading (backpressure) is charged to the gateway, never hidden inside provider time;
//     write_max_ns records the longest single write+flush so backpressure is visible.
//   - JSON (non-stream): the body is written when the last token would have been generated
//     (recv + TTFT + (n-1)*ITL); first == last == that instant.
//
// The request path is (almost) allocation-free: GC mark phases were measured to delay
// timer-driven goroutines by ~12 ms (evidence/harness-builder/runs/cal-1000-gctrace), so the
// default is GC off with a soft memory limit (-gc -1 -memlimit auto) and GC cycles are reported.
package main

import (
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"flag"
	"fmt"
	"hash"
	"io"
	"log"
	"net"
	"net/http"
	"os"
	"os/signal"
	"runtime"
	"strconv"
	"strings"
	"sync"
	"sync/atomic"
	"syscall"
	"time"

	"rvharness/internal/rv"
)

type config struct {
	listen      string
	ttft, itl   time.Duration
	defTokens   int
	tokensCap   int
	model       string
	record      string
	sampleMod   uint64
	hb          time.Duration
	cpuLog      string
	statsOut    string
	maxBody     int64
	fingerprint bool
	directives  bool
	canaryFile  string
	queue       int
}

type server struct {
	cfg     config
	gcDesc  string
	words   *rv.Words
	canary  *rv.CanarySet
	rec     *rv.JSONL
	created int64

	reqs, inflight, streamsActive, chunks, bytesOut atomic.Int64
	st2xx, st4xx, st5xx, clientGone, faults         atomic.Int64
	maxSchedErrNs, maxWriteNs                       atomic.Int64
	sampledN                                        atomic.Int64

	statePool sync.Pool
}

func (s *server) initPools() {
	s.statePool.New = func() any { return &reqState{body: make([]byte, 0, 16<<10), out: make([]byte, 0, 16<<10)} }
}

// reqState is pooled per request: body buffer, parsed request, output buffer.
type reqState struct {
	body []byte
	cr   rv.ChatReq
	out  []byte
}

// provRecord is one synthprov JSONL line (schema documented in USAGE.md).
type provRecord struct {
	RID           string   `json:"rid"`
	RIDSrc        string   `json:"rid_src"`
	RecvNs        int64    `json:"recv_ns"` // body fully read, ns since process start (same-host diagnostics only)
	Nonce         string   `json:"nonce,omitempty"`
	Stream        bool     `json:"stream"`
	Status        int      `json:"status"`
	BodyLen       int      `json:"body_len"`
	BodySHA       string   `json:"body_sha256"`
	BodyReadNs    int64    `json:"body_read_ns"`
	Canary        []string `json:"canary_hits"`
	MaxTokens     int      `json:"max_tokens_req"`
	TokensOut     int      `json:"tokens_out"`
	TTFTms        float64  `json:"ttft_ms"`
	ITLms         float64  `json:"itl_ms"`
	FirstNs       int64    `json:"recv_to_first_ns"`
	LastNs        int64    `json:"recv_to_last_ns"`
	LastWrittenNs int64    `json:"recv_to_last_written_ns"`
	SchedLastNs   int64    `json:"sched_last_ns"`
	SchedErrLast  int64    `json:"sched_err_last_ns"`
	SchedErrMax   int64    `json:"sched_err_max_ns"`
	WriteMaxNs    int64    `json:"write_max_ns"`
	ContentSHA    string   `json:"content_sha256"`
	ContentLen    int      `json:"content_len"`
	ToolSHA       string   `json:"tool_args_sha256,omitempty"`
	Inject        string   `json:"inject,omitempty"`
	InjectDone    bool     `json:"inject_applied,omitempty"`
	Fault         string   `json:"fault,omitempty"`
	Tool          bool     `json:"tool,omitempty"`
	ParamSrc      string   `json:"param_src,omitempty"`
	ClientGone    bool     `json:"client_gone,omitempty"`
	Err           string   `json:"err,omitempty"`
	Sampled       bool     `json:"sampled,omitempty"`
	EmitNs        []int64  `json:"emit_ns,omitempty"`
	EmitCum       []int    `json:"emit_cum,omitempty"`
}

func main() {
	var c config
	flag.StringVar(&c.listen, "listen", ":8080", "comma-separated listen addresses (one server per address)")
	flag.DurationVar(&c.ttft, "ttft", 150*time.Millisecond, "default time to first token (header x-synth-ttft-ms overrides)")
	flag.DurationVar(&c.itl, "itl", 20*time.Millisecond, "default inter-token interval (header x-synth-itl-ms overrides)")
	flag.IntVar(&c.defTokens, "tokens", 256, "output tokens when neither max_tokens nor x-synth-tokens is given")
	flag.IntVar(&c.tokensCap, "tokens-cap", 16384, "hard cap on output tokens")
	flag.StringVar(&c.model, "model", "rv-synth-1", "model id reported")
	flag.StringVar(&c.record, "record", "records.jsonl", "per-request JSONL record file")
	flag.Uint64Var(&c.sampleMod, "sample-mod", 10, "per-chunk timing sample: FNV1a64(rid)%mod==0 (0=off, 1=all)")
	flag.DurationVar(&c.hb, "hb", 100*time.Microsecond, "timer heartbeat period (0=off); see rv.StartHeartbeat")
	flag.StringVar(&c.cpuLog, "cpu-log", "", "per-second host CPU JSONL (empty=off)")
	flag.StringVar(&c.statsOut, "stats-out", "", "write final /_rv/stats JSON here on shutdown")
	flag.Int64Var(&c.maxBody, "max-body", 8<<20, "max request body bytes")
	flag.BoolVar(&c.fingerprint, "fingerprint", true, "include service_tier/system_fingerprint like api.openai.com")
	flag.BoolVar(&c.directives, "directives", true, "honour in-prompt rvsynth{...} directives when x-synth-* headers are absent")
	flag.StringVar(&c.canaryFile, "canaries", "", "canaries.json override (default: embedded)")
	flag.IntVar(&c.queue, "record-queue", 1<<18, "recorder queue length")
	gcPct := flag.Int("gc", -1, "GOGC percent (-1 = off; the heap is then bounded by -memlimit)")
	memlimit := flag.String("memlimit", "auto", "Go soft memory limit: auto (60% of RAM), none, or bytes (e.g. 8GiB)")
	flag.Parse()
	desc, err := rv.ConfigureGC(*gcPct, *memlimit)
	if err != nil {
		log.Fatal(err)
	}
	if err := rv.StartHeartbeat(c.hb); err != nil {
		log.Fatalf("heartbeat: %v", err)
	}
	words, err := rv.LoadWords()
	if err != nil {
		log.Fatal(err)
	}
	cs, err := rv.LoadCanaries(c.canaryFile)
	if err != nil {
		log.Fatal(err)
	}
	rec, err := rv.NewJSONL(c.record, c.queue)
	if err != nil {
		log.Fatal(err)
	}
	s := &server{cfg: c, gcDesc: desc, words: words, canary: cs, rec: rec, created: time.Now().Unix()}
	s.initPools()
	var cpu *rv.CPULog
	if c.cpuLog != "" {
		cpu = rv.StartCPULog(c.cpuLog, time.Second)
	}
	mux := http.NewServeMux()
	mux.HandleFunc("/v1/chat/completions", s.chat)
	mux.HandleFunc("/chat/completions", s.chat)
	mux.HandleFunc("/v1/models", s.models)
	mux.HandleFunc("/_rv/stats", s.statsHandler)
	mux.HandleFunc("/healthz", func(w http.ResponseWriter, r *http.Request) { w.Write([]byte("ok\n")) })

	var servers []*http.Server
	for _, addr := range strings.Split(c.listen, ",") {
		addr = strings.TrimSpace(addr)
		if addr == "" {
			continue
		}
		ln, err := net.Listen("tcp", addr)
		if err != nil {
			log.Fatalf("listen %s: %v", addr, err)
		}
		srv := &http.Server{Handler: mux, ReadHeaderTimeout: 60 * time.Second, IdleTimeout: 600 * time.Second,
			MaxHeaderBytes: 1 << 20, ErrorLog: log.New(io.Discard, "", 0)}
		servers = append(servers, srv)
		go func() {
			if err := srv.Serve(ln); err != nil && !errors.Is(err, http.ErrServerClosed) {
				log.Fatalf("serve %s: %v", addr, err)
			}
		}()
		log.Printf("synthprov %s listening on %s (ttft=%v itl=%v sample-mod=%d hb=%v gomaxprocs=%d %s)",
			rv.BuildSHA, addr, c.ttft, c.itl, c.sampleMod, c.hb, runtime.GOMAXPROCS(0), desc)
	}
	sig := make(chan os.Signal, 1)
	signal.Notify(sig, syscall.SIGINT, syscall.SIGTERM)
	<-sig
	log.Printf("shutting down")
	ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
	defer cancel()
	var wg sync.WaitGroup
	for _, srv := range servers {
		wg.Add(1)
		go func() { defer wg.Done(); srv.Shutdown(ctx) }()
	}
	wg.Wait()
	if err := rec.Close(); err != nil {
		log.Printf("record close: %v", err)
	}
	st := s.stats()
	if cpu != nil {
		st["cpu"] = summarizeCPU(cpu.Stop())
	}
	st["host"] = rv.CollectHostInfo()
	if c.statsOut != "" {
		b, _ := json.MarshalIndent(st, "", " ")
		os.WriteFile(c.statsOut, append(b, '\n'), 0o644)
	}
	log.Printf("final stats: requests=%v records=%v blocked=%v", st["requests"], st["records"], st["record_queue_blocked"])
}

func summarizeCPU(s []rv.CPUSample) map[string]float64 {
	out := map[string]float64{"samples": float64(len(s))}
	var maxB, sumB, maxP float64
	for _, x := range s {
		sumB += x.Busy
		maxB = max(maxB, x.Busy)
		maxP = max(maxP, x.Proc)
	}
	if len(s) > 0 {
		out["busy_mean"] = sumB / float64(len(s))
	}
	out["busy_max"] = maxB
	out["proc_max"] = maxP
	return out
}

func (s *server) stats() map[string]any {
	return map[string]any{
		"requests": s.reqs.Load(), "inflight": s.inflight.Load(), "streams_active": s.streamsActive.Load(),
		"chunks": s.chunks.Load(), "bytes_out": s.bytesOut.Load(), "status_2xx": s.st2xx.Load(),
		"status_4xx": s.st4xx.Load(), "status_5xx": s.st5xx.Load(), "client_gone": s.clientGone.Load(),
		"faults": s.faults.Load(), "sched_err_max_ns": s.maxSchedErrNs.Load(), "write_max_ns": s.maxWriteNs.Load(),
		"records": s.rec.Count(), "record_queue_blocked": s.rec.Blocked(), "sampled": s.sampledN.Load(),
		"ttft_ms": float64(s.cfg.ttft) / 1e6, "itl_ms": float64(s.cfg.itl) / 1e6, "sample_mod": s.cfg.sampleMod,
		"build_sha": rv.BuildSHA, "uptime_s": time.Since(rv.Epoch).Seconds(), "gc_config": s.gcDesc,
		"runtime": rv.RuntimeStats(),
	}
}

func (s *server) statsHandler(w http.ResponseWriter, r *http.Request) {
	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(s.stats())
}

func (s *server) models(w http.ResponseWriter, r *http.Request) {
	w.Header().Set("Content-Type", "application/json")
	fmt.Fprintf(w, `{"object":"list","data":[{"id":%q,"object":"model","created":%d,"owned_by":"rv-synth"}]}`+"\n", s.cfg.model, s.created)
}

// short8 returns at most the first 8 bytes of a request id (client ids can be any length).
func short8(s string) string {
	if len(s) > 8 {
		return s[:8]
	}
	return s
}

func atomicMax(a *atomic.Int64, v int64) {
	for {
		cur := a.Load()
		if v <= cur || a.CompareAndSwap(cur, v) {
			return
		}
	}
}

func writeErr(w http.ResponseWriter, status int, typ, code, msg string) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	b, _ := json.Marshal(map[string]any{"error": map[string]any{"message": msg, "type": typ, "param": nil, "code": code}})
	w.Write(append(b, '\n'))
}

// params are the per-request synthesis controls (headers win over in-prompt directives).
type params struct {
	tokens        int
	ttft, itl     time.Duration
	inject, fault string
	tool          bool
	src           string
}

// directive returns the value of key in an in-prompt "rvsynth{k=v;k=v}" directive (fallback for
// gateways that do not forward x-synth-* headers).
func directive(text []byte, key string) (string, bool) {
	i := bytes.Index(text, []byte("rvsynth{"))
	if i < 0 {
		return "", false
	}
	rest := text[i+len("rvsynth{"):]
	j := bytes.IndexByte(rest, '}')
	if j < 0 {
		return "", false
	}
	for _, kv := range bytes.Split(rest[:j], []byte{';'}) {
		if k, v, ok := bytes.Cut(kv, []byte{'='}); ok && string(bytes.TrimSpace(k)) == key {
			return string(bytes.TrimSpace(v)), true
		}
	}
	return "", false
}

// readBody reads the whole body into buf (pooled), honouring Content-Length when present.
func readBody(r *http.Request, buf []byte, limit int64) ([]byte, error) {
	if n := r.ContentLength; n > 0 {
		if n > limit {
			return buf[:0], fmt.Errorf("body %d bytes exceeds %d", n, limit)
		}
		if int64(cap(buf)) < n {
			buf = make([]byte, n)
		}
		buf = buf[:n]
		_, err := io.ReadFull(r.Body, buf)
		return buf, err
	}
	buf = buf[:0]
	for {
		if len(buf) == cap(buf) {
			buf = append(buf, 0)[:len(buf)]
		}
		m, err := r.Body.Read(buf[len(buf):cap(buf)])
		buf = buf[:len(buf)+m]
		if int64(len(buf)) > limit {
			return buf, fmt.Errorf("body exceeds %d bytes", limit)
		}
		if err == io.EOF {
			return buf, nil
		}
		if err != nil {
			return buf, err
		}
	}
}

func (s *server) chat(w http.ResponseWriter, r *http.Request) {
	entry := time.Now()
	s.reqs.Add(1)
	s.inflight.Add(1)
	defer s.inflight.Add(-1)
	st := s.statePool.Get().(*reqState)
	defer s.statePool.Put(st)
	rec := provRecord{}
	defer func() {
		switch {
		case rec.Status >= 500:
			s.st5xx.Add(1)
		case rec.Status >= 400:
			s.st4xx.Add(1)
		default:
			s.st2xx.Add(1)
		}
		s.rec.Write(&rec)
	}()
	if r.Method != http.MethodPost {
		rec.Status, rec.Err = 405, "method"
		writeErr(w, 405, "invalid_request_error", "method_not_allowed", "POST required")
		return
	}
	var err error
	st.body, err = readBody(r, st.body, s.cfg.maxBody)
	body := st.body
	recv := time.Now()
	rec.RecvNs = int64(recv.Sub(rv.Epoch))
	rec.BodyReadNs = int64(recv.Sub(entry))
	rec.BodyLen = len(body)
	sum := sha256.Sum256(body)
	rec.BodySHA = hex.EncodeToString(sum[:])
	rec.RID = r.Header.Get("x-request-id")
	rec.RIDSrc = "header"
	if err != nil {
		rec.Status, rec.Err = 413, "body_read"
		if rec.RID == "" {
			rec.RIDSrc = "none"
		}
		writeErr(w, 413, "invalid_request_error", "body_read_failed", err.Error())
		return
	}
	cr := &st.cr
	if err := rv.ParseChatRequest(body, cr); err != nil {
		rec.Status, rec.Err = 400, "bad_json"
		if rec.RID == "" {
			rec.RIDSrc = "none"
		}
		writeErr(w, 400, "invalid_request_error", "invalid_json", "request body is not valid JSON")
		return
	}
	text := cr.Text
	rec.Nonce = s.words.FindNonce(text)
	if rec.RID == "" {
		if rec.Nonce != "" {
			rec.RID, rec.RIDSrc = rv.RIDFromNonce(rec.Nonce), "nonce"
		} else {
			var b [8]byte
			for i := range b {
				b[i] = byte(rv.SplitMix64(uint64(time.Now().UnixNano()) + uint64(i)))
			}
			rec.RID, rec.RIDSrc = "synth-"+hex.EncodeToString(b[:]), "generated"
		}
	}
	rec.Canary = s.canary.Hits(body, text)
	rec.Stream = cr.Stream

	// ---- parameters: header > in-prompt directive > flag defaults ----
	p := params{tokens: -1, ttft: s.cfg.ttft, itl: s.cfg.itl}
	hasDirective := s.cfg.directives && bytes.Contains(text, []byte("rvsynth{"))
	get := func(name string) (string, string) {
		if v := r.Header.Get("x-synth-" + name); v != "" {
			return v, "header"
		}
		if hasDirective {
			if v, ok := directive(text, name); ok {
				return v, "text"
			}
		}
		return "", ""
	}
	note := func(src string) {
		if src != "" && !strings.Contains(p.src, src) {
			if p.src != "" {
				p.src += ","
			}
			p.src += src
		}
	}
	if v, src := get("tokens"); v != "" {
		if n, err := strconv.Atoi(v); err == nil && n >= 0 {
			p.tokens = n
			note(src)
		}
	}
	if v, src := get("ttft-ms"); v != "" {
		if f, err := strconv.ParseFloat(v, 64); err == nil && f >= 0 {
			p.ttft = time.Duration(f * 1e6)
			note(src)
		}
	}
	if v, src := get("itl-ms"); v != "" {
		if f, err := strconv.ParseFloat(v, 64); err == nil && f >= 0 {
			p.itl = time.Duration(f * 1e6)
			note(src)
		}
	}
	if v, src := get("inject"); v != "" && v != "none" {
		p.inject = v
		note(src)
	}
	if v, src := get("fault"); v != "" && v != "none" {
		p.fault = v
		note(src)
	}
	if v, src := get("tool"); v == "1" || v == "true" {
		p.tool = cr.NumTools > 0
		note(src)
	}
	rec.ParamSrc = p.src
	maxTok := cr.MaxTokens
	if maxTok < 0 {
		maxTok = cr.MaxCompletionTokens
	}
	rec.MaxTokens = maxTok
	n := s.cfg.defTokens
	switch {
	case p.tokens >= 0 && maxTok >= 0:
		n = min(p.tokens, maxTok)
	case p.tokens >= 0:
		n = p.tokens
	case maxTok >= 0:
		n = maxTok
	}
	n = min(max(n, 1), s.cfg.tokensCap)
	rec.TTFTms, rec.ITLms = float64(p.ttft)/1e6, float64(p.itl)/1e6
	rec.Inject, rec.Fault, rec.Tool = p.inject, p.fault, p.tool
	rec.Sampled = rv.Sampled(rec.RID, s.cfg.sampleMod)
	if rec.Sampled {
		s.sampledN.Add(1)
	}
	w.Header().Set("x-request-id", rec.RID)

	if p.fault == "prebyte-500" {
		s.faults.Add(1)
		rec.Status, rec.Err = 500, "fault_prebyte_500"
		writeErr(w, 500, "server_error", "synthetic_fault", "synthetic pre-byte 500")
		return
	}
	g := newGen(s, cr, &rec, p, n, recv, st)
	if cr.Stream {
		s.streamsActive.Add(1)
		defer s.streamsActive.Add(-1)
		g.stream(w)
	} else {
		g.json(w)
	}
}

// gen produces the deterministic token sequence for one request. Pieces are computed on the fly
// (no per-request slice); an injection is an overlay of 1..3 pieces at position insAt.
type gen struct {
	s       *server
	cr      *rv.ChatReq
	rec     *provRecord
	p       params
	n       int // base tokens
	recv    time.Time
	keyHash uint64
	ins     [][]byte
	insAt   int
	toolArg [][]byte // tool-call argument fragments when p.tool
	hasher  hash.Hash
	cum     int
	st      *reqState
}

func newGen(s *server, cr *rv.ChatReq, rec *provRecord, p params, n int, recv time.Time, st *reqState) *gen {
	g := &gen{s: s, cr: cr, rec: rec, p: p, n: n, recv: recv, keyHash: rv.FNV1a64(rec.RID), hasher: sha256.New(), st: st}
	if p.tool {
		g.buildTool()
		return g
	}
	g.insAt = n / 2
	switch p.inject {
	case "email":
		if c := s.canary.ByID("out.email"); c != nil {
			g.ins = [][]byte{[]byte(" " + c.Value)}
		}
	case "aws":
		if c := s.canary.ByID("out.aws"); c != nil {
			g.ins = [][]byte{[]byte(" " + c.Value)}
		}
	case "split-aws":
		if c := s.canary.ByID("out.aws"); c != nil && len(c.Split) == 3 {
			g.ins = [][]byte{[]byte(" " + c.Split[0]), []byte(c.Split[1]), []byte(c.Split[2])}
		}
	}
	rec.InjectDone = len(g.ins) > 0
	return g
}

func (g *gen) items() int {
	if g.p.tool {
		return len(g.toolArg)
	}
	return g.n + len(g.ins)
}

func (g *gen) piece(j int) []byte {
	if g.p.tool {
		return g.toolArg[j]
	}
	if k := len(g.ins); k > 0 && j >= g.insAt {
		if j < g.insAt+k {
			return g.ins[j-g.insAt]
		}
		return g.s.words.TokenB(g.keyHash, j-k)
	}
	return g.s.words.TokenB(g.keyHash, j)
}

// buildTool makes a deterministic arguments JSON and splits it into >=5 fragments at
// arbitrary byte offsets (mid-token), one fragment per chunk.
func (g *gen) buildTool() {
	var q []string
	for j := 0; j < 12; j++ {
		q = append(q, strings.TrimSpace(g.s.words.Token(g.keyHash, j)))
	}
	b, _ := json.Marshal(map[string]any{"query": strings.Join(q, " "), "limit": 5, "ref": short8(g.rec.RID)})
	args := string(b)
	frags := max(5, min(g.n, 16))
	if frags > len(args) {
		frags = len(args)
	}
	step := len(args) / frags
	for i := 0; i < frags; i++ {
		lo, hi := i*step, (i+1)*step
		if i == frags-1 {
			hi = len(args)
		}
		g.toolArg = append(g.toolArg, []byte(args[lo:hi]))
	}
}

func (g *gen) toolName() []byte {
	if len(g.cr.ToolName) > 0 {
		return g.cr.ToolName
	}
	return []byte("lookup")
}

func (g *gen) appendPrefix(b []byte) []byte {
	b = append(b, `{"id":"chatcmpl-`...)
	b = append(b, g.rec.RID...)
	b = append(b, `","object":"chat.completion.chunk","created":`...)
	b = strconv.AppendInt(b, g.s.created, 10)
	b = append(b, `,"model":"`...)
	b = rv.AppendJSONString(b, g.s.cfg.model)
	b = append(b, `",`...)
	if g.s.cfg.fingerprint {
		b = append(b, `"service_tier":"default","system_fingerprint":"fp_rvsynth01",`...)
	}
	return b
}

// parseFault: "disconnect-after:N", "malformed-after:N", "stall-after:N:MS"
func parseFault(f string) (kind string, n int, ms int) {
	parts := strings.Split(f, ":")
	kind = parts[0]
	if len(parts) > 1 {
		n, _ = strconv.Atoi(parts[1])
	}
	if len(parts) > 2 {
		ms, _ = strconv.Atoi(parts[2])
	}
	return
}

func (g *gen) stream(w http.ResponseWriter) {
	rec := g.rec
	fl, _ := w.(http.Flusher)
	hdr := w.Header()
	hdr.Set("Content-Type", "text/event-stream; charset=utf-8")
	hdr.Set("Cache-Control", "no-cache")
	w.WriteHeader(200)
	rec.Status = 200
	includeUsage := g.cr.IncludeUsage
	prefix := g.appendPrefix(make([]byte, 0, 256))
	usageNull := ""
	if includeUsage {
		usageNull = `,"usage":null`
	}
	fkind, fN, fMS := parseFault(g.p.fault)
	if g.p.fault != "" {
		g.s.faults.Add(1)
	}
	items := g.items()
	if rec.Sampled {
		rec.EmitNs = make([]int64, 0, items)
		rec.EmitCum = make([]int, 0, items)
	}
	buf := g.st.out[:0]
	defer func() { g.st.out = buf[:0] }()
	var stallShift time.Duration
	var schedErrMax int64
	for j := 0; j < items; j++ {
		sched := g.p.ttft + time.Duration(j)*g.p.itl + stallShift
		rv.SleepUntil(g.recv.Add(sched))
		ready := time.Now()
		off := int64(ready.Sub(g.recv))
		if e := off - int64(sched); e > schedErrMax {
			schedErrMax = e
		}
		buf = buf[:0]
		if j == 0 {
			// role chunk (OpenAI sends role first with empty content)
			buf = append(buf, "data: "...)
			buf = append(buf, prefix...)
			if g.p.tool {
				buf = append(buf, `"choices":[{"index":0,"delta":{"role":"assistant","content":null,"tool_calls":[{"index":0,"id":"call_`...)
				buf = append(buf, short8(rec.RID)...)
				buf = append(buf, `","type":"function","function":{"name":"`...)
				buf = rv.AppendJSONString(buf, g.toolName())
				buf = append(buf, `","arguments":""}}],"refusal":null},"logprobs":null,"finish_reason":null}]`...)
			} else {
				buf = append(buf, `"choices":[{"index":0,"delta":{"role":"assistant","content":"","refusal":null},"logprobs":null,"finish_reason":null}]`...)
			}
			buf = append(buf, usageNull...)
			buf = append(buf, "}\n\n"...)
		}
		piece := g.piece(j)
		buf = append(buf, "data: "...)
		buf = append(buf, prefix...)
		if g.p.tool {
			buf = append(buf, `"choices":[{"index":0,"delta":{"tool_calls":[{"index":0,"function":{"arguments":"`...)
			buf = rv.AppendJSONString(buf, piece)
			buf = append(buf, `"}}]},"logprobs":null,"finish_reason":null}]`...)
		} else {
			buf = append(buf, `"choices":[{"index":0,"delta":{"content":"`...)
			buf = rv.AppendJSONString(buf, piece)
			buf = append(buf, `"},"logprobs":null,"finish_reason":null}]`...)
		}
		buf = append(buf, usageNull...)
		buf = append(buf, "}\n\n"...)
		g.hasher.Write(piece)
		g.cum += len(piece)
		if fkind == "malformed-after" && j+1 == fN {
			buf = append(buf, "data: {\"choices\":[{\"index\":0,\"delta\":{\"content\":\"broken\n\n"...)
		}
		last := j == items-1
		if last {
			buf = append(buf, "data: "...)
			buf = append(buf, prefix...)
			if g.p.tool {
				buf = append(buf, `"choices":[{"index":0,"delta":{},"logprobs":null,"finish_reason":"tool_calls"}]`...)
			} else {
				buf = append(buf, `"choices":[{"index":0,"delta":{},"logprobs":null,"finish_reason":"stop"}]`...)
			}
			buf = append(buf, usageNull...)
			buf = append(buf, "}\n\n"...)
			if includeUsage {
				buf = append(buf, "data: "...)
				buf = append(buf, prefix...)
				buf = append(buf, `"choices":[],"usage":{"prompt_tokens":`...)
				buf = strconv.AppendInt(buf, int64(g.cr.NumMessages*10), 10)
				buf = append(buf, `,"completion_tokens":`...)
				buf = strconv.AppendInt(buf, int64(items), 10)
				buf = append(buf, `,"total_tokens":`...)
				buf = strconv.AppendInt(buf, int64(items+g.cr.NumMessages*10), 10)
				buf = append(buf, "}}\n\n"...)
			}
			buf = append(buf, "data: [DONE]\n\n"...)
		}
		if j == 0 {
			rec.FirstNs = off
		}
		rec.LastNs = off
		rec.SchedLastNs = int64(sched)
		if rec.Sampled {
			rec.EmitNs = append(rec.EmitNs, off)
			rec.EmitCum = append(rec.EmitCum, g.cum)
		}
		_, werr := w.Write(buf)
		if werr == nil && fl != nil {
			fl.Flush()
		}
		wdur := int64(time.Since(ready))
		if wdur > rec.WriteMaxNs {
			rec.WriteMaxNs = wdur
		}
		g.s.chunks.Add(1)
		g.s.bytesOut.Add(int64(len(buf)))
		rec.TokensOut = j + 1
		if werr != nil {
			rec.ClientGone, rec.Err = true, "client_write"
			g.s.clientGone.Add(1)
			break
		}
		if last {
			rec.LastWrittenNs = int64(time.Since(g.recv))
		}
		if fkind == "disconnect-after" && j+1 == fN && !last {
			rec.Err = "fault_disconnect"
			g.finish()
			panic(http.ErrAbortHandler) // net/http closes the connection without terminating the chunked body
		}
		if fkind == "stall-after" && j+1 == fN {
			stallShift += time.Duration(fMS) * time.Millisecond
		}
	}
	rec.SchedErrLast = rec.LastNs - rec.SchedLastNs
	rec.SchedErrMax = schedErrMax
	g.finish()
}

func (g *gen) finish() {
	rec := g.rec
	sum := g.hasher.Sum(nil)
	if g.p.tool {
		rec.ToolSHA = hex.EncodeToString(sum)
		empty := sha256.Sum256(nil)
		rec.ContentSHA = hex.EncodeToString(empty[:])
		rec.ContentLen = 0
	} else {
		rec.ContentSHA = hex.EncodeToString(sum)
		rec.ContentLen = g.cum
	}
	atomicMax(&g.s.maxSchedErrNs, rec.SchedErrMax)
	atomicMax(&g.s.maxWriteNs, rec.WriteMaxNs)
}

func (g *gen) json(w http.ResponseWriter) {
	rec := g.rec
	items := g.items()
	sched := g.p.ttft + time.Duration(max(items-1, 0))*g.p.itl
	rv.SleepUntil(g.recv.Add(sched))
	ready := time.Now()
	off := int64(ready.Sub(g.recv))
	b := g.st.out[:0]
	defer func() { g.st.out = b[:0] }()
	b = append(b, `{"id":"chatcmpl-`...)
	b = append(b, rec.RID...)
	b = append(b, `","object":"chat.completion","created":`...)
	b = strconv.AppendInt(b, g.s.created, 10)
	b = append(b, `,"model":"`...)
	b = rv.AppendJSONString(b, g.s.cfg.model)
	b = append(b, `","choices":[{"index":0,"message":{"role":"assistant","content":`...)
	if g.p.tool {
		b = append(b, `null,"tool_calls":[{"id":"call_`...)
		b = append(b, short8(rec.RID)...)
		b = append(b, `","type":"function","function":{"name":"`...)
		b = rv.AppendJSONString(b, g.toolName())
		b = append(b, `","arguments":"`...)
		for j := 0; j < items; j++ {
			pc := g.piece(j)
			b = rv.AppendJSONString(b, pc)
			g.hasher.Write(pc)
			g.cum += len(pc)
		}
		b = append(b, `"}}],"refusal":null},"logprobs":null,"finish_reason":"tool_calls"}]`...)
	} else {
		b = append(b, '"')
		for j := 0; j < items; j++ {
			pc := g.piece(j)
			b = rv.AppendJSONString(b, pc)
			g.hasher.Write(pc)
			g.cum += len(pc)
		}
		b = append(b, `","refusal":null},"logprobs":null,"finish_reason":"stop"}]`...)
	}
	b = append(b, `,"usage":{"prompt_tokens":`...)
	b = strconv.AppendInt(b, int64(g.cr.NumMessages*10), 10)
	b = append(b, `,"completion_tokens":`...)
	b = strconv.AppendInt(b, int64(items), 10)
	b = append(b, `,"total_tokens":`...)
	b = strconv.AppendInt(b, int64(items+g.cr.NumMessages*10), 10)
	b = append(b, '}')
	if g.s.cfg.fingerprint {
		b = append(b, `,"service_tier":"default","system_fingerprint":"fp_rvsynth01"`...)
	}
	b = append(b, "}\n"...)
	w.Header().Set("Content-Type", "application/json")
	w.Header().Set("Content-Length", strconv.Itoa(len(b)))
	w.WriteHeader(200)
	rec.Status = 200
	_, werr := w.Write(b)
	rec.FirstNs, rec.LastNs, rec.SchedLastNs = off, off, int64(sched)
	rec.SchedErrLast = off - int64(sched)
	rec.SchedErrMax = rec.SchedErrLast
	rec.WriteMaxNs = int64(time.Since(ready))
	rec.LastWrittenNs = int64(time.Since(g.recv))
	rec.TokensOut = items
	if rec.Sampled {
		rec.EmitNs = []int64{off}
		rec.EmitCum = []int{g.cum}
	}
	if werr != nil {
		rec.ClientGone, rec.Err = true, "client_write"
		g.s.clientGone.Add(1)
	}
	g.s.chunks.Add(1)
	g.s.bytesOut.Add(int64(len(b)))
	g.finish()
}
