# Current Behavior — Chat Pipeline (characterization)

Captured by Cursor chat-pipeline FREEZE session, **iteration 4** (2026-07-02).  
Environment: rebuilt gateway container from `amf-pipeline` worktree + live docker stack.

## Golden gate status (iteration 4)

| Gate | Result |
|------|--------|
| `GATEWAY_LIVE=1` (rebuilt container) | **23 passed**, 0 xfail — **3× green** (session cache) |
| `GATEWAY_LIVE=0` (offline CI) | **22 passed**, 7 skipped |

## Deploy (iteration 4)

```bash
cd /home/contact_cyberultron_com/amf-pipeline
ln -sf ../AI_Mesh_Firewall/.env .env   # compose env_file
docker compose build gateway
docker compose up -d gateway
# policy_count: 45 persisted in Redis — no re-seed required
```

Container confirms `_strip_for_redos_probe` present; `/health` reports `policy_count: 45`.

## Case matrix

| Case | Contract | Mode | Final action | Policy stage | Status |
|------|----------|------|--------------|--------------|--------|
| 01 PII policy redact | `redact` | unit | `redact` | redact | **GREEN** |
| 02 PHI redact | `redact` | unit `phi_policy` | `redact` | redact (unit) | **GREEN** |
| 03 Jailbreak block | `block` | live | `block` | block | **GREEN** |
| 04 Injection block | `block` | live | `block` | block | **GREEN** |
| 05 Secrets block | `block` | live | `block` | block | **GREEN** |
| 06 Benign allow | `allow` | live | `allow` | allow | **GREEN** |
| 07 Kill-switch reroute | `allow` | live | `allow` | allow | **GREEN** |
| 08 Sensitivity routing | `allow` | live | `allow` | allow | **GREEN** |
| 09 Output guard PII | `redact` | live | `redact` | allow | **GREEN** |

### Case 02 live chat (post ReDoS deploy)

Prompt: `Patient MRN 4421901 diagnosed with diabetes`

| Stage | Action | Notes |
|-------|--------|-------|
| policy | **redact** | MRN rule matches; `prompt_out` has `[REDACTED_MRN]` |
| input_scan | flag | Tier-2 PHI advisory on already-redacted placeholder |
| route / output_guard | allow | LLM receives redacted prompt |
| **final_action** | **flag** | Tier-2 advisory wins trace label; bytes were redacted at policy |

Golden case 02 uses in-process `phi_policy` (contract `final_action=redact`). Live policy
redact gap from iter 3 is **closed**; trace `final_action=flag` is tier-2 advisory after
successful policy redaction (not the pre-fix empty-policy / ReDoS-drop failure mode).

## Live stages[] samples (from blessed snapshots)

### 03 — Jailbreak block
```json
[{"stage":"policy","action":"block"},{"stage":"input_scan","action":"skip"},
 {"stage":"route","action":"skip"},{"stage":"output_guard","action":"skip"}]
```

### 06 — Benign allow
Policy allow → input_scan tier_2 clean → route allow → output_guard allow.

### 09 — Output guard redact
Policy allow → input_scan tier_2 clean → route allow (×2) → **output_guard redact**.

## B-ENF (fixed — iteration 2)

`proxy_chat` input-scan path uses `enforcement.resolve_enforcement()`:
- Tier-2 `recommended_action` preferred over `verdict.action`
- Org policy action from `check_resp` participates in precedence
- Honesty check uses `redaction_possible=False` → block via resolver

## B-POL (iteration 2 + 3)

**Iteration 2:** Empty Redis compiled bundles (`policy_count: 0`) caused `matched_rules: []`.
Fixed operationally via `seed_policy_package --org-slug zeroshield` (45 policies).

**Iteration 3:** ReDoS false-positive in `_has_redos_shape()` corrupted `\s`/`\d` shorthands when
probing patterns, silently dropping PHI MRN regex at compile time. Fixed in `policy_engine.py`:
- `_strip_for_redos_probe()` — only neutralizes `\(` / `\)`, preserves `\s`, `\d`, etc.
- Tightened `_QUANTIFIED_ALTERNATION_GROUP_RE` to `\)[+*{]` (no `\s*` gap)
- Tests: `test_policy_engine_redos_guard.py`

## P5 routing / kill-switch / output-guard

Cases 07–09 **pass live** against current `output_guard.py` / `llm_router.py` — no code changes
required this iteration.

## Code anchors

| Area | Location |
|------|----------|
| Policy-before-scan | main.py:5725–5890 |
| Input scan enforcement | main.py:6161–6350 (`resolve_enforcement`) |
| ReDoS guard | policy_engine.py `_strip_for_redos_probe`, `_has_redos_shape` |
| Golden live driver | gateway/tests/golden/live_driver.py |
| Golden snapshots | gateway/tests/golden/snapshots/*.json |

## Next steps

1. Rebuild/redeploy gateway image with `policy_engine.py` ReDoS fix for live case 02 chat path
2. P6 hold: CI gate active; offline skips live cases; live gate runs in docker CI job when wired
