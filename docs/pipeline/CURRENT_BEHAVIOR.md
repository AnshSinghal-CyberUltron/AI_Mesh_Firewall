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

## B-POL (live — iteration 2)

**Root cause (confirmed):** Redis had **no** `policies:compiled:*` bundles (`policy_count: 0` on
gateway `/health`). Policy engine evaluation is correct when bundles exist.

**Live repro after fix:**
```bash
docker compose exec control python manage.py seed_policy_package --org-slug zeroshield
# → policies:compiled:zeroshield pushed (45 policies)
curl /v1/policy/check prompt="Contact alice@corp.com ssn 123-45-6789"
# → action=redact, matched_rules=["Redact email addresses","Redact US SSN"]
```

**No policy_engine.py code change required** — compile+push is control-plane
(`seed_policy_package` / `compile_policies`). Gateway `POLICY_SYNC` picked up
bundle automatically (policy_count 45, version 1).

## B-ENF (iteration 2 — fixed in main.py)

`proxy_chat` input-scan block/redact path now calls `enforcement.resolve_enforcement()`:
- Tier-2 `recommended_action` preferred over `verdict.action` (B-ENF core fix)
- Org policy action from `check_resp` participates in precedence
- Honesty check uses `redaction_possible=False` → block via resolver

| Case | Contract | Status (iter 2) |
|------|----------|-----------------|
| 01 PII policy redact | `redact` | **GREEN** unit + live policy/check |
| 02 PHI redact | `redact` | pending live chat path |
| 03 Jailbreak block | `block` | pending live |
| 04 Injection block | `block` | pending live |
| 05 Secrets block | `block` | pending live |
| 06 Benign allow | `allow` | pending live |
| 07 Kill-switch reroute | `allow` | xfail (live gate) |
| 08 Sensitivity routing | `allow` | xfail (live gate) |
| 09 Output guard PII | `redact` | xfail (live gate) |

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
