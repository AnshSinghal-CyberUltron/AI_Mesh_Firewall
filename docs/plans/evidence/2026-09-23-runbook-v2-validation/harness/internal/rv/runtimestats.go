package rv

import (
	"math"
	"runtime/metrics"
)

// RuntimeStats summarises Go runtime health for a manifest/stats file: GC pause and goroutine
// scheduling-latency distributions (from runtime/metrics histograms, since process start).
// A load generator or provider whose goroutines wait milliseconds to run would distort the
// timings it records; these numbers make that visible.
func RuntimeStats() map[string]any {
	samples := []metrics.Sample{
		{Name: "/gc/pauses:seconds"},
		{Name: "/sched/latencies:seconds"},
		{Name: "/gc/cycles/total:gc-cycles"},
		{Name: "/gc/heap/goal:bytes"},
		{Name: "/sched/goroutines:goroutines"},
	}
	metrics.Read(samples)
	out := map[string]any{}
	for _, s := range samples {
		switch s.Value.Kind() {
		case metrics.KindFloat64Histogram:
			out[s.Name] = histSummary(s.Value.Float64Histogram())
		case metrics.KindUint64:
			out[s.Name] = s.Value.Uint64()
		}
	}
	return out
}

// histSummary returns count and upper-bound quantiles (ms) of a runtime/metrics histogram.
// Values are bucket UPPER bounds, i.e. conservative.
func histSummary(h *metrics.Float64Histogram) map[string]float64 {
	var total uint64
	for _, c := range h.Counts {
		total += c
	}
	res := map[string]float64{"count": float64(total)}
	if total == 0 {
		return res
	}
	q := func(p float64) float64 {
		need := uint64(math.Ceil(p * float64(total)))
		var cum uint64
		for i, c := range h.Counts {
			cum += c
			if cum >= need && c > 0 {
				ub := h.Buckets[i+1]
				if math.IsInf(ub, 1) {
					ub = h.Buckets[i]
				}
				return ub * 1e3
			}
		}
		return math.NaN()
	}
	res["p50_ms"], res["p99_ms"], res["p999_ms"], res["p9999_ms"], res["max_ms"] = q(0.5), q(0.99), q(0.999), q(0.9999), q(1)
	return res
}
