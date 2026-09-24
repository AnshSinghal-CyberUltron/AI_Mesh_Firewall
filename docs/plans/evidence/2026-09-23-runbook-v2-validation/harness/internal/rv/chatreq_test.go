package rv

import (
	"bytes"
	"encoding/json"
	"errors"
	"fmt"
	"math/rand/v2"
	"testing"
)

type refReq struct {
	Stream, IncludeUsage     bool
	MaxTokens, MaxCompletion int
	NumMessages, NumTools    int
	ToolName, Text           string
}

func refChatReq(b []byte) (refReq, error) {
	r := refReq{MaxTokens: -1, MaxCompletion: -1}
	if !json.Valid(b) {
		return r, ErrJSON
	}
	if hasDupKeys(b) {
		return r, errSkip
	}
	dec := json.NewDecoder(bytes.NewReader(b))
	dec.UseNumber()
	var v any
	if dec.Decode(&v) != nil {
		return r, ErrJSON
	}
	top, ok := v.(map[string]any)
	if !ok {
		return r, ErrJSON
	}
	boolv := func(x any) (bool, bool) {
		switch t := x.(type) {
		case nil:
			return false, true
		case bool:
			return t, true
		}
		return false, false
	}
	intv := func(x any) (int, bool) {
		if x == nil {
			return -1, true
		}
		return refInt(x)
	}
	if x, ok := top["stream"]; ok {
		if r.Stream, ok = boolv(x); !ok {
			return r, ErrJSON
		}
	}
	if x, ok := top["max_tokens"]; ok {
		if r.MaxTokens, ok = intv(x); !ok {
			return r, ErrJSON
		}
	}
	if x, ok := top["max_completion_tokens"]; ok {
		if r.MaxCompletion, ok = intv(x); !ok {
			return r, ErrJSON
		}
	}
	if x, ok := top["stream_options"]; ok && x != nil {
		m, ok := x.(map[string]any)
		if !ok {
			return r, ErrJSON
		}
		if y, ok := m["include_usage"]; ok {
			if r.IncludeUsage, ok = boolv(y); !ok {
				return r, ErrJSON
			}
		}
	}
	if x, ok := top["messages"]; ok && x != nil {
		arr, ok := x.([]any)
		if !ok {
			return r, ErrJSON
		}
		for _, el := range arr {
			m, ok := el.(map[string]any)
			if !ok {
				return r, ErrJSON
			}
			r.NumMessages++
			switch c := m["content"].(type) {
			case nil:
			case string:
				r.Text += c
			case []any:
				for i, p := range c {
					pm, ok := p.(map[string]any)
					if !ok {
						return r, ErrJSON
					}
					if i > 0 {
						r.Text += "\n"
					}
					switch t := pm["text"].(type) {
					case nil:
					case string:
						r.Text += t
					default:
						return r, ErrJSON
					}
				}
			default:
				return r, ErrJSON
			}
			r.Text += "\n"
		}
	}
	if x, ok := top["tools"]; ok && x != nil {
		arr, ok := x.([]any)
		if !ok {
			return r, ErrJSON
		}
		for i, el := range arr {
			m, ok := el.(map[string]any)
			if !ok {
				return r, ErrJSON
			}
			r.NumTools++
			if f, ok := m["function"]; ok && f != nil {
				fm, ok := f.(map[string]any)
				if !ok {
					return r, ErrJSON
				}
				switch n := fm["name"].(type) {
				case nil:
				case string:
					if i == 0 {
						r.ToolName = n
					}
				default:
					return r, ErrJSON
				}
			}
		}
	}
	return r, nil
}

func compareReq(b []byte) string {
	var cr ChatReq
	err := ParseChatRequest(b, &cr)
	ref, rerr := refChatReq(b)
	if errors.Is(rerr, errSkip) {
		return ""
	}
	if (err == nil) != (rerr == nil) {
		return fmt.Sprintf("acceptance differs mine=%v ref=%v input=%q", err, rerr, b)
	}
	if err != nil {
		return ""
	}
	got := refReq{Stream: cr.Stream, IncludeUsage: cr.IncludeUsage, MaxTokens: cr.MaxTokens, MaxCompletion: cr.MaxCompletionTokens,
		NumMessages: cr.NumMessages, NumTools: cr.NumTools, ToolName: string(cr.ToolName), Text: string(cr.Text)}
	if got != ref {
		return fmt.Sprintf("fields differ\n mine=%+v\n  ref=%+v\ninput=%q", got, ref, b)
	}
	return ""
}

func randReq(r *rand.Rand) map[string]any {
	m := map[string]any{"model": "gpt-" + randString(r)}
	if r.IntN(5) > 0 {
		m["stream"] = maybeWrong(r, r.IntN(2) == 0)
	}
	if r.IntN(3) > 0 {
		m["max_tokens"] = maybeWrong(r, r.IntN(2000))
	}
	if r.IntN(6) == 0 {
		m["max_completion_tokens"] = maybeWrong(r, r.IntN(2000))
	}
	if r.IntN(3) == 0 {
		m["stream_options"] = maybeWrong(r, map[string]any{"include_usage": maybeWrong(r, r.IntN(2) == 0)})
	}
	var msgs []any
	for n := r.IntN(4); n >= 0; n-- {
		msg := map[string]any{"role": []string{"system", "user", "assistant"}[r.IntN(3)]}
		switch r.IntN(5) {
		case 0:
			msg["content"] = nil
		case 1:
			var parts []any
			for k := r.IntN(3); k >= 0; k-- {
				p := map[string]any{"type": "text"}
				if r.IntN(5) > 0 {
					p["text"] = maybeWrong(r, randString(r))
				}
				parts = append(parts, maybeWrong(r, p))
			}
			msg["content"] = parts
		case 2:
		default:
			msg["content"] = maybeWrong(r, randString(r))
		}
		msgs = append(msgs, maybeWrong(r, msg))
	}
	m["messages"] = maybeWrong(r, msgs)
	if r.IntN(4) == 0 {
		var tools []any
		for k := r.IntN(3); k >= 0; k-- {
			f := map[string]any{"parameters": map[string]any{"type": "object"}}
			if r.IntN(4) > 0 {
				f["name"] = maybeWrong(r, randString(r))
			}
			tools = append(tools, maybeWrong(r, map[string]any{"type": "function", "function": maybeWrong(r, f)}))
		}
		m["tools"] = maybeWrong(r, tools)
	}
	return m
}

func TestParseChatRequestDifferential(t *testing.T) {
	r := rand.New(rand.NewPCG(5, 6))
	var b bytes.Buffer
	accepted := 0
	for i := 0; i < 150000; i++ {
		b.Reset()
		enc(r, randReq(r), &b)
		in := b.Bytes()
		if r.IntN(20) == 0 && len(in) > 2 {
			in = append([]byte(nil), in...)
			in[r.IntN(len(in))] = byte(r.IntN(256))
		}
		if msg := compareReq(in); msg != "" {
			t.Fatalf("iteration %d: %s", i, msg)
		}
		var cr ChatReq
		if ParseChatRequest(in, &cr) == nil {
			accepted++
		}
	}
	if accepted < 60000 {
		t.Fatalf("too few valid: %d", accepted)
	}
	t.Logf("150000 random requests, %d accepted, all agree with encoding/json reference", accepted)
}

func TestParseChatRequestBufferReuse(t *testing.T) {
	var cr ChatReq
	a := []byte(`{"messages":[{"role":"user","content":"first message with a long body"}],"max_tokens":5}`)
	bb := []byte(`{"messages":[{"role":"user","content":"x"}],"tools":[{"function":{"name":"f"}}],"stream":true}`)
	if ParseChatRequest(a, &cr) != nil || string(cr.Text) != "first message with a long body\n" || cr.MaxTokens != 5 {
		t.Fatalf("%+v", cr)
	}
	if ParseChatRequest(bb, &cr) != nil || string(cr.Text) != "x\n" || cr.MaxTokens != -1 || !cr.Stream || string(cr.ToolName) != "f" {
		t.Fatalf("state leaked across calls: %+v", cr)
	}
	allocs := testing.AllocsPerRun(1000, func() { ParseChatRequest(a, &cr) })
	if allocs != 0 {
		t.Fatalf("ParseChatRequest allocates %.1f per call", allocs)
	}
}

func FuzzParseChatRequest(f *testing.F) {
	f.Add([]byte(`{"model":"m","messages":[{"role":"user","content":"hi"}],"stream":true,"stream_options":{"include_usage":true},"max_tokens":40}`))
	f.Add([]byte(`{"messages":[{"role":"user","content":[{"type":"text","text":"a"},{"type":"image_url"}]}],"tools":[{"type":"function","function":{"name":"f"}}]}`))
	f.Fuzz(func(t *testing.T, b []byte) {
		if msg := compareReq(b); msg != "" {
			t.Fatal(msg)
		}
	})
}
