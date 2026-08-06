"""
Vector DB client abstraction for the Gateway Data Plane.

Provides a unified async interface for querying different vector databases
(ChromaDB, Pinecone, Milvus). All synchronous operations are offloaded to a
ThreadPoolExecutor to avoid blocking the async event loop.

Namespace isolation is enforced at the client level: the actual collection
name is always constructed as ``{project_id}__{collection_name}`` to prevent
cross-tenant leakage.
"""

import asyncio
import logging
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Protocol, runtime_checkable

LOG = logging.getLogger("gateway.vector_client")

DEFAULT_THREAD_POOL_SIZE = 4

# Upsert-time embedding-validity band (E11 poisoning guard). A legitimate dense
# embedding has a finite L2 norm in a sane range — typical models emit either
# unit-normalized vectors (norm ~= 1.0) or unnormalized vectors with norms of a
# few tens. A vector whose magnitude lands far outside this band, or that is
# all-zero / near-zero / non-finite, is a poisoning or corruption signal, not a
# real document embedding. The band is deliberately WIDE (and absolute) so it
# fires only on unambiguous garbage — zero false positives on legitimate
# embeddings — unlike per-collection distance calibration (the F12 lesson).
_EMBEDDING_MIN_L2_NORM = 1e-3
_EMBEDDING_MAX_L2_NORM = 1e4
_INF = float("inf")


def _milvus_collection_name(namespaced: str) -> str:
    """Map a tenant namespace to a VALID Milvus collection identifier.

    RAG-13 (2026-08-03): Milvus identifiers allow only ``[0-9A-Za-z_]`` and must start
    with a letter/underscore, but the tenant namespace ``org{id}-{project}__{collection}``
    always contains hyphens (from the ``org{id}-`` prefix). ``Collection(name=…)`` therefore
    raised "Invalid collection name" for EVERY tenant, which a bare ``except`` swallowed into
    a silent empty-200 — masquerading a hard failure as "no results". Sanitize invalid chars
    to ``_`` and append a short deterministic hash of the ORIGINAL namespace so two distinct
    tenants can never collide onto one physical collection.
    """
    import hashlib
    import re

    safe = re.sub(r"[^0-9A-Za-z_]", "_", namespaced or "")
    if not safe or not (safe[0].isalpha() or safe[0] == "_"):
        safe = "t_" + safe
    digest = hashlib.sha1((namespaced or "").encode("utf-8")).hexdigest()[:10]
    return f"{safe[:230]}_{digest}"


def _tenant_namespace(project_id: str, collection_name: str) -> str:
    """Build the Pinecone tenant namespace, failing CLOSED on a missing tenant key.

    RAG-03 (2026-08-03): a falsy/``None`` ``project_id`` used to interpolate literally
    into ``None__{collection}`` — one shared namespace that every affected tenant read
    and wrote (silent cross-tenant collapse). Tenant isolation is not optional.
    """
    if not project_id or str(project_id).strip().lower() in ("", "none", "null"):
        raise ValueError(
            "refusing to build a vector namespace without a tenant project_id "
            "(tenant isolation cannot be disabled)"
        )
    return f"{project_id}__{collection_name}"


# ── Chroma distance-metric normalization (RAG-05b) ──
#
# The whole policy contract treats ``anomaly_distance_threshold`` as a COSINE
# distance in [0,1] (control-plane FloatField, default 0.85, MaxValue 1.0 — the
# serializer validators cap it, so an operator CANNOT compensate for a provider
# that emits a differently-scaled number). Pinecone honors that contract
# (``distance = 1.0 - score``). Chroma did NOT: collections created without an
# explicit ``metadata={"hnsw:space": ...}`` use Chroma's DEFAULT ``l2`` space,
# whose "distance" is SQUARED L2 — exactly 2x the cosine distance for
# unit-normalized vectors (measured on chromadb 1.5.9: query/doc pairs 60deg and
# 90deg apart return 1.0 / 2.0 where the cosine space returns 0.5 / 1.0).
#
# Passing that through verbatim is an AVAILABILITY break, not a miscalibration:
# every document in a perfectly legitimate result set lands above 0.85, so
# ranker_stage flags them all, empties the document list, sets
# ``anomaly_removed_all`` and resolves the verdict to "block". Because setting
# ``anomaly_distance_threshold`` is ALSO what force-enables the ranker, any
# Chroma-backed policy that configures a threshold turns normal retrieval into a
# 100% block. The same mis-scaling silently zeroes the retriever's relevance
# filter (``1.0 - distance`` clamps to 0.0) and degrades ``compute_trust_score``.
#
# So the scale is normalized at the CLIENT boundary: the ``distance`` a client
# emits is a cosine distance whenever we can PROVE it is one, and is explicitly
# marked ``_distance_metric: "unknown"`` when we cannot — never silently guessed.

# Chroma's default space when a collection is created without an explicit
# ``hnsw:space`` (chromadb ``segment/impl/vector/hnsw_params.py``:
# ``metadata.get("hnsw:space", "l2")``). Recorded for documentation only — it is
# deliberately never ASSUMED: guessing "l2" for a collection whose space we could
# not read would halve every distance and silently disable absolute-threshold
# anomaly detection if that collection were really cosine.
_CHROMA_DEFAULT_SPACE = "l2"

# A cosine distance (``1 - cosine_similarity``) is bounded by [0, 2]. The squared
# L2 distance between UNIT-NORMALIZED vectors is bounded by [0, 4] (it is exactly
# 2x the cosine distance). A result set that breaches its bound PROVES the corpus
# is not unit-normalized, so the conversion below does not apply to it.
_COSINE_DISTANCE_MAX = 2.0
_UNIT_NORM_MAX_L2_SQ = 4.0
_METRIC_BOUND_TOLERANCE = 1e-6


def _chroma_collection_space(collection: Any) -> str | None:
    """Return the space a Chroma collection is CONFIGURED with (``"l2"`` /
    ``"cosine"`` / ``"ip"``), or ``None`` when it cannot be determined.

    RAG-05b: pre-existing collections are the important half of the fix — they
    were created with no ``hnsw:space`` and are stuck on the default ``l2``, so
    the conversion has to be driven by what the server actually reports, not by
    what we now create. chromadb >= 1.x materializes the EFFECTIVE space on
    ``collection.configuration`` even when the caller never set one (verified on
    1.5.9: a default ``get_or_create_collection`` reports
    ``{"hnsw": {"space": "l2", ...}}`` while ``collection.metadata`` is ``None``),
    so prefer that, then the raw ``configuration_json``, then the legacy
    ``metadata["hnsw:space"]``. Every access is defensive: a client version that
    does not expose the space must degrade to "unknown", never to a guess.
    """
    for source in ("configuration", "configuration_json"):
        try:
            config = getattr(collection, source, None)
            if isinstance(config, dict):
                for index_kind in ("hnsw", "spann"):
                    sub = config.get(index_kind)
                    if isinstance(sub, dict):
                        space = sub.get("space")
                        if isinstance(space, str) and space.strip():
                            return space.strip().lower()
        except Exception:  # noqa: BLE001 - introspection must never break a query
            continue
    try:
        metadata = getattr(collection, "metadata", None)
        if isinstance(metadata, dict):
            space = metadata.get("hnsw:space")
            if isinstance(space, str) and space.strip():
                return space.strip().lower()
    except Exception:  # noqa: BLE001
        pass
    return None


def _within_bound(values: list[float], upper: float) -> bool:
    """True when every value sits inside ``[0, upper]`` (with float tolerance)."""
    return all(
        -_METRIC_BOUND_TOLERANCE <= v <= upper + _METRIC_BOUND_TOLERANCE
        for v in values
    )


def _chroma_cosine_distances(
    raw_distances: list[Any],
    space: str | None,
) -> tuple[list[Any], str]:
    """Convert Chroma's per-space distances to COSINE distance (RAG-05b).

    Returns ``(distances, metric)``, where ``metric`` is ``"cosine"`` when every
    emitted value is provably a cosine distance and ``"unknown"`` when the values
    are the provider's raw, unconverted numbers.

    Conversions (measured against chromadb 1.5.9, not inferred):
      * ``cosine`` — already ``1 - cosine_similarity``; passed through. Chroma
        normalizes internally for this space, so it needs no corpus assumption.
      * ``ip``     — hnswlib returns ``1 - inner_product``, which EQUALS the
        cosine distance for unit-normalized vectors; passed through. (It is NOT
        the bare inner product, so ``1 - value`` would INVERT the metric.)
      * ``l2``     — hnswlib returns SQUARED L2, which is ``2x`` the cosine
        distance for unit-normalized vectors; halved.

    UNIT-NORMALIZATION ASSUMPTION: the ``l2`` halving and the ``ip`` equivalence
    hold only for unit-normalized embeddings. That is not assumed — it is CHECKED
    against the returned values (squared L2 between unit vectors cannot exceed 4;
    a cosine distance cannot exceed 2). A set that breaches its bound proves the
    corpus is not unit-normalized, so the raw values are passed through and
    reported as "unknown" rather than converted into a wrong number.

    Fail-safe by construction: a conversion only ever makes a distance SMALLER,
    so this can never flag MORE documents than the unconverted behaviour did, and
    the "unknown" fallback is byte-identical to the previous behaviour — leaving
    the scale-invariant 2-sigma path in ``detect_embedding_anomaly`` as the
    meaningful signal for corpora we cannot calibrate.
    """
    try:
        values = [float(d) for d in raw_distances]
    except (TypeError, ValueError):
        # A non-numeric distance means the scale cannot be verified for the set;
        # keep the WHOLE set raw rather than emitting mixed units.
        return list(raw_distances), "unknown"

    if space == "cosine":
        return values, "cosine"

    if space == "ip" and _within_bound(values, _COSINE_DISTANCE_MAX):
        return values, "cosine"

    if space == "l2" and _within_bound(values, _UNIT_NORM_MAX_L2_SQ):
        # Clamped to the true cosine-distance range so an out-of-band value can
        # never become a nonsense distance. NOTE: the clamp is [0, 2], not [0, 1]
        # — a cosine distance legitimately reaches 2.0 for anti-correlated
        # vectors, and capping at 1.0 would both diverge from the native-cosine
        # path above and make a real outlier unflaggable at the contract's
        # maximum threshold of 1.0 (the comparison is a strict ``>``).
        return [min(max(v / 2.0, 0.0), _COSINE_DISTANCE_MAX) for v in values], "cosine"

    return list(raw_distances), "unknown"


def _embedding_validity_reason(vector: Any) -> str | None:
    """Return a rejection reason if ``vector`` is a degenerate/adversarial
    embedding that must NOT be written to the vector store, else ``None``.

    Rejects (clear poisoning / corruption signals, ~zero false positives on
    legitimate embeddings):
      * empty / non-sequence
      * any NaN or +/-inf component
      * any non-numeric component
      * all-zero or near-zero L2 norm (norm < ``_EMBEDDING_MIN_L2_NORM``)
      * abnormal L2 magnitude (non-finite, or outside the sane absolute band)

    Pure-Python L2 + ``isfinite``-style checks (no numpy/math dependency, to
    match ``byok_embedder``). The L2 sum is accumulated as we validate each
    component so a single pass both finds garbage components and the norm.
    """
    if vector is None:
        return "empty/non-sequence"
    try:
        n = len(vector)
    except TypeError:
        return "empty/non-sequence"
    if n == 0:
        return "empty"
    sq_sum = 0.0
    for x in vector:
        try:
            xf = float(x)
        except (TypeError, ValueError, OverflowError):
            return "non-numeric component"
        if xf != xf:
            return "NaN component"
        if xf == _INF or xf == -_INF:
            return "inf component"
        sq_sum += xf * xf
    # sq_sum can overflow to inf for very large (poisoned) magnitudes.
    if sq_sum != sq_sum or sq_sum == _INF:
        return "non-finite L2 norm"
    norm = sq_sum ** 0.5
    if norm < _EMBEDDING_MIN_L2_NORM:
        return f"near-zero L2 norm ({norm:.3g})"
    if norm > _EMBEDDING_MAX_L2_NORM:
        return f"abnormal L2 norm ({norm:.3g})"
    return None


def filter_valid_embedding_batch(
    documents: list[str],
    ids: list[str],
    embeddings: list[Any],
    metadatas: list[dict[str, Any]] | None,
    *,
    context: str = "",
) -> tuple[list[str], list[str], list[Any], list[dict[str, Any]] | None, int]:
    """Drop any embedding that fails the upsert-time validity guard, dropping
    its paired id / document / metadata IN LOCKSTEP so the remaining batch stays
    index-aligned. Returns the filtered ``(documents, ids, embeddings,
    metadatas, rejected_count)``.

    This is the vector-write poisoning/anomaly guard (E11 part b): rag_ingest
    embeds and upserts with no validity check, so a corrupt/adversarial vector
    (NaN/inf, all-zero, or wildly-scaled magnitude) would be written and later
    surface as a "real" nearest neighbour. We refuse to persist such vectors.
    Logs a count of rejected vectors; never raises (a single poisoned row must
    not fail the whole legitimate batch).
    """
    keep_docs: list[str] = []
    keep_ids: list[str] = []
    keep_emb: list[Any] = []
    keep_meta: list[dict[str, Any]] | None = [] if metadatas is not None else None
    rejected = 0
    for i, emb in enumerate(embeddings):
        reason = _embedding_validity_reason(emb)
        if reason is not None:
            rejected += 1
            _id = ids[i] if i < len(ids) else "?"
            LOG.warning(
                "E11 upsert guard: rejecting poisoned/degenerate embedding "
                "(id=%s, reason=%s, context=%s) — vector + its doc/metadata "
                "dropped, NOT written.",
                _id, reason, context or "upsert",
            )
            continue
        keep_emb.append(emb)
        if i < len(ids):
            keep_ids.append(ids[i])
        if i < len(documents):
            keep_docs.append(documents[i])
        if keep_meta is not None and metadatas is not None and i < len(metadatas):
            keep_meta.append(metadatas[i])
    return keep_docs, keep_ids, keep_emb, keep_meta, rejected


@runtime_checkable
class VectorDBClient(Protocol):
    """Protocol defining the vector DB client interface."""

    async def query(
        self,
        collection_name: str,
        query_text: str,
        n_results: int,
        where: dict[str, Any] | None,
        namespace: str,
        project_id: str,
    ) -> list[dict[str, Any]]:
        ...

    async def health_check(self) -> bool:
        ...


class ChromaDBClient:
    """
    Async ChromaDB HTTP client for the Gateway Data Plane.

    Uses chromadb.HttpClient (synchronous) wrapped in ThreadPoolExecutor
    to avoid blocking the event loop. Collection names are namespaced by
    project_id to enforce tenant isolation.
    """

    def __init__(
        self,
        url: str,
        auth_token: str = "",
        thread_pool_size: int = DEFAULT_THREAD_POOL_SIZE,
    ) -> None:
        self._url = url
        self._auth_token = auth_token
        self._executor = ThreadPoolExecutor(
            max_workers=thread_pool_size,
            thread_name_prefix="chroma",
        )
        self._client = None
        LOG.info("ChromaDBClient initialized (url=%s)", url)

    def _parse_url(self) -> tuple[str, int, bool]:
        """Parse ``connection_url`` ONCE into ``(host, port, use_tls)``.

        RAG-01 (2026-08-03): uses the SAME parser (urlsplit) the SSRF guard uses.
        The previous inline ``replace("http://","").split(":")[0]`` disagreed with
        ``urlparse().hostname`` on a userinfo URL, so a connection_url of
        ``http://169.254.169.254:8000@example.com`` cleared the guard (which validated
        ``example.com``) while this client connected to ``169.254.169.254`` — a
        live-proven SSRF to the cloud metadata endpoint that also leaked the org's
        provider token. Userinfo is additionally rejected by the guard (defense in
        depth) and here, so the two layers can never disagree again.

        RAG-09: the scheme decides TLS. ``chromadb.HttpClient`` defaults ``ssl=False``,
        so an ``https://`` endpoint was silently downgraded to cleartext, exposing the
        bearer token and every retrieved document on the wire.
        """
        from urllib.parse import urlsplit

        split = urlsplit(self._url if "://" in (self._url or "") else f"http://{self._url or ''}")
        if split.username is not None or split.password is not None:
            raise ValueError("Chroma connection_url must not contain userinfo ('@')")
        host = split.hostname
        if not host:
            raise ValueError(f"Chroma connection_url has no host: {self._url!r}")
        use_tls = (split.scheme or "http").lower() == "https"
        try:
            port = split.port or (443 if use_tls else 8000)
        except ValueError as exc:  # malformed port -> fail closed
            raise ValueError(f"Chroma connection_url has an invalid port: {self._url!r}") from exc
        return host, port, use_tls

    # Test seams (the parse is security-critical; tests assert client/guard agreement).
    def _url_parts_for_test(self) -> tuple[str, int, bool]:
        return self._parse_url()

    def _url_host_for_test(self) -> str:
        return self._parse_url()[0]

    def _get_client(self):
        """Lazy-init the ChromaDB HTTP client."""
        if self._client is None:
            try:
                import chromadb

                settings = chromadb.config.Settings(
                    chroma_api_impl="chromadb.api.fastapi.FastAPI",
                    anonymized_telemetry=False,
                )
                host, port, _is_tls = self._parse_url()

                kwargs: dict[str, Any] = {
                    "host": host,
                    "port": port,
                    # RAG-09: an https:// endpoint was silently downgraded to cleartext
                    # (chromadb defaults ssl=False), exposing the bearer token and every
                    # retrieved document on the wire. Honor the scheme.
                    "ssl": _is_tls,
                    "settings": settings,
                }
                if self._auth_token:
                    kwargs["headers"] = {"Authorization": f"Bearer {self._auth_token}"}

                self._client = chromadb.HttpClient(**kwargs)
                LOG.info("ChromaDB HTTP client connected to %s:%d", host, port)
            except Exception:
                LOG.exception("Failed to initialize ChromaDB client")
                raise
        return self._client

    def _build_collection_name(self, project_id: str, collection_name: str) -> str:
        """Construct the namespaced collection name for tenant isolation.

        RAG-03 (2026-08-03): fail CLOSED on a missing tenant key. A falsy/``None``
        ``project_id`` used to interpolate literally, producing the shared namespace
        ``None__{collection}`` that every affected tenant then read and wrote — a silent
        cross-tenant collapse. A namespace without a tenant key is never legitimate, so
        raise instead of building one. Callers surface this as a clean block.
        """
        if not project_id or str(project_id).strip().lower() in ("", "none", "null"):
            raise ValueError(
                "refusing to build a vector namespace without a tenant project_id "
                "(tenant isolation cannot be disabled)"
            )
        return f"{project_id}__{collection_name}"

    def _query_sync(
        self,
        collection_name: str,
        query_text: str,
        n_results: int,
        where: dict[str, Any] | None,
        project_id: str,
    ) -> list[dict[str, Any]]:
        """Synchronous query execution, run inside ThreadPoolExecutor."""
        client = self._get_client()
        namespaced = self._build_collection_name(project_id, collection_name)

        try:
            collection = client.get_collection(name=namespaced)
        except Exception:
            LOG.warning("Collection '%s' not found in ChromaDB", namespaced)
            return []

        query_params: dict[str, Any] = {
            "query_texts": [query_text],
            "n_results": n_results,
            "include": ["documents", "metadatas", "distances"],
        }
        if where:
            query_params["where"] = where

        try:
            results = collection.query(**query_params)
        except Exception:
            # RAG-P1 (2026-08-06): this used to ``return []``, which the pipeline
            # cannot distinguish from "the collection genuinely has no match" —
            # so a vector store that answered its control calls but ERRORED the
            # query (500 / 429 / rate-limit / partial outage) produced a clean
            # ``HTTP 200 documents:[]`` AND the retriever recorded circuit-breaker
            # SUCCESS, so the breaker never tripped. Measured: 500 and 429 both
            # returned empty-200 indefinitely while a healthy call still worked,
            # i.e. a degraded DB silently served "no documents" forever and the
            # application could not tell retrieval was broken.
            # Connection-level faults already fail closed; this makes the
            # QUERY-level failure behave the same. Raising lets retriever_stage
            # record the error, open the breaker, and return a block verdict.
            LOG.exception("ChromaDB query failed for collection '%s'", namespaced)
            raise

        documents: list[dict[str, Any]] = []
        ids = results.get("ids", [[]])[0]
        docs = results.get("documents", [[]])[0]
        metadatas = results.get("metadatas", [[]])[0]
        distances = results.get("distances", [[]])[0]

        # RAG-05b: Chroma's distance is whatever its collection SPACE emits —
        # squared L2 for the default ``l2`` space, i.e. 2x the cosine distance the
        # policy's ``anomaly_distance_threshold`` is defined in. Normalize to a
        # cosine distance here so downstream absolute-threshold math (ranker
        # anomaly detection, trust scoring, relevance filtering) is comparing like
        # with like; when the scale cannot be PROVEN the raw value is kept and
        # marked "unknown" rather than guessed at.
        space = _chroma_collection_space(collection)
        distances, distance_metric = _chroma_cosine_distances(distances, space)
        if distance_metric != "cosine":
            LOG.warning(
                "ChromaDB collection '%s': distance scale is not provably cosine "
                "(space=%s) — emitting raw distances marked _distance_metric=unknown. "
                "Absolute anomaly_distance_threshold comparisons are NOT calibrated "
                "for this collection; the scale-invariant 2-sigma path still applies.",
                namespaced, space or "undetermined",
            )

        for i, doc_id in enumerate(ids):
            doc: dict[str, Any] = {
                "id": doc_id,
                "content": docs[i] if i < len(docs) else "",
                "metadata": metadatas[i] if i < len(metadatas) else {},
                "distance": distances[i] if i < len(distances) else 0.0,
                # Firewall-internal marker (underscore keys are stripped before
                # client egress by main._strip_internal_doc_fields): tells a
                # downstream consumer whether ``distance`` is provably a cosine
                # distance or an uncalibrated provider-native number.
                "_distance_metric": distance_metric,
            }
            if distance_metric == "cosine":
                # Emit the similarity alongside the distance (parity with the
                # Pinecone path) so the retriever's relevance filter stops
                # deriving it. Clamped to [0,1]: the filter is defined on a
                # similarity, and this is never BELOW the value the filter would
                # have derived from ``distance``, so it cannot drop a document
                # that previously survived.
                doc["score"] = min(max(1.0 - float(doc["distance"]), 0.0), 1.0)
            documents.append(doc)

        return documents

    async def query(
        self,
        collection_name: str,
        query_text: str,
        n_results: int = 10,
        where: dict[str, Any] | None = None,
        namespace: str = "",
        project_id: str = "",
    ) -> list[dict[str, Any]]:
        """Async query execution via ThreadPoolExecutor."""
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            self._executor,
            self._query_sync,
            collection_name,
            query_text,
            n_results,
            where,
            project_id,
        )

    # ── Ingestion / deletion / collection management ──

    def _add_sync(
        self,
        collection_name: str,
        documents: list[str],
        ids: list[str],
        metadatas: list[dict[str, Any]] | None,
        project_id: str,
    ) -> int:
        """Synchronous add documents to a ChromaDB collection."""
        # NOTE (E11 upsert guard): Chroma embeds documents SERVER-SIDE inside
        # ``collection.add`` — the gateway never sees the resulting vectors here,
        # so the embedding-validity guard (filter_valid_embedding_batch) has no
        # vector to inspect at this choke point. The guard applies where the
        # gateway itself produces the vectors before writing (PineconeClient.
        # _upsert_sync). Text-level content scanning still gates Chroma ingest
        # upstream in the RAG pipeline.
        client = self._get_client()
        namespaced = self._build_collection_name(project_id, collection_name)
        # RAG-05b: pin the space to cosine so a NEW collection speaks the same
        # unit as the policy's ``anomaly_distance_threshold`` instead of Chroma's
        # default squared-L2. This only affects collections created here — on an
        # existing collection Chroma ignores the metadata and keeps its original
        # space (verified on 1.5.9: no raise, no mutation), which is why
        # ``_query_sync`` still converts on read.
        collection = client.get_or_create_collection(
            name=namespaced, metadata={"hnsw:space": "cosine"},
        )
        kwargs: dict[str, Any] = {"ids": ids, "documents": documents}
        if metadatas:
            kwargs["metadatas"] = metadatas
        collection.add(**kwargs)
        return len(ids)

    async def add(
        self,
        collection_name: str,
        documents: list[str],
        ids: list[str],
        metadatas: list[dict[str, Any]] | None = None,
        project_id: str = "",
    ) -> int:
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            self._executor, self._add_sync,
            collection_name, documents, ids, metadatas, project_id,
        )

    def _delete_sync(self, collection_name: str, ids: list[str], project_id: str) -> int:
        client = self._get_client()
        namespaced = self._build_collection_name(project_id, collection_name)
        try:
            collection = client.get_collection(name=namespaced)
        except Exception:
            return 0
        collection.delete(ids=ids)
        return len(ids)

    async def delete(self, collection_name: str, ids: list[str], project_id: str = "") -> int:
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            self._executor, self._delete_sync, collection_name, ids, project_id,
        )

    def _list_collections_sync(self, project_id: str = "") -> list[str]:
        client = self._get_client()
        collections = client.list_collections()
        names = [c.name if hasattr(c, "name") else str(c) for c in collections]
        if project_id:
            prefix = f"{project_id}__"
            names = [n for n in names if n.startswith(prefix)]
        return names

    async def list_collections(self, project_id: str = "") -> list[str]:
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            self._executor, self._list_collections_sync, project_id,
        )

    def _create_collection_sync(self, collection_name: str, project_id: str) -> str:
        client = self._get_client()
        namespaced = self._build_collection_name(project_id, collection_name)
        # RAG-05b: see _add_sync — new collections are created in cosine space so
        # their distances match the policy contract's unit.
        client.get_or_create_collection(
            name=namespaced, metadata={"hnsw:space": "cosine"},
        )
        return namespaced

    async def create_collection(self, collection_name: str, project_id: str = "") -> str:
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            self._executor, self._create_collection_sync, collection_name, project_id,
        )

    def _delete_collection_sync(self, collection_name: str, project_id: str) -> bool:
        client = self._get_client()
        namespaced = self._build_collection_name(project_id, collection_name)
        try:
            client.delete_collection(name=namespaced)
            return True
        except Exception:
            LOG.warning("Failed to delete ChromaDB collection '%s'", namespaced)
            return False

    async def delete_collection(self, collection_name: str, project_id: str = "") -> bool:
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            self._executor, self._delete_collection_sync, collection_name, project_id,
        )

    async def health_check(self) -> bool:
        """Check if ChromaDB is reachable."""
        try:
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(self._executor, self._get_client().heartbeat)
            return True
        except Exception:
            return False


class PineconeClient:
    """
    Async Pinecone client for the Gateway Data Plane.

    Uses pinecone-client SDK with namespace-based tenant isolation.
    The project_id maps to the Pinecone namespace, and collection_name
    maps to the Pinecone index name.
    """

    def __init__(
        self,
        api_key: str,
        environment: str = "",
        embedding_model: str = "text-embedding-3-small",
        thread_pool_size: int = DEFAULT_THREAD_POOL_SIZE,
        embedding_api_key: str = "",
        reranker_model: str = "",
        is_org_byok: bool = False,
        embedding_dimension: int | None = None,
    ) -> None:
        self._api_key = api_key
        self._environment = environment
        self._embedding_model = embedding_model
        # BYOK key for an EXTERNAL (litellm) embedding model — e.g. an
        # OpenRouter / OpenAI-compatible key from the org's VectorProviderConfig.
        # Empty -> embed_texts uses gateway-environment credentials (legacy).
        self._embedding_api_key = embedding_api_key or ""
        # Per-org Pinecone-hosted reranker (e.g. 'bge-reranker-v2-m3'). Empty
        # -> no reranking (the retriever skips rerank()).
        self._reranker_model = reranker_model or ""
        # True when this client authenticates with the ORG'S OWN Pinecone key
        # (per-org BYOK), so every index on the account belongs to this org and
        # list_collections must return them ALL. False for the SHARED env-default
        # client, where one account holds many tenants' indexes and listing must
        # stay namespace-scoped to avoid leaking other tenants' collection names.
        self._is_org_byok = bool(is_org_byok)
        # RAG-32: the collection policy's ``embedding_dimension``, enforced
        # FAIL-CLOSED against the vectors actually produced. The only prior check
        # compared the policy value to a CLIENT-SUPPLIED body field, so simply
        # omitting ``embedding_dimension`` from the request skipped it entirely
        # and a 1024-dim index happily served a 1536-pinned collection — the
        # firewall never saw the mismatch, the provider SDK did. ``embed_texts``
        # already implements the check (byok_embedder.expected_dim); it was just
        # never given the value.
        self._embedding_dimension = (
            int(embedding_dimension) if embedding_dimension else None
        )
        self._executor = ThreadPoolExecutor(
            max_workers=thread_pool_size,
            thread_name_prefix="pinecone",
        )
        self._pc = None
        LOG.info(
            "PineconeClient initialized (embedding_model=%s, reranker_model=%s)",
            embedding_model, reranker_model or "(none)",
        )

    async def rerank(
        self,
        query_text: str,
        documents: list[dict[str, Any]],
        top_n: int | None = None,
    ) -> list[dict[str, Any]]:
        """Rerank retrieved documents via Pinecone's hosted rerank Inference API.

        Reorders ``documents`` by semantic relevance to ``query_text`` using the
        org's configured ``reranker_model``. No-op (returns input unchanged) when
        no reranker is configured or on any provider error (fail-open on rerank —
        the un-reranked order is still valid retrieval).
        """
        if not self._reranker_model or not documents:
            return documents
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            self._executor, self._rerank_sync, query_text, documents, top_n
        )

    def _rerank_sync(
        self,
        query_text: str,
        documents: list[dict[str, Any]],
        top_n: int | None,
    ) -> list[dict[str, Any]]:
        try:
            pc = self._get_client()
            texts = [str(d.get("content", "") or "") for d in documents]
            resp = pc.inference.rerank(
                model=self._reranker_model,
                query=query_text,
                documents=texts,
                top_n=top_n or len(documents),
                return_documents=False,
            )
            reordered: list[dict[str, Any]] = []
            for row in resp.data:
                idx = getattr(row, "index", None)
                if idx is None and hasattr(row, "get"):
                    idx = row.get("index")
                if idx is None or not (0 <= int(idx) < len(documents)):
                    continue
                doc = dict(documents[int(idx)])
                score = getattr(row, "score", None)
                if score is None and hasattr(row, "get"):
                    score = row.get("score")
                if score is not None:
                    doc["rerank_score"] = float(score)
                reordered.append(doc)
            return reordered or documents
        except Exception:
            LOG.warning(
                "Pinecone rerank failed (model=%s); keeping retrieval order (fail-open)",
                self._reranker_model, exc_info=True,
            )
            return documents

    # Pinecone-hosted embedding models served via the Inference API. When the
    # configured embedding_model is one of these, queries/passages are embedded
    # by Pinecone itself using the same API key — no OpenAI/Bedrock key needed.
    PINECONE_INFERENCE_MODELS = {
        "multilingual-e5-large",
        "llama-text-embed-v2",
        "pinecone-sparse-english-v0",
    }

    def _get_client(self):
        """Lazy-init the Pinecone client."""
        if self._pc is None:
            try:
                from pinecone import Pinecone

                self._pc = Pinecone(api_key=self._api_key)
                LOG.info("Pinecone client connected")
            except Exception:
                LOG.exception("Failed to initialize Pinecone client")
                raise
        return self._pc

    def _embed(self, texts: list[str], input_type: str) -> list[list[float]]:
        """Embed ``texts`` for the configured model.

        Pinecone-hosted models go through the Pinecone Inference API (the
        provider API key grants access — no external embedding credential), with
        ``input_type`` 'query' or 'passage' for asymmetric models like e5.
        Any other model falls back to LiteLLM (OpenAI/Bedrock/etc.).
        """
        from byok_embedder import embed_texts

        model = self._embedding_model
        is_pinecone_hosted = (
            model in self.PINECONE_INFERENCE_MODELS or model.startswith("pinecone-")
        )
        # Pinecone-hosted models are BYOK via the org's Pinecone key (used by the
        # pinecone_client). External models route through litellm using the org's
        # per-model BYOK ``embedding_api_key`` (e.g. OpenRouter) when configured,
        # falling back to gateway-environment credentials when empty. embed_texts
        # is FAIL-CLOSED: it raises on any zero/empty/wrong-shaped vector instead
        # of letting a meaningless embedding silently poison retrieval.
        return embed_texts(
            list(texts),
            embedding_model=model,
            input_type=input_type,
            pinecone_client=self._get_client() if is_pinecone_hosted else None,
            provider_api_key=None if is_pinecone_hosted else (self._embedding_api_key or None),
            # RAG-32: enforce the operator's pinned dimension on the REAL vectors.
            expected_dim=self._embedding_dimension,
        )

    def _query_sync(
        self,
        collection_name: str,
        query_text: str,
        n_results: int,
        where: dict[str, Any] | None,
        project_id: str,
    ) -> list[dict[str, Any]]:
        """Synchronous Pinecone query execution."""
        pc = self._get_client()
        namespace = _tenant_namespace(project_id, collection_name)

        try:
            index = pc.Index(collection_name)
        except Exception:
            LOG.warning("Pinecone index '%s' not accessible", collection_name)
            return []

        query_params: dict[str, Any] = {
            "namespace": namespace,
            "top_k": n_results,
            "include_metadata": True,
        }

        if where:
            query_params["filter"] = where

        # Fail-CLOSED embedding: if the query cannot be embedded we must NOT
        # fall back to a zero vector — that returns arbitrary nearest neighbours
        # as if they were real matches (silently-wrong RAG). Embed OUTSIDE the
        # result-swallowing try so the error propagates to the caller as an
        # explicit failure instead of garbage (or silently-empty) results.
        # 'query' input_type for asymmetric models like e5.
        query_vector = self._embed([query_text], input_type="query")[0]

        try:
            from pinecone import QueryResponse
            response: QueryResponse = index.query(
                vector=query_vector,
                **query_params,
            )
        except Exception:
            LOG.exception("Pinecone query failed for index '%s' namespace '%s'", collection_name, namespace)
            return []

        documents: list[dict[str, Any]] = []
        for match in response.get("matches", []):
            metadata = match.get("metadata", {})
            # NOTE: `metadata.pop("content", metadata.pop("text", ""))` would
            # evaluate the inner pop unconditionally (Python evaluates all args
            # before the call), deleting the 'text' field from the returned
            # metadata even when 'content' is present. Pop sequentially instead.
            _content = metadata.pop("content", None)
            if _content is None:
                _content = metadata.pop("text", "")
            _raw_score = match.get("score", 0.0)
            documents.append({
                "id": match.get("id", ""),
                "content": _content,
                "metadata": metadata,
                "distance": 1.0 - _raw_score,
                # Cosine similarity (higher = more relevant). Emitted so the
                # retriever's relevance-threshold filter has the key it reads
                # (it defaulted to 1.0 — a silent no-op — when only `distance`
                # was present, so degenerate matches were never filtered). (H6)
                "score": _raw_score,
                # RAG-05b metric marker (underscore keys are stripped before
                # client egress). Pinecone already emits a true cosine distance
                # via ``1 - score``, which is the unit the policy's
                # ``anomaly_distance_threshold`` is defined in — declare it so a
                # downstream consumer never has to guess the scale.
                "_distance_metric": "cosine",
            })

        return documents

    async def query(
        self,
        collection_name: str,
        query_text: str,
        n_results: int = 10,
        where: dict[str, Any] | None = None,
        namespace: str = "",
        project_id: str = "",
    ) -> list[dict[str, Any]]:
        """Async query via ThreadPoolExecutor."""
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            self._executor,
            self._query_sync,
            collection_name,
            query_text,
            n_results,
            where,
            project_id,
        )

    async def health_check(self) -> bool:
        """Check if Pinecone is reachable."""
        try:
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(self._executor, self._get_client().list_indexes)
            return True
        except Exception:
            return False

    # ── Ingestion / deletion / index management ──

    def _upsert_sync(
        self,
        collection_name: str,
        documents: list[str],
        ids: list[str],
        metadatas: list[dict[str, Any]] | None,
        project_id: str,
    ) -> int:
        pc = self._get_client()
        namespace = _tenant_namespace(project_id, collection_name)
        try:
            index = pc.Index(collection_name)
        except Exception:
            LOG.warning("Pinecone index '%s' not accessible for upsert", collection_name)
            raise

        # 'passage' input_type for documents (asymmetric models like e5 embed
        # queries and passages differently).
        embeddings = self._embed(documents, input_type="passage")

        # E11 (b) upsert-time poisoning/anomaly guard: refuse to WRITE any
        # degenerate/adversarial embedding (NaN/inf, all-zero/near-zero norm, or
        # an abnormal L2 magnitude). Drop the vector AND its paired id/doc/
        # metadata in lockstep so the persisted batch stays index-aligned.
        documents, ids, embeddings, metadatas, rejected = filter_valid_embedding_batch(
            documents, ids, embeddings, metadatas,
            context=f"pinecone:{namespace}",
        )
        if rejected:
            LOG.warning(
                "E11 upsert guard: dropped %d poisoned embedding(s) before "
                "Pinecone upsert (namespace=%s); %d valid vector(s) remain.",
                rejected, namespace, len(embeddings),
            )

        vectors = []
        for i, doc_id in enumerate(ids):
            emb = embeddings[i]
            meta = dict(metadatas[i]) if metadatas and i < len(metadatas) else {}
            meta["content"] = documents[i]
            vectors.append({"id": doc_id, "values": emb, "metadata": meta})
        if not vectors:
            # Entire batch was poisoned/degenerate — nothing legitimate to write.
            return 0
        index.upsert(vectors=vectors, namespace=namespace)
        return len(vectors)

    async def upsert(
        self,
        collection_name: str,
        documents: list[str],
        ids: list[str],
        metadatas: list[dict[str, Any]] | None = None,
        project_id: str = "",
    ) -> int:
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            self._executor, self._upsert_sync,
            collection_name, documents, ids, metadatas, project_id,
        )

    # Alias for unified interface
    async def add(
        self,
        collection_name: str,
        documents: list[str],
        ids: list[str],
        metadatas: list[dict[str, Any]] | None = None,
        project_id: str = "",
    ) -> int:
        return await self.upsert(collection_name, documents, ids, metadatas, project_id)

    def _delete_sync(self, collection_name: str, ids: list[str], project_id: str) -> int:
        pc = self._get_client()
        namespace = _tenant_namespace(project_id, collection_name)
        try:
            index = pc.Index(collection_name)
            index.delete(ids=ids, namespace=namespace)
            return len(ids)
        except Exception:
            LOG.warning("Pinecone delete failed for index '%s'", collection_name)
            return 0

    async def delete(self, collection_name: str, ids: list[str], project_id: str = "") -> int:
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            self._executor, self._delete_sync, collection_name, ids, project_id,
        )

    def _list_indexes_sync(self, project_id: str = "") -> list[str]:
        pc = self._get_client()
        indexes = pc.list_indexes()
        if hasattr(indexes, "indexes") and indexes.indexes:
            raw = [idx.name if hasattr(idx, "name") else str(idx) for idx in indexes.indexes]
        elif hasattr(indexes, "__iter__"):
            raw = [idx.name if hasattr(idx, "name") else str(idx) for idx in indexes]
        else:
            return []
        # Tenant isolation is namespace-based (namespace = f"{project_id}__{name}").
        # On a SHARED account (env-default key) returning ALL index names leaks the
        # infra/other-tenant collection set, so keep only indexes where THIS project
        # has a namespace; fail-closed (skip on stats error).
        #
        # A per-org BYOK client authenticates with the ORG'S OWN key — every index
        # on that account belongs to this org, so namespace-filtering would wrongly
        # hide the org's own collections (the "Existing Collections (0)" bug when a
        # freshly-connected org, or a key that has not yet ingested under this exact
        # project_id, lists its indexes). Return them all for BYOK.
        if not project_id or self._is_org_byok:
            return raw
        prefix = f"{project_id}__"
        scoped: list[str] = []
        for name in raw:
            try:
                stats = pc.Index(name).describe_index_stats()
                ns = stats.get("namespaces") if isinstance(stats, dict) else getattr(stats, "namespaces", None)
                if ns and any(str(k).startswith(prefix) for k in ns):
                    scoped.append(name)
            except Exception:
                continue
        return scoped

    async def list_collections(self, project_id: str = "") -> list[str]:
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(self._executor, self._list_indexes_sync, project_id)


class MilvusClient:
    """
    Async Milvus client for the Gateway Data Plane.

    Uses pymilvus SDK with partition-based tenant isolation.
    The project_id maps to the partition name within the collection.
    """

    def __init__(
        self,
        uri: str,
        token: str = "",
        thread_pool_size: int = DEFAULT_THREAD_POOL_SIZE,
    ) -> None:
        self._uri = uri
        self._token = token
        self._executor = ThreadPoolExecutor(
            max_workers=thread_pool_size,
            thread_name_prefix="milvus",
        )
        self._connections_initialized: set[str] = set()
        LOG.info("MilvusClient initialized (uri=%s)", uri)

    def _ensure_connection(self, alias: str = "default") -> None:
        """Lazy-init the Milvus connection."""
        if alias not in self._connections_initialized:
            try:
                from pymilvus import connections

                connect_params: dict[str, Any] = {"alias": alias, "uri": self._uri}
                if self._token:
                    connect_params["token"] = self._token
                connections.connect(**connect_params)
                self._connections_initialized.add(alias)
                LOG.info("Milvus connection established (alias=%s)", alias)
            except Exception:
                LOG.exception("Failed to connect to Milvus")
                raise

    def _query_sync(
        self,
        collection_name: str,
        query_text: str,
        n_results: int,
        where: dict[str, Any] | None,
        project_id: str,
    ) -> list[dict[str, Any]]:
        """Synchronous Milvus search execution.

        FAIL-CLOSED by contract: the Milvus client does not embed the query text (no
        embedding model is wired here), so a real vector search is impossible; searching
        with a placeholder vector would return arbitrary nearest-neighbours as if they
        were real matches — silently-wrong RAG. So this RAISES (the retriever turns that
        into a clean block) instead of returning results.

        RAG-13 (2026-08-03): the previous implementation checked ``Collection(name=…)``
        first inside a bare ``except Exception: return []``. Because the tenant namespace
        contains hyphens (invalid in a Milvus identifier), that check raised for EVERY
        tenant and was swallowed into a silent empty-200 — a hard failure disguised as
        "no results found" (a red-team-confirmed silent-wrong bypass), so the intended
        fail-closed raise below was never reached. We no longer probe the collection (the
        query fail-closes regardless), and the name is now sanitized so it is at least a
        valid Milvus identifier for when server-side embedding is wired.
        """
        self._ensure_connection()
        # RAG-03: guarded builder — fail CLOSED on a missing tenant key (parity with the
        # Chroma/Pinecone paths; never query the shared None__ namespace).
        namespaced = _milvus_collection_name(_tenant_namespace(project_id, collection_name))
        try:
            from byok_embedder import EmbeddingConfigError
        except ImportError:  # pragma: no cover - top-level import path
            from .byok_embedder import EmbeddingConfigError  # type: ignore[no-redef]
        raise EmbeddingConfigError(
            f"Milvus query embedding is not implemented for collection "
            f"'{namespaced}'; refusing to search with a placeholder vector "
            f"(fail-closed). Configure a provider with a wired embedding model."
        )

    async def query(
        self,
        collection_name: str,
        query_text: str,
        n_results: int = 10,
        where: dict[str, Any] | None = None,
        namespace: str = "",
        project_id: str = "",
    ) -> list[dict[str, Any]]:
        """Async query via ThreadPoolExecutor."""
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            self._executor,
            self._query_sync,
            collection_name,
            query_text,
            n_results,
            where,
            project_id,
        )

    async def health_check(self) -> bool:
        """Check if Milvus is reachable."""
        try:
            self._ensure_connection()
            return True
        except Exception:
            return False


def get_vector_client(vector_db_type: str, clients: dict[str, Any]) -> VectorDBClient | None:
    """Select the vector DB client for an operation based on the policy's vector_db_type."""
    return clients.get(vector_db_type)
