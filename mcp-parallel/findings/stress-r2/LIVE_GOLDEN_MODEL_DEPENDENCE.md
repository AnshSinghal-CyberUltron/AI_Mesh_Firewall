# FINDING (for the chat-pipeline FREEZE session) — live-only golden cases 07/08/09 are model-pinned

**Reported by:** `claude-ralph-stress` · **Date:** 2026-07-02 · **Severity:** LOW (test fixture, NOT a leak)

## What
`GATEWAY_LIVE=1 pytest gateway/tests/golden` fails on the three `live_only` cases when the org is
connected to **free OpenRouter models** (the R5 requirement: `pricing.prompt == 0`):
- `07_benign_kill_switch_reroute`, `08_benign_sensitivity_routing` — routing/reroute drift.
- `09_output_guard_pii_redact` — expects `final=redact`, gets `final=flag`.

In-process (`GATEWAY_LIVE=0`) the suite is GREEN 3× (these cases `skip`).

## Root cause (not a defect, not a leak)
The snapshots were blessed against a specific (non-free) model. No single free model reproduces all three:
- `_detect_model` picks `active[0]` = `cohere/north-mini-code:free` (a **code** model) → 09 = `flag`.
- Pinning `SIM_MODEL=google/gemma-4-31b-it:free` → 09 = `redact` but 07/08 fail (route differently).

**09 is NOT a PII leak.** Verified across 5 free models: the models describe email formats abstractly,
emit **no literal PII** (`delivered_emails=[]`, `LEAK=False`) → `redact_all` has nothing to mask → the
output guard's honest `redact→flag` relabel fires. No PII is delivered to the client in any case.

Independent of chat-stress: reverting the only chat-stress backend change (`patterns.py`) does not affect
these cases (it is a no-op on plain text); the in-process golden is green.

## Ask (freeze session owns the bless config)
To make the live golden suite green with free models, either (a) pin a single general model for the live
run that satisfies 07/08/09, (b) re-bless 07/08 against the free-model routing (routing-only, no security
change), or (c) make 07/08/09 assert the CONTRACT (`final_action == contract_final`) with model-tolerant
stage matching. Do **not** re-bless 09 to `flag` blindly — keep a case that verifies real output-side
redaction (ideally an in-process case that injects known PII into the model output so it is deterministic).
`claude-ralph-stress` will not edit the bless config or weaken the frozen cases.
