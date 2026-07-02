# Current Behavior — Chat Pipeline (characterization)

Captured by Cursor chat-pipeline FREEZE session, iteration 1.  
Environment: unit characterization + policy_engine probes (live docker gate pending).

## Summary

| Case | Contract | Unit probe | Status |
|------|----------|------------|--------|
| 01 PII policy redact | `redact` | policy_engine matches + redacts | **GREEN** (unit) |
| 02 PHI redact | `redact` | enforcement only | pending live |
| 03 Jailbreak block | `block` | enforcement only | pending live |
| 04 Injection block | `block` | enforcement only | pending live |
| 05 Secrets block | `block` | enforcement only | pending live |
| 06 Benign allow | `allow` | enforcement only | pending live |
| 07 Kill-switch reroute | `allow` | live gate | xfail |
| 08 Sensitivity routing | `allow` | live gate | xfail |
| 09 Output guard PII | `redact` | live gate | xfail |

## B-ENF (confirmed)

**Symptom:** Tier-2 `recommended_action=redact` can surface as HTTP 403 block in
`proxy_chat` when honesty check fires or score thresholds escalate.

**Root area:** `main.py` ~6160–6385 merges `verdict.action` directly; scanner maps
tier-2 `redact` → `flag` (scanner.py:1370) while block paths use `verdict.action == "block"`.

**Fix direction:** `enforcement.resolve_enforcement()` created; main.py swap pending
(claim `main.py:3500-6600` before edit).

## B-POL (confirmed at unit layer)

**Symptom (reported):** `matched_rules: []` for SSN/CC/email/phone in live traces.

**Unit probe:** Compiled `PKG2_PIPE_PII` bundle shape matches when `condition.regex`
is present — `evaluate()` returns matched rules for all four identifier types.

**Hypothesis for live miss:** bundle not compiled/pushed to `POLICY_SYNC`, wrong
`policy_domain` filter, or org slug mismatch — needs live Redis verification.

## Code anchors

| Area | Location |
|------|----------|
| Policy-before-scan | main.py:5725–5890 |
| Input scan enforcement | main.py:5974–6385 |
| Honesty check (input) | main.py:6315–6385 |
| Honesty check (output) | main.py:1566 |
| Tier-2 redact mapping | scanner.py:1370–1379 |
| Policy cache path | main.py:1002–1085, policy_sync.py |

## Next characterization

1. Run live stack: `scripts/start_dev_stack.sh` + `scripts/openai_sdk_live_gateway.py`
2. Capture real `stages[]` from telemetry for cases 2–9
3. Compare against `gateway/tests/golden/snapshots/`
