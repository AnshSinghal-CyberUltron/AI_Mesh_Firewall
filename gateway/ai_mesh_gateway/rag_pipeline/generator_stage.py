"""Generator stage: verify approved context, sanitize documents, and prepare context binding."""
from __future__ import annotations

import hashlib
import json as _json
import logging
import time
import uuid
from typing import Any, TYPE_CHECKING

from .contracts import DocumentManifest, GeneratorStageInput, GeneratorStageOutput, StageVerdict
from .escalation import get_escalation_config

if TYPE_CHECKING:
    from leakage_detector import SemanticLeakageDetector
    from canary_tokens import CanaryTokenManager

LOG = logging.getLogger("gateway.rag_pipeline.generator_stage")


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

                if doc_id and doc_id not in approved_ids:
                    LOG.warning("Generator received unapproved doc %s — removing", doc_id)
                    rejected_doc_ids.append(doc_id)
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
                doc["content"] = redact_structured_fields(content, max_sensitivity)
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
            binding_id = f"rag_ctx:{uuid.uuid4().hex[:16]}"
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
