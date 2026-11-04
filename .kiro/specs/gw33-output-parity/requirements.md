# Requirements Document

## Introduction

This feature implements the locally-buildable half of card **GW33 — Output guardrail parity** of the AI Mesh Firewall v3 backend rewrite (`gateway_v2/`), as defined in `docs/AI_MESH_MASTER_RUNBOOK_v3.1_CLEAN.docx` (card GW33, live tests L33-1..L33-3), `docs/AI_MESH_MASTER_RUNBOOK_v3_BACKEND_REWRITE.md`, and `docs/AI_MESH_RUNBOOK_v3.1_PROGRESS.md` (status line: *"Not started. The GW26 publisher omits `rewrite` and `model_downgrade` and names them unsupported. It does not implement REWRITE."*).

The card objective, verbatim: *"Restore v1's output guardrails on v2."* Implementation work named by the card: *IP and infrastructure leakage; exfiltration channels (zero-click images, links, HTML sinks, DNS labels); hallucination and grounding on a local model; human-review flag; REWRITE through a local model or a declared template (today REWRITE is mapped to REDACT); canary tokens checked on output. `Category.EXFIL` exists but nothing produces it.* Dependencies: GW09, GW10, GW12. Required by GW21 and GW23.

The card text is the sole source of scope. v1 behaviour (`gateway/ai_mesh_gateway/output_guard.py`, `canary_tokens.py`, `secure_streaming.py`, `rag_pipeline/grounding_guard.py`, `patterns.py::detect_ip_leakage`) is the parity reference, not a design to copy. This spec describes WHAT must hold; `design.md` decides HOW.

### What is already shipped (reused, NOT rebuilt)

- **The one resolver.** `resolve/resolver.py::resolve(findings, plan, phase, surface, stage) -> Decision` is the single disposition authority. `resolve/decision.py::_action_to_disposition` maps `Action.REWRITE` to `Disposition.REDACT` as an interim; this card replaces that interim.
- **Deterministic detectors and byte-verified redaction (GW09).** `detect/deterministic/{canonicalize,matcher,patterns,detectors,redactor}.py`: one canonicalisation pass, `Finding[]` with original-byte `Span`s, `redact()` + `verify()`.
- **Semantic/guard detection (GW10/GW08).** `detect/semantic/*`, `detect/guard/*` (`GuardBackend`, `Budget`), and the `Detector` protocol in `detect/base.py`.
- **Output enforcement glue (GW12b/GW13).** `egress/output_guard.py` (`OutputResolver`, `apply_decision`, `enforcing_output_rules`, `OutputBlocked`), `egress/stream.py` (INCREMENTAL release loop with bounded holdback and force-release trade-off), `egress/strict.py` (STRICT_WITHHOLD), `egress/holdback_tradeoff.py` (egress-side mirror of `detect.holdback.TRADE_OFF`, pinned by a drift test).
- **Request-side byte verification (GW11).** `dispatch/verify.py::apply_and_verify` and the `RewriteBackend` seam in `dispatch/authorized.py` (a local-model seam, never a provider).
- **Output channel coverage (C21).** `domain/channels.py` marks every text-bearing OUTPUT field `scanned=True` (`output_content`, `output_refusal`, `output_reasoning`, `output_tool_arguments`, `logprobs`, `structured_output`).
- **Plan vocabulary.** `domain/plan.py`: `Action.REWRITE` exists; `Category.EXFIL` exists in `domain/category.py`; `plan/compiler.py::SUPPORTED_ACTIONS = {ALLOW, FLAG, REDACT, BLOCK}` currently excludes REWRITE.
- **The GW26 publisher and console map.** `control/ai_mesh_control/core/firewall_v2_publish.py` (`_UNSUPPORTED_ACTIONS = {"rewrite", "model_downgrade"}`), `firewall_v2_map.py` (`FIELDS`, with `output_ip_leakage_*` mapped and `factuality_check_enabled`, `hallucination_grounding_*`, `output_hallucination_action` marked `unsupported`), and `firewall_config_serializer.py::validate` (refuses `rewrite` at save).

### Assumptions (conservative, fail-closed; ambiguity resolved here, never by blocking the work)

- **A-1.** "REWRITE through a local model or a declared template": the **declared template** is the mandatory baseline (deterministic, pure, byte-verifiable). A local-model rewrite is an optional backend behind the existing local-seam pattern; whenever it is absent, unavailable, over budget, or its output fails re-inspection, the result falls back to the template, and if the template cannot be applied and verified the result is BLOCK.
- **A-2.** New threat categories needed by this card are added to the single `Category` taxonomy with exactly one spelling each (working names `ip_leak`, `hallucination`, `canary`); `design.md` fixes the final spellings. `Category.EXFIL` is reused for exfiltration channels and is not renamed.
- **A-3.** `model_downgrade` is a **routing/model-selection** action (GW28/GW29), not an output-guard action. GW33 does not implement it (see Requirement 5).
- **A-4.** GW27 is built in parallel and retires `factuality_check_enabled`, `hallucination_grounding_mode`, `hallucination_grounding_threshold`, `hallucination_grounding_model`, and `output_hallucination_action` by name to GW33, and keeps `rewrite` refused-at-save on every output action field. GW33 builds those semantics (it does not re-retire them) and, once its own gate is green, supersedes GW27's refusal for `rewrite` (Requirement 9).
- **A-5.** Human review in v2 is an **audit/telemetry flag** carried on the decision record and published through the existing audit path. The review queue UI and `HumanReviewItem` model are v1 control-plane artefacts and are not rebuilt here.
- **A-6.** Live L33-1..L33-3 and the staging/Docker gate are out of this step and are **deferred, not run, and not claimed** (Requirement 11).

## Glossary

- **Resolver**: `resolve/resolver.py::resolve`, the single pure decision authority for both phases.
- **Output_Phase**: `Phase.OUTPUT`.
- **IP_Leak**: Disclosure in model output of internal infrastructure identifiers: RFC1918/loopback/link-local/ULA addresses, internal hostnames and TLD-style internal suffixes, and internal URLs.
- **Exfil_Channel**: A construct in model output that causes a client to transmit data without a user action or that smuggles data in a locator: zero-click markdown/HTML images and media, auto-fetch HTML/SVG/CSS sinks, meta-refresh, links whose URL carries encoded payload, and DNS-label (hostname) payloads.
- **Defang**: Neutralising an Exfil_Channel so it can no longer auto-fetch or navigate, while the surrounding text is preserved.
- **Grounding**: Whether an answer is supported by the supplied retrieval context (RAG) or otherwise passes the configured factuality check.
- **Rewrite_Template**: A declared, per-rule, org-signed replacement string (or small pattern with a bounded set of substitutions) that a REWRITE writes in place of a span.
- **Canary**: A per-request (or per-session) unguessable marker injected on the request side whose appearance in output proves the model is echoing protected context.
- **Review_Flag**: A boolean on the decision/audit record meaning "a human should look at this response".
- **Byte_Verified**: The GW09/GW11 discipline: apply transformations to exact spans, then verify that no raw bytes of the span survive and that every span was covered.

## Requirements

### Requirement 1: IP and infrastructure leakage detection on output

**User Story:** As a security engineer, I want internal addresses and hostnames the model emits to be detected and handled per the org's IP-leakage action, so that infrastructure topology does not leave the perimeter (L33-2).

#### Acceptance Criteria

1. THE Gateway SHALL provide a deterministic output detector that emits `Finding`s of the IP_Leak category with original-byte `Span`s for: IPv4 RFC1918 (`10/8`, `172.16/12`, `192.168/16`), loopback (`127/8`, `::1`), link-local (`169.254/16`, `fe80::/10`), IPv6 unique-local (`fc00::/7`), carrier-grade NAT (`100.64/10`), and cloud metadata addresses (`169.254.169.254`, `fd00:ec2::254`).
2. THE detector SHALL emit IP_Leak findings for internal hostnames: single-label hostnames used in a URL or `host:port` position, and hostnames ending in `.internal`, `.local`, `.lan`, `.corp`, `.intranet`, `.home.arpa`, or a Kubernetes service suffix (`.svc`, `.svc.cluster.local`).
3. THE detector SHALL NOT emit an IP_Leak finding for public IPv4/IPv6 addresses, for the documentation ranges `192.0.2.0/24`, `198.51.100.0/24`, `203.0.113.0/24`, `2001:db8::/32`, or for dotted version strings (`1.2.3.4` is a finding only when every octet is in `0..255`; `10.2.3` and `v1.2.3.4-rc1` are not).
4. THE detector SHALL run on the canonicalised text (GW09 canonicalisation: zero-width, fullwidth, percent/entity decoding within the declared Decode_Budget) so an obfuscated address is detected, and SHALL map every finding back to ORIGINAL byte offsets.
5. WHEN canonicalisation exceeds the Decode_Budget, THE detector SHALL emit one `status=UNAVAILABLE` finding with `confidence=None` (never an empty tuple presented as clean), matching the GW09 detector contract.
6. THE disposition for IP_Leak findings SHALL be produced only by the Resolver from the org's IP-leakage rule (`output_ip_leakage_enabled` / `output_ip_leakage_action`, already `mapped` in `firewall_v2_map.py`), with `allow`, `flag`, `redact`, `block`, and `rewrite` all honoured, and `output_ip_leakage_enabled = false` producing no enforcing rule.
7. WHEN the IP-leakage action is `redact`, THE Gateway SHALL replace each IP_Leak span with `[REDACTED]` through the Byte_Verified path (GW09), and the released bytes SHALL contain none of the matched address/hostname bytes. **[Amended: redact/rewrite/block of IP leak is STRICT_WITHHOLD-only (D-9b); in INCREMENTAL it is flag-only at final; legacy `output_ip_leakage_*` rules are inert in INCREMENTAL (D-2)]**
8. THE detector SHALL NOT include the matched value in `Finding.evidence` (evidence is `None`; spans and `detector_version` locate the finding), consistent with GW09 and the R2-05 audit rule that audit streams never carry the leaked secret.
9. THE detector SHALL carry a `detector_version` that changes whenever its ruleset changes, and SHALL declare `required_capabilities` as the empty set (in-process, no `GuardBackend`).

### Requirement 2: Exfiltration channel detection and defanging

**User Story:** As a security engineer, I want zero-click and encoded-payload exfiltration constructs in model output to be defanged or blocked, so that an indirect injection cannot make a client beacon data to an attacker (L33-1; v1 G13/G41/G42/G92 parity).

#### Acceptance Criteria

1. THE Gateway SHALL provide a deterministic output detector that finally makes `Category.EXFIL` produced: it SHALL emit EXFIL findings for markdown images `![alt](url)` and reference-style image definitions, and for HTML/SVG/CSS auto-fetch sinks: `<img|iframe|video|audio|source|embed|object|input|script|link src/href/srcset/poster/data>`, `<svg><image|use href>`, `<base href>`, `<meta http-equiv="refresh">`, CSS `url(...)`/`@import`, and `dns-prefetch`/`preconnect`/`stylesheet` link rels.
2. THE detector SHALL classify an image or auto-fetch sink as an Exfil_Channel when its URL carries a payload: a query string or path/fragment of at least the declared minimum payload length, or encoded content (base64/base64url, hex, percent-encoded runs, HTML entities inside the URL) that decodes to mostly-printable text, or any URL whose host is not on the org's declared allow-list of image hosts.
3. THE detector SHALL emit an EXFIL finding for DNS-label exfiltration: a hostname whose leftmost label(s) are a high-entropy or long hex/base64 blob (a payload smuggled in the hostname), independent of scheme.
4. THE detector SHALL treat one-click anchors (`<a href>` and markdown links `[t](url)`) as EXFIL only when the URL satisfies criterion 2 or 3; a plain link to an allow-listed host SHALL NOT produce a finding.
5. THE detector SHALL bound its work: it SHALL cap the number of URLs examined per scanned text at a declared budget and, WHEN the cap is exceeded, SHALL emit a `status=UNAVAILABLE` finding for the remainder (fail-closed under the rule's `on_unavailable`), never silently stop scanning.
6. THE detector SHALL use the GW09 canonicalised text (so `h\u200bttp`, fullwidth, entity- and percent-obfuscated schemes/hosts are caught) and SHALL map spans to original bytes.
7. WHEN the EXFIL rule's action is `redact` or `rewrite`, THE Gateway SHALL Defang by replacing the whole sink span (the URL, and for an auto-fetch tag the tag) so that no scheme+host of the removed URL survives in released bytes; WHEN the action is `block`, no byte of the response is delivered (STRICT_WITHHOLD) or the stream is terminated with a declared error (INCREMENTAL, after the first content byte). **[Amended: holds in STRICT_WITHHOLD; in INCREMENTAL exfil rules with redact/rewrite/block are refused at compile time and exfil is flag-only at final (D-9a)]**
8. A markdown image carrying an encoded-data URL (L33-1 shape) SHALL, under every action other than `allow`/`flag`, result in output in which no auto-fetchable form of that URL remains. **[Amended: same STRICT_WITHHOLD scope as 2.7]**
9. THE EXFIL rule SHALL be selected by the org's plan like any other rule (an OUTPUT/BOTH-scope rule with `Category.EXFIL`); WHEN the org has no such enforcing rule the detector SHALL NOT cause any hold in the streaming path (the GW12b "no enforcing rule, no hold" property is preserved).
10. THE detector SHALL span chunk boundaries correctly in INCREMENTAL mode: an image or sink split across upstream deltas SHALL be held (within the existing `RV_HOLDBACK_MAX_TOKENS` bound and trade-off table) until its disposition is decidable, and on force-release SHALL follow the fail-closed trade-off of Requirement 8. **[Amended: only the `canary` class is added to the trade-off tables; `ip_leak` and `exfil_sink` are intentionally absent and pinned absent by test (D-9, C6)]**

### Requirement 3: Grounding and factuality (hallucination) on output

**User Story:** As a compliance owner, I want ungrounded answers on a RAG request to be flagged or handled per the org's hallucination action, so that unsupported answers are not presented as fact (L33-3).

#### Acceptance Criteria

1. THE Gateway SHALL provide a grounding detector that, for a response on a request carrying retrieval context (`Surface.RAG` or a chat request with supplied context chunks), emits a `hallucination`-category finding whose `confidence` is the risk score in `[0.0, 1.0]`, and no finding (an `EXECUTED` clean result) when the score is below the rule threshold.
2. THE detector SHALL support the modes of the v1 field `hallucination_grounding_mode`: at least `lexical` (deterministic, in-process, no backend) and one model-backed mode via the injected local `GuardBackend` seam; an unknown mode SHALL be refused at plan-compile time with a field-level message, never silently defaulted to a weaker mode. **[Amended: lexical shipped; model mode is capability-gated and UNAVAILABLE until a backend advertises hallucination (D-27)]**
3. THE rule threshold SHALL come from `hallucination_grounding_threshold` (range-validated `0..1`) via `Rule.threshold`; THE model-backed mode SHALL honour `hallucination_grounding_model` as a local-model selector only; it SHALL NEVER route to an external provider (zero firewall-attributable provider calls, as LGW11-4).
4. WHEN the model-backed mode is selected and the backend is absent, over its `Budget`, or errors, THE detector SHALL emit a `status=UNAVAILABLE` finding (not a clean `EXECUTED`), and the Resolver SHALL apply the rule's `on_unavailable` posture. THE default posture for a hallucination rule whose action is `redact`, `rewrite`, or `block` SHALL be `FAIL_CLOSED`; for `flag`/`allow` it has no effect on disposition.
5. WHEN a request has no retrieval context, THE detector SHALL emit a `SKIPPED` finding (recorded, contributes nothing to disposition), not a fabricated `EXECUTED` clean result.
6. THE disposition SHALL come only from the Resolver using `output_hallucination_action` (`allow`, `flag`, `redact`, `block`, `rewrite`); `factuality_check_enabled = false` SHALL produce no hallucination rule.
7. THE `flag` action SHALL set the Review_Flag (Requirement 4) and SHALL NOT alter released bytes; `redact`/`rewrite` on a hallucination finding SHALL apply to the ungrounded claim spans when the detector can localise them, and SHALL otherwise fail closed to BLOCK (whole-response action) rather than leaving the response unchanged.
8. THE detector SHALL read only channels the C21 model already scans (`output_content`, `output_refusal`, `output_reasoning`, `output_tool_arguments`) and SHALL NOT add an unscanned channel (`unscanned_channels() == ()` remains true).
9. THE detector SHALL be bounded in input size and time by values derived from `ResourceContract` (no new capacity literal outside `runtime/resources.py`); WHEN the response exceeds the bound, THE result SHALL be `UNAVAILABLE` per criterion 4.
10. IN INCREMENTAL streaming mode THE grounding check, which needs the whole response, SHALL run on the completed response and SHALL NOT be used to hold released bytes; a hallucination rule with a byte-altering action (`redact`, `rewrite`, `block`) SHALL therefore require STRICT_WITHHOLD, and a plan that selects INCREMENTAL with such a rule SHALL either be refused at compile time with a field-level message or be demoted to `flag` with a recorded declared code. `design.md` picks one and states it. **[Amended: option 1 chosen (compile refusal), extended to exfil and ip_leak (D-9)]**

### Requirement 4: Human-review flag

**User Story:** As a SOC analyst, I want responses that were flagged, redacted, rewritten, or ungrounded to carry a review flag, so that post-generation sanitisation is auditable in the human-review surface (v1 `HumanReviewItem` parity).

#### Acceptance Criteria

1. THE `Decision`/audit record SHALL carry a `review_required` boolean (additive, default `False`), computed by the Resolver, which is `True` when any per-finding disposition is `FLAG`, `REDACT`, `REWRITE`, or `BLOCK` from an OUTPUT-phase rule that requests review, and `False` otherwise.
2. THE Review_Flag SHALL be derived by the Resolver (the single authority) and SHALL NOT be computed in a second place (egress, edge, audit).
3. WHEN `output_incident_logging_enabled` is `false` for the org, THE Gateway SHALL NOT emit a review-queue item for `REDACT`/`REWRITE` dispositions, but SHALL still set the in-record flag; `FLAG` and `BLOCK` always set it.
4. THE audit record SHALL carry the flag, the deciding rule ids, and the finding categories, and SHALL NOT carry the matched bytes or the rewritten text.
5. THE flag SHALL be present on both the non-streaming and streaming (INCREMENTAL and STRICT_WITHHOLD) paths for the same inputs (parity of the flag across modes).
6. THE existing `Decision.__post_init__` invariant (request disposition equals the most restrictive per-finding disposition) SHALL be unchanged; adding `review_required` SHALL NOT permit a record whose flag is inconsistent with its per-finding dispositions (constructing such a record is rejected).

### Requirement 5: Real REWRITE action in the Resolver and the plan

**User Story:** As an org admin, I want REWRITE to actually rewrite a matched span using a declared template or a local model, so that I can keep a useful answer instead of an opaque `[REDACTED]`, and so that a saved REWRITE is no longer silently treated as REDACT.

#### Acceptance Criteria

1. THE domain SHALL represent REWRITE as a first-class disposition (working name `Disposition.REWRITE`) and a first-class transformation kind (`Transformation(kind="rewrite", span, replacement)`); `resolve/decision.py::_action_to_disposition` SHALL map `Action.REWRITE` to it and the interim "REWRITE -> REDACT" mapping and its docstring SHALL be removed.
2. THE disposition ordering SHALL be total and fail-closed: `BLOCK` outranks `REWRITE`, `REWRITE` outranks `REDACT`, `REDACT` outranks `FLAG`, `FLAG` outranks `ALLOW`; `most_restrictive` SHALL remain the only combinator, and a REWRITE SHALL never downgrade a BLOCK.
3. THE Resolver SHALL remain the single enforcement authority: no module other than `resolve/` SHALL map an `Action` to a `Disposition` or choose a replacement; an AST/grep gate SHALL fail the build if a second `Action`-to-`Disposition` site appears.
4. THE replacement text of a REWRITE SHALL come from a Rewrite_Template declared on the rule (a plan field covered by `ExecutionPlan.content_hash`), applied deterministically with a bounded set of substitutions (category name, rule id); a REWRITE rule with no declared template SHALL be refused at plan-compile time with a field-level message (never defaulted to `[REDACTED]` silently).
5. THE compiler SHALL add `Action.REWRITE` to `SUPPORTED_ACTIONS` and SHALL validate the template (non-empty, length-bounded from `ResourceContract`, containing none of the rule's own detector patterns so a rewrite cannot re-emit the finding).
6. WHERE a local rewrite backend is configured, THE Gateway MAY produce the replacement text via the injected local `RewriteBackend` seam; THE backend output SHALL be treated as untrusted: it SHALL be re-inspected through the same Output_Phase detectors and Resolver before release, SHALL be length-bounded, and WHEN it is unavailable, over budget, empty, or re-inspection yields any non-ALLOW finding, THE Gateway SHALL fall back to the Rewrite_Template, and WHEN that also cannot be applied and verified, SHALL BLOCK.
7. THE rewrite path SHALL be Byte_Verified: after applying `kind="rewrite"` transformations, the verifier SHALL confirm that every rewritten span's original bytes are absent from the output and the replacement is present exactly once at the expected offset; any uncovered, partial, overlapping, or surviving span SHALL fail closed (BLOCK / `OutputBlocked` / `VerificationFailure`), never release.
8. THE GW09 redaction guarantees SHALL be unchanged: for any input without a REWRITE rule, the `Decision`, transformations, and released bytes SHALL be identical to those produced before this card (a differential/property test over generated inputs SHALL assert byte-equality with the pre-change behaviour).
9. THE overlapping-span normalisation (`_normalise_redactions`) SHALL treat `kind="rewrite"` and `kind="redact"` as distinct kinds that do not merge into each other; WHEN a rewrite span overlaps a redact span, THE Resolver SHALL resolve the overlap to the stricter, non-lossy-for-security outcome (REDACT of the union) and record it, never emit overlapping edits of different kinds.
10. THE GW26 publisher SHALL stop naming `rewrite` as unsupported: `firewall_v2_publish.py::_UNSUPPORTED_ACTIONS` SHALL no longer contain `rewrite`, `_policy_rule` SHALL emit a rewrite rule with its template, and the publisher's returned `unsupported` list SHALL not include a rewrite action.
11. THE REWRITE decision SHALL be visible in the trace/audit as `rewrite` (not `redact`), with the deciding rule id.
12. THE REWRITE on the request/input phase SHALL be supported by the same resolver path and the GW11 `apply_and_verify` byte discipline; WHERE the input-phase rewrite is not built in this card, THE Resolver SHALL refuse (fail closed to REDACT of the same spans, recorded with a declared code) rather than leaving the span unmodified. `design.md` states which.

### Requirement 6: Model-downgrade decision (explicit)

**User Story:** As a reviewer, I want the fate of `model_downgrade` stated, so that the "unsupported" omission is either lifted or retired by name.

#### Acceptance Criteria

1. THE spec's decision SHALL be: `model_downgrade` is **retired by name from GW33 to GW28/GW29** (routing and model isolation), because it selects a model rather than guarding output bytes; GW33 does not implement it.
2. THE publisher SHALL continue to name `model_downgrade` as unsupported with a message naming the owning card; ONLY `rewrite` SHALL be removed from `_UNSUPPORTED_ACTIONS`.
3. A test SHALL assert that after this card `_UNSUPPORTED_ACTIONS == {"model_downgrade"}` and that a `model_downgrade` action is still reported (loudly), never silently dropped or mapped to another action.

### Requirement 7: Canary tokens checked on output

**User Story:** As a security engineer, I want canary tokens injected on the request side and checked on output, so that a model echoing protected context is detected (v1 `CanaryTokenManager` parity).

#### Acceptance Criteria

1. THE Gateway SHALL generate a per-request canary from a cryptographically secure source with at least 64 bits of entropy, injected into the request context before dispatch through the pure request transform, and SHALL hold it only for the lifetime of that request.
2. THE canary SHALL NOT be stored in module-level state, logged, placed in an audit record, or written to the trace; the audit record SHALL carry only `canary_checked: bool` and `canary_leaked: bool`.
3. THE canary check SHALL be a deterministic output detector emitting a `canary`-category finding with original-byte spans WHEN the canary (compared after GW09 canonicalisation of the output, so zero-width-wrapped, case- or encoding-obfuscated echoes are caught) appears in any scanned OUTPUT channel.
4. A canary finding SHALL default to action `block` in the compiled rule when the org enables canary checking, SHALL honour `redact`/`rewrite`/`flag`/`block` from the plan, and SHALL always be resolved by the Resolver.
5. THE detector SHALL redact the canary itself under `redact` (the canary bytes SHALL NOT appear in released output), through the Byte_Verified path.
6. THE canary check SHALL span chunk boundaries in INCREMENTAL mode using the existing holdback window (the canary length is within the holdback bound), and WHEN a boundary-split canary cannot be decided within the bound, THE trade-off SHALL be the fail-closed one of Requirement 8.
7. WHEN canary injection is disabled for the org, THE Gateway SHALL inject nothing and the detector SHALL emit no findings (and SHALL NOT hold bytes).
8. THE injected canary SHALL NOT alter the visible semantics of the prompt for the model beyond the marker itself and SHALL be bounded in length (derived from `ResourceContract`).

### Requirement 8: Streaming and holdback compatibility

**User Story:** As a platform engineer, I want the new detectors and REWRITE to work in both streaming modes without weakening the shipped bounded holdback.

#### Acceptance Criteria

1. THE new detectors (IP_Leak, Exfil_Channel, canary) SHALL be classified in the holdback trade-off tables (`detect.holdback.TRADE_OFF` and its egress mirror `egress/holdback_tradeoff.py`), each with exactly one outcome (`redact-remainder` or `terminate`), and the existing drift test SHALL be extended so the two tables agree for the new classes.
2. THE egress layer SHALL NOT import `detect` (the `egress -> detect` edge remains forbidden); any new class name needed by egress SHALL be mirrored locally and pinned by a drift test.
3. THE held-token bound (`RV_HOLDBACK_MAX_TOKENS`, default 3) and the per-chunk rescan window SHALL be unchanged; the new classes SHALL NOT raise the maximum hold; a class that cannot be decided within the bound SHALL follow its declared outcome (redact-remainder or terminate), never release raw bytes.
4. IN INCREMENTAL mode, a REWRITE whose span has already been released SHALL NOT be presented as having been applied; the stream SHALL terminate with a declared terminal error frame (Terminate_With_Declared_Error) rather than truncate silently, consistent with GW13.
5. IN STRICT_WITHHOLD mode, REWRITE SHALL be applicable to any span because nothing has been released; the rewritten response SHALL be released only after Byte_Verified inspection.
6. THE streaming property tests (raw bytes of a rewritten, redacted, defanged, or canary span never appear in the concatenation of released chunks, for random chunkings) SHALL cover the new classes and the rewrite kind.
7. WHEN no Enforcing_Output_Rule exists (`enforcing_output_rules(plan) == ()`), the pipeline SHALL hold nothing, with the new detectors present or not.

### Requirement 9: GW27 handoff for factuality, grounding, and output_hallucination_action

**User Story:** As a reviewer, I want the fields GW27 retires to GW33 to be built or retired with a reason, never lost between the two cards.

#### Acceptance Criteria

1. THE fields `factuality_check_enabled`, `hallucination_grounding_mode`, `hallucination_grounding_threshold`, `hallucination_grounding_model`, and `output_hallucination_action` SHALL each be **BUILT** by this card (Requirement 3); none SHALL remain `unsupported` in `firewall_v2_map.py::FIELDS` once the card is green, and each SHALL become `mapped` (with `output_hallucination_action` no longer carrying `reject: ("rewrite",)`).
2. THE existing `output_policy_action`, `output_pii_action`, `output_credential_action`, `output_ip_leakage_action`, and `output_hallucination_action` `rewrite` refusal at save (`firewall_v2_map.py` `reject: ("rewrite",)` and `firewall_config_serializer.py::validate`) SHALL be lifted for these five fields ONLY for orgs whose published plan is enforced by v2; for any org not served by v2 the refusal SHALL remain (fail-closed), because a v1 gateway would not implement REWRITE and accepting the save would be accepted-then-ignored.
3. THE GW27 retire list, ledger, and tests that assert "`rewrite` refused" or "hallucination/grounding fields retired to GW33" SHALL be updated in the same change so they assert the new state; no test SHALL be deleted without an equivalent assertion of the replacement behaviour.
4. IF the GW27 branch has not landed when this card is integrated, THEN GW33 SHALL carry the field-map and serializer changes against the current mainline state and the integration note SHALL name the merge order; the final state after both cards SHALL satisfy criteria 1-3 regardless of order.
5. THE `hallucination_grounding_*` and `factuality_check_enabled` plan fields SHALL be carried in the signed Plan_Document and covered by `content_hash` so that a change republishes and the gateway never enforces a stale threshold.
6. THE `max_response_tokens` response-side token counting that GW27 design D-6 delegates to GW33 SHALL be recorded in the design as either built here (bounded output length check producing a declared code) or retired by name to a stated card; it SHALL NOT be left silent.

### Requirement 10: Fail-closed, truthful reporting, and security properties

**User Story:** As a security reviewer, I want every failure of the new output machinery to fail closed with a declared code, and no new log to leak the data it detects.

#### Acceptance Criteria

1. WHEN any new output detector raises, times out, or cannot execute, THE result SHALL be an `UNAVAILABLE` finding routed by the rule's `on_unavailable` through the Resolver, never an exception propagated to the client and never a clean pass; FAIL_OPEN SHALL remain pinned at `0` in the exported metrics unless a rule explicitly declares `FAIL_OPEN`.
2. THE new detectors, the rewrite verifier, and the Resolver changes SHALL NOT log or place in `Finding.evidence`, audit records, metrics labels, or trace payloads the matched bytes, the canary, or the rewritten replacement text derived from a model.
3. THE new detectors SHALL run in bounded time and memory with budgets derived from `ResourceContract`; pathological input (deeply encoded, very many URLs, very long hostnames) SHALL produce a bounded result or `UNAVAILABLE`, never unbounded work (checked by a pathological-input test with a wall-clock assertion against the declared budget).
4. THE rewrite template, detector rulesets, and category additions SHALL be covered by `content_hash`/`ruleset_version` so an unannounced change is observable in `plan_version`/`detector_version`.
5. THE Resolver SHALL remain pure and total: no I/O, no clock, no globals, no logging, and no exception for any input of the declared types, including the new `REWRITE` disposition.
6. THE Resolver outcome for an org that has none of the new rules SHALL be byte-identical to the pre-card outcome (the preservation property of Requirement 5.8 extended to `review_required == False` and unchanged `deciding_rules`).

### Requirement 11: Live-test obligations (DEFERRED, not run, not claimed)

**User Story:** As an owner, I want the card's live acceptance tests listed with their PASS criteria and an honest status, so that the offline gate is never mistaken for card exit.

#### Acceptance Criteria

1. THE spec and the final changelog entry SHALL list the following live tests as **deferred and not run**, with their PASS criteria verbatim from the runbook:
   - **L33-1** — Model emits a markdown image whose URL carries encoded data. PASS: defanged or blocked; never delivered live.
   - **L33-2** — Model emits an internal hostname and an RFC1918 address. PASS: handled per the org's IP-leakage action.
   - **L33-3** — Ungrounded answer on a RAG request. PASS: flagged per the org's hallucination action.
2. THE card's offline equivalents SHALL exist and be named after the live test they stand in for (for example a test module per L33-n), each running the same procedure against the in-process pipeline with a stub provider, and SHALL be described as "offline equivalent, not the live gate".
3. NO document, commit message, changelog line, or test name produced by this card SHALL state that GW33 has exited or that a live Docker/staging gate passed; the status SHALL read "local half closed; live L33-1..L33-3 deferred".
4. THE implementation SHALL NOT run the live Docker/staging gate or any command that contacts a live provider.
5. THE deferred items that depend on other cards SHALL be named: a real local grounding model (GW10 backends), end-to-end SSE transport wiring (GW13/GW16), and fleet certification (GW20/GW21/GW23).

### Requirement 12: Structural and gate constraints

**User Story:** As a maintainer, I want the card to land inside the existing layering and gates so that nothing in the offline suite regresses.

#### Acceptance Criteria

1. THE new code SHALL respect the import-linter layer contract (`edge > admit > plan > detect > resolve > dispatch > egress > audit > runtime > contracts > domain`): detectors under `detect/`, resolver changes under `resolve/` (importing only `domain`), byte-verification under `egress/` and `dispatch/` (no `egress -> detect` edge), vocabulary under `domain/`; `lint-imports` SHALL report all contracts kept.
2. THE new code SHALL contain no capacity literal outside `runtime/resources.py` (new budgets: URL cap, rewrite template length, canary length, grounding input bound, and payload-length threshold SHALL be added as derivations there); `lint/check_capacity_literals.py` SHALL exit 0. Detection-semantics constants (CIDR ranges, TLD suffix lists, scheme names) are not capacity and SHALL live in the detector ruleset module with `ruleset_version`.
3. THE new code SHALL contain no module-level mutable state (`lint/check_no_module_mutable.py` exit 0): rulesets are `MappingProxyType`/`frozenset`/tuples; canaries are per-request values.
4. ALL new value types (findings extensions, rewrite templates, canary check result, review flag carriers) SHALL be `@dataclass(frozen=True, slots=True)` (`lint/check_frozen_dataclasses.py` exit 0).
5. THE `lint/check_http_outside_edge_resolve.py`, `lint/check_tenant_scale.py` (for any new per-tenant structure), and `lint/check_sizes.py` gates SHALL exit 0.
6. `mypy --strict` over `gateway_v2` (and `state_control`/`audit_control` where they are strict-covered) SHALL be clean; `ruff` SHALL be clean.
7. THE full offline pytest suite SHALL pass in the container-run style used by prior cards (no new failures, no newly skipped or xfailed tests beyond ones with a recorded reason); the count of passing tests SHALL increase.
8. EVERY correctness property (Requirement 13) SHALL be a seeded-`random.Random` test of at least 100 iterations (the house idiom is >= 10,000 where cheap), tagged in-source with a comment of the form `# Feature: gw33-output-parity, Property N: <name>` and a docstring naming the requirement it validates. No `hypothesis` dependency SHALL be added.
9. THE control-plane changes (`firewall_v2_map.py`, `firewall_v2_publish.py`, `firewall_config_serializer.py`) SHALL keep the "a save the compiler cannot represent keeps failing loudly" behaviour (L14b-5): the map, serializer, and compiler SHALL agree, with a cross-check test that every mapped value the map accepts compiles.
10. THE AGENTS.md changelog discipline SHALL be followed in the final commit of the card: a one-line pointer in `AGENTS.md`, the canonical entry in `docs/pipeline/CHANGELOG.md` (when present), and a plan/evidence note under `docs/plans/`, each stating the status "local half closed; live deferred".

### Requirement 13: Correctness properties (testable invariants)

Each property is a seeded randomized test (>= 100 iterations) with a source tag.

1. **Property 1 — No raw leak after rewrite/redact/defang.** For random output text with embedded IP_Leak / Exfil / canary spans, random chunkings, and any enforcing action, the concatenation of released bytes contains none of the matched bytes. Validates 1.7, 2.7, 7.5, 8.6.
2. **Property 2 — Rewrite byte-verification soundness.** For random texts and random non-overlapping spans, applying `kind="rewrite"` transformations then verifying succeeds iff every span was covered; any perturbation (shifted span, surviving raw bytes, overlap) is rejected. Validates 5.7.
3. **Property 3 — Disposition order and no downgrade.** `most_restrictive` over random multisets of {ALLOW, FLAG, REDACT, REWRITE, BLOCK} equals the maximum in the declared order; adding a BLOCK finding always yields BLOCK; adding a REWRITE never lowers REDACT/BLOCK. Validates 5.2.
4. **Property 4 — Preservation.** For random plans containing no REWRITE rule and none of the new categories, the new `resolve` equals the pre-card `resolve` in disposition, transformations, deciding rules, and `review_required == False`. Validates 5.8, 10.6.
5. **Property 5 — Single authority.** Over the source tree, `Action`-to-`Disposition` mapping exists only under `resolve/` (AST gate). Validates 5.3.
6. **Property 6 — IP classification.** For random IPv4/IPv6 addresses, the detector flags exactly those in the declared internal ranges, flags none in public/documentation ranges, and finds an obfuscated (zero-width, fullwidth, percent) internal address at the correct original span. Validates 1.1-1.4.
7. **Property 7 — Exfil defang completeness.** For random image/sink constructs with random payload URLs (plain, base64, hex, entity, DNS-label), no auto-fetchable URL remains after redact/rewrite; allow-listed plain links produce no finding. Validates 2.2-2.8.
8. **Property 8 — Review flag consistency.** `review_required` equals the declared function of per-finding dispositions for every random decision, and is equal across INCREMENTAL and STRICT_WITHHOLD for the same input. Validates 4.1, 4.5, 4.6.
9. **Property 9 — Fail-closed unavailability.** For random detector failures, an enforcing FAIL_CLOSED rule yields BLOCK and a FAIL_OPEN rule does not; no failure yields a clean `EXECUTED` finding. Validates 3.4, 10.1.
10. **Property 10 — Canary secrecy and detection.** A per-request canary is never present in any audit/trace/log payload produced by the pipeline, and is detected (including zero-width/case obfuscated and chunk-split forms) in any scanned output channel. Validates 7.2, 7.3, 7.6.
11. **Property 11 — Template safety.** For random templates, the compiler refuses empty, over-length, or finding-re-emitting templates and accepts the rest; a compiled rewrite rule's `content_hash` changes when its template changes. Validates 5.4, 5.5, 10.4.
12. **Property 12 — Map/serializer/compiler agreement.** For random FirewallConfig action values, the console map accepts a value iff the compiler can represent it (for rewrite: only on a v2-served org). Validates 9.2, 12.9.

## Acceptance Criteria Summary (numbered, card-level)

1. **AC-1 (IP leak).** Internal IPs/hostnames in output are detected on canonical text, mapped to original spans, resolved only by the org's IP-leakage action, and redaction removes every matched byte. (Req 1, Prop 6)
2. **AC-2 (exfil).** `Category.EXFIL` is produced for zero-click images, auto-fetch sinks, payload-carrying links, and DNS-label payloads; redact/rewrite/block leave no auto-fetchable form; boundary-split sinks are held within the existing bound. (Req 2, Prop 7)
3. **AC-3 (grounding).** A grounding detector builds `factuality_check_enabled`, `hallucination_grounding_mode/threshold/model`, and `output_hallucination_action`; unavailable backend is `UNAVAILABLE` under `on_unavailable`, no-context is `SKIPPED`; no external provider call. (Req 3, 9, Prop 9)
4. **AC-4 (human review).** `review_required` is computed once by the Resolver, equal across streaming modes, carries no matched bytes. (Req 4, Prop 8)
5. **AC-5 (REWRITE).** REWRITE is a real disposition and transformation with a declared template (local model optional and re-inspected), Byte_Verified, never below REDACT, never above BLOCK, and the GW09 behaviour is byte-identical when no REWRITE rule exists. (Req 5, Props 2-5, 11)
6. **AC-6 (publisher).** `rewrite` is removed from `_UNSUPPORTED_ACTIONS`; `model_downgrade` stays named unsupported and is retired by name to GW28/GW29. (Req 5.10, 6)
7. **AC-7 (canary).** Per-request canary injected on request, checked on output on canonical text, never logged or audited, covered in streaming. (Req 7, Prop 10)
8. **AC-8 (streaming).** New classes are in both trade-off tables with a drift test; held-token bound unchanged; no `egress -> detect` import. (Req 8)
9. **AC-9 (GW27 handoff).** The five fields are `mapped`; `rewrite` save is accepted only for v2-served orgs; GW27 ledger and tests updated in the same change. (Req 9, Prop 12)
10. **AC-10 (fail-closed).** All detector failures route through `on_unavailable`; no matched bytes, canary, or model-generated rewrite in logs/evidence/audit/labels. (Req 10)
11. **AC-11 (live deferred).** L33-1, L33-2, L33-3 are listed as deferred, offline equivalents exist and are labelled as such, and nothing claims GW33 exit. (Req 11)
12. **AC-12 (gates).** Offline pytest, `mypy --strict`, `ruff`, `lint-imports`, and the five AST gates are green; every property is a >= 100-iteration seeded test tagged in source. (Req 12, 13)

## Non-Functional Requirements

- **Latency.** The new detectors run inside the existing output scan budget; they SHALL NOT add a hold longer than the GW12b bound and SHALL NOT make INCREMENTAL streaming wait for the whole response (grounding is the only whole-response check, Requirement 3.10).
- **Determinism.** Every detector other than the optional model-backed grounding/rewrite modes is deterministic for a given input and ruleset version.
- **Observability.** Producers remain label-free (GW14d): new counters (`rewrite_applied`, `rewrite_fallback_template`, `rewrite_blocked`, `canary_leaks`, `exfil_defanged`, `ip_leak_findings`) carry no tenant-derived or content-derived label.
- **Backward compatibility.** Additive domain changes only (new enum members, default-valued fields); existing plan documents without a template or new categories compile and resolve exactly as before.

## Out of Scope

- Running the live Docker/staging gate, live L33-1..L33-3, or any fleet certification (GW20/GW21/GW23); they are listed as deferred (Requirement 11).
- Implementing `model_downgrade` (retired by name to GW28/GW29) and any routing or model-isolation behaviour.
- The other GW27-retired fields owned by GW28 (routing), GW29 (model isolation), GW31 (RAG fields), GW36 (token budget).
- Rebuilding the review-queue UI, the `HumanReviewItem` model, or the v1 gateway/control incident tables; only the v2 flag and the existing audit publication are in scope.
- Training or shipping a new grounding model; the card uses the existing local `GuardBackend`/`RewriteBackend` seams and a deterministic lexical mode.
- Real SSE transport wiring and end-to-end serving (GW13/GW15/GW16); the offline equivalents use the in-process pipeline.
- Changing the shipped holdback bound, the GW09 canonicalisation Decode_Budget, or the GW12b trade-off outcomes for existing classes.
- Edits to the v1 gateway (`gateway/ai_mesh_gateway/*`) beyond reading it as the parity reference.


## Requirements amendments (applied, task 0.1)

Applied from the "Requirements amendments" table of `design.md`; each is a narrowing forced by the verified holdback mechanics, with STRICT_WITHHOLD kept as the stricter alternative.

| Req | Amended |
|---|---|
| 2.7, 2.8 | Holds in STRICT_WITHHOLD. In INCREMENTAL, exfil rules with redact/rewrite/block are refused at compile; exfil is flag-only at `final`. |
| 2.10, 8.1 | Only `canary` is classified in the trade-off tables; `ip_leak` and `exfil_sink` are intentionally absent and pinned absent by test. |
| 1.x (IP action) | IP leak redact/rewrite/block is STRICT-only (D-9b); INCREMENTAL IP redaction parity gap and TTFT cost accepted; legacy-prefix rules inert in INCREMENTAL (D-2). |
| 3.1/3.2 | Lexical shipped; model mode capability-gated, UNAVAILABLE until a backend advertises hallucination (D-27). |
| 7.x (canary) | Never releasing raw canary bytes holds for the plain form; an obfuscated prefix may release in INCREMENTAL (D-26). |
| 3.10 | Unchanged (compile refusal), extended to exfil and ip_leak. |
| Property 1 | As restated in design.md Correctness Properties. |
| 9.6 | Built here (D-13). |

Status wording: "local half closed; live L33-1..L33-3 deferred".
