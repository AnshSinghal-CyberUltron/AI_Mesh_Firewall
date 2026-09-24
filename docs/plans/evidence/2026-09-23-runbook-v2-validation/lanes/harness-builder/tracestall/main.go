// tracestall: find the olg dispatcher goroutine (it emits trace.Log "dispatch-woke" with the
// wake lateness in ns) and explain its largest late wake-up: its state transitions around it and
// what every P / goroutine was doing in the stall window.
package main

import (
	"fmt"
	"io"
	"os"
	"sort"
	"strconv"

	"golang.org/x/exp/trace"
)

type span struct {
	t    trace.Time
	desc string
}

func main() {
	if len(os.Args) > 2 && os.Args[2] == "wide" {
		wide(os.Args[1])
		return
	}
	if len(os.Args) > 2 && os.Args[2] == "disp" {
		dispTimeline(os.Args[1])
		return
	}
	if len(os.Args) > 2 && os.Args[2] == "p1" {
		p1Detail(os.Args[1])
		return
	}
	f, err := os.Open(os.Args[1])
	if err != nil {
		panic(err)
	}
	r, err := trace.NewReader(f)
	if err != nil {
		panic(err)
	}
	var evs []trace.Event
	for {
		ev, err := r.ReadEvent()
		if err == io.EOF {
			break
		}
		if err != nil {
			panic(err)
		}
		evs = append(evs, ev)
	}
	// dispatcher goroutine + worst woke
	var disp trace.GoID = -1
	var worstT trace.Time
	var worstLate int64
	for _, ev := range evs {
		if ev.Kind() == trace.EventLog && ev.Log().Category == "dispatch-woke" {
			disp = ev.Goroutine()
			v, _ := strconv.ParseInt(ev.Log().Message, 10, 64)
			if v > worstLate {
				worstLate, worstT = v, ev.Time()
			}
		}
	}
	fmt.Printf("events=%d dispatcher=g%d worst wake lateness=%.3f ms at t=%d\n", len(evs), disp, float64(worstLate)/1e6, worstT)
	lo, hi := worstT-trace.Time(worstLate)-2_000_000, worstT+500_000
	// dispatcher transitions in window
	fmt.Println("--- dispatcher state transitions (ms relative to wake event) ---")
	for _, ev := range evs {
		if ev.Time() < lo || ev.Time() > hi {
			continue
		}
		rel := float64(ev.Time()-worstT) / 1e6
		switch ev.Kind() {
		case trace.EventStateTransition:
			st := ev.StateTransition()
			if st.Resource.Kind == trace.ResourceGoroutine && st.Resource.Goroutine() == disp {
				from, to := st.Goroutine()
				fmt.Printf("%9.3f  g%d %v -> %v reason=%q on P%d M%d\n", rel, disp, from, to, st.Reason, ev.Proc(), ev.Thread())
			}
		case trace.EventLog:
			if ev.Goroutine() == disp {
				fmt.Printf("%9.3f  log %s=%s\n", rel, ev.Log().Category, ev.Log().Message)
			}
		}
	}
	// per-P activity in the stall window [wake-lateness, wake]
	s0, s1 := worstT-trace.Time(worstLate), worstT
	fmt.Printf("--- per-P / per-M activity in stall window [%.3f, 0] ms ---\n", -float64(worstLate)/1e6)
	type key struct{ p trace.ProcID }
	running := map[trace.ProcID][]string{}
	syscalls := []string{}
	procStates := map[trace.ProcID][]string{}
	gcEvents := []string{}
	for _, ev := range evs {
		if ev.Time() < s0-1_000_000 || ev.Time() > s1 {
			continue
		}
		rel := float64(ev.Time()-worstT) / 1e6
		switch ev.Kind() {
		case trace.EventStateTransition:
			st := ev.StateTransition()
			switch st.Resource.Kind {
			case trace.ResourceGoroutine:
				from, to := st.Goroutine()
				if to == trace.GoRunning || from == trace.GoRunning {
					running[ev.Proc()] = append(running[ev.Proc()], fmt.Sprintf("%.3f g%d %v->%v %s", rel, st.Resource.Goroutine(), from, to, st.Reason))
				}
				if to == trace.GoSyscall || from == trace.GoSyscall {
					syscalls = append(syscalls, fmt.Sprintf("%.3f g%d %v->%v P%d M%d", rel, st.Resource.Goroutine(), from, to, ev.Proc(), ev.Thread()))
				}
			case trace.ResourceProc:
				from, to := st.Proc()
				procStates[st.Resource.Proc()] = append(procStates[st.Resource.Proc()], fmt.Sprintf("%.3f %v->%v M%d", rel, from, to, ev.Thread()))
			}
		case trace.EventRangeBegin, trace.EventRangeEnd:
			gcEvents = append(gcEvents, fmt.Sprintf("%.3f %v %s", rel, ev.Kind(), ev.Range().Name))
		}
	}
	var ps []int
	for p := range procStates {
		ps = append(ps, int(p))
	}
	sort.Ints(ps)
	for _, p := range ps {
		st := procStates[trace.ProcID(p)]
		fmt.Printf("P%d proc transitions (%d): %v\n", p, len(st), head(st, 6))
	}
	fmt.Printf("syscall transitions in window (%d): %v\n", len(syscalls), head(syscalls, 40))
	fmt.Printf("ranges (GC/STW etc): %v\n", head(gcEvents, 20))
	for _, p := range ps {
		rs := running[trace.ProcID(p)]
		fmt.Printf("P%d running-transitions: %d  first/last: %v\n", p, len(rs), ends(rs))
	}
}

func head(s []string, n int) []string {
	if len(s) > n {
		return s[:n]
	}
	return s
}
func ends(s []string) []string {
	if len(s) <= 4 {
		return s
	}
	return append(append([]string{}, s[:2]...), s[len(s)-2:]...)
}
