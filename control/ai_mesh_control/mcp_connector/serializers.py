from rest_framework import serializers

from .models import GuardrailProfile, MCPEvent, MCPServerRegistration, MCPToolRegistration


SUPPORTED_CONTEXTFORGE_TRANSPORTS = {"streamable-http", "sse"}
ALL_TRANSPORTS = {"streamable-http", "sse", "stdio", "websocket"}


class AuthHeaderPairSerializer(serializers.Serializer):
    key = serializers.CharField(required=True, allow_blank=False)
    value = serializers.CharField(required=True, allow_blank=False)


class MCPServerRegistrationSerializer(serializers.ModelSerializer):
    gateway_endpoint = serializers.ReadOnlyField()

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
            "contextforge_server_id",
            "is_active",
            "is_exposed_to_agents",
            "connection_status",
            "tools_count",
            "last_sync_at",
            "last_health_at",
            "last_health_status",
            "risk_level",
            "gateway_endpoint",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "id",
            "server_slug",
            "contextforge_server_id",
            "connection_status",
            "tools_count",
            "last_sync_at",
            "last_health_at",
            "last_health_status",
            "risk_level",
            "gateway_endpoint",
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
        ]

    def validate(self, attrs):
        transport = (attrs.get("transport") or "").strip().lower()

        if transport and transport not in ALL_TRANSPORTS:
            raise serializers.ValidationError(
                {"transport": f"Unsupported transport '{transport}'. Use one of: {', '.join(sorted(ALL_TRANSPORTS))}."}
            )

        # stdio requires command; url is optional
        if transport == "stdio":
            if not attrs.get("command"):
                raise serializers.ValidationError({"command": "command is required for stdio transport."})
        else:
            # websocket, streamable-http, sse all require url
            if not attrs.get("url"):
                raise serializers.ValidationError({"url": "url is required for this transport."})

        auth_type = (attrs.get("auth_type") or "none").strip().lower()
        attrs["auth_type"] = auth_type

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
        """Return only fields persisted in MCPServerRegistration."""
        return {
            "name": validated_data.get("name"),
            "url": validated_data.get("url", ""),
            "transport": validated_data.get("transport"),
            "command": validated_data.get("command", ""),
            "args": validated_data.get("args", []),
            "env_vars": validated_data.get("env_vars", {}),
            "description": validated_data.get("description", ""),
        }

    @staticmethod
    def contextforge_data(validated_data: dict) -> dict:
        """Return local + optional upstream auth fields for ContextForge."""
        keys = {
            "name",
            "url",
            "transport",
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
        }
        return {k: v for k, v in validated_data.items() if k in keys and v not in (None, "")}


class GuardrailProfileSerializer(serializers.ModelSerializer):
    class Meta:
        model = GuardrailProfile
        fields = [
            "id",
            "name",
            "description",
            "input_policy",
            "output_policy",
            "is_default",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]


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
            "last_seen_at",
        ]
        read_only_fields = ["id", "server", "server_name", "tool_name", "description", "input_schema", "last_seen_at"]


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
            "timestamp",
        ]
        read_only_fields = fields
