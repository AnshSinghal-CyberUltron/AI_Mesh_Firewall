# `redact_all` costs ~5.36 ms per 4 KB call — and a harness worry that proved unfounded

**Tag `[M]`** · offline bench, 4,096-char prompts, cache cleared, n=60

## The false alarm, checked rather than assumed

The post-fix profile showed transport decoding at ~17% of on-CPU time
(`_iter_transport_decodes` 10.26% + `a85decode` 2.92% + `_decode_one` 2.03% +
`_iter_short_b64_infra` 1.67%). Before optimising it I checked whether my own harness was
producing it: `make_prompt` injects `uuid.uuid4().hex[:12]`, and a 12-char hex token is
exactly what `_B64ISH_RE` matches.

It does — **54 matches per prompt**, once per repetition — and in isolation that makes
`_iter_transport_decodes` **73% dearer**: 0.585 ms vs 0.338 ms for plain prose. Both yield
**zero** decodes.

But at the level that matters it vanishes:

| prompt shape | b64ish tokens | `redact_all` |
|---|---:|---:|
| harness (uuid hex nonce) | 54 | **5.357 ms** |
| plain prose (no nonce) | 0 | **5.363 ms** |
| nonce split by dashes | 0 | **5.367 ms** |

0.25 ms inside a 5.36 ms call — under 5%, within noise. **The session's latency numbers are
not materially inflated by the harness.** The concern was worth raising and is now closed;
recording it so nobody re-derives the alarm from the profile alone.

## The floor this establishes

`redact_all` is **~5.36 ms per 4 KB call, independent of content shape**. Plain prose costs
the same as text full of base64-looking tokens, because the cost is the pattern sweep
itself, not the decoding it triggers.

That sets the arithmetic for what is left:

- ~2–3 genuine `redact_all` calls per request after tasks 8/8b removed the duplicate and
  the discarded ones ⇒ **~11–16 ms**
- measured CPU per request: **~27.3 ms**
- traced firewall tax: **7.5 ms**

So redaction remains roughly half the per-request CPU, and it is now **irreducible without
changing either what is redacted or how**.

## What this justifies

The plan's **task 7 (multi-pattern / Hyperscan engine)** targets exactly this: the same
detection at lower cost per byte. This session began assuming the policy engine was the hot
path; it is not (0.20 ms at 45 rules). `redact_all` at ~5.36 ms a call is, and that is now
measured rather than assumed.

The alternative — redacting less — is a detection-safety decision and belongs in front of
the corpus equivalence gate, not inside a performance change.
