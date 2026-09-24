package rv

import (
	"regexp"
	"strings"
	"testing"
)

func TestNonceFormatAndUniqueness(t *testing.T) {
	w, err := LoadWords()
	if err != nil {
		t.Fatal(err)
	}
	re := regexp.MustCompile(`^\(ref-[a-z]+(-[a-z]+){5}\)$`)
	seen := map[string]bool{}
	for lg := 0; lg < 4; lg++ {
		for seq := uint32(0); seq < 70000; seq += 7 {
			n := w.NonceText(3, uint8(lg), seq)
			if !re.MatchString(n) {
				t.Fatalf("bad nonce %q", n)
			}
			if seen[n] {
				t.Fatalf("duplicate nonce %q", n)
			}
			seen[n] = true
			if got := w.FindNonce([]byte("hello " + n + " world")); got != n {
				t.Fatalf("FindNonce %q != %q", got, n)
			}
		}
	}
	// seq bytes are big-endian words 2..5
	n := w.NonceText(0, 0, 0x01020304)
	parts := strings.Split(strings.Trim(n, "()"), "-")
	if parts[3] != w.Nonce[1] || parts[4] != w.Nonce[2] || parts[5] != w.Nonce[3] || parts[6] != w.Nonce[4] {
		t.Fatalf("seq encoding wrong: %v", parts)
	}
}

func TestFindNonceRejectsNearMisses(t *testing.T) {
	w, _ := LoadWords()
	good := w.NonceText(1, 2, 3)
	for _, s := range []string{"(ref-)", "(ref-notaword-a-b-c-d-e)", strings.TrimSuffix(good, ")"), "(ref-" + w.Nonce[0] + ")"} {
		if got := w.FindNonce([]byte(s)); got != "" {
			t.Fatalf("FindNonce(%q) = %q", s, got)
		}
	}
	if got := w.FindNonce([]byte("(ref-x) (ref-y " + good)); got != good {
		t.Fatalf("second occurrence not found: %q", got)
	}
}

func TestRIDAndSampling(t *testing.T) {
	w, _ := LoadWords()
	re := regexp.MustCompile(`^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$`)
	sampled := 0
	const n = 100000
	seen := map[string]bool{}
	for seq := uint32(0); seq < n; seq++ {
		rid := RIDFromNonce(w.NonceText(9, 1, seq))
		if !re.MatchString(rid) || seen[rid] {
			t.Fatalf("bad/dup rid %q", rid)
		}
		seen[rid] = true
		if Sampled(rid, 10) {
			sampled++
		}
	}
	if sampled < n/10-600 || sampled > n/10+600 {
		t.Fatalf("sample fraction off: %d / %d", sampled, n)
	}
	// Known vector: keeps Go and analyze.py in lock-step (same value asserted in tests/test_analyze.py).
	if got := FNV1a64("abc"); got != 0xe71fa2190541574b {
		t.Fatalf("fnv1a64(abc) = %x", got)
	}
	if got := SplitMix64(0); got != 0xe220a8397b1dcdaf {
		t.Fatalf("splitmix64(0) = %x", got)
	}
}

func TestTokenDeterminism(t *testing.T) {
	w, _ := LoadWords()
	k := FNV1a64("some-rid")
	var a, b strings.Builder
	for j := 0; j < 400; j++ {
		a.WriteString(w.Token(k, j))
		b.WriteString(w.Token(k, j))
	}
	if a.String() != b.String() || a.Len() < 400*3 {
		t.Fatal("content not deterministic or too short")
	}
}

func TestCanaryHits(t *testing.T) {
	cs, err := LoadCanaries("")
	if err != nil {
		t.Fatal(err)
	}
	if got := cs.Hits([]byte("mail ana.rvcanary4417@example.org now")); len(got) != 1 || got[0] != "pii.email" {
		t.Fatalf("email: %v", got)
	}
	// fragment-only hit (value redacted around the distinctive core)
	if got := cs.Hits([]byte("key [REDACTED]RVCANARY4417XQ2Z")); len(got) != 1 || got[0] != "secret.aws" {
		t.Fatalf("fragment: %v", got)
	}
	// PEM value has newlines: only its fragments can match JSON-escaped raw bytes
	raw := []byte(`{"content":"-----BEGIN RSA PRIVATE KEY-----\nMIIEpA..."}`)
	if got := cs.Hits(raw); len(got) != 1 || got[0] != "secret.pem" {
		t.Fatalf("pem: %v", got)
	}
	if got := cs.Hits([]byte("nothing here"), nil); got != nil {
		t.Fatalf("false hit: %v", got)
	}
	c := cs.ByID("out.aws")
	if c == nil || strings.Join(c.Split, "") != c.Value {
		t.Fatal("out.aws split")
	}
}

func TestAppendJSONString(t *testing.T) {
	got := string(AppendJSONString(nil, "a\"b\\c\nd\x01é"))
	if got != `a\"b\\c\nd\u0001é` {
		t.Fatalf("%q", got)
	}
}
