package rv

// ChatReq holds what synthprov needs from an OpenAI chat request, extracted without allocation
// (buffers are reused across calls). Semantics match encoding/json decoding of the same fields
// (differential test in chatreq_test.go).
type ChatReq struct {
	Stream              bool
	MaxTokens           int // -1 when absent or null
	MaxCompletionTokens int // -1 when absent or null
	IncludeUsage        bool
	NumMessages         int
	NumTools            int
	ToolName            []byte // function.name of tools[0] ("" when absent)
	// Text = every message's text content, each followed by '\n': string contents verbatim,
	// array contents as the "text" of each part (parts without text contribute "\n").
	Text []byte

	scratch []byte
	key     []byte
}

func (r *ChatReq) reset() {
	r.Stream, r.IncludeUsage = false, false
	r.MaxTokens, r.MaxCompletionTokens = -1, -1
	r.NumMessages, r.NumTools = 0, 0
	r.ToolName = r.ToolName[:0]
	r.Text = r.Text[:0]
}

// ParseChatRequest parses body (a JSON object) into r. Unknown fields are validated and skipped.
func ParseChatRequest(body []byte, r *ChatReq) error {
	r.reset()
	s := jscan{b: body}
	var c Chunk // key scratch
	c.keyBuf = r.key[:0]
	defer func() { r.key = c.keyBuf }()
	s.ws()
	if !s.eat('{') {
		return ErrJSON
	}
	s.ws()
	if !s.eat('}') {
		for {
			key, ok := s.key(&c)
			if !ok {
				return ErrJSON
			}
			switch string(key) {
			case "stream":
				v, ok := s.boolOrNull()
				if !ok {
					return ErrJSON
				}
				r.Stream = v
			case "max_tokens":
				v, ok := s.intOrNull()
				if !ok {
					return ErrJSON
				}
				r.MaxTokens = v
			case "max_completion_tokens":
				v, ok := s.intOrNull()
				if !ok {
					return ErrJSON
				}
				r.MaxCompletionTokens = v
			case "stream_options":
				if !s.streamOptions(r, &c) {
					return ErrJSON
				}
			case "messages":
				if !s.messages(r, &c) {
					return ErrJSON
				}
			case "tools":
				if !s.tools(r, &c) {
					return ErrJSON
				}
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

func (s *jscan) boolOrNull() (bool, bool) {
	switch {
	case s.lit("true"):
		return true, true
	case s.lit("false"), s.lit("null"):
		return false, true
	}
	return false, false
}

// intOrNull: null -> -1; non-negative integer literal -> value.
func (s *jscan) intOrNull() (int, bool) {
	if s.lit("null") {
		return -1, true
	}
	return s.intLit()
}

func (s *jscan) streamOptions(r *ChatReq, c *Chunk) bool {
	if s.lit("null") {
		return true
	}
	if !s.eat('{') {
		return false
	}
	s.ws()
	if s.eat('}') {
		return true
	}
	for {
		key, ok := c.keyFrom(s)
		if !ok {
			return false
		}
		if string(key) == "include_usage" {
			v, ok := s.boolOrNull()
			if !ok {
				return false
			}
			r.IncludeUsage = v
		} else if !s.skip(0) {
			return false
		}
		s.ws()
		if s.eat(',') {
			s.ws()
			continue
		}
		return s.eat('}')
	}
}

func (c *Chunk) keyFrom(s *jscan) ([]byte, bool) { return s.key(c) }

func (s *jscan) messages(r *ChatReq, c *Chunk) bool {
	if s.lit("null") {
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
		if !s.message(r, c) {
			return false
		}
		r.NumMessages++
		s.ws()
		if s.eat(',') {
			s.ws()
			continue
		}
		return s.eat(']')
	}
}

// message appends this message's text (last "content" key wins) and a trailing '\n'.
func (s *jscan) message(r *ChatReq, c *Chunk) bool {
	if !s.eat('{') {
		return false
	}
	start := len(r.Text)
	s.ws()
	if !s.eat('}') {
		for {
			key, ok := s.key(c)
			if !ok {
				return false
			}
			if string(key) == "content" {
				r.Text = r.Text[:start]
				switch {
				case s.lit("null"):
				case s.i < len(s.b) && s.b[s.i] == '"':
					var ok bool
					if r.Text, ok = s.str(r.Text); !ok {
						return false
					}
				case s.i < len(s.b) && s.b[s.i] == '[':
					if !s.contentParts(r, c) {
						return false
					}
				default:
					return false
				}
			} else if !s.skip(0) {
				return false
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
	r.Text = append(r.Text, '\n')
	return true
}

// contentParts appends the "text" of each part, each followed by '\n' (except the last, whose
// '\n' is added by message()).
func (s *jscan) contentParts(r *ChatReq, c *Chunk) bool {
	s.i++ // '['
	s.ws()
	if s.eat(']') {
		return true
	}
	first := true
	for {
		if !first {
			r.Text = append(r.Text, '\n')
		}
		first = false
		if !s.eat('{') {
			return false
		}
		partStart := len(r.Text)
		s.ws()
		if !s.eat('}') {
			for {
				key, ok := s.key(c)
				if !ok {
					return false
				}
				if string(key) == "text" {
					r.Text = r.Text[:partStart]
					if s.lit("null") {
					} else if s.i < len(s.b) && s.b[s.i] == '"' {
						var ok bool
						if r.Text, ok = s.str(r.Text); !ok {
							return false
						}
					} else {
						return false
					}
				} else if !s.skip(0) {
					return false
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
		s.ws()
		if s.eat(',') {
			s.ws()
			continue
		}
		return s.eat(']')
	}
}

func (s *jscan) tools(r *ChatReq, c *Chunk) bool {
	if s.lit("null") {
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
		first := r.NumTools == 0
		if !s.eat('{') {
			return false
		}
		s.ws()
		if !s.eat('}') {
			for {
				key, ok := s.key(c)
				if !ok {
					return false
				}
				if string(key) == "function" && !s.isNull() {
					if !s.toolFunction(r, c, first) {
						return false
					}
				} else if !s.skip(0) {
					return false
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
		r.NumTools++
		s.ws()
		if s.eat(',') {
			s.ws()
			continue
		}
		return s.eat(']')
	}
}

func (s *jscan) toolFunction(r *ChatReq, c *Chunk, first bool) bool {
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
		if string(key) == "name" {
			if s.lit("null") {
				if first {
					r.ToolName = r.ToolName[:0]
				}
			} else if s.i < len(s.b) && s.b[s.i] == '"' {
				var v []byte
				var ok bool
				if first {
					v, ok = s.str(r.ToolName[:0])
					r.ToolName = v
				} else {
					r.scratchStr(s, &ok)
				}
				if !ok {
					return false
				}
			} else {
				return false
			}
		} else if !s.skip(0) {
			return false
		}
		s.ws()
		if s.eat(',') {
			s.ws()
			continue
		}
		return s.eat('}')
	}
}

func (r *ChatReq) scratchStr(s *jscan, ok *bool) {
	r.scratch, *ok = s.str(r.scratch[:0])
}
