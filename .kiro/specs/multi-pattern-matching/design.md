# Task 7 — design

## Shape: a prefilter that only ever *removes* work

```python
_PREFILTER = _build()          # None when hyperscan is unavailable

def _candidate_keys(text) -> set[str] | None:
    """Pattern keys that COULD match, or None when no prefilter is available."""
```

In each family loop, one guard:

```python
if cand is not None and key not in cand and key not in _RE_ONLY_KEYS:
    continue                    # this pattern provably cannot match
```

Everything else — `_included()`, the per-type maskers, the substitution order, the
placeholder text — is untouched.

## Why this is safe by construction

- `cand is None` (no hyperscan) ⇒ **no guard fires, byte-identical to today.** The
  fallback is not a code path to test separately; it is the current code.
- `_RE_ONLY_KEYS` (the 7 lookaround patterns) are **never skipped**, whatever the
  prefilter says, because they are not in it.
- For the other 56, the guard skips only when Hyperscan reported no match — and
  `hyperscan_prefilter_gate.py` shows 0 under-reports over 762 inputs, so a skip means
  `.sub()` would have been a no-op.
- Over-reporting costs a redundant `.sub()`, which is correct but slower. It cannot change
  output.

The ordering of substitutions is preserved because the guard only ever `continue`s; it
never reorders or merges passes. That matters: a later pattern can match text an earlier
one rewrote, and any design that batched substitutions would change results.

## Failure handling

A prefilter exception returns `None` for that call, i.e. falls back to scanning everything.
The prefilter is an optimisation; it must never be able to fail a request or reduce
redaction.

## Dependency (R4)

`hyperscan` is imported inside a `try`. Absent ⇒ `_PREFILTER is None` ⇒ current behaviour.
So the wheel is a performance dependency, not a functional one, and a platform without one
degrades to today's speed rather than failing to start.

## Verification

1. `hyperscan_prefilter_gate.py` — 0 under-reports (already green).
2. Corpus equivalence: `redact_all` byte-identical, prefilter on vs off, over all 748
   corpus items plus the adversarial set.
3. A test that forcing `_PREFILTER = None` reproduces byte-identical output — pinning that
   the fallback is genuinely the same code.
4. A test that the 7 `_RE_ONLY_KEYS` still run when the prefilter reports nothing.
5. Full gateway suite.
6. E2E with `--repeat`: CPU/request and p99 vs the ~27.3 ms / 26–41 ms baseline.
