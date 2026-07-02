# Trace UI Contract — `stages[]` handoff (frontend session)

**Owner:** Cursor chat-pipeline FREEZE session (`amf-pipeline` worktree).  
**Consumer:** Frontend trace / pipeline timeline UI (do **not** edit `frontend/**` here).

## Purpose

The gateway emits a ordered list of pipeline stages so Module 1.1+ dashboards can
render an honest enforcement timeline. UI must reflect **byte-verified** outcomes:
never show `redact` when egress bytes are unchanged.

## Response envelope (chat completions)

ZeroShield metadata rides on blocked responses and on successful completions
(`zeroshield` object / `x-zeroshield-*` headers — mirror existing gateway patterns).

```json
{
  "zeroshield": {
    "action": "redact",
    "pipeline_trace": {
      "stages": [
        {
          "stage": "policy",
          "action": "redact",
          "detection_tier": "policy",
          "matched_policy_names": ["PII Detection & Redaction"],
          "matched_rules": ["Redact email addresses"],
          "latency_ms": 1.2
        },
        {
          "stage": "input_scan",
          "action": "allow",
          "detection_tier": "tier_1",
          "latency_ms": 4.5
        }
      ],
      "final_action": "redact",
      "processing_time_ms": 12.3
    }
  }
}
```

## Field semantics

### `stage` (required)

| Value | Meaning |
|-------|---------|
| `policy` | Deterministic org policy evaluation |
| `policy_redact` | Policy-driven masking applied to prompt |
| `input_scan` | Tier-1 / Tier-2 input guard |
| `route` | Model selection / kill-switch / sensitivity routing |
| `llm` | Upstream provider call (no enforcement action) |
| `output_guard` | Response inspection |

### `action` (required)

One of: `allow`, `monitor`, `flag`, `redact`, `block`, `rewrite`.

**UI rule:** If `action=redact` and `redact_noop=true` in metadata, display as
`flag` (honesty parity with output_guard at main.py:1566).

### Optional fields

| Field | UI usage |
|-------|----------|
| `detection_tier` | Badge: Policy / T1 / T2 |
| `threat_type` | Icon + filter chips |
| `matched_rules` | Expandable list (rule names, not raw PII) |
| `matched_policy_names` | Policy attribution column |
| `latency_ms` | Stage timing bar |
| `metadata.redact_noop` | Suppress redact badge when true |

## Rendering rules

1. Stages render **in array order** (policy → scan → route → llm → output).
2. `final_action` on the parent must equal the highest-severity stage action
   unless monitor mode downgraded a block (show strikethrough block + "monitor").
3. Never render raw `matched_patterns` that may contain PII — use rule names only.
4. Degraded Tier-2 (`input_scan_degraded`) shows warning chip; traffic may still flow.

## Test vectors

Golden snapshots: `gateway/tests/golden/snapshots/*.json`  
Contract doc: `docs/pipeline/CHAT_PIPELINE_CONTRACT.md`

## Open items for frontend

- [ ] Consume `pipeline_trace.stages` when present on chat responses
- [ ] Map `redact_noop` → flag styling
- [ ] Add kill-switch / routing stage when `stage=route` appears
- [ ] Playwright gate: align with `scripts/playwright_*` Module-1.1 waits (≥60s)
