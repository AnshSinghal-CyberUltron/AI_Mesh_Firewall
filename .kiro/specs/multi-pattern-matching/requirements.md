# Task 7 — multi-pattern matching for `redact_all`

## Why this, and why now

Measured, not assumed. `redact_all` is **31.74% of on-CPU time** after tasks 8/8b (py-spy),
and costs **~5.36 ms per 4 KB call regardless of content shape** — because it runs **63
full-text `.sub()` scans**, one per pattern, and the cost is the sweep itself rather than
what it finds.

This session opened assuming the policy engine was the hot path. It is not: 0.20 ms at 45
rules. `redact_all` is.

**The cheap alternative is already refuted.** A single combined `re` alternation over the
same 63 patterns measured **3.478 ms vs 3.300 ms** on benign prose — *slower* than the
scans it replaces, because Python's `re` tries branches sequentially with backtracking
(`…-prefilter-refuted.md`). Single-pass multi-pattern matching is a capability `re` does
not have.

## Measured ceiling

| | benign prose | with PII |
|---|---:|---:|
| today, 63 sequential `.sub()` | 3.394 ms | 3.251 ms |
| **hybrid (Hyperscan prefilter + `re` substitution)** | **0.456 ms** | **0.490 ms** |
| | **7.4×** | 6.6× |

Hyperscan's own scan is **0.005 ms** (788× on its own). The hybrid is 0.456 ms because
**7 of 63 patterns cannot go to Hyperscan** and still run on `re` every call — they are the
floor, ~100× the cost of the 56 combined.

## The 7, and why they are not negotiable

All seven fail for the same reason: **zero-width assertions are not supported**.

| family | pattern | the lookaround does |
|---|---|---|
| PII | `email` | `(?!:[^\s/]+/)` — stops URLs matching as emails |
| PII | `government_id` | context lookahead |
| SECRET | `password_assignment`, `secret_assignment`, `token_assignment`, `api_key_assignment` | `(?!forgotten\|forgot\|instructions?\|…)` — stops "password: forgotten" being flagged as a leaked credential |
| CRED | `exposed_password` | same FP guard |

These are **false-positive suppressors**. Translating a negative lookahead into a
Hyperscan-compatible form changes what the firewall flags — in the direction of flagging
more, i.e. blocking benign traffic. They stay on `re`.

## Requirements

**R1 — byte-identical output over the corpus.** This is the whole risk and it is not
addressed by the benchmark above. Hyperscan reports matches with different semantics from
`re` (all-matches vs leftmost, end-offset only, its own UTF-8 handling), and the hybrid
runs `.sub()` only for patterns Hyperscan says matched. If Hyperscan misses a match `re`
would have made, **redaction silently stops happening** — a security regression that looks
like a speedup.

**R2 — the prefilter must never subtract.** A pattern must be substituted whenever `re`
alone would have substituted it. Over-reporting from Hyperscan is harmless (the `re` pass
decides); under-reporting is a leak. The equivalence gate must be built to catch
*under*-reporting specifically, not just diff the outputs on benign text.

**R3 — the 7 lookaround patterns keep running unconditionally.** No attempt to translate
them in this task.

**R4 — a new native dependency is acknowledged, not smuggled.** Gate 4 was framed as
"zero-new-dependency wins". This is not one of those. It needs its own decision: wheel
availability per platform (verified: `hyperscan-0.8.2` cp314 manylinux2014 x86_64), what
happens on a platform with no wheel, and whether the gateway falls back to pure `re`.

**R5 — measured end to end, with `--repeat`.** The 7.4× is a matching-path microbenchmark.
Its effect on CPU/request and p99 is a separate measurement.

## What this is worth, honestly

`redact_all` is roughly half of the ~27.3 ms CPU/request. A 7.4× on its matching path is
not a 7.4× on the request. The end-to-end number is unknown until measured, and I am not
going to quote one.
