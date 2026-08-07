"""Generator stage: verify approved context, sanitize documents, and prepare context binding."""
from __future__ import annotations

import hashlib
import json as _json
import logging
import re
import time
import uuid
from typing import Any, TYPE_CHECKING

from .contracts import DocumentManifest, GeneratorStageInput, GeneratorStageOutput, StageVerdict
from .escalation import get_escalation_config

if TYPE_CHECKING:
    from leakage_detector import SemanticLeakageDetector
    from canary_tokens import CanaryTokenManager

LOG = logging.getLogger("gateway.rag_pipeline.generator_stage")

# E11: bare-digit fail-closed backstop. ``detect_and_redact_typed`` (the typed
# placeholder redactor the ingest path uses) intentionally skips separatorless
# digit runs for ``phone_us`` (order IDs / revenue figures are not PII), so a
# bare 10-digit phone like ``8929554991`` survives it. This is the SAME
# ``***-***-####`` shape + 7+-digit rule used in
# ``main._scan_redact_embedding_inputs`` so a bare phone in retrieved-context
# plain text is masked before egress.
_BARE_DIGIT_RUN_RE = re.compile(r"\d{7,}")
# A phone/contact cue immediately preceding a bare digit run. Used to mask a bare
# phone in an otherwise-clean retrieved chunk WITHOUT over-redacting context-less
# numerics (order IDs / SKUs / revenue / epochs) — i.e. it preserves the F12 concern
# while still failing safe on a clearly-labelled phone like "contact number is …".
_PHONE_CONTEXT_RE = re.compile(
    r"(?i)(?:phone|telephone|tel|mobile|cell|fax|call|dial|contact|reach|number|no\.)"
    r"[^\d]{0,20}?(\d{7,})"
)

# RAG-04 (2026-08-04): the typed redactor scans RAW text only, so a secret carried in a
# retrieved document as a transport-encoded blob egressed in cleartext — PROVEN with
# base64("AKIAIOSFODNN7EXAMPLE") == "QUtJQUlPU0ZPRE5ON0VYQU1QTEU=", which the redactor
# passed through untouched while the raw AKIA form was correctly masked. ``context_guard``
# already decodes transport layers, but only against INDIRECT_INJECTION_PATTERNS; its
# credential check scans raw text, so nothing on the egress path closed this.
#
# Budgets below are DELIBERATELY tighter than ``patterns``' own decode caps
# (_MAX_DECODE_TOKENS=4096): this backstop runs the FULL pattern catalogue on every decoded
# candidate, so an unbounded candidate count would turn a large document into synchronous
# CPU exhaustion on the egress hot path. A document hiding a secret past the 64th distinct
# encoded blob is pathological, not a real corpus.
_MAX_ENCODED_SECRET_CANDIDATES = 64
_MAX_ENCODED_SECRET_DECODED_CHARS = 8192
_ENCODED_SECRET_PLACEHOLDER = "[ENCODED_SECRET]"


def _redact_encoded_secrets(original: str, redacted: str, detect_fn) -> str:
    """RAG-04: replace transport-encoded blobs whose DECODED form carries a secret.

    Runs after the raw typed pass. For each decoded candidate that the typed redactor
    would redact, the OUTER encoded token is replaced in ``redacted`` by a bare
    ``[ENCODED_SECRET]`` placeholder — so the encoded carrier cannot egress either.

    Reuses ``patterns._iter_transport_decodes`` (the decoder ``_iter_transport_decodes_canon``
    itself wraps) rather than adding a second decoder. The ``_canon`` variant is deliberately
    NOT used here: it yields only the decoded payload, dropping the outer token, and its extra
    views (Cf-stripped / whitespace-collapsed) produce tokens that do not appear verbatim in
    the original bytes — so there is no span to splice. Those obfuscation-compounded forms stay
    with the existing ``context_guard`` detection path.

    Substitution is literal and only fires when a decode actually reveals a secret, so benign
    text — including ordinary base64 that decodes to nothing sensitive — is byte-identical.
    NEVER raises: this is an egress hot path, so any failure degrades to the raw-pass result
    rather than dropping the response.

    KNOWN LIMIT (measured, RAG-04): ``_iter_transport_decodes`` scans only the first
    ``patterns._CANON_MAX_LEN`` (20000) chars of its input, so an ENCODED secret buried past
    ~20KB of a single document is not decoded here. That cap is the shared decoder's, not this
    backstop's — every gateway decode path (context_guard, scanner, output guard) inherits it,
    and raising it would change CPU characteristics for all of them. RAW (unencoded) secrets are
    NOT affected: the typed pass above scans the whole document at any length.
    """
    try:
        try:
            from patterns import _iter_transport_decodes
        except ImportError:  # pragma: no cover - packaging fallback
            from ..patterns import _iter_transport_decodes  # type: ignore[no-redef]

        examined = 0
        replaced: set[str] = set()
        for token, decoded in _iter_transport_decodes(original):
            if examined >= _MAX_ENCODED_SECRET_CANDIDATES:
                break
            examined += 1
            if not decoded or len(decoded) > _MAX_ENCODED_SECRET_DECODED_CHARS:
                continue
            if token in replaced:
                continue  # already neutralized via an outer/earlier layer
            if not detect_fn(decoded).redacted:
                continue
            # The blob leaked precisely BECAUSE it matched nothing raw, so it normally
            # survives the typed pass verbatim; guard anyway in case it was partly consumed.
            if token in redacted:
                redacted = redacted.replace(token, _ENCODED_SECRET_PLACEHOLDER)
                replaced.add(token)
    except Exception:  # pragma: no cover - never break egress on a backstop
        LOG.debug("RAG-04 encoded-secret backstop skipped", exc_info=True)
    return redacted


def _redact_retrieved_pii(content: str) -> str:
    """E11 generation-time PII backstop for retrieved-context plain text.

    Applies the typed-placeholder redactor (``[SSN]``/``[EMAIL]``/``[API_KEY]``…)
    that the INGEST path uses, then a fail-closed 7+-digit backstop for bare
    phones the typed redactor does not catch. Applied UNCONDITIONALLY — regardless
    of ``rag_redaction_enabled`` — so documents ingested while redaction was OFF
    (the default for existing orgs) cannot leak raw PII at generation. This is a
    GATE fail-safe, not a configurable feature.

    Pure-string, no I/O, deterministic. Returns ``content`` unchanged on empty
    input or when nothing matches (no over-redaction of benign content).
    """
    if not content or not isinstance(content, str):
        return content

    try:
        from typed_placeholder_redactor import detect_and_redact_typed
    except ImportError:  # pragma: no cover - packaging fallback
        from ..typed_placeholder_redactor import detect_and_redact_typed  # type: ignore[no-redef]

    redacted = detect_and_redact_typed(content).text

    # RAG-04: decode-then-scan backstop. The typed pass above sees RAW text only, so a
    # secret smuggled through as a base64/hex/base32/base85 blob survives it. Bounded and
    # exception-proof; a no-op on text with no encoded blobs.
    redacted = _redact_encoded_secrets(content, redacted, detect_and_redact_typed)

    # Contextual phone backstop — UNCONDITIONAL (fail-safe): a bare digit run with a
    # phone/contact cue in front of it (e.g. "contact number is 8929554991") is a
    # phone the typed redactor skips. Scoped to cue-preceded runs so context-less
    # numerics (order IDs / SKUs / revenue / epochs) are NOT over-redacted (keeps the
    # F12 concern). Proven LIVE (E11): a clean retrieved chunk "...office contact
    # number is 8929554991..." was returned RAW to the RAG client before this.
    def _mask_ctx(m: "re.Match") -> str:
        run = m.group(1)
        return m.group(0).replace(run, f"***-***-{run[-4:]}")

    redacted = _PHONE_CONTEXT_RE.sub(_mask_ctx, redacted)

    # Co-occurring-PII backstop: when the chunk ALREADY carries other PII, mask any
    # remaining 7+ digit run (a bare value beside an email/SSN is near-certainly PII).
    if redacted != content:
        for run in set(_BARE_DIGIT_RUN_RE.findall(redacted)):
            if run in redacted:
                redacted = redacted.replace(run, f"***-***-{run[-4:]}")

    return redacted


def _redact_metadata_values(value):
    """Recursively mask PII in retrieved-document METADATA values before egress.

    Applies the same GATED ``_redact_retrieved_pii`` to every string value in a
    metadata dict/list, so PII hidden in an UNDECLARED metadata field is not
    returned raw to the client. Ingest-side metadata redaction (G2b) is gated on
    ``rag_redaction_enabled`` (default OFF) and the ranker only scrubs explicitly
    declared ``sensitive_fields``, so arbitrary metadata PII otherwise egresses
    unmasked. Non-string scalars are left untouched.
    """
    if isinstance(value, str):
        return _redact_retrieved_pii(value) if value else value
    if isinstance(value, dict):
        return {k: _redact_metadata_values(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_redact_metadata_values(v) for v in value]
    return value


class GeneratorStage:
    """Verifies approved context, sanitizes documents, and prepares context binding.

    Responsibilities:
    - Approved context verification: only process ranker-approved documents
    - Content integrity check via SHA-256 hash comparison
    - Field-level sensitivity redaction
    - Canary token injection for leakage detection
    - Leakage detector registration for cross-request tracking
    - Context binding storage in Redis for cross-endpoint hallucination grounding
    """

    def __init__(
        self,
        leakage_detector: "SemanticLeakageDetector | None",
        redis_client: Any,
        config: dict,
        canary_token_manager: "CanaryTokenManager | None" = None,
    ) -> None:
        self._leakage = leakage_detector
        self._redis = redis_client
        self._config = config
        self._canary = canary_token_manager

    async def execute(self, inp: GeneratorStageInput) -> GeneratorStageOutput:
        start = time.perf_counter()
        documents = list(inp.documents)
        escalation = get_escalation_config(inp.escalation_level)
        context_chunks: list[str] = []
        leakage_registrations = 0
        context_verified = True


        # ── RAG-30: operator policy rules scoped to the GENERATOR stage ──
        # The control plane lets an operator target a rule at pipeline_stage
        # "generator" and the UI offers it, but only ranker_stage ever called
        # evaluate_for_stage — so such a rule saved, compiled, shipped to Redis
        # and displayed as Enabled while evaluating ZERO times. Rules with an
        # empty pipeline_stage already match every stage and are unaffected.
        if getattr(inp, "compiled_policies", None):
            try:
                try:
                    from policy_engine import evaluate_for_stage
                except ImportError:
                    from gateway.policy_engine import evaluate_for_stage
                # PER-DOCUMENT, not whole-request. Evaluating the CONCATENATED
                # context and blocking on a hit destroys the entire response
                # because ONE document matched — measured: a benign document was
                # dropped alongside the offending one, and the caller got a 403
                # with nothing. That is over-blocking the operator did not ask
                # for. RankerStage already sets the precedent for a
                # document-content stage: drop the offending document, keep the
                # rest, and let the request continue.
                _surviving: list[dict[str, Any]] = []
                _rejected = 0
                for _doc in documents:
                    _pol = evaluate_for_stage(
                        prompt=str(_doc.get("content", "") or ""),
                        response_text="",
                        compiled_policies=inp.compiled_policies,
                        stage="generator",
                        actor=getattr(inp, "actor", None),
                    )
                    if _pol.action == "block":
                        _rejected += 1
                        LOG.info(
                            "RAG generator dropped doc %s by operator policy rule "
                            "(RAG-30): %s",
                            _doc.get("_doc_id", ""), _pol.message,
                        )
                    else:
                        _surviving.append(_doc)
                if _rejected:
                    documents = _surviving
            except Exception:  # noqa: BLE001 — advisory layer, never break retrieval
                LOG.warning("RAG-30 generator-stage policy evaluation failed", exc_info=True)

        if not documents:
            return GeneratorStageOutput(
                verdict=StageVerdict(action="allow"),
                context_integrity_verified=True,
            )

        # ── 0. Approved context verification ──
        approved_ids: set[str] = set()
        approved_hashes: dict[str, str] = {}
        rejected_doc_ids: list[str] = []
        verified_manifest: list[DocumentManifest] = []

        if inp.approved_manifest:
            for m in inp.approved_manifest:
                if "ranker" in m.approved_by:
                    approved_ids.add(m.doc_id)
                    approved_hashes[m.doc_id] = m.content_hash

            verified_docs: list[dict[str, Any]] = []
            for doc in documents:
                doc_id = doc.get("_doc_id", "")
                content = doc.get("content", "")

                # Fail-closed chain-of-custody: a manifest IS present, so every
                # document must carry a retriever-assigned ``_doc_id`` that the
                # ranker approved. A doc with NO _doc_id cannot be proven
                # ranker-approved, so the old ``doc_id and …`` guard silently
                # TRUSTED it (fail-open) — an injected/unidentified chunk reached
                # the generator context with context_integrity_verified still
                # True. Drop it instead (the invariant is "only approved
                # documents reach the generator"). Retriever always sets a
                # non-empty _doc_id, so this never drops a legitimate document.
                if not doc_id or doc_id not in approved_ids:
                    LOG.warning(
                        "Generator received unapproved/unidentified doc %r — removing",
                        doc_id or "<no _doc_id>",
                    )
                    rejected_doc_ids.append(doc_id or "<no _doc_id>")
                    context_verified = False
                    continue

                # Verify content integrity
                if doc_id and approved_hashes.get(doc_id):
                    actual_hash = hashlib.sha256(content.encode()).hexdigest()
                    if actual_hash != approved_hashes[doc_id]:
                        LOG.warning("Document %s content tampered between ranker and generator — removing", doc_id)
                        rejected_doc_ids.append(doc_id)
                        context_verified = False
                        continue

                verified_docs.append(doc)
                if doc_id:
                    verified_manifest.append(DocumentManifest(
                        doc_id=doc_id,
                        content_hash=doc.get("_content_hash", ""),
                        source_stage="retriever",
                        approved_by=["retriever", "ranker", "generator"],
                    ))
            documents = verified_docs
        else:
            # No manifest provided — legacy path, trust all documents
            for doc in documents:
                doc_id = doc.get("_doc_id", "")
                if doc_id:
                    verified_manifest.append(DocumentManifest(
                        doc_id=doc_id,
                        content_hash=doc.get("_content_hash", ""),
                        source_stage="retriever",
                        approved_by=["retriever", "generator"],
                    ))

        if not documents:
            return GeneratorStageOutput(
                verdict=StageVerdict(
                    action="block",
                    threat_type="context_integrity",
                    confidence=0.95,
                    detail="All documents failed context verification",
                ),
                context_integrity_verified=False,
                verified_manifest=verified_manifest,
            )

        # ── 1. Field-level sensitivity redaction ──
        try:
            from context_assembler import redact_structured_fields
        except ImportError:
            from gateway.context_assembler import redact_structured_fields

        max_sensitivity = inp.policy.get("max_sensitivity", "internal")
        for doc in documents:
            content = doc.get("content", "")
            if content:
                # 1a. Field-level sensitivity redaction (JSON-block fields only).
                content = redact_structured_fields(content, max_sensitivity)
                # 1b. E11 generation-time PII backstop: redact PLAIN-TEXT PII
                #     (SSN/email/API-key/bare phone) that redact_structured_fields
                #     leaves untouched. Applied UNCONDITIONALLY so a document
                #     ingested while rag_redaction_enabled was OFF (and thus stored
                #     raw PII) is still redacted before it reaches the generator,
                #     the output-guard grounding context, and the client. GATE
                #     fail-safe — closes the recon-confirmed core-1.2 leak.
                content = _redact_retrieved_pii(content)
                doc["content"] = content
                context_chunks.append(doc["content"])

        # ── 1.5. Canary token injection ──
        canary_word = ""
        if self._canary is not None:
            canary_enabled = self._config.get("canary_tokens_enabled", True)
            if canary_enabled and context_chunks:
                try:
                    combined_context = "\n".join(context_chunks)
                    injected_text, canary_word = self._canary.inject_canary(combined_context)
                    # Replace chunks with canary-injected text
                    context_chunks = [injected_text]
                    LOG.debug("Canary token injected: %s (length=%d)", canary_word[:8], len(canary_word))
                except Exception as e:
                    LOG.debug("Canary injection failed (non-critical): %s", e)

        # ── 2. Register with leakage detector for cross-request tracking ──
        if self._leakage is not None:
            for doc in documents:
                content = doc.get("content", "")
                doc_id = doc.get("id", str(uuid.uuid4()))
                if content:
                    self._leakage.register_confidential_content(doc_id, content)
                    leakage_registrations += 1

        # ── 3. Store context binding in Redis for cross-endpoint grounding ──
        binding_id = ""
        binding_enabled = self._config.get("rag_context_binding_enabled", True)
        if self._redis is not None and context_chunks and binding_enabled:
            # #24: scope the binding key to the org (project_id carries the
            # immutable org id). The reader verifies this prefix so a leaked
            # binding id (it ships in the X-ZeroShield-RAG-Context-ID response
            # header) cannot be replayed cross-tenant within the TTL.
            binding_id = f"rag_ctx:{inp.project_id}:{uuid.uuid4().hex[:16]}"
            ttl = self._config.get("rag_context_binding_ttl", 300)
            try:
                await self._redis.set(binding_id, _json.dumps(context_chunks), ex=ttl)
            except Exception as exc:
                LOG.debug("Context binding store failed (non-critical): %s", exc)
                binding_id = ""

        return GeneratorStageOutput(
            verdict=StageVerdict(action="allow"),
            safe_documents=documents,
            context_chunks=context_chunks,
            context_binding_id=binding_id,
            leakage_registrations=leakage_registrations,
            verified_manifest=verified_manifest,
            context_integrity_verified=context_verified,
            canary_word=canary_word,
        )
