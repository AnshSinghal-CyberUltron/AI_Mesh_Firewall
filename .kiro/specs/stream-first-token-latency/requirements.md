# Task 1E — streaming first-token latency

## Problem (measured, not assumed)

`docs/perf/evidence/2026-09-09-1D-streaming-head-latency.md`: a streaming caller waits
**1393–3098 ms** for a first token. A 100-token answer streams nothing at all — it is
delivered whole at `[DONE]`. The gateway converts a stream into a batch response.

Cause: `_release_with_lookahead_tail` sets `min_retain = STREAM_LOOKAHEAD_BYTES` (512)
**unconditionally**, so the client is permanently ≥512 bytes (~86 token-sized chunks)
behind, whatever the flush cadence.

## What the 512-byte window is actually worth

Measured over all 166 pattern strings in `patterns.py` using the regex parser's
`getwidth()`:

- 31 patterns can match across whitespace.
- **25 of those 31 are UNBOUNDED** (`[\s\S]*?`, `{20,}`, `.*?`). No finite window
  covers them — and the current 512-byte window does not either.
- Of the 6 bounded ones, the widest that genuinely contains whitespace *inside the
  match* is `phone_intl` at **52 chars**. (`email`'s 601 comes from `\s` appearing only
  in a negative lookahead; the match itself has no whitespace, so a contiguous-run rule
  covers it in full.)

So 512 is not a safety guarantee. It is an arbitrary constant that over-covers the
bounded patterns by 10× and under-covers 25 unbounded ones. The real cross-boundary
protection is the `_secret_anchor` mechanism (E14), which carries state between flushes
and is unaffected by window size.

## Requirements

**R1 — first-token latency.** A streaming answer must reach the client within a bounded
prefix. Budget: first release at **≤32 input chunks** (today: 129).

**R2 — detection equivalence is the gate.** No corpus item may change verdict, action,
or released bytes. Proven against all 748 items of `tests/detection_corpus/`
(323 malicious, 425 benign) streamed token-wise, comparing old vs new retention
byte-for-byte. This gate is built and run BEFORE the behaviour change.

**R3 — never retain less than the widest bounded whitespace-crossing pattern.**
≥52 chars ⇒ floor of 64 bytes, evidence above.

**R4 — never retain MORE than today.** 512 stays a hard cap, so no input can be held
longer than it is now. The change is one-directional.

**R5 — contiguous runs stay whole.** A trailing non-whitespace run (API key, email,
URL, connection string) must be retained in full up to the cap, since a contiguous
token can only be judged complete.

**R6 — keep the 1C CPU win.** Guard passes must stay proportional to content volume,
not chunk count. The 31.9× reduction must not regress.

**R7 — the open-media holdback is untouched.** `MAX_OPEN_MEDIA_HOLDBACK` and the
`_open_media_opener_start` path keep their current semantics; retention may only be
raised by them, never lowered.

## Out of scope

Making the 25 unbounded patterns safe under streaming. That is a pre-existing gap this
task neither creates nor closes; it is recorded here so it is not mistaken for a
regression.
