package main

import (
	"fmt"
	"io"
	"os"
	"strconv"

	"golang.org/x/exp/trace"
)

// wide: dispatcher transitions in the 40 ms before the worst wake, and per-P busy fraction
// (time with a goroutine Running) in 1 ms bins over the 15 ms before the wake.
func wide(path string) {
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
	fmt.Printf("== %s: g%d late %.3f ms\n", path, disp, float64(late)/1e6)
	for _, ev := range evs {
		if ev.Time() < wakeT-40_000_000 || ev.Time() > wakeT {
			continue
		}
		if ev.Kind() == trace.EventStateTransition {
			st := ev.StateTransition()
			if st.Resource.Kind == trace.ResourceGoroutine && st.Resource.Goroutine() == disp {
				from, to := st.Goroutine()
				fmt.Printf("  %9.3f %v->%v %q P%d M%d\n", float64(ev.Time()-wakeT)/1e6, from, to, st.Reason, ev.Proc(), ev.Thread())
			}
		}
	}
	// per-P running goroutine intervals
	type iv struct{ a, b trace.Time }
	runStart := map[trace.ProcID]trace.Time{}
	busy := map[trace.ProcID][]iv{}
	for _, ev := range evs {
		if ev.Kind() != trace.EventStateTransition {
			continue
		}
		st := ev.StateTransition()
		if st.Resource.Kind != trace.ResourceGoroutine {
			continue
		}
		from, to := st.Goroutine()
		p := ev.Proc()
		if to == trace.GoRunning {
			runStart[p] = ev.Time()
		} else if from == trace.GoRunning {
			if s, ok := runStart[p]; ok {
				busy[p] = append(busy[p], iv{s, ev.Time()})
				delete(runStart, p)
			}
		}
	}
	lo := wakeT - 15_000_000
	fmt.Printf("  per-P running fraction per 1ms bin, from -15ms to 0:\n")
	for p := trace.ProcID(0); p < 8; p++ {
		line := fmt.Sprintf("   P%d ", p)
		for b := 0; b < 15; b++ {
			s, e := lo+trace.Time(b)*1_000_000, lo+trace.Time(b+1)*1_000_000
			var tot trace.Time
			for _, x := range busy[p] {
				a, bb := max(x.a, s), min(x.b, e)
				if bb > a {
					tot += bb - a
				}
			}
			line += fmt.Sprintf("%3d", int(100*float64(tot)/1e6))
		}
		fmt.Println(line)
	}
}
