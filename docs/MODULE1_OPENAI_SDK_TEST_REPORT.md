# ZeroShield AI Mesh Firewall — Module 1 (1.1–1.7)
## Rigorous Test Report: Stock OpenAI SDK Surface

| | |
|---|---|
| **Date** | 2026-07-19 |
| **Scope** | Module 1 capabilities 1.1–1.7, exercised through the **unmodified** `openai` Python SDK (v2.38) |
| **Method** | 8 new pytest suites (4,190 lines, 171 tests) + adversarial refutation of every finding |
| **Test result (independently re-run)** | **221 passed · 27 xfailed · 0 failed · 0 errors** |
| **Baseline suite after campaign** | `test_openai_sdk_compat.py` — 46 passed, 2 xpassed (unchanged) |
| **Production code modified** | **None** (verified via `git status`) |
| **Findings** | 39 adversarially reviewed → **25 CONFIRMED**, 4 PLAUSIBLE, **10 REFUTED** |
| **Critical** | 2 (F-01, F-02) |

---

## 0. How to reproduce anything in this report

Every reproduction step below assumes this preamble. Run it once.

```bash
cd /home/contact_cyberultron_com/AI_Mesh_Firewall/gateway
```

All commands use the gateway's own virtualenv:

```bash
.venv/bin/python -m pytest <node-id> -q -p no:cacheprovider
```

**Understanding `xfail` vs `--runxfail`.** Every confirmed defect is pinned by a test that
asserts the *correct* (spec-mandated) behaviour and is marked `xfail` with the finding
recorded in its reason string. Two ways to run them:

| Command | What you see | Use it to |
|---|---|---|
| `pytest <node-id> -q -rx` | `XFAIL … <full finding text>` | Read the finding + confirm the defect still exists |
| `pytest <node-id> -q --runxfail` | `FAILED …` + full assertion diff | **See the actual defect output.** This is the real repro |

`--runxfail` strips the xfail marker so the test fails visibly. **A `FAILED` result under
`--runxfail` is the proof of the defect.** When a fix lands, the same command passes.

Run the whole campaign:

```bash
.venv/bin/python -m pytest ai_mesh_gateway/tests/test_m1_*.py -q -p no:cacheprovider -rx
# expected today: 221 passed, 27 xfailed
```

### Environment the tests run in — read this before trusting any result

Tests drive the **real gateway FastAPI app** in-process via `httpx.ASGITransport`, and under a
**real uvicorn server on a loopback TCP socket** where wire behaviour matters (SSE framing,
`[DONE]`, chunked transfer, mid-stream disconnect). The following are stubbed:

| Component | State | Consequence |
|---|---|---|
| Gateway app, routing, middleware, handlers | **Real** | Strong evidence |
| `InputScanner` | **Real** | Detection verdicts genuine |
| `OutputGuard` | **Real** in 1.7; `None` elsewhere | §1.7 genuine; §1.1–1.6 ran with guard removed |
| `LLM_ROUTER` | **MagicMock** recorder (real scoring grafted on in 1.5) | "upstream received X" = a mock recorded a dict |
| `AGENT_ID` | **`None`** in 1.1, 1.2, 1.3, 1.4, 1.7 | Takes the `main.py:7563` short-circuit — **~1,900 lines never execute** |
| Tier-2 (Bedrock) | **Disabled everywhere** | Production default is ON. All detection is Tier-1 + Tier-0.5 only |
| `RATE_LIMITER`, `POLICY_SYNC`, `CIRCUIT_BREAKER`, `TELEMETRY` | `None` unless overridden | Gateway 429s, policy 403s, telemetry out of scope |
| Redis | `fakeredis` (no Lua engine) | Limiter atomicity untested |
| Live docker stack | Gateway up but `degraded / policy_cache_unavailable`; redis + postgres + control plane **stopped** | **No assertion depends on it** |

> **This is the single most important caveat in the report.** See §6 — the harness's
> `AGENT_ID=None` default silently disables large parts of the pipeline and invalidates a
> meaningful share of the campaign's own positive results.

---

## 1. Executive summary

**Is ZeroShield drop-in usable with the stock OpenAI SDK? — YES.** `base_url` + `api_key`
swap only. Error taxonomy is fully conformant (every firewall rejection is a 4xx on the
correct `openai` exception subclass; only genuine internal faults are 5xx). Request fields
survive the enforcement chain. Streaming SSE framing/ordering/`[DONE]` are correct over real
TCP. Sync and async clients behave identically. `.with_raw_response` and
`.with_streaming_response` both work. Two cosmetic caveats: a size/DoS rejection is
indistinguishable from a content block on `error.code` (I-19), and `n>1` is silently clamped
to 1 (I-20).

**Are Module 1's controls actually enforced on that path? — PARTIALLY PROVEN, and the
unproven part is large.** 25 defects confirmed, including two CRITICAL: a raw
PII/credential exfiltration channel through `tool_calls.function.arguments` (I-01), and a
control-plane-registration failure mode that silently disables the entire §1.5 governance
block (I-02). Two §1.2 inline actions (`rewrite`, policy-custom `redact`) are no-ops on the
wire while telemetry attests success. Per-key action scoping is parsed, documented in the
vendor's own API docs, and never consulted.

**What genuinely held up.** The controls that *were* exercised held well: encoding-evasion
detection (base64, zero-width, homoglyph, leetspeak — all blocked), split-payload evasion,
injection in every message role, header/kwarg tenant spoofing (8 spoof headers all inert),
kill-switch reroute privilege escalation, and — the campaign's designated highest-risk path —
**streaming chunk-boundary secret splitting, masked at 1 character per SSE frame.** The
passthrough-route "blind proxy" hypothesis was disproven: hard 404 with zero upstream
invocation.

**What was never tested at all.** Vector-DB firewall capabilities (collection ACL, namespace
isolation, cross-tenant vector leakage, embedding anomaly detection), RAG pipeline stages,
intent classification, incident logging, human-review actions, and **any genuine
cross-tenant scenario** — no test anywhere seeds two orgs and attempts A→B.

---

## 2. Test suite inventory

All files are new. None modify production code.

| File | Tests | Result |
|---|---|---|
| `ai_mesh_gateway/tests/test_m1_1_ingress_sdk.py` | 42 | 39 passed, 3 xfailed |
| `ai_mesh_gateway/tests/test_m1_2_query_firewall_sdk.py` | 31 | 26 passed, 5 xfailed |
| `ai_mesh_gateway/tests/test_m1_3_vector_firewall_sdk.py` | 34 | 30 passed, 4 xfailed |
| `ai_mesh_gateway/tests/test_m1_4_context_guardrails_sdk.py` | 34 | 29 passed, 5 xfailed |
| `ai_mesh_gateway/tests/test_m1_5_routing_governance_sdk.py` | 27 | 22 passed, 5 xfailed |
| `ai_mesh_gateway/tests/test_m1_6_isolation_killswitch_sdk.py` | 28 | 27 passed, 1 xfailed |
| `ai_mesh_gateway/tests/test_m1_7_output_guard_sdk.py` | 38 | 34 passed, 4 xfailed |
| `ai_mesh_gateway/tests/test_m1_x_protocol_conformance_sdk.py` | 14 | 14 passed |
| **Total** | **248** | **221 passed, 27 xfailed** |

---

## 3. Coverage matrix

| Capability | SDK-reachable? | Result | Confidence |
|---|---|---|---|
| 1.1 Authentication | Yes | **ENFORCED** — 6 credential classes × 2 surfaces fail closed | High |
| 1.1 Tenant isolation | Yes | **PARTIAL** — headers/`organization=` inert with *one* key | **Low** — no two-tenant test exists |
| 1.1 Per-key model allowlist | Yes | **ENFORCED** — 403, upstream untouched | High |
| 1.1 Per-key action scoping | Yes | **GAP (I-03)** — parsed, advertised, never enforced | High |
| 1.1 Token budgets / clamping | Yes | **ENFORCED** — clamp verified on the wire | High |
| 1.1 Rate limiting | Yes | **PARTIAL** — chat enforced; embeddings has no per-key TPM (I-06); no quota headers (I-16) | Medium |
| 1.2 Query-stage detection | Yes | **ENFORCED** — injection, jailbreak, 4 encoding evasions, split payloads, all roles | **Medium** — Tier-1 only |
| 1.2 Action: block | Yes | **ENFORCED** — terminal, upstream never called | High |
| 1.2 Action: mask/redact | Yes | **PARTIAL** — built-ins masked; custom policy masks dropped (I-04) | High |
| 1.2 Action: rewrite | Yes | **GAP (I-05)** — no-op on the wire | High |
| 1.2 Action: model downgrade | Yes | ENFORCED | Medium |
| 1.2 Intent classification | Yes | **NOT TESTED** — telemetry sink no-op'd | — |
| 1.2 RAG pipeline stages | **No** | **NOT-SDK-REACHABLE (I-15)** — and SDK context gets chat-grade, not RAG-grade, treatment | High |
| 1.3 Collection ACL / namespace isolation | **No** | **NO COVERAGE** | — |
| 1.3 Cross-tenant vector leakage | **No** | **NO COVERAGE** | — |
| 1.3 Embedding anomaly detection | **No** | **NO COVERAGE** | — |
| 1.3 Embedding input scanning | Yes | PARTIAL — PII masked; blocked-keywords absent (I-07) | Medium |
| 1.4 Context minimization | Yes | **GAP (I-13, I-18)** — inert by default; scanned ≠ egressing prompt | High |
| 1.4 Field-level redaction | Yes | **PLAUSIBLE (P-03)** — implemented, never invoked on SDK path | Medium |
| 1.4 MCP guardrails | Yes | **GAP (I-08, I-09, I-14)** — specs unscanned, `mcp_context` dropped | High |
| 1.4 Compliance tagging | Yes | **NOT TESTED** — telemetry sink no-op'd | — |
| 1.5 Model catalogue | Yes | **GAP (I-11, I-21)** — org-less key enumerates shared catalogue | Medium |
| 1.5 Dynamic routing | Yes | **GAP (I-02, CRITICAL)** — entire block skipped when unregistered | High |
| 1.5 Provider normalization | Yes | ENFORCED | Medium |
| 1.6 Kill-switch | Yes | **ENFORCED** — incl. mid-stream; 4 escalation cases 503 | High |
| 1.6 Credential isolation | Yes | **ENFORCED** (reclassified from a refuted finding) | High |
| 1.6 Independent rate limits | Yes | Fail-open **by documented design** (refuted as defect) | High |
| 1.7 Output PII/credential inspection | Yes | **GAP (I-01, CRITICAL)** — tool-channel exfiltration | High |
| 1.7 Streaming output guard | Yes | **ENFORCED** — chunk-boundary splitting masked at 1 char/frame | High |
| 1.7 Action honesty | Yes | **GAP (I-10)** — envelope reports `allow` after redacting | High |
| 1.7 Incident logging | Yes | **NOT TESTED** — audit sink no-op'd | — |
| 1.7 Human review | — | **DOES NOT EXIST** (I-25) — not in `_VALID_OUTPUT_ACTIONS` | High |

---

## 4. Issue index

| ID | Severity | Title | Spec |
|---|---|---|---|
| [I-01](#i-01) | **CRITICAL** | Raw PII/credentials ship in `tool_calls.function.arguments` | 1.7 |
| [I-02](#i-02) | **CRITICAL** | Unregistered gateway skips the entire §1.5 governance block | 1.5 |
| [I-03](#i-03) | HIGH | `allowed_actions`/`denied_actions` parsed, advertised, never enforced | 1.1 |
| [I-04](#i-04) | HIGH | Policy `redact` with custom mask is a phantom redaction | 1.2 |
| [I-05](#i-05) | MEDIUM | `rewrite` inline action is a no-op on the wire | 1.2 |
| [I-06](#i-06) | MEDIUM | No per-key TPM ceiling on `/v1/embeddings` | 1.1 |
| [I-07](#i-07) | MEDIUM | `blocked_keywords` enforced on chat, absent on embeddings | 1.3 |
| [I-08](#i-08) | MEDIUM | Responses-API MCP tool specs forwarded unscanned | 1.4 |
| [I-09](#i-09) | MEDIUM | `extra_body={"mcp_context":…}` silently discarded | 1.4 |
| [I-10](#i-10) | MEDIUM | Envelope reports `action='allow'` after the guard redacted | 1.7 |
| [I-11](#i-11) | MEDIUM | Org-less key enumerates the shared LiteLLM catalogue | 1.5 |
| [I-12](#i-12) | MEDIUM | Unregistered gateway does not scrub provider topology | 1.5 |
| [I-13](#i-13) | MEDIUM | Scanner inspects the PRE-minimization prompt | 1.4 |
| [I-14](#i-14) | MEDIUM | Responses `mcp_call` input items collapse to empty messages | 1.4 |
| [I-15](#i-15) | MEDIUM | RAG stages not SDK-reachable; `role=tool` gets chat-grade scanning | 1.2 |
| [I-16](#i-16) | LOW | No `x-ratelimit-*` headers on any response | 1.1 |
| [I-17](#i-17) | LOW | `enforcement_mode=monitor` reports `action='allow'` | 1.2 |
| [I-18](#i-18) | LOW | Context minimization inert by default | 1.4 |
| [I-19](#i-19) | LOW | Size/DoS rejection reports `code='content_filter'` | x-cut |
| [I-20](#i-20) | LOW | `n>1` silently clamped to 1 | x-cut |
| [I-21](#i-21) | LOW | `GET /v1/models` org-scoped but never key-scoped | 1.5 |
| [I-22](#i-22) | LOW | Nested `agent_data` scanned only to depth 6 | 1.4 |
| [I-23](#i-23) | LOW | Injection in `file_search` filter neither scanned nor dropped | 1.3 |
| [I-24](#i-24) | LOW | Context budget cannot rescue oversized history from DoS block | 1.4 |
| [I-25](#i-25) | INFO | "Human review" action named in spec, does not exist in code | 1.7 |

---

## 5. Detailed issues

---

<a name="i-01"></a>
### I-01 — Raw PII and AWS credentials ship to the caller in `tool_calls.function.arguments`

> **CRITICAL** · CONFIRMED · §1.7 output inspection for PII/credential exposure · orig. `M17-01`

#### Description

When PII or secrets appear **only** in `choices[0].message.tool_calls[].function.arguments`
and `message.content` is clean, the output guard **detects them correctly** but **fails to
redact them**, and the response ships `200 OK` with the raw values.

This is not a detection failure. The guard fires and returns
`action='redact', matched_patterns=['ssn','email']`. The failure is in *delivery
enforcement*: the redactor only rewrites `content`. Because `content` was never modified, the
downstream mitigation `_neutralize_secondary_output_channels` (which exists precisely to blank
tool/reasoning channels after enforcement) never runs — it is gated on the primary content
having changed.

**The verdict is then downgraded to `allow`**, which has a second consequence nobody expected:
**no output-guard incident telemetry is emitted.** The leak is silent to the operator, not
merely un-redacted.

`reasoning_content` leaks by the identical mechanism. Both non-streaming guard call sites
(`main.py:2029` and `main.py:8510`) are affected.

#### Root cause — **corrected during verification**

> ⚠️ The original finding blamed `_set_completion_response_text` never running. **That is
> wrong** — `main.py:8753` calls it unconditionally inside the redact branch. **A fix written
> against the stated cause would land in a branch that never executes.**

The real cause is one call earlier, at **`main.py:8510`**:

```python
coalesce_output_guard_verdict_for_delivery(output_verdict, delivered_text=response_text)
```

`response_text` is **content only**. A tool-channel-only match therefore fails the
`output_verdict_applies_to_delivered_text` check, and the verdict is replaced with a bare
`OutputVerdict()` (`action=allow`).

The suppressor's own docstring justifies itself with a factually false premise:

> *"The scan input can include reasoning/tool channels that are not returned to the client."*

`tool_calls` and `reasoning_content` **are** returned to the client and are surfaced by the
stock SDK as first-class fields.

Confirm the gating mechanism directly:

```bash
sed -n '1873,1890p' ai_mesh_gateway/main.py    # the neutralizer + its content-gated premise
sed -n '8505,8515p' ai_mesh_gateway/main.py    # the actual defect site
```

#### Evidence

```
### content_has_pii=True  status=200
  content : 'Here it is: ***-**-9083'
  toolargs: ''
  RAW SSN anywhere in envelope: False | RAW AKIA: False
### content_has_pii=False status=200
  content : 'Calling the tool now.'
  toolargs: '{"ssn": "412-55-9083", "key": "AKIAIOSFODNN7EXAMPLE"}'
  RAW SSN anywhere in envelope: True | RAW AKIA: True

Guard spy (proving detection did fire):
  verdict: ('redact', 'pii', 'PII/secret detected in output: ssn, email', ['ssn','email'])
```

Note the control row: with PII **also** in `content`, both channels are correctly cleaned.
The leak appears only when `content` is clean — which is the *dominant shape of a real
agentic tool call*, not an exotic edge case.

#### Reproduction steps

1. Preamble (§0), then run the pinning test to see the defect fail visibly:

```bash
.venv/bin/python -m pytest \
  'ai_mesh_gateway/tests/test_m1_7_output_guard_sdk.py::test_tool_call_arguments_leak_raw_pii_when_content_is_clean' \
  -q -p no:cacheprovider --runxfail
```

**Expected today:** `1 failed` — the assertion "no raw canary in any response field" fails,
printing the raw SSN and AKIA key.

2. To read the recorded finding instead of the failure diff:

```bash
.venv/bin/python -m pytest \
  'ai_mesh_gateway/tests/test_m1_7_output_guard_sdk.py::test_tool_call_arguments_leak_raw_pii_when_content_is_clean' \
  -q -p no:cacheprovider -rx
```

3. **Conceptual repro against a live gateway** (stock SDK, org configured
   `output_pii_action=redact`): issue a normal tool-calling request and arrange for the model
   to return a short clean `content` plus PII inside the function arguments:

```python
import openai
c = openai.OpenAI(base_url="https://<gateway>/v1", api_key="<zs-key>")
r = c.chat.completions.create(
    model="gpt-4o-mini",
    messages=[{"role": "user", "content": "look up this customer"}],
    tools=[{"type": "function", "function": {"name": "lookup",
            "parameters": {"type": "object", "properties": {"ssn": {"type": "string"}}}}}],
)
print(r.choices[0].message.content)                      # clean
print(r.choices[0].message.tool_calls[0].function.arguments)  # raw SSN present
```

#### Impact / bypass risk

Direct exfiltration channel, reachable from the stock SDK with a plain `tools=[...]` request.
An indirect-prompt-injection payload need only steer the model to emit the secret as a
function-call argument rather than as prose. **Only mitigation: the `block` posture is
unaffected.**

#### Fix guidance

Pass the **full client-delivered envelope text** (`content` + `tool_calls` +
`reasoning_content` + `refusal`) as `delivered_text` to
`coalesce_output_guard_verdict_for_delivery` at both `main.py:8510` and `main.py:2029`.
Correct the suppressor's docstring premise. Add a regression test asserting no raw canary in
any response field under **every posture × every channel**.

---

<a name="i-02"></a>
### I-02 — An unregistered or degraded gateway silently skips the entire §1.5 governance block

> **CRITICAL** · CONFIRMED · §1.5 routing by sensitivity/compliance; also §1.1 allowlist and per-model limits · orig. `M15-02`

#### Description

`main.py:7563` short-circuits the request pipeline:

```python
if not AGENT_ID or not CONFIG["backend_url"]:
```

When taken, the request goes straight to `LLM_ROUTER.acompletion()` and returns at `:7793`,
**jumping the entire §1.5 block at `:7796-8245`** — dynamic routing, the compliance filter,
the data-sensitivity floor, the per-key model allowlist re-check, per-model rate limits, the
circuit breaker, and provider-response scrubbing (see I-12).

The gate is on **registration state**, not on **routing-catalogue availability**. `AGENT_ID`
is set only by successful control-plane registration (`main.py:929-948`), while `ConfigSync`
loads the model catalogue from Redis *independently*. **An unregistered worker holding a warm,
complete catalogue is a real production state** — and in it, governance is off while traffic
flows `200 OK`.

Observed with `AGENT_ID=None`:
- `data_sensitivity=restricted` → **served on `gpt-4o-mini`** (a public model)
- `compliance_requirements=[pci-dss]` (satisfiable by no model) → **served, not blocked**
- a model the org does not own → **served** (404 when registered)
- a model outside the caller's key allowlist → **served**

The last two make this a **fail-open on a security control**, not merely a routing-quality
regression.

#### Evidence

The verifier isolated the two variables the original repro conflated:

```
PROBE AGENT_ID=set  backend_url='http://cp.invalid'
  -> {'pci': '403 PermissionDenied', 'restricted_served_on': 'claude-3-5-sonnet'}
PROBE AGENT_ID=None backend_url='http://cp.invalid'
  -> {'pci': 'SERVED',               'restricted_served_on': 'gpt-4o-mini'}
```

Row 2 defeats the benign "a standalone deployment has nothing to route against" reading: the
control plane is *addressable*; only **registration** failed — and governance is off.

Escalation beyond the finding as written:

```
PROBE2 AGENT_ID=set  -> {'foreign_model': 'NotFoundError 404',
                         'key_not_allowed': 'SERVED as claude-3-5-sonnet'}
PROBE2 AGENT_ID=None -> {'foreign_model': 'SERVED as peer-org-private-gpt5',
                         'key_not_allowed': 'SERVED as llama-3-70b'}
```

*Caveat recorded honestly:* `peer-org-private-gpt5` exists only in the mocked catalogue, so
this **corroborates rather than independently proves** a shared-LiteLLM cross-tenant leak.

#### Reproduction steps

```bash
.venv/bin/python -m pytest \
  'ai_mesh_gateway/tests/test_m1_5_routing_governance_sdk.py::test_m15_02_unregistered_gateway_still_enforces_routing_governance' \
  -q -p no:cacheprovider --runxfail
```

**Expected today:** `1 failed`.

Inspect the short-circuit and its documented intent:

```bash
sed -n '7560,7568p' ai_mesh_gateway/main.py    # the gate
sed -n '955,975p'   ai_mesh_gateway/main.py    # "stuck on the degraded sync path forever"
sed -n '6846,6852p' ai_mesh_gateway/main.py    # the SAME bug class, already fixed for policy
```

#### Impact / bypass risk

High and **silent** — no error, no 403, no degraded flag on the response. The background
re-registration loop (`main.py:961-973`, capped 60s backoff) bounds a *transient* failure but
does nothing during a **sustained control-plane outage** — exactly when cached Redis config
keeps traffic flowing `200 OK` with governance off indefinitely.

> **Operational note:** the live docker stack in this environment is currently in the
> neighbourhood of this state (`degraded / policy_cache_unavailable`, control plane exited).
> Whether `AGENT_ID` is `None` there was **not verified**.

#### Fix guidance

Gate the fast path on **routing-catalogue availability**, not on `AGENT_ID`. Apply the exact
treatment already applied to the policy path at `main.py:6846-6852`, whose comment reads:
*"Gating the whole pipeline on AGENT_ID meant a failed/racy per-worker registration silently
disabled ALL policy-rule enforcement."* Add a degraded-mode integration test.

---

<a name="i-03"></a>
### I-03 — API-key action permissions are parsed, advertised, and never enforced

> **HIGH** · CONFIRMED · §1.1 per-credential scoping · orig. `M11-01`

#### Description

A key configured `permissions={'allowed_actions':['chat'],'denied_actions':['embedding']}`
successfully calls `/v1/embeddings` and returns `200`.

`permissions` is read into `AuthContext` at `middleware.py:94` and **never consulted**. Across
all non-test gateway code, `.permissions` has exactly two consumers —
`playground_auth.py:24` (`permissions.get("playground")`) and `admin_auth.py:45`
(`perms.get("admin")`). Neither reads actions. The upstream embedding provider is genuinely
invoked, so the denied action **consumes real BYOK capacity**.

**The "reserved field / not yet implemented" defence is refuted by the vendor's own
documentation.** `control/ai_mesh_control/core/gateway_key_views.py:260` advertises it
alongside genuinely-enforced knobs:

> *`**Update permissions:** {"permissions": {"allowed_actions": ["chat"], "denied_actions": ["embedding"]}}`* … *"Changes are automatically propagated to the Gateway via Redis sync."*

The propagation is real. The consumption does not exist. `models.py:324` calls it an
*"RBAC Payload"*.

**No compensating control in the documented configuration.** `main.py:2856` is
`if allowed_models:` — an empty list means unrestricted — and the vendor's own example at
`gateway_key_views.py:125` pairs `denied_actions:["embedding"]` with `allowed_models:[]`.
`DEFAULT_PERMISSIONS` ships `allowed_models` empty.

#### Evidence

```
> with pytest.raises(openai.PermissionDeniedError):
E Failed: DID NOT RAISE <class 'openai.PermissionDeniedError'>

test_d1_evidence_chat_only_key_actually_gets_embeddings PASSED
  assert len(hz.embed_bodies) == 1, "denied action still reached the provider"
```

#### Reproduction steps

```bash
.venv/bin/python -m pytest \
  'ai_mesh_gateway/tests/test_m1_1_ingress_sdk.py::test_d1_chat_only_key_must_not_reach_embeddings' \
  -q -p no:cacheprovider --runxfail
```

**Expected today:** `1 failed` — `DID NOT RAISE PermissionDeniedError`.

Confirm the field has no consumers:

```bash
grep -rn '\.permissions' ai_mesh_gateway/*.py | grep -v tests
# only playground_auth.py:24 and admin_auth.py:45 — neither reads actions
```

Live-gateway repro: create a key with `denied_actions:["embedding"]` via the control-plane
API, then `client.embeddings.create(...)` — returns 200.

#### Impact — **scope limit recorded honestly**

**No tenant boundary is crossed.** The actor must already hold a valid key for that org; org
isolation and the model allowlist remain intact. This is an **intra-tenant least-privilege
failure, not a data-isolation breach** — it should not be triaged as the latter. HIGH is
sustained on the *character* of the failure (silently no-op'ing an advertised, documented
control is worse than not shipping it) rather than raw CVSS impact.

#### Fix guidance

Either enforce `allowed_actions`/`denied_actions` at the surface handlers, **or remove them
from the control-plane API documentation and UI.**

---

<a name="i-04"></a>
### I-04 — Policy-engine `redact` with a custom mask is a phantom redaction

> **HIGH** · CONFIRMED · §1.2 inline mask/redact · orig. `M12-02`

#### Description

When the policy engine returns `action='redact'` with a masked value — a corporate codename, a
custom `redaction_config` regex — the **unmasked** value reaches the provider. Only values
the *generic* regex redactor can independently re-derive (built-in PII categories) are masked.
Anything policy-specific is **silently dropped while the pipeline trace attests `redact`**.

#### Root cause — **corrected during verification**

> ⚠️ Not the digit-run backstop (which is an additive, fail-closed egress check).

The policy engine's redaction result is **never plumbed into the router's signature at all**.
`llm_router.py:916-922` is:

```python
async def acompletion(self, body: dict, redacted_content: str | None = None, ...)
```

There is **no** `redaction_config` / `redaction_hints` parameter, and `effective_prompt` is
never written back into `body['messages']`. **There is no channel by which a policy-specified
mask can reach the wire.** This is structurally impossible, not a bug in the backstop.

`llm_router.py:753-762` *deliberately* documents `redacted_content` as **"only a SIGNAL"** — a
change made to fix a real multi-turn PII leak. That intent is legitimate. **What is
undocumented, and is the actual defect, is that a custom policy mask is silently discarded
while telemetry and `pipeline_trace` attest `action='redact'`.**

#### Evidence

The verifier reproduced this with **zero harness**, calling production code directly — no
fakeredis, no MagicMock, no ASGI app, no stubbed `_policy_check_cached`:

```
codename : 'Project Bluefin ships in Q3.'      <- policy mask DROPPED
custom   : 'Acquisition target is Acme Corp.'  <- policy mask DROPPED
email    : 'mail b***@c***.com'                <- masked
ssn      : 'ssn ***-**-6789'                   <- masked
```

#### Reproduction steps

```bash
.venv/bin/python -m pytest \
  'ai_mesh_gateway/tests/test_m1_2_query_firewall_sdk.py::test_policy_redact_custom_replacement_reaches_upstream' \
  -q -p no:cacheprovider --runxfail
```

**Expected today:** `1 failed` — captured wire text is
`'Project Bluefin ships in Q3.'` (unmasked).

Confirm the missing channel:

```bash
sed -n '916,925p' ai_mesh_gateway/llm_router.py   # no redaction_config parameter
sed -n '753,762p' ai_mesh_gateway/llm_router.py   # "only a SIGNAL" — documented intent
```

#### Impact / bypass risk

Blast radius is narrower than first reported (built-in PII categories **are** masked
independently), but the affected surface — **operator-authored `redaction_config` rules** —
fails **totally and silently**. A compliance control that reports success while leaking is a
wrong-direction failure. That is what sustains HIGH despite the narrow scope.

#### Fix guidance

Add a `redaction_config` / `redaction_hints` parameter to `LLMRouter.acompletion` so
policy-specified masks have a channel to the wire. **Until then, `pipeline_trace` and
telemetry must not report `action='redact'` for masks that were discarded.**

---

<a name="i-05"></a>
### I-05 — `rewrite` inline action is a no-op on the wire

> **MEDIUM** (corrected from HIGH) · CONFIRMED · §1.2 inline actions · orig. `M12-01`

#### Description

The §1.2 `rewrite` action executes its branch, emits `action='rewrite'` telemetry and an audit
record — and forwards the **original prompt verbatim** to the provider.

`main.py:6996` builds `'[Content policy applied: …] ' + prompt` into `redacted_prompt`, but
`llm_router._apply_redaction` treats `redacted_content` as a **signal only** (same root cause
family as I-04) and re-derives the wire text with `patterns.redact_all()`. The rewrite never
reaches the provider.

**Severity corrected HIGH→MEDIUM:** `main.py:7001` only **prepends an advisory notice** to the
full original prompt — it removes nothing. So the real delta is a lost prefix, not a content
bypass. Two defects nonetheless survive: a product-surface action that is a genuine wire
no-op, and trace dishonesty.

#### Evidence

Stderr proves the branch ran:

```
gateway: Rewrite action applied (user=1, rules=['rewrite_rule'])
```

Captured wire text: `'Please describe how to bypass the paywall.'` — byte-identical to input.

`body["messages"] =` is assigned exactly once in `main.py`, at line 6663 — **before** the
policy stage.

#### Reproduction steps

```bash
.venv/bin/python -m pytest \
  'ai_mesh_gateway/tests/test_m1_2_query_firewall_sdk.py::test_policy_rewrite_reaches_upstream' \
  -q -p no:cacheprovider --runxfail
```

#### Impact

The operator believes harmful content is stripped; dashboards show `decision='rewrite'`.

#### Fix guidance

Fix together with I-04 and I-10 — all three are the same class: **the enforcement that ran is
not what the client-visible envelope reports.** Make `rewrite` either reach the wire or stop
claiming it did.

---

<a name="i-06"></a>
### I-06 — No per-key TPM ceiling on `/v1/embeddings`

> **MEDIUM** · CONFIRMED · §1.1 token budgets / rate limits · orig. `M11-03`

#### Description

With identical rate-limiter state, `/v1/chat/completions` returns 429 and `/v1/embeddings`
returns 200. `RATE_LIMITER.check_rate_limit` has exactly **two call sites gateway-wide**, and
`proxy_embeddings` has neither.

The gap is **bidirectional**: `record_usage` is also absent, so embeddings never *charge* the
bucket. `/v1/usage` reports `tpm_used` flat while a key embeds unbounded tokens.

`/v1/embeddings` does enforce per-**org** TPM + burst/RPM (`main.py:10442-10479`), but the
per-**key** ceiling that guards chat is absent.

#### Reproduction steps

```bash
.venv/bin/python -m pytest \
  'ai_mesh_gateway/tests/test_m1_1_ingress_sdk.py::test_f7_per_key_tpm_also_guards_embeddings' \
  -q -p no:cacheprovider --runxfail
```

```bash
grep -rn 'check_rate_limit\|record_usage' ai_mesh_gateway/main.py | grep -v test
# note: no hits inside proxy_embeddings (10204-10594)
```

#### Impact

Shift traffic from chat to embeddings to evade a spent token budget. Held at MEDIUM because
rate limiting is documented fail-open best-effort, not a security boundary; per-org TPM is
acknowledged in-code as effectively dead (`main.py:10462`).

#### Fix guidance

Fix as **one piece of work with I-07**, and add an architectural test asserting parity of the
ingress control list between `proxy_chat` and `proxy_embeddings` — so the next control added
to chat cannot silently skip embeddings. See §7 bypass-channel #2.

---

<a name="i-07"></a>
### I-07 — Org `blocked_keywords` enforced on chat but not on `/v1/embeddings`

> **MEDIUM** (corrected from HIGH) · CONFIRMED · §1.3 · orig. `M13-4`

#### Description

A term the org forbids in conversation is still embeddable and indexable.

AST-level verification: `proxy_chat` (lines 5316-9692) contains `blocked_keywords`;
`proxy_embeddings` (10204-10594) contains **neither token**.

The `_rag_blocked_keyword_hit` docstring frames chat-only enforcement as a **defect** that was
fixed for RAG query under issue #21 — so documented intent is that blocklists apply to text
entering the embedding pipeline.

**Downgraded** because the same docstring designates `blocked_keywords` *"a custom filter, not
a hard security floor"*, and the tier-1 PII/secret floor with fail-closed byte-verify **does**
run on `/v1/embeddings`.

#### Reproduction steps

```bash
.venv/bin/python -m pytest \
  'ai_mesh_gateway/tests/test_m1_3_vector_firewall_sdk.py::test_spec_blocked_keywords_apply_to_embeddings_like_chat' \
  -q -p no:cacheprovider --runxfail
```

---

<a name="i-08"></a>
### I-08 — Responses-API MCP tool specs forwarded to the provider completely unscanned

> **MEDIUM** (corrected from HIGH) · CONFIRMED · §1.4 MCP guardrails · orig. `GAP-1.4-D`

#### Description

`_extract_tool_definitions_text([mcp_spec])` yields **literally 0 characters**, versus 76 for
a function-shaped tool. `grep '"mcp"'` across `main.py` + `responses_adapters.py` → **zero
hits**. So `server_label` / `server_url` / `allowed_tools` reach the model unscanned, and
`server_url` gets no URL-guard.

The harness-artifact hypothesis is **killed** by a control: the *identical* injection string
blocks in three sibling positions of the same tool object.

**Downgraded** because `proxy_responses` converts Responses→chat and calls `acompletion`
(`aresponses` has **0 production callers**), so a real provider would likely 400 an MCP tool
arriving on the chat wire. The gateway-side defect — making its own coverage contingent on an
upstream parser rejecting the payload — still stands.

#### Reproduction steps

```bash
.venv/bin/python -m pytest \
  'ai_mesh_gateway/tests/test_m1_4_context_guardrails_sdk.py::test_responses_mcp_tool_spec_should_be_scanned' \
  -q -p no:cacheprovider --runxfail
```

```bash
grep -n '"mcp"' ai_mesh_gateway/main.py ai_mesh_gateway/responses_adapters.py   # zero hits
```

---

<a name="i-09"></a>
### I-09 — `extra_body={"mcp_context": …}` silently discarded on both SDK surfaces

> **MEDIUM** (corrected from HIGH) · CONFIRMED · §1.4 · orig. `GAP-1.4-D5`

#### Description

The documented MCP-context channel is **never scanned, never telemetered, and never
delivered.** The OpenAI body normalizer reduces the body to `['messages','model']`; only
`agent_data` is restored at `main.py:5811`. `main.py:1513` is **dead code**.

Two in-code intent statements assert the opposite. Worse, `test_responses_adapters.py:70-75`
is a **passing** unit test that gives false end-to-end confidence in a contract broken one
layer later.

**Downgraded** because the behaviour is **fail-closed** — the payload is dropped, not
forwarded. Real impact: dead code + silent data loss + a misleading passing test.

#### Reproduction steps

```bash
.venv/bin/python -m pytest \
  'ai_mesh_gateway/tests/test_m1_4_context_guardrails_sdk.py::test_mcp_context_should_be_scanned_like_agent_data' \
  -q -p no:cacheprovider --runxfail
```

Verifier reproduced with **no harness at all** — the normalizer is a pure function:

```bash
.venv/bin/python -c "
from ai_mesh_shared.openai_request_normalizer import *  # noqa
"   # then normalize a body carrying mcp_context and inspect the surviving keys
```

---

<a name="i-10"></a>
### I-10 — Client envelope reports `action='allow'` / `threat_type='clean'` after the guard redacted the answer

> **MEDIUM** · CONFIRMED · §1.7 · orig. `M17-03`

#### Description

Delivered content is `'Customer SSN ***-**-9083 confirmed.'` — visibly redacted — while the
client-visible envelope reports:

```
zeroshield.action      = 'allow'
zeroshield.threat_type = 'clean'
scan_outcome           = 'clean'
detail                 = 'All security checks passed. No threats detected.'
```

and the `output_guardrail` trace stage reports `action='allow'`, `threat_type=None`,
`matched_patterns=None`. The `X-ZeroShield-Action` / `X-ZeroShield-Matched-Patterns` headers
are also dropped.

#### Root cause — **corrected during verification**

> ⚠️ The propagation at `main.py:9337-9362` is **correct**. The request never reaches it.

`tier2_execution_mode` defaults to `sync_pre_llm`, so guarding happens in
`_apply_output_guard_nonstream` (`main.py:7725`) — **after** `resp["zeroshield"]` was already
built — and that helper **never calls `_merge_output_enforcement_state`** (verified: 0 calls).

`pipeline_trace.py:1135-1153` carries a comment identifying this exact mislabel as a bug that
was fixed — **for the other output-guard path.**

**Partially mitigated, and the original finding's claim here was false:**
`pipeline_trace.final_action` **does** correctly report `redact`. It is not true that only
prose records the mutation.

#### Reproduction steps

```bash
.venv/bin/python -m pytest \
  'ai_mesh_gateway/tests/test_m1_7_output_guard_sdk.py::test_envelope_reports_the_action_that_actually_ran' \
  -q -p no:cacheprovider --runxfail
```

#### Impact

No data exposure. Corrupts the client-facing contract field that programmatic consumers key
on.

---

<a name="i-11"></a>
### I-11 — A valid key with an empty `org_slug` enumerates the shared LiteLLM catalogue

> **MEDIUM** (corrected from HIGH) · CONFIRMED · §1.5 · orig. `M15-03`

#### Description

`_resolve_models_for_request` (`main.py:13976`) applies the org filter only inside
`if org_slug and CONFIG_SYNC:` — **with no else-branch**. A valid key whose `org_slug` is
empty falls through to the **shared LiteLLM catalogue**, enumerating other tenants' deployment
names via `GET /v1/models`.

**Not a mock artifact:** `config_sync.py:546-596` genuinely merges **every** org's deployments
into one router, and `main.py:4785` boots every gateway with the full cross-org catalogue
resident.

**The empty-`org_slug` state is reachable:** `GatewayAPIKey.organization` is `null=True`, and
the `save()` backfill is a bare `try/except: pass`.

#### Reproduction steps

```bash
.venv/bin/python -m pytest \
  'ai_mesh_gateway/tests/test_m1_5_routing_governance_sdk.py::test_models_list_leaks_whole_router_when_key_has_no_org_slug' \
  -q -p no:cacheprovider --runxfail
```

#### Impact

Model-**name** disclosure only. BYOK keys stay separated by `_zs_org` tagging; inference
containment holds. An attacker cannot *induce* the org-less state. Latent fail-open.

---

<a name="i-12"></a>
### I-12 — Unregistered gateway does not scrub provider topology

> **MEDIUM** · CONFIRMED · §1.5 · orig. `M15-06` · **same root cause as I-02**

#### Description

The topology scrub at `main.py:9643-9660` (`pop("provider")`, `usage.pop(cost/is_byok)`,
`_scrub_upstream_passthrough`, client-facing model rewrite) lives **only in the connected
branch**. The `:7563` standalone branch returns at `:7793` without it.

```
CONNECTED=True  -> provider=False citations=False usage_cost=False model='claude-3-5-sonnet'
CONNECTED=False -> provider=True  citations=True  usage_cost=True
                   model='anthropic/claude-3-5-sonnet-20241022'
```

**Verifier narrowed and broadened this:** streaming already scrubs per-chunk at
`llm_router.py:1165-1178` — *but* a **third** unscrubbed path exists independent of
registration: the firewall-disabled passthrough at `main.py:6801-6820` leaks the same topology
on a fully connected, registered gateway.

#### Reproduction steps

```bash
.venv/bin/python -m pytest \
  'ai_mesh_gateway/tests/test_m1_5_routing_governance_sdk.py::test_m15_06_unregistered_gateway_still_scrubs_provider_topology' \
  -q -p no:cacheprovider --runxfail
```

#### Impact

Leaks provider/BYOK topology and **cost basis (margin)** to the tenant. Audience is
already-authenticated tenant callers; no cross-tenant exposure.

---

<a name="i-13"></a>
### I-13 — The scanner inspects the PRE-minimization prompt; inspected text and egressing text diverge

> **MEDIUM** · CONFIRMED · §1.4 · orig. `GAP-1.4-A3`

#### Description

`prompt_for_estimate` is flattened at `main.py:6548`; `minimize_context` runs at `:6662`; then
`:6665` does `prompt = prompt_for_estimate or ...` — **the truthy stale string always wins.**
The scanner therefore inspects a conversation the model never received.

Proof: `'turn 0 '` is **in** the captured scanner argument and **not in** the captured
upstream body.

#### Reproduction steps

```bash
.venv/bin/python -m pytest \
  'ai_mesh_gateway/tests/test_m1_4_context_guardrails_sdk.py::test_scanned_prompt_should_equal_the_egressing_prompt' \
  -q -p no:cacheprovider --runxfail
```

```bash
sed -n '6545,6552p' ai_mesh_gateway/main.py
sed -n '6660,6668p' ai_mesh_gateway/main.py    # the `or` that always wins
```

#### Impact

**Fail-safe against evasion** — detection operates on a strict superset, so nothing escapes
scanning. The real cost is **audit integrity**: prompt hash, `pipeline_trace.prompt_in`,
redaction payload and length accounting all describe a conversation that was never sent.
Directly causes I-24.

---

<a name="i-14"></a>
### I-14 — Responses `mcp_call` input items silently collapse to an empty message

> **MEDIUM** correctness / **LOW** security · CONFIRMED · §1.4 · orig. `GAP-1.4-D4`

#### Description

Reproduced with **zero stubs** (the adapter functions are pure):

```
mcp_call                -> {'role':'user','content':''}
INJECTION present in chat body? False
control: function_call_output -> {'role':'tool', content: <payload>}
```

The defect class is broader than reported: `mcp_approval_request`, `mcp_approval_response`,
`mcp_list_tools` and `custom_tool_call_output` **all collapse identically**.

#### Reproduction steps

Covered by the §1.4 suite; the adapters can be exercised directly since they are pure
functions:

```bash
.venv/bin/python -m pytest ai_mesh_gateway/tests/test_m1_4_context_guardrails_sdk.py -q -p no:cacheprovider -rx
```

#### Impact

**Fail-closed** — *not forwarded* is the mitigation, so the "neither scanned nor forwarded"
injection framing should be dropped. The genuine defect is silent data loss: HTTP 200, no
diagnostic, violating the file's own documented B12 no-silent-drop rule. Replay-store
escalation was ruled out.

---

<a name="i-15"></a>
### I-15 — RAG stages are not SDK-reachable, and SDK-delivered context gets chat-grade scanning

> **MEDIUM** · CONFIRMED · status `NOT-SDK-REACHABLE` · §1.2 · orig. `M12-11`

#### Description

`is_rag_request` has exactly three consumers, **all scanner arguments** — it never reaches the
pipeline stages. `_detect_rag_request` inspects only `body['rag_context']`,
`body['documents']` and system messages. **A `role=tool` result matches none of these**, so
`RAG_POISONING_PATTERNS` (8 patterns) is silently inactive on the primary agentic
indirect-injection delivery vector.

The verifier's probe is stronger than the finding's own evidence: **5 of 6** RAG-poisoning
payloads that RAG-grade scanning blocks are **allowed** under the chat-grade treatment the SDK
path actually applies.

#### Reproduction steps

```bash
.venv/bin/python -m pytest ai_mesh_gateway/tests/test_m1_2_query_firewall_sdk.py -q -p no:cacheprovider -rx
grep -rn 'is_rag_request' ai_mesh_gateway/ | grep -v tests   # 3 consumers, all scanner args
```

#### Impact

Query-grade inspection is **not** bypassable via the SDK (proven). But an entire detector tier
is off for `role=tool` content.

---

<a name="i-16"></a>
### I-16 — No `x-ratelimit-*` headers on any response

> **LOW** · CONFIRMED · §1.1 · orig. `M11-02`

**Description.** No quota headers on any response; observed headers are
`[content-length, content-type, x-request-id]`. `current_rpm` / `rpm_limit` **are** computed on
the allow path and discarded. OpenAI clients that pace on `x-ratelimit-remaining-*` are blind
and can only discover the ceiling by tripping a 429.

The verifier closed a methodological hole in the original test (the limiter was not explicitly
enabled) and the result held.

**Repro.**
```bash
.venv/bin/python -m pytest \
  'ai_mesh_gateway/tests/test_m1_1_ingress_sdk.py::test_f3_ratelimit_headers_are_advertised' \
  -q -p no:cacheprovider --runxfail
```

**Impact.** None to security. Reactive backoff is fully correct (`Retry-After` parseable); only
proactive pacing is unavailable. **Note: no ZeroShield doc promises these headers** — this is
OpenAI-parity, arguably a feature request rather than a defect.

---

<a name="i-17"></a>
### I-17 — `enforcement_mode=monitor` reports `zeroshield.action='allow'` for a 1.0-confidence injection

> **LOW–MEDIUM** (corrected from MEDIUM) · CONFIRMED · §1.2 · orig. `M12-03`

**Description.** In monitor mode a 1.0-confidence prompt-injection detection is byte-identical
on the `action` field to genuinely clean traffic.

**Root cause — corrected.** The monitor branch is **correct** (`resolve_and_enforce` returns
`monitor`); the value is discarded at `main.py:4058`:

```python
action = scan_verdict.action if scan_verdict.action in ("allow","flag") else "allow"
```

which reads the **raw** verdict. The original claim that detection is recoverable only from
`threat_type`/`confidence` is **false** — `pipeline_trace.final_action == "monitor"` is present
and correct.

**Aggravator the finding missed:** the monitor-mode logging branch (`main.py:7405-7409`) is
structurally **dead code**, so operator telemetry also reports `action="allow"`,
`threat_type=""`, `risk_score=0.0`.

**Repro.**
```bash
.venv/bin/python -m pytest \
  'ai_mesh_gateway/tests/test_m1_2_query_firewall_sdk.py::test_monitor_mode_action_field_distinguishes_flagged_traffic' \
  -q -p no:cacheprovider --runxfail
```

---

<a name="i-18"></a>
### I-18 — Context minimization inert by default

> **LOW** (corrected from MEDIUM) · CONFIRMED · §1.4 · orig. `GAP-1.4-A`

**Description.** The context budget defaults to `0 = unlimited`, stated as deliberate intent in
**three independent source locations**. The verifier confirmed the real value is 0 via an
unstubbed `load_config()` and confirmed no `.env` or compose file sets it.

The finding's headline assertion (`CONFIG.get(...) == 0`) is **stub-tautological** and should
not be cited as evidence.

**Impact.** No security bypass — the scanner inspects a strict superset. **A product-defaults
finding, not a vulnerability.**

**Repro.**
```bash
.venv/bin/python -m pytest ai_mesh_gateway/tests/test_m1_4_context_guardrails_sdk.py -q -p no:cacheprovider -rx
```

---

<a name="i-19"></a>
### I-19 — Size/DoS rejection reports `error.code='content_filter'`, identical to an unsafe-content block

> **LOW** · CONFIRMED · cross-cutting conformance · orig. `X-CONF-02`

**Description.** Clean boundary through the stock SDK: 9,001 chars allowed; 10,101 chars →
`BadRequestError code='content_filter' param=None` — **byte-identical to an injection block.**
`e.body` is the *nested* error object, so `category` is unreachable from the exception. A
client cannot distinguish "your input was too large" from "your input was malicious".

**Not correct-by-design:** the same gateway already emits `413 / embedding_input_too_large` on
`/v1/embeddings`.

*Correction:* one of the three originally-cited probes (960-char lorem) is **misclassified** —
it trips the separate `_is_repetitive` heuristic, for which `content_filter` is defensible.

**Repro.**
```bash
.venv/bin/python -m pytest \
  'ai_mesh_gateway/tests/test_m1_x_protocol_conformance_sdk.py::test_oversized_inputs_are_bounded_fast_4xx_not_5xx_or_hang' \
  -q -p no:cacheprovider
```
(This test **passes** — it pins current behaviour. The defect is the taxonomy choice it records.)

---

<a name="i-20"></a>
### I-20 — `n>1` silently clamped to 1

> **LOW** · CONFIRMED · cross-cutting · orig. `X-CONF-03` · **duplicate of tracked `P2-N-CHAT-clamp`**

**Description.** The security half is genuinely sound: `n=1` is what the router receives, so
`choices[1..]` are never generated and **cannot leak unscanned**. The defect is purely the
missing client signal — no `n_clamped` field, no header, despite the gateway signalling many
other mutations via `X-ZeroShield-*`.

**Under-scoped:** `max_tokens` is silently clamped identically at `main.py:6558-6560`.

**Repro.**
```bash
.venv/bin/python -m pytest \
  'ai_mesh_gateway/tests/test_m1_x_protocol_conformance_sdk.py::test_n_gt_1_is_silently_clamped_at_the_boundary' \
  -q -p no:cacheprovider
```

---

<a name="i-21"></a>
### I-21 — `GET /v1/models` is org-scoped but never key-scoped

> **LOW** · CONFIRMED · §1.5 · orig. `M15-05`

**Description.** `auth_ctx.allowed_models` is never consulted by the catalogue endpoint.
Containment is intact and **independently proven** (`test_key_entitlement_is_a_hard_routing_filter`
— `routing_allowed_models` hard-filters at `main.py:5948`), so this is over-disclosure of the
org fleet, not a bypass.

Worth noting: `retrieve_model` returns *"does not exist or you do not have access to it"* while
performing **no access-scoping** — an inaccurate error contract.

**Repro.**
```bash
.venv/bin/python -m pytest \
  'ai_mesh_gateway/tests/test_m1_5_routing_governance_sdk.py::test_models_list_ignores_per_key_entitlement' \
  -q -p no:cacheprovider --runxfail
```

---

<a name="i-22"></a>
### I-22 — Nested `agent_data` context scanned only to depth 6

> **LOW–MEDIUM** (corrected from MEDIUM) · CONFIRMED · §1.4 · orig. `GAP-1.4-C`

**Description.** `_collect_nested_strings` caps recursion at `_depth > 6`. The cap is exact and
reproduced stub-free: the payload vanishes at depth 7; `n_strings` saturates at 7.

`agent_data` is **not** in `_PASSTHROUGH_PARAMS`, so the payload never reaches the model — **no
live injection exploit.** The real loss is a silent detection/compliance blind spot with **no
truncation telemetry**: `grep -c "LOG\|warn\|telemetry\|metric"` over the function → `0`.

Scope is broader than reported: the same capped helper is reused for RAG metadata folding at
`main.py:1645` and `:11959`.

> ⚠️ **Caution for re-runners:** a probe may print `egressed_upstream=True`. **That is a mock
> artifact** — the harness replaces `acompletion` wholesale, and the real allowlist filter
> lives inside it.

**Repro.**
```bash
.venv/bin/python -m pytest \
  'ai_mesh_gateway/tests/test_m1_4_context_guardrails_sdk.py::test_deeply_nested_agent_context_injection_should_be_blocked' \
  -q -p no:cacheprovider --runxfail
```

---

<a name="i-23"></a>
### I-23 — Injection inside a `file_search` retrieval filter is neither scanned nor dropped

> **LOW** (corrected from MEDIUM) · CONFIRMED · §1.3 · orig. `M13-2`

**Description.** `_extract_tool_definitions_text` folds only `name`/`description`/`parameters`.

**Downgraded** because `filters` is a metadata comparison predicate evaluated by the retrieval
backend — **not model-facing text** — so it is not a prompt-injection delivery vector.

**Scope corrected:** the hole is generic, not filter-specific — `ranking_options`,
`instructions`, and even an invented `some_vendor_key` all pass through. **Fixing only
`filters` would be incomplete.**

**Repro.**
```bash
.venv/bin/python -m pytest \
  'ai_mesh_gateway/tests/test_m1_3_vector_firewall_sdk.py::test_spec_injection_inside_file_search_filter_is_scanned' \
  -q -p no:cacheprovider --runxfail
```

---

<a name="i-24"></a>
### I-24 — A configured context budget cannot rescue an oversized history from the DoS block

> **LOW** · CONFIRMED · **corollary of I-13, not a separate defect** · orig. `GAP-1.4-A4`

**Description.** The causal claim is non-vacuous: pre-prune 33,307 chars → post-prune 906
chars, well under the 10,000 cap, so a correct re-flatten **would** pass. But no spec claims a
budget raises the size limit, the behaviour is fail-closed, and the root cause is **identical
to I-13**.

> **Tracking this as a second MEDIUM double-counts one defect.** Fix I-13 and this resolves.

---

<a name="i-25"></a>
### I-25 — "Human review" is named as a §1.7 action but does not exist in code

> **INFO** · spec/implementation mismatch · observed and never filed during the campaign

**Description.** §1.7 names *human review* as an output-guardrail action. `_VALID_OUTPUT_ACTIONS`
is `{block, redact, rewrite, flag, allow}`. There is no human-review action.

**Repro.**
```bash
grep -rn '_VALID_OUTPUT_ACTIONS' ai_mesh_gateway/ | grep -v tests
```

**Action.** Either implement it or remove it from the product spec — same principle as I-03.

---

## 6. PLAUSIBLE — real observations, conclusions NOT established

**Do not action these as filed.** Each reproduces, but the verifier could neither confirm the
stated conclusion nor refute the observation.

| ID | Observation | Why not confirmed |
|---|---|---|
| **P-01** `M12-04` | *"Ignore YOUR previous instructions"* (one intervening possessive) evades detection | Reproduces at Tier-1 **outside the harness**, so it is real. But gathered with **Tier-2 force-disabled**, and Tier-2 is on by default in code and in the deployed `.env`; the Bedrock system prompt explicitly enumerates *"role-play framing"*. The stated root cause ("signatures are contiguous") is **contradicted by source** — `_fuzzy_subsequence_match` handles insertion; the match was rejected by a documented FP-avoidance rule. Controls show `"disregard your instructions"` and `"ignore the previous instructions"` both **block** — only possessives evade. **Correct framing: a two-determiner anchor-list gap, backstopped by Tier-2.** MEDIUM→LOW. Repro: `pytest '…test_m1_2_query_firewall_sdk.py::test_roleplay_framing_with_intervening_words_is_blocked' --runxfail` |
| **P-02** `M13-1` | `file_search` `vector_store_ids` forwarded verbatim, bypassing §1.3 collection ACL | Mechanical claim fully reproduced; **security claim is not**. `aresponses` — the only path where `file_search` actually executes retrieval — has **zero production callers**; `/v1/responses` is a format adapter over `proxy_chat`. litellm's `file_search` retrieval is a *proxy-server* feature, not part of the SDK `acompletion` path. Deployments are per-org BYOK with org-qualified `{org}::{model}` keys, so a `vs_` id resolves in the caller's **own** provider account. **Correct framing: unvalidated provider-native tool passthrough (defense-in-depth), not a live cross-tenant bypass.** HIGH→LOW. Repro: `pytest '…test_m1_3_vector_firewall_sdk.py::test_spec_file_search_vector_store_should_be_tenant_checked' --runxfail` |
| **P-03** `GAP-1.4-B` | Field-level clearance redaction implemented but never invoked on the SDK path | Mechanically true and *not* a harness artifact. But the stated root cause — *"missing third argument at the single call site"* — implies a one-line fix, and **there is no clearance value in existence to pass.** `AuthContext.__slots__` has no sensitivity field; the only org candidate is documented at `main.py:2595` as *"synced but never read"*, with an explicit note that enforcing it would 403 every org's default traffic; the only per-request value is **client-controlled**. `generator_stage.py:224-229` wires the same capability correctly with a *policy-derived* clearance, showing deliberate scoping. HIGH→LOW. Repro: `pytest '…test_m1_4_context_guardrails_sdk.py::test_field_level_redaction_should_apply_to_structured_content' --runxfail` |
| **P-04** `M15-01` | `response.model` reports the requested, not the serving, model after a reroute | Mechanism reproduces and is **not** a harness artifact (production log: `Chat routing selected model: claude-3-5-sonnet (requested=gpt-4o-mini)`). But the impact premise is **false**: the served identity is present **twice** on the SDK-visible surface — five `X-ZeroShield-*` headers and `zeroshield.routing.{routed_model,selected_model,rerouted}`, all explicitly whitelisted by `_redact_for_client_response`. §1.5 promises OpenAI-schema compatibility, not that `model` names the serving model. Counter-argument for current behaviour: a client round-tripping `response.model` would silently pin to a model it never requested. HIGH→LOW; reclassify GAP→**design note**. Repro: `pytest '…test_m1_5_routing_governance_sdk.py::test_response_model_field_reports_the_model_that_actually_served' --runxfail` |

---

## 7. REFUTED — not defects, recorded so nobody re-files them

Each was reproduced *as a test result* and then shown to be a **harness artifact**, a misread
of the source, or documented design.

| ID | Claimed | Why it is not a defect |
|---|---|---|
| `M16-01` HIGH | Kill-switch reroute invisible to the SDK; `model` reports the killed model | `response.model == "gpt-4o-mini"` was the **test stub's hardcoded string** (`test_openai_sdk_compat.py:92`), not gateway output — a control run with **no** kill-switch produced the identical value. And `zeroshield.routing` **does** exist: on the connected path the same reroute yields `selected_model='gpt-4o-alt'`, `rerouted=True`, `decision_source='kill_switch'`. The harness's `AGENT_ID=None` never reached that code. |
| `M17-04` HIGH | Configured `block` downgraded to `redact` under `stream=true` | The fixture applied `block` to the **OutputGuard constructor config only** while `CONFIG_SYNC.get_config` returned unmodified `TEST_CONFIG`. `main.py:3280` passes `org_slug or "default"`, so **the two transports were literally given different policies.** With both set to block, the stream hard-blocks. Secondary defeater: `status_code == 400` on a `StreamingResponse` is structurally unsatisfiable. `enforcement.py:539-546` documents the block→redact coercion as **removed**. |
| `GAP-1.4-D3` HIGH | Per-key `mcp_allowed_tools` bypassable by declaring tools inline on chat | The `tool_calls` asserted on are the stub's **unconditional hardcoded `get_weather`** — not even one of the declared tools. The gateway never dispatches a declared chat tool. A `chat.completions` tool entry is a *client-side* schema; if the client then calls MCP, `_tool_allowed_by_key` applies normally. No privilege gained. |
| `M16-02` MEDIUM | Standalone deployments never reach the per-model rate limiter | Asserted a `rate_limit_rpm` that **cannot exist** standalone. The fixture MagicMocked a self-contradictory state (control plane absent for registration, present for model config). The limiter is *vacuous* there, not bypassed. The collateral allowlist claim is also wrong — model isolation runs at `main.py:5914/5986/5998-6030`, **before** the short-circuit. |
| `M16-03` MEDIUM | Per-model rate limit fails OPEN while kill-switch fails CLOSED | Behaviour is real but violates no spec claim and is **documented deliberate intent in three source locations**, including a note that it was *changed away from* fail-closed. The asymmetry is correct engineering: an emergency integrity stop and an availability/cost cap have opposite failure economics. Residual nit: fail-open allows are labelled `result="allowed"` in Prometheus. |
| `M16-11` LOW | BYOK per-model credential selection "not SDK-observable" | The verifier **built the test in one sitting** by patching module-level `litellm.acompletion` instead of MagicMock-ing `LLM_ROUTER`, and proved the control **works**: `gpt-4o-mini → sk-ACME-PRIMARY`, `gpt-4o-alt → sk-ACME-ALT`, foreign `sk-EVIL-PRIMARY` never selected. **Reclassify NOT-SDK-REACHABLE → ENFORCED.** A test-harness gap, not a product finding. |
| `M17-02` MEDIUM | `output_credential_action` is a dead knob | Generalized from a single payload (AWS key pair) that the credential detector **does not match at all**. Stated root cause (detector order) is wrong: selection is `max` by `ACTION_PRIORITY`. With a real credential (postgres connection string) **all five actions are distinguishable end-to-end**, including `block → 400`. Self-contradicted by `test_private_key_block_and_redact` 28 lines below, which passes. |
| `M17-05` LOW | Hallucination inspection structurally inert without RAG context | Probe text matched **zero of the eight** shipped `HALLUCINATION_PATTERNS` — there is no "overconfidence" detector. The no-context branch scores `risk = pattern_score * 0.80` against a 0.45 threshold, crossable with four markers. With a detector-matching fabrication: `block → 400`, `rewrite → "I cannot verify that claim."`, `flag → delivered`. Residual (INFO): no detector for bare over-confident assertion. |
| `M13-3` MEDIUM | 40-char AWS secret access key embedded verbatim | The claimed-missing pattern exists **twice** (`patterns.py:711`, `:790`); the finding mislocated it in `scanner.py`. All three canonical forms are redacted end-to-end. The "chat leaks it too" corollary is a harness artifact. The only miss is an *uncued* 40-char blob in prose, documented at `patterns.py:787-790` as a deliberate low-FP gate. **Re-reports an already-fixed bug.** |
| `M12-05` LOW | `pipeline_trace` policy stage reports `matched_rules=[]` | The values **are** propagated (spy pre-scrub: `matched_rules=['no_competitor_talk']`). `_scrub_trace_for_client` deliberately blanks them: *"so a blocked caller cannot use the response as an oracle for which rule matched."* **Inverse finding worth tracking (LOW):** the scrubber leaks those same names in `guard_reason` free text, defeating its own anti-oracle purpose. |

> **Pattern across all ten:** an agent observed a mock's boundary and wrote it up as a product
> property. **M1.6 had 4/4 findings refuted; M1.7 had 3/5 refuted.** Those two files'
> *ENFORCED* claims come from the same fixtures and deserve the same confidence discount —
> nobody applied it.

---

## 8. Bypass-channel analysis

### Channels positively verified CLOSED

| Channel | Evidence |
|---|---|
| Passthrough surfaces (`/v1/files`, `/v1/batches`, `/v1/images`, `/v1/audio`, `/v1/fine_tuning`) | Hard **404 across 9 method/path combinations with zero upstream invocation**. Auth runs ahead of routing, so anonymous probing cannot enumerate implemented surfaces. The "blind proxy to a real provider" hypothesis is **disproven**. |
| Header / kwarg tenant spoofing | 8 spoof headers + SDK `organization=` kwarg all inert — and **self-validating**: an ASGI header-spy first asserts each spoof was *delivered*, so a pass means the gateway ignored it. Without that spy the section would have passed vacuously. |
| Encoding evasion (base64, zero-width, homoglyph, leetspeak) | All four blocked via Tier-0.5 deobfuscation, `conf=0.95`. |
| Split-payload evasion | Blocked across two user messages *and* mid-word across two content-parts of one message. |
| Non-final / system / `role=tool` message positions | All scanned; injection in any position blocks. |
| **Streaming chunk-boundary secret splitting** | Masked at **1 character per SSE frame** over a real TCP socket, including a 303-char key forcing a buffer-limit flush mid-secret, and a secret abutting stream end. **The campaign's designated highest-risk path — it held.** |
| Kill-switch reroute privilege escalation | Fallback re-validated against live disable state, model-state isolation, caller allowlist, chat-capability. All four negative cases 503 with empty upstream. |
| Client `enable_routing=false` | Honoured with no privilege check, but the FIX-1.5a gate then **refuses** 403 rather than serving on a non-compliant model. |
| `rag_context` / `documents` chat fields | Dropped, not relayed. Silent-drop is a UX issue, not a wire leak. |
| BYOK per-model / per-tenant credential selection | Each model receives its own key; a same-named foreign tenant's key never selected. |
| SDK auto-retry laundering a 429 into a 200 | Does not occur; `max_retries=2` against a persistent limit still raises. |

### Channels OPEN

1. **`tool_calls.function.arguments` output egress (I-01, CRITICAL).** The single most exploitable channel found.
2. **Surface asymmetry: `/v1/embeddings` is a strict subset of `/v1/chat/completions`** — lacks per-key TPM (I-06), blocked-keywords (I-07), and the policy engine. **This is a repeating architectural pattern, not three separate bugs.** The embeddings handler was built by copying a subset of the chat ingress chain; every future chat-path control is at risk of the same omission.
3. **The `AGENT_ID=None` standalone path (I-02, I-12, CRITICAL).** Skips §1.5 governance, org-model ownership validation, per-model limits, the final allowlist re-check, the circuit breaker, and provider-response scrubbing — while serving 200 OK.
4. **Per-key action scoping does not exist (I-03).** Any authenticated key reaches every OpenAI-shaped surface.
5. **Structural nesting depth > 6 in `agent_data` (I-22).** Detection blind spot only; silent, no truncation telemetry.
6. **Unrecognized Responses input-item types collapse to empty messages (I-14).** Fail-closed for security; silent data loss for correctness.
7. **Unvalidated provider-native tool passthrough** (`vector_store_ids`, `filters`, `ranking_options`, arbitrary vendor keys). Inert on the chat wire today; operative for any provider or proxy tolerating unknown tool fields.
8. **`role=tool` results receive chat-grade, not RAG-grade, inspection (I-15).** 5 of 6 probe payloads that RAG-grade blocks are allowed.

---

## 9. Limitations — what was NOT proven

**Read this before acting on any ENFORCED claim in this report.**

> This campaign tested the OpenAI-SDK surface of a gateway configured **without a control
> plane** (`AGENT_ID=None`), **with Tier-2 scanning disabled**, and with the rate limiter,
> policy engine, circuit breaker, telemetry and audit sinks **nulled**. Five of seven spec
> bullets were tested on **non-streaming requests only** and with a **single tenant**. Under
> this configuration `main.py:7563` short-circuits the request, so roughly **1,900 lines of
> the chat pipeline never executed** — including dynamic routing, per-model rate limiting,
> provider-response scrubbing, and a second non-streaming output-guard implementation.

**A. The `AGENT_ID=None` default undermines six of eight files.** `T._make_sdk_app` sets
`AGENT_ID = None` and `backend_url = ""`. Only 1.5 and 1.6 override it — and only because
their authors hit the wall and diagnosed it. Two consequences nobody chased:

- **§1.7's coverage claim is false as written.** It claims to have exercised "the entire
  output-guard stage (`main.py:8460-8994`)". It did not — it exercised
  `_apply_output_guard_nonstream` at `main.py:7725`, **a different implementation inside the
  standalone branch.** There are two non-streaming output-guard implementations and **the
  connected-path one has zero SDK coverage.** I-10's verdict proves they diverge. **Every
  §1.7 verdict — including CRITICAL I-01 — is a statement about the *other* implementation.**
- **`M16-01` and `M16-04` were refuted specifically because of this.** Setting `AGENT_ID`
  revealed `zeroshield.routing`, `selected_model`, `rerouted` and five `X-ZeroShield-*`
  headers all exist. Two HIGH findings evaporated. **Nobody asked the same question of 1.1,
  1.2, 1.3, 1.4 or 1.7.**

**B. Tier-2 is dead everywhere.** `TEST_CONFIG["tier2_enabled"] = False`; no file overrides it.
Production default is `ENABLE_TIER2=true` (`.env:57`, both compose files). Every detection
result measures Tier-1 + Tier-0.5 against a product that ships with two tiers. P-01 was
downgraded for exactly this reason — **but the same objection applies to every
"blocked"/"not blocked" cell in 1.2, 1.3 and 1.4.** `Tier2UnavailableStrict` fail-closed,
`tier2_execution_mode` sync-vs-async, and the async `tier2_post_scan` path were never touched.

**C. §1.3 has effectively zero coverage of its actual capability.** Grep across all eight
files for `chroma|pinecone|milvus|namespace`: **4 hits, all prose comments in one file.** Grep
for `"/v1/rag`: **zero — nobody ever issued a request to a RAG endpoint.** What was tested:
`/v1/embeddings` PII masking, `/v1/files` 404s, `file_search` passthrough. **Collection ACL,
namespace isolation, cross-tenant vector leakage and embedding anomaly detection received
literally no test.** The one HIGH finding was downgraded to LOW. Weakest module by a wide
margin.

**D. There is no genuine cross-tenant test anywhere in the campaign.** No file seeds two API
keys under two different non-empty `org_slug`s and attempts A→B. §1.1's tenant-isolation suite
spoofs *headers* with a single key — that proves headers are inert, not that org scoping
holds. The canonical `_auth_payload()` ships `org_slug=""`, **precisely the degenerate state
I-11 showed fails open.** "Tenant isolation" (1.1) and "cross-tenant leakage" (1.3) are both
marked ENFORCED **on evidence that does not establish them.**

**E. Zero of ~41 ENFORCED findings were adversarially verified.** Verdicts ran only against GAP
and NOT-SDK-REACHABLE findings. Of the four NOT-SDK-REACHABLE claims that *were* reviewed,
**one (`M16-11`) was refuted outright** — the capability was testable and working. That is a
**25% error rate on the only non-GAP sample anyone checked**, and it is the best available
estimate for the unchecked ENFORCED set.

Specific ENFORCED claims weak on their own terms:
- `M11-E1` "tenant isolation enforced" — one key; the `_zs_org_slug` assertion is against a MagicMock body *above* the layer that actually strips vendor fields. Title overclaims.
- `M15-E1` "cost/latency/risk routing genuinely implemented" — strongest ENFORCED result (binds real `LLMRouter` scoring), but the router is a MagicMock with real methods grafted on and **the adjudicator (production default: ON) was disabled.** Proves fallback arithmetic, not shipping behaviour.
- `M16-05/06/07` — best-evidenced block, but from the file whose **four findings were all refuted**.
- `M17-07` "trace carries sanitized text" — the same envelope sweep returns `['SSN']` for the I-01 case; the finding concedes this in its own caveat.
- `X-CONF-01` "error taxonomy fully conformant" — asserted with `RATE_LIMITER=None` and `POLICY_SYNC=None`, so gateway 429s and policy 403s are **absent from the matrix.** Supportable claim: *"conformant across the eight paths reachable with the limiter and policy engine removed."*

**F. Three confirmed findings have root causes that would misdirect a fix.** I-01 (real cause
`main.py:8510`, not the neutralizer), I-04 (real cause the router signature, not the digit
backstop), I-10 (real cause `_apply_output_guard_nonstream`, not the propagation at 9337).
**Fixes must be written against the corrected causes recorded above.**

**G. Streaming is untested for five of seven bullets.** `stream=True` appears only in 1.6 (3
tests), 1.7 (5), 1.x (6). **1.1, 1.2, 1.3, 1.4 and 1.5 have zero streaming tests** — despite
the campaign surfacing stream/non-stream divergence twice.

**H. Concurrency is shallow.** 24-way in-process over ASGITransport only. Not proven: real
sockets, multi-worker/multi-process, Redis-backed state contention, or the rate limiter's Lua
`INCR+EXPIRE` atomicity (fakeredis has no Lua engine; 1.6 hand-wrote a `_LuaShim`).

**I. Known hygiene defect in the shipped test files.** Some `xfail` reason strings still read
`confirmed 2026-07-19` for findings that were later **refuted** (`M16-01`, `M17-04`). Correct
these strings before anyone reads them as ground truth.

---

## 10. Recommended next steps, prioritized

### Tier 0 — test infrastructure, before any product fix

1. **Re-run 1.1, 1.2, 1.3, 1.4 and 1.7 with `AGENT_ID` set and a non-empty `backend_url`.** A
   fixture parameter, not new tests. Validates or invalidates **~41 ENFORCED claims** and
   exercises `main.py:7796-9700` — thousands of lines with zero SDK coverage. **Highest value
   in the campaign by an order of magnitude.**
2. **Add a real two-tenant fixture.** Two keys, two non-empty `org_slug`s, two catalogues, two
   BYOK deployments. Then test A→B for: models, routing, kill-switch, rate bucket, vectors,
   Responses store. **This is the entire cross-tenant threat model, currently untested.**
3. **Add a telemetry/audit-capturing harness.** One fixture unlocks four spec bullets currently
   untestable by construction: intent classification (1.2), incident logging (1.7), compliance
   tagging (1.4), and the operator attribution channel.
4. **Correct the stale `confirmed` xfail reason strings** for the refuted findings (limitation I above).

### Tier 1 — product fixes, in severity order

5. **I-01 (CRITICAL).** Pass the full delivered envelope as `delivered_text` at `main.py:8510`
   and `:2029`. Fix the docstring premise. Regression-test every posture × every channel.
6. **I-02 (CRITICAL).** Gate the §1.5 fast path on routing-catalogue availability, not
   `AGENT_ID` — mirroring the existing fix at `main.py:6846-6852`. Add a degraded-mode test.
7. **I-03 (HIGH).** Enforce `allowed_actions`/`denied_actions`, **or remove them from the API
   docs and UI.**
8. **I-04 (HIGH).** Add a `redaction_config` channel to `LLMRouter.acompletion`. Until then,
   **stop reporting `action='redact'` for masks that were discarded.**
9. **I-06 + I-07 as one work item.** Add an architectural test asserting ingress-control parity
   between `proxy_chat` and `proxy_embeddings`.
10. **I-05, I-10, I-17 as one honesty defect.** All three are the same class: the enforcement
    that ran is not what the envelope reports.

### Tier 2 — coverage never reached

11. **Enable Tier-2 with a stubbed-but-realistic Bedrock client.** Settles P-01 and exercises
    `Tier2UnavailableStrict`, `tier2_execution_mode`, `tier2_post_scan`, circuit-breaker-OPEN.
    **Every detection verdict in this campaign is currently half-evidenced.**
12. **Test the connected-path output guard (`main.py:8460-8994`).** Not a re-run — a genuinely
    different implementation. **Treat every §1.7 result as provisional until this is tested.**
13. **Test `/v1/rag/query` and `/v1/vector/*`** under uvicorn with `lifespan="on"` and a fake
    vector client. This is where retriever/ranker/generator guards, collection ACL, namespace
    isolation, custody/provenance and embedding anomaly detection live — **the bulk of spec
    bullets 1.2 and 1.3, currently at zero.**
14. **Add streaming coverage to 1.1–1.5.**

### Tier 3 — hygiene

15. Resolve I-25 (human review): implement or remove from spec.
16. I-19 error-taxonomy split; I-16 quota headers; I-20 clamp signalling.
17. Track the inverse `M12-05` finding: `guard_reason` free text leaks the rule names that
    `_scrub_trace_for_client` deliberately blanks, defeating its own anti-oracle purpose.

---

## Appendix A — Full xfail inventory

```bash
cd /home/contact_cyberultron_com/AI_Mesh_Firewall/gateway
.venv/bin/python -m pytest ai_mesh_gateway/tests/test_m1_*.py -q -p no:cacheprovider -rx
```

Prints all 27 xfail reason strings — each is a self-contained finding record with captured
evidence.

## Appendix B — Verification performed for this report

- All 8 suites re-run independently: **221 passed, 27 xfailed, 0 failed, 0 errors**
- Baseline `test_openai_sdk_compat.py` re-run: **46 passed, 2 xpassed** (unchanged)
- `git status` confirms **only test files added**; zero production files modified
- All 25 published test node IDs validated via `--collect-only`
- `--runxfail` repro verified end-to-end for I-01 and I-02 (both `FAILED` as documented)
- I-01 mechanism confirmed directly in source at `main.py:1873-1885` (content-gated neutralizer)
- I-02 short-circuit confirmed directly in source at `main.py:7563`
- I-04 mechanism confirmed at `llm_router.py:768-789` (`redacted_content` as signal only)
- `M16-01` refutation confirmed: `model` string is hardcoded at `test_openai_sdk_compat.py:92`
