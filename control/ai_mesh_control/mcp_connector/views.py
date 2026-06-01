"""REST API views for MCP Connector.

Provides endpoints for managing MCP servers (registered locally), tool
discovery, tool controls, MCP-Firewall pre/post flight enforcement, and
structured observability. Guardrails are unified into the policy engine —
the legacy Enkrypt Secure-MCP-Gateway profile layer has been removed.
"""

import json
import logging
import os
import secrets
import time
import uuid as uuid_mod
from datetime import timedelta
from functools import lru_cache

import jsonschema
import requests
from auth.utils import get_request_organization
from django.conf import settings
from django.db import IntegrityError
from django.db.models import Count, Q
from django.http import HttpResponse
from django.utils import timezone
from rest_framework import status
from rest_framework.permissions import AllowAny, BasePermission
from rest_framework.response import Response
from rest_framework.views import APIView

from . import mcp_firewall_client
from .models import MCPEvent, MCPServerRegistration, MCPToolRegistration
from .serializers import (
    MCPEventSerializer,
    MCPServerCreateSerializer,
    MCPServerRegistrationSerializer,
    MCPToolRegistrationSerializer,
)

logger = logging.getLogger(__name__)

# HTTP read timeout (seconds) for the control -> gateway discover-tools call.
# This MUST be >= the gateway's stdio init timeout (MCP_STDIO_INIT_TIMEOUT,
# default 120s) plus margin, otherwise a legitimate first-connect that fetches
# a server package on-demand (npx/uvx cold download) is aborted control-side
# before the gateway finishes initializing — surfacing as a misleading
# "Read timed out" even though the server would have come up. Configurable so
# operators can match it to their gateway init budget.
_DISCOVER_HTTP_TIMEOUT = float(os.environ.get("MCP_DISCOVER_HTTP_TIMEOUT", "150"))


def _gateway_internal_secret() -> str:
    """Return the configured internal gateway-to-backend shared secret."""
    return (
        (getattr(settings, "GATEWAY_INTERNAL_API_KEY", "") or "").strip()
        or (getattr(settings, "AGENT_API_KEY", "") or "").strip()
    )


def _is_gateway_internal_request(request) -> bool:
    """Validate trusted gateway->backend requests using shared secret + marker header."""
    if (request.headers.get("X-Gateway-Auth", "") or "").lower() != "true":
        return False

    configured_secret = _gateway_internal_secret()
    provided_secret = (request.headers.get("X-Gateway-Internal-Key", "") or "").strip()
    if not configured_secret or not provided_secret:
        return False
    return secrets.compare_digest(provided_secret, configured_secret)


def _gateway_request_org(request):
    """Resolve org from trusted gateway headers when request is gateway-authenticated."""
    if not _is_gateway_internal_request(request):
        return None
    org_slug = (request.headers.get("X-Org-Slug", "") or "").strip().lower()
    if not org_slug:
        return None

    from auth.models import Organization

    return Organization.objects.filter(slug=org_slug, is_active=True).first()


class IsAuthenticatedOrGatewayInternal(BasePermission):
    """Allow either regular JWT auth or trusted internal gateway authentication."""

    def has_permission(self, request, view):
        user = getattr(request, "user", None)
        if user and user.is_authenticated:
            return True
        return _is_gateway_internal_request(request)


def _request_actor(request) -> tuple[int | None, str]:
    """Resolve actor identity for observability (JWT user or trusted gateway identity)."""
    user = getattr(request, "user", None)
    if user and user.is_authenticated:
        return user.id, user.get_username()

    if _is_gateway_internal_request(request):
        gateway_user_id = (request.headers.get("X-Gateway-User-Id", "") or "").strip()
        actor_user_id = int(gateway_user_id) if gateway_user_id.isdigit() else None
        key_prefix = (request.headers.get("X-Gateway-Key-Prefix", "") or "").strip()
        actor_username = f"gateway:{key_prefix}" if key_prefix else ""
        return actor_user_id, actor_username

    return None, ""


def _upstream_error_detail(exc: Exception) -> str:
    """Extract actionable details from upstream HTTP failures."""
    response = getattr(exc, "response", None)
    if response is not None:
        text = (response.text or "").strip()
        if text:
            return f"Upstream HTTP {response.status_code}: {text[:1200]}"
        return f"Upstream HTTP {response.status_code}"
    return str(exc)


# SEC-03 FIX: JSON Schema validation for MCP tool arguments
@lru_cache(maxsize=256)
def _compile_schema(schema_json: str):
    """Compile and cache JSON schema validators for performance."""
    schema = json.loads(schema_json)
    return jsonschema.Draft7Validator(schema)


def _validate_tool_arguments(tool_reg, arguments: dict) -> list[dict] | None:
    """Validate tool arguments against input_schema. Returns list of errors or None."""
    if not tool_reg or not tool_reg.input_schema:
        return None  # No schema = no validation (allow)
    
    try:
        # Serialize schema for cache lookup
        schema_key = json.dumps(tool_reg.input_schema, sort_keys=True)
        validator = _compile_schema(schema_key)
        errors = list(validator.iter_errors(arguments))
        if errors:
            return [
                {
                    "field": "/".join(str(p) for p in e.path) if e.path else "(root)",
                    "message": e.message,
                    "validator": e.validator,
                }
                for e in errors
            ]
    except Exception as exc:
        logger.warning("Schema validation failed: %s", exc)
        # Schema parsing error - don't block, just log
        return None
    
    return None  # No errors


def _request_org(request):
    """Resolve request organization for strict tenant scoping."""
    org = get_request_organization(request)
    if org is not None:
        return org
    return _gateway_request_org(request)


def _org_scoped_servers_queryset(request):
    org = _request_org(request)
    if org is None:
        return MCPServerRegistration.objects.none(), None
    return MCPServerRegistration.objects.filter(organization=org), org


def _store_oauth_tokens(server, tok: dict) -> None:
    """Persist an OAuth token response onto the server (encrypted fields).

    The access token is stored in ``auth_token`` (the existing encrypted
    bearer field) so the gateway forwards it as a normal ``Authorization:
    Bearer`` with no OAuth awareness. Refresh token + expiry are stored for
    silent renewal.
    """
    access = (tok.get("access_token") or "").strip()
    update_fields = ["updated_at"]
    if access:
        server.auth_token = access
        update_fields.append("auth_token")
    new_refresh = tok.get("refresh_token")
    if new_refresh:
        server.oauth_refresh_token = new_refresh
        update_fields.append("oauth_refresh_token")
    expires_in = tok.get("expires_in")
    server.oauth_token_expires_at = None
    if expires_in:
        try:
            server.oauth_token_expires_at = timezone.now() + timedelta(seconds=int(expires_in))
        except (TypeError, ValueError):
            server.oauth_token_expires_at = None
    update_fields.append("oauth_token_expires_at")
    if tok.get("scope"):
        server.oauth_scope = tok["scope"]
        update_fields.append("oauth_scope")
    server.auth_type = "oauth"
    update_fields.append("auth_type")
    server.save(update_fields=update_fields)


def _ensure_oauth_token_fresh(server) -> bool:
    """Refresh the OAuth access token in place when missing or near expiry.

    Returns ``True`` when the server has a *usable* (fresh or just-refreshed)
    token that is safe to forward upstream, ``False`` when re-authentication is
    required. No-op-True for non-OAuth servers. On refresh failure we do NOT
    raise (a hard failure here would 500 the hot path for every tool call);
    instead we surface the cause per-org via ``last_sync_error`` +
    ``needs_reauth`` so the operator sees an actionable "re-authenticate
    <server>" signal while other orgs keep working. Critically we now also
    return ``False`` so callers skip forwarding the expired token (which the
    upstream rejects with an opaque ``invalid_token``).
    """
    if getattr(server, "auth_type", "") != "oauth":
        return True
    expires_at = getattr(server, "oauth_token_expires_at", None)
    has_token = bool(server.auth_token)
    near_expiry = bool(expires_at) and expires_at <= timezone.now() + timedelta(seconds=60)
    if has_token and not near_expiry:
        return True
    refresh = getattr(server, "oauth_refresh_token", "") or ""
    token_endpoint = getattr(server, "oauth_token_endpoint", "") or ""
    if not refresh or not token_endpoint:
        # Configured for OAuth but no usable refresh material — needs re-auth.
        _mark_needs_reauth(
            server,
            "OAuth credentials missing or incomplete — re-authenticate this server.",
        )
        return False
    try:
        from . import oauth as oauth_mod

        tok = oauth_mod.refresh_access_token(
            token_endpoint,
            refresh,
            server.oauth_client_id,
            server.oauth_client_secret,
            server.oauth_resource,
            server.oauth_scope,
        )
        _store_oauth_tokens(server, tok)
        _clear_needs_reauth(server)
        logger.info("Refreshed OAuth token for %s", server.server_slug)
        return True
    except Exception as exc:  # noqa: BLE001
        logger.error("OAuth token refresh failed for %s: %s", server.server_slug, exc)
        _mark_needs_reauth(
            server,
            f"OAuth token refresh failed ({exc}) — re-authenticate this server.",
        )
        return False


def _mark_needs_reauth(server, message: str) -> None:
    """Persist an actionable per-org auth error without raising."""
    try:
        server.needs_reauth = True
        server.last_sync_error = message
        server.last_sync_attempt_at = timezone.now()
        server.save(update_fields=["needs_reauth", "last_sync_error", "last_sync_attempt_at"])
    except Exception as exc:  # noqa: BLE001
        logger.error("Failed to persist needs_reauth for %s: %s", server.server_slug, exc)


def _clear_needs_reauth(server) -> None:
    """Clear a previously-set auth error after a successful refresh."""
    if not getattr(server, "needs_reauth", False) and not getattr(server, "last_sync_error", ""):
        return
    try:
        server.needs_reauth = False
        server.last_sync_error = ""
        server.save(update_fields=["needs_reauth", "last_sync_error"])
    except Exception as exc:  # noqa: BLE001
        logger.error("Failed to clear needs_reauth for %s: %s", server.server_slug, exc)


def _discover_tools_via_gateway(server, org) -> tuple[list[dict], str | None]:
    """Discover tools from an MCP server via the gateway's internal endpoint.

    Works for stdio, websocket, and streamable-http servers. The gateway
    routes the JSON-RPC tools/list call to the appropriate adapter or
    directly to the upstream MCP server.

    Returns ``(tools, error)``. ``error`` is ``None`` on success or a short
    human-readable string on failure. Previously this swallowed every
    failure and returned ``[]``, which surfaced to the operator as a
    misleading ``synced: 0`` with no cause -- the single biggest reason
    the MCP sync feature appeared silently broken.
    """
    gateway_url = (
        (getattr(settings, "GATEWAY_URL", "") or "").strip().rstrip("/")
        or os.environ.get("GATEWAY_URL", "").strip().rstrip("/")
        or "http://gateway:8300"
    )
    internal_key = _gateway_internal_secret()
    if not internal_key:
        logger.warning("Cannot discover tools via gateway: no GATEWAY_INTERNAL_API_KEY configured")
        return [], "Gateway internal key not configured (GATEWAY_INTERNAL_API_KEY)."

    payload = {
        "org_slug": org.slug,
        "server_slug": server.server_slug,
        # Send the authoritative url/transport so the gateway overrides any
        # stale TTL-cached config (fixes edit-then-resync using the old URL).
        "url": server.url or "",
        "transport": server.transport or "streamable-http",
    }

    # Pass auth credentials for upstream HTTP servers
    if server.transport in ("streamable-http", "sse") and hasattr(server, "auth_type"):
        auth_type = getattr(server, "auth_type", "none") or "none"
        if auth_type == "oauth":
            # Refresh if needed, then forward the OAuth access token as a
            # normal bearer so the gateway needs no OAuth awareness. Only
            # forward when the token is usable; an expired/unrefreshable token
            # is rejected upstream as opaque ``invalid_token``, so instead we
            # short-circuit with an actionable re-auth error.
            if _ensure_oauth_token_fresh(server) and server.auth_token:
                payload["auth_type"] = "bearer"
                payload["auth_token"] = server.auth_token
            else:
                return [], (
                    f"{server.server_slug} needs re-authentication — "
                    "the OAuth token expired and could not be refreshed."
                )
        elif auth_type != "none":
            payload["auth_type"] = auth_type
            for field in ("auth_token", "auth_username", "auth_password",
                          "auth_header_key", "auth_header_value"):
                val = getattr(server, field, None)
                if val:
                    payload[field] = val

    try:
        resp = requests.post(
            f"{gateway_url}/v1/mcp/internal/discover-tools",
            json=payload,
            headers={
                "Content-Type": "application/json",
                "X-Gateway-Internal-Key": internal_key,
            },
            timeout=_DISCOVER_HTTP_TIMEOUT,
        )
        if resp.status_code != 200:
            logger.warning(
                "Gateway discover-tools returned HTTP %s for %s/%s: %s",
                resp.status_code, org.slug, server.server_slug,
                resp.text[:500],
            )
            return [], f"Gateway returned HTTP {resp.status_code}: {resp.text[:200]}"

        data = resp.json()
        # JSON-RPC response: {"jsonrpc":"2.0","id":1,"result":{"tools":[...]}}
        # A JSON-RPC error object means the upstream MCP rejected the call
        # (e.g. auth failure) -- surface it rather than reporting 0 tools.
        if isinstance(data, dict) and data.get("error"):
            err = data["error"]
            msg = err.get("message") if isinstance(err, dict) else str(err)
            return [], f"Upstream MCP error: {msg}"
        result = data.get("result", {})
        tools = result.get("tools", [])
        if isinstance(tools, list):
            logger.info(
                "Gateway discover-tools found %d tools for %s/%s",
                len(tools), org.slug, server.server_slug,
            )
            return tools, None
        return [], "Malformed tools/list response from gateway."
    except Exception as exc:
        logger.warning(
            "Gateway discover-tools failed for %s/%s: %s",
            org.slug, server.server_slug, exc,
        )
        return [], f"Discovery request failed: {exc}"


def _resync_server_tools(server, org) -> dict:
    """Discover tools via the gateway and persist them for *server*.

    Shared by the API re-sync view (:class:`MCPServerToolListView`) and the
    ``resync_mcp_servers`` management command so both paths handle pruning,
    connection status, and ``last_sync_error`` identically. Returns a summary
    dict ``{synced, pruned, error, connection_status}``.
    """
    server_tool_names: set[str] = set()
    gateway_tools, sync_error = _discover_tools_via_gateway(server, org)
    for tool in gateway_tools:
        tname = tool.get("name", "")
        if tname:
            server_tool_names.add(tname)
            MCPToolRegistration.objects.update_or_create(
                server=server,
                tool_name=tname,
                defaults={
                    "description": tool.get("description", ""),
                    "input_schema": tool.get("inputSchema", {}),
                    "organization": org,
                    "last_seen_at": timezone.now(),
                },
            )
    # Prune tools the upstream no longer advertises -- ONLY on success, so a
    # transient upstream/auth failure does not wipe the operator's view.
    pruned = 0
    if sync_error is None:
        stale = MCPToolRegistration.objects.filter(server=server).exclude(
            tool_name__in=server_tool_names
        )
        pruned = stale.count()
        stale.delete()
    server.tools_count = MCPToolRegistration.objects.filter(server=server).count()
    server.last_sync_at = timezone.now()
    server.connection_status = "connected" if (sync_error is None) else "failed"
    update_fields = ["tools_count", "last_sync_at", "connection_status", "updated_at"]
    if hasattr(server, "last_sync_error"):
        server.last_sync_error = sync_error or ""
        update_fields.append("last_sync_error")
    # Map an interactive-auth / BYOK-OAuth failure (e.g. a stdio `mcp-remote`
    # server whose headless OAuth login the gateway detected and short-circuited)
    # to an actionable ``needs_reauth`` badge instead of a generic "failed", so
    # the operator sees "re-authenticate" rather than an opaque error. Cleared
    # on success or any non-auth failure.
    if hasattr(server, "needs_reauth"):
        _err_l = (sync_error or "").lower()
        server.needs_reauth = sync_error is not None and any(
            h in _err_l
            for h in (
                "interactive authentication",
                "requires re-authentication",
                "re-authentication",
                "re-authenticate",
                "byok / oauth",
                "needs_reauth",
                # Bearer-token BYOK failures: an invalid/expired token the
                # client supplied at runtime — actionable as "provide valid
                # credentials" rather than an opaque generic failure.
                "invalid_token",
                "invalid token",
                "unauthorized",
            )
        )
        update_fields.append("needs_reauth")
    if hasattr(server, "last_sync_attempt_at"):
        server.last_sync_attempt_at = timezone.now()
        update_fields.append("last_sync_attempt_at")
    server.save(update_fields=update_fields)
    return {
        "synced": len(server_tool_names),
        "pruned": pruned,
        "error": sync_error,
        "connection_status": server.connection_status,
    }


def _call_tool_via_gateway(server, org, tool_name: str, arguments: dict) -> dict:
    """Execute a tool through the gateway's internal MCP route.

    This is used for all MCP server transports (stdio, websocket,
    streamable-http, sse). The gateway proxies directly to the upstream
    URL (or spawns the stdio process) and enforces policy in-band.
    """
    gateway_url = (
        (getattr(settings, "GATEWAY_URL", "") or "").strip().rstrip("/")
        or os.environ.get("GATEWAY_URL", "").strip().rstrip("/")
        or "http://gateway:8300"
    )
    internal_key = _gateway_internal_secret()
    if not internal_key:
        raise requests.RequestException(
            "Gateway tool execution unavailable: missing GATEWAY_INTERNAL_API_KEY"
        )

    payload = {
        "org_slug": org.slug,
        "server_slug": server.server_slug,
        "tool_name": tool_name,
        "arguments": arguments or {},
        # Authoritative url/transport so the gateway overrides stale cached config.
        "url": server.url or "",
        "transport": server.transport or "streamable-http",
    }

    if server.transport in ("streamable-http", "sse") and hasattr(server, "auth_type"):
        auth_type = getattr(server, "auth_type", "none") or "none"
        if auth_type == "oauth":
            if _ensure_oauth_token_fresh(server) and server.auth_token:
                payload["auth_type"] = "bearer"
                payload["auth_token"] = server.auth_token
            else:
                raise requests.RequestException(
                    f"{server.server_slug} needs re-authentication — "
                    "the OAuth token expired and could not be refreshed."
                )
        elif auth_type != "none":
            payload["auth_type"] = auth_type
            for field in (
                "auth_token",
                "auth_username",
                "auth_password",
                "auth_header_key",
                "auth_header_value",
            ):
                val = getattr(server, field, None)
                if val:
                    payload[field] = val

    try:
        resp = requests.post(
            f"{gateway_url}/v1/mcp/internal/tools-call",
            json=payload,
            headers={
                "Content-Type": "application/json",
                "X-Gateway-Internal-Key": internal_key,
            },
            timeout=90,
        )
        if resp.status_code != 200:
            raise requests.RequestException(
                f"Gateway tool execution returned HTTP {resp.status_code}: {resp.text[:500]}"
            )

        data = resp.json() if resp.content else {}
        if not isinstance(data, dict):
            raise requests.RequestException("Gateway tool execution returned non-JSON payload")

        rpc_error = data.get("error")
        if isinstance(rpc_error, dict):
            raise requests.RequestException(rpc_error.get("message") or "Gateway tool execution failed")
        if rpc_error:
            raise requests.RequestException(str(rpc_error))

        return data.get("result") if isinstance(data.get("result"), dict) else data
    except requests.RequestException:
        raise
    except Exception as exc:
        raise requests.RequestException(f"Gateway tool execution failed: {exc}") from exc


# ── Health ────────────────────────────────────────────────────────────


class MCPServicesHealthView(APIView):
    """Health status of Secure-MCP-Gateway and MCP-Firewall.

    ContextForge has been removed (DECISION-D Phase 0); the gateway proxies
    streamable-http/sse directly to upstream URLs and spawns stdio processes
    via the local adapter, so there is no upstream registry to probe.
    """

    permission_classes = [IsAuthenticatedOrGatewayInternal]

    # Detectors enforced in-band by the policy engine on every tool call.
    # Enkrypt-only detectors (hallucination/toxicity) were never enforced on
    # the hot path and have been removed to end the policy/guardrail duality.
    _BUILTIN_DETECTORS = [
        "pii_redaction", "injection_attack", "sensitive_data",
        "profanity", "topic_restriction",
        "policy_violation", "pii_leakage", "data_exfiltration",
    ]

    def get(self, request):
        return Response(
            {
                "mcp_firewall": mcp_firewall_client.health(),
                "builtin_detectors": self._BUILTIN_DETECTORS,
            }
        )


# ── MCP Servers ───────────────────────────────────────────────────


class MCPServerListCreateView(APIView):
    """List all registered MCP servers or register a new one."""

    permission_classes = [IsAuthenticatedOrGatewayInternal]

    def get(self, request):
        registrations, _org = _org_scoped_servers_queryset(request)
        serializer = MCPServerRegistrationSerializer(registrations, many=True)
        return Response(serializer.data)

    def post(self, request):
        org = _request_org(request)
        if org is None:
            return Response(
                {"error": "No organization context for this user."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        serializer = MCPServerCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        transport = (serializer.validated_data.get("transport") or "").strip().lower()
        local_data = MCPServerCreateSerializer.local_model_data(serializer.validated_data)

        # DECISION-D Phase 0: ContextForge removed. All transports register
        # locally; the gateway proxies streamable-http/sse directly to the
        # upstream URL and spawns stdio processes via mcp_stdio_adapter.
        # Policy enforcement (G7 redaction, G8 scope, tool toggles, compliance
        # tagging) runs in-band on every tool call regardless of transport.
        try:
            registration, created = MCPServerRegistration.objects.get_or_create(
                organization=org,
                name=local_data["name"],
                defaults={k: v for k, v in local_data.items() if k != "name"},
            )
        except IntegrityError:
            return Response(
                {"error": "An MCP server with this name already exists in your organization."},
                status=status.HTTP_409_CONFLICT,
            )
        if not created:
            return Response(
                {
                    "error": "An MCP server with this name already exists in your organization.",
                    "existing_server": MCPServerRegistrationSerializer(registration).data,
                },
                status=status.HTTP_409_CONFLICT,
            )
        logger.info(
            "mcp_connector.server.registered org_id=%s user_id=%s server=%s transport=%s",
            org.id,
            request.user.id,
            registration.name,
            transport,
        )

        # ── Auto-provision a default MCP gateway key for this org ──
        gw_key_info = {}
        try:
            from core.models import GatewayAPIKey

            actor = request.user if getattr(request.user, "is_authenticated", False) else None
            if actor is None:
                from django.contrib.auth import get_user_model
                User = get_user_model()
                actor = User.objects.filter(is_superuser=True).order_by("id").first()
            if actor:
                key_instance, raw_key = GatewayAPIKey.ensure_default_for_org(org, actor)
                gw_key_info["default_gateway_key_prefix"] = key_instance.prefix
                gw_key_info["has_gateway_key"] = True
                if raw_key:
                    gw_key_info["default_gateway_key"] = raw_key
                    gw_key_info["gateway_key_warning"] = (
                        "A default MCP gateway API key was auto-created for your organization. "
                        "Store it securely — it will not be shown again."
                    )
                    logger.info(
                        "mcp_connector.gateway_key.auto_provisioned org_id=%s prefix=%s",
                        org.id,
                        key_instance.prefix,
                    )
        except Exception:
            logger.exception("Failed to auto-provision MCP gateway key for org %s", org.id)

        response_data = MCPServerRegistrationSerializer(registration).data
        response_data.update(gw_key_info)

        return Response(
            response_data,
            status=status.HTTP_201_CREATED,
        )


class MCPServerDetailView(APIView):
    """Retrieve, update, or delete a single MCP server registration."""

    permission_classes = [IsAuthenticatedOrGatewayInternal]

    def get(self, request, pk):
        registrations, _org = _org_scoped_servers_queryset(request)
        try:
            reg = registrations.get(pk=pk)
        except MCPServerRegistration.DoesNotExist:
            return Response({"error": "Not found"}, status=status.HTTP_404_NOT_FOUND)
        return Response(MCPServerRegistrationSerializer(reg).data)

    def patch(self, request, pk):
        registrations, _org = _org_scoped_servers_queryset(request)
        try:
            reg = registrations.get(pk=pk)
        except MCPServerRegistration.DoesNotExist:
            return Response({"error": "Not found"}, status=status.HTTP_404_NOT_FOUND)

        serializer = MCPServerCreateSerializer(reg, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)

        # DECISION-D Phase 0: no upstream registry to update; server config is
        # the single source of truth and the gateway re-reads it via the
        # `_get_server_config` Redis cache (TTL 120s) on next tool call.
        local_data = MCPServerCreateSerializer.local_model_data(serializer.validated_data)
        for field, value in local_data.items():
            setattr(reg, field, value)
        reg.save(update_fields=[*local_data.keys(), "updated_at"])
        return Response(MCPServerRegistrationSerializer(reg).data)

    def delete(self, request, pk):
        registrations, org = _org_scoped_servers_queryset(request)
        try:
            reg = registrations.get(pk=pk)
        except MCPServerRegistration.DoesNotExist:
            return Response({"error": "Not found"}, status=status.HTTP_404_NOT_FOUND)

        logger.info(
            "mcp_connector.server.deleted org_id=%s user_id=%s server=%s",
            getattr(org, "id", None),
            request.user.id,
            reg.name,
        )
        reg.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


# ── Tool Discovery ──────────────────────────────────────────────────────


class MCPToolListView(APIView):
    """List all tools available across all federated MCP servers."""

    permission_classes = [IsAuthenticatedOrGatewayInternal]

    def get(self, request):
        registrations, org = _org_scoped_servers_queryset(request)
        if org is None:
            return Response([])

        server_slug_hint = (request.headers.get("X-Server-Slug", "") or "").strip().lower()
        if server_slug_hint:
            target_server = registrations.filter(server_slug=server_slug_hint).first()
            if target_server is None:
                return Response(
                    {"error": "Server not found for this organization."},
                    status=status.HTTP_404_NOT_FOUND,
                )
            registrations = registrations.filter(server_slug=server_slug_hint)

        # Source tools from local inventory. Inventory is updated via the
        # per-server sync endpoint which calls the gateway's
        # /v1/mcp/internal/discover-tools route for every transport.       
        tools = []
        tool_qs = MCPToolRegistration.objects.filter(
            organization=org,
            server__in=registrations,
        ).select_related("server")
        for tr in tool_qs:
            tools.append(
                {
                    "name": tr.tool_name,
                    "description": tr.description or "",
                    "inputSchema": tr.input_schema or {},
                    "server_name": tr.server.name,
                    "server_id": str(tr.server.id),
                    "server_slug": tr.server.server_slug,
                    "transport": tr.server.transport,
                    "enabled": tr.enabled,
                    "source": "registration",
                }
            )

        logger.info(
            "mcp_connector.tools.listed org_id=%s user_id=%s tool_count=%s",
            org.id,
            request.user.id,
            len(tools) if isinstance(tools, list) else 0,
        )
        return Response(tools)


class MCPToolCallView(APIView):
    """Invoke an MCP tool with pre-flight policy, tool controls, and post-flight audit."""

    permission_classes = [IsAuthenticatedOrGatewayInternal]

    def post(self, request):
        _registrations, org = _org_scoped_servers_queryset(request)
        if org is None:
            return Response(
                {"error": "No organization context for this user."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        actor_user_id, _actor_username = _request_actor(request)

        tool_name = request.data.get("name")
        arguments = request.data.get("arguments", {})
        if not tool_name:
            return Response({"error": "Missing 'name' field"}, status=status.HTTP_400_BAD_REQUEST)

        server_slug_hint = (
            request.headers.get("X-Server-Slug", "")
            or request.data.get("server_slug", "")
            or ""
        ).strip().lower()
        requested_server = None
        if server_slug_hint:
            requested_server = MCPServerRegistration.objects.filter(
                organization=org,
                server_slug=server_slug_hint,
            ).first()
            if requested_server is None:
                return Response(
                    {"error": "Server not found for this organization.", "server_slug": server_slug_hint},
                    status=status.HTTP_404_NOT_FOUND,
                )

        request_id = str(uuid_mod.uuid4())[:16]
        t0 = time.time()

        # ── Tool enable/disable check ──
        tool_reg_qs = MCPToolRegistration.objects.filter(
            organization=org, tool_name=tool_name
        )
        if requested_server is not None:
            tool_reg_qs = tool_reg_qs.filter(server=requested_server)
        tool_reg = tool_reg_qs.first()

        resolved_server = requested_server or (tool_reg.server if tool_reg else None)
        if tool_reg and not tool_reg.enabled:
            _record_event(
                org=org, request=request, tool_name=tool_name,
                decision="block", reason="tool_disabled",
                request_id=request_id, latency_ms=int((time.time() - t0) * 1000),
                server_name=tool_reg.server.name if tool_reg.server else "",
                server_slug=tool_reg.server.server_slug if tool_reg.server else "",
            )
            return Response(
                {"error": "Tool is disabled", "reason": "tool_disabled", "request_id": request_id},
                status=status.HTTP_403_FORBIDDEN,
            )

        # ── SEC-03 FIX: JSON Schema validation ──
        validation_errors = _validate_tool_arguments(tool_reg, arguments)
        if validation_errors:
            _record_event(
                org=org, request=request, tool_name=tool_name,
                decision="block", reason="schema_validation_failed",
                request_id=request_id, latency_ms=int((time.time() - t0) * 1000),
                server_name=resolved_server.name if resolved_server else "",
                server_slug=resolved_server.server_slug if resolved_server else "",
            )
            logger.warning(
                "mcp_connector.tool.schema_validation_failed org=%s tool=%s errors=%s",
                org.id, tool_name, validation_errors,
            )
            return Response(
                {
                    "error": "Schema validation failed",
                    "reason": "invalid_arguments",
                    "validation_errors": validation_errors,
                    "request_id": request_id,
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        # ── PRE-FLIGHT: Built-in policy engine check (org+server scoped) ──
        from policy.engine import evaluate as policy_evaluate
        from policy.models import Policy as PolicyModel
        from policy.redaction import apply_field_redaction, redact_structured
        from django.db.models import Q
        from urllib.parse import unquote as _unquote

        # ── G8: extract actor identifiers for per-user/agent/role policy
        # scoping. user_id is JWT-authenticated (or trusted gateway
        # header); agent_id is the API key prefix (8 chars, identity of
        # the calling key); roles come from the gateway's signed Redis
        # auth payload, forwarded as a comma-separated URL-quoted header.
        # All three feed BOTH the policy queryset filter AND the engine
        # context so rule conditions can also reference them.
        agent_id = (request.headers.get("X-Gateway-Key-Prefix", "") or "").strip()
        raw_roles_header = request.headers.get("X-Gateway-Roles", "") or ""
        actor_roles: list[str] = []
        if raw_roles_header:
            for chunk in raw_roles_header.split(","):
                chunk = chunk.strip()
                if not chunk:
                    continue
                try:
                    actor_roles.append(_unquote(chunk))
                except Exception:
                    actor_roles.append(chunk)

        arg_text = " ".join(str(v) for v in arguments.values()) if arguments else ""
        policy_context = {
            "prompt": f"tool:{tool_name} {arg_text}",
            "response": "",
            # Structured tool arguments so scope='key' input rules can target
            # a single argument by name (engine reads context["input_args"]).
            "input_args": arguments or {},
            "user_id": actor_user_id,
            "agent_id": agent_id,
            "roles": actor_roles,
        }
        # Get policies: org-specific + system-wide (org=None), in the MCP
        # domain PLUS the universal 'global' baseline. Global policies apply
        # everywhere; severity (ACTION_ORDER) resolves any overlap with MCP
        # rules and redaction hints are unioned, so the two never conflict.
        policy_qs = PolicyModel.objects.filter(
            enabled=True,
            policy_domain__in=["mcp", "global"],
        ).filter(
            Q(organization=org) | Q(organization__isnull=True)
        ).prefetch_related("rules")
        if resolved_server:
            policy_qs = policy_qs.filter(
                Q(mcp_server__isnull=True) | Q(mcp_server=resolved_server)
            )
        # ── G8: actor allowlist filters. Each dimension uses
        # "empty list = wildcard" semantics (existing rows after
        # migration 0029 default to []). ``__isnull=True`` is included
        # defensively in case any row predates the migration and was
        # not backfilled. ``__contains=[v]`` is the correct ArrayField
        # containment operator (Django 6 + Postgres array @>); we use
        # ``__overlap`` for roles since a user may have multiple roles
        # and we want match if ANY user role intersects the allowlist.
        if actor_user_id is not None:
            policy_qs = policy_qs.filter(
                Q(allowed_user_ids__isnull=True)
                | Q(allowed_user_ids=[])
                | Q(allowed_user_ids__contains=[actor_user_id])
            )
        else:
            # Anonymous/unauthenticated: only policies with empty
            # allowlist (wildcard) may apply.
            policy_qs = policy_qs.filter(
                Q(allowed_user_ids__isnull=True) | Q(allowed_user_ids=[])
            )
        if agent_id:
            policy_qs = policy_qs.filter(
                Q(allowed_agent_ids__isnull=True)
                | Q(allowed_agent_ids=[])
                | Q(allowed_agent_ids__contains=[agent_id])
            )
        else:
            policy_qs = policy_qs.filter(
                Q(allowed_agent_ids__isnull=True) | Q(allowed_agent_ids=[])
            )
        if actor_roles:
            policy_qs = policy_qs.filter(
                Q(allowed_roles__isnull=True)
                | Q(allowed_roles=[])
                | Q(allowed_roles__overlap=actor_roles)
            )
        else:
            policy_qs = policy_qs.filter(
                Q(allowed_roles__isnull=True) | Q(allowed_roles=[])
            )
        # Materialization is deferred until AFTER ``policy_evaluate``
        # because the engine calls ``.filter(policy_domain=...)`` on the
        # queryset and would crash on a list. We re-query by id below
        # to collect each matched policy's ``redaction_fields`` for
        # post-call response scrubbing (G7).
        eval_result = policy_evaluate(policy_context, policies_qs=policy_qs, domain="mcp", tool_name=tool_name)
        if eval_result.action == "block":
            _record_event(
                org=org, request=request, tool_name=tool_name,
                decision="block",
                reason=eval_result.message or "blocked_by_builtin_policy",
                policy_ids=eval_result.matched_policy_ids,
                request_id=request_id, latency_ms=int((time.time() - t0) * 1000),
                server_name=resolved_server.name if resolved_server else "",
                server_slug=resolved_server.server_slug if resolved_server else "",
                metadata={
                    "matched_policy_codes": list(eval_result.matched_policy_codes or []),
                    "matched_rule_names": list(eval_result.matched_rule_names or []),
                },
            )
            logger.warning(
                "mcp_connector.tool.blocked_by_policy org=%s user=%s tool=%s policies=%s",
                org.id, actor_user_id, tool_name, eval_result.matched_policy_codes,
            )
            return Response(
                {
                    "error": "Tool call blocked by policy",
                    "reason": eval_result.message,
                    "matched_policies": eval_result.matched_policy_codes,
                    "matched_rules": eval_result.matched_rule_names,
                    "request_id": request_id,
                },
                status=status.HTTP_403_FORBIDDEN,
            )

        # ── INPUT redaction: scrub sensitive data OUT of the tool arguments
        # BEFORE they are forwarded to the gateway/tool. Driven by redact
        # rules whose direction includes the input side (input|both). This
        # prevents secrets in the prompt from ever reaching the downstream
        # tool. ``arguments`` is replaced with a redacted copy used for the
        # actual call; the original is not mutated.
        if eval_result.action != "block" and eval_result.redaction_hints:
            try:
                redacted_args = redact_structured(
                    arguments or {}, eval_result.redaction_hints, "input"
                )
                if isinstance(redacted_args, dict):
                    arguments = redacted_args
            except Exception as exc:  # pragma: no cover - defensive
                logger.warning(
                    "mcp_connector.tool.input_redaction_failed org=%s tool=%s err=%s",
                    org.id, tool_name, exc,
                )

        # ── PRE-FLIGHT: MCP-Firewall policy check (external, best-effort) ──
        policy_result = mcp_firewall_client.preflight_check(
            tool_name, arguments,
            org_id=str(org.id), user_id=str(actor_user_id or ""),
            server_name=resolved_server.name if resolved_server else "",
        )
        if not policy_result.get("allowed", True):
            _record_event(
                org=org, request=request, tool_name=tool_name,
                decision="block", reason=policy_result.get("reason", "blocked_by_policy"),
                policy_ids=policy_result.get("policy_ids", []),
                request_id=request_id, latency_ms=int((time.time() - t0) * 1000),
                server_name=resolved_server.name if resolved_server else "",
                server_slug=resolved_server.server_slug if resolved_server else "",
                metadata={
                    "matched_policy_codes": list(policy_result.get("violations", []) or []),
                    "matched_rule_names": [],
                },
            )
            logger.warning(
                "mcp_connector.tool.blocked org=%s user=%s tool=%s reason=%s",
                org.id, actor_user_id, tool_name, policy_result.get("reason"),
            )
            return Response(
                {
                    "error": "Tool call blocked by policy",
                    "reason": policy_result.get("reason", ""),
                    "violations": policy_result.get("violations", []),
                    "request_id": request_id,
                },
                status=status.HTTP_403_FORBIDDEN,
            )

        # ── EXECUTE: always via gateway (DECISION-D Phase 0) ──
        try:
            if not resolved_server:
                return Response(
                    {"error": "Tool call failed", "detail": "No server resolved for tool", "request_id": request_id},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            logger.info(
                "mcp_connector.tool.gateway_execution org_id=%s user_id=%s tool=%s server=%s transport=%s",
                org.id,
                actor_user_id,
                tool_name,
                resolved_server.server_slug,
                (resolved_server.transport or "").strip().lower(),
            )
            result = _call_tool_via_gateway(resolved_server, org, tool_name, arguments)
        except requests.RequestException as exc:
            latency_ms = int((time.time() - t0) * 1000)
            mcp_firewall_client.postflight_audit(
                tool_name, org_id=str(org.id), user_id=str(actor_user_id or ""),
                decision="error", success=False, latency_ms=latency_ms,
                server_name=resolved_server.name if resolved_server else "",
            )
            _record_event(
                org=org, request=request, tool_name=tool_name,
                decision="error", reason=str(exc),
                request_id=request_id, latency_ms=latency_ms,
                server_name=resolved_server.name if resolved_server else "",
                server_slug=resolved_server.server_slug if resolved_server else "",
            )
            logger.warning(
                "mcp_connector.tool.failed org_id=%s user_id=%s tool=%s detail=%s",
                org.id, actor_user_id, tool_name, str(exc),
            )
            return Response(
                {"error": "Tool call failed", "detail": _upstream_error_detail(exc), "request_id": request_id},
                status=status.HTTP_502_BAD_GATEWAY,
            )

        latency_ms = int((time.time() - t0) * 1000)

        # ── OUTPUT evaluation: re-run the SAME policy set against the tool
        # RESPONSE so rules whose direction targets the output side can act
        # on returned data. Pre-call evaluation saw an empty response, so
        # output rules could not have matched yet. We populate both a
        # serialized ``response`` string (entire-scope rules) and the raw
        # ``output_data`` dict (scope='key' rules).
        try:
            output_context = {
                "prompt": "",
                "response": json.dumps(result, ensure_ascii=False, default=str)
                if not isinstance(result, str)
                else result,
                "output_data": result,
                "user_id": actor_user_id,
                "agent_id": agent_id,
                "roles": actor_roles,
            }
            eval_out = policy_evaluate(
                output_context, policies_qs=policy_qs, domain="mcp", tool_name=tool_name
            )
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning(
                "mcp_connector.tool.output_eval_failed org=%s tool=%s err=%s",
                org.id, tool_name, exc,
            )
            eval_out = None

        # Output BLOCK: a rule decided the RESPONSE must not leave. The tool
        # already executed, but we refuse to return its data to the caller.
        if eval_out is not None and eval_out.action == "block":
            _record_event(
                org=org, request=request, tool_name=tool_name,
                decision="block",
                reason=eval_out.message or "blocked_by_output_policy",
                policy_ids=eval_out.matched_policy_ids,
                request_id=request_id, latency_ms=latency_ms,
                server_name=resolved_server.name if resolved_server else "",
                server_slug=resolved_server.server_slug if resolved_server else "",
                metadata={
                    "matched_policy_codes": list(eval_out.matched_policy_codes or []),
                    "matched_rule_names": list(eval_out.matched_rule_names or []),
                    "stage": "output",
                },
            )
            logger.warning(
                "mcp_connector.tool.output_blocked org=%s user=%s tool=%s policies=%s",
                org.id, actor_user_id, tool_name, eval_out.matched_policy_codes,
            )
            return Response(
                {
                    "error": "Tool response blocked by policy",
                    "reason": eval_out.message,
                    "matched_policies": eval_out.matched_policy_codes,
                    "matched_rules": eval_out.matched_rule_names,
                    "request_id": request_id,
                },
                status=status.HTTP_403_FORBIDDEN,
            )

        # Output REDACTION: scrub the response via output-direction redact
        # rules (regex/keyword for entire-scope, key replacement for
        # scope='key'). Union of both input/both/output hints handled inside.
        if eval_out is not None and eval_out.redaction_hints:
            try:
                result = redact_structured(result, eval_out.redaction_hints, "output")
            except Exception as exc:  # pragma: no cover - defensive
                logger.warning(
                    "mcp_connector.tool.output_redaction_failed org=%s tool=%s err=%s",
                    org.id, tool_name, exc,
                )

        # Merge output-stage matches into the aggregate result used for
        # event logging / G7 field-union below.
        if eval_out is not None:
            eval_result.matched_policy_ids = list(
                dict.fromkeys(eval_result.matched_policy_ids + eval_out.matched_policy_ids)
            )
            eval_result.matched_policy_codes = list(
                dict.fromkeys(eval_result.matched_policy_codes + eval_out.matched_policy_codes)
            )
            eval_result.matched_rule_names = list(
                dict.fromkeys(eval_result.matched_rule_names + eval_out.matched_rule_names)
            )

        # ── G7: response-field redaction. Union of ``redaction_fields``
        # from every matched policy is applied recursively to the tool
        # result. The trigger condition (per adopted decision D6) is
        # simply "policy matched AND has non-empty redaction_fields" —
        # we deliberately do NOT gate on ``eval_result.action == 'redact'``
        # because field redaction is an output-shaping concern that is
        # orthogonal to the block/allow/redact verdict. If no matched
        # policy lists any fields, this is a no-op and ``result`` flows
        # through unchanged.
        redacted_field_names: list[str] = []
        if eval_result.matched_policy_ids:
            try:
                from policy.models import Policy as _PolicyModel
                fields_union: set[str] = set()
                for fields in _PolicyModel.objects.filter(
                    id__in=list(eval_result.matched_policy_ids),
                ).values_list("redaction_fields", flat=True):
                    if fields:
                        fields_union.update(f for f in fields if isinstance(f, str) and f)
                if fields_union:
                    redacted_field_names = sorted(fields_union)
                    result = apply_field_redaction(result, redacted_field_names)
            except Exception as exc:  # pragma: no cover - defensive
                logger.warning(
                    "mcp_connector.tool.field_redaction_failed org=%s tool=%s err=%s",
                    org.id, tool_name, exc,
                )

        # ── POST-FLIGHT: audit record ──
        mcp_firewall_client.postflight_audit(
            tool_name, org_id=str(org.id), user_id=str(actor_user_id or ""),
            decision="allow", success=True, latency_ms=latency_ms,
            server_name=resolved_server.name if resolved_server else "",
        )
        _record_event(
            org=org, request=request, tool_name=tool_name,
            decision="allow", request_id=request_id, latency_ms=latency_ms,
            server_name=resolved_server.name if resolved_server else "",
            server_slug=resolved_server.server_slug if resolved_server else "",
            policy_ids=eval_result.matched_policy_ids,
            metadata={
                "matched_policy_codes": list(eval_result.matched_policy_codes or []),
                "matched_rule_names": list(eval_result.matched_rule_names or []),
                "redacted_field_names": redacted_field_names,
                "actor_agent_id": agent_id,
                "actor_roles": actor_roles,
            },
        )

        logger.info(
            "mcp_connector.tool.called org_id=%s user_id=%s agent=%s tool=%s success=true latency_ms=%s redacted_fields=%s",
            org.id, actor_user_id, agent_id, tool_name, latency_ms, redacted_field_names,
        )
        return Response({"result": result, "request_id": request_id, "decision": "allow"})


# ── Helper: Record structured MCP event ──────────────────────────────


def _record_event(
    org,
    request,
    tool_name: str,
    decision: str,
    reason: str = "",
    policy_ids: list | None = None,
    request_id: str = "",
    latency_ms: int = 0,
    server_name: str = "",
    server_slug: str = "",
    metadata: dict | None = None,
    compliance_tags: list | None = None,
    presidio_findings: list | None = None,
):
    """Write a structured MCPEvent record.

    INVARIANT 3 (Tenant Isolation): Events without org context are
    recorded with decision='error' so observability is never silently
    un-scoped.
    INVARIANT 5 (Observability Completeness): Missing actor or server
    context is flagged in metadata so partial events are visible.
    """
    try:
        actor_user_id, actor_username = _request_actor(request)
        ev_metadata = dict(metadata or {})

        # ── Invariant 3: reject org-less events ──
        if org is None:
            logger.error(
                "INVARIANT-3 VIOLATION: Attempted to record MCP event "
                "without organization context (tool=%s, decision=%s)",
                tool_name, decision,
            )
            ev_metadata["invariant_violation"] = "missing_org"
            decision = "error"

        # ── Invariant 5: flag incomplete observability fields ──
        missing = []
        if not actor_user_id:
            missing.append("user_id")
        if not actor_username:
            missing.append("username")
        if not server_slug:
            missing.append("server_slug")
        if not tool_name:
            missing.append("tool_name")
        if missing:
            ev_metadata["incomplete_fields"] = missing
            logger.warning(
                "INVARIANT-5: MCP event has missing observability fields: %s "
                "(tool=%s, server=%s)", missing, tool_name, server_slug,
            )

        MCPEvent.objects.create(
            organization=org,
            user_id=actor_user_id,
            username=actor_username,
            server_slug=server_slug,
            server_name=server_name,
            tool_name=tool_name,
            decision=decision,
            policy_ids=policy_ids or [],
            policy_reason=reason,
            latency_ms=latency_ms,
            request_id=request_id,
            metadata=ev_metadata,
            compliance_tags=compliance_tags or [],
            presidio_findings=presidio_findings or [],
        )

        # ── Mirror to EnforcementEvent so AI Mesh Firewall dashboard
        #    (graphs, OWASP stats, "Recent MCP and context evidence") shows
        #    MCP traffic. The Module 1.4 page filters EnforcementEvent by
        #    metadata.source == "mcp_scan", so we always tag it that way.
        if org is not None:
            try:
                from policy.models import EnforcementEvent as _EnforcementEvent
                from policy.models import Policy as _Policy, Rule as _Rule

                # Capture request transport/client info for LogDetail panel.
                _src_ip = ""
                _ua = ""
                if request is not None and getattr(request, "META", None):
                    _src_ip = (
                        request.META.get("HTTP_X_FORWARDED_FOR", "").split(",")[0].strip()
                        or request.META.get("REMOTE_ADDR", "")
                        or ""
                    )
                    _ua = request.META.get("HTTP_USER_AGENT", "") or ""

                _status_code_map = {
                    "block": 403,
                    "redact": 200,
                    "monitor": 200,
                    "allow": 200,
                    "error": 502,
                }
                _status_code = _status_code_map.get(decision, 200)

                # Map MCPEvent decision → EnforcementEvent action.
                # 'allow' is recorded as 'monitor' so success traffic still
                # appears on dashboard timelines without being mis-tagged
                # as a block. 'error' is also recorded as 'monitor'.
                _action_map = {
                    "block": "block",
                    "redact": "redact",
                    "monitor": "monitor",
                    "allow": "monitor",
                    "error": "monitor",
                }
                _action = _action_map.get(decision, "monitor")

                _risk_map = {
                    "block": 85,
                    "redact": 55,
                    "monitor": 25,
                    "allow": 10,
                    "error": 40,
                }
                _risk = _risk_map.get(decision, 10)

                # Best-effort threat category from reason / decision.
                _reason = (reason or "").lower()
                if decision == "block":
                    if "schema" in _reason:
                        _threat_category = "MCP Schema Violation"
                        _owasp = "MCP08"
                    elif "tool_disabled" in _reason:
                        _threat_category = "MCP Tool Disabled"
                        _owasp = "MCP01"
                    elif "policy" in _reason or policy_ids:
                        _threat_category = "MCP Policy Violation"
                        _owasp = "MCP02"
                    else:
                        _threat_category = "MCP Tool Block"
                        _owasp = "MCP01"
                elif decision == "redact":
                    _threat_category = "MCP Data Redaction"
                    _owasp = "MCP06"
                elif decision == "error":
                    _threat_category = "MCP Tool Error"
                    _owasp = "MCP01"
                else:
                    _threat_category = "MCP Tool Call"
                    _owasp = "MCP01"

                # Resolve a policy + rule FK for richer dashboard rows.
                _policy_obj = None
                _rule_obj = None
                if policy_ids:
                    try:
                        _policy_obj = _Policy.objects.filter(pk=policy_ids[0]).first()
                    except Exception:
                        _policy_obj = None

                _ef_metadata = {
                    "source": "mcp_scan",
                    "threat_category": _threat_category,
                    "owasp_code": _owasp,
                    "decision": decision,
                    "reason": reason or "",
                    "tool_name": tool_name,
                    "tools_invoked": [tool_name] if tool_name else [],
                    "data_accessed": [server_slug] if server_slug else [],
                    "server_slug": server_slug,
                    "server_name": server_name,
                    "request_id": request_id,
                    "pipeline_request_id": request_id,
                    "latency_ms": latency_ms,
                    "policy_ids": policy_ids or [],
                    "security_risk_score": _risk,
                    "actor_username": actor_username or "",
                    "incomplete_fields": ev_metadata.get("incomplete_fields", []),
                    # ── Detail-panel fields (Request / Response / Security / Metadata)
                    "method": "POST",
                    "endpoint": f"/api/mcp-connector/tools/call/ ({tool_name})",
                    "source_ip": _src_ip,
                    "user_agent": _ua,
                    "model": tool_name,
                    "status_code": _status_code,
                    "pipeline_stage": "mcp_tool_call",
                    "intent": f"mcp:{tool_name}",
                    "event_type": "mcp_tool_call",
                    "compliance_tags": ["OWASP-MCP", _owasp],
                    "rate_limit_status": "n/a",
                    "auth_status": "authenticated" if actor_user_id else "anonymous",
                    "input_validation": "schema_ok" if decision != "block" or "schema" not in (reason or "").lower() else "schema_failed",
                    "content_safety": "violation" if decision == "block" else "ok",
                    "pii_detected": decision == "redact",
                    "prompt_injection_detected": "MCP_TOOL_INJECT" in str(ev_metadata.get("matched_policy_codes") or []),
                    "jailbreak_detected": "MCP_EVASION" in str(ev_metadata.get("matched_policy_codes") or []),
                    "policy_violations": list(ev_metadata.get("matched_policy_codes") or []),
                    "extra": {
                        "matched_patterns": list(ev_metadata.get("matched_rule_names") or []),
                        "request_id": request_id,
                    },
                }
                # Carry through any extra context the caller passed
                # (e.g. matched_policy_codes, matched_rule_names).
                for _k, _v in (metadata or {}).items():
                    _ef_metadata.setdefault(_k, _v)

                _EnforcementEvent.objects.create(
                    organization=org,
                    policy=_policy_obj,
                    rule=_rule_obj,
                    action=_action,
                    user_id=actor_user_id,
                    metadata=_ef_metadata,
                )
            except Exception as _ef_exc:
                logger.warning(
                    "Failed to mirror MCP event to EnforcementEvent: %s",
                    _ef_exc,
                )
    except Exception as exc:
        logger.warning("Failed to record MCP event: %s", exc)


# ── Tool Controls (per-server per-tool enable/disable) ───────────────


class MCPServerToolListView(APIView):
    """List or sync tool registrations for a specific MCP server."""

    permission_classes = [IsAuthenticatedOrGatewayInternal]

    def get(self, request, pk):
        """List tool registrations with their enable/disable state."""
        registrations, org = _org_scoped_servers_queryset(request)
        if org is None:
            return Response([], status=status.HTTP_200_OK)
        try:
            server = registrations.get(pk=pk)
        except MCPServerRegistration.DoesNotExist:
            return Response({"error": "Not found"}, status=status.HTTP_404_NOT_FOUND)

        tools = MCPToolRegistration.objects.filter(server=server)
        return Response(MCPToolRegistrationSerializer(tools, many=True).data)

    def post(self, request, pk):
        """Sync tools by asking the gateway to discover them on the upstream server.

        DECISION-D Phase 0: a single discovery path — the gateway's
        /v1/mcp/internal/discover-tools route handles every transport
        (stdio, websocket, streamable-http, sse) uniformly.
        """
        registrations, org = _org_scoped_servers_queryset(request)
        if org is None:
            return Response({"error": "No org context"}, status=status.HTTP_400_BAD_REQUEST)
        try:
            server = registrations.get(pk=pk)
        except MCPServerRegistration.DoesNotExist:
            return Response({"error": "Not found"}, status=status.HTTP_404_NOT_FOUND)

        server_tool_names = set()

        result = _resync_server_tools(server, org)
        sync_error = result["error"]
        pruned = result["pruned"]

        tools = MCPToolRegistration.objects.filter(server=server)
        return Response({
            "synced": result["synced"],
            "pruned": pruned,
            "error": sync_error,
            "connection_status": server.connection_status,
            "tools": MCPToolRegistrationSerializer(tools, many=True).data,
        })


class MCPToolControlView(APIView):
    """Enable/disable a tool or update its sensitivity."""

    permission_classes = [IsAuthenticatedOrGatewayInternal]

    def patch(self, request, pk, tool_name):
        registrations, org = _org_scoped_servers_queryset(request)
        if org is None:
            return Response({"error": "No org context"}, status=status.HTTP_400_BAD_REQUEST)
        try:
            server = registrations.get(pk=pk)
        except MCPServerRegistration.DoesNotExist:
            return Response({"error": "Server not found"}, status=status.HTTP_404_NOT_FOUND)

        try:
            tool = MCPToolRegistration.objects.get(server=server, tool_name=tool_name)
        except MCPToolRegistration.DoesNotExist:
            return Response({"error": "Tool not found"}, status=status.HTTP_404_NOT_FOUND)

        if "enabled" in request.data:
            tool.enabled = bool(request.data["enabled"])
        if "sensitivity" in request.data:
            allowed = [c[0] for c in MCPToolRegistration.SENSITIVITY_CHOICES]
            if request.data["sensitivity"] in allowed:
                tool.sensitivity = request.data["sensitivity"]
        if "presidio_action" in request.data:
            allowed_actions = {"inherit", "tag", "redact", "block"}
            if request.data["presidio_action"] in allowed_actions:
                tool.presidio_action = request.data["presidio_action"]
        tool.save()
        return Response(MCPToolRegistrationSerializer(tool).data)


# ── Gateway-internal: enable/disable enforcement helpers ─────────────


class MCPGatewayEnabledToolsView(APIView):
    """Gateway-internal lookup of enabled/disabled tools for a server.

    Used by the gateway proxy to enforce per-tool enable/disable for
    stdio and websocket transports (which bypass the normal HTTP
    backend tool-call path).

    GET /api/mcp-connector/internal/enabled-tools/?server_slug=<slug>
        Returns: {
            "server_slug": "<slug>",
            "known_tools": ["a","b","c"],   # all registered tool names
            "enabled_tools": ["a","b"],     # subset where enabled=True
            "disabled_tools": ["c"]
        }
    """

    permission_classes = [IsAuthenticatedOrGatewayInternal]

    def get(self, request):
        org = _request_org(request)
        if org is None:
            return Response(
                {"error": "No organization context."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        server_slug = (
            request.query_params.get("server_slug")
            or request.headers.get("X-Server-Slug")
            or ""
        ).strip().lower()
        if not server_slug:
            return Response(
                {"error": "server_slug required"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        try:
            server = MCPServerRegistration.objects.get(
                organization=org, server_slug=server_slug,
            )
        except MCPServerRegistration.DoesNotExist:
            return Response(
                {"error": "Server not found", "server_slug": server_slug},
                status=status.HTTP_404_NOT_FOUND,
            )

        regs = list(MCPToolRegistration.objects.filter(server=server).values(
            "tool_name", "enabled", "presidio_action",
        ))
        known = [r["tool_name"] for r in regs]
        enabled = [r["tool_name"] for r in regs if r["enabled"]]
        disabled = [r["tool_name"] for r in regs if not r["enabled"]]
        # DECISION-D Phase 1: surface per-tool Presidio action overrides plus the
        # server-level fallback so the gateway can decide per call without an
        # extra round-trip. Tools with "inherit" are omitted from tool_actions.
        tool_actions = {
            r["tool_name"]: r["presidio_action"]
            for r in regs
            if r.get("presidio_action") and r["presidio_action"] != "inherit"
        }
        return Response({
            "server_slug": server_slug,
            "server_name": server.name,
            "known_tools": known,
            "enabled_tools": enabled,
            "disabled_tools": disabled,
            "default_presidio_action": server.default_presidio_action,
            "tool_presidio_actions": tool_actions,
        })


class MCPGatewayRecordEventView(APIView):
    """Gateway-internal endpoint to record an MCPEvent.

    Used by the gateway when it short-circuits a tool call (e.g. blocks
    a disabled tool for stdio/websocket transports) so audit/observability
    stays consistent across transports.

    POST /api/mcp-connector/internal/record-event/
        Body: {
            "server_slug": "<slug>",
            "tool_name": "<name>",
            "decision": "allow|block|redact|error",
            "reason": "<text>",
            "request_id": "<id>",
            "latency_ms": <int>,
            "metadata": {...}
        }
    """

    permission_classes = [IsAuthenticatedOrGatewayInternal]

    def post(self, request):
        org = _request_org(request)
        if org is None:
            return Response(
                {"error": "No organization context."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        data = request.data or {}
        decision = (data.get("decision") or "").strip().lower()
        if decision not in ("allow", "block", "redact", "error"):
            return Response(
                {"error": "invalid decision"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        server_slug = (data.get("server_slug") or "").strip().lower()
        server_name = data.get("server_name", "")
        if server_slug and not server_name:
            srv = MCPServerRegistration.objects.filter(
                organization=org, server_slug=server_slug,
            ).first()
            if srv:
                server_name = srv.name

        _record_event(
            org=org,
            request=request,
            tool_name=data.get("tool_name", ""),
            decision=decision,
            reason=data.get("reason", ""),
            policy_ids=data.get("policy_ids") or [],
            request_id=data.get("request_id", ""),
            latency_ms=int(data.get("latency_ms") or 0),
            server_name=server_name,
            server_slug=server_slug,
            metadata=data.get("metadata") or {},
            compliance_tags=data.get("compliance_tags") or [],
            presidio_findings=data.get("presidio_findings") or [],
        )
        return Response({"recorded": True}, status=status.HTTP_201_CREATED)


class MCPGatewayNeedsReauthView(APIView):
    """Gateway-internal endpoint to flag an org's MCP server as needing re-auth.

    Closes the Flow-2 backprop gap: for mcp-remote (stdio) servers the gateway
    holds the OAuth token and the control plane never learned about refresh
    failures, so an expired token surfaced only as an opaque upstream
    ``invalid_token``. The gateway now calls this when it cannot inject a
    usable token, letting control set ``needs_reauth`` and surface an
    actionable per-org "re-authenticate <server>" signal.

    POST /api/mcp-connector/internal/needs-reauth/
        Body: {"org_slug": "<slug>", "server_slug": "<slug>", "reason": "<text>"}
    """

    permission_classes = [IsAuthenticatedOrGatewayInternal]

    def post(self, request):
        org = _request_org(request)
        if org is None:
            return Response(
                {"error": "No organization context."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        data = request.data or {}
        server_slug = (data.get("server_slug") or "").strip().lower()
        if not server_slug:
            return Response(
                {"error": "server_slug required"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        server = MCPServerRegistration.objects.filter(
            organization=org, server_slug=server_slug,
        ).first()
        if server is None:
            return Response(
                {"error": "server not found"},
                status=status.HTTP_404_NOT_FOUND,
            )
        reason = (data.get("reason") or "").strip() or (
            "OAuth token expired and refresh failed — re-authenticate this server."
        )
        _mark_needs_reauth(server, reason)
        return Response({"needs_reauth": True}, status=status.HTTP_200_OK)


# ── Org Gateway Key Provisioning ─────────────────────────────────────


class OrgGatewayKeyView(APIView):
    """Ensure the organization has a default MCP gateway API key.

    GET  — returns key metadata (prefix, name, active status).
    POST — creates one if none exists; returns plaintext key on first creation.
    """

    permission_classes = [IsAuthenticatedOrGatewayInternal]

    def _resolve(self, request):
        org = _request_org(request)
        if org is None:
            return None, None, Response(
                {"error": "No organization context for this user."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        actor = request.user if getattr(request.user, "is_authenticated", False) else None
        if actor is None:
            return None, None, Response(
                {"error": "Authentication required."},
                status=status.HTTP_401_UNAUTHORIZED,
            )
        return org, actor, None

    def get(self, request):
        org, actor, err = self._resolve(request)
        if err:
            return err

        from core.models import GatewayAPIKey

        project_id = f"mcp-default-{org.slug}"
        key = GatewayAPIKey.objects.filter(
            organization=org,
            project_id=project_id,
            is_active=True,
        ).first()
        if key is None:
            # Also check for ANY active key in the org
            key = GatewayAPIKey.objects.filter(
                organization=org,
                is_active=True,
            ).first()

        if key:
            return Response({
                "has_gateway_key": True,
                "prefix": key.prefix,
                "name": key.name,
                "is_active": key.is_active,
                "project_id": key.project_id,
            })
        return Response({"has_gateway_key": False})

    def post(self, request):
        org, actor, err = self._resolve(request)
        if err:
            return err

        from core.models import GatewayAPIKey

        key_instance, raw_key = GatewayAPIKey.ensure_default_for_org(org, actor)
        data = {
            "has_gateway_key": True,
            "prefix": key_instance.prefix,
            "name": key_instance.name,
            "is_active": key_instance.is_active,
            "project_id": key_instance.project_id,
            "created": raw_key is not None,
        }
        if raw_key:
            data["key"] = raw_key
            data["warning"] = (
                "Store this key securely. It will not be shown again."
            )
            logger.info(
                "mcp_connector.gateway_key.provisioned org_id=%s prefix=%s",
                org.id,
                key_instance.prefix,
            )
        return Response(data, status=status.HTTP_201_CREATED if raw_key else status.HTTP_200_OK)


# ── Observability Events ─────────────────────────────────────────────


class MCPEventListView(APIView):
    """List structured MCP events with optional filters."""

    permission_classes = [IsAuthenticatedOrGatewayInternal]

    def get(self, request):
        _regs, org = _org_scoped_servers_queryset(request)
        if org is None:
            return Response([])

        events = MCPEvent.objects.filter(organization=org)

        # Time-window filter (hours). Omit or 0 = all-time.
        try:
            hours = int(request.query_params.get("hours", 0))
        except (TypeError, ValueError):
            hours = 0
        if hours > 0:
            from datetime import timedelta
            from django.utils import timezone as _tz
            events = events.filter(timestamp__gte=_tz.now() - timedelta(hours=hours))

        # Filters
        decision = request.query_params.get("decision")
        if decision:
            events = events.filter(decision=decision)
        tool = request.query_params.get("tool")
        if tool:
            events = events.filter(tool_name=tool)
        server = request.query_params.get("server")
        if server:
            events = events.filter(server_slug=server)
        user_id = request.query_params.get("user_id")
        if user_id:
            events = events.filter(user_id=user_id)

        # Limit
        limit = min(int(request.query_params.get("limit", 100)), 500)
        events = events[:limit]

        return Response(MCPEventSerializer(events, many=True).data)


class MCPEventSummaryView(APIView):
    """Aggregated summary of MCP events for dashboard cards."""

    permission_classes = [IsAuthenticatedOrGatewayInternal]

    def get(self, request):
        _regs, org = _org_scoped_servers_queryset(request)
        if org is None:
            return Response({})

        events = MCPEvent.objects.filter(organization=org)

        # Time-window filter (hours). Omit or 0 = all-time.
        try:
            hours = int(request.query_params.get("hours", 0))
        except (TypeError, ValueError):
            hours = 0
        if hours > 0:
            from datetime import timedelta
            from django.utils import timezone as _tz
            events = events.filter(timestamp__gte=_tz.now() - timedelta(hours=hours))

        # Decision counts
        decision_counts = dict(
            events.values_list("decision").annotate(count=Count("id")).values_list("decision", "count")
        )

        # Top tools
        top_tools = list(
            events.values("tool_name")
            .annotate(count=Count("id"))
            .order_by("-count")[:10]
        )

        # Top users
        top_users = list(
            events.filter(username__gt="")
            .values("username")
            .annotate(count=Count("id"))
            .order_by("-count")[:10]
        )

        # Recent events
        recent = MCPEventSerializer(events[:5], many=True).data

        return Response({
            "total": events.count(),
            "decisions": decision_counts,
            "top_tools": top_tools,
            "top_users": top_users,
            "recent": recent,
        })


# ── OAuth 2.1 authorization (Phase C) ────────────────────────────────


def _oauth_redirect_uri() -> str:
    """Browser-reachable callback URI registered with the authorization server.

    Must be a localhost or HTTPS URL per the MCP/OAuth spec. Overridable via
    env so a production deployment can point at its public control host.
    """
    return os.environ.get(
        "MCP_OAUTH_REDIRECT_URI",
        "http://localhost:8100/api/mcp-connector/oauth/callback",
    )


def _oauth_frontend_return_url(ok: bool, server_name: str = "", error: str = "") -> str:
    """Where the callback HTML bounces the browser back to after token exchange."""
    base = os.environ.get("MCP_OAUTH_FRONTEND_URL", "http://localhost:8180/?tab=firewall-1-4")
    return base


class MCPServerOAuthStartView(APIView):
    """Begin the OAuth 2.1 authorization-code (PKCE) flow for a server.

    POST /servers/<pk>/oauth/authorize/ → run discovery + dynamic client
    registration, generate PKCE + state, persist the transient flow state, and
    return the authorization URL for the operator to open in their browser.
    """

    permission_classes = [IsAuthenticatedOrGatewayInternal]

    def post(self, request, pk):
        from . import oauth as oauth_mod

        registrations, org = _org_scoped_servers_queryset(request)
        if org is None:
            return Response({"error": "No org context"}, status=status.HTTP_400_BAD_REQUEST)
        try:
            server = registrations.get(pk=pk)
        except MCPServerRegistration.DoesNotExist:
            return Response({"error": "Not found"}, status=status.HTTP_404_NOT_FOUND)

        if not server.url:
            return Response(
                {"error": "Server has no URL; OAuth is only for HTTP transports."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        redirect_uri = _oauth_redirect_uri()
        try:
            meta = oauth_mod.discover(server.url)
        except oauth_mod.OAuthDiscoveryError as exc:
            return Response({"error": str(exc)}, status=status.HTTP_502_BAD_GATEWAY)
        except Exception as exc:  # noqa: BLE001
            logger.warning("OAuth discovery error for %s: %s", server.server_slug, exc)
            return Response({"error": f"OAuth discovery failed: {exc}"}, status=status.HTTP_502_BAD_GATEWAY)

        # Reuse an existing registered client_id when present (idempotent
        # re-auth); otherwise register a fresh public client via DCR.
        client_id = server.oauth_client_id
        client_secret = server.oauth_client_secret
        if not client_id:
            if not meta.get("registration_endpoint"):
                return Response(
                    {
                        "error": (
                            "Server does not advertise dynamic client registration. "
                            "Provide a client_id/secret manually."
                        )
                    },
                    status=status.HTTP_502_BAD_GATEWAY,
                )
            try:
                reg = oauth_mod.register_client(
                    meta["registration_endpoint"],
                    redirect_uri,
                    client_name=f"AI Mesh Firewall ({server.name})",
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning("DCR failed for %s: %s", server.server_slug, exc)
                return Response({"error": f"Client registration failed: {exc}"}, status=status.HTTP_502_BAD_GATEWAY)
            client_id = reg.get("client_id", "")
            client_secret = reg.get("client_secret", "") or ""
            if not client_id:
                return Response({"error": "Registration returned no client_id."}, status=status.HTTP_502_BAD_GATEWAY)

        verifier, challenge = oauth_mod.generate_pkce()
        state = oauth_mod.generate_state()
        # Prefer the server's configured scope; else the AS-advertised scopes.
        scope = server.oauth_scope or " ".join(meta.get("scopes_supported") or [])

        server.oauth_authorization_endpoint = meta["authorization_endpoint"]
        server.oauth_token_endpoint = meta["token_endpoint"]
        server.oauth_registration_endpoint = meta.get("registration_endpoint", "")
        server.oauth_client_id = client_id
        server.oauth_client_secret = client_secret
        server.oauth_scope = scope
        server.oauth_resource = meta["resource"]
        server.oauth_code_verifier = verifier
        server.oauth_state = state
        server.auth_type = "oauth"
        server.save(update_fields=[
            "oauth_authorization_endpoint",
            "oauth_token_endpoint",
            "oauth_registration_endpoint",
            "oauth_client_id",
            "oauth_client_secret",
            "oauth_scope",
            "oauth_resource",
            "oauth_code_verifier",
            "oauth_state",
            "auth_type",
            "updated_at",
        ])

        authorize_url = oauth_mod.build_authorize_url(
            meta["authorization_endpoint"],
            client_id,
            redirect_uri,
            challenge,
            state,
            scope,
            meta["resource"],
        )
        return Response({"authorize_url": authorize_url, "resource": meta["resource"]})


class MCPOAuthCallbackView(APIView):
    """OAuth redirect target. The browser lands here after user consent.

    GET /oauth/callback/?code=...&state=... → match the server by ``state``,
    exchange the code for tokens, store them (encrypted), clear the transient
    PKCE/state, and bounce the browser back to the frontend.

    Public endpoint (no JWT): the authorization server redirects the browser
    here with no Authorization header. CSRF protection is provided by the
    high-entropy, single-use ``state`` value.
    """

    permission_classes = [AllowAny]
    authentication_classes: list = []

    def _html(self, ok: bool, message: str, server_name: str = "") -> HttpResponse:
        return_url = _oauth_frontend_return_url(ok, server_name, message)
        status_word = "succeeded" if ok else "failed"
        color = "#16a34a" if ok else "#dc2626"
        body = f"""<!doctype html><html><head><meta charset="utf-8">
<title>MCP OAuth {status_word}</title>
<meta http-equiv="refresh" content="3;url={return_url}">
<style>body{{font-family:system-ui,sans-serif;background:#0b1020;color:#e5e7eb;
display:flex;align-items:center;justify-content:center;height:100vh;margin:0}}
.card{{background:#111827;padding:32px 40px;border-radius:12px;max-width:520px;
box-shadow:0 10px 40px rgba(0,0,0,.4);border:1px solid #1f2937}}
h1{{color:{color};margin:0 0 12px;font-size:20px}}
p{{margin:6px 0;line-height:1.5}} a{{color:#60a5fa}}</style></head>
<body><div class="card"><h1>Authorization {status_word}</h1>
<p>{message}</p>
<p>Returning to the dashboard… <a href="{return_url}">click here</a> if you are not redirected.</p>
</div></body></html>"""
        return HttpResponse(body, content_type="text/html")

    def get(self, request):
        from . import oauth as oauth_mod

        error = request.GET.get("error")
        error_desc = request.GET.get("error_description", "")
        code = request.GET.get("code", "")
        state = request.GET.get("state", "")

        if error:
            return self._html(False, f"Authorization server returned: {error} {error_desc}".strip())
        if not code or not state:
            return self._html(False, "Missing authorization code or state in callback.")

        try:
            server = MCPServerRegistration.objects.get(oauth_state=state)
        except MCPServerRegistration.DoesNotExist:
            return self._html(False, "Unknown or expired authorization state (possible CSRF).")
        except MCPServerRegistration.MultipleObjectsReturned:
            return self._html(False, "Ambiguous authorization state.")

        redirect_uri = _oauth_redirect_uri()
        try:
            tok = oauth_mod.exchange_code(
                server.oauth_token_endpoint,
                code,
                redirect_uri,
                server.oauth_client_id,
                server.oauth_client_secret,
                server.oauth_code_verifier,
                server.oauth_resource,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("OAuth code exchange failed for %s: %s", server.server_slug, exc)
            # Clear single-use state even on failure so it can't be replayed.
            server.oauth_state = ""
            server.oauth_code_verifier = ""
            server.save(update_fields=["oauth_state", "oauth_code_verifier", "updated_at"])
            return self._html(False, f"Token exchange failed: {exc}", server.name)

        _store_oauth_tokens(server, tok)
        # Clear transient single-use flow state.
        server.oauth_state = ""
        server.oauth_code_verifier = ""
        server.save(update_fields=["oauth_state", "oauth_code_verifier", "updated_at"])

        return self._html(
            True,
            f"“{server.name}” is now authorized. You can sync its tools.",
            server.name,
        )

