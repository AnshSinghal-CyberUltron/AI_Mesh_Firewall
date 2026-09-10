# Task 7 — multi-pattern prefilter: 2.34× on `redact_all`, and three numbers that were not it

**Tag `[M]`** · offline bench, 4,096-char inputs, uncached body, n=40

## The measured result

| | prefilter OFF | prefilter ON | |
|---|---:|---:|---:|
| `_redact_all_raw` + `_redact_obfuscated`, 4 KB benign prose | 5.273 ms | **2.253 ms** | **2.34×** |

56 of 63 patterns go through one Hyperscan scan; 7 stay on `re`.

## Three larger numbers, and why none of them was the answer

| figure | what it actually measured | why it misleads |
|---:|---|---|
| **788×** | one Hyperscan scan vs 63 `re.sub` scans | ignores that 7 patterns cannot be ported |
| **7.4×** | the hybrid matching path | excludes `_redact_obfuscated` and transport decoding, which the prefilter does not touch |
| **3545×** | **an `lru_cache` hit** | my bench called `redact_all`, which task 8b memoises. The "0.001 ms" was not work. |

The 3545× is the one worth recording as a mistake. It is the third time this session a
result looked too good and turned out to be measurement error rather than code — after the
n=1 "28%" and the sum-of-medians attribution. **The pattern is reliable enough to use as a
rule: when a number is implausibly large, suspect the instrument first.**

The honest figure is the uncached body, because that is the work a request actually does.

## Design: the prefilter may only remove work

```python
if _cand is not None and key not in _cand and key not in _RE_ONLY_KEYS:
    continue
```

One guard per family loop; nothing else changes — not `_included`, not the maskers, not the
substitution order. Order matters: a later pattern can match text an earlier one rewrote, so
any design that batched or reordered substitutions would change results. This one only ever
`continue`s.

`_candidate_keys` returns `None` — meaning "scan everything" — when Hyperscan is absent
**or on any exception**. So:

- **the no-Hyperscan path is not a second implementation**; it is this code with the guards
  inert, which a test pins directly;
- **a broken prefilter cannot reduce redaction**. A test installs a prefilter that raises
  and asserts an email is still masked.

## The 7 that stay on `re`

`email`, `government_id`, `password_assignment`, `secret_assignment`, `token_assignment`,
`api_key_assignment`, `exposed_password` — all reject on *zero-width assertions are not
supported*, and all are **false-positive suppressors**. The
`(?!forgotten|forgot|instructions?|…)` guards stop "password: forgotten" being flagged as a
leaked credential. A test asserts they are absent from the prefilter set, so a future edit
cannot quietly add one and drop the guard.

## Safety

| check | result |
|---|---|
| R2 gate: prefilter under-reports? | **0 of 762 inputs** (748 corpus + 14 adversarial) |
| 748 corpus items, prefilter on vs off | byte-identical |
| 15 sensitive/adversarial inputs incl. `password: forgotten` | byte-identical |
| exploding prefilter still redacts | asserted |
| the 7 FP-suppressors never skipped | asserted |

## Not established here

The end-to-end effect. `redact_all` is roughly half of ~27.3 ms CPU/request, so 2.34× on it
is not 2.34× on a request — and the gateway image does not currently ship the wheel, so the
container measures the fallback. Adding the dependency is a decision (R4), not a detail.
