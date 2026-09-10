# Task 7 feasibility — 7.4× available, and the prefilter does not leak

**Tag `[M]`** · offline bench + `scripts/detection/hyperscan_prefilter_gate.py`

## The three questions, answered in order of what could kill the task

**1. Is the cheap alternative viable?** No — refuted before reaching for a dependency.
A single combined `re` alternation over all 63 patterns measured **3.478 ms vs 3.300 ms**
on benign prose: *slower* than the scans it replaces. Python's `re` tries branches
sequentially with backtracking, so 63 branches ≈ 63 scans plus overhead
(`…-prefilter-refuted.md`).

**2. Does the prefilter ever miss what `re` catches?** **No — 0 under-reports across 762
inputs** (748 corpus + 14 adversarial: emails, cards, SSNs, long API keys, Bearer tokens,
AWS secrets, PEM headers, medical IDs, connection strings, zero-width obfuscation, Unicode
tag-block smuggling). Also **0 over-reports** — Hyperscan and `re` agree exactly on which
of the 56 patterns match, on every input tested.

This was the question most worth asking. An under-report is **not a slow path — it is
redaction silently not happening**, on one input, while every output diff on benign text
still looks perfect. The gate compares *matching pattern sets*, not redacted output,
precisely so it can see that.

**3. What is actually available?**

| | benign prose | with PII |
|---|---:|---:|
| today, 63 sequential `.sub()` | 3.394 ms | 3.251 ms |
| hybrid (Hyperscan prefilter + `re` substitution) | **0.456 ms** | **0.490 ms** |
| | **7.4×** | 6.6× |

Hyperscan alone scans in **0.005 ms** (788×). The hybrid is 0.456 ms because **7 of 63
patterns cannot go to Hyperscan** and run on `re` every call — they are ~100× the cost of
the other 56 combined, and they set the floor.

## The 7, and why they stay on `re`

All reject for the same reason — *zero-width assertions are not supported* — and all seven
are **false-positive suppressors**:

`email`, `government_id`, `password_assignment`, `secret_assignment`, `token_assignment`,
`api_key_assignment`, `exposed_password`

The `(?!forgotten|forgot|instructions?|…)` guards stop "password: forgotten" being flagged
as a leaked credential; `email`'s `(?!:[^\s/]+/)` stops URLs matching as addresses.
Translating a negative lookahead away makes the firewall flag **more** benign traffic. They
are not a porting backlog; they are a design boundary.

## What is still unknown

`redact_all` is roughly half of the ~27.3 ms CPU per request, so **7.4× on the matching
path is not 7.4× on the request**. The end-to-end effect on CPU/request and p99 is
unmeasured and is not estimated here.

Also open (R4): a native dependency is new to this codebase. Wheel availability is verified
for this platform (`hyperscan-0.8.2`, cp314, manylinux2014 x86_64); what happens on a
platform without one — fall back to pure `re`, or fail the build — is a decision, not a
detail.
