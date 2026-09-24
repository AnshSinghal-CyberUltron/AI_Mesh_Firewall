package rv

import (
	"bytes"
	"encoding/json"
	"errors"
	"fmt"
	"math/rand/v2"
	"strings"
	"testing"
	"unicode/utf8"
)

// ---------------------------------------------------------------------------------------------
// Reference implementation: encoding/json for syntax + an explicit walk of the same schema.
// ---------------------------------------------------------------------------------------------

var errSkip = errors.New("skip: ambiguous input (duplicate keys / delta+message)")

type refTool struct {
	Index   int
	ID      string
	Name    string
	Args    string
	HasArgs bool
}

type refResult struct {
	Content        string
	ContentPresent bool
	Finish         string
	Tools          []refTool
	Usage, Error   bool
	Choices        int
}

// hasDupKeys walks the token stream and reports duplicate keys in any object.
func hasDupKeys(b []byte) bool {
	dec := json.NewDecoder(bytes.NewReader(b))
	type frame struct {
		obj  bool
		keys map[string]bool
		key  bool // next string token is a key
	}
	var st []*frame
	for {
		t, err := dec.Token()
		if err != nil {
			return false
		}
		if len(st) > 0 {
			top := st[len(st)-1]
			if top.obj && top.key {
				if k, ok := t.(string); ok {
					if top.keys[k] {
						return true
					}
					top.keys[k] = true
					top.key = false
					continue
				}
			}
		}
		switch t {
		case json.Delim('{'):
			if len(st) > 0 && st[len(st)-1].obj {
				st[len(st)-1].key = true
			}
			st = append(st, &frame{obj: true, keys: map[string]bool{}, key: true})
			continue
		case json.Delim('['):
			if len(st) > 0 && st[len(st)-1].obj {
				st[len(st)-1].key = true
			}
			st = append(st, &frame{})
			continue
		case json.Delim('}'), json.Delim(']'):
			st = st[:len(st)-1]
		}
		if len(st) > 0 && st[len(st)-1].obj {
			st[len(st)-1].key = true
		}
	}
}

func refInt(v any) (int, bool) {
	n, ok := v.(json.Number)
	if !ok {
		return 0, false
	}
	s := string(n)
	if s == "" {
		return 0, false
	}
	x := 0
	for _, c := range s {
		if c < '0' || c > '9' {
			return 0, false
		}
		x = x*10 + int(c-'0')
		if x > 1e9 {
			return 0, false
		}
	}
	return x, true
}

func refStrOrNull(v any, present bool) (string, bool, bool) { // value, isString, ok
	if !present || v == nil {
		return "", false, true
	}
	s, ok := v.(string)
	if !ok {
		return "", false, false
	}
	return s, true, true
}

func refParse(b []byte) (refResult, error) {
	var r refResult
	if !json.Valid(b) {
		return r, ErrJSON
	}
	if hasDupKeys(b) {
		return r, errSkip
	}
	dec := json.NewDecoder(bytes.NewReader(b))
	dec.UseNumber()
	var v any
	if err := dec.Decode(&v); err != nil {
		return r, ErrJSON
	}
	top, ok := v.(map[string]any)
	if !ok {
		return r, ErrJSON
	}
	if u, ok := top["usage"]; ok && u != nil {
		r.Usage = true
	}
	if e, ok := top["error"]; ok && e != nil {
		r.Error = true
	}
	cv, present := top["choices"]
	if !present || cv == nil {
		return r, nil
	}
	arr, ok := cv.([]any)
	if !ok {
		return r, ErrJSON
	}
	for _, el := range arr {
		m, ok := el.(map[string]any)
		if !ok {
			return r, ErrJSON
		}
		r.Choices++
		idx := 0
		if iv, ok := m["index"]; ok {
			if idx, ok = refInt(iv); !ok {
				return r, ErrJSON
			}
		}
		_, hasD := m["delta"]
		_, hasM := m["message"]
		if hasD && hasM {
			return r, errSkip
		}
		var content string
		var cPresent bool
		var tools []refTool
		for _, k := range []string{"delta", "message"} {
			dv, ok := m[k]
			if !ok || dv == nil {
				continue
			}
			d, ok := dv.(map[string]any)
			if !ok {
				return r, ErrJSON
			}
			cvv, cp := d["content"]
			s, isStr, ok := refStrOrNull(cvv, cp)
			if !ok {
				return r, ErrJSON
			}
			content, cPresent = s, isStr
			if tv, ok := d["tool_calls"]; ok && tv != nil {
				ta, ok := tv.([]any)
				if !ok {
					return r, ErrJSON
				}
				for _, te := range ta {
					tm, ok := te.(map[string]any)
					if !ok {
						return r, ErrJSON
					}
					var t refTool
					if iv, ok := tm["index"]; ok {
						if t.Index, ok = refInt(iv); !ok {
							return r, ErrJSON
						}
					}
					idv, idp := tm["id"]
					if t.ID, _, ok = refStrOrNull(idv, idp); !ok {
						return r, ErrJSON
					}
					if fv, ok := tm["function"]; ok && fv != nil {
						fm, ok := fv.(map[string]any)
						if !ok {
							return r, ErrJSON
						}
						nv, np := fm["name"]
						if t.Name, _, ok = refStrOrNull(nv, np); !ok {
							return r, ErrJSON
						}
						av, ap := fm["arguments"]
						if t.Args, t.HasArgs, ok = refStrOrNull(av, ap); !ok {
							return r, ErrJSON
						}
					}
					tools = append(tools, t)
				}
			}
		}
		fv, fp := m["finish_reason"]
		finish, _, ok := refStrOrNull(fv, fp)
		if !ok {
			return r, ErrJSON
		}
		if idx == 0 {
			r.Content, r.ContentPresent, r.Finish, r.Tools = content, cPresent, finish, tools
		}
	}
	return r, nil
}

func toRef(c *Chunk) refResult {
	r := refResult{Content: string(c.Content), ContentPresent: c.ContentPresent, Finish: string(c.Finish),
		Usage: c.Usage, Error: c.Error, Choices: c.Choices}
	for _, t := range c.Tools {
		r.Tools = append(r.Tools, refTool{Index: t.Index, ID: string(t.ID), Name: string(t.Name), Args: string(t.Args), HasArgs: t.HasArgs})
	}
	return r
}

func sameResult(a, b refResult) bool {
	if a.Content != b.Content || a.ContentPresent != b.ContentPresent || a.Finish != b.Finish ||
		a.Usage != b.Usage || a.Error != b.Error || a.Choices != b.Choices || len(a.Tools) != len(b.Tools) {
		return false
	}
	for i := range a.Tools {
		if a.Tools[i] != b.Tools[i] {
			return false
		}
	}
	return true
}

// compare returns "" when ParseChunk agrees with the reference on b.
func compare(b []byte) string {
	var c Chunk
	err := ParseChunk(b, &c)
	ref, rerr := refParse(b)
	if errors.Is(rerr, errSkip) {
		return ""
	}
	if (err == nil) != (rerr == nil) {
		return fmt.Sprintf("acceptance differs: mine=%v ref=%v input=%q", err, rerr, b)
	}
	if err == nil && !sameResult(toRef(&c), ref) {
		return fmt.Sprintf("fields differ:\n mine=%+v\n  ref=%+v\ninput=%q", toRef(&c), ref, b)
	}
	return ""
}

// ---------------------------------------------------------------------------------------------
// Table tests on real OpenAI shapes
// ---------------------------------------------------------------------------------------------

func TestParseChunkOpenAIShapes(t *testing.T) {
	cases := []struct {
		in      string
		content string
		present bool
		finish  string
		usage   bool
		tools   int
		args    string
	}{
		{`{"id":"chatcmpl-1","object":"chat.completion.chunk","created":1,"model":"m","service_tier":"default","system_fingerprint":"fp","choices":[{"index":0,"delta":{"role":"assistant","content":"","refusal":null},"logprobs":null,"finish_reason":null}],"usage":null}`, "", true, "", false, 0, ""},
		{`{"choices":[{"index":0,"delta":{"content":" the"},"logprobs":null,"finish_reason":null}]}`, " the", true, "", false, 0, ""},
		{`{"choices":[{"index":0,"delta":{},"logprobs":null,"finish_reason":"stop"}]}`, "", false, "stop", false, 0, ""},
		{`{"id":"x","choices":[],"usage":{"prompt_tokens":1,"completion_tokens":2,"total_tokens":3}}`, "", false, "", true, 0, ""},
		{`{"choices":[{"index":0,"delta":{"role":"assistant","content":null,"tool_calls":[{"index":0,"id":"call_1","type":"function","function":{"name":"get","arguments":""}}]},"finish_reason":null}]}`, "", false, "", false, 1, ""},
		{`{"choices":[{"index":0,"delta":{"tool_calls":[{"index":0,"function":{"arguments":"{\"q"}}]},"finish_reason":null}]}`, "", false, "", false, 1, `{"q`},
		{`{"choices":[{"index":0,"message":{"role":"assistant","content":"Hello é😀 \"q\"\n"},"finish_reason":"stop"}],"usage":{"total_tokens":1}}`, "Hello é😀 \"q\"\n", true, "stop", true, 0, ""},
		{" \r\n\t{ \"choices\" : [ { \"delta\" : { \"content\" : \"a\" } , \"index\" : 0 } ] } \n", "a", true, "", false, 0, ""},
		{`{"choices":[{"index":1,"delta":{"content":"other"}},{"index":0,"delta":{"content":"mine"}}]}`, "mine", true, "", false, 0, ""},
	}
	for i, tc := range cases {
		var c Chunk
		if err := ParseChunk([]byte(tc.in), &c); err != nil {
			t.Fatalf("case %d: %v", i, err)
		}
		if string(c.Content) != tc.content || c.ContentPresent != tc.present || string(c.Finish) != tc.finish ||
			c.Usage != tc.usage || len(c.Tools) != tc.tools {
			t.Fatalf("case %d: got %+v", i, toRef(&c))
		}
		if tc.tools > 0 && string(c.Tools[0].Args) != tc.args {
			t.Fatalf("case %d: args %q", i, c.Tools[0].Args)
		}
		if msg := compare([]byte(tc.in)); msg != "" {
			t.Fatalf("case %d: %s", i, msg)
		}
	}
	bad := []string{``, `[]`, `{"choices":[{"delta":{"content":1}}]}`, `{"choices":{}}`, `{"choices":[{"index":-1}]}`,
		`{"choices":[{"index":1.5}]}`, `{"a":1,}`, `{"a":"\x"}`, `{"a":"` + "\x01" + `"}`, `{"a":1} x`, `{"choices":[{"delta":{"content":"x"}}]`,
		`{"choices":[{"finish_reason":3}]}`, `{"a":01}`, `{"a":1.}`, `{"a":.5}`, `{"a":tru}`, `{"a":"\ud800\u12"}`}
	for _, in := range bad {
		var c Chunk
		if err := ParseChunk([]byte(in), &c); err == nil {
			t.Fatalf("accepted invalid %q", in)
		}
		if msg := compare([]byte(in)); msg != "" {
			t.Fatal(msg)
		}
	}
}

// ---------------------------------------------------------------------------------------------
// Randomised differential test
// ---------------------------------------------------------------------------------------------

var runePool = []rune{'a', 'Z', ' ', '"', '\\', '/', '\n', '\r', '\t', '\b', '\f', 0x01, 0x1f, 0x7f, 'é', '中', '😀',
	0xFFFD, 0x2028, 0x200B, '<', '>', '&', '{', '}', '[', ']', ':', ','}

func randString(r *rand.Rand) string {
	n := r.IntN(12)
	var b strings.Builder
	for i := 0; i < n; i++ {
		b.WriteRune(runePool[r.IntN(len(runePool))])
	}
	return b.String()
}

// enc serialises a value with random whitespace, random \u escapes and random key order.
func enc(r *rand.Rand, v any, b *bytes.Buffer) {
	ws := func() {
		for r.IntN(4) == 0 {
			b.WriteByte(" \t\n\r"[r.IntN(4)])
		}
	}
	ws()
	switch x := v.(type) {
	case nil:
		b.WriteString("null")
	case bool:
		fmt.Fprint(b, x)
	case int:
		fmt.Fprint(b, x)
	case float64:
		fmt.Fprint(b, x)
	case string:
		encStr(r, x, b)
	case []any:
		b.WriteByte('[')
		for i, e := range x {
			if i > 0 {
				b.WriteByte(',')
			}
			enc(r, e, b)
		}
		ws()
		b.WriteByte(']')
	case map[string]any:
		keys := make([]string, 0, len(x))
		for k := range x {
			keys = append(keys, k)
		}
		r.Shuffle(len(keys), func(i, j int) { keys[i], keys[j] = keys[j], keys[i] })
		b.WriteByte('{')
		for i, k := range keys {
			if i > 0 {
				b.WriteByte(',')
			}
			ws()
			encStr(r, k, b)
			ws()
			b.WriteByte(':')
			enc(r, x[k], b)
		}
		ws()
		b.WriteByte('}')
	}
	ws()
}

func encStr(r *rand.Rand, s string, b *bytes.Buffer) {
	b.WriteByte('"')
	for _, ch := range s {
		switch {
		case ch == '"':
			b.WriteString(`\"`)
		case ch == '\\':
			b.WriteString(`\\`)
		case ch < 0x20:
			fmt.Fprintf(b, `\u%04x`, ch)
		case r.IntN(5) == 0: // random \u escape (surrogate pair for astral runes)
			if ch > 0xFFFF {
				c := ch - 0x10000
				fmt.Fprintf(b, `\u%04X\u%04x`, 0xD800+(c>>10), 0xDC00+(c&0x3FF))
			} else {
				fmt.Fprintf(b, `\u%04x`, ch)
			}
		default:
			b.WriteRune(ch)
		}
	}
	b.WriteByte('"')
}

func randAnyScalar(r *rand.Rand) any {
	switch r.IntN(6) {
	case 0:
		return nil
	case 1:
		return r.IntN(2) == 0
	case 2:
		return r.IntN(1000) - 10
	case 3:
		return r.Float64() * 100
	case 4:
		return []any{randString(r), 1}
	default:
		return randString(r)
	}
}

func maybeWrong(r *rand.Rand, good any) any {
	if r.IntN(12) == 0 {
		return randAnyScalar(r)
	}
	return good
}

func randChunk(r *rand.Rand) map[string]any {
	top := map[string]any{"id": "chatcmpl-" + randString(r), "object": "chat.completion.chunk", "created": 1}
	if r.IntN(10) > 0 {
		var choices []any
		for n := r.IntN(3); n >= 0; n-- {
			ch := map[string]any{"logprobs": nil}
			if r.IntN(6) > 0 {
				ch["index"] = maybeWrong(r, r.IntN(3))
			}
			d := map[string]any{}
			switch r.IntN(4) {
			case 0:
				d["content"] = nil
			case 1, 2:
				d["content"] = maybeWrong(r, randString(r))
			}
			if r.IntN(3) == 0 {
				d["role"] = "assistant"
			}
			if r.IntN(4) == 0 {
				var tcs []any
				for k := r.IntN(3); k >= 0; k-- {
					tc := map[string]any{"index": maybeWrong(r, k), "type": "function"}
					if r.IntN(2) == 0 {
						tc["id"] = maybeWrong(r, "call_"+randString(r))
					}
					if r.IntN(5) > 0 {
						f := map[string]any{}
						if r.IntN(2) == 0 {
							f["name"] = maybeWrong(r, randString(r))
						}
						if r.IntN(4) > 0 {
							f["arguments"] = maybeWrong(r, randString(r))
						}
						tc["function"] = maybeWrong(r, f)
					}
					tcs = append(tcs, tc)
				}
				d["tool_calls"] = maybeWrong(r, tcs)
			}
			key := "delta"
			if r.IntN(4) == 0 {
				key = "message"
			}
			ch[key] = maybeWrong(r, d)
			switch r.IntN(3) {
			case 0:
				ch["finish_reason"] = nil
			case 1:
				ch["finish_reason"] = maybeWrong(r, []string{"stop", "length", "tool_calls"}[r.IntN(3)])
			}
			choices = append(choices, ch)
		}
		top["choices"] = maybeWrong(r, choices)
	}
	if r.IntN(3) == 0 {
		top["usage"] = maybeWrong(r, map[string]any{"total_tokens": 3})
	}
	if r.IntN(8) == 0 {
		top["error"] = maybeWrong(r, map[string]any{"message": randString(r)})
	}
	if r.IntN(3) == 0 {
		top["extra"] = map[string]any{"nested": []any{1, "x", nil, map[string]any{"deep": true}}}
	}
	return top
}

func TestParseChunkDifferential(t *testing.T) {
	r := rand.New(rand.NewPCG(1, 2))
	var b bytes.Buffer
	accepted := 0
	for i := 0; i < 200000; i++ {
		b.Reset()
		enc(r, randChunk(r), &b)
		in := b.Bytes()
		if r.IntN(20) == 0 && len(in) > 2 { // corrupt: truncate, flip or inject bytes
			switch r.IntN(3) {
			case 0:
				in = in[:r.IntN(len(in))]
			case 1:
				in = append([]byte(nil), in...)
				in[r.IntN(len(in))] = byte(r.IntN(256))
			case 2:
				p := r.IntN(len(in))
				in = append(append(append([]byte(nil), in[:p]...), "\xff\xfe"...), in[p:]...)
			}
		}
		if msg := compare(in); msg != "" {
			t.Fatalf("iteration %d: %s", i, msg)
		}
		var c Chunk
		if ParseChunk(in, &c) == nil {
			accepted++
		}
	}
	if accepted < 100000 {
		t.Fatalf("too few valid inputs exercised: %d", accepted)
	}
	t.Logf("200000 random chunks, %d accepted, all agree with encoding/json reference", accepted)
}

func TestParseChunkInvalidUTF8(t *testing.T) {
	in := []byte("{\"choices\":[{\"delta\":{\"content\":\"a\xffb\xed\xa0\x80c\"}}]}")
	if msg := compare(in); msg != "" {
		t.Fatal(msg)
	}
	var c Chunk
	if err := ParseChunk(in, &c); err != nil || !utf8.Valid(c.Content) {
		t.Fatalf("err=%v content=%q", err, c.Content)
	}
}

func FuzzParseChunk(f *testing.F) {
	f.Add([]byte(`{"choices":[{"index":0,"delta":{"content":" the"},"finish_reason":null}]}`))
	f.Add([]byte(`{"choices":[{"index":0,"delta":{"tool_calls":[{"index":0,"id":"c","function":{"name":"n","arguments":"{\"a"}}]}}],"usage":null}`))
	f.Add([]byte(`{"choices":[{"index":0,"message":{"content":"😀\ud800x"},"finish_reason":"stop"}],"usage":{"a":1}}`))
	f.Add([]byte(`{"error":{"message":"x"}}`))
	f.Fuzz(func(t *testing.T, b []byte) {
		if msg := compare(b); msg != "" {
			t.Fatal(msg)
		}
	})
}

func BenchmarkParseChunk(b *testing.B) {
	in := []byte(`{"id":"chatcmpl-6f1c2d7e-1234-4abc-9def-0123456789ab","object":"chat.completion.chunk","created":1790000000,"model":"rv-synth-1","service_tier":"default","system_fingerprint":"fp_rvsynth01","choices":[{"index":0,"delta":{"content":" there"},"logprobs":null,"finish_reason":null}],"usage":null}`)
	var c Chunk
	b.SetBytes(int64(len(in)))
	b.ReportAllocs()
	for i := 0; i < b.N; i++ {
		if err := ParseChunk(in, &c); err != nil {
			b.Fatal(err)
		}
	}
}

func BenchmarkEncodingJSONChunk(b *testing.B) {
	in := []byte(`{"id":"chatcmpl-6f1c2d7e-1234-4abc-9def-0123456789ab","object":"chat.completion.chunk","created":1790000000,"model":"rv-synth-1","service_tier":"default","system_fingerprint":"fp_rvsynth01","choices":[{"index":0,"delta":{"content":" there"},"logprobs":null,"finish_reason":null}],"usage":null}`)
	type delta struct {
		Content *string `json:"content"`
	}
	type choice struct {
		Index        int     `json:"index"`
		Delta        delta   `json:"delta"`
		FinishReason *string `json:"finish_reason"`
	}
	type chunk struct {
		Choices []choice        `json:"choices"`
		Usage   json.RawMessage `json:"usage"`
	}
	b.SetBytes(int64(len(in)))
	b.ReportAllocs()
	for i := 0; i < b.N; i++ {
		var c chunk
		if err := json.Unmarshal(in, &c); err != nil {
			b.Fatal(err)
		}
	}
}
