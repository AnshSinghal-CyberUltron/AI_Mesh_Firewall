# Task 1E — design

## The rule

In `_release_with_lookahead_tail`, replace the flat floor:

```python
min_retain = STREAM_LOOKAHEAD_BYTES                     # 512, unconditional
```

with a content-derived minimum, capped at the same 512:

```python
tail_run   = <trailing contiguous non-whitespace run, in bytes>
min_retain = min(STREAM_LOOKAHEAD_BYTES,                # R4: never more than today
                 tail_run + STREAM_MIN_RETAIN_BYTES)    # R3/R5
```

`STREAM_MIN_RETAIN_BYTES = 64`, from R3 (widest bounded whitespace-crossing pattern is
52 chars).

The `_open_media_opener_start` branch is left exactly as-is and still applies
`max(min_retain, open_tail_bytes)` afterwards, so R7 holds by construction.

## Why this is correct

A pattern that would be split by the boundary is either:

- **contiguous** (no whitespace in the match) — then it lies wholly inside `tail_run`,
  which is retained in full up to the cap. Same as today for runs ≥512, better below.
- **whitespace-crossing and bounded** — widest is 52 chars, covered by the 64-byte term.
- **whitespace-crossing and unbounded** — not covered today either (512 < ∞). No change.

So the change cannot move any pattern from covered to uncovered. Retention only ever
shrinks in prose, where the trailing run is a word and no bounded pattern spans the
released region.

## Effect

Prose with ~6-byte tokens: retention 512 → ~70 bytes. First release moves from input
chunk 129 to ~12.

## Verification order (R2 is a gate, not a check afterwards)

1. Build `test_stream_retention_equivalence.py`: stream all 748 corpus items token-wise
   through `SecureStreamingResponse` and record (released text, actions, verdicts).
2. Snapshot that under TODAY's retention.
3. Apply the change.
4. Re-run; require byte-identical released text and identical action sequences.
5. Re-run the first-release ratchets — the two strict-xfails must flip to XPASS.
6. Re-run `test_stream_flush_per_token.py` — R6, the 1C CPU win must hold.
7. Re-measure E2E HEAD in Docker.
