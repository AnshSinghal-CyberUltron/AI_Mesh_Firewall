package rv

import (
	"fmt"
	"os"
	"runtime/debug"
	"strconv"
	"strings"
)

// ConfigureGC applies the GC policy of a harness binary and returns a description for logs and
// manifests. Why: on the provider, every GC mark phase (~12 ms at 25% of Ps) delayed the
// timer-driven token schedule of ~25% of live streams (runs/cal-1000-gctrace vs cal-1000-nogc),
// so the default is GC off (gcPercent -1) bounded by a soft memory limit; the runtime then only
// collects when the heap approaches the limit, and every GC cycle is reported in the run output.
//
// memlimit: "auto" = 60% of MemTotal, "none"/"0" = no limit, or a size such as 8GiB / 6000MB / 123456.
func ConfigureGC(gcPercent int, memlimit string) (string, error) {
	var limit int64
	switch strings.ToLower(strings.TrimSpace(memlimit)) {
	case "auto":
		total := memTotalBytes()
		if total <= 0 {
			return "", fmt.Errorf("memlimit auto: cannot read MemTotal")
		}
		limit = total * 6 / 10
	case "none", "0", "":
		limit = 0
	default:
		v, err := ParseBytes(memlimit)
		if err != nil {
			return "", fmt.Errorf("memlimit: %v", err)
		}
		limit = v
	}
	if gcPercent < 0 && limit == 0 {
		return "", fmt.Errorf("-gc -1 (GC off) requires a memory limit (-memlimit auto or a size)")
	}
	debug.SetGCPercent(gcPercent)
	if limit > 0 {
		debug.SetMemoryLimit(limit)
	}
	gc := strconv.Itoa(gcPercent)
	if gcPercent < 0 {
		gc = "off"
	}
	return fmt.Sprintf("gc=%s memlimit=%.2fGiB", gc, float64(limit)/(1<<30)), nil
}

// ParseBytes parses sizes like 8GiB, 512MiB, 6GB, 1024 (bytes).
func ParseBytes(s string) (int64, error) {
	s = strings.TrimSpace(s)
	mult := int64(1)
	for _, u := range []struct {
		suf string
		m   int64
	}{{"KiB", 1 << 10}, {"MiB", 1 << 20}, {"GiB", 1 << 30}, {"TiB", 1 << 40}, {"KB", 1e3}, {"MB", 1e6}, {"GB", 1e9}, {"TB", 1e12}, {"B", 1}} {
		if strings.HasSuffix(s, u.suf) {
			s, mult = strings.TrimSuffix(s, u.suf), u.m
			break
		}
	}
	v, err := strconv.ParseFloat(strings.TrimSpace(s), 64)
	if err != nil || v < 0 {
		return 0, fmt.Errorf("bad size %q", s)
	}
	return int64(v * float64(mult)), nil
}

func memTotalBytes() int64 {
	b, err := os.ReadFile("/proc/meminfo")
	if err != nil {
		return 0
	}
	for _, l := range strings.Split(string(b), "\n") {
		if strings.HasPrefix(l, "MemTotal:") {
			f := strings.Fields(l)
			if len(f) >= 2 {
				kb, _ := strconv.ParseInt(f[1], 10, 64)
				return kb * 1024
			}
		}
	}
	return 0
}
