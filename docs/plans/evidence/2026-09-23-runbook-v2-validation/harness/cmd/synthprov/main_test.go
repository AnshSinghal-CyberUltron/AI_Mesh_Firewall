package main

import (
	"bytes"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"testing"

	"rvharness/internal/rv"
)

func testServer(t testing.TB) *server {
	words, err := rv.LoadWords()
	if err != nil {
		t.Fatal(err)
	}
	cs, err := rv.LoadCanaries("")
	if err != nil {
		t.Fatal(err)
	}
	rec, err := rv.NewJSONL(filepath.Join(t.TempDir(), "r.jsonl"), 1<<16)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { rec.Close() })
	s := &server{cfg: config{ttft: 0, itl: 0, defTokens: 16, tokensCap: 4096, model: "m", sampleMod: 10,
		maxBody: 8 << 20, fingerprint: true}, words: words, canary: cs, rec: rec, created: 1}
	s.initPools()
	return s
}

func body(stream bool, maxTokens int) []byte {
	w, _ := rv.LoadWords()
	text := w.NonceText(1, 2, 3) + " " + strings.Repeat("The quick brown fox jumps over the lazy dog. ", 110)
	b, _ := json.Marshal(map[string]any{"model": "m", "stream": stream, "max_tokens": maxTokens,
		"messages": []any{map[string]any{"role": "system", "content": "You are a helpful assistant."},
			map[string]any{"role": "user", "content": text}}})
	return b
}

func TestStreamFramingAndRecord(t *testing.T) {
	s := testServer(t)
	req := httptest.NewRequest(http.MethodPost, "/v1/chat/completions", bytes.NewReader(body(true, 40)))
	req.Header.Set("x-request-id", "rid-abc")
	req.Header.Set("x-synth-inject", "split-aws")
	w := httptest.NewRecorder()
	s.chat(w, req)
	out := w.Body.String()
	if !strings.HasPrefix(out, "data: {") || !strings.HasSuffix(out, "data: [DONE]\n\n") {
		t.Fatalf("framing: %q ... %q", out[:40], out[len(out)-40:])
	}
	var content strings.Builder
	var ch rv.Chunk
	events := strings.Split(strings.TrimSuffix(out, "\n\n"), "\n\n")
	for _, ev := range events[:len(events)-1] {
		if err := rv.ParseChunk([]byte(strings.TrimPrefix(ev, "data: ")), &ch); err != nil {
			t.Fatalf("chunk %q: %v", ev, err)
		}
		content.Write(ch.Content)
	}
	if !strings.Contains(content.String(), "AKIARVOUTPUT4417ZZ9Q") {
		t.Fatalf("split-aws canary not reassembled: %q", content.String())
	}
	if w.Header().Get("x-request-id") != "rid-abc" {
		t.Fatal("x-request-id not echoed")
	}
}

func TestDeterministicAcrossStreamAndJSON(t *testing.T) {
	s := testServer(t)
	get := func(stream bool) string {
		req := httptest.NewRequest(http.MethodPost, "/v1/chat/completions", bytes.NewReader(body(stream, 30)))
		req.Header.Set("x-request-id", "same-rid")
		w := httptest.NewRecorder()
		s.chat(w, req)
		if !stream {
			var r struct {
				Choices []struct {
					Message struct{ Content string } `json:"message"`
				} `json:"choices"`
			}
			json.Unmarshal(w.Body.Bytes(), &r)
			return r.Choices[0].Message.Content
		}
		var sb strings.Builder
		var ch rv.Chunk
		for _, ev := range strings.Split(w.Body.String(), "\n\n") {
			ev = strings.TrimPrefix(ev, "data: ")
			if ev == "" || ev == "[DONE]" {
				continue
			}
			rv.ParseChunk([]byte(ev), &ch)
			sb.Write(ch.Content)
		}
		return sb.String()
	}
	a, b := get(true), get(false)
	if a != b || len(a) < 30 {
		t.Fatalf("stream %q != json %q", a, b)
	}
}

type discardWriter struct{ h http.Header }

func (d *discardWriter) Header() http.Header         { return d.h }
func (d *discardWriter) Write(p []byte) (int, error) { return len(p), nil }
func (d *discardWriter) WriteHeader(int)             {}
func (d *discardWriter) Flush()                      {}

// BenchmarkChat* report allocations per request (the GC-pressure driver on provider VMs).
// httptest.NewRequest itself allocates ~10 objects; the recorder is replaced by a discard writer.
func benchChat(b *testing.B, stream bool) {
	s := testServer(b)
	bd := body(stream, 400)
	b.ReportAllocs()
	b.ResetTimer()
	for i := 0; i < b.N; i++ {
		req := httptest.NewRequest(http.MethodPost, "/v1/chat/completions", bytes.NewReader(bd))
		req.Header.Set("x-request-id", "rid-"+strconv.Itoa(i))
		s.chat(&discardWriter{h: http.Header{}}, req)
	}
}

func BenchmarkChatStream400(b *testing.B) { benchChat(b, true) }
func BenchmarkChatJSON400(b *testing.B)   { benchChat(b, false) }

func TestMain(m *testing.M) { os.Exit(m.Run()) }
