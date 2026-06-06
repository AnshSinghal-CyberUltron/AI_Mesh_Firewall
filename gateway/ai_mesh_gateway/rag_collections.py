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
    from vector_client import MilvusClient, PineconeClient

    provider_type = (cfg.get("provider_type") or "").strip()
    if not provider_type or not cfg.get("is_active"):
        return None, ""

    try:
        if provider_type == "pinecone" and cfg.get("api_key"):
            return PineconeClient(
                api_key=cfg["api_key"],
                environment=cfg.get("environment", ""),
                embedding_model=cfg.get("embedding_model", "text-embedding-3-small"),
            ), "pinecone"
        if provider_type in ("milvus", "custom") and cfg.get("connection_url"):
            return MilvusClient(
                uri=cfg["connection_url"],
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
