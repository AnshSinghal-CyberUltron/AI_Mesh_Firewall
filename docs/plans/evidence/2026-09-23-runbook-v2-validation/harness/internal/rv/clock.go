// Package rv holds the code shared by synthprov, olg and rvproxy.
package rv

import (
	"fmt"
	"os"
	"time"

	"golang.org/x/sys/unix"
)

// Epoch is the process-wide monotonic reference. All *_ns values the harness records are
// durations on ONE host's monotonic clock (time.Since uses the monotonic reading); the
// harness never subtracts timestamps taken on different hosts.
var Epoch = time.Now()

// NowNs returns monotonic nanoseconds since Epoch.
func NowNs() int64 { return int64(time.Since(Epoch)) }

// SleepUntil sleeps until the monotonic instant t. Precision depends on StartHeartbeat.
func SleepUntil(t time.Time) {
	if d := time.Until(t); d > 0 {
		time.Sleep(d)
	}
}

// StartHeartbeat registers a periodic Linux timerfd with the Go netpoller.
//
// Why: the Go runtime waits in epoll_pwait with MILLISECOND granularity when all Ps are idle
// (runtime/netpoll_epoll.go: waitms = delay/1e6, minimum 1), so a plain time.Sleep can wake up
// to ~1 ms late. Measured on this controller (evidence/harness-builder/timer-exp/results.txt):
// without heartbeat p50 ~0.5 ms / p99 ~1.06 ms lateness; with a 100 us heartbeat p99 12-100 us.
// The timerfd wakes the netpoller every period, so due timers are serviced promptly.
func StartHeartbeat(period time.Duration) error {
	if period <= 0 {
		return nil
	}
	fd, err := unix.TimerfdCreate(unix.CLOCK_MONOTONIC, unix.TFD_NONBLOCK|unix.TFD_CLOEXEC)
	if err != nil {
		return err
	}
	ts := unix.NsecToTimespec(int64(period))
	if err := unix.TimerfdSettime(fd, 0, &unix.ItimerSpec{Interval: ts, Value: ts}, nil); err != nil {
		return err
	}
	f := os.NewFile(uintptr(fd), "rv-heartbeat")
	go func() {
		var buf [8]byte
		for {
			if _, err := f.Read(buf[:]); err != nil {
				return
			}
		}
	}()
	return nil
}

// PreciseClock sleeps with clock_nanosleep(CLOCK_MONOTONIC, TIMER_ABSTIME): the calling thread
// blocks in the kernel on an hrtimer, so the wake-up does not depend on the Go runtime servicing
// its per-P timer heaps.
//
// Why: an execution trace of olg (evidence/harness-builder/tracestall, runs/diag-stall) showed
// the dispatcher's time.Sleep timer firing ~2 ms late: the timer sat in the heap of a P that had
// gone idle, and no spinning M stole it until ~2 ms later. Go's monotonic clock is CLOCK_MONOTONIC,
// so absolute deadlines map exactly.
type PreciseClock struct {
	base     time.Time
	baseMono int64
}

// NewPreciseClock pairs a time.Time with the raw CLOCK_MONOTONIC reading.
func NewPreciseClock() *PreciseClock {
	var ts unix.Timespec
	unix.ClockGettime(unix.CLOCK_MONOTONIC, &ts)
	t := time.Now()
	return &PreciseClock{base: t, baseMono: ts.Nano()}
}

// SleepUntil blocks the calling goroutine's thread until t (no-op if t has passed).
func (c *PreciseClock) SleepUntil(t time.Time) {
	target := c.baseMono + int64(t.Sub(c.base))
	ts := unix.NsecToTimespec(target)
	for {
		if err := unix.ClockNanosleep(unix.CLOCK_MONOTONIC, unix.TIMER_ABSTIME, &ts, nil); err != unix.EINTR {
			return
		}
	}
}

// SetRealtime puts the CALLING OS thread (use runtime.LockOSThread first) in SCHED_FIFO at prio;
// returns a status string for manifests. Real-time threads also sleep with zero timer slack.
func SetRealtime(prio int) string {
	if prio <= 0 {
		return "off"
	}
	attr := unix.SchedAttr{Size: unix.SizeofSchedAttr, Policy: unix.SCHED_FIFO, Priority: uint32(prio)}
	if err := unix.SchedSetAttr(0, &attr, 0); err != nil {
		return "failed: " + err.Error()
	}
	return fmt.Sprintf("SCHED_FIFO:%d", prio)
}
