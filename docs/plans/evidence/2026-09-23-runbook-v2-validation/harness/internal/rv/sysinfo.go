package rv

import (
	"bufio"
	"bytes"
	"context"
	"encoding/json"
	"io"
	"net/http"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"strconv"
	"strings"
	"sync"
	"time"
)

// BuildSHA is set at link time: -ldflags "-X rvharness/internal/rv.BuildSHA=<git sha>".
var BuildSHA = "unknown"

// HostInfo describes the machine a binary ran on (recorded in every manifest).
type HostInfo struct {
	Hostname    string            `json:"hostname"`
	NumCPU      int               `json:"num_cpu"`
	GOMAXPROCS  int               `json:"gomaxprocs"`
	GoVersion   string            `json:"go_version"`
	BuildSHA    string            `json:"harness_build_sha"`
	Kernel      string            `json:"kernel"`
	MemTotalKB  int64             `json:"mem_total_kb"`
	Lscpu       map[string]string `json:"lscpu,omitempty"`
	GCE         map[string]string `json:"gce,omitempty"`
	Sysctl      map[string]string `json:"sysctl,omitempty"`
	NofileLimit string            `json:"nofile_limit,omitempty"`
	StartUTC    string            `json:"start_utc"`
}

// CollectHostInfo gathers host facts; failures leave fields empty rather than aborting.
func CollectHostInfo() HostInfo {
	h := HostInfo{NumCPU: runtime.NumCPU(), GOMAXPROCS: runtime.GOMAXPROCS(0), GoVersion: runtime.Version(),
		BuildSHA: BuildSHA, StartUTC: time.Now().UTC().Format(time.RFC3339Nano)}
	h.Hostname, _ = os.Hostname()
	if b, err := os.ReadFile("/proc/version"); err == nil {
		h.Kernel = strings.TrimSpace(string(b))
	}
	if b, err := os.ReadFile("/proc/meminfo"); err == nil {
		for _, l := range strings.Split(string(b), "\n") {
			if strings.HasPrefix(l, "MemTotal:") {
				f := strings.Fields(l)
				if len(f) >= 2 {
					h.MemTotalKB, _ = strconv.ParseInt(f[1], 10, 64)
				}
			}
		}
	}
	if out, err := exec.Command("lscpu").Output(); err == nil {
		h.Lscpu = map[string]string{}
		for _, l := range strings.Split(string(out), "\n") {
			if k, v, ok := strings.Cut(l, ":"); ok {
				k = strings.TrimSpace(k)
				switch k {
				case "Model name", "CPU(s)", "Thread(s) per core", "Core(s) per socket", "Socket(s)",
					"NUMA node(s)", "CPU max MHz", "Hypervisor vendor", "Flags":
					if k == "Flags" {
						// keep only the flags that matter for hashing/crypto speed
						var keep []string
						for _, f := range strings.Fields(v) {
							switch f {
							case "sha_ni", "avx2", "avx512f", "aes", "amx_tile":
								keep = append(keep, f)
							}
						}
						v = strings.Join(keep, " ")
					}
					h.Lscpu[k] = strings.TrimSpace(v)
				}
			}
		}
	}
	h.Sysctl = map[string]string{}
	for _, k := range []string{"net/core/somaxconn", "net/ipv4/ip_local_port_range", "net/ipv4/tcp_tw_reuse",
		"net/core/netdev_max_backlog", "net/ipv4/tcp_max_syn_backlog", "fs/file-max"} {
		if b, err := os.ReadFile("/proc/sys/" + k); err == nil {
			h.Sysctl[k] = strings.TrimSpace(string(b))
		}
	}
	if b, err := os.ReadFile("/proc/self/limits"); err == nil {
		for _, l := range strings.Split(string(b), "\n") {
			if strings.HasPrefix(l, "Max open files") {
				h.NofileLimit = strings.Join(strings.Fields(l)[3:5], "/")
			}
		}
	}
	h.GCE = gceMetadata()
	return h
}

func gceMetadata() map[string]string {
	ctx, cancel := context.WithTimeout(context.Background(), 400*time.Millisecond)
	defer cancel()
	out := map[string]string{}
	for _, k := range []string{"instance/machine-type", "instance/zone", "instance/name", "instance/image"} {
		req, _ := http.NewRequestWithContext(ctx, "GET", "http://169.254.169.254/computeMetadata/v1/"+k, nil)
		req.Header.Set("Metadata-Flavor", "Google")
		resp, err := http.DefaultClient.Do(req)
		if err != nil {
			return nil
		}
		b, _ := io.ReadAll(io.LimitReader(resp.Body, 4096))
		resp.Body.Close()
		v := string(b)
		if i := strings.LastIndexByte(v, '/'); i >= 0 && k != "instance/image" {
			v = v[i+1:]
		}
		out[k[len("instance/"):]] = v
	}
	return out
}

// CPUSample is one per-interval host CPU utilisation line (cpu.jsonl).
//
// Busy is measured from cpuidle residency (/sys/devices/system/cpu/cpu*/cpuidle/state*/time,
// microseconds, maintained by the idle driver): busy = 1 - idle_time / (wall * ncpu). It counts
// tasks, IRQ and softirq time alike. /proc/stat is tick-sampled on these kernels
// (VIRT_CPU_ACCOUNTING_GEN without nohz_full, no IRQ_TIME_ACCOUNTING) and was observed to read
// ~0% busy on a loadgen whose own process used 16.5% of all CPUs (aliasing), so the /proc/stat
// figure is kept only as BusyTick. Proc (this process) comes from utime+stime, which the kernel
// scales to the precise sum_exec_runtime.
type CPUSample struct {
	T          float64 `json:"t"`         // seconds since process Epoch (monotonic)
	Busy       float64 `json:"busy"`      // % of all CPUs from cpuidle residency (falls back to BusyTick)
	BusyTick   float64 `json:"busy_tick"` // % of all CPUs from /proc/stat (tick-sampled)
	User       float64 `json:"user"`
	Sys        float64 `json:"sys"`
	SoftIRQ    float64 `json:"softirq"`
	Steal      float64 `json:"steal"`
	IOWait     float64 `json:"iowait"`
	Proc       float64 `json:"proc"` // this process, % of all CPUs
	Goroutines int     `json:"goroutines"`
}

// CPULog samples /proc/stat and /proc/self/stat at a fixed interval.
type CPULog struct {
	mu      sync.Mutex
	samples []CPUSample
	stop    chan struct{}
	done    chan struct{}
}

type cpuTimes struct{ user, nice, sys, idle, iowait, irq, softirq, steal, total float64 }

func readCPU() (cpuTimes, bool) {
	b, err := os.ReadFile("/proc/stat")
	if err != nil {
		return cpuTimes{}, false
	}
	line, _, _ := bytes.Cut(b, []byte{'\n'})
	f := strings.Fields(string(line))
	if len(f) < 9 || f[0] != "cpu" {
		return cpuTimes{}, false
	}
	v := make([]float64, 8)
	for i := range v {
		v[i], _ = strconv.ParseFloat(f[i+1], 64)
	}
	t := cpuTimes{v[0], v[1], v[2], v[3], v[4], v[5], v[6], v[7], 0}
	t.total = v[0] + v[1] + v[2] + v[3] + v[4] + v[5] + v[6] + v[7]
	return t, true
}

// readIdleUs sums cpuidle residency (us) over all CPUs and states; -1 when unavailable.
func readIdleUs() int64 {
	files, _ := filepath.Glob("/sys/devices/system/cpu/cpu[0-9]*/cpuidle/state[0-9]*/time")
	if len(files) == 0 {
		return -1
	}
	var total int64
	for _, f := range files {
		b, err := os.ReadFile(f)
		if err != nil {
			return -1
		}
		v, err := strconv.ParseInt(strings.TrimSpace(string(b)), 10, 64)
		if err != nil {
			return -1
		}
		total += v
	}
	return total
}

func readProcTicks() float64 {
	b, err := os.ReadFile("/proc/self/stat")
	if err != nil {
		return 0
	}
	s := string(b)
	i := strings.LastIndexByte(s, ')')
	if i < 0 {
		return 0
	}
	f := strings.Fields(s[i+2:])
	if len(f) < 13 {
		return 0
	}
	ut, _ := strconv.ParseFloat(f[11], 64)
	st, _ := strconv.ParseFloat(f[12], 64)
	return ut + st
}

// StartCPULog begins sampling; path "" keeps samples in memory only.
func StartCPULog(path string, interval time.Duration) *CPULog {
	l := &CPULog{stop: make(chan struct{}), done: make(chan struct{})}
	var w *bufio.Writer
	var f *os.File
	if path != "" {
		if ff, err := os.Create(path); err == nil {
			f = ff
			w = bufio.NewWriter(f)
		}
	}
	go func() {
		defer close(l.done)
		prev, _ := readCPU()
		prevP := readProcTicks()
		prevT := time.Now()
		prevIdle := readIdleUs()
		ncpu := float64(runtime.NumCPU())
		t := time.NewTicker(interval)
		defer t.Stop()
		for {
			select {
			case <-l.stop:
				if w != nil {
					w.Flush()
					f.Close()
				}
				return
			case <-t.C:
			}
			cur, ok := readCPU()
			p := readProcTicks()
			idle := readIdleUs()
			now := time.Now()
			elapsed := now.Sub(prevT).Seconds()
			if !ok {
				continue
			}
			d := cur.total - prev.total
			if d <= 0 {
				continue
			}
			pct := func(x float64) float64 { return 100 * x / d }
			tick := 100 * (1 - (cur.idle-prev.idle+cur.iowait-prev.iowait)/d)
			busy := tick
			if idle >= 0 && prevIdle >= 0 {
				busy = 100 * (1 - float64(idle-prevIdle)/(elapsed*1e6*ncpu))
			}
			s := CPUSample{
				T:          float64(NowNs()) / 1e9,
				Busy:       busy,
				BusyTick:   tick,
				User:       pct(cur.user - prev.user + cur.nice - prev.nice),
				Sys:        pct(cur.sys - prev.sys),
				SoftIRQ:    pct(cur.softirq - prev.softirq + cur.irq - prev.irq),
				Steal:      pct(cur.steal - prev.steal),
				IOWait:     pct(cur.iowait - prev.iowait),
				Proc:       (p - prevP) / (elapsed * ncpu), // ticks are 1/100 s (CLK_TCK=100) -> percent
				Goroutines: runtime.NumGoroutine(),
			}
			prev, prevP, prevT, prevIdle = cur, p, now, idle
			l.mu.Lock()
			l.samples = append(l.samples, s)
			l.mu.Unlock()
			if w != nil {
				b, _ := json.Marshal(s)
				w.Write(append(b, '\n'))
				w.Flush()
			}
		}
	}()
	return l
}

// Stop ends sampling and returns the samples.
func (l *CPULog) Stop() []CPUSample {
	close(l.stop)
	<-l.done
	l.mu.Lock()
	defer l.mu.Unlock()
	return l.samples
}

// Samples returns a copy of the samples so far.
func (l *CPULog) Samples() []CPUSample {
	l.mu.Lock()
	defer l.mu.Unlock()
	return append([]CPUSample(nil), l.samples...)
}
