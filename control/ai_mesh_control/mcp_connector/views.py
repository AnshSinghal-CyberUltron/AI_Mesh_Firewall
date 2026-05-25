"""REST API views for MCP Connector.

Provides endpoints for managing MCP servers (via ContextForge),
guardrail profiles (via Secure-MCP-Gateway), tool discovery, tool controls,
MCP-Firewall pre/post flight enforcement, and structured observability.
"""

import json
import logging
import os
import secrets
import time
import uuid as uuid_mod
from functools import lru_cache

import jsonschema
import requests
from auth.utils import get_request_organization
from django.conf import settings
from django.db import IntegrityError
from django.db.models import Count, Q
from django.utils import timezone
from rest_framework import status
from rest_framework.permissions import BasePermission
from rest_framework.response import Response
from rest_framework.views import APIView

from . import contextforge_client, mcp_firewall_client, secure_gateway_client
from .models import GuardrailProfile, MCPEvent, MCPServerRegistration, MCPToolRegistration
from .serializers import (
    GuardrailProfileSerializer,
    MCPEventSerializer,
    MCPServerCreateSerializer,
    MCPServerRegistrationSerializer,
    MCPToolRegistrationSerializer,
)

logger = logging.getLogger(__name__)


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


def _discover_tools_via_gateway(server, org) -> list[dict]:
    """Discover tools from an MCP server via the gateway's internal endpoint.

    Works for stdio, websocket, and streamable-http servers. The gateway
    routes the JSON-RPC tools/list call to the appropriate adapter or
    directly to the upstream MCP server.
    """
    gateway_url = (
        (getattr(settings, "GATEWAY_URL", "") or "").strip().rstrip("/")
        or os.environ.get("GATEWAY_URL", "").strip().rstrip("/")
        or "http://gateway:8300"
    )
    internal_key = _gateway_internal_secret()
    if not internal_key:
        logger.warning("Cannot discover tools via gateway: no GATEWAY_INTERNAL_API_KEY configured")
        return []

    payload = {
        "org_slug": org.slug,
        "server_slug": server.server_slug,
    }

    # Pass auth credentials for upstream HTTP servers
    if server.transport in ("streamable-http", "sse") and hasattr(server, "auth_type"):
        auth_type = getattr(server, "auth_type", "none") or "none"
        if auth_type != "none":
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
            timeout=45,
        )
        if resp.status_code != 200:
            logger.warning(
                "Gateway discover-tools returned HTTP %s for %s/%s: %s",
                resp.status_code, org.slug, server.server_slug,
                resp.text[:500],
            )
            return []

        data = resp.json()
        # JSON-RPC response: {"jsonrpc":"2.0","id":1,"result":{"tools":[...]}}
        result = data.get("result", {})
        tools = result.get("tools", [])
        if isinstance(tools, list):
            logger.info(
                "Gateway discover-tools found %d tools for %s/%s",
                len(tools), org.slug, server.server_slug,
            )
            return tools
        return []
    except Exception as exc:
        logger.warning(
            "Gateway discover-tools failed for %s/%s: %s",
            org.slug, server.server_slug, exc,
        )
        return []


def _call_tool_via_gateway(server, org, tool_name: str, arguments: dict) -> dict:
    """Execute a tool through the gateway's internal MCP route.

    This is used for servers that do not execute through ContextForge
    discovery/execution, such as stdio/websocket registrations and any
    gateway-managed MCP server without a ContextForge server id.
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
    }

    if server.transport in ("streamable-http", "sse") and hasattr(server, "auth_type"):
        auth_type = getattr(server, "auth_type", "none") or "none"
        if auth_type != "none":
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
    """Health status of ContextForge, Secure-MCP-Gateway, and MCP-Firewall."""

    permission_classes = [IsAuthenticatedOrGatewayInternal]

    _BUILTIN_DETECTORS = [
        "pii_redaction", "injection_attack", "sensitive_data",
        "profanity", "topic_restriction",
        "policy_violation", "pii_leakage", "data_exfiltration",
    ]
    _ENKRYPT_ONLY_DETECTORS = ["hallucination", "toxicity"]

    def get(self, request):
        enkrypt_configured = secure_gateway_client.is_configured()
        return Response(
            {
                "contextforge": contextforge_client.health(),
                "secure_mcp_gateway": secure_gateway_client.health(),
                "mcp_firewall": mcp_firewall_client.health(),
                "enkrypt_configured": enkrypt_configured,
                "builtin_detectors": self._BUILTIN_DETECTORS,
                "enkrypt_only_detectors": self._ENKRYPT_ONLY_DETECTORS,
            }
        )


# ── MCP Servers (via ContextForge) ───────────────────────────────────


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

        # stdio / websocket — register locally only (no ContextForge)
        if transport in ("stdio", "websocket"):
            try:
                registration, created = MCPServerRegistration.objects.get_or_create(
                    organization=org,
                    name=local_data["name"],
                    defaults={
                        **{k: v for k, v in local_data.items() if k != "name"},
                        "contextforge_server_id": "",
                    },
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
                "mcp_connector.server.registered_local org_id=%s user_id=%s server=%s transport=%s",
                org.id,
                request.user.id,
                registration.name,
                transport,
            )
        else:
            # Register with ContextForge for streamable-http / sse
            cf_payload = {
                **MCPServerCreateSerializer.contextforge_data(serializer.validated_data),
            }
            try:
                cf_result = contextforge_client.register_server(cf_payload)
            except ValueError as exc:
                logger.warning("ContextForge payload validation failed: %s", exc)
                return Response({"error": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
            except requests.RequestException as exc:
                response = getattr(exc, "response", None)
                if response is not None and response.status_code == 409:
                    existing = contextforge_client.resolve_existing_server(cf_payload)
                    if existing:
                        cf_result = existing
                        logger.info(
                            "mcp_connector.server.reused org_id=%s user_id=%s server=%s contextforge_id=%s",
                            org.id,
                            request.user.id if getattr(request, "user", None) else None,
                            serializer.validated_data.get("name", ""),
                            existing.get("id", ""),
                        )
                    else:
                        logger.error("ContextForge duplicate conflict but no matching gateway resolved: %s", exc)
                        return Response(
                            {"error": "Failed to register MCP server", "detail": _upstream_error_detail(exc)},
                            status=status.HTTP_502_BAD_GATEWAY,
                        )
                else:
                    logger.error("ContextForge registration failed: %s", exc)
                    return Response(
                        {"error": "Failed to register MCP server", "detail": _upstream_error_detail(exc)},
                        status=status.HTTP_502_BAD_GATEWAY,
                    )

            try:
                registration, created = MCPServerRegistration.objects.get_or_create(
                    organization=org,
                    name=local_data["name"],
                    defaults={
                        **{k: v for k, v in local_data.items() if k != "name"},
                        "contextforge_server_id": cf_result.get("id", cf_result.get("server_id", "")),
                    },
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
                "mcp_connector.server.registered org_id=%s user_id=%s server=%s contextforge_id=%s",
                org.id,
                request.user.id,
                registration.name,
                registration.contextforge_server_id,
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

        # Update ContextForge if the server was registered there
        if reg.contextforge_server_id:
            try:
                contextforge_client.update_server(
                    reg.contextforge_server_id,
                    MCPServerCreateSerializer.contextforge_data(serializer.validated_data),
                )
            except ValueError as exc:
                return Response({"error": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
            except requests.RequestException as exc:
                logger.warning("ContextForge update failed: %s", exc)

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

        # Delete from ContextForge
        if reg.contextforge_server_id:
            try:
                contextforge_client.delete_server(reg.contextforge_server_id)
            except requests.RequestException as exc:
                logger.warning("ContextForge delete failed (continuing local delete): %s", exc)

        logger.info(
            "mcp_connector.server.deleted org_id=%s user_id=%s server=%s contextforge_id=%s",
            getattr(org, "id", None),
            request.user.id,
            reg.name,
            reg.contextforge_server_id,
        )
        reg.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


# ── Tool Discovery (via ContextForge) ────────────────────────────────


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
            allowed_server_names = {target_server.name}
            allowed_contextforge_ids = {
                target_server.contextforge_server_id
            } if target_server.contextforge_server_id else set()
        else:
            allowed_server_names = {
                name.strip()
                for name in registrations.values_list("name", flat=True)
                if isinstance(name, str) and name.strip()
            }
            allowed_contextforge_ids = {
                sid.strip()
                for sid in registrations.values_list("contextforge_server_id", flat=True)
                if isinstance(sid, str) and sid.strip()
            }

        try:
            tools = contextforge_client.list_tools()
        except requests.RequestException as exc:
            return Response(
                {"error": "Failed to fetch tools from ContextForge", "detail": _upstream_error_detail(exc)},
                status=status.HTTP_502_BAD_GATEWAY,
            )
        # Strict org-scoped filtering: if the org has no registered servers,
        # return no tools (prevents cross-tenant data leakage).
        if isinstance(tools, list):
            if not allowed_server_names and not allowed_contextforge_ids:
                tools = []
            else:
                filtered_tools = []
                for tool in tools:
                    if not isinstance(tool, dict):
                        continue
                    server_name = (
                        tool.get("server_name")
                        or tool.get("server")
                        or tool.get("gateway_name")
                        or tool.get("gateway")
                    )
                    server_id = (
                        tool.get("server_id")
                        or tool.get("serverId")
                        or tool.get("gateway_id")
                        or tool.get("gatewayId")
                    )
                    if server_name in allowed_server_names or server_id in allowed_contextforge_ids:
                        filtered_tools.append(tool)
                tools = filtered_tools

        # ── Include locally-registered tools for non-ContextForge servers ──
        # stdio/websocket MCP servers are spawned directly by the gateway and
        # never appear in ContextForge. Their tools live in MCPToolRegistration
        # (populated on Sync via gateway introspection). Without this merge,
        # the Tool Discovery tab would only show ContextForge-backed servers
        # (e.g. Context7) and silently hide stdio tools.
        if not isinstance(tools, list):
            tools = []
        seen_keys = {(t.get("server_name") or "", t.get("name") or "") for t in tools if isinstance(t, dict)}
        local_servers_qs = registrations.filter(
            Q(contextforge_server_id__isnull=True) | Q(contextforge_server_id="")
        )
        if server_slug_hint:
            local_servers_qs = local_servers_qs.filter(server_slug=server_slug_hint)
        local_tool_qs = MCPToolRegistration.objects.filter(
            organization=org,
            server__in=local_servers_qs,
        ).select_related("server")
        for tr in local_tool_qs:
            key = (tr.server.name, tr.tool_name)
            if key in seen_keys:
                continue
            seen_keys.add(key)
            tools.append({
                "name": tr.tool_name,
                "description": tr.description or "",
                "inputSchema": tr.input_schema or {},
                "server_name": tr.server.name,
                "server_id": str(tr.server.id),
                "server_slug": tr.server.server_slug,
                "transport": tr.server.transport,
                "enabled": tr.enabled,
                "source": "registration",
            })

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
        from django.db.models import Q

        arg_text = " ".join(str(v) for v in arguments.values()) if arguments else ""
        policy_context = {
            "prompt": f"tool:{tool_name} {arg_text}",
            "response": "",
        }
        # Get policies: org-specific MCP + system-wide MCP (org=None) + server-specific
        # System-wide policies have organization=None and apply to all orgs
        policy_qs = PolicyModel.objects.filter(
            enabled=True,
            policy_domain="mcp",
        ).filter(
            Q(organization=org) | Q(organization__isnull=True)
        ).prefetch_related("rules")
        if resolved_server:
            policy_qs = policy_qs.filter(
                Q(mcp_server__isnull=True) | Q(mcp_server=resolved_server)
            )
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

        # ── EXECUTE: via ContextForge ──
        try:
            use_gateway_execution = bool(
                resolved_server
                and (
                    (resolved_server.transport or "").strip().lower() in {"stdio", "websocket"}
                    or not (resolved_server.contextforge_server_id or "").strip()
                )
            )

            if use_gateway_execution:
                logger.info(
                    "mcp_connector.tool.gateway_execution org_id=%s user_id=%s tool=%s server=%s transport=%s",
                    org.id,
                    actor_user_id,
                    tool_name,
                    resolved_server.server_slug if resolved_server else "",
                    (resolved_server.transport or "").strip().lower() if resolved_server else "",
                )
                result = _call_tool_via_gateway(resolved_server, org, tool_name, arguments)
            else:
                result = contextforge_client.call_tool(
                    tool_name,
                    arguments,
                    server_id=resolved_server.contextforge_server_id if resolved_server else "",
                    server_name=resolved_server.name if resolved_server else "",
                )
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
        )

        logger.info(
            "mcp_connector.tool.called org_id=%s user_id=%s tool=%s success=true latency_ms=%s",
            org.id, actor_user_id, tool_name, latency_ms,
        )
        return Response({"result": result, "request_id": request_id, "decision": "allow"})


# ── Guardrail Profiles ──────────────────────────────────────────────


class GuardrailProfileListCreateView(APIView):
    """List or create guardrail profiles (org-scoped)."""

    permission_classes = [IsAuthenticatedOrGatewayInternal]

    def get(self, request):
        org = _request_org(request)
        if org is None:
            return Response(
                {"error": "No organization context for this user."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        profiles = GuardrailProfile.objects.filter(organization=org)
        return Response(GuardrailProfileSerializer(profiles, many=True).data)

    def post(self, request):
        org = _request_org(request)
        if org is None:
            return Response(
                {"error": "No organization context for this user."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        serializer = GuardrailProfileSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        serializer.save(organization=org)
        return Response(serializer.data, status=status.HTTP_201_CREATED)


class GuardrailProfileDetailView(APIView):
    """Retrieve, update, or delete a guardrail profile (org-scoped)."""

    permission_classes = [IsAuthenticatedOrGatewayInternal]

    def _get_profile(self, request, pk):
        org = _request_org(request)
        if org is None:
            return None, Response(
                {"error": "No organization context for this user."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        try:
            return GuardrailProfile.objects.get(pk=pk, organization=org), None
        except GuardrailProfile.DoesNotExist:
            return None, Response({"error": "Not found"}, status=status.HTTP_404_NOT_FOUND)

    def get(self, request, pk):
        profile, err = self._get_profile(request, pk)
        if err:
            return err
        return Response(GuardrailProfileSerializer(profile).data)

    def put(self, request, pk):
        profile, err = self._get_profile(request, pk)
        if err:
            return err
        serializer = GuardrailProfileSerializer(profile, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data)

    def delete(self, request, pk):
        profile, err = self._get_profile(request, pk)
        if err:
            return err
        profile.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class ApplyGuardrailView(APIView):
    """Apply a guardrail profile to an MCP server in Secure-MCP-Gateway.

    When the Enkrypt gateway is not configured (no API key), the profile
    is saved locally and built-in pattern detectors are used.  External
    scanning via Enkrypt is only attempted when the key is present.
    """

    permission_classes = [IsAuthenticatedOrGatewayInternal]

    # Detectors that work without Enkrypt API (regex / keyword patterns)
    _BUILTIN_DETECTORS = {
        "pii_redaction", "injection_attack", "sensitive_data",
        "profanity", "topic_restriction",
        "policy_violation", "pii_leakage", "data_exfiltration",
    }

    def post(self, request):
        profile_id = request.data.get("profile_id")
        config_id = request.data.get("config_id")
        server_name = request.data.get("server_name")

        if not all([profile_id, config_id, server_name]):
            return Response(
                {"error": "Required: profile_id, config_id, server_name"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            profile = GuardrailProfile.objects.get(pk=profile_id)
        except GuardrailProfile.DoesNotExist:
            return Response({"error": "Profile not found"}, status=status.HTTP_404_NOT_FOUND)

        guardrails_payload = {}
        if profile.input_policy:
            guardrails_payload["input_policy"] = profile.input_policy
        if profile.output_policy:
            guardrails_payload["output_policy"] = profile.output_policy

        if not secure_gateway_client.is_configured():
            return Response({
                "message": "Guardrail profile saved locally. Built-in pattern detectors active. "
                           "Set SECURE_MCP_GATEWAY_ADMIN_KEY to enable Enkrypt AI scanning.",
                "mode": "builtin",
                "builtin_detectors": sorted(self._BUILTIN_DETECTORS),
            })

        try:
            result = secure_gateway_client.update_server_guardrails(config_id, server_name, guardrails_payload)
        except requests.RequestException as exc:
            return Response({
                "warning": "Enkrypt gateway unreachable — falling back to built-in detectors.",
                "mode": "builtin_fallback",
                "detail": str(exc),
                "builtin_detectors": sorted(self._BUILTIN_DETECTORS),
            })

        return Response({"message": "Guardrails applied via Enkrypt", "mode": "enkrypt", "result": result})


# ── Secure MCP Gateway Config Proxy ─────────────────────────────────


class SecureGatewayConfigListView(APIView):
    """Proxy to Secure-MCP-Gateway config listing."""

    permission_classes = [IsAuthenticatedOrGatewayInternal]

    def get(self, request):
        try:
            configs = secure_gateway_client.list_configs()
        except requests.RequestException as exc:
            return Response(
                {"error": "Failed to fetch configs", "detail": str(exc)},
                status=status.HTTP_502_BAD_GATEWAY,
            )
        return Response(configs)

    def post(self, request):
        config_name = request.data.get("config_name")
        if not config_name:
            return Response({"error": "Missing 'config_name'"}, status=status.HTTP_400_BAD_REQUEST)
        try:
            result = secure_gateway_client.create_config(config_name)
        except requests.RequestException as exc:
            return Response(
                {"error": "Failed to create config", "detail": str(exc)},
                status=status.HTTP_502_BAD_GATEWAY,
            )
        return Response(result, status=status.HTTP_201_CREATED)


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
        """Sync tools from ContextForge or via direct gateway discovery."""
        registrations, org = _org_scoped_servers_queryset(request)
        if org is None:
            return Response({"error": "No org context"}, status=status.HTTP_400_BAD_REQUEST)
        try:
            server = registrations.get(pk=pk)
        except MCPServerRegistration.DoesNotExist:
            return Response({"error": "Not found"}, status=status.HTTP_404_NOT_FOUND)

        server_tool_names = set()

        # ── Strategy 1: ContextForge (for servers registered there) ──
        use_contextforge = bool(server.contextforge_server_id)
        if use_contextforge:
            try:
                all_tools = contextforge_client.list_tools()
            except requests.RequestException:
                all_tools = []

            if isinstance(all_tools, list):
                for tool in all_tools:
                    if not isinstance(tool, dict):
                        continue
                    sname = (
                        tool.get("server_name") or tool.get("server")
                        or tool.get("gateway_name") or tool.get("gateway") or ""
                    )
                    sid = (
                        tool.get("server_id") or tool.get("serverId")
                        or tool.get("gateway_id") or tool.get("gatewayId") or ""
                    )
                    if sname == server.name or sid == server.contextforge_server_id:
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

        # ── Strategy 2: Direct gateway discovery (stdio, websocket, or CF fallback) ──
        if not server_tool_names:
            gateway_tools = _discover_tools_via_gateway(server, org)
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

        # Update server metadata
        server.tools_count = len(server_tool_names)
        server.last_sync_at = timezone.now()
        server.connection_status = "connected" if server_tool_names else "failed"
        server.save(update_fields=["tools_count", "last_sync_at", "connection_status", "updated_at"])

        tools = MCPToolRegistration.objects.filter(server=server)
        return Response({
            "synced": len(server_tool_names),
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
            "tool_name", "enabled",
        ))
        known = [r["tool_name"] for r in regs]
        enabled = [r["tool_name"] for r in regs if r["enabled"]]
        disabled = [r["tool_name"] for r in regs if not r["enabled"]]
        return Response({
            "server_slug": server_slug,
            "server_name": server.name,
            "known_tools": known,
            "enabled_tools": enabled,
            "disabled_tools": disabled,
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
        )
        return Response({"recorded": True}, status=status.HTTP_201_CREATED)


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
