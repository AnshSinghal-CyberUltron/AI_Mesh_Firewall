"""Fail-closed BYOK embedding for the RAG/vector firewall.

The per-org embedding model comes from the org's ``VectorProviderConfig``
(``embedding_model``), and the provider API key is the org's own (BYOK). Two
routes:

* **Pinecone-hosted models** (multilingual-e5-large, llama-text-embed-v2, …):
  embedded by Pinecone's Inference API using the org's Pinecone key — fully
  BYOK and key-free for the embedding step. The caller passes a live
  ``pinecone_client``.
* **External models** (OpenAI ``text-embedding-3-small``, Bedrock Titan, …):
  routed through litellm. A per-model BYOK key may be supplied via
  ``provider_api_key``; if absent, litellm uses the gateway environment.

The single invariant this module enforces is **fail-closed**: it NEVER returns
a zero/empty/garbage vector. On a provider error it re-raises (so the caller
returns an explicit error), and if a provider hands back a zero or
wrong-shaped vector it raises ``EmbeddingDegradedError`` rather than letting a
semantically-meaningless vector poison RAG retrieval. The previous behaviour
(``vector_client`` silently substituting ``[0.0] * dim``) returned arbitrary
nearest-neighbours as if they were real matches.
"""
from __future__ import annotations

import logging
from typing import Any, List, Optional, Sequence

LOG = logging.getLogger("gateway.byok_embedder")


class EmbeddingConfigError(RuntimeError):
    """No usable embedding configuration (model/provider) for the org."""


class EmbeddingDegradedError(RuntimeError):
    """The provider returned a zero / empty / wrong-shaped embedding vector."""


def _is_degraded_vector(vector: Sequence[float]) -> bool:
    """A vector a fail-closed embedder must NEVER return: empty, non-numeric,
    non-finite (NaN / +/-inf), or all-zero.

    NaN/inf are checked WITHOUT ``math`` via ``x != x`` (only NaN) and an
    explicit inf comparison — a previous version only tested ``== 0.0`` so a
    provider returning ``[nan, ...]`` or an inf-laden vector slipped past the
    gate and was returned as a "real" embedding (fail-closed breach)."""
    if not vector:
        return True  # empty
    saw_nonzero = False
    _INF = float("inf")
    for x in vector:
        try:
            xf = float(x)
        except (TypeError, ValueError, OverflowError):
            # OverflowError: a Python int too large for IEEE-754 (e.g. 10**309)
            # or a custom __float__ that overflows. Treat any value whose
            # float() conversion raises as non-numeric -> degraded (fail-closed,
            # and keeps EmbeddingDegradedError as the documented contract).
            return True  # non-numeric / non-finite-on-convert
        if xf != xf or xf == _INF or xf == -_INF:
            return True  # NaN (xf != xf) or +/-inf
        if xf != 0.0:
            saw_nonzero = True
    return not saw_nonzero  # all-zero


def embed_texts(
    texts: Sequence[str],
    *,
    embedding_model: str,
    input_type: str = "passage",
    pinecone_client: Optional[Any] = None,
    provider_api_key: Optional[str] = None,
    expected_dim: Optional[int] = None,
) -> List[List[float]]:
    """Embed ``texts`` with the org's BYOK embedding model, failing closed.

    Args:
        texts: documents (``input_type='passage'``) or a query
            (``input_type='query'``) to embed.
        embedding_model: the org's configured embedding model id.
        input_type: ``'passage'`` or ``'query'`` (asymmetric models like e5).
        pinecone_client: pass a live Pinecone client ONLY for Pinecone-hosted
            models — its presence selects the Pinecone Inference route.
        provider_api_key: BYOK key for external (litellm) models; ``None`` ->
            gateway environment credentials.
        expected_dim: if given, every returned vector must match this length.

    Raises:
        EmbeddingConfigError: no embedding model configured.
        EmbeddingDegradedError: a returned vector is zero/empty/wrong-shaped.
        Exception: any provider/litellm error is re-raised unchanged so the
            caller can map it to an HTTP status (never swallowed to a fallback).
    """
    if not embedding_model:
        raise EmbeddingConfigError(
            "No embedding model is configured for this organization's vector "
            "provider; cannot embed (fail-closed)."
        )
    items = [t if isinstance(t, str) else str(t) for t in texts]
    if not items:
        return []

    if pinecone_client is not None:
        # Pinecone Inference API (BYOK via the org's Pinecone key).
        resp = pinecone_client.inference.embed(
            model=embedding_model,
            inputs=items,
            parameters={"input_type": input_type, "truncate": "END"},
        )
        vectors: List[List[float]] = []
        for d in resp.data:
            vals = getattr(d, "values", None)
            if vals is None and hasattr(d, "get"):
                vals = d.get("values")
            vectors.append(list(vals) if vals is not None else [])
    else:
        import litellm

        kwargs: dict[str, Any] = {"model": embedding_model, "input": items}
        if provider_api_key:
            kwargs["api_key"] = provider_api_key
        resp = litellm.embedding(**kwargs)
        # Build from however many entries the provider returned (NOT
        # range(len(items))) so a short response leaves ``vectors`` shorter and
        # trips the count-mismatch guard below with EmbeddingDegradedError,
        # instead of raising a raw IndexError mid-comprehension.
        vectors = [list(d["embedding"]) for d in resp.data]

    if len(vectors) != len(items):
        raise EmbeddingDegradedError(
            f"Embedding provider returned {len(vectors)} vectors for "
            f"{len(items)} inputs (model='{embedding_model}')."
        )
    for idx, vec in enumerate(vectors):
        if _is_degraded_vector(vec):
            raise EmbeddingDegradedError(
                f"Embedding provider returned a degraded vector "
                f"(zero/empty/NaN/inf) (model='{embedding_model}', index={idx}); "
                f"refusing to embed into RAG with a meaningless vector (fail-closed)."
            )
        if expected_dim is not None and len(vec) != expected_dim:
            raise EmbeddingDegradedError(
                f"Embedding dimension mismatch: got {len(vec)}, expected "
                f"{expected_dim} (model='{embedding_model}', index={idx})."
            )
    return vectors
