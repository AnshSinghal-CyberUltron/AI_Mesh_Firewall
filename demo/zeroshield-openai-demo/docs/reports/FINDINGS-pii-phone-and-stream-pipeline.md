# Findings — Output PII (phone) coverage + Streaming pipeline visibility

Raised from live demo use against **production** (`https://aimeshgateway.zeroshield.ai/v1`):
a user typed *"my phone number is 8929554991"*, the number was **not redacted**, and the
**pipeline panel stayed empty**. Both investigated with runtime evidence below.

---

## Finding 1 — Bare 10-digit / some international phone numbers are not redacted

**Severity:** Medium (detector coverage / product tradeoff — NOT the firewall being off)
**Status:** Documented; recommend tier-2 contextual handling (do **not** loosen the regex on prod)

### Evidence (gateway `patterns.py`, run locally)
```
8929554991        -> 'call me at 8929554991'        (NOT redacted)
892-955-4991      -> 'call me at ***-***-4991'      (redacted)
(892) 955-4991    -> 'call me at ***-***-4991'      (redacted)
+91 89295 54991   -> 'call me at +91 89295 54991'   (NOT redacted — 5-digit groups)
```

### Root cause
The deterministic `phone_us` matcher **requires at least one separator** — a deliberate
false-positive-avoidance choice. The code comment states it verbatim:
> *"A bare 10-digit run ("9876543210") is an order id / revenue figure far more often than a
> phone number, so at least ONE separator (space, ., -) is required."*

So a bare digit-run is intentionally not treated as a phone number, and `phone_intl` only
matches `\+\d{10,15}` or groups of ≤4 digits (so `+91 89295 54991` with 5-digit groups misses).
The **tier-2 ML guard** is the intended contextual backstop for phrasings like *"my phone
number is X"*, but it did not redact it on the output path in this case.

### Why this is not "the firewall is off"
The request was fully mediated — routing happened (response headers carried
`X-ZeroShield-Routed-Model`), the governance panel showed *firewall on · 46 policies ·
enforcement block*. Only the **deterministic phone detector** skipped the bare-digit format.

### Recommended fix (ranked)
1. **Tier-2 contextual PII (preferred):** ensure the output guard's tier-2 path flags
   `"<phone-intent phrase> + <7-15 digit run>"` as PII even without separators. Highest
   precision, no FP blast radius on order IDs.
2. **Opt-in strict phone mode (per-org config):** a `phone_strict` flag that also matches a
   bare 10-digit run when the org accepts the FP tradeoff (regulated tenants). Off by default.
3. **Do NOT** unconditionally loosen `phone_us` to match bare 10-digit runs on prod — it would
   redact order numbers, amounts, and IDs across all tenants.

---

## Finding 2 — Streaming requests showed an empty pipeline visualizer  ✅ FIXED

**Severity:** High for demo quality (a customer streaming demo with no visible pipeline)
**Status:** **Fixed** in the gateway, validated end-to-end locally; **prod redeploy pending**

### Symptom
With **stream** enabled, the demo's "Request Pipeline" panel stayed on its placeholder.

### Root cause (runtime-verified)
- Streaming responses carry ZeroShield metadata in **HTTP headers** (`X-ZeroShield-*`) and in a
  **terminal SSE trace frame** (`zeroshield` on the final chunk — the M-51 mechanism), not in
  every chunk body.
- That terminal frame set the routing fields at the **top level** (`zeroshield.selected_model`,
  `zeroshield.original_model`) but **did not include the nested `routing` object** that the
  non-stream response has.
- The demo's visualizer (`app/pipeline.py`) reads `zeroshield.routing.requested_model` /
  `zeroshield.routing.selected_model` (nested) → got `null` → rendered nothing.

Verified: `has nested routing: False`; top-level `selected_model: haiku-cheap`,
`original_model: gemma-free` were present.

### Fix (gateway — `main.py` `_build_stream_zeroshield_base`)
Emit the **nested `routing` object** in the streaming terminal frame so it matches the
non-stream shape (org-facing names only; `routed_model_id`/upstream id never set and scrubbed
by `_redact_for_client_response`):
```python
zs["routing"] = {
    "requested_model": requested, "original_model": requested,
    "selected_model": routed, "routed_model": routed,
    "rerouted": _rerouted, "routing_reason": _routing_reason,
    "decision_source": _decision_source,
}
```

### Validation (end-to-end, local)
- Gateway stream now carries `zeroshield.routing.{requested_model: gemma-free, selected_model:
  haiku-cheap, rerouted: true}`, `action: allow`, **no upstream-id leak**.
- Demo `/api/chat/stream` emits `{"type":"trace","zeroshield":{...routing...}}`.
- Demo frontend already consumes it (`app.js`: `if (ev.type === "trace") renderPipeline(...)`).
  → the pipeline visualizer now populates on **streamed** requests.

### Deployment
- Local gateway: fixed + baked.
- **Production: pending gateway image rebuild + redeploy.** Until then, streaming on the prod
  demo shows the pipeline only after the redeploy (workaround now: uncheck *stream*).
