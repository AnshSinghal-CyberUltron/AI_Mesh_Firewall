// olg is the open-loop load generator of the runbook validation harness (HARNESS_SPEC.md §2).
//
// Arrivals follow a precomputed schedule (constant or Poisson, optional linear ramp + warm-up)
// that never depends on completions: request i is launched in its own goroutine at its
// scheduled instant. Lateness L_i = send_start - scheduled; L_i > drop threshold is a schedule
// DROP and makes a run invalid for capacity claims.
//
// Timestamps (ns on this host's monotonic clock, relative to send_start unless noted):
//
//	send_start = immediately before http.Client.Do (after the body is built);
//	conn_ns = connection obtained (includes dial when not reused);
//	fb_ns = first response byte; hdr_ns = headers parsed;
//	first_ns = arrival of the first SSE event carrying non-empty content / tool-call arguments
//	           (JSON: body complete); end_ns = arrival of "data: [DONE]" (JSON: body complete).
//
// "Arrival" = completion time of the socket read that delivered the event's final byte
// (a net.Conn wrapper stamps every Read), not the time the parser got around to it.
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
	"io"
	"log"
	"math"
	"math/rand/v2"
	"net"
	"net/http"
	"net/http/httptrace"
	"os"
	"os/signal"
	"path/filepath"
	"runtime"
	"runtime/trace"
	"slices"
	"strconv"
	"strings"
	"sync"
	"sync/atomic"
	"syscall"
	"time"

	"rvharness/internal/rv"
)

const placeholder = "{{RVNONCE}}"

type headerList []string

func (h *headerList) String() string     { return strings.Join(*h, ";") }
func (h *headerList) Set(v string) error { *h = append(*h, v); return nil }

type config struct {
	Targets      []string `json:"targets"`
	Path         string   `json:"path"`
	Corpus       string   `json:"corpus"`
	CorpusSHA    string   `json:"corpus_sha256"`
	Rate         float64  `json:"rate"`
	Duration     float64  `json:"duration_s"`
	Warmup       float64  `json:"warmup_s"`
	Ramp         float64  `json:"ramp_s"`
	Arrival      string   `json:"arrival"`
	Seed         uint64   `json:"seed"`
	SSEFrac      float64  `json:"sse_frac"`
	IncludeUsage bool     `json:"include_usage"`
	LG           int      `json:"lg_index"`
	LGCount      int      `json:"lg_count"`
	RunTag       int      `json:"run_tag"`
	RunID        string   `json:"run_id"`
	Out          string   `json:"out_dir"`
	SampleMod    uint64   `json:"sample_mod"`
	ReqTimeout   float64  `json:"req_timeout_s"`
	DrainTimeout float64  `json:"drain_timeout_s"`
	DropNs       int64    `json:"drop_threshold_ns"`
	MaxInflight  int64    `json:"max_inflight"`
	MaxRequests  int64    `json:"max_requests"`
	HB           string   `json:"heartbeat"`
	StartAt      int64    `json:"start_at_unix_ms"`
	Model        string   `json:"model_override"`
	SynthHeaders bool     `json:"synth_headers"`
	ExtraHeaders []string `json:"extra_header_names"`
	GCPercent    int      `json:"gc_percent"`
	MemLimit     string   `json:"mem_limit"`
	GCDesc       string   `json:"gc_config"`
	DialTimeout  float64  `json:"dial_timeout_s"`
	AuthSet      bool     `json:"auth_set"`
	RTPrio       int      `json:"rt_prio"`
	Dispatchers  int      `json:"dispatchers"`
	StaggerUs    float64  `json:"dispatch_stagger_us"`
	RTStatus     string   `json:"rt_status"`
	Label        string   `json:"label"`
}

type corpusEntry struct {
	ID        string            `json:"id"`
	Class     string            `json:"class"`
	Messages  json.RawMessage   `json:"messages"`
	Stream    *bool             `json:"stream"`
	MaxTokens int               `json:"max_tokens"`
	TokensIn  int               `json:"tokens_in"`
	Synth     map[string]string `json:"synth"`
	Tools     json.RawMessage   `json:"tools"`
	Model     string            `json:"model"`
}

type tmpl struct{ pre, post []byte }

type entry struct {
	e      corpusEntry
	body   [2]tmpl // [0]=json, [1]=stream
	synthH [][2]string
}

// reqRecord is one olg JSONL line (schema documented in USAGE.md).
type reqRecord struct {
	RID        string   `json:"rid"`
	LG         int      `json:"lg"`
	Seq        uint32   `json:"seq"`
	Ph         int8     `json:"ph"`
	Cls        string   `json:"cls"`
	CID        string   `json:"cid"`
	Stream     bool     `json:"stream"`
	TokIn      int      `json:"tokens_in"`
	MaxTok     int      `json:"max_tokens"`
	Target     int      `json:"target"`
	SchedNs    int64    `json:"sched_ns"`
	LateNs     int64    `json:"late_ns"`
	ConnNs     int64    `json:"conn_ns"`
	Reused     bool     `json:"reused"`
	WroteNs    int64    `json:"wrote_ns"`
	FBNs       int64    `json:"fb_ns"`
	HdrNs      int64    `json:"hdr_ns"`
	FirstNs    int64    `json:"first_ns"`
	LastCNs    int64    `json:"last_content_ns"`
	EndNs      int64    `json:"end_ns"`
	Status     int      `json:"status"`
	Err        string   `json:"err,omitempty"`
	ErrDetail  string   `json:"err_detail,omitempty"`
	Chunks     int      `json:"chunks"`
	Events     int      `json:"events"`
	Bytes      int64    `json:"bytes"`
	ContentSHA string   `json:"content_sha256"`
	ContentLen int      `json:"content_len"`
	ToolSHA    string   `json:"tool_args_sha256,omitempty"`
	Done       bool     `json:"done_seen"`
	Finish     string   `json:"finish,omitempty"`
	Usage      bool     `json:"usage_seen,omitempty"`
	Malformed  int      `json:"malformed,omitempty"`
	ErrEvent   bool     `json:"error_event,omitempty"`
	PostDone   int      `json:"post_done_events,omitempty"`
	PostHang   bool     `json:"post_done_hang,omitempty"`
	Disp       string   `json:"disp,omitempty"`
	PlanVer    string   `json:"plan_ver,omitempty"`
	Stages     string   `json:"stages,omitempty"`
	RespRID    string   `json:"resp_rid,omitempty"`
	CType      string   `json:"ctype,omitempty"`
	Canary     []string `json:"canary_hits,omitempty"`
	Nonce      string   `json:"nonce"`
	Inject     string   `json:"inject,omitempty"`
	Fault      string   `json:"fault,omitempty"`
	Sampled    bool     `json:"sampled,omitempty"`
	ArrNs      []int64  `json:"arr_ns,omitempty"`
	ArrCum     []int    `json:"arr_cum,omitempty"`
}

type timedConn struct {
	net.Conn
	lastRead atomic.Int64 // rv.NowNs() after the most recent Read that returned data
}

func (c *timedConn) Read(p []byte) (int, error) {
	n, err := c.Conn.Read(p)
	if n > 0 {
		c.lastRead.Store(rv.NowNs())
	}
	return n, err
}

type gen struct {
	cfg      config
	words    *rv.Words
	canary   *rv.CanarySet
	entries  []entry
	client   *http.Client
	auth     string
	extra    [][2]string
	rec      *rv.JSONL
	epoch    time.Time
	baseCtx  context.Context
	draining atomic.Bool

	scheduled, sent, recorded, inflight, maxInflight, okDone, errs, drops, connOpens atomic.Int64
	secLateMax                                                                       atomic.Int64
	errMu                                                                            sync.Mutex
	errByClass                                                                       map[string]int64
	lateMu                                                                           sync.Mutex
	lateUs                                                                           []int32

	chunkPool sync.Pool
	bufPool   sync.Pool
	ssePool   sync.Pool
	bodyPool  sync.Pool

	// optional execution-trace flight recorder: dumps the last seconds of runtime trace when a
	// request's lateness exceeds flightNs (diagnosing dispatcher/scheduler stalls)
	claims [8]atomic.Int64 // arrivals claimed per dispatcher (manifest: shows how often backups covered)

	fr       *trace.FlightRecorder
	flightNs int64
	flightMu sync.Mutex
	dumps    atomic.Int32
}

func main() {
	var c config
	var targets, auth, authFile, hb string
	var extra headerList
	var dropMs, reqTimeout, drainTimeout, dialTimeout float64
	flag.StringVar(&targets, "targets", "http://127.0.0.1:8080", "comma-separated base URLs (client-side round robin)")
	flag.StringVar(&c.Path, "path", "/v1/chat/completions", "request path")
	flag.StringVar(&c.Corpus, "corpus", "corpus.jsonl", "corpus JSONL (make_corpus.py)")
	flag.Float64Var(&c.Rate, "rate", 100, "arrival rate (req/s) during warm-up and measurement")
	flag.Float64Var(&c.Duration, "duration", 60, "measurement phase seconds")
	flag.Float64Var(&c.Warmup, "warmup", 10, "warm-up seconds at full rate (excluded by the analyzer)")
	flag.Float64Var(&c.Ramp, "ramp", 0, "linear ramp seconds from 0 to rate before warm-up (excluded)")
	flag.StringVar(&c.Arrival, "arrival", "constant", "constant|poisson")
	flag.Uint64Var(&c.Seed, "seed", 1, "PRNG seed (corpus pick, Poisson gaps)")
	flag.Float64Var(&c.SSEFrac, "sse-frac", 0.7, "fraction of SSE requests (exact, evenly interleaved); <0 = use corpus stream field")
	flag.BoolVar(&c.IncludeUsage, "include-usage", true, "send stream_options.include_usage on SSE requests")
	flag.IntVar(&c.LG, "lg", 0, "loadgen index (0..255); part of every nonce")
	flag.IntVar(&c.LGCount, "lg-count", 1, "number of loadgens sharing the run (constant arrivals are phase-offset)")
	flag.IntVar(&c.RunTag, "run-tag", -1, "nonce run tag 0..255 (-1 = derive from seed and start time)")
	flag.StringVar(&c.RunID, "run-id", "", "free-form run id recorded in the manifest")
	flag.StringVar(&c.Label, "label", "", "free-form label recorded in the manifest")
	flag.StringVar(&c.Out, "out", "olg-out", "output directory")
	flag.Uint64Var(&c.SampleMod, "sample-mod", 10, "per-chunk timing sample FNV1a64(rid)%mod==0 (must match synthprov)")
	flag.Float64Var(&reqTimeout, "req-timeout", 120, "per-request timeout seconds")
	flag.Float64Var(&drainTimeout, "drain-timeout", 120, "seconds to wait for in-flight requests after the schedule ends")
	flag.Float64Var(&dropMs, "drop-ms", 5, "schedule lateness above this is a DROP")
	flag.Int64Var(&c.MaxInflight, "max-inflight", 0, "0 = unlimited; above it requests are recorded as err=inflight_cap without sending")
	flag.Int64Var(&c.MaxRequests, "max-requests", 0, "stop scheduling after N requests (0 = no limit)")
	flag.StringVar(&hb, "hb", "100us", "timer heartbeat period (0 = off)")
	flag.Int64Var(&c.StartAt, "start-at", 0, "unix ms wall-clock start (synchronise several loadgens); 0 = now")
	flag.StringVar(&c.Model, "model", "", "override the model field of every request")
	flag.BoolVar(&c.SynthHeaders, "synth-headers", true, "send corpus synth{} fields as x-synth-* headers")
	flag.StringVar(&auth, "auth", "", "Authorization header value (prefer -auth-file / RV_AUTH; never logged)")
	flag.StringVar(&authFile, "auth-file", "", "file holding the Authorization header value")
	flag.Var(&extra, "H", "extra header 'Name: value' (repeatable)")
	flag.IntVar(&c.GCPercent, "gc", -1, "GOGC percent (-1 = off; the heap is then bounded by -memlimit)")
	flag.StringVar(&c.MemLimit, "memlimit", "auto", "Go soft memory limit: auto (60% of RAM), none, or bytes (e.g. 8GiB)")
	flag.Float64Var(&dialTimeout, "dial-timeout", 10, "dial timeout seconds")
	flag.IntVar(&c.Dispatchers, "dispatchers", 3, "redundant dispatcher goroutines (each sleeps in the kernel; first to wake claims)")
	flag.Float64Var(&c.StaggerUs, "dispatch-stagger-us", 250, "wake offset between redundant dispatchers (microseconds)")
	flag.IntVar(&c.RTPrio, "rt-prio", 0, "SCHED_FIFO priority for the dispatcher thread (0 = off; measured worse at 6000 RPS, runs/val-6000-r2)")
	flightDir := flag.String("flight-dir", "", "enable the execution-trace flight recorder; dumps go here (diagnostics only)")
	flightMs := flag.Float64("flight-ms", 1.5, "dump the flight recorder when a request is later than this")
	flightMax := flag.Int("flight-max", 3, "maximum flight-recorder dumps")
	flag.Parse()

	c.ReqTimeout, c.DrainTimeout, c.DialTimeout = reqTimeout, drainTimeout, dialTimeout
	c.DropNs = int64(dropMs * 1e6)
	c.HB = hb
	for _, t := range strings.Split(targets, ",") {
		if t = strings.TrimRight(strings.TrimSpace(t), "/"); t != "" {
			c.Targets = append(c.Targets, t)
		}
	}
	if len(c.Targets) == 0 || c.Rate <= 0 || c.LG < 0 || c.LG > 255 || c.LGCount < 1 || c.Dispatchers < 1 || c.Dispatchers > 8 {
		log.Fatal("need -targets, -rate > 0, 0 <= -lg <= 255, -lg-count >= 1, 1 <= -dispatchers <= 8")
	}
	if c.RunTag < 0 {
		// default: differs between runs (seed mixed with the start time) so a caching gateway can never
		// serve a prompt from an earlier run; pass -run-tag to reproduce exact nonces.
		c.RunTag = int(rv.SplitMix64(c.Seed^uint64(time.Now().UnixNano())) % 256)
	}
	c.RunTag %= 256
	gcDesc, err := rv.ConfigureGC(c.GCPercent, c.MemLimit)
	if err != nil {
		log.Fatal(err)
	}
	c.GCDesc = gcDesc
	hbd, err := time.ParseDuration(hb)
	if err != nil {
		log.Fatalf("-hb: %v", err)
	}
	if err := rv.StartHeartbeat(hbd); err != nil {
		log.Fatalf("heartbeat: %v", err)
	}
	if authFile != "" {
		b, err := os.ReadFile(authFile)
		if err != nil {
			log.Fatalf("auth-file: %v", err)
		}
		auth = strings.TrimSpace(string(b))
	}
	if auth == "" {
		auth = os.Getenv("RV_AUTH")
	}
	c.AuthSet = auth != ""
	if err := os.MkdirAll(c.Out, 0o755); err != nil {
		log.Fatal(err)
	}
	g := &gen{cfg: c, auth: auth, errByClass: map[string]int64{}}
	for _, h := range extra {
		k, v, ok := strings.Cut(h, ":")
		if !ok {
			log.Fatalf("bad -H %q", h)
		}
		g.extra = append(g.extra, [2]string{strings.TrimSpace(k), strings.TrimSpace(v)})
		g.cfg.ExtraHeaders = append(g.cfg.ExtraHeaders, strings.TrimSpace(k))
	}
	if g.words, err = rv.LoadWords(); err != nil {
		log.Fatal(err)
	}
	if g.canary, err = rv.LoadCanaries(""); err != nil {
		log.Fatal(err)
	}
	if err := g.loadCorpus(); err != nil {
		log.Fatalf("corpus: %v", err)
	}
	g.chunkPool.New = func() any { return new(rv.Chunk) }
	g.bufPool.New = func() any { b := make([]byte, 0, 4096); return &b }
	g.ssePool.New = func() any { return rv.NewSSEReader(nil, 8<<10) }
	g.bodyPool.New = func() any { b := make([]byte, 0, 8<<10); return &b }
	g.client = g.newClient()
	if *flightDir != "" {
		os.MkdirAll(*flightDir, 0o755)
		g.fr = trace.NewFlightRecorder(trace.FlightRecorderConfig{MinAge: 3 * time.Second, MaxBytes: 256 << 20})
		if err := g.fr.Start(); err != nil {
			log.Fatalf("flight recorder: %v", err)
		}
		g.flightNs = int64(*flightMs * 1e6)
		g.dumps.Store(-int32(*flightMax))
		flightDirPath = *flightDir
	}
	host := rv.CollectHostInfo()
	writeJSON(filepath.Join(c.Out, "manifest.start.json"), map[string]any{"config": g.cfg, "host": host})

	if c.StartAt > 0 {
		wait := time.Until(time.UnixMilli(c.StartAt))
		if wait > time.Hour || wait < -time.Minute {
			log.Fatalf("-start-at %d is %v away: expected unix MILLISECONDS within the next hour", c.StartAt, wait)
		}
		log.Printf("waiting %v for synchronized start", wait.Round(time.Millisecond))
		if wait > 0 {
			time.Sleep(wait)
		}
	}
	rec, err := rv.NewJSONL(filepath.Join(c.Out, "requests.jsonl"), 1<<18)
	if err != nil {
		log.Fatal(err)
	}
	g.rec = rec
	cpu := rv.StartCPULog(filepath.Join(c.Out, "cpu.jsonl"), time.Second)
	ctx, cancel := context.WithCancel(context.Background())
	g.baseCtx = ctx
	sigc := make(chan os.Signal, 1)
	signal.Notify(sigc, syscall.SIGINT, syscall.SIGTERM)
	stopSched := make(chan struct{})
	go func() {
		<-sigc
		log.Printf("signal: stopping schedule")
		close(stopSched)
	}()
	startWall := time.Now().UTC()
	g.epoch = time.Now()
	tsDone := g.timeseries(filepath.Join(c.Out, "timeseries.jsonl"))
	var wg sync.WaitGroup
	interrupted := g.dispatch(&wg, stopSched)
	schedEnd := time.Since(g.epoch)
	// drain
	drained := make(chan struct{})
	go func() { wg.Wait(); close(drained) }()
	select {
	case <-drained:
	case <-time.After(time.Duration(c.DrainTimeout * 1e9)):
		log.Printf("drain timeout: cancelling %d in-flight requests", g.inflight.Load())
		g.draining.Store(true)
		cancel()
		<-drained
	}
	cancel()
	close(tsDone)
	cpuSamples := cpu.Stop()
	if err := rec.Close(); err != nil {
		log.Printf("requests.jsonl close: %v", err)
	}
	g.writeManifest(host, startWall, schedEnd, interrupted, cpuSamples)
}

var flightDirPath string

func claimsList(g *gen) []int64 {
	var out []int64
	for i := 0; i < max(1, min(g.cfg.Dispatchers, len(g.claims))); i++ {
		out = append(out, g.claims[i].Load())
	}
	return out
}

// maybeDump writes the flight recorder's window ~0.5 s after a stall (to include its aftermath).
func (g *gen) maybeDump(seq uint32, late int64) {
	if g.dumps.Add(1) > 0 {
		return
	}
	go func() {
		time.Sleep(500 * time.Millisecond)
		g.flightMu.Lock()
		defer g.flightMu.Unlock()
		f, err := os.Create(filepath.Join(flightDirPath, fmt.Sprintf("stall-seq%d-late%dus.trace", seq, late/1000)))
		if err != nil {
			return
		}
		defer f.Close()
		if _, err := g.fr.WriteTo(f); err != nil {
			log.Printf("flight recorder dump: %v", err)
		}
	}()
}

func writeJSON(path string, v any) {
	b, _ := json.MarshalIndent(v, "", " ")
	if err := os.WriteFile(path, append(b, '\n'), 0o644); err != nil {
		log.Printf("write %s: %v", path, err)
	}
}

func (g *gen) newClient() *http.Client {
	d := &net.Dialer{Timeout: time.Duration(g.cfg.DialTimeout * 1e9), KeepAlive: 30 * time.Second}
	tr := &http.Transport{
		DialContext: func(ctx context.Context, network, addr string) (net.Conn, error) {
			conn, err := d.DialContext(ctx, network, addr)
			if err != nil {
				return nil, err
			}
			g.connOpens.Add(1)
			return &timedConn{Conn: conn}, nil
		},
		MaxIdleConns:          0,
		MaxIdleConnsPerHost:   1 << 20,
		MaxConnsPerHost:       0,
		IdleConnTimeout:       600 * time.Second,
		DisableCompression:    true,
		ForceAttemptHTTP2:     false,
		WriteBufferSize:       8 << 10,
		ReadBufferSize:        8 << 10,
		ExpectContinueTimeout: 0,
	}
	return &http.Client{Transport: tr, CheckRedirect: func(*http.Request, []*http.Request) error { return http.ErrUseLastResponse }}
}

func (g *gen) loadCorpus() error {
	raw, err := os.ReadFile(g.cfg.Corpus)
	if err != nil {
		return err
	}
	sum := sha256.Sum256(raw)
	g.cfg.CorpusSHA = hex.EncodeToString(sum[:])
	for ln, line := range bytes.Split(raw, []byte{'\n'}) {
		if len(bytes.TrimSpace(line)) == 0 {
			continue
		}
		var e corpusEntry
		if err := json.Unmarshal(line, &e); err != nil {
			return fmt.Errorf("line %d: %v", ln+1, err)
		}
		if len(e.Messages) == 0 {
			return fmt.Errorf("line %d: no messages", ln+1)
		}
		msgs := e.Messages
		if !bytes.Contains(msgs, []byte(placeholder)) {
			// no placeholder: prepend the nonce to the last user message
			var arr []map[string]any
			if err := json.Unmarshal(msgs, &arr); err != nil {
				return fmt.Errorf("line %d: messages: %v", ln+1, err)
			}
			done := false
			for i := len(arr) - 1; i >= 0 && !done; i-- {
				if arr[i]["role"] == "user" {
					if s, ok := arr[i]["content"].(string); ok {
						arr[i]["content"] = placeholder + " " + s
						done = true
					}
				}
			}
			if !done {
				return fmt.Errorf("line %d: no string user message to carry the nonce", ln+1)
			}
			msgs, _ = json.Marshal(arr)
		}
		model := e.Model
		if g.cfg.Model != "" {
			model = g.cfg.Model
		}
		if model == "" {
			model = "gpt-4o-mini"
		}
		var en entry
		en.e = e
		for si, stream := range []bool{false, true} {
			var b []byte
			b = append(b, `{"model":`...)
			mb, _ := json.Marshal(model)
			b = append(b, mb...)
			b = append(b, `,"messages":`...)
			b = append(b, msgs...)
			if e.MaxTokens > 0 {
				b = append(b, fmt.Sprintf(`,"max_tokens":%d`, e.MaxTokens)...)
			}
			if stream {
				b = append(b, `,"stream":true`...)
				if g.cfg.IncludeUsage {
					b = append(b, `,"stream_options":{"include_usage":true}`...)
				}
			} else {
				b = append(b, `,"stream":false`...)
			}
			if len(e.Tools) > 0 && string(e.Tools) != "null" {
				b = append(b, `,"tools":`...)
				b = append(b, e.Tools...)
			}
			b = append(b, '}')
			if !json.Valid(bytes.ReplaceAll(b, []byte(placeholder), []byte("x"))) {
				return fmt.Errorf("line %d: built body is not valid JSON", ln+1)
			}
			i := bytes.Index(b, []byte(placeholder))
			if i < 0 || bytes.Count(b, []byte(placeholder)) != 1 {
				return fmt.Errorf("line %d: need exactly one %s", ln+1, placeholder)
			}
			en.body[si] = tmpl{pre: b[:i], post: b[i+len(placeholder):]}
		}
		keys := make([]string, 0, len(e.Synth))
		for k := range e.Synth {
			keys = append(keys, k)
		}
		slices.Sort(keys)
		for _, k := range keys {
			if v := e.Synth[k]; v != "" {
				en.synthH = append(en.synthH, [2]string{"x-synth-" + k, v})
			}
		}
		g.entries = append(g.entries, en)
	}
	if len(g.entries) == 0 {
		return errors.New("empty corpus")
	}
	log.Printf("corpus: %d entries sha256=%s", len(g.entries), g.cfg.CorpusSHA[:16])
	return nil
}

// schedule maps a cumulative arrival coordinate x to time t via the inverse of
// Lambda(t) = integral of rate(t): rate ramps linearly over [0,ramp), then is constant.
// Constant arrivals use x = x0 + i; Poisson arrivals use x = sum of Exp(1) (time rescaling).
type schedule struct {
	rate, ramp, end float64
	poisson         bool
	rng             *rand.Rand
	x               float64
}

func (s *schedule) inv(x float64) float64 {
	if s.ramp > 0 {
		xr := s.rate * s.ramp / 2
		if x < xr {
			return math.Sqrt(2 * x * s.ramp / s.rate)
		}
		return s.ramp + (x-xr)/s.rate
	}
	return x / s.rate
}

func (s *schedule) next() (float64, bool) {
	if s.poisson {
		s.x += s.rng.ExpFloat64()
	} else {
		s.x += 1
	}
	t := s.inv(s.x)
	return t, t < s.end
}

func (g *gen) dispatch(wg *sync.WaitGroup, stop <-chan struct{}) bool {
	c := g.cfg
	sch := &schedule{rate: c.Rate, ramp: c.Ramp, end: c.Ramp + c.Warmup + c.Duration,
		poisson: c.Arrival == "poisson", rng: rand.New(rand.NewPCG(c.Seed, uint64(c.LG)+0x5eed))}
	if !sch.poisson {
		sch.x = float64(c.LG)/float64(c.LGCount) - 1 // phase offset so several loadgens interleave
	}
	// Precompute every arrival (ns offsets from the run epoch).
	var arrivals []int64
	for {
		if c.MaxRequests > 0 && int64(len(arrivals)) >= c.MaxRequests {
			break
		}
		t, ok := sch.next()
		if !ok {
			break
		}
		arrivals = append(arrivals, int64(t*1e9))
	}
	measureStart := int64((c.Ramp + c.Warmup) * 1e9)
	rampEnd := int64(c.Ramp * 1e9)
	n := int64(len(arrivals))

	// Redundant dispatchers. A single pacing thread is occasionally not run for 2-10 ms (traces in
	// runs/diag-6000*: a Go timer left on an idle P, starvation in the global run queue, a woken
	// thread waiting behind other threads' EEVDF slices, and kernel/hypervisor-level delays with idle
	// CPUs). Each dispatcher sleeps in the kernel (clock_nanosleep ABSTIME) until the next unclaimed
	// arrival plus its own stagger (d * -dispatch-stagger); whichever wakes first claims every due
	// arrival with a CAS on the shared index and spawns it. A stall of one dispatcher therefore costs
	// at most the stagger instead of stalling the whole schedule.
	var next atomic.Int64
	var interrupted atomic.Bool
	var dwg sync.WaitGroup
	g.cfg.RTStatus = rv.SetRealtime(0)
	for d := 0; d < max(1, c.Dispatchers); d++ {
		dwg.Add(1)
		go func(d int) {
			defer dwg.Done()
			if c.RTPrio > 0 {
				runtime.LockOSThread()
				defer runtime.UnlockOSThread()
				g.cfg.RTStatus = rv.SetRealtime(c.RTPrio)
			}
			pc := rv.NewPreciseClock()
			stagger := time.Duration(d) * time.Duration(c.StaggerUs*1e3)
			for {
				select {
				case <-stop:
					interrupted.Store(true)
					return
				default:
				}
				i := next.Load()
				if i >= n {
					return
				}
				pc.SleepUntil(g.epoch.Add(time.Duration(arrivals[i]) + stagger))
				for {
					i := next.Load()
					if i >= n {
						break
					}
					due := g.epoch.Add(time.Duration(arrivals[i]))
					if time.Now().Before(due) {
						break
					}
					if !next.CompareAndSwap(i, i+1) {
						continue
					}
					if g.fr != nil {
						trace.Log(context.Background(), "dispatch-woke", strconv.FormatInt(int64(time.Since(due)), 10))
					}
					ph := int8(2)
					if arrivals[i] < rampEnd {
						ph = 0
					} else if arrivals[i] < measureStart {
						ph = 1
					}
					g.claims[d].Add(1)
					g.scheduled.Add(1)
					wg.Add(1)
					go g.do(wg, uint32(i), due, ph)
					// run the new request goroutine now on this P; this dispatcher may then wait in
					// the global run queue, which the other dispatchers cover
					runtime.Gosched()
				}
			}
		}(d)
	}
	dwg.Wait()
	return interrupted.Load()
}

// pickTarget spreads requests over targets with a seeded hash of (loadgen, seq). Plain
// round robin (seq % n) made every loadgen send runs of consecutive arrivals to the same target and,
// with several phase-aligned loadgens, delivered ~20 requests to one provider within ~3 ms every
// ~13 ms (runs/val-6000-r4: provider schedule error p99 0.31 -> 1.1 ms); a SUT would see the same bursts.
func (g *gen) pickTarget(seq uint32) int {
	n := uint64(len(g.cfg.Targets))
	if n == 1 {
		return 0
	}
	return int(rv.SplitMix64(g.cfg.Seed*0x9E3779B97F4A7C15^uint64(g.cfg.LG)<<32^uint64(seq)^0x7a26e7) % n)
}

// stream decision: exact fraction, evenly interleaved (Bresenham) and independent of the
// corpus pick (which is a hash of seq).
func (g *gen) isStream(seq uint32, e *corpusEntry) bool {
	f := g.cfg.SSEFrac
	if f < 0 {
		return e.Stream != nil && *e.Stream
	}
	return math.Floor(float64(seq+1)*f) > math.Floor(float64(seq)*f)
}

func (g *gen) addErr(class string) {
	g.errMu.Lock()
	g.errByClass[class]++
	g.errMu.Unlock()
}

func classify(err error, ctx context.Context, draining bool) string {
	var ne net.Error
	var oe *net.OpError
	switch {
	case draining && ctx.Err() != nil:
		return "drain_cancel"
	case errors.Is(err, context.DeadlineExceeded) || (errors.As(err, &ne) && ne.Timeout()):
		return "timeout"
	case errors.As(err, &oe) && oe.Op == "dial":
		return "connect"
	case errors.Is(err, io.ErrUnexpectedEOF), errors.Is(err, syscall.ECONNRESET), errors.Is(err, rv.ErrSSETruncated):
		return "eof_mid_stream"
	case errors.Is(err, context.Canceled):
		return "cancelled"
	default:
		return "transport"
	}
}

func truncate(s string, n int) string {
	if len(s) > n {
		return s[:n]
	}
	return s
}

func (g *gen) do(wg *sync.WaitGroup, seq uint32, due time.Time, ph int8) {
	defer wg.Done()
	c := &g.cfg
	pick := rv.SplitMix64(c.Seed^0xC0FFEE+uint64(seq)) % uint64(len(g.entries))
	en := &g.entries[pick]
	stream := g.isStream(seq, &en.e)
	nonce := g.words.NonceText(uint8(c.RunTag), uint8(c.LG), seq)
	rid := rv.RIDFromNonce(nonce)
	r := reqRecord{RID: rid, LG: c.LG, Seq: seq, Ph: ph, Cls: en.e.Class, CID: en.e.ID, Stream: stream,
		TokIn: en.e.TokensIn, MaxTok: en.e.MaxTokens, Target: g.pickTarget(seq), Nonce: nonce,
		Inject: en.e.Synth["inject"], Fault: en.e.Synth["fault"], Sampled: rv.Sampled(rid, c.SampleMod)}
	r.SchedNs = int64(due.Sub(g.epoch))
	si := 0
	if stream {
		si = 1
	}
	t := en.body[si]
	bp := g.bodyPool.Get().(*[]byte)
	body := append(append(append((*bp)[:0], t.pre...), nonce...), t.post...)
	// returned to the pool only after the response body is closed (the transport has finished
	// writing the request by then).
	defer func() { *bp = body[:0]; g.bodyPool.Put(bp) }()

	defer func() {
		g.rec.Write(&r)
		g.recorded.Add(1)
		if r.Err != "" {
			g.errs.Add(1)
			g.addErr(r.Err)
		} else if r.Status == 200 && r.Done {
			g.okDone.Add(1)
		}
	}()

	if c.MaxInflight > 0 && g.inflight.Load() >= c.MaxInflight {
		r.LateNs = int64(time.Since(due))
		r.Err = "inflight_cap"
		g.drops.Add(1)
		return
	}
	ctx, cancel := context.WithTimeout(g.baseCtx, time.Duration(c.ReqTimeout*1e9))
	defer cancel()
	req, _ := http.NewRequestWithContext(ctx, http.MethodPost, c.Targets[r.Target]+c.Path, bytes.NewReader(body))
	hd := req.Header
	hd.Set("Content-Type", "application/json")
	hd.Set("Accept", "application/json")
	hd.Set("User-Agent", "rv-olg/"+rv.BuildSHA)
	hd.Set("x-request-id", rid)
	if g.auth != "" {
		hd.Set("Authorization", g.auth)
	}
	for _, kv := range g.extra {
		hd.Set(kv[0], kv[1])
	}
	if c.SynthHeaders {
		for _, kv := range en.synthH {
			hd.Set(kv[0], kv[1])
		}
	}
	var conn *timedConn
	var sendStart time.Time
	var startNs int64
	// WroteRequest / GotFirstResponseByte run on the transport's writer/reader goroutines.
	var wroteAt, fbAt atomic.Int64
	trace := &httptrace.ClientTrace{
		GotConn: func(info httptrace.GotConnInfo) {
			r.ConnNs = rv.NowNs() - startNs
			r.Reused = info.Reused
			conn, _ = info.Conn.(*timedConn)
		},
		WroteRequest:         func(httptrace.WroteRequestInfo) { wroteAt.Store(rv.NowNs()) },
		GotFirstResponseByte: func() { fbAt.Store(rv.NowNs()) },
	}
	defer func() {
		if v := wroteAt.Load(); v > 0 {
			r.WroteNs = v - startNs
		}
		if v := fbAt.Load(); v > 0 {
			r.FBNs = v - startNs
		}
	}()
	req = req.WithContext(httptrace.WithClientTrace(ctx, trace))

	n := g.inflight.Add(1)
	for {
		m := g.maxInflight.Load()
		if n <= m || g.maxInflight.CompareAndSwap(m, n) {
			break
		}
	}
	defer g.inflight.Add(-1)
	sendStart = time.Now()
	startNs = rv.NowNs()
	r.LateNs = int64(sendStart.Sub(due))
	if r.LateNs > c.DropNs {
		g.drops.Add(1)
	}
	if g.fr != nil && r.LateNs > g.flightNs {
		g.maybeDump(seq, r.LateNs)
	}
	for {
		m := g.secLateMax.Load()
		if r.LateNs <= m || g.secLateMax.CompareAndSwap(m, r.LateNs) {
			break
		}
	}
	g.lateMu.Lock()
	g.lateUs = append(g.lateUs, int32(min(r.LateNs/1000, math.MaxInt32)))
	g.lateMu.Unlock()
	g.sent.Add(1)

	arrival := func() int64 {
		if conn != nil {
			if v := conn.lastRead.Load(); v > 0 {
				return v - startNs
			}
		}
		return rv.NowNs() - startNs
	}
	resp, err := g.client.Do(req)
	r.HdrNs = rv.NowNs() - startNs
	if err != nil {
		r.Err = classify(err, ctx, g.draining.Load())
		r.ErrDetail = truncate(err.Error(), 200)
		r.EndNs = r.HdrNs
		return
	}
	defer resp.Body.Close()
	r.Status = resp.StatusCode
	rh := resp.Header
	r.Disp, r.PlanVer, r.Stages = rh.Get("x-rv-disposition"), rh.Get("x-rv-plan-version"), rh.Get("x-rv-stages")
	r.RespRID = rh.Get("x-request-id")
	r.CType = rh.Get("Content-Type")
	if r.RespRID == rid {
		r.RespRID = "=" // echoed unchanged; keep records small
	}
	if resp.StatusCode != 200 {
		b, rerr := io.ReadAll(io.LimitReader(resp.Body, 64<<10))
		r.Bytes = int64(len(b))
		r.EndNs = arrival()
		r.Err = fmt.Sprintf("http_%d", resp.StatusCode)
		var env struct {
			Error struct {
				Code any    `json:"code"`
				Type string `json:"type"`
			} `json:"error"`
		}
		if json.Unmarshal(b, &env) == nil {
			r.ErrDetail = truncate(fmt.Sprintf("type=%s code=%v", env.Error.Type, env.Error.Code), 200)
		}
		if rerr != nil {
			r.ErrDetail = truncate(r.ErrDetail+" body_err="+rerr.Error(), 200)
		}
		return
	}
	ch := g.chunkPool.Get().(*rv.Chunk)
	defer g.chunkPool.Put(ch)
	cbp := g.bufPool.Get().(*[]byte)
	content := (*cbp)[:0]
	var toolArgs []byte
	defer func() { *cbp = content[:0]; g.bufPool.Put(cbp) }()
	if r.Sampled {
		r.ArrNs = make([]int64, 0, max(en.e.MaxTokens, 16))
		r.ArrCum = make([]int, 0, max(en.e.MaxTokens, 16))
	}
	cum := 0
	if strings.HasPrefix(r.CType, "text/event-stream") {
		cr := &countReader{r: resp.Body}
		sr := g.ssePool.Get().(*rv.SSEReader)
		sr.Reset(cr)
		defer func() { sr.Reset(nil); g.ssePool.Put(sr) }()
		var postTimer *time.Timer
		for {
			data, _, err := sr.Next()
			if err != nil {
				if postTimer != nil {
					postTimer.Stop()
				}
				r.Bytes = cr.n
				if err == io.EOF {
					if !r.Done {
						r.Err = "no_done"
						r.EndNs = arrival()
					}
				} else if r.Done {
					if ctx.Err() != nil && !g.draining.Load() {
						r.PostHang = true
					}
				} else {
					r.Err = classify(err, ctx, g.draining.Load())
					r.ErrDetail = truncate(err.Error(), 200)
					r.EndNs = arrival()
				}
				break
			}
			r.Events++
			at := arrival()
			if r.Done {
				r.PostDone++
				continue
			}
			if string(data) == "[DONE]" {
				r.Done = true
				r.EndNs = at
				// a correct server ends the body right after [DONE]; bound the wait
				postTimer = time.AfterFunc(5*time.Second, cancel)
				continue
			}
			if perr := rv.ParseChunk(data, ch); perr != nil {
				r.Malformed++
				continue
			}
			if ch.Error {
				r.ErrEvent = true
			}
			if ch.Usage {
				r.Usage = true
			}
			if len(ch.Finish) > 0 {
				r.Finish = string(ch.Finish)
			}
			added := 0
			if ch.ContentPresent && len(ch.Content) > 0 {
				content = append(content, ch.Content...)
				added += len(ch.Content)
			}
			for i := range ch.Tools {
				if tf := &ch.Tools[i]; tf.HasArgs && len(tf.Args) > 0 {
					toolArgs = append(toolArgs, tf.Args...)
					added += len(tf.Args)
				}
			}
			if added > 0 {
				r.Chunks++
				cum += added
				if r.FirstNs == 0 {
					r.FirstNs = at
				}
				r.LastCNs = at
				if r.Sampled {
					r.ArrNs = append(r.ArrNs, at)
					r.ArrCum = append(r.ArrCum, cum)
				}
			}
		}
		if r.Err == "" && r.ErrEvent {
			r.Err = "error_event"
		} else if r.Err == "" && r.Malformed > 0 {
			r.Err = "malformed_sse"
		}
	} else {
		b, rerr := io.ReadAll(io.LimitReader(resp.Body, 64<<20))
		r.Bytes = int64(len(b))
		r.EndNs = arrival()
		if rerr != nil {
			r.Err = classify(rerr, ctx, g.draining.Load())
			r.ErrDetail = truncate(rerr.Error(), 200)
		} else if perr := rv.ParseChunk(b, ch); perr != nil || ch.Choices == 0 {
			r.Err = "json_invalid"
		} else {
			r.Done = true
			r.Chunks = 1
			if ch.Usage {
				r.Usage = true
			}
			r.Finish = string(ch.Finish)
			if ch.ContentPresent {
				content = append(content, ch.Content...)
			}
			for i := range ch.Tools {
				if tf := &ch.Tools[i]; tf.HasArgs {
					toolArgs = append(toolArgs, tf.Args...)
				}
			}
			cum = len(content) + len(toolArgs)
			r.FirstNs, r.LastCNs = r.EndNs, r.EndNs
			if r.Sampled {
				r.ArrNs = append(r.ArrNs, r.EndNs)
				r.ArrCum = append(r.ArrCum, cum)
			}
		}
	}
	sum := sha256.Sum256(content)
	r.ContentSHA = hex.EncodeToString(sum[:])
	r.ContentLen = len(content)
	if len(toolArgs) > 0 {
		ts := sha256.Sum256(toolArgs)
		r.ToolSHA = hex.EncodeToString(ts[:])
	}
	r.Canary = g.canary.Hits(content, toolArgs)
}

type countReader struct {
	r io.Reader
	n int64
}

func (c *countReader) Read(p []byte) (int, error) {
	n, err := c.r.Read(p)
	c.n += int64(n)
	return n, err
}

func (g *gen) timeseries(path string) chan struct{} {
	done := make(chan struct{})
	f, err := os.Create(path)
	if err != nil {
		log.Printf("timeseries: %v", err)
		return done
	}
	go func() {
		defer f.Close()
		t := time.NewTicker(time.Second)
		defer t.Stop()
		i := 0
		for {
			select {
			case <-done:
				return
			case <-t.C:
			}
			i++
			line := map[string]any{"t": time.Since(g.epoch).Seconds(), "scheduled": g.scheduled.Load(),
				"sent": g.sent.Load(), "recorded": g.recorded.Load(), "inflight": g.inflight.Load(),
				"ok": g.okDone.Load(), "errors": g.errs.Load(), "drops": g.drops.Load(),
				"late_max_ns": g.secLateMax.Swap(0), "conn_opens": g.connOpens.Load(),
				"goroutines": runtime.NumGoroutine()}
			b, _ := json.Marshal(line)
			f.Write(append(b, '\n'))
			if i%10 == 0 {
				log.Printf("t=%.0fs sched=%d inflight=%d ok=%d err=%d drops=%d conns=%d",
					line["t"], line["scheduled"], line["inflight"], line["ok"], line["errors"], line["drops"], line["conn_opens"])
			}
		}
	}()
	return done
}

func pct(xs []int32, p float64) float64 {
	if len(xs) == 0 {
		return math.NaN()
	}
	i := int(math.Ceil(p*float64(len(xs)))) - 1
	return float64(xs[max(0, min(i, len(xs)-1))])
}

func (g *gen) writeManifest(host rv.HostInfo, startWall time.Time, schedEnd time.Duration, interrupted bool, cpu []rv.CPUSample) {
	g.lateMu.Lock()
	lat := slices.Clone(g.lateUs)
	g.lateMu.Unlock()
	slices.Sort(lat)
	g.errMu.Lock()
	errs := map[string]int64{}
	for k, v := range g.errByClass {
		errs[k] = v
	}
	g.errMu.Unlock()
	var busyMax, busySum, procMax float64
	measureFrom := g.cfg.Ramp + g.cfg.Warmup
	nMeasure := 0
	for _, s := range cpu {
		if s.T-g.epoch.Sub(rv.Epoch).Seconds() < measureFrom {
			continue
		}
		nMeasure++
		busySum += s.Busy
		busyMax = max(busyMax, s.Busy)
		procMax = max(procMax, s.Proc)
	}
	m := map[string]any{
		"config": g.cfg, "host": host, "start_wall_utc": startWall.Format(time.RFC3339Nano),
		"schedule_end_s": schedEnd.Seconds(), "interrupted": interrupted,
		"counts": map[string]any{"scheduled": g.scheduled.Load(), "sent": g.sent.Load(), "recorded": g.recorded.Load(),
			"ok_done": g.okDone.Load(), "errors": g.errs.Load(), "errors_by_class": errs, "drops": g.drops.Load(),
			"conn_opens": g.connOpens.Load(), "max_inflight": g.maxInflight.Load(), "record_queue_blocked": g.rec.Blocked(),
			"claims_by_dispatcher": claimsList(g)},
		"lateness_us_all_phases": map[string]float64{"p50": pct(lat, 0.5), "p99": pct(lat, 0.99), "p999": pct(lat, 0.999),
			"max": pct(lat, 1.0)},
		"cpu_measure_phase": map[string]float64{"samples": float64(nMeasure), "busy_max": busyMax,
			"busy_mean": busySum / math.Max(1, float64(nMeasure)), "proc_max": procMax},
		"epoch_offset_s": g.epoch.Sub(rv.Epoch).Seconds(),
		"runtime":        rv.RuntimeStats(),
	}
	writeJSON(filepath.Join(g.cfg.Out, "manifest.json"), m)
	log.Printf("done: scheduled=%d recorded=%d ok=%d errors=%v drops=%d late_p99=%.0fus max=%.0fus cpu_busy_max=%.1f%%",
		g.scheduled.Load(), g.recorded.Load(), g.okDone.Load(), errs, g.drops.Load(), pct(lat, 0.99), pct(lat, 1.0), busyMax)
}
