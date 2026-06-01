"""
Grounding Guard — D_G10 Semantic Hallucination Grounding (Bedrock Titan v2).

Two public APIs:

* :meth:`GroundingGuard.score` returns a raw max-cosine similarity in
  [0, 1] (or ``None`` on fail-open). Designed to be plugged INTO the
  existing ``output_guard.OutputGuard`` as the ``semantic`` / ``hybrid``
  backend for ``hallucination_grounding_mode`` per migration 0022.

* :meth:`GroundingGuard.check_grounding` returns a full :class:`GroundingVerdict`
  with an action (``warn`` / ``downgrade`` / ``block``). Used by tests and as
  a standalone callable for any future surface that wants the guard to own
  the action decision.

All cross-tenant safety constraints from :mod:`bedrock_embedder` apply:
inputs MUST be PII-redacted by the caller before invoking this guard.
"""
from __future__ import annotations

import asyncio
import logging
import math
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Iterable, List, Optional

if TYPE_CHECKING:  # pragma: no cover
    from ai_mesh_gateway.rag_pipeline.bedrock_embedder import BedrockEmbedder

try:
    from ai_mesh_gateway.telemetry_ops import (
        EVENT_CLASS_HALLUCINATION_DETECTED,
        emit_operational_event,
    )
except ModuleNotFoundError:  # pragma: no cover — container path layout
    from telemetry_ops import (  # type: ignore[no-redef]
        EVENT_CLASS_HALLUCINATION_DETECTED,
        emit_operational_event,
    )

try:
    from ai_mesh_gateway.rag_pipeline.bedrock_embedder import (
        BedrockCircuitOpenError,
        PIIRedactionRequiredError,
    )
except ModuleNotFoundError:  # pragma: no cover
    from rag_pipeline.bedrock_embedder import (  # type: ignore[no-redef]
        BedrockCircuitOpenError,
        PIIRedactionRequiredError,
    )

LOG = logging.getLogger("gateway.grounding_guard")


VALID_MODES = {"off", "warn", "downgrade", "block"}


@dataclass
class GroundingVerdict:
    """Verdict returned by :func:`GroundingGuard.check_grounding`."""

    action: str = "allow"        # allow | warn | downgrade | block
    grounded: bool = True
    max_similarity: float = 0.0
    threshold: float = 0.0
    mode: str = "off"
    error: str = ""               # populated when guard fails open
    per_chunk_scores: List[float] = field(default_factory=list)


def _cosine(a: Iterable[float], b: Iterable[float]) -> float:
    """Cosine similarity for two equal-length sequences of floats.

    Titan v2 vectors are L2-normalized (``normalize=True`` in invoke body),
    so this reduces to a dot product. We still compute the full form to
    stay correct if a caller injects un-normalized vectors.
    """
    a_list = list(a)
    b_list = list(b)
    if not a_list or len(a_list) != len(b_list):
        return 0.0
    dot = 0.0
    na = 0.0
    nb = 0.0
    for x, y in zip(a_list, b_list):
        dot += x * y
        na += x * x
        nb += y * y
    denom = math.sqrt(na) * math.sqrt(nb)
    if denom == 0.0:
        return 0.0
    # Clamp tiny FP overshoot
    val = dot / denom
    if val > 1.0:
        return 1.0
    if val < -1.0:
        return -1.0
    return val


class GroundingGuard:
    """Post-LLM grounding check using Bedrock Titan v2 embeddings."""

    def __init__(self, *, embedder: "BedrockEmbedder") -> None:
        self._embedder = embedder

    # ------------------------------------------------------------------ #
    # score() — plug-in backend for the existing OutputGuard algorithm    #
    # selector (lexical | semantic | hybrid) per migration 0022.          #
    # ------------------------------------------------------------------ #
    async def score(
        self,
        *,
        answer_text: str,
        context_chunks: List[str],
        org_slug: str,
        assume_redacted: bool = False,
    ) -> Optional[float]:
        """Return max cosine similarity in [0, 1], or ``None`` on fail-open.

        Semantics (matches the lexical ``_compute_grounding_score`` scale
        used by ``output_guard.OutputGuard``: higher == better-grounded):

        * ``1.0`` — perfect semantic match between answer and at least
          one context chunk.
        * Negative cosine is clamped to ``0.0`` (no negative grounding).
        * Empty answer or empty context → ``None`` (caller should treat as
          "no semantic signal", falling back to lexical or default).
        * Circuit OPEN or any embedder error → ``None`` (fail-OPEN).
        * PII-redaction-required raises :class:`PIIRedactionRequiredError`
          (fail-CLOSED — caller violated the assume_redacted contract).
        """
        if not answer_text or not answer_text.strip():
            return None
        if not context_chunks:
            return None

        try:
            all_vecs = await asyncio.gather(
                self._embedder.embed(
                    answer_text,
                    org_slug=org_slug,
                    assume_redacted=assume_redacted,
                ),
                *(
                    self._embedder.embed(
                        chunk,
                        org_slug=org_slug,
                        assume_redacted=assume_redacted,
                    )
                    for chunk in context_chunks
                ),
            )
        except PIIRedactionRequiredError:
            # Fail-CLOSED — the caller violated the redaction contract.
            raise
        except BedrockCircuitOpenError as exc:
            LOG.warning("GroundingGuard.score: bedrock circuit OPEN; failing open: %s", exc)
            return None
        except Exception:  # noqa: BLE001
            LOG.exception("GroundingGuard.score: embed failed; failing open")
            return None

        answer_vec = all_vecs[0]
        chunk_vecs = list(all_vecs[1:])
        scores = [_cosine(answer_vec, cv) for cv in chunk_vecs]
        if not scores:
            return None
        max_sim = max(scores)
        # Clamp negative cosines to 0 (no negative grounding signal).
        return max(0.0, float(max_sim))

    async def check_grounding(
        self,
        *,
        answer_text: str,
        context_chunks: List[str],
        org_slug: str,
        mode: str,
        threshold: float,
        assume_redacted: bool = False,
    ) -> GroundingVerdict:
        """Return a :class:`GroundingVerdict` for the given answer.

        Args:
            answer_text: LLM-generated answer (PII-redacted by caller).
            context_chunks: Retrieved/ranker-approved context strings.
            org_slug: Tenant identifier for embedder cache isolation.
            mode: One of ``off`` / ``warn`` / ``downgrade`` / ``block``.
            threshold: Min cosine similarity required to consider answer
                grounded. Range [0.0, 1.0].
            assume_redacted: Propagated to embedder; MUST be True.

        Failure modes:
            * Embedder breaker OPEN → fail-OPEN with action=allow + error
              field populated. Telemetry is still emitted with
              ``event_class=hallucination_detected`` ``decision="error"``.
            * Empty answer or empty context → action=allow (nothing to score).
            * Mode == "off" → action=allow without invoking embedder.
        """
        verdict = GroundingVerdict(mode=mode, threshold=threshold)

        if mode == "off" or mode not in VALID_MODES:
            verdict.action = "allow"
            return verdict

        if not answer_text or not answer_text.strip():
            verdict.action = "allow"
            return verdict

        if not context_chunks:
            # No grounding source available — treat as ungrounded warn but
            # do not block (no evidence either way). Mode controls behavior.
            verdict.grounded = False
            verdict.action = "warn" if mode in {"warn", "downgrade", "block"} else "allow"
            await self._emit(
                org_slug=org_slug,
                decision=verdict.action,
                max_sim=0.0,
                threshold=threshold,
                mode=mode,
                reason="empty_context",
            )
            return verdict

        # ---- 1) Embed answer + chunks (parallel, fail-OPEN on errors) ---- #
        try:
            all_vecs = await asyncio.gather(
                self._embedder.embed(
                    answer_text,
                    org_slug=org_slug,
                    assume_redacted=assume_redacted,
                ),
                *(
                    self._embedder.embed(
                        chunk,
                        org_slug=org_slug,
                        assume_redacted=assume_redacted,
                    )
                    for chunk in context_chunks
                ),
            )
            answer_vec = all_vecs[0]
            chunk_vecs = list(all_vecs[1:])
        except PIIRedactionRequiredError:
            # Configuration bug — caller failed to attest redaction.
            # Fail-CLOSED here since this is not a runtime/transport failure.
            verdict.action = "block"
            verdict.error = "pii_redaction_required"
            verdict.grounded = False
            await self._emit(
                org_slug=org_slug,
                decision="block",
                max_sim=0.0,
                threshold=threshold,
                mode=mode,
                reason="pii_redaction_required",
            )
            raise
        except BedrockCircuitOpenError as exc:
            verdict.action = "allow"  # fail-OPEN to preserve UX
            verdict.error = "circuit_open"
            LOG.warning("OutputGuard: bedrock circuit OPEN; failing open: %s", exc)
            await self._emit(
                org_slug=org_slug,
                decision="error",
                max_sim=0.0,
                threshold=threshold,
                mode=mode,
                reason="circuit_open",
            )
            return verdict
        except Exception as exc:  # noqa: BLE001
            verdict.action = "allow"  # fail-OPEN
            verdict.error = type(exc).__name__
            LOG.exception("OutputGuard: embed failed; failing open")
            await self._emit(
                org_slug=org_slug,
                decision="error",
                max_sim=0.0,
                threshold=threshold,
                mode=mode,
                reason=f"embed_error:{type(exc).__name__}",
            )
            return verdict

        # ---- 2) Score ---- #
        scores = [_cosine(answer_vec, cv) for cv in chunk_vecs]
        max_sim = max(scores) if scores else 0.0
        verdict.per_chunk_scores = scores
        verdict.max_similarity = max_sim
        verdict.grounded = max_sim >= threshold

        # ---- 3) Decide action ---- #
        if verdict.grounded:
            verdict.action = "allow"
            return verdict

        if mode == "block":
            verdict.action = "block"
        elif mode == "downgrade":
            verdict.action = "downgrade"
        else:  # warn
            verdict.action = "warn"

        await self._emit(
            org_slug=org_slug,
            decision=verdict.action,
            max_sim=max_sim,
            threshold=threshold,
            mode=mode,
            reason="below_threshold",
        )
        return verdict

    # ---------------------------------------------------------------- #
    async def _emit(
        self,
        *,
        org_slug: str,
        decision: str,
        max_sim: float,
        threshold: float,
        mode: str,
        reason: str,
    ) -> None:
        try:
            await emit_operational_event(
                org_slug=org_slug,
                event_class=EVENT_CLASS_HALLUCINATION_DETECTED,
                severity="warning" if decision in {"block", "downgrade", "warn"} else "info",
                metadata={
                    "decision": decision,
                    "max_similarity": round(float(max_sim), 6),
                    "threshold": round(float(threshold), 6),
                    "mode": mode,
                    "reason": reason,
                },
            )
        except Exception:  # noqa: BLE001
            LOG.exception("OutputGuard: telemetry emit failed; swallowing")
