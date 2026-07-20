# Chat Pipeline Contract (FREEZE)

Canonical semantics for the `/v1/chat/completions` enforcement chain in the
gateway data plane. This document is the source of truth for golden tests in
`gateway/tests/golden/`.

## Pipeline order (proxy_chat)

1. **Auth / rate-limit / kill-switch** — org config, TPM, routing eligibility
2. **Policy** (`policy_engine.evaluate` via `POLICY_SYNC`) — deterministic,
   domain=`pipeline`, runs **before** input scan
3. **Input scan** (Tier-1 → optional Tier-2 Bedrock) — on `effective_prompt`
   (post-policy redaction)
4. **Enforcement resolution** (`enforcement.resolve_enforcement`) — merges
   policy action + guard recommendation + default
5. **LLM route** (`llm_router`) — sensitivity / kill-switch fallback
6. **Output guard** — byte-verified redact/block on model response

## Stage `stages[]` shape (telemetry + UI)

Each stage entry:

| Field | Type | Description |
|-------|------|-------------|
| `stage` | string | `policy`, `policy_redact`, `input_scan`, `output_guard`, `route` |
| `action` | string | `allow`, `monitor`, `flag`, `redact`, `block` |
| `detection_tier` | string? | `policy`, `tier_1`, `tier_2`, `none` |
| `threat_type` | string? | `pii`, `secret`, `jailbreak`, `prompt_injection`, … |
| `matched_rules` | string[]? | Rule names (policy or scanner) |
| `matched_policy_names` | string[]? | Policy display names |

## Enforcement precedence

| Priority | Source | Notes |
|----------|--------|-------|
| 1 | Org policy action (matched category) | From `POLICY_SYNC` / `_policy_check_cached` |
| 2 | Guard recommendation | Tier-1/Tier-2 `recommended_action` or `verdict.action` |
| 3 | Default | `allow` unless org config says otherwise |

## Action lattice (severity low → high)

```
allow < monitor/flag < redact < block
```

## REDACT contract (B-ENF)

- `recommended_action=redact` → enforce **redact**, not block.
- Escalate to **block** only when:
  - org policy action is `block` for the matched category, **or**
  - redaction is byte-impossible (`redaction_possible=False` after honesty check).

## Policy-before-scan contract (B-POL)

- `PKG{org_id}_PIPE_PII`, `PIPE_PCI`, `PIPE_PHI` must compile into
  `policies:compiled:{org_slug}` with `policy_domain=pipeline`.
- Policy redaction mutates `effective_prompt` **before** Tier-1/Tier-2 runs.
- Input scan on a masked prompt should `allow` when no residual threat remains.

## Golden cases (9)

| # | Scenario | Expected final |
|---|----------|----------------|
| 1 | PII in prompt → policy redact → scan allow | `redact` |
| 2 | PHI detected | `redact` |
| 3 | Jailbreak | `block` |
| 4 | Prompt injection | `block` |
| 5 | Secrets/credentials | `block` |
| 6 | Benign prompt | `allow` |
| 7 | Benign + kill-switch | `allow` (fallback route) |
| 8 | Benign + sensitivity routing | `allow` (compliant model) |
| 9 | Model emits PII | `redact` (bytes changed) |

## CI gate

```bash
cd gateway && ./.venv/bin/python -m pytest tests/golden -q
```

Snapshot drift requires deliberate re-bless:

```bash
GOLDEN_UPDATE=1 cd gateway && ./.venv/bin/python -m pytest tests/golden -q
```
