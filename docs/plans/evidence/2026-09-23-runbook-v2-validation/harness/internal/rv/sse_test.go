package rv

import (
	"errors"
	"io"
	"strings"
	"testing"
	"testing/iotest"
)

func readAll(t *testing.T, r io.Reader, size int) ([]string, error) {
	t.Helper()
	sr := NewSSEReader(r, size)
	var out []string
	for {
		d, ev, err := sr.Next()
		if err != nil {
			return out, err
		}
		s := string(d)
		if len(ev) > 0 {
			s = string(ev) + "|" + s
		}
		out = append(out, s)
	}
}

func TestSSEFraming(t *testing.T) {
	cases := []struct {
		name string
		in   string
		want []string
		err  error
	}{
		{"lf", "data: {\"a\":1}\n\ndata: [DONE]\n\n", []string{`{"a":1}`, "[DONE]"}, io.EOF},
		{"crlf", "data: x\r\n\r\ndata: [DONE]\r\n\r\n", []string{"x", "[DONE]"}, io.EOF},
		{"bare-cr", "data: x\r\rdata: y\r\r\n", []string{"x", "y"}, io.EOF},
		{"no-space", "data:x\n\n", []string{"x"}, io.EOF},
		{"multiline", "data: a\ndata: b\n\n", []string{"a\nb"}, io.EOF},
		{"comment+event", ": keepalive\nevent: error\ndata: {}\n\n", []string{"error|{}"}, io.EOF},
		{"empty-data", "data:\n\n", []string{""}, io.EOF},
		{"blank-lines-only", "\n\n\n", nil, io.EOF},
		{"truncated", "data: x\n\ndata: y", []string{"x"}, io.EOF},                  // unterminated line discarded
		{"truncated-event", "data: x\n\ndata: y\n", []string{"x"}, ErrSSETruncated}, // no blank line
		{"field-without-colon", "data\n\n", []string{""}, io.EOF},
		{"unknown-field", "id: 7\nretry: 5\ndata: z\n\n", []string{"z"}, io.EOF},
	}
	for _, tc := range cases {
		for _, oneByte := range []bool{false, true} {
			var r io.Reader = strings.NewReader(tc.in)
			if oneByte {
				r = iotest.OneByteReader(r)
			}
			got, err := readAll(t, r, 16)
			if !errors.Is(err, tc.err) {
				t.Fatalf("%s onebyte=%v: err=%v want %v", tc.name, oneByte, err, tc.err)
			}
			if strings.Join(got, "¦") != strings.Join(tc.want, "¦") {
				t.Fatalf("%s onebyte=%v: got %q want %q", tc.name, oneByte, got, tc.want)
			}
		}
	}
}

func TestSSELongLineBeyondBuffer(t *testing.T) {
	long := strings.Repeat("x", 100000)
	got, err := readAll(t, strings.NewReader("data: "+long+"\n\ndata: [DONE]\n\n"), 16)
	if err != io.EOF || len(got) != 2 || got[0] != long || got[1] != "[DONE]" {
		t.Fatalf("err=%v n=%d", err, len(got))
	}
}

func TestSSETransportErrorPropagates(t *testing.T) {
	boom := errors.New("reset")
	r := io.MultiReader(strings.NewReader("data: a\n\ndata: b"), iotest.ErrReader(boom))
	got, err := readAll(t, r, 64)
	if !errors.Is(err, boom) || len(got) != 1 {
		t.Fatalf("got %q err %v", got, err)
	}
}
