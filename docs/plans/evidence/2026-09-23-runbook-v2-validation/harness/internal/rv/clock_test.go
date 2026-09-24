package rv

import (
	"sort"
	"sync"
	"testing"
	"time"
)

// TestPreciseClockLateness compares time.Sleep-based and clock_nanosleep-based periodic wake-ups
// (1 ms period) while 2000 background goroutines tick on Go timers (the olg situation).
func TestPreciseClockLateness(t *testing.T) {
	if testing.Short() {
		t.Skip()
	}
	StartHeartbeat(100 * time.Microsecond)
	stop := make(chan struct{})
	var wg sync.WaitGroup
	for i := 0; i < 2000; i++ {
		wg.Add(1)
		go func(i int) {
			defer wg.Done()
			d := 20*time.Millisecond + time.Duration(i)*time.Microsecond
			for {
				select {
				case <-stop:
					return
				case <-time.After(d):
				}
			}
		}(i)
	}
	measure := func(sleep func(time.Time)) []time.Duration {
		var lat []time.Duration
		start := time.Now()
		for k := 1; k <= 3000; k++ {
			due := start.Add(time.Duration(k) * time.Millisecond)
			sleep(due)
			lat = append(lat, time.Since(due))
		}
		sort.Slice(lat, func(i, j int) bool { return lat[i] < lat[j] })
		return lat
	}
	pc := NewPreciseClock()
	goT := measure(SleepUntil)
	kern := measure(pc.SleepUntil)
	close(stop)
	wg.Wait()
	q := func(l []time.Duration, p float64) time.Duration { return l[int(p*float64(len(l)-1))] }
	t.Logf("go timer   : p50=%v p99=%v p999=%v max=%v", q(goT, .5), q(goT, .99), q(goT, .999), goT[len(goT)-1])
	t.Logf("nanosleep  : p50=%v p99=%v p999=%v max=%v", q(kern, .5), q(kern, .99), q(kern, .999), kern[len(kern)-1])
	if kern[len(kern)-1] > 5*time.Millisecond {
		t.Fatalf("nanosleep max lateness %v", kern[len(kern)-1])
	}
}
