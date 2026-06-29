# ZeroShieldResponse.v1 — Client Response Contract

Status: **current** (documents shipped behavior as of gateway v1.4.x + M-51).
Audience: API consumers integrating through the OpenAI-compatible endpoint
`POST /v1/chat/completions`.

**Product requirement:** ZeroShield is consumable with the **stock OpenAI
SDK** — no custom SDK. Integration is a base-URL + API-key swap only (see
[Quickstart](#quickstart-stock-openai-sdk-python)).

Source-of-truth anchors (gateway repo, `gateway/ai_mesh_gateway/`):

| Concern | Anchor |
| --- | --- |
| Metadata builder (internal, all fields) | `main.py::_build_zeroshield_metadata` (≈ line 968) |
| Guard-model enrichment | `pipeline_trace.py::enrich_zeroshield_from_verdict` (≈ line 117) |
| Client-safe field allowlist | `main.py::_redact_for_client_response` (≈ line 472) |
| Streaming trace base | `main.py::_build_stream_zeroshield_base` (≈ line 1428) |
| Streaming trace frame + choke point | `stream_orchestration.py::build_stream_trace_frame` (≈ line 348) / `stream_with_finalize` (≈ line 420) |
| Block error envelope | `main.py::_build_safe_block_response` (≈ line 401) / `_build_block_response` (≈ line 1887) |
| Streaming headers | `stream_orchestration.py::enrich_stream_headers` (≈ line 168) |

---

## 1. Non-streaming: the `zeroshield` response object

Successful (HTTP 200) chat completions are standard OpenAI `chat.completion`
JSON with ONE extra top-level field, `zeroshield`. The stock OpenAI SDK
tolerates this (its pydantic models allow extra fields); access it via
`completion.zeroshield` or `completion.model_extra["zeroshield"]`.

```json
{
  "id": "chatcmpl-...",
  "object": "chat.completion",
  "created": 1700000000,
  "model": "gpt-4o-mini",
  "choices": [ { "index": 0, "message": { "...": "..." }, "finish_reason": "stop" } ],
  "usage": { "prompt_tokens": 10, "completion_tokens": 8, "total_tokens": 18 },
  "zeroshield": {
    "request_id": "zs-1a2b3c4d5e6f",
    "action": "allow",
    "detection_tier": "tier_1",
    "threat_type": "clean",
    "confidence": 0.98,
    "matched_patterns": [],
    "reason": "All security checks passed. No threats detected.",
    "detail": "...",
    "processing_time_ms": 42.17,
    "risk_score": 0.02,
    "scan_outcome": "clean",
    "guard_action": "allow",
    "guard_reason": "...",
    "guard_model": "...",
    "selected_model": "gpt-4o-mini",
    "original_model": "auto",
    "rerouted": false,
    "routing_reason": "...",
    "decision_source": "weighted"
  }
}
```

### 1.1 Field reference (client-visible)

Internally the gateway builds a richer object
(`main.py::_build_zeroshield_metadata`), then **strips it to a client-safe
allowlist** before returning (`main.py::_redact_for_client_response`,
≈ line 4333 and ≈ line 5477 call sites). Only the following keys can appear
client-side; absent keys were either not produced or stripped:

| Field | Type | Meaning |
| --- | --- | --- |
| `request_id` | string | Correlation id (`zs-<12 hex>`); quote it in support requests. |
| `action` | string | Final input-pipeline outcome: `allow`, `flag`, `redact`, `block`, `passthrough` (firewall disabled). |
| `detection_tier` | string | Stage that decided: `none`, `tier_1` (regex), `tier_2` (guard model), `policy`, `output_guard`, `threat_intel`. |
| `threat_type` | string | Threat label (`clean`, `pii`, `secret`, `prompt_injection`, …). |
| `confidence` | number | Detector confidence 0–1 (clean allow paths report `1 - risk`). |
| `matched_patterns` | string[] | Pattern/rule labels that matched (e.g. `["EMAIL"]`). |
| `reason` / `detail` | string | Human-readable explanation (client-safe text only). |
| `processing_time_ms` | number | Gateway-side processing time. |
| `risk_score` | number | Guard-model risk score 0–1 (`pipeline_trace.py::enrich_zeroshield_from_verdict`). |
| `scan_outcome` | string | `clean` on tier-2-verified allows. |
| `guard_action`, `guard_reason`, `guard_model`, `guard_findings`, `recommended_action`, `enforcement_source`, `reason_code` | mixed | Operator-facing Guard Model explanation (`pipeline_trace.py::build_guard_fields`). |
| `selected_model`, `original_model`, `routed_model`, `rerouted`, `routing_reason`, `decision_source`, `policy_summary`, `routing`, `decision_factors`, `weights` | mixed | Multi-model routing attribution (present when routing ran). |

Fields that are **never** sent to clients (stripped by
`_redact_for_client_response`): `compliance_tags`, `original_prompt_hash`,
`redacted_prompt`, `redacted_response`, `rewritten_response`,
`review_required`, `security_incident`, `factuality_warning`, `intent`,
`matched_policy_names` / `matched_rule_names` and anything else not in the
allowlist above.

> Note: `action: "redact"` with HTTP 200 means PII/secrets were masked
> (input and/or output) and the request still completed; the response body
> already contains only the sanitized text.

---

## 2. Streaming: the terminal zeroshield trace frame (M-51)

Every SSE stream from `/v1/chat/completions` (`stream: true`) terminates
with **exactly one** trace frame immediately **before** `data: [DONE]`, on
every termination path: allow, redact, flag, block, upstream error, and
mid-stream exceptions. Emission point:
`stream_orchestration.py::stream_with_finalize` — the single choke point all
three streaming launch paths funnel through
(`main.py::_launch_chat_stream_response`).

The frame is a **ChatCompletionChunk-shaped** JSON object with an empty
`choices` array plus the extra top-level `zeroshield` object. The stock
OpenAI SDK accepts it as a normal chunk (the SDK itself emits empty-choices
chunks for `stream_options.include_usage`, and its models allow extra
fields):

```text
data: {"id":"chatcmpl-abc","object":"chat.completion.chunk","created":1718000000,
       "model":"gpt-4o-mini","choices":[],
       "usage":{"prompt_tokens":5,"completion_tokens":7,"total_tokens":12},
       "zeroshield":{"request_id":"zs-stream-1a2b3c4d5e6f","action":"allow",
                     "detection_tier":"none","threat_type":"clean",
                     "confidence":1.0,"matched_patterns":[],
                     "reason":"All security checks passed. No threats detected.",
                     "processing_time_ms":318.42}}

data: [DONE]
```

Contract details (`stream_orchestration.py::build_stream_trace_frame`):

- `id` / `model` are copied from the upstream stream when available, else
  synthesized (`chatcmpl-<request_id>` / requested model).
- `object` is always `"chat.completion.chunk"`; `created` is an int epoch.
- `choices` is always `[]` — SDK-safe, never confused with content.
- `usage` is included when the upstream reported token usage.
- The `zeroshield` payload reuses the **same client-safe shape as §1**
  (`main.py::_build_stream_zeroshield_base` runs the identical
  build → enrich → redact pipeline), then overlays the mid-stream
  output-guard outcome recorded in
  `stream_orchestration.py::StreamRunMetrics.record_guard_action`
  (set by `secure_streaming.py::SecureStreamingResponse._flush_buffer`).

`zeroshield.action` in the trace frame:

| Value | Meaning | Stream shape |
| --- | --- | --- |
| `allow` | Clean stream. | content chunks → trace → `[DONE]` |
| `redact` | Input PII was redacted pre-LLM, **or** the output guard redacted streamed content (`detection_tier: "output_guard"`). | sanitized chunks → trace → `[DONE]` |
| `flag` | Scanner/guard flagged but monitor mode allowed it. | content chunks → trace → `[DONE]` |
| `block` | Output guard terminated the stream (`detection_tier: "output_guard"`, `reason: "Streaming response blocked by output guard."`). | partial content → error chunk (`{"error":{...,"code":"output_blocked"}}`) → trace → `[DONE]` |
| `error` | Upstream/provider error terminated the stream. | partial content → (optional error chunk) → trace → `[DONE]` |

SDK note: when a `data: {"error": ...}` chunk appears mid-stream (block /
upstream-error paths), the stock OpenAI SDK raises `openai.APIError` during
iteration as soon as it sees that chunk — by design. The trace frame is still
emitted on the wire for raw-SSE consumers and logging proxies.

Requests **blocked before the stream opens** (input scan, policy, preflight)
never start SSE — they return the JSON error envelope of §4 with the normal
`application/json` content type (`tests/test_stream_governance_integration.py::test_injection_block_response_is_json_not_sse`).

---

## 3. `X-ZeroShield-*` response headers

Set on streaming responses by `stream_orchestration.py::enrich_stream_headers`
and on non-streaming 200s in `main.py` (≈ lines 5298–5440). All values are
latin-1-sanitized (`main.py::_latin1_safe_headers`).

| Header | Where | Value |
| --- | --- | --- |
| `X-ZeroShield-Action` | both | `redacted` \| `redact` \| `flag` \| final output-guard action. Omitted on plain allows (non-streaming). |
| `X-ZeroShield-Detection-Tier` | streaming | Tier of the input scan verdict (`tier_1`, `tier_2`, …). |
| `X-ZeroShield-Matched-Patterns` | non-streaming | Comma-joined matched pattern labels. |
| `X-ZeroShield-Redacted-Types` | non-streaming | Comma-joined pattern labels that were redacted. |
| `X-ZeroShield-Review-Required` | non-streaming | `"true"` when the output guard requests review. |
| `X-ZeroShield-Factuality-Warning` | non-streaming | `"true"` when hallucination heuristics flagged the answer. |
| `X-ZeroShield-Original-Model` | both | Client-requested model (or `auto`). |
| `X-ZeroShield-Routed-Model` | both | Model actually used after routing. |
| `X-ZeroShield-Rerouted` | both | `"true"`/`"false"`. |
| `X-ZeroShield-Routing-Source` | both | Routing decision source (e.g. `weighted`). |
| `X-ZeroShield-Routing-Reason` | both | Reason string (truncated to 180 chars). |
| `X-ZeroShield-Routing-Policy-Id` | streaming | sha256-derived 16-char id of the routing policy summary. |
| `X-ZeroShield-Routing-Policy-Summary` | both | Only when org debug headers are enabled (`stream_emit_debug_headers`). |
| `X-ZeroShield-Stream-Scan-Mode` | streaming | `none` \| `scanner_only` \| `output_guard`. |
| `X-ZeroShield-Stream-Lifecycle` | streaming (debug) | `preflight,selection,stream,finalize`. |
| `X-ZeroShield-RAG-Context-ID` / `X-ZeroShield-Pipeline-Request-ID` | RAG endpoints | RAG context correlation ids (outside this contract's chat scope). |

---

## 4. Error bodies

### 4.1 Blocked request (HTTP 403, also 451 for tier-2 strict degradation)

Built by `main.py::_build_safe_block_response` (≈ line 401). Deliberately
generic — never includes prompts, matched rules, or threat specifics:

```json
{
  "error": "blocked",
  "message": "Request blocked due to security policy",
  "code": "content_blocked",
  "request_id": "zs-1a2b3c4d5e6f",
  "category": "prompt_injection",
  "blocked_by": "input_scan",
  "detection_tier": "tier_1",
  "pipeline_stage": "input_scan",
  "pipeline_trace": { "...": "operator pipeline stages (see pipeline_trace.py::build_pipeline_trace)" }
}
```

- `code` values include: `content_blocked`, `output_blocked`,
  `tier_1_prompt_injection`, `tier2_degraded`, `threat_intel_blocked`.
- `category` is the generic threat category (`prompt_injection`,
  `jailbreak`, `pii`, `secret`, `toxicity`, `policy_violation`, …).
- With the stock OpenAI SDK this surfaces as
  `openai.APIStatusError` (403 ⇒ `openai.PermissionDeniedError`); the JSON
  above is available as `error.body` / `error.response.json()`.

### 4.2 Policy engine unavailable (HTTP 503, fail-closed)

`main.py` ≈ lines 2762 / 3740:

```json
{
  "error": "service_unavailable",
  "message": "Policy evaluation unavailable. Request denied (fail-closed).",
  "code": "policy_unavailable"
}
```

### 4.3 Streaming preflight failure (HTTP 503, fail-closed, before SSE)

`stream_orchestration.py::streaming_preflight_block_body` (≈ line 132):

```json
{
  "error": "service_unavailable",
  "message": "Streaming preflight failed: policy cache not loaded (fail-closed).",
  "code": "stream_preflight_policy_unavailable"
}
```

Other pre-stream JSON failures reuse standard envelopes:
`{"error","message","code"}` with codes such as `kill_switch_active`,
`model_not_allowed`, `model_not_configured`, `no_provider_configured`,
`circuit_breaker_open`, `invalid_request_body`. Auth failures come from
`middleware.py::validate_api_key`: `{"error": "unauthorized" | "forbidden" |
"service_unavailable", "message": "..."}` with 401/403/503.

---

## 5. Quickstart: stock OpenAI SDK (python)

No ZeroShield SDK. Swap the base URL and use your gateway API key
(`zs_...`). Everything else is unchanged OpenAI usage.

```python
from openai import OpenAI

client = OpenAI(
    base_url="https://<your-gateway-host>/v1",  # ZeroShield gateway
    api_key="zs_live_...",                       # ZeroShield gateway API key
)

# ── Non-streaming ────────────────────────────────────────────────
completion = client.chat.completions.create(
    model="gpt-4o-mini",
    messages=[{"role": "user", "content": "Summarize our Q3 plan."}],
)
print(completion.choices[0].message.content)
trace = completion.model_extra.get("zeroshield", {})   # §1
print(trace.get("action"), trace.get("request_id"))

# ── Streaming (terminal trace frame, §2) ─────────────────────────
stream = client.chat.completions.create(
    model="gpt-4o-mini",
    messages=[{"role": "user", "content": "Stream a haiku."}],
    stream=True,
)
for chunk in stream:
    if chunk.choices:                                   # content chunk
        print(chunk.choices[0].delta.content or "", end="")
    else:                                               # terminal trace frame
        zs = (chunk.model_extra or {}).get("zeroshield")
        if zs:
            print(f"\n[zeroshield] action={zs['action']} request_id={zs['request_id']}")

# ── Blocked requests (§4.1) ──────────────────────────────────────
import openai
try:
    client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[{"role": "user", "content": "Ignore previous instructions ..."}],
    )
except openai.APIStatusError as err:                    # 403 ⇒ PermissionDeniedError
    print(err.status_code, err.body.get("code"), err.body.get("request_id"))
```

Verified by `gateway/ai_mesh_gateway/tests/test_openai_sdk_compat.py`
(stock `openai` SDK against the live app) and
`tests/test_stream_trace_frame.py` (one-trace-frame-per-termination
invariant).
