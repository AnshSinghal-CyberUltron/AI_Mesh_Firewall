package main

import (
	"fmt"
	"io"
	"os"
	"strconv"

	"golang.org/x/exp/trace"
)

// p1Detail prints every event on the timer's P (the P where the dispatcher went to sleep) and
// every event of the goroutines that ran on it, between the dispatcher's sleep and its wake.
func p1Detail(path string) {
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
	var wakeT trace.Time
	var late int64
	for _, ev := range evs {
		if ev.Kind() == trace.EventLog && ev.Log().Category == "dispatch-woke" {
			v, _ := strconv.ParseInt(ev.Log().Message, 10, 64)
			if v > late {
				late, wakeT = v, ev.Time()
			}
		}
	}
	// find the sleep transition just before the stall and the P it happened on
	var sleepT trace.Time
	var sleepP trace.ProcID = -1
	for _, ev := range evs {
		if ev.Time() >= wakeT {
			break
		}
		if ev.Kind() == trace.EventStateTransition {
			st := ev.StateTransition()
			if st.Resource.Kind == trace.ResourceGoroutine && st.Resource.Goroutine() == 1 {
				if _, to := st.Goroutine(); to == trace.GoWaiting {
					sleepT, sleepP = ev.Time(), ev.Proc()
				}
			}
		}
	}
	fmt.Printf("== %s\nsleep at %.3f ms on P%d; wake log at 0 (late %.3f ms)\n", path, float64(sleepT-wakeT)/1e6, sleepP, float64(late)/1e6)
	for _, ev := range evs {
		if ev.Time() < sleepT || ev.Time() > wakeT {
			continue
		}
		if ev.Proc() != sleepP {
			continue
		}
		rel := float64(ev.Time()-wakeT) / 1e6
		switch ev.Kind() {
		case trace.EventStateTransition:
			st := ev.StateTransition()
			switch st.Resource.Kind {
			case trace.ResourceGoroutine:
				from, to := st.Goroutine()
				stk := ""
				for fr := range ev.Stack().Frames() {
					stk = fr.Func
					break
				}
				fmt.Printf("  %8.3f g%-6d %v->%v %s M%d top=%s\n", rel, st.Resource.Goroutine(), from, to, st.Reason, ev.Thread(), stk)
			case trace.ResourceProc:
				from, to := st.Proc()
				fmt.Printf("  %8.3f P%d %v->%v M%d\n", rel, st.Resource.Proc(), from, to, ev.Thread())
			}
		case trace.EventRangeBegin, trace.EventRangeEnd, trace.EventRangeActive:
			fmt.Printf("  %8.3f range %v %s\n", rel, ev.Kind(), ev.Range().Name)
		}
	}
}
