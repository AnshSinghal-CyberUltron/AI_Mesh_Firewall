"""
BYOK-aware RAG collection helpers.

Resolves vector clients per org (VectorProviderSync) plus env VECTOR_CLIENTS,
and returns soft-empty responses when no external vector backend is configured.
"""
from __future__ import annotations

import logging
from typing import Any, Callable

LOG = logging.getLogger("gateway.rag_collections")


def coerce_org_id(value: Any) -> int | str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    if text.isdigit():
        return int(text)
    return text


def client_from_provider_config(cfg: dict[str, Any]) -> tuple[Any | None, str]:
    """Build a vector client from a VectorProviderSync config dict."""
    from vector_client import ChromaDBClient, MilvusClient, PineconeClient

    provider_type = (cfg.get("provider_type") or "").strip()
    if not provider_type or not cfg.get("is_active"):
        return None, ""

    # R12 (#7): SSRF guard on the org-supplied connection_url. A tenant admin
    # controls this value and the gateway dials it server-side — an internal
    # target (cloud metadata, gateway loopback, link-local) would be SSRF. Permits
    # private RFC1918 (legit self-hosted / docker vector DBs) but blocks the
    # dangerous ranges. No connection_url providers (pinecone) are unaffected.
    _conn_url = cfg.get("connection_url")
    if _conn_url and provider_type in ("chroma", "milvus", "custom"):
        try:
            from _url_guard import is_safe_vector_provider_url as _safe_vp
        except ImportError:
            from ._url_guard import is_safe_vector_provider_url as _safe_vp
        _ok, _why = _safe_vp(str(_conn_url))
        if not _ok:
            LOG.warning("Rejected org vector provider connection_url (SSRF guard): %s", _why)
            return None, ""

    try:
        if provider_type == "pinecone" and cfg.get("api_key"):
            return PineconeClient(
                api_key=cfg["api_key"],
                environment=cfg.get("environment", ""),
                embedding_model=cfg.get("embedding_model", "text-embedding-3-small"),
                embedding_api_key=cfg.get("embedding_api_key", ""),
                reranker_model=cfg.get("reranker_model", ""),
            ), "pinecone"
        # Chroma is now a per-org BYOK provider (the client connects their own
        # Chroma server via connection_url) — NOT a gateway built-in. api_key, if
        # set, is sent as the Chroma auth bearer token.
        if provider_type == "chroma" and cfg.get("connection_url"):
            return ChromaDBClient(
                url=cfg["connection_url"],
                auth_token=cfg.get("api_key", ""),
            ), "chroma"
        if provider_type in ("milvus", "custom") and cfg.get("connection_url"):
            url = str(cfg["connection_url"])
            if provider_type == "custom" and (url.startswith("http://") or url.startswith("https://")):
                return ChromaDBClient(
                    url=url,
                    auth_token=cfg.get("api_key", ""),
                ), "chroma"
            return MilvusClient(
                uri=url,
                token=cfg.get("api_key", ""),
            ), provider_type
    except Exception:
        LOG.warning(
            "Failed to create org-level %s client",
            provider_type,
            exc_info=True,
        )
    return None, ""


def iter_vector_clients_for_org(
    org_id: int | str | None,
    *,
    vector_clients: dict[str, Any],
    provider_sync: Any | None,
) -> list[tuple[str, Any]]:
    """Return [(provider_type, client), ...] for org BYOK plus env fallbacks."""
    seen: set[str] = set()
    clients: list[tuple[str, Any]] = []

    if org_id and provider_sync is not None:
        for cfg in provider_sync.get_org_providers(org_id):
            ptype = (cfg.get("provider_type") or "").strip()
            if not ptype or ptype in seen:
                continue
            client, used = client_from_provider_config(cfg)
            if client and used and used not in seen:
                clients.append((used, client))
                seen.add(used)

    for ptype, client in vector_clients.items():
        if ptype not in seen:
            clients.append((ptype, client))
            seen.add(ptype)

    return clients


def rag_vector_available(
    org_id: int | str | None,
    *,
    vector_clients: dict[str, Any],
    provider_sync: Any | None,
) -> bool:
    return bool(iter_vector_clients_for_org(org_id, vector_clients=vector_clients, provider_sync=provider_sync))


def no_provider_configured_payload(project_id: str) -> dict[str, Any]:
    return {
        "project_id": project_id,
        "collections": {},
        "rag_available": False,
        "reason": "no_provider_configured",
    }


def strip_namespace(name: str, prefix: str) -> str:
    if isinstance(name, str) and name.startswith(prefix):
        return name[len(prefix):]
    return name


async def list_collections_for_clients(
    project_id: str,
    clients: list[tuple[str, Any]],
) -> dict[str, list[str]]:
    prefix = f"{project_id}__"
    result: dict[str, list[str]] = {}
    for provider_name, client in clients:
        try:
            if hasattr(client, "list_collections"):
                cols = await client.list_collections(project_id=project_id)
                result[provider_name] = [
                    strip_namespace(n, prefix) for n in (cols or [])
                ]
            else:
                result[provider_name] = []
        except Exception as exc:
            LOG.warning("list_collections_for_clients: %s failed: %s", provider_name, exc)
            result[provider_name] = []
    return result


def resolve_vector_client_for_org(
    vector_db_type: str,
    org_id: int | str | None,
    *,
    vector_clients: dict[str, Any],
    provider_sync: Any | None,
    resolver: Callable[[str, int | str | None], tuple[Any | None, str | None]] | None = None,
) -> tuple[Any | None, str | None]:
    """Resolve a single client using org BYOK then env defaults."""
    if resolver is not None:
        client, used = resolver(vector_db_type, org_id)
        if client is not None:
            return client, used

    if org_id and provider_sync is not None:
        cfg = provider_sync.get_provider_config(org_id, vector_db_type)
        if cfg and cfg.get("is_active"):
            client, used = client_from_provider_config(cfg)
            if client:
                return client, used

    client = vector_clients.get(vector_db_type)
    if client:
        return client, vector_db_type

    if vector_clients:
        fallback_type = next(iter(vector_clients))
        return vector_clients[fallback_type], fallback_type

    return None, None
