package main

import (
	"fmt"
	"io"
	"os"
	"strconv"

	"golang.org/x/exp/trace"
)

// dispTimeline prints every state transition of the dispatcher goroutine (the one logging
// "dispatch-woke") in the 8 ms before its worst late wake, plus Proc transitions of the P it ends on.
func dispTimeline(path string) {
	f, _ := os.Open(path)
	r, _ := trace.NewReader(f)
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
	var disp trace.GoID = -1
	var wakeT trace.Time
	var late int64
	for _, ev := range evs {
		if ev.Kind() == trace.EventLog && ev.Log().Category == "dispatch-woke" {
			disp = ev.Goroutine()
			v, _ := strconv.ParseInt(ev.Log().Message, 10, 64)
			if v > late {
				late, wakeT = v, ev.Time()
			}
		}
	}
	// the olg request lateness includes catch-up; find the max request lateness log too
	fmt.Printf("== %s dispatcher g%d worst woke lateness %.3f ms\n", path, disp, float64(late)/1e6)
	lo := wakeT - 9_000_000
	for _, ev := range evs {
		if ev.Time() < lo || ev.Time() > wakeT+200_000 {
			continue
		}
		rel := float64(ev.Time()-wakeT) / 1e6
		switch ev.Kind() {
		case trace.EventStateTransition:
			st := ev.StateTransition()
			if st.Resource.Kind == trace.ResourceGoroutine && st.Resource.Goroutine() == disp {
				from, to := st.Goroutine()
				stk := ""
				for fr := range ev.Stack().Frames() {
					stk = fr.Func
					break
				}
				fmt.Printf("  %8.3f %v->%v %q P%d M%d %s\n", rel, from, to, st.Reason, ev.Proc(), ev.Thread(), stk)
			}
		case trace.EventLog:
			if ev.Goroutine() == disp {
				fmt.Printf("  %8.3f log %s=%s\n", rel, ev.Log().Category, ev.Log().Message)
			}
		}
	}
}
