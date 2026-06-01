"""
Bedrock Titan v2 embedder for D_G10 Semantic Hallucination Grounding.

Provides per-org cached, circuit-breaker-protected access to
``amazon.titan-embed-text-v2:0`` (256-dim, L2-normalized).

Design constraints (locked, Layer-2 revised plan):
  * Fail-CLOSED on missing PII redaction (caller MUST pass
    ``assume_redacted=True`` to attest the input has been scrubbed).
  * OrderedDict + monotonic TTL cache (mirrors ``embedding_vault.py``).
  * Cache key: md5 of ``"{org_slug}:{text[:MAX_INPUT_CHARS]}"`` — per-tenant
    isolation prevents cross-org cache hits.
  * Existing Redis-backed ``CircuitBreaker`` is injected (no hand-roll).
  * Single internal metrics dict labelled by result
    (``hit`` | ``miss`` | ``fail`` | ``circuit_open`` | ``truncated``).
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
from collections import OrderedDict
from typing import TYPE_CHECKING, Any, Callable, Dict, List, Optional

if TYPE_CHECKING:  # pragma: no cover
    from gateway.ai_mesh_gateway.circuit_breaker import CircuitBreaker

LOG = logging.getLogger("gateway.bedrock_embedder")


class PIIRedactionRequiredError(RuntimeError):
    """Raised when caller invokes embed() without attesting PII redaction.

    The embedder fail-CLOSES rather than risk leaking raw user prompts to
    AWS Bedrock. Callers MUST pass ``assume_redacted=True`` after running
    the input through the PII scrubber chain.
    """


class BedrockCircuitOpenError(RuntimeError):
    """Raised when the upstream circuit breaker is OPEN for the embed model."""


# Module-level result counters (metrics-friendly without prometheus dep).
# Keys: hit, miss, fail, circuit_open, truncated.
embedding_operations_total: Dict[str, int] = {
    "hit": 0,
    "miss": 0,
    "fail": 0,
    "circuit_open": 0,
    "truncated": 0,
}


def _bump(result: str) -> None:
    embedding_operations_total[result] = embedding_operations_total.get(result, 0) + 1


class BedrockEmbedder:
    """Async client for Titan v2 embeddings with per-org cache + breaker."""

    MODEL_ID: str = "amazon.titan-embed-text-v2:0"
    DIMENSIONS: int = 256
    MAX_INPUT_CHARS: int = 8192
    # Cap on how many distinct truncation-warning hashes we remember per process.
    _TRUNC_WARN_LRU_MAX: int = 256

    def __init__(
        self,
        *,
        bedrock_client: Any,
        circuit_breaker: "Optional[CircuitBreaker]" = None,
        ttl_seconds: float = 300.0,
        cache_max_entries: int = 1024,
        time_provider: Optional[Callable[[], float]] = None,
    ) -> None:
        self._client = bedrock_client
        self._breaker = circuit_breaker
        self._ttl = ttl_seconds
        self._cache_max = cache_max_entries
        self._time = time_provider or time.monotonic
        self._lock = asyncio.Lock()
        # OrderedDict[cache_key] -> (vector, inserted_at_monotonic)
        self._cache: "OrderedDict[str, tuple[List[float], float]]" = OrderedDict()
        # Dedup LRU for truncation warnings (key: text-hash)
        self._trunc_warned: "OrderedDict[str, None]" = OrderedDict()

    # ---------------------- private helpers --------------------- #
    @staticmethod
    def _cache_key(org_slug: str, text_for_key: str) -> str:
        h = hashlib.md5()
        h.update(org_slug.encode("utf-8"))
        h.update(b":")
        h.update(text_for_key.encode("utf-8"))
        return h.hexdigest()

    def _maybe_warn_truncate(self, original_text: str) -> None:
        h = hashlib.md5(original_text.encode("utf-8")).hexdigest()
        if h in self._trunc_warned:
            self._trunc_warned.move_to_end(h)
            return
        self._trunc_warned[h] = None
        if len(self._trunc_warned) > self._TRUNC_WARN_LRU_MAX:
            self._trunc_warned.popitem(last=False)
        LOG.warning(
            "BedrockEmbedder: input truncated to %d chars (orig=%d)",
            self.MAX_INPUT_CHARS,
            len(original_text),
        )

    def _cache_get(self, key: str) -> Optional[List[float]]:
        entry = self._cache.get(key)
        if entry is None:
            return None
        vec, inserted_at = entry
        if (self._time() - inserted_at) > self._ttl:
            self._cache.pop(key, None)
            return None
        self._cache.move_to_end(key)
        return vec

    def _cache_put(self, key: str, vec: List[float]) -> None:
        self._cache[key] = (vec, self._time())
        self._cache.move_to_end(key)
        while len(self._cache) > self._cache_max:
            self._cache.popitem(last=False)

    def _invoke_bedrock(self, text: str) -> List[float]:
        """Synchronous boto3 call. Wrapped via asyncio.to_thread by caller."""
        body = json.dumps(
            {"inputText": text, "dimensions": self.DIMENSIONS, "normalize": True}
        )
        resp = self._client.invoke_model(
            modelId=self.MODEL_ID,
            contentType="application/json",
            accept="application/json",
            body=body,
        )
        raw = resp["body"].read()
        payload = json.loads(raw)
        vec = payload.get("embedding")
        if not isinstance(vec, list) or len(vec) != self.DIMENSIONS:
            raise RuntimeError(
                "Bedrock returned unexpected embedding shape: "
                f"type={type(vec).__name__} "
                f"len={len(vec) if isinstance(vec, list) else 'N/A'}"
            )
        return vec

    # ---------------------- public API -------------------------- #
    async def embed(
        self,
        text: str,
        *,
        org_slug: str,
        assume_redacted: bool = False,
    ) -> List[float]:
        """Return 256-dim embedding for ``text`` scoped by ``org_slug``.

        Args:
            text: Already-PII-scrubbed user/document text.
            org_slug: Tenant identifier for per-org cache isolation.
            assume_redacted: Caller MUST set True after redaction. False
                raises ``PIIRedactionRequiredError`` (fail-closed).

        Raises:
            PIIRedactionRequiredError: ``assume_redacted`` is False.
            BedrockCircuitOpenError: upstream breaker is OPEN.
        """
        if not assume_redacted:
            raise PIIRedactionRequiredError(
                "BedrockEmbedder.embed requires assume_redacted=True. "
                "Run the PII scrubber chain on the input first."
            )

        # Truncate (and warn-once) BEFORE keying so cache key matches what we send.
        truncated = False
        text_for_key = text
        if len(text) > self.MAX_INPUT_CHARS:
            truncated = True
            self._maybe_warn_truncate(text)
            text_for_key = text[: self.MAX_INPUT_CHARS]

        key = self._cache_key(org_slug, text_for_key)

        # 1) Cache check under lock
        async with self._lock:
            cached = self._cache_get(key)
            if cached is not None:
                _bump("hit")
                return cached

        # 2) Circuit breaker gate
        if self._breaker is not None:
            status = await self._breaker.check(self.MODEL_ID)
            if status.should_block:
                _bump("circuit_open")
                raise BedrockCircuitOpenError(
                    f"Circuit OPEN for {self.MODEL_ID}"
                )

        # 3) Invoke Bedrock off the event loop
        try:
            vector = await asyncio.to_thread(self._invoke_bedrock, text_for_key)
        except BaseException as exc:
            _bump("fail")
            if self._breaker is not None:
                try:
                    await self._breaker.record_error(
                        self.MODEL_ID, type(exc).__name__
                    )
                except Exception:  # noqa: BLE001
                    LOG.exception("breaker.record_error failed; swallowing")
            raise

        # 4) Success bookkeeping
        if self._breaker is not None:
            try:
                await self._breaker.record_success(self.MODEL_ID)
            except Exception:  # noqa: BLE001
                LOG.exception("breaker.record_success failed; swallowing")

        async with self._lock:
            self._cache_put(key, vector)

        if truncated:
            _bump("truncated")
        else:
            _bump("miss")
        return vector
