package rv

import (
	"bufio"
	"encoding/json"
	"os"
	"sync"
	"sync/atomic"
	"time"
)

// JSONL is an asynchronous JSON-lines writer. Records are marshalled in the caller's goroutine
// (spreading encode cost over cores) and handed to one writer goroutine. Send blocks when the
// queue is full: a record is never silently dropped (completeness beats latency here, and the
// record is only written after the measured request has finished).
type JSONL struct {
	ch      chan []byte
	f       *os.File
	w       *bufio.Writer
	wg      sync.WaitGroup
	n       atomic.Int64
	blocked atomic.Int64
	closeMu sync.Mutex
	closed  bool
}

// NewJSONL creates/truncates path.
func NewJSONL(path string, queue int) (*JSONL, error) {
	f, err := os.Create(path)
	if err != nil {
		return nil, err
	}
	j := &JSONL{ch: make(chan []byte, queue), f: f, w: bufio.NewWriterSize(f, 1<<20)}
	j.wg.Add(1)
	go j.loop()
	return j, nil
}

func (j *JSONL) loop() {
	defer j.wg.Done()
	t := time.NewTicker(time.Second)
	defer t.Stop()
	for {
		select {
		case b, ok := <-j.ch:
			if !ok {
				j.w.Flush()
				return
			}
			j.w.Write(b)
		case <-t.C:
			j.w.Flush()
		}
	}
}

// Write marshals v and enqueues it.
func (j *JSONL) Write(v any) error {
	b, err := json.Marshal(v)
	if err != nil {
		return err
	}
	b = append(b, '\n')
	select {
	case j.ch <- b:
	default:
		j.blocked.Add(1)
		j.ch <- b
	}
	j.n.Add(1)
	return nil
}

// Count returns records enqueued; Blocked how many Write calls found the queue full.
func (j *JSONL) Count() int64   { return j.n.Load() }
func (j *JSONL) Blocked() int64 { return j.blocked.Load() }

// Close flushes and closes the file.
func (j *JSONL) Close() error {
	j.closeMu.Lock()
	defer j.closeMu.Unlock()
	if j.closed {
		return nil
	}
	j.closed = true
	close(j.ch)
	j.wg.Wait()
	if err := j.f.Sync(); err != nil {
		return err
	}
	return j.f.Close()
}
