from rest_framework import serializers

from ._url_guard import is_safe_outbound_url
from .models import MCPEvent, MCPServerRegistration, MCPScanControl, MCPToolRegistration


ALL_TRANSPORTS = {"streamable-http", "sse", "stdio", "websocket"}


class AuthHeaderPairSerializer(serializers.Serializer):
    key = serializers.CharField(required=True, allow_blank=False)
    value = serializers.CharField(required=True, allow_blank=False)


class MCPServerRegistrationSerializer(serializers.ModelSerializer):
    gateway_endpoint = serializers.ReadOnlyField()
    oauth_authorized = serializers.ReadOnlyField()

    class Meta:
        model = MCPServerRegistration
        fields = [
            "id",
            "name",
            "server_slug",
            "url",
            "transport",
            "command",
            "args",
            "env_vars",
            "description",
            "is_active",
            "is_exposed_to_agents",
            "connection_status",
            "tools_count",
            "last_sync_at",
            "last_health_at",
            "last_health_status",
            "risk_level",
            "gateway_endpoint",
            "default_scan_action",
            # Non-secret auth descriptor + sync diagnostics (secrets such as
            # auth_token/auth_password/auth_header_value are intentionally
            # NOT listed here — they live in write-only inputs only).
            "auth_type",
            "oauth_authorized",
            "oauth_token_expires_at",
            "last_sync_error",
            "last_sync_attempt_at",
            "needs_reauth",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "id",
            "server_slug",
            "connection_status",
            "tools_count",
            "last_sync_at",
            "last_health_at",
            "last_health_status",
            "risk_level",
            "gateway_endpoint",
            "oauth_authorized",
            "oauth_token_expires_at",
            "last_sync_error",
            "last_sync_attempt_at",
            "needs_reauth",
            "created_at",
            "updated_at",
        ]


class MCPServerCreateSerializer(serializers.ModelSerializer):
    AUTH_TYPE_CHOICES = [
        ("none", "None"),
        ("bearer", "Bearer Token"),
        ("basic", "Basic Auth"),
        ("authheaders", "Custom Header(s)"),
        ("query_param", "Query Parameter"),
        ("oauth", "OAuth 2.1"),
    ]

    auth_type = serializers.ChoiceField(
        choices=AUTH_TYPE_CHOICES,
        required=False,
        allow_blank=True,
        default="none",
        write_only=True,
    )
    auth_token = serializers.CharField(required=False, allow_blank=True, write_only=True)
    auth_username = serializers.CharField(required=False, allow_blank=True, write_only=True)
    auth_password = serializers.CharField(required=False, allow_blank=True, write_only=True)
    auth_header_key = serializers.CharField(required=False, allow_blank=True, write_only=True)
    auth_header_value = serializers.CharField(required=False, allow_blank=True, write_only=True)
    auth_headers = AuthHeaderPairSerializer(many=True, required=False, write_only=True)
    auth_query_param_key = serializers.CharField(required=False, allow_blank=True, write_only=True)
    auth_query_param_value = serializers.CharField(required=False, allow_blank=True, write_only=True)

    class Meta:
        model = MCPServerRegistration
        fields = [
            "name",
            "url",
            "transport",
            "command",
            "args",
            "env_vars",
            "description",
            "auth_type",
            "auth_token",
            "auth_username",
            "auth_password",
            "auth_header_key",
            "auth_header_value",
            "auth_headers",
            "auth_query_param_key",
            "auth_query_param_value",
            "default_scan_action",
        ]

    def validate(self, attrs):
        # On partial updates (PATCH) fall back to the instance's existing values
        # so unrelated field edits (e.g. default_scan_action) don't trip the
        # transport/url/command/auth_type guards.
        instance = getattr(self, "instance", None)

        def _val(key, default=""):
            if key in attrs:
                return attrs[key]
            return getattr(instance, key, default) if instance is not None else default

        transport = (_val("transport") or "").strip().lower()

        if transport and transport not in ALL_TRANSPORTS:
            raise serializers.ValidationError(
                {"transport": f"Unsupported transport '{transport}'. Use one of: {', '.join(sorted(ALL_TRANSPORTS))}."}
            )

        # stdio requires command; url is optional
        if transport == "stdio":
            if not _val("command"):
                raise serializers.ValidationError({"command": "command is required for stdio transport."})
        else:
            # websocket, streamable-http, sse all require url
            url_value = _val("url")
            if not url_value:
                raise serializers.ValidationError({"url": "url is required for this transport."})

            # SSRF guard (finding mcp#1): reject internal / loopback /
            # link-local / cloud-metadata targets at the registration boundary.
            # This is best-effort — the gateway/control fetch-time guards remain
            # the authoritative check because DNS can change post-registration
            # (TOCTOU). websocket allows ws/wss in addition to http/https.
            if transport == "websocket":
                allowed_schemes = ("ws", "wss", "http", "https")
            else:
                allowed_schemes = ("http", "https")
            ok, reason = is_safe_outbound_url(url_value, allowed_schemes=allowed_schemes)
            if not ok:
                raise serializers.ValidationError(
                    {"url": f"URL rejected by SSRF guard: {reason}"}
                )

        auth_type = (_val("auth_type", "none") or "none").strip().lower()
        attrs["auth_type"] = auth_type

        # Transport-aware OAuth guard (fixes MCP OAuth bugs #1 dup-UI / #2
        # "Server has no URL"). OAuth 2.1 authorization-code flow is HTTP-only:
        # it runs RFC 9728/8414 discovery + token injection against an HTTP MCP
        # endpoint URL. stdio servers (incl. Linear via `mcp-remote`) authorize
        # upstream INSIDE the gateway sandbox — their auth_type stays "none" and
        # the gateway-side device flow handles OAuth. Persisting auth_type="oauth"
        # on a stdio/websocket (URL-less) row creates an "oauth" server the UI
        # renders a SECOND, broken authorize button for, which 400s with
        # "Server has no URL" when clicked. Reject it at the registration
        # boundary so the invalid state can never exist.
        if auth_type == "oauth" and transport not in ("streamable-http", "sse"):
            raise serializers.ValidationError(
                {
                    "auth_type": (
                        "OAuth 2.1 (authorize via provider) requires an HTTP MCP "
                        "transport (streamable-http or sse) with a URL. For stdio "
                        "servers such as Linear via mcp-remote, upstream OAuth is "
                        "handled automatically by the gateway sandbox — leave the "
                        "auth type as 'none'."
                    )
                }
            )

        if auth_type == "bearer" and not attrs.get("auth_token"):
            raise serializers.ValidationError({"auth_token": "auth_token is required for bearer auth."})
        if auth_type == "basic" and (not attrs.get("auth_username") or not attrs.get("auth_password")):
            raise serializers.ValidationError(
                {"auth_username": "auth_username and auth_password are required for basic auth."}
            )
        if auth_type == "authheaders":
            collected_headers: list[dict[str, str]] = []

            auth_header_key = (attrs.get("auth_header_key") or "").strip()
            auth_header_value = attrs.get("auth_header_value")
            if auth_header_key and auth_header_value not in (None, ""):
                collected_headers.append({"key": auth_header_key, "value": auth_header_value})

            for header in attrs.get("auth_headers") or []:
                key = (header.get("key") or "").strip()
                value = header.get("value")
                if key and value not in (None, ""):
                    collected_headers.append({"key": key, "value": value})

            deduped_headers: list[dict[str, str]] = []
            seen_keys: set[str] = set()
            for header in collected_headers:
                key_norm = header["key"].lower()
                if key_norm in seen_keys:
                    continue
                seen_keys.add(key_norm)
                deduped_headers.append(header)

            if not deduped_headers:
                raise serializers.ValidationError(
                    {
                        "auth_headers": (
                            "At least one header pair is required for custom header auth."
                        )
                    }
                )

            # Keep first header mirrored for legacy single-header upstream fields.
            attrs["auth_headers"] = deduped_headers
            attrs["auth_header_key"] = deduped_headers[0]["key"]
            attrs["auth_header_value"] = deduped_headers[0]["value"]

        if auth_type == "query_param" and (
            not attrs.get("auth_query_param_key") or not attrs.get("auth_query_param_value")
        ):
            raise serializers.ValidationError(
                {
                    "auth_query_param_key": (
                        "auth_query_param_key and auth_query_param_value are required for query_param auth."
                    )
                }
            )
        return attrs

    @staticmethod
    def local_model_data(validated_data: dict) -> dict:
        """Return only fields persisted in MCPServerRegistration.

        Emits ONLY keys actually present in `validated_data` so that PATCH
        partial updates don't wipe untouched columns (DECISION-D Phase 1 bug
        fix: previously every key defaulted to "" / [] / {} which trampled
        existing rows on a one-field PATCH like default_scan_action).
        """
        allowed = {
            "name",
            "url",
            "transport",
            "command",
            "args",
            "env_vars",
            "description",
            "default_scan_action",
            # ── BYOK auth (Phase B) ──────────────────────────────────
            # Persisted to MCPServerRegistration; secret values land in
            # EncryptedCharField columns (encrypted-at-rest transparently).
            # The model only stores the single-header / bearer / basic
            # shapes the gateway body-reader supports today; auth_headers[]
            # and auth_query_param_* remain validated but unpersisted
            # (deferred — bearer/basic/header cover the target servers).
            "auth_type",
            "auth_token",
            "auth_username",
            "auth_password",
            "auth_header_key",
            "auth_header_value",
        }
        return {k: v for k, v in validated_data.items() if k in allowed}


class MCPToolRegistrationSerializer(serializers.ModelSerializer):
    server_name = serializers.CharField(source="server.name", read_only=True)

    class Meta:
        model = MCPToolRegistration
        fields = [
            "id",
            "server",
            "server_name",
            "tool_name",
            "description",
            "enabled",
            "sensitivity",
            "input_schema",
            "scan_action",
            "last_seen_at",
        ]
        read_only_fields = ["id", "server", "server_name", "tool_name", "description", "input_schema", "last_seen_at"]


class MCPScanControlSerializer(serializers.ModelSerializer):
    server_id = serializers.UUIDField(source="server.id", read_only=True, allow_null=True)
    server_slug = serializers.CharField(source="server.server_slug", read_only=True, allow_null=True)

    class Meta:
        model = MCPScanControl
        fields = [
            "id",
            "organization",
            "server",
            "server_id",
            "server_slug",
            "tool_name",
            "tier",
            "enabled",
            "direction",
            "scope_type",
            "target_mode",
            "key_path",
            "strict_mode",
            "action",
            "priority",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "organization", "created_at", "updated_at"]

    def validate(self, attrs):
        scope = attrs.get("scope_type") or getattr(self.instance, "scope_type", "org")
        server = attrs.get("server") if "server" in attrs else getattr(self.instance, "server", None)
        tool_name = (attrs.get("tool_name") or getattr(self.instance, "tool_name", "") or "").strip()
        target_mode = attrs.get("target_mode") or getattr(self.instance, "target_mode", "entire")
        key_path = (attrs.get("key_path") or getattr(self.instance, "key_path", "") or "").strip()

        if scope == "server" and not server:
            raise serializers.ValidationError({"server": "Required for server scope."})
        if scope == "tool":
            if not server:
                raise serializers.ValidationError({"server": "Required for tool scope."})
            if not tool_name:
                raise serializers.ValidationError({"tool_name": "Required for tool scope."})
        if scope == "org" and server:
            raise serializers.ValidationError({"server": "Must be empty for org scope."})
        if target_mode == "key_path" and not key_path:
            raise serializers.ValidationError({"key_path": "Required when target_mode is key_path."})
        if target_mode == "entire" and key_path:
            attrs["key_path"] = ""
        return attrs


class MCPEventSerializer(serializers.ModelSerializer):
    class Meta:
        model = MCPEvent
        fields = [
            "id",
            "user_id",
            "username",
            "server_slug",
            "server_name",
            "tool_name",
            "decision",
            "policy_ids",
            "policy_reason",
            "latency_ms",
            "request_id",
            "metadata",
            "compliance_tags",
            "scan_findings",
            "timestamp",
        ]
        read_only_fields = fields
