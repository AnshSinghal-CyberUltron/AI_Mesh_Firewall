"""Query stage: validate and scan the incoming query before retrieval."""
from __future__ import annotations

import logging
import os
import re
import time
from typing import Any, TYPE_CHECKING

from .contracts import QueryStageInput, QueryStageOutput, StageVerdict

if TYPE_CHECKING:
    from scanner import InputScanner
    from llm_judge import LLMJudge
    from embedding_vault import EmbeddingVault
    from intent_classifier import IntentClassifier

LOG = logging.getLogger("gateway.rag_pipeline.query_stage")


class QueryStage:
    """Wraps InputScanner, LLM Judge, Embedding Vault, and Intent Classifier.

    Responsibilities:
    - Query length validation
    - Tier1/1.5/2 scanning for injection (regex + optional Bedrock)
    - LLM-as-Judge: asks a dedicated model to classify injection attempts
    - Embedding Vault: cosine similarity against known attack patterns
    - Intent Classification: determines if query intent is suspicious
    - Query rewrite: strip injection patterns while preserving legitimate query
    - Below-threshold flags for downstream escalation
    - Model downgrade recommendation for medium-risk queries
    """

    def __init__(
        self,
        scanner: "InputScanner | None",
        config: dict,
        llm_judge: "LLMJudge | None" = None,
        embedding_vault: "EmbeddingVault | None" = None,
        intent_classifier: "IntentClassifier | None" = None,
    ) -> None:
        self._scanner = scanner
        self._config = config
        self._llm_judge = llm_judge
        self._embedding_vault = embedding_vault
        self._intent_classifier = intent_classifier

    async def execute(self, inp: QueryStageInput) -> QueryStageOutput:
        start = time.perf_counter()
        injection_flags: list[str] = []
        llm_judge_result: dict[str, Any] = {}
        vault_result: dict[str, Any] = {}
        intent_result: dict[str, Any] = {}
        # FIX G3: the query text that proceeds to retrieval is PII-redacted before
        # it is embedded (set once the scanner verdict is known, below). Blocked
        # paths never reach the embedder so they keep the raw query for telemetry.
        embed_query = inp.query_text

        # ── Basic validation ──
        if not inp.query_text.strip():
            return QueryStageOutput(
                verdict=StageVerdict(
                    action="block",
                    threat_type="empty_query",
                    confidence=1.0,
                    detail="Query text is empty",
                ),
                sanitized_query=inp.query_text,
                original_query=inp.query_text,
                scan_tier="validation",
                latency_ms=(time.perf_counter() - start) * 1000,
            )

        max_len = inp.policy.get(
            "max_query_length",
            self._config.get("rag_max_query_length", 2000),
        )
        if len(inp.query_text) > max_len:
            return QueryStageOutput(
                verdict=StageVerdict(
                    action="block",
                    threat_type="query_too_long",
                    confidence=1.0,
                    detail=f"Query length {len(inp.query_text)} exceeds max {max_len}",
                ),
                sanitized_query=inp.query_text,
                original_query=inp.query_text,
                scan_tier="validation",
                latency_ms=(time.perf_counter() - start) * 1000,
            )

        # ── Intent Classification (fast, pattern-based) ──
        if self._intent_classifier is not None:
            try:
                intent_v = self._intent_classifier.classify(inp.query_text)
                intent_result = {
                    "intent": intent_v.intent,
                    "confidence": intent_v.confidence,
                    "risk_factors": intent_v.risk_factors,
                    "is_suspicious": intent_v.is_suspicious,
                }
                if intent_v.is_suspicious:
                    injection_flags.append(f"intent:{intent_v.intent}")
            except Exception as e:
                # Observability: an ENABLED detection layer that silently no-ops on
                # failure is a hidden fallback. Fail-open is intended (an additive
                # layer's outage must not block the query) but it MUST be VISIBLE,
                # so operators know detection is degraded — warn, don't debug-swallow.
                LOG.warning("Intent classifier failed (fail-open, layer skipped): %s", e)

        # ── Embedding Vault check (cosine similarity against known attacks) ──
        if self._embedding_vault is not None:
            try:
                vault_v = await self._embedding_vault.check(inp.query_text)
                vault_result = {
                    "is_match": vault_v.is_match,
                    "confidence": vault_v.confidence,
                    "closest_distance": vault_v.closest_distance,
                    "vault_size": vault_v.vault_size,
                    "matches": [
                        {"attack_id": m.attack_id, "attack_type": m.attack_type, "distance": m.distance}
                        for m in vault_v.matches
                    ],
                    "latency_ms": vault_v.latency_ms,
                }
                if vault_v.is_match and vault_v.confidence >= 0.85:
                    # High-confidence vault match → block
                    return QueryStageOutput(
                        verdict=StageVerdict(
                            action="block",
                            threat_type="embedding_vault_match",
                            confidence=vault_v.confidence,
                            detail=f"Query matches {len(vault_v.matches)} known attack pattern(s) "
                                   f"(distance={vault_v.closest_distance:.4f})",
                        ),
                        sanitized_query=inp.query_text,
                        original_query=inp.query_text,
                        injection_flags=["embedding_vault_match"],
                        scan_tier="embedding_vault",
                        latency_ms=(time.perf_counter() - start) * 1000,
                        embedding_vault_verdict=vault_result,
                        intent_verdict=intent_result,
                    )
                elif vault_v.is_match:
                    injection_flags.append("embedding_vault_match")
            except Exception as e:
                LOG.warning("Embedding vault check failed (fail-open, layer skipped): %s", e)

        # ── RAG-27: operator policy rules scoped to the QUERY stage ──
        # The control plane lets an operator target a rule at pipeline_stage
        # "query" / "retriever" / "ranker" / "generator", and the UI advertises
        # all four. Only ranker_stage ever called ``evaluate_for_stage``, so a
        # rule scoped to "query" saved, compiled, shipped to Redis and displayed
        # as Enabled while evaluating ZERO times — a silent bypass the UI itself
        # invites. This evaluates the org's RAG-domain bundle against the raw
        # query text with stage="query"; rules with an empty pipeline_stage
        # already match every stage, so they are unaffected here (the ranker
        # still evaluates them against document content, which is a different
        # haystack and a different decision).
        if inp.compiled_policies:
            try:
                try:
                    from policy_engine import evaluate_for_stage
                except ImportError:
                    from gateway.policy_engine import evaluate_for_stage
                _pol = evaluate_for_stage(
                    prompt=inp.query_text,
                    response_text="",
                    compiled_policies=inp.compiled_policies,
                    stage="query",
                    actor=getattr(inp, "actor", None),
                )
                if _pol.action == "block":
                    LOG.info(
                        "RAG query blocked by operator policy rule (RAG-27): %s",
                        _pol.message,
                    )
                    return QueryStageOutput(
                        verdict=StageVerdict(
                            action="block",
                            threat_type="policy_violation",
                            confidence=1.0,
                            detail=_pol.message or "Blocked by policy",
                            matched_patterns=[],
                        ),
                        sanitized_query=inp.query_text,
                        original_query=inp.query_text,
                        injection_flags=list(_pol.matched_rule_names or []),
                        scan_tier="policy",
                        latency_ms=(time.perf_counter() - start) * 1000,
                    )
            except Exception:  # noqa: BLE001
                # Advisory layer: a malformed bundle must never break retrieval.
                # (The scanner below is the baseline control and still runs.)
                LOG.warning("RAG-27 query-stage policy evaluation failed", exc_info=True)

        # ── Scanner: Tier1 regex + Tier1.5 fuzzy + optional Tier2 Bedrock ──
        scan_tier = "none"
        if self._scanner is not None:
            # RAG-33: honor the org's Tier-2 (Bedrock guard model) switch.
            # This stage called the Tier-1-only ``scan_prompt``, so semantic-only
            # injections — the exact class Tier-2 exists to catch — passed on the
            # RAG path even when the operator had Tier-2 ON for the org. (RAG
            # INGEST already ran Tier-2, so the asymmetry was query-vs-ingest,
            # not just RAG-vs-chat.)
            # The override is passed BY IDENTITY: the scanner distinguishes None
            # ("no org opinion — use the gateway default") from False ("the
            # operator explicitly turned it OFF"), so collapsing them with
            # ``or``/``bool()`` would silently re-enable a stage the operator
            # disabled. Default stays OFF (FirewallConfig.rag_tier2_enabled
            # default=False) — Tier-2 runs only when the operator switches it on.
            _t2 = inp.policy.get("rag_tier2_enabled", None)
            _scan_t2 = getattr(self._scanner, "scan_prompt_with_tier2", None)
            if _t2 is True and callable(_scan_t2):
                verdict = await _scan_t2(
                    inp.query_text,
                    is_rag=True,
                    org_tier2_override=True,
                    org_slug=str(inp.policy.get("_org_slug", "") or ""),
                    org_tier2_strict=bool(inp.policy.get("tier2_strict", True)),
                )
            else:
                verdict = await self._scanner.scan_prompt(inp.query_text, is_rag=True)
            scan_tier = getattr(verdict, "tier", "tier_1") or "tier_1"
            # Per-request policy overrides the static startup config so the RAG
            # query stage honors the SAME per-org FirewallConfig the chat path
            # honors. ``input_scan_enabled`` + the injection thresholds are
            # control-plane FirewallConfig fields mirrored per-org by
            # config_sync; the chat path reads them via org_config
            # (main.py:7091/7248/12990), but this stage previously read them ONLY
            # from the static startup CONFIG, so a per-org threshold change never
            # affected RAG query blocking. Fall back to self._config when the
            # caller omits a key — identical to the ``max_query_length`` pattern
            # above, so there is NO behavior change when policy lacks these keys.
            input_scan_on = inp.policy.get(
                "input_scan_enabled", self._config.get("input_scan_enabled", True)
            )

            def _thr(key: str, default: float) -> float:
                val = inp.policy.get(key, self._config.get(key, default))
                try:
                    return float(val)
                except (TypeError, ValueError):
                    return float(default)

            # FIX G3 (path unification): PII-redact the query with the SAME
            # verdict-aware redactor the ingest/embeddings path uses, BEFORE it
            # flows downstream to the retriever (where it is embedded). Computed
            # off the injection verdict we already have so there is no extra scan.
            embed_query = self._redact_pii(
                inp.query_text, verdict, input_scan_enabled=input_scan_on
            )
            threshold = _thr("prompt_injection_threshold", 0.80)
            # RAG-12c: the threshold band must stay MONOTONIC. The rewrite arm below
            # fires on ``rewrite_threshold <= confidence < threshold``, so RAISING the
            # block threshold WIDENED the rewrite band and silently converted hard
            # blocks into pass-with-rewrite: at the fully-legal max 1.0, rag_poisoning
            # (0.9), tier-0.5 obfuscated injection (0.95), obfuscated pii/secret (0.9)
            # and tier-1.5 fuzzy (0.75-1.0) ALL flipped from block to rewrite. Clamping
            # to ``threshold`` makes the band COLLAPSE rather than invert as the block
            # threshold rises — raising it can now only block more, never less.
            rewrite_threshold = min(_thr("prompt_rewrite_threshold", 0.50), threshold)
            downgrade_threshold = min(
                _thr("prompt_downgrade_threshold", 0.40), rewrite_threshold
            )
            # RAG-12c: rewriting silently EDITS the user's query — an enforcement
            # action in its own right. The north-star invariant is that the firewall
            # never takes an action the operator did not select, so the rewrite arm is
            # opt-in per-org (default OFF). With it off a would-be rewrite is NOT newly
            # blocked: it falls through to the sub-threshold arm and returns allow+flag,
            # so this can only relax enforcement, never over-block.
            rewrite_enabled = bool(
                inp.policy.get(
                    "rag_rewrite_enabled", self._config.get("rag_rewrite_enabled", False)
                )
            )

            # ── RAG-12b: scanner ``redact`` verdicts were invisible in the audit ──
            #
            # The scanner returns action="redact" for pii (0.85) / secret (0.9) /
            # credential (0.9) / multi-turn-split (0.85). Every arm below gates on
            # "block" or ("block", "flag"), so "redact" matched NONE of them and the
            # request fell through to the terminal action="allow" return — a query
            # carrying a live AWS key was audited as clean. (The data leak itself is
            # already closed: ``embed_query`` above is the redacted text.) Record the
            # detection so the stage verdict and telemetry stop under-reporting it.
            # This is the SAME treatment the sub-threshold arm already gives a "flag"
            # verdict, and the same treatment Tier-2 redaction already gets (the
            # Bedrock adapter maps recommended="redact" onto action="flag") — Tier-1
            # redact was the ONLY detection class missing from the audit trail.
            if verdict.action == "redact" and verdict.threat_type:
                injection_flags.append(verdict.threat_type)

            if verdict.action == "block" and verdict.confidence >= threshold:
                # Hard block: confidence exceeds block threshold
                # Self-harden: add to embedding vault
                if self._embedding_vault is not None:
                    try:
                        await self._embedding_vault.add_attack(
                            inp.query_text,
                            attack_type=verdict.threat_type or "detected",
                        )
                    except Exception:
                        pass

                return QueryStageOutput(
                    verdict=StageVerdict(
                        action="block",
                        threat_type=verdict.threat_type,
                        confidence=verdict.confidence,
                        detail=verdict.detail,
                        matched_patterns=verdict.matched_patterns,
                    ),
                    sanitized_query=inp.query_text,
                    original_query=inp.query_text,
                    injection_flags=[verdict.threat_type],
                    scan_tier=scan_tier,
                    latency_ms=(time.perf_counter() - start) * 1000,
                    embedding_vault_verdict=vault_result,
                    intent_verdict=intent_result,
                )

            # ── RAG-12b: fail-closed when redaction DETECTED but could not mask ──
            #
            # Ported from the ingest path's byte-verify (G4). ``_redact_pii`` is
            # best-effort by design, so a pii/secret verdict whose redactor handed back
            # byte-identical text means the detector found a value it has no mask for —
            # embedding it ships the raw value to the third-party embedding provider.
            # Gated on the operator's EXISTING ``scan_block_on_pii`` posture (default
            # OFF, matching config.py:109) — FROZEN invariant: no action the operator
            # did not select. With the posture off this stays a flag-only detection
            # (recorded above) and the query proceeds exactly as it does today.
            if self._redaction_incomplete(inp.query_text, embed_query, verdict, input_scan_on):
                block_on_pii = bool(
                    inp.policy.get(
                        "scan_block_on_pii", self._config.get("scan_block_on_pii", False)
                    )
                )
                LOG.warning(
                    "Query redaction masked NOTHING for a %s detection (%s) — "
                    "unmaskable value would be embedded (block_on_pii=%s)",
                    verdict.threat_type, verdict.detail, block_on_pii,
                )
                if block_on_pii:
                    return QueryStageOutput(
                        verdict=StageVerdict(
                            action="block",
                            threat_type=verdict.threat_type,
                            confidence=verdict.confidence,
                            detail=(
                                f"{verdict.threat_type} detected but not maskable — "
                                f"refusing to embed unredacted query: {verdict.detail}"
                            ),
                            matched_patterns=verdict.matched_patterns,
                        ),
                        sanitized_query=inp.query_text,
                        original_query=inp.query_text,
                        injection_flags=injection_flags,
                        scan_tier=scan_tier,
                        latency_ms=(time.perf_counter() - start) * 1000,
                        embedding_vault_verdict=vault_result,
                        intent_verdict=intent_result,
                    )

            # Rewrite path: confidence between rewrite and block thresholds
            # (RAG-12c: gated on the operator's explicit ``rag_rewrite_enabled`` opt-in)
            if (
                rewrite_enabled
                and verdict.action in ("block", "flag")
                and rewrite_threshold <= verdict.confidence < threshold
                and verdict.matched_patterns
            ):
                rewritten = self._attempt_rewrite(inp.query_text, verdict.matched_patterns)
                if rewritten and rewritten != inp.query_text:
                    # FIX G3: the rewritten text is what proceeds to embedding —
                    # PII-redact it too so the rewrite path matches ingest.
                    rewritten = self._redact_pii(
                        rewritten, verdict, input_scan_enabled=input_scan_on
                    )
                    LOG.info(
                        "Query rewritten: removed %d injection patterns (confidence=%.2f)",
                        len(verdict.matched_patterns),
                        verdict.confidence,
                    )
                    return QueryStageOutput(
                        verdict=StageVerdict(
                            action="rewrite",
                            threat_type=verdict.threat_type,
                            confidence=verdict.confidence,
                            detail=f"Query rewritten: removed {len(verdict.matched_patterns)} injection patterns",
                            matched_patterns=verdict.matched_patterns,
                            rewritten_text=rewritten,
                        ),
                        sanitized_query=rewritten,
                        rewritten_query=rewritten,
                        original_query=inp.query_text,
                        injection_flags=[verdict.threat_type],
                        scan_tier=scan_tier,
                        latency_ms=(time.perf_counter() - start) * 1000,
                        embedding_vault_verdict=vault_result,
                        intent_verdict=intent_result,
                    )

            # Model downgrade path: confidence between downgrade and rewrite thresholds
            if (
                verdict.action in ("block", "flag")
                and downgrade_threshold <= verdict.confidence < rewrite_threshold
            ):
                downgrade_model = self._config.get("rag_downgrade_model", os.getenv("BEDROCK_MODEL", "global.anthropic.claude-haiku-4-5-20251001-v1:0"))
                LOG.info(
                    "Model downgrade recommended: confidence=%.2f → model=%s",
                    verdict.confidence, downgrade_model,
                )
                return QueryStageOutput(
                    verdict=StageVerdict(
                        action="model_downgrade",
                        threat_type=verdict.threat_type,
                        confidence=verdict.confidence,
                        detail=f"Medium-risk query: downgrade to {downgrade_model}",
                        downgrade_model=downgrade_model,
                    ),
                    sanitized_query=embed_query,
                    original_query=inp.query_text,
                    injection_flags=[verdict.threat_type],
                    scan_tier=scan_tier,
                    latency_ms=(time.perf_counter() - start) * 1000,
                    embedding_vault_verdict=vault_result,
                    intent_verdict=intent_result,
                )

            # Sub-threshold detections → flag for downstream escalation
            if verdict.action in ("block", "flag") and verdict.confidence > 0:
                injection_flags.append(verdict.threat_type)

        # ── LLM-as-Judge (async, higher latency, higher accuracy) ──
        if self._llm_judge is not None and self._llm_judge.enabled:
            try:
                judge_v = await self._llm_judge.judge(inp.query_text)
                llm_judge_result = {
                    "is_injection": judge_v.is_injection,
                    "confidence": judge_v.confidence,
                    "attack_type": judge_v.attack_type,
                    "reasoning": judge_v.reasoning,
                    "judge_model": judge_v.judge_model,
                    "latency_ms": judge_v.latency_ms,
                }
                if judge_v.is_injection and judge_v.confidence >= 0.85:
                    # High-confidence LLM judge detection → block
                    # Self-harden: add to vault
                    if self._embedding_vault is not None:
                        try:
                            await self._embedding_vault.add_attack(
                                inp.query_text,
                                attack_type=judge_v.attack_type or "llm_detected",
                            )
                        except Exception:
                            pass

                    return QueryStageOutput(
                        verdict=StageVerdict(
                            action="block",
                            threat_type=f"llm_judge:{judge_v.attack_type}",
                            confidence=judge_v.confidence,
                            detail=f"LLM Judge detected injection: {judge_v.reasoning[:200]}",
                        ),
                        sanitized_query=inp.query_text,
                        original_query=inp.query_text,
                        injection_flags=["llm_judge_detection"],
                        scan_tier="llm_judge",
                        latency_ms=(time.perf_counter() - start) * 1000,
                        llm_judge_verdict=llm_judge_result,
                        embedding_vault_verdict=vault_result,
                        intent_verdict=intent_result,
                    )
                elif judge_v.is_injection:
                    injection_flags.append(f"llm_judge:{judge_v.attack_type}")
            except Exception as e:
                LOG.warning("LLM Judge failed (fail-open, layer skipped): %s", e)

        return QueryStageOutput(
            verdict=StageVerdict(
                action="flag" if injection_flags else "allow",
                threat_type=injection_flags[0] if injection_flags else "",
                confidence=0.0,
            ),
            # FIX G3: the allow/flag path proceeds to retrieval, so the query that
            # gets embedded is the PII-redacted ``embed_query`` (== raw text when
            # input scanning is disabled or nothing matched).
            sanitized_query=embed_query,
            original_query=inp.query_text,
            injection_flags=injection_flags,
            scan_tier=scan_tier,
            latency_ms=(time.perf_counter() - start) * 1000,
            llm_judge_verdict=llm_judge_result,
            embedding_vault_verdict=vault_result,
            intent_verdict=intent_result,
        )

    def _redact_pii(self, text: str, verdict: Any, input_scan_enabled: bool | None = None) -> str:
        """PII-redact the RAG query BEFORE it is embedded/sent to the retriever.

        FIX G3 (path unification): RAG ingest (``_scan_redact_embedding_inputs``)
        and ``/v1/embeddings`` already redact PII/secrets with the verdict-aware
        ``InputScanner.redact_pii`` + a fail-closed 7+-digit backstop before the
        text leaves the gateway to the embedding provider. The RAG QUERY path
        scanned for injection but embedded the raw query verbatim, so a customer
        email/SSN/phone in the query was sent to the third-party embedding
        provider unredacted. This applies the SAME redaction the ingest path uses
        so all three embedding paths converge (no divergence).

        Gated by the SAME ``input_scan_enabled`` config as the ingest/embeddings
        helper, so there is NO behavior change when input scanning is disabled.
        Best-effort: any failure returns the original text (the injection scan
        above is the security-critical gate; redaction is defense-in-depth and
        must never crash the query path).
        """
        scanner = self._scanner
        enabled = (
            self._config.get("input_scan_enabled", True)
            if input_scan_enabled is None
            else input_scan_enabled
        )
        if scanner is None or not enabled:
            return text
        if not isinstance(text, str) or not text.strip():
            return text
        try:
            redacted = scanner.redact_pii(text, verdict=verdict)
        except Exception as e:  # noqa: BLE001
            # Redaction crashed. Do NOT embed fully-raw text (the "never embed
            # unredacted content" invariant): fall through to the digit backstop
            # below so 7+-digit phone/SSN/ID runs are still masked, rather than
            # returning the raw query. WARN so the redactor failure is observable
            # (a silent debug-swallow here would be a hidden fallback that embeds PII).
            LOG.warning("Query PII redaction failed (applying digit backstop): %s", e)
            redacted = text
        # Fail-closed digit backstop (mirrors _scan_redact_embedding_inputs and
        # llm_router._apply_redaction): mask any run of 7+ digits the original
        # carried that the verdict-aware redactor did not remove, using the SAME
        # ``***-***-####`` shape so the query and ingest paths redact identically.
        #
        # UNCONDITIONAL on the query path (unlike generator_stage, which gates on
        # detected-PII to protect benign document numbers in user-facing answers):
        # a query is short and user-typed, a bare 7+-digit run is far more likely a
        # phone/ID than prose, and masking only the embedded RETRIEVAL query has no
        # answer-corruption cost. This catches BOTH a bare-phone-only query AND a
        # bare phone co-occurring with other PII — the mixed email+phone leak the
        # old ``== text`` gate missed — matching the embeddings/ingest path.
        for run in set(re.findall(r"\d{7,}", text)):
            if run in redacted:
                redacted = redacted.replace(run, f"***-***-{run[-4:]}")
        return redacted

    def _redaction_incomplete(
        self, original: str, redacted: str, verdict: Any, input_scan_enabled: bool
    ) -> bool:
        """RAG-12b: True when a pii/secret verdict produced NO masking at all.

        ``_redact_pii`` is best-effort by design (see its docstring): it never
        raises, so "the redactor could not mask what the detector found" was
        indistinguishable from "nothing needed masking" and the raw value was
        embedded silently. This is the query-path analog of the ingest path's
        byte-verify (G4): the verdict NAMES a pii/secret detection, yet the
        verdict-aware redactor plus the 7+-digit backstop handed back
        byte-identical text — so the detected value survives into the embedding
        request to the third-party provider.

        Returns False (never a detection) when redaction did not run at all —
        no scanner, or input scanning disabled — since unchanged text is the
        expected, operator-selected outcome there, not a failure.
        """
        if self._scanner is None or not input_scan_enabled:
            return False
        if getattr(verdict, "threat_type", "") not in ("pii", "secret"):
            return False
        return bool(original) and redacted == original

    @staticmethod
    def _attempt_rewrite(query: str, patterns: list[str]) -> str:
        """Strip injection patterns while preserving legitimate query content.

        Returns the cleaned query if meaningful content remains (>20% of
        original length), otherwise returns empty string so the caller
        can fall through to another action.

        RAG-12d: ``patterns`` is ``ScanVerdict.matched_patterns``, which holds
        matched TEXT / evidence — never a regex. Every producer stores either the
        matched span (``redact_all(match.group(0))``), a detector LABEL
        (``list(pii_matched.keys())``), or free-text Tier-2 Bedrock evidence
        (``bedrock_evidence[:5]``). Compiling that as a PATTERN treated
        attacker-influenced text as code: a stray ``[`` raised ``re.error`` and a
        nested quantifier (``(((((a+)+)+)+)+)$``) is catastrophic backtracking
        (ReDoS) burning the request thread. Latent today because no reachable
        producer carries metacharacters — but live the moment Tier-2 is enabled,
        since Bedrock free-text evidence flows straight in. ``re.escape`` makes
        every pattern a LITERAL sequence: no metacharacter is interpreted, and a
        literal pattern cannot backtrack. ``re.IGNORECASE`` is kept (rather than a
        plain ``str.replace``) because the Tier-0.5 producer stores evidence taken
        from the LOWERCASED deobfuscation buffer, which a case-sensitive replace
        would silently fail to strip.
        """
        rewritten = query
        for pattern in patterns:
            rewritten = re.sub(
                re.escape(str(pattern)), "", rewritten, flags=re.IGNORECASE
            ).strip()
        # Collapse multiple spaces
        rewritten = re.sub(r"\s{2,}", " ", rewritten).strip()
        # Only return if meaningful content remains
        if len(rewritten) > len(query) * 0.2 and rewritten.strip():
            return rewritten.strip()
        return ""
