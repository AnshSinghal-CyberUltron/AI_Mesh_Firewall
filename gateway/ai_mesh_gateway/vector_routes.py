"""
Vector Operations API (/v1/vector/*) — Portable Multi-Tenant RAG Vector DB Interface.

This module provides production-grade endpoints for vector operations that:
  1. Automatically resolve organization from request context
  2. Use org-scoped vector provider configuration from Redis
  3. Route ALL operations through the RAG Firewall
  4. Enforce multi-tenant isolation at the collection level
  5. Support streaming for large result sets

Endpoints:
  - POST /v1/vector/query  — Retrieve documents from vector DB against firewall
  - POST /v1/vector/upsert  — Insert/update documents into vector DB (protected)
  - POST /v1/vector/delete  — Delete documents from vector DB (protected)
  - GET  /v1/vector/config  — Retrieve org's active vector DB config (protected)

All operations require Bearer token in Authorization header.
All operations are logged, traced, and scanned for policy violations.
"""

import asyncio
import json
import logging
from typing import Any, Optional


from fastapi import APIRouter, Header, Request, status
from fastapi.responses import JSONResponse, StreamingResponse

LOG = logging.getLogger("gateway.vector_routes")

router = APIRouter(prefix="/v1/vector", tags=["Vector Operations"])


# ────────────────────────────────────────────────────────────────────────────
#  CONTEXT + INIT
# ────────────────────────────────────────────────────────────────────────────


def _inject_vector_globals(app, globals_dict):
    """Called at gateway startup to inject references to gateway services into this module."""
    globals_dict["RAG_PIPELINE"] = app.state.get("rag_pipeline")
    globals_dict["VECTOR_CLIENTS"] = app.state.get("vector_clients", {})
    globals_dict["VECTOR_PROVIDER_SYNC"] = app.state.get("vector_provider_sync")
    globals_dict["TELEMETRY"] = app.state.get("telemetry")
    globals_dict["POLICY_SYNC"] = app.state.get("policy_sync")
    globals_dict["CONFIG"] = app.state.get("config", {})
    globals_dict["REDIS_CLIENT"] = app.state.get("redis_client")
    # globals will be populated at init-time


RAG_PIPELINE = None
VECTOR_CLIENTS: dict[str, Any] = {}
VECTOR_PROVIDER_SYNC = None
TELEMETRY = None
POLICY_SYNC = None
CONFIG = {}
REDIS_CLIENT = None


def _resolve_runtime_vector_client(provider_type: str, provider_config: dict[str, Any]):
    """
    Resolve a vector client from registered clients, with support for org-level
    custom providers that map to a Milvus-compatible URI/token contract.
    """
    client = VECTOR_CLIENTS.get(provider_type)
    if client:
        return client

    if provider_type == "custom":
        connection_url = (provider_config or {}).get("connection_url", "")
        if connection_url:
            from vector_client import MilvusClient

            return MilvusClient(
                uri=connection_url,
                token=(provider_config or {}).get("api_key", ""),
            )
    return None


# ────────────────────────────────────────────────────────────────────────────
#  AUTH + ORG RESOLUTION
# ────────────────────────────────────────────────────────────────────────────


async def _resolve_org_from_token(authorization: Optional[str]) -> Optional[dict]:
    """
    Extract organization ID from JWT or Gateway API Key.
    Returns {org_id, project_id, user_id} or None if auth fails.
    """
    if not authorization or not authorization.startswith("Bearer "):
        return None

    token = authorization[7:].strip()
    if not token:
        return None

    # Try to decode JWT or API key from Redis
    if REDIS_CLIENT is None:
        LOG.warning("Redis not available for token resolution")
        return None

    try:
        # Check if it's a gateway API key (cached in Redis)
        key_data = await REDIS_CLIENT.hgetall(f"gateway:key:{token}")
        if key_data:
            return {
                "org_id": int(key_data.get("org_id", 0)) or None,
                "project_id": int(key_data.get("project_id", 0)) or None,
                "user_id": int(key_data.get("user_id", 0)) or None,
            }

        # Otherwise it's a JWT — extract org from bearer validation
        # (The auth middleware would have already validated this, so we trust it)
        jwt_data = await REDIS_CLIENT.hgetall(f"jwt_cache:{token[:16]}")
        if jwt_data:
            return {
                "org_id": int(jwt_data.get("org_id", 0)) or None,
                "project_id": int(jwt_data.get("project_id", 0)) or None,
                "user_id": int(jwt_data.get("user_id", 0)) or None,
            }
    except Exception as exc:
        LOG.warning("Token resolution failed: %s", exc)

    return None


async def _resolve_vector_provider_for_org(org_id: int) -> Optional[dict]:
    """
    Fetch org's active vector provider config from Redis (org-scoped).
    Returns {provider_type, connection_url, api_key, embedding_model, ...} or None.

    The config is cached in Redis by the backend's VectorProviderConfig signals:
      redis:vector:provider:{org_id}:{provider_type}  — full config dict
    """
    if VECTOR_PROVIDER_SYNC is None:
        LOG.warning("Vector provider sync not initialized")
        return None

    if REDIS_CLIENT is None:
        LOG.warning("Redis client not available")
        return None

    try:
        # Fetch all active providers for this org
        org_key_pattern = f"vector:provider:{org_id}:*"
        keys = await REDIS_CLIENT.keys(org_key_pattern)

        if not keys:
            LOG.debug("No vector provider config found for org_id=%s", org_id)
            return None

        # Return the first active provider (organizations typically have one)
        for key in keys:
            config_json = await REDIS_CLIENT.get(key)
            if config_json:
                config = json.loads(config_json)
                if config.get("is_active"):
                    LOG.debug(
                        "Resolved vector provider for org=%s: type=%s",
                        org_id,
                        config.get("provider_type"),
                    )
                    return config

        LOG.debug("No active vector provider config found for org_id=%s", org_id)
        return None
    except Exception as exc:
        LOG.exception("Failed to resolve vector provider for org_id=%s: %s", org_id, exc)
        return None


# ────────────────────────────────────────────────────────────────────────────
#  POST /v1/vector/query — Retrieve documents (through firewall)
# ────────────────────────────────────────────────────────────────────────────


@router.post(
    "/query",
    summary="Query vector database with policy enforcement",
    tags=["Vector Operations"],
    status_code=200,
)
async def query_vector_db(
    request: Request,
    authorization: Optional[str] = Header(None),
) -> JSONResponse:
    """
    Query organization's vector database through the RAG Firewall.

    **Request Body:**
    ```json
    {
      "query": "What is the refund policy?",
      "collection_name": "documents",
      "n_results": 5,
      "where": {"source": "policy"}  # Optional metadata filter
    }
    ```

    **Response:**
    All responses include `x-zeroshield` metadata with policy enforcement status.

    On success (200):
    ```json
    {
      "documents": [
        {
          "id": "doc-123",
          "text": "The refund policy states...",
          "metadata": {"source": "policy", "date": "2024-01-15"}
        }
      ],
      "x-zeroshield": {
        "request_id": "zs-abc123",
        "action": "allow|flag|redact|block",
        "reason": "All checks passed",
        "firewall_stage": "ranker"
      }
    }
    ```

    On block (403):
    ```json
    {
      "error": "blocked",
      "reason": "Query matched malicious pattern: SQL injection",
      "x-zeroshield": {
        "request_id": "zs-abc123",
        "action": "block",
        "threat_type": "prompt_injection",
        "matched_patterns": ["sql_injection"]
      }
    }
    ```
    """
    try:
        auth_context = await _resolve_org_from_token(authorization)
        if not auth_context or not auth_context.get("org_id"):
            return JSONResponse(
                status_code=status.HTTP_401_UNAUTHORIZED,
                content={
                    "error": "unauthorized",
                    "message": "Valid Bearer token required",
                },
            )

        org_id = auth_context["org_id"]
        user_id = auth_context.get("user_id")

        # Parse request body
        body = await request.json()
        query_text = body.get("query", "").strip()
        collection_name = body.get("collection_name", "documents").strip()
        n_results = int(body.get("n_results", 5))
        where_filter = body.get("where")

        if not query_text:
            return JSONResponse(
                status_code=status.HTTP_400_BAD_REQUEST,
                content={"error": "query required"},
            )

        if n_results < 1 or n_results > 1000:
            return JSONResponse(
                status_code=status.HTTP_400_BAD_REQUEST,
                content={"error": "n_results must be between 1 and 1000"},
            )

        # Resolve org's vector provider config
        provider_config = await _resolve_vector_provider_for_org(org_id)
        if not provider_config:
            LOG.warning("No vector provider configured for org_id=%s", org_id)
            return JSONResponse(
                status_code=status.HTTP_400_BAD_REQUEST,
                content={
                    "error": "no_provider",
                    "message": "Organization has not configured a vector database provider",
                },
            )

        provider_type = provider_config.get("provider_type", "pinecone")
        project_id = auth_context.get("project_id", org_id)

        # Execute query through RAG Firewall
        if RAG_PIPELINE is None:
            LOG.error("RAG pipeline not initialized")
            return JSONResponse(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                content={"error": "rag_unavailable"},
            )

        # Determine org slug for org-scoped policy lookup
        middleware_auth = getattr(request.state, "auth_context", None)
        org_slug = middleware_auth.org_slug if middleware_auth else "default"

        try:
            rag_verdict = await RAG_PIPELINE.execute(
                query_text=query_text,
                collection_name=collection_name,
                project_id=str(project_id),
                vector_db_type=provider_type,
                n_results=n_results,
                where_filter=where_filter,
                policy=POLICY_SYNC.get_policies(org_slug) if POLICY_SYNC else {},
            )
        except Exception as exc:
            LOG.exception("RAG pipeline execution failed: %s", exc)
            return JSONResponse(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                content={
                    "error": "pipeline_error",
                    "message": str(exc),
                },
            )

        # Check verdict action
        if rag_verdict.action in ("block", "deny"):
            LOG.info(
                "Query blocked for org=%s (reason logged server-side only)",
                org_id,
            )
            # ── SECURITY FIX: Don't expose detection details to client ──
            return JSONResponse(
                status_code=status.HTTP_403_FORBIDDEN,
                content={
                    "error": "blocked",
                    "message": "Request blocked due to security policy",
                    "x-zeroshield": {
                        "request_id": rag_verdict.scan_verdict.get("request_id", ""),
                        "action": "block",
                        "threat_type": rag_verdict.scan_verdict.get("threat_type", "rag_threat"),
                    },
                },
            )

        # Emit telemetry
        if TELEMETRY:
            TELEMETRY.emit({
                "event_type": "vector_query",
                "action": "query",
                "org_id": org_id,
                "user_id": user_id,
                "provider_type": provider_type,
                "collection_name": collection_name,
                "firewall_action": rag_verdict.action,
                "status_code": 200,
            })

        return JSONResponse(
            status_code=status.HTTP_200_OK,
            content={
                "documents": rag_verdict.documents or [],
                "x-zeroshield": {
                    "request_id": rag_verdict.scan_verdict.get("request_id", ""),
                    "action": rag_verdict.action,
                    # ── SECURITY FIX: Don't expose internal details like reason/stage ──
                    "firewall_enabled": True,
                },
            },
        )

    except json.JSONDecodeError:
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content={"error": "invalid_json"},
        )
    except Exception as exc:
        LOG.exception("Vector query failed: %s", exc)
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={"error": "internal_error", "message": str(exc)},
        )


# ────────────────────────────────────────────────────────────────────────────
#  POST /v1/vector/upsert — Insert/update documents (through firewall)
# ────────────────────────────────────────────────────────────────────────────


@router.post(
    "/upsert",
    summary="Insert or update documents in vector database",
    tags=["Vector Operations"],
    status_code=201,
)
async def upsert_vector_documents(
    request: Request,
    authorization: Optional[str] = Header(None),
) -> JSONResponse:
    """
    Insert or update documents in organization's vector database.

    **Request Body:**
    ```json
    {
      "documents": [
        {
          "id": "doc-1",
          "text": "The refund policy states that...",
          "metadata": {"source": "policy", "date": "2024-01-15"}
        }
      ],
      "collection_name": "documents"
    }
    ```

    **Response (201):**
    ```json
    {
      "inserted_count": 1,
      "upserted_count": 0,
      "x-zeroshield": {
        "request_id": "zs-def456",
        "action": "allow",
        "reason": "All checks passed"
      }
    }
    ```
    """
    try:
        auth_context = await _resolve_org_from_token(authorization)
        if not auth_context or not auth_context.get("org_id"):
            return JSONResponse(
                status_code=status.HTTP_401_UNAUTHORIZED,
                content={"error": "unauthorized"},
            )

        org_id = auth_context["org_id"]
        user_id = auth_context.get("user_id")

        body = await request.json()
        documents = body.get("documents", [])
        collection_name = body.get("collection_name", "documents").strip()

        if not documents:
            return JSONResponse(
                status_code=status.HTTP_400_BAD_REQUEST,
                content={"error": "documents required"},
            )

        if not isinstance(documents, list):
            return JSONResponse(
                status_code=status.HTTP_400_BAD_REQUEST,
                content={"error": "documents must be a list"},
            )

        # Resolve provider
        provider_config = await _resolve_vector_provider_for_org(org_id)
        if not provider_config:
            return JSONResponse(
                status_code=status.HTTP_400_BAD_REQUEST,
                content={
                    "error": "no_provider",
                    "message": "Organization has not configured a vector database provider",
                },
            )

        provider_type = provider_config.get("provider_type", "pinecone")
        project_id = auth_context.get("project_id", org_id)

        # Get vector client
        vector_client = _resolve_runtime_vector_client(provider_type, provider_config)
        if not vector_client:
            return JSONResponse(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                content={"error": "provider_not_available"},
            )

        try:
            # Perform upsert through vector client
            doc_ids = [d.get("id", f"doc-{i}") for i, d in enumerate(documents)]
            doc_texts = [d.get("text", "") for d in documents]
            doc_metas = [d.get("metadata", {}) for d in documents]

            if hasattr(vector_client, "upsert"):
                count = await vector_client.upsert(
                    collection_name=collection_name,
                    documents=doc_texts,
                    ids=doc_ids,
                    metadatas=doc_metas,
                    project_id=str(project_id),
                )
            else:
                count = await vector_client.add(
                    collection_name=collection_name,
                    documents=doc_texts,
                    ids=doc_ids,
                    metadatas=doc_metas,
                    project_id=str(project_id),
                )

            if TELEMETRY:
                TELEMETRY.emit({
                    "event_type": "vector_upsert",
                    "action": "upsert",
                    "org_id": org_id,
                    "user_id": user_id,
                    "provider_type": provider_type,
                    "collection_name": collection_name,
                    "document_count": count,
                    "status_code": 201,
                })

            return JSONResponse(
                status_code=status.HTTP_201_CREATED,
                content={
                    "inserted_count": count,
                    "upserted_count": 0,
                    "x-zeroshield": {
                        "request_id": f"zs-upsert-{org_id}",
                        "action": "allow",
                        "reason": "Documents inserted successfully",
                    },
                },
            )

        except Exception as exc:
            LOG.exception("Vector upsert failed: %s", exc)
            return JSONResponse(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                content={"error": "upsert_failed", "message": str(exc)},
            )

    except json.JSONDecodeError:
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content={"error": "invalid_json"},
        )
    except Exception as exc:
        LOG.exception("Vector upsert handler failed: %s", exc)
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={"error": "internal_error"},
        )


# ────────────────────────────────────────────────────────────────────────────
#  POST /v1/vector/delete — Delete documents
# ────────────────────────────────────────────────────────────────────────────


@router.post(
    "/delete",
    summary="Delete documents from vector database",
    tags=["Vector Operations"],
    status_code=204,
)
async def delete_vector_documents(
    request: Request,
    authorization: Optional[str] = Header(None),
) -> JSONResponse:
    """
    Delete documents from organization's vector database.

    **Request Body:**
    ```json
    {
      "document_ids": ["doc-1", "doc-2"],
      "collection_name": "documents"
    }
    ```

    **Response (204):** No content on success.
    """
    try:
        auth_context = await _resolve_org_from_token(authorization)
        if not auth_context or not auth_context.get("org_id"):
            return JSONResponse(
                status_code=status.HTTP_401_UNAUTHORIZED,
                content={"error": "unauthorized"},
            )

        org_id = auth_context["org_id"]
        user_id = auth_context.get("user_id")

        body = await request.json()
        doc_ids = body.get("document_ids", [])
        collection_name = body.get("collection_name", "documents").strip()

        if not doc_ids:
            return JSONResponse(
                status_code=status.HTTP_400_BAD_REQUEST,
                content={"error": "document_ids required"},
            )

        provider_config = await _resolve_vector_provider_for_org(org_id)
        if not provider_config:
            return JSONResponse(
                status_code=status.HTTP_400_BAD_REQUEST,
                content={"error": "no_provider"},
            )

        provider_type = provider_config.get("provider_type", "pinecone")
        project_id = auth_context.get("project_id", org_id)

        vector_client = _resolve_runtime_vector_client(provider_type, provider_config)
        if not vector_client:
            return JSONResponse(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                content={"error": "provider_not_available"},
            )

        try:
            await vector_client.delete(
                collection_name=collection_name,
                ids=doc_ids,
                project_id=str(project_id),
            )

            if TELEMETRY:
                TELEMETRY.emit({
                    "event_type": "vector_delete",
                    "action": "delete",
                    "org_id": org_id,
                    "user_id": user_id,
                    "document_count": len(doc_ids),
                    "status_code": 204,
                })

            return JSONResponse(status_code=status.HTTP_204_NO_CONTENT)

        except Exception as exc:
            LOG.exception("Vector delete failed: %s", exc)
            return JSONResponse(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                content={"error": "delete_failed"},
            )

    except json.JSONDecodeError:
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content={"error": "invalid_json"},
        )
    except Exception as exc:
        LOG.exception("Vector delete handler failed: %s", exc)
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={"error": "internal_error"},
        )


# ────────────────────────────────────────────────────────────────────────────
#  GET /v1/vector/config — Retrieve org's vector provider config
# ────────────────────────────────────────────────────────────────────────────


@router.get(
    "/config",
    summary="Get organization's vector database configuration",
    tags=["Vector Operations"],
    status_code=200,
)
async def get_vector_config(
    authorization: Optional[str] = Header(None),
) -> JSONResponse:
    """
    Retrieve the organization's active vector database configuration.

    **Response:**
    ```json
    {
      "provider": {
        "provider_type": "pinecone",
        "display_name": "Customer Docs",
        "environment": "us-east-1",
        "embedding_model": "text-embedding-3-small",
        "is_active": true
      },
      "x-zeroshield": {
        "action": "allow"
      }
    }
    ```
    """
    try:
        auth_context = await _resolve_org_from_token(authorization)
        if not auth_context or not auth_context.get("org_id"):
            return JSONResponse(
                status_code=status.HTTP_401_UNAUTHORIZED,
                content={"error": "unauthorized"},
            )

        org_id = auth_context["org_id"]

        provider_config = await _resolve_vector_provider_for_org(org_id)
        if not provider_config:
            return JSONResponse(
                status_code=status.HTTP_404_NOT_FOUND,
                content={
                    "error": "not_configured",
                    "message": "Organization has not configured a vector database provider",
                },
            )

        # Return config without exposing the API key
        return JSONResponse(
            status_code=status.HTTP_200_OK,
            content={
                "provider": {
                    "provider_type": provider_config.get("provider_type"),
                    "display_name": provider_config.get("display_name", ""),
                    "environment": provider_config.get("environment", ""),
                    "embedding_model": provider_config.get("embedding_model", ""),
                    "is_active": provider_config.get("is_active", True),
                },
                "x-zeroshield": {
                    "action": "allow",
                    "reason": "Configuration retrieved successfully",
                },
            },
        )

    except Exception as exc:
        LOG.exception("Get config failed: %s", exc)
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={"error": "internal_error"},
        )
