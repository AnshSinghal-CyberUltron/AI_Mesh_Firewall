package rv

import (
	"bytes"
	"encoding/json"
	"fmt"
	"os"
	"unsafe"

	"rvharness/shared"
)

// Canary is one planted/injected sensitive string (shared/canaries.json).
type Canary struct {
	ID        string   `json:"id"`
	Class     string   `json:"class"`
	Kind      string   `json:"kind"`
	Value     string   `json:"value"`
	Fragments []string `json:"fragments"`
	Split     []string `json:"split,omitempty"`
}

// CanarySet matches canary values and fragments in byte buffers.
type CanarySet struct {
	List  []Canary
	pats  [][]byte
	owner []int
	byID  map[string]*Canary
}

// LoadCanaries parses canaries.json; path "" means the embedded copy.
func LoadCanaries(path string) (*CanarySet, error) {
	data := shared.CanariesJSON
	if path != "" {
		b, err := os.ReadFile(path)
		if err != nil {
			return nil, err
		}
		data = b
	}
	var doc struct {
		Canaries []Canary `json:"canaries"`
	}
	if err := json.Unmarshal(data, &doc); err != nil {
		return nil, err
	}
	cs := &CanarySet{List: doc.Canaries, byID: map[string]*Canary{}}
	for i := range cs.List {
		c := &cs.List[i]
		if c.ID == "" || c.Value == "" {
			return nil, fmt.Errorf("canary %d: empty id/value", i)
		}
		cs.byID[c.ID] = c
		for _, p := range append([]string{c.Value}, c.Fragments...) {
			if p == "" {
				continue
			}
			cs.pats = append(cs.pats, []byte(p))
			cs.owner = append(cs.owner, i)
		}
	}
	return cs, nil
}

// ByID returns the canary with the given id or nil.
func (cs *CanarySet) ByID(id string) *Canary { return cs.byID[id] }

// Hits returns the ids (in list order, deduplicated) of canaries whose value or any fragment
// occurs verbatim in any of bufs.
func (cs *CanarySet) Hits(bufs ...[]byte) []string {
	var hit []bool
	for pi, p := range cs.pats {
		oi := cs.owner[pi]
		if hit != nil && hit[oi] {
			continue
		}
		for _, b := range bufs {
			if bytes.Contains(b, p) {
				if hit == nil {
					hit = make([]bool, len(cs.List))
				}
				hit[oi] = true
				break
			}
		}
	}
	if hit == nil {
		return nil
	}
	var out []string
	for i, h := range hit {
		if h {
			out = append(out, cs.List[i].ID)
		}
	}
	return out
}

func unsafeString(b []byte) string {
	if len(b) == 0 {
		return ""
	}
	return unsafe.String(&b[0], len(b))
}
