package rv

import (
	"bufio"
	"bytes"
	"errors"
	"io"
)

// SSEReader parses a text/event-stream body per the WHATWG event-stream rules that matter for
// OpenAI streams: "data:" lines (one optional leading space stripped) are joined with "\n";
// "event:" sets the type; ":" lines are comments; a blank line dispatches the event.
// Line terminators LF and CRLF are handled; a bare CR inside a line also terminates a line.
type SSEReader struct {
	br      *bufio.Reader
	data    []byte
	event   []byte
	hasData bool
	long    []byte
	pending [][]byte // extra lines split out of one LF-terminated line by bare CRs
}

// NewSSEReader wraps r with a bufio.Reader of the given size.
func NewSSEReader(r io.Reader, size int) *SSEReader {
	return &SSEReader{br: bufio.NewReaderSize(r, size)}
}

// Reset discards all state and reads from r next (buffers are kept for reuse).
func (r *SSEReader) Reset(src io.Reader) {
	r.br.Reset(src)
	r.data, r.event, r.long = r.data[:0], r.event[:0], r.long[:0]
	r.hasData = false
	r.pending = r.pending[:0]
}

// ErrSSETruncated means the body ended in the middle of an event (no terminating blank line).
var ErrSSETruncated = errors.New("rv: sse stream ended mid-event")

// Next returns the next dispatched event. data/event are valid until the next call.
// At a clean end of stream it returns io.EOF; if bytes of an undispatched event were pending,
// it returns ErrSSETruncated; transport errors are returned as-is.
func (r *SSEReader) Next() (data []byte, event []byte, err error) {
	for {
		line, err := r.line()
		if err != nil {
			if err == io.EOF && (r.hasData || len(r.event) > 0) {
				return nil, nil, ErrSSETruncated
			}
			return nil, nil, err
		}
		if len(line) == 0 {
			if r.hasData {
				r.hasData = false
				d := r.data
				ev := r.event
				r.data = r.data[:0]
				r.event = r.event[:0]
				return d, ev, nil
			}
			r.event = r.event[:0]
			continue
		}
		if line[0] == ':' {
			continue
		}
		field, value := line, []byte(nil)
		if i := bytes.IndexByte(line, ':'); i >= 0 {
			field, value = line[:i], line[i+1:]
			if len(value) > 0 && value[0] == ' ' {
				value = value[1:]
			}
		}
		switch string(field) {
		case "data":
			if r.hasData {
				r.data = append(r.data, '\n')
			}
			r.data = append(r.data, value...)
			r.hasData = true
		case "event":
			r.event = append(r.event[:0], value...)
		}
	}
}

// line returns the next line without its terminator.
func (r *SSEReader) line() ([]byte, error) {
	if len(r.pending) > 0 {
		l := r.pending[0]
		r.pending = r.pending[1:]
		return l, nil
	}
	l, err := r.br.ReadSlice('\n')
	if err == bufio.ErrBufferFull {
		r.long = append(r.long[:0], l...)
		for err == bufio.ErrBufferFull {
			l, err = r.br.ReadSlice('\n')
			r.long = append(r.long, l...)
		}
		l = r.long
	}
	if err != nil {
		if err == io.EOF && len(l) > 0 {
			// final line without terminator: SSE says an unterminated line is discarded
			// together with any undispatched event.
			return nil, io.EOF
		}
		return nil, err
	}
	l = l[:len(l)-1]
	if len(l) > 0 && l[len(l)-1] == '\r' {
		l = l[:len(l)-1]
	}
	if bytes.IndexByte(l, '\r') >= 0 {
		parts := bytes.Split(l, []byte{'\r'})
		r.pending = append(r.pending[:0], parts[1:]...)
		return parts[0], nil
	}
	return l, nil
}
