// Timer-precision experiment: N goroutines each sleep to absolute deadlines every ITL;
// report lateness distribution, with or without a timerfd heartbeat registered in the netpoller.
package main

import (
	"flag"
	"fmt"
	"os"
	"sort"
	"sync"
	"time"

	"golang.org/x/sys/unix"
)

func heartbeat(period time.Duration) {
	fd, err := unix.TimerfdCreate(unix.CLOCK_MONOTONIC, unix.TFD_NONBLOCK|unix.TFD_CLOEXEC)
	if err != nil {
		panic(err)
	}
	ts := unix.NsecToTimespec(int64(period))
	if err := unix.TimerfdSettime(fd, 0, &unix.ItimerSpec{Interval: ts, Value: ts}, nil); err != nil {
		panic(err)
	}
	f := os.NewFile(uintptr(fd), "timerfd")
	buf := make([]byte, 8)
	for {
		if _, err := f.Read(buf); err != nil {
			panic(err)
		}
	}
}

func main() {
	n := flag.Int("n", 10, "goroutines")
	itl := flag.Duration("itl", 20*time.Millisecond, "interval")
	dur := flag.Duration("dur", 3*time.Second, "duration")
	hb := flag.Duration("hb", 0, "heartbeat period (0=off)")
	flag.Parse()
	if *hb > 0 {
		go heartbeat(*hb)
	}
	var mu sync.Mutex
	var lat []int64
	var wg sync.WaitGroup
	start := time.Now()
	for g := 0; g < *n; g++ {
		wg.Add(1)
		go func(g int) {
			defer wg.Done()
			base := start.Add(time.Duration(g) * (*itl) / time.Duration(*n))
			local := make([]int64, 0, 1024)
			for k := 1; ; k++ {
				dl := base.Add(time.Duration(k) * (*itl))
				if dl.Sub(start) > *dur {
					break
				}
				time.Sleep(time.Until(dl))
				local = append(local, int64(time.Since(dl)))
			}
			mu.Lock()
			lat = append(lat, local...)
			mu.Unlock()
		}(g)
	}
	wg.Wait()
	sort.Slice(lat, func(i, j int) bool { return lat[i] < lat[j] })
	q := func(p float64) float64 { return float64(lat[int(p*float64(len(lat)-1))]) / 1e3 }
	fmt.Printf("n=%d itl=%v hb=%v samples=%d lateness_us p50=%.0f p90=%.0f p99=%.0f p999=%.0f max=%.0f\n",
		*n, *itl, *hb, len(lat), q(0.5), q(0.9), q(0.99), q(0.999), float64(lat[len(lat)-1])/1e3)
}
