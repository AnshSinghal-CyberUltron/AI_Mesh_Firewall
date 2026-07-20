# REGRESSION FLAG (for the B1/E1 program owner) — chat fail-closed broken on main

**Reported by:** `claude-ralph-stress` · **Date:** 2026-07-02 · **Severity:** HIGH (potential egress leak)

## What
`gateway/tests/leakhunt/test_b1_attestation_byte_verify.py::test_b1_chat_endpoint_fails_closed_on_unmaskable_flagged_span`
**FAILS on main HEAD.** A Tier-2-`flag`ged span that `redact_all` + the digit backstop leave UNMASKABLE
(natural-language credential `correcthorsebatterystaple`) is **forwarded to the provider instead of
failing closed (block)**. `assert blocked` → `False`. This is a phantom-redaction / egress-leak path.

## Root cause (attributed)
Introduced by **`75b51d8e ralph(E1-openai-sdk-frontend-playwright): fix policy-pre-redact false block on
PII demo`**, which changed the unmaskable-span fail-closed comparison in `gateway/ai_mesh_gateway/main.py`
(compares the redaction result "against the [pre-policy] prompt so policy redaction that already masked
the text before Tier-2 flags it isn't double-counted"). That fix for a false-block on the PII demo
regressed the genuine fail-closed case where there was **no prior policy redaction** and the span is truly
unmaskable. The fail-closed logic + test were both added earlier in `05fea6d2`.

## Evidence it is NOT the chat-stress work
Reverting `claude-ralph-stress`'s only backend change (`patterns.py`, commit 28fa828a) via
`git stash` leaves this test **still failing** → the regression is independent of the chat-stress program.
The chat-stress golden gate (`pytest tests/golden`) and the 9 frozen cases are GREEN.

## Ask
The E1/B1 program owns `main.py`'s fail-closed logic and is actively editing it — please restore the
fail-closed-on-unmaskable-flagged-span behaviour without reintroducing the PII-demo false block (the two
were reconciled once and drifted apart). `claude-ralph-stress` is deliberately NOT editing `main.py` here
to avoid clobbering active E1/MCP work. Happy to help if you hand off the span.
