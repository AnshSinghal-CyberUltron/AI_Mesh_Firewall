package rv

import (
	"errors"
	"unicode/utf16"
	"unicode/utf8"
)

// ToolFrag is one tool_calls[] element of a choice-0 delta/message.
type ToolFrag struct {
	Index   int
	ID      []byte
	Name    []byte
	Args    []byte
	HasArgs bool
}

// Chunk holds the fields the harness needs from one OpenAI chat.completion or
// chat.completion.chunk object. Only choice index 0 is extracted (the harness sends n=1).
type Chunk struct {
	Content        []byte // decoded choices[i].delta.content / message.content of index 0
	ContentPresent bool   // content key present with a string value (possibly "")
	Finish         []byte // finish_reason of index 0 when a string
	Tools          []ToolFrag
	Usage          bool // top-level "usage" present and not null
	Error          bool // top-level "error" present and not null
	Choices        int  // number of choices[] elements

	// scratch for the choice being parsed (index may follow delta in key order)
	sContent  []byte
	sPresent  bool
	sFinish   []byte
	sTools    []ToolFrag
	keyBuf    []byte
	strBuf    []byte
	toolCount int
}

// ErrJSON is returned for any input that is not a single valid JSON object of the expected
// shape. The parser is strict: it accepts exactly what encoding/json's scanner accepts
// (verified by the differential fuzz test in jsonchunk_test.go) and rejects wrong types for the
// extracted fields.
var ErrJSON = errors.New("rv: malformed chunk JSON")

const maxDepth = 10000

type jscan struct {
	b []byte
	i int
}

func (c *Chunk) reset() {
	c.Content = c.Content[:0]
	c.ContentPresent = false
	c.Finish = c.Finish[:0]
	c.Tools = c.Tools[:0]
	c.Usage, c.Error = false, false
	c.Choices = 0
}

// ParseChunk parses b into c, reusing c's buffers.
func ParseChunk(b []byte, c *Chunk) error {
	c.reset()
	s := jscan{b: b}
	s.ws()
	if !s.eat('{') {
		return ErrJSON
	}
	s.ws()
	if !s.eat('}') {
		for {
			key, ok := s.key(c)
			if !ok {
				return ErrJSON
			}
			switch string(key) {
			case "choices":
				if !s.choices(c) {
					return ErrJSON
				}
			case "usage":
				null, ok := s.skipNullable()
				if !ok {
					return ErrJSON
				}
				c.Usage = !null
			case "error":
				null, ok := s.skipNullable()
				if !ok {
					return ErrJSON
				}
				c.Error = !null
			default:
				if !s.skip(0) {
					return ErrJSON
				}
			}
			s.ws()
			if s.eat(',') {
				s.ws()
				continue
			}
			if s.eat('}') {
				break
			}
			return ErrJSON
		}
	}
	s.ws()
	if s.i != len(s.b) {
		return ErrJSON
	}
	return nil
}

func (s *jscan) ws() {
	for s.i < len(s.b) {
		switch s.b[s.i] {
		case ' ', '\t', '\n', '\r':
			s.i++
		default:
			return
		}
	}
}

func (s *jscan) eat(ch byte) bool {
	if s.i < len(s.b) && s.b[s.i] == ch {
		s.i++
		return true
	}
	return false
}

// key parses `"name"` ws `:` ws and returns the decoded key.
func (s *jscan) key(c *Chunk) ([]byte, bool) {
	if s.i >= len(s.b) || s.b[s.i] != '"' {
		return nil, false
	}
	var ok bool
	c.keyBuf, ok = s.str(c.keyBuf[:0])
	if !ok {
		return nil, false
	}
	s.ws()
	if !s.eat(':') {
		return nil, false
	}
	s.ws()
	return c.keyBuf, true
}

func (s *jscan) isNull() bool {
	return s.i+4 <= len(s.b) && s.b[s.i] == 'n' && s.b[s.i+1] == 'u' && s.b[s.i+2] == 'l' && s.b[s.i+3] == 'l'
}

// skipNullable skips a value and reports whether it was the literal null.
func (s *jscan) skipNullable() (null bool, ok bool) {
	if s.isNull() {
		s.i += 4
		return true, true
	}
	return false, s.skip(0)
}

func (s *jscan) choices(c *Chunk) bool {
	if s.isNull() {
		s.i += 4
		return true
	}
	if !s.eat('[') {
		return false
	}
	s.ws()
	if s.eat(']') {
		return true
	}
	for {
		if !s.choice(c) {
			return false
		}
		c.Choices++
		s.ws()
		if s.eat(',') {
			s.ws()
			continue
		}
		return s.eat(']')
	}
}

func (s *jscan) choice(c *Chunk) bool {
	if !s.eat('{') {
		return false
	}
	c.sContent = c.sContent[:0]
	c.sPresent = false
	c.sFinish = c.sFinish[:0]
	c.sTools = c.sTools[:0]
	idx := 0
	s.ws()
	if !s.eat('}') {
		for {
			key, ok := s.key(c)
			if !ok {
				return false
			}
			switch string(key) {
			case "index":
				v, ok := s.intLit()
				if !ok {
					return false
				}
				idx = v
			case "delta", "message":
				if s.isNull() {
					s.i += 4
				} else if !s.delta(c) {
					return false
				}
			case "finish_reason":
				if s.isNull() {
					s.i += 4
					c.sFinish = c.sFinish[:0]
				} else {
					if s.i >= len(s.b) || s.b[s.i] != '"' {
						return false
					}
					var ok bool
					c.sFinish, ok = s.str(c.sFinish[:0])
					if !ok {
						return false
					}
				}
			default:
				if !s.skip(0) {
					return false
				}
			}
			s.ws()
			if s.eat(',') {
				s.ws()
				continue
			}
			if s.eat('}') {
				break
			}
			return false
		}
	}
	if idx == 0 {
		c.Content = append(c.Content[:0], c.sContent...)
		c.ContentPresent = c.sPresent
		c.Finish = append(c.Finish[:0], c.sFinish...)
		c.Tools = append(c.Tools[:0], c.sTools...)
	}
	return true
}

func (s *jscan) delta(c *Chunk) bool {
	if !s.eat('{') {
		return false
	}
	s.ws()
	if s.eat('}') {
		return true
	}
	for {
		key, ok := s.key(c)
		if !ok {
			return false
		}
		switch string(key) {
		case "content":
			if s.isNull() {
				s.i += 4
				c.sContent = c.sContent[:0]
				c.sPresent = false
			} else {
				if s.i >= len(s.b) || s.b[s.i] != '"' {
					return false
				}
				var ok bool
				c.sContent, ok = s.str(c.sContent[:0])
				if !ok {
					return false
				}
				c.sPresent = true
			}
		case "tool_calls":
			c.sTools = c.sTools[:0]
			if s.isNull() {
				s.i += 4
			} else if !s.toolCalls(c) {
				return false
			}
		default:
			if !s.skip(0) {
				return false
			}
		}
		s.ws()
		if s.eat(',') {
			s.ws()
			continue
		}
		return s.eat('}')
	}
}

func (s *jscan) toolCalls(c *Chunk) bool {
	if !s.eat('[') {
		return false
	}
	s.ws()
	if s.eat(']') {
		return true
	}
	for {
		var tf ToolFrag
		if !s.toolCall(c, &tf) {
			return false
		}
		c.sTools = append(c.sTools, tf)
		s.ws()
		if s.eat(',') {
			s.ws()
			continue
		}
		return s.eat(']')
	}
}

// strField parses a string-or-null value into a fresh slice (tool fragments are rare, so
// allocation here is fine).
func (s *jscan) strField(dst *[]byte, present *bool) bool {
	if s.isNull() {
		s.i += 4
		*dst = nil
		if present != nil {
			*present = false
		}
		return true
	}
	if s.i >= len(s.b) || s.b[s.i] != '"' {
		return false
	}
	v, ok := s.str(nil)
	if !ok {
		return false
	}
	*dst = v
	if present != nil {
		*present = true
	}
	return true
}

func (s *jscan) toolCall(c *Chunk, tf *ToolFrag) bool {
	if !s.eat('{') {
		return false
	}
	s.ws()
	if s.eat('}') {
		return true
	}
	for {
		key, ok := s.key(c)
		if !ok {
			return false
		}
		switch string(key) {
		case "index":
			v, ok := s.intLit()
			if !ok {
				return false
			}
			tf.Index = v
		case "id":
			if !s.strField(&tf.ID, nil) {
				return false
			}
		case "function":
			if s.isNull() {
				s.i += 4
			} else if !s.function(c, tf) {
				return false
			}
		default:
			if !s.skip(0) {
				return false
			}
		}
		s.ws()
		if s.eat(',') {
			s.ws()
			continue
		}
		return s.eat('}')
	}
}

func (s *jscan) function(c *Chunk, tf *ToolFrag) bool {
	if !s.eat('{') {
		return false
	}
	s.ws()
	if s.eat('}') {
		return true
	}
	for {
		key, ok := s.key(c)
		if !ok {
			return false
		}
		switch string(key) {
		case "name":
			if !s.strField(&tf.Name, nil) {
				return false
			}
		case "arguments":
			if !s.strField(&tf.Args, &tf.HasArgs) {
				return false
			}
		default:
			if !s.skip(0) {
				return false
			}
		}
		s.ws()
		if s.eat(',') {
			s.ws()
			continue
		}
		return s.eat('}')
	}
}

// intLit parses a non-negative JSON integer literal (no fraction/exponent), max 1e9.
func (s *jscan) intLit() (int, bool) {
	start := s.i
	if !s.number() {
		return 0, false
	}
	lit := s.b[start:s.i]
	v := 0
	for _, ch := range lit {
		if ch < '0' || ch > '9' {
			return 0, false
		}
		v = v*10 + int(ch-'0')
		if v > 1e9 {
			return 0, false
		}
	}
	return v, true
}

// number validates a JSON number per RFC 8259.
func (s *jscan) number() bool {
	b, i := s.b, s.i
	if i < len(b) && b[i] == '-' {
		i++
	}
	if i >= len(b) {
		return false
	}
	if b[i] == '0' {
		i++
	} else if b[i] >= '1' && b[i] <= '9' {
		for i < len(b) && b[i] >= '0' && b[i] <= '9' {
			i++
		}
	} else {
		return false
	}
	if i < len(b) && b[i] == '.' {
		i++
		d := i
		for i < len(b) && b[i] >= '0' && b[i] <= '9' {
			i++
		}
		if i == d {
			return false
		}
	}
	if i < len(b) && (b[i] == 'e' || b[i] == 'E') {
		i++
		if i < len(b) && (b[i] == '+' || b[i] == '-') {
			i++
		}
		d := i
		for i < len(b) && b[i] >= '0' && b[i] <= '9' {
			i++
		}
		if i == d {
			return false
		}
	}
	s.i = i
	return true
}

func (s *jscan) lit(word string) bool {
	if s.i+len(word) <= len(s.b) && string(s.b[s.i:s.i+len(word)]) == word {
		s.i += len(word)
		return true
	}
	return false
}

// skip validates and skips any JSON value.
func (s *jscan) skip(depth int) bool {
	if depth > maxDepth || s.i >= len(s.b) {
		return false
	}
	switch ch := s.b[s.i]; {
	case ch == '"':
		return s.skipStr()
	case ch == '{':
		s.i++
		s.ws()
		if s.eat('}') {
			return true
		}
		for {
			if s.i >= len(s.b) || s.b[s.i] != '"' || !s.skipStr() {
				return false
			}
			s.ws()
			if !s.eat(':') {
				return false
			}
			s.ws()
			if !s.skip(depth + 1) {
				return false
			}
			s.ws()
			if s.eat(',') {
				s.ws()
				continue
			}
			return s.eat('}')
		}
	case ch == '[':
		s.i++
		s.ws()
		if s.eat(']') {
			return true
		}
		for {
			if !s.skip(depth + 1) {
				return false
			}
			s.ws()
			if s.eat(',') {
				s.ws()
				continue
			}
			return s.eat(']')
		}
	case ch == 't':
		return s.lit("true")
	case ch == 'f':
		return s.lit("false")
	case ch == 'n':
		return s.lit("null")
	default:
		return s.number()
	}
}

// skipStr validates and skips a string without decoding it.
func (s *jscan) skipStr() bool {
	s.i++
	for s.i < len(s.b) {
		ch := s.b[s.i]
		switch {
		case ch == '"':
			s.i++
			return true
		case ch < 0x20:
			return false
		case ch == '\\':
			s.i++
			if s.i >= len(s.b) {
				return false
			}
			switch s.b[s.i] {
			case '"', '\\', '/', 'b', 'f', 'n', 'r', 't':
				s.i++
			case 'u':
				s.i++
				if _, ok := s.hex4(); !ok {
					return false
				}
			default:
				return false
			}
		default:
			s.i++
		}
	}
	return false
}

func (s *jscan) hex4() (rune, bool) {
	if s.i+4 > len(s.b) {
		return 0, false
	}
	var r rune
	for k := 0; k < 4; k++ {
		ch := s.b[s.i+k]
		switch {
		case ch >= '0' && ch <= '9':
			r = r<<4 | rune(ch-'0')
		case ch >= 'a' && ch <= 'f':
			r = r<<4 | rune(ch-'a'+10)
		case ch >= 'A' && ch <= 'F':
			r = r<<4 | rune(ch-'A'+10)
		default:
			return 0, false
		}
	}
	s.i += 4
	return r, true
}

// str decodes the JSON string at s.i (which is '"') appending to dst, with exactly
// encoding/json's semantics: invalid UTF-8 bytes become U+FFFD one byte at a time; an
// unpaired surrogate escape becomes U+FFFD.
func (s *jscan) str(dst []byte) ([]byte, bool) {
	s.i++
	for {
		start := s.i
		for s.i < len(s.b) {
			ch := s.b[s.i]
			if ch == '"' || ch == '\\' || ch < 0x20 || ch >= utf8.RuneSelf {
				break
			}
			s.i++
		}
		dst = append(dst, s.b[start:s.i]...)
		if s.i >= len(s.b) {
			return dst, false
		}
		ch := s.b[s.i]
		switch {
		case ch == '"':
			s.i++
			return dst, true
		case ch < 0x20:
			return dst, false
		case ch >= utf8.RuneSelf:
			r, size := utf8.DecodeRune(s.b[s.i:])
			s.i += size
			dst = utf8.AppendRune(dst, r)
		default: // backslash
			s.i++
			if s.i >= len(s.b) {
				return dst, false
			}
			e := s.b[s.i]
			s.i++
			switch e {
			case '"', '\\', '/':
				dst = append(dst, e)
			case 'b':
				dst = append(dst, '\b')
			case 'f':
				dst = append(dst, '\f')
			case 'n':
				dst = append(dst, '\n')
			case 'r':
				dst = append(dst, '\r')
			case 't':
				dst = append(dst, '\t')
			case 'u':
				r, ok := s.hex4()
				if !ok {
					return dst, false
				}
				if utf16.IsSurrogate(r) {
					save := s.i
					if s.i+6 <= len(s.b) && s.b[s.i] == '\\' && s.b[s.i+1] == 'u' {
						s.i += 2
						if r2, ok2 := s.hex4(); ok2 {
							if dec := utf16.DecodeRune(r, r2); dec != utf8.RuneError {
								dst = utf8.AppendRune(dst, dec)
								continue
							}
						}
					}
					s.i = save
					r = utf8.RuneError
				}
				dst = utf8.AppendRune(dst, r)
			default:
				return dst, false
			}
		}
	}
}
