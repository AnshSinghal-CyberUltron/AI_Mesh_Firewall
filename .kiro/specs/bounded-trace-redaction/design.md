# Design — redact a window that ends at whitespace

## The change

```python
def _truncate(text, limit=1200):
    raw = (text or "").strip()
    raw = _redact_all(_redaction_window(raw, limit))   # window, not the whole text
    return raw if len(raw) <= limit else raw[:limit] + "…"
```

`_redaction_window(text, limit)` returns a prefix long enough that redacting it gives the
same first `limit` characters as redacting everything.

## Why a whitespace boundary makes this sound

The worry is a span that starts before `limit` and ends beyond the window: the window
redaction would not match it, and its visible head would survive into the trace.

Auditing every redaction pattern for unbounded length (`+`, `*`, `{n,}`) splits them cleanly:

**Unbounded patterns match a whitespace-free run.** `\bxox[baprs]-[0-9A-Za-z-]{10,}\b`,
`\bglpat-[A-Za-z0-9_-]{20,}\b`, `(?:mongodb|postgres|…)://[^\s]{10,}`,
`https://hooks\.slack\.com/services/[A-Za-z0-9/_+-]+` — every one is a contiguous run
containing no whitespace. **Such a run cannot straddle a cut placed at whitespace.**

**Patterns containing internal whitespace are short and bounded.** `\d{3}[-\s]\d{2}[-\s]\d{4}`
(SSN, ~11), phone (~14), card (~19), `-----BEGIN PRIVATE KEY-----` (~30 — the pattern matches
the *header*, not the key body). All fit inside a modest fixed overlap.

**Patterns shaped `LABEL<ws>SECRET`** — `Bearer\s+[A-Za-z0-9_\-\.]{20,}`,
`(?:password|passwd|pwd)\s*[:=]\s*…` — are the interesting case. If the cut falls between the
label and the token, the window match fails; but what remains visible is the *label*
("Bearer", "password:"), not the secret. The token is a whitespace-free run, so it lies
wholly on one side: inside the window it is matched and masked; beyond it, it is discarded
along with the rest.

So: take `limit + OVERLAP`, then **advance to the next whitespace character**.

```python
OVERLAP  = 256     # covers every whitespace-containing pattern (longest ~30 chars)
HARD_CAP = 4096    # never scan further than this looking for whitespace
```

## The residual risk, stated (R4)

If the text contains a **single whitespace-free run longer than `HARD_CAP` (4096) characters**
spanning the cut, the window ends mid-run and a pattern matching that run is missed. The
visible head of such a run would reach the trace.

This is the one input shape that differs, it is bounded and named, and `HARD_CAP` is the knob.
Note the run must *span* the cut — a 5000-character token wholly before `limit` is inside the
window and masked normally.

## Why not the alternatives

**Truncate first, then redact** — unsound, and the reason the current order exists.

**A generous fixed overlap with no whitespace rule** — to be safe against token-shaped
patterns the overlap would have to exceed the longest possible token, which is unbounded.
The whitespace rule replaces an unbounded requirement with a bounded one.

**Skip trace redaction entirely** — the trace is returned to the operator; it must not carry
raw PII.

## Verification

R1 is an equivalence property, so it gets an equivalence gate rather than examples: over the
detection corpus plus adversarial shapes (secrets straddling the cut at every offset, long
runs, unicode, empty), assert `new(text, limit) == old(text, limit)` byte for byte.
