"""Serializers for core models (Agent, Endpoint, KillSwitch)."""

from django.contrib.auth import get_user_model
from django.db.models import Manager
from rest_framework import serializers

from core.models import AGENT_TYPE_CHOICES, Agent, Endpoint, KillSwitch

# M-02: Resolve the active Django user model once at import time. The previous
# code referenced ``User`` in get_primary_user_display() without ever importing
# it, raising NameError on every call (the except clause raised the same error).
User = get_user_model()


class AgentRegisterSerializer(serializers.Serializer):
    """Request body for POST /api/agents/register/."""

    agent_id = serializers.UUIDField(required=False, allow_null=True)
    agent_type = serializers.ChoiceField(choices=[c[0] for c in AGENT_TYPE_CHOICES])
    name = serializers.CharField(max_length=255)
    endpoint_identifier = serializers.CharField(max_length=255, required=False, allow_blank=True)
    endpoint_id = serializers.IntegerField(required=False, allow_null=True)
    user_id = serializers.IntegerField(required=False, allow_null=True)
    organization_id = serializers.IntegerField(required=False, allow_null=True)
    metadata = serializers.JSONField(required=False, default=dict)
    endpoint_username = serializers.CharField(required=False, allow_blank=True, max_length=255)


class AgentTelemetrySerializer(serializers.Serializer):
    """Request body for POST /api/agents/telemetry/."""

    agent_id = serializers.UUIDField()
    endpoint_id = serializers.IntegerField(required=False, allow_null=True)
    os = serializers.CharField(required=False, allow_blank=True)
    agent_version = serializers.CharField(required=False, allow_blank=True)
    threats_24h = serializers.JSONField(required=False, allow_null=True)  # number (count) or list of IDs
    active_copilots = serializers.ListField(child=serializers.CharField(), required=False, allow_null=True)
    detected_services = serializers.ListField(child=serializers.CharField(), required=False, allow_null=True)
    cpu_usage = serializers.FloatField(required=False, allow_null=True, min_value=0, max_value=100)
    memory_usage = serializers.FloatField(required=False, allow_null=True, min_value=0, max_value=100)
    process_count = serializers.IntegerField(required=False, allow_null=True, min_value=0)
    high_load = serializers.BooleanField(required=False, allow_null=True)
    file_access_events = serializers.ListField(child=serializers.DictField(), required=False, allow_null=True)
    clipboard_metadata = serializers.DictField(required=False, allow_null=True)
    proxy_enabled = serializers.BooleanField(required=False, allow_null=True)
    endpoint_username = serializers.CharField(required=False, allow_blank=True, max_length=255)
    # Gateway-only (stored in Agent.metadata when agent_type=gateway)
    total_requests = serializers.IntegerField(required=False, allow_null=True, min_value=0)
    blocked = serializers.IntegerField(required=False, allow_null=True, min_value=0)
    allowed = serializers.IntegerField(required=False, allow_null=True, min_value=0)
    avg_latency_ms = serializers.FloatField(required=False, allow_null=True, min_value=0)
    active_connections = serializers.IntegerField(required=False, allow_null=True, min_value=0)
    rules_applied = serializers.IntegerField(required=False, allow_null=True, min_value=0)
    location = serializers.CharField(required=False, allow_blank=True)


class EndpointSummarySerializer(serializers.ModelSerializer):
    """Minimal endpoint for nested in agent list."""

    class Meta:
        model = Endpoint
        fields = ("id", "identifier", "name", "status", "last_seen_at", "metadata")


class _EndpointListManySerializer(serializers.ListSerializer):
    """Batched list serializer for EndpointListSerializer (M-26 N+1 fix).

    The old per-row path evaluated ``obj.agents.all()`` twice per endpoint
    (once in get_primary_user_id, once in get_primary_user_display) and then
    resolved the primary user with a per-row ``User.objects.get`` — a 3N+1
    query pattern. This resolves every endpoint's primary agent in ONE query
    and every primary user in ONE ``in_bulk`` query, regardless of row count.
    """

    def to_representation(self, data):
        iterable = data.all() if isinstance(data, Manager) else data
        items = list(iterable)
        child = self.child
        child._primary_agent_by_endpoint = self._build_primary_agent_map(items)
        user_ids = {
            agent.user_id
            for agent in child._primary_agent_by_endpoint.values()
            if agent is not None and agent.user_id
        }
        child._users_by_id = User.objects.in_bulk(user_ids) if user_ids else {}
        return [child.to_representation(item) for item in items]

    @staticmethod
    def _build_primary_agent_map(items):
        """Map endpoint pk -> most recently updated agent (or None)."""
        primary: dict = {}
        pending = []
        for endpoint in items:
            prefetched = getattr(endpoint, "_prefetched_objects_cache", None) or {}
            if "agents" in prefetched:
                # Respect an existing prefetch_related("agents") — no extra query.
                primary[endpoint.pk] = next(iter(endpoint.agents.all()), None)
            else:
                pending.append(endpoint.pk)
        if pending:
            # Agent.Meta.ordering = ["-updated_at"], so the first agent seen per
            # endpoint is the most recently updated one — identical semantics to
            # the old per-row next(iter(obj.agents.all())).
            for agent in Agent.objects.filter(endpoint_id__in=pending).order_by("-updated_at"):
                primary.setdefault(agent.endpoint_id, agent)
            for pk in pending:
                primary.setdefault(pk, None)
        return primary


class EndpointListSerializer(serializers.ModelSerializer):
    """Endpoint for GET /api/endpoints/ with flattened telemetry metadata for dashboard."""

    os = serializers.SerializerMethodField()
    agent_version = serializers.SerializerMethodField()
    threats_24h = serializers.SerializerMethodField()
    active_copilots = serializers.SerializerMethodField()
    detected_services = serializers.SerializerMethodField()
    cpu_usage = serializers.SerializerMethodField()
    memory_usage = serializers.SerializerMethodField()
    primary_user_id = serializers.SerializerMethodField()
    primary_user_display = serializers.SerializerMethodField()
    endpoint_username = serializers.SerializerMethodField()

    class Meta:
        model = Endpoint
        fields = (
            "id",
            "identifier",
            "name",
            "status",
            "last_seen_at",
            "os",
            "agent_version",
            "threats_24h",
            "active_copilots",
            "detected_services",
            "cpu_usage",
            "memory_usage",
            "primary_user_id",
            "primary_user_display",
            "endpoint_username",
        )
        # M-26: many=True goes through the batched list serializer above.
        list_serializer_class = _EndpointListManySerializer

    def _meta(self, obj, key, default=None):
        return (obj.metadata or {}).get(key, default)

    def get_os(self, obj):
        return self._meta(obj, "os", "")

    def get_agent_version(self, obj):
        return self._meta(obj, "agent_version", "")

    def get_threats_24h(self, obj):
        # Prefer agent-reported value; else use backend-derived count from EnforcementEvent (last 24h)
        agent_value = self._meta(obj, "threats_24h")
        if agent_value is not None:
            return agent_value
        return self.context.get("threats_24h_by_endpoint", {}).get(obj.id, 0)

    def get_active_copilots(self, obj):
        return self._meta(obj, "active_copilots") or []

    def get_detected_services(self, obj):
        return self._meta(obj, "detected_services") or []

    def get_cpu_usage(self, obj):
        return self._meta(obj, "cpu_usage")

    def get_memory_usage(self, obj):
        return self._meta(obj, "memory_usage")

    def _primary_agent(self, obj):
        """Most recently updated agent for this endpoint.

        M-26: in list mode the batch map built by _EndpointListManySerializer
        is used (one query for ALL rows). In detail mode the per-object result
        is memoized so ``agents.all()`` is evaluated at most ONCE per object
        (the old code evaluated it twice — once per SerializerMethodField).
        """
        batch = getattr(self, "_primary_agent_by_endpoint", None)
        if batch is not None and obj.pk in batch:
            return batch[obj.pk]
        if not hasattr(obj, "_endpoint_primary_agent_cache"):
            obj._endpoint_primary_agent_cache = next(iter(obj.agents.all()), None)
        return obj._endpoint_primary_agent_cache

    def _resolve_user(self, user_id):
        """M-26: batched in list mode (in_bulk), single fetch in detail mode."""
        users_by_id = getattr(self, "_users_by_id", None)
        if users_by_id is not None:
            return users_by_id.get(user_id)
        try:
            return User.objects.get(id=user_id)
        except User.DoesNotExist:
            return None

    def get_primary_user_id(self, obj):
        first = self._primary_agent(obj)
        return first.user_id if first else None

    def get_primary_user_display(self, obj):
        """
        Return a human-friendly name for the primary user associated with this endpoint.
        Uses the most recently updated agent's user_id to resolve the Django User.
        """
        first = self._primary_agent(obj)
        user_id = first.user_id if first else None
        if not user_id:
            return None
        user = self._resolve_user(user_id)
        if user is None:
            return None
        full = getattr(user, "get_full_name", lambda: "")() or ""
        if full.strip():
            return full
        if getattr(user, "username", ""):
            return user.username
        if getattr(user, "email", ""):
            return user.email
        return f"User {user.id}"

    def get_endpoint_username(self, obj):
        return (obj.metadata or {}).get("endpoint_username") or ""


class AgentListSerializer(serializers.ModelSerializer):
    """Agent for GET /api/agents/ list response."""

    agent_id = serializers.UUIDField(source="id", read_only=True)
    endpoint_id = serializers.SerializerMethodField()
    endpoint_summary = serializers.SerializerMethodField()

    class Meta:
        model = Agent
        fields = (
            "agent_id",
            "agent_type",
            "name",
            "endpoint_id",
            "user_id",
            "status",
            "metadata",
            "created_at",
            "updated_at",
            "endpoint_summary",
        )

    def get_endpoint_id(self, obj):
        return obj.endpoint_id if obj.endpoint_id else None

    def get_endpoint_summary(self, obj):
        if not obj.endpoint_id:
            return None
        return EndpointSummarySerializer(obj.endpoint).data


class KillSwitchSerializer(serializers.ModelSerializer):
    """Full KillSwitch representation for list/detail responses."""

    activated_by_username = serializers.SerializerMethodField()

    class Meta:
        model = KillSwitch
        fields = (
            "id",
            "model_name",
            "api_key_prefix",
            "is_active",
            "action",
            "fallback_model",
            "reason",
            "activated_by",
            "activated_by_username",
            "activated_at",
            "created_at",
            "updated_at",
        )
        read_only_fields = ("id", "activated_by", "activated_at", "created_at", "updated_at")

    def get_activated_by_username(self, obj: KillSwitch) -> str | None:
        if obj.activated_by:
            return obj.activated_by.username
        return None


class KillSwitchCreateSerializer(serializers.ModelSerializer):
    """Create/update a KillSwitch entry."""

    class Meta:
        model = KillSwitch
        fields = ("model_name", "api_key_prefix", "action", "fallback_model", "reason")

    def validate(self, attrs: dict) -> dict:
        # M-23: on partial update (PATCH) DRF only puts the supplied fields in
        # ``attrs``, so validating attrs alone let a payload of just
        # {"fallback_model": "<instance.model_name>"} bypass the self-loop
        # check below (and the global-scope / M-22 prefix checks). The DRF
        # update path never calls full_clean(), so KillSwitch.clean() does
        # not catch it either. Resolve every field against the EFFECTIVE
        # post-update state: the payload value when supplied, otherwise the
        # current instance value.
        def _effective(field: str):
            if field in attrs:
                return attrs[field]
            if self.instance is not None:
                return getattr(self.instance, field, None)
            return None

        action = _effective("action")
        model_name = (_effective("model_name") or "").strip()
        api_key_prefix = (_effective("api_key_prefix") or "").strip()
        fallback_model = (_effective("fallback_model") or "").strip()

        if action == "reroute" and not fallback_model:
            raise serializers.ValidationError(
                {"fallback_model": "Fallback model is required when action is 'reroute'."}
            )
        # M-20: reject reroute-to-self. The model's clean() enforces this but
        # full_clean() is never invoked from the DRF .save() path, so we mirror
        # the rule here to block self-loops before they reach the DB / Redis.
        if fallback_model and fallback_model == model_name:
            raise serializers.ValidationError(
                {"fallback_model": "fallback_model must differ from model_name (self-loop)."}
            )
        # ZeroShield guard models are platform-managed: they must never be the
        # target of a kill-switch (model_name) nor a reroute fallback. The
        # ModelIsolate path already rejects guard models; mirror that here so
        # the two containment surfaces are consistent (a guard switch / reroute
        # would otherwise persist and break _resolve_runtime_model at runtime).
        from core.models import is_platform_managed_llm_model_name

        if model_name and is_platform_managed_llm_model_name(model_name):
            raise serializers.ValidationError(
                {"model_name": "ZeroShield guard models are platform-managed and cannot be kill-switched."}
            )
        if fallback_model and is_platform_managed_llm_model_name(fallback_model):
            raise serializers.ValidationError(
                {"fallback_model": "ZeroShield guard models are platform-managed and cannot be a reroute target."}
            )
        # Global ('__global__') scope is disabled entirely: a single switch must
        # never be able to take down every model. Operators target a specific
        # model in their own organization.
        if model_name == KillSwitch.SCOPE_GLOBAL:
            raise serializers.ValidationError(
                {
                    "model_name": (
                        "Global ('__global__') kill-switch scope is disabled. "
                        "Target a specific model in your organization."
                    )
                }
            )
        # Credential-wide sentinel requires a real API key prefix (Module 2 SOC
        # containment). Without a prefix the Redis key would be org-model scoped
        # under the fake name '__credential__' and would not enforce as intended.
        if model_name == KillSwitch.SCOPE_CREDENTIAL and not api_key_prefix:
            raise serializers.ValidationError(
                {
                    "api_key_prefix": (
                        "Credential-wide ('__credential__') kill-switch requires "
                        "api_key_prefix (gateway API key prefix)."
                    )
                }
            )
        if model_name == KillSwitch.SCOPE_CREDENTIAL and action == "reroute":
            raise serializers.ValidationError(
                {
                    "action": (
                        "Credential-wide ('__credential__') kill-switch supports "
                        "action='disable' only."
                    )
                }
            )
        request = self.context.get("request")
        # Duplicate guard: the DB has unique_together (organization, model_name,
        # api_key_prefix), but `organization` is NOT a serializer field (it is
        # injected in perform_create), so DRF cannot auto-apply
        # UniqueTogetherValidator. Without this pre-check a second kill-switch for
        # the same (org, model, prefix) reaches the DB and raises IntegrityError
        # -> an unhandled 500 ("Internal server error"). Return a clean,
        # actionable 400 instead. (The view ALSO catches IntegrityError to cover
        # the create-create race.) Excludes self on update so an edit that keeps
        # the same model_name is not flagged as a duplicate of itself.
        if request and "model_name" in attrs and model_name and model_name != KillSwitch.SCOPE_GLOBAL:
            org = getattr(getattr(request.user, "profile", None), "organization", None)
            if org:
                dup_qs = KillSwitch.objects.filter(
                    organization=org,
                    model_name=model_name,
                    api_key_prefix=api_key_prefix,
                )
                if self.instance is not None:
                    dup_qs = dup_qs.exclude(pk=self.instance.pk)
                if dup_qs.exists():
                    _scope = f' (key prefix "{api_key_prefix}")' if api_key_prefix else ""
                    raise serializers.ValidationError(
                        {
                            "model_name": (
                                f'A kill-switch already exists for "{model_name}"{_scope}. '
                                "Edit or delete the existing kill-switch instead of creating a duplicate."
                            )
                        }
                    )
        # Warning is advisory-only: keep it gated on a model_name actually
        # supplied in the payload (always true on create) so a PATCH that
        # does not touch model_name never injects the warning sentinel.
        # Skip for credential-wide sentinel (not a real LLM model name).
        if (
            request
            and "model_name" in attrs
            and model_name
            and model_name != KillSwitch.SCOPE_GLOBAL
            and model_name != KillSwitch.SCOPE_CREDENTIAL
        ):
            org = getattr(getattr(request.user, "profile", None), "organization", None)
            if org:
                from core.models import LLMModelConfig

                if not LLMModelConfig.objects.filter(
                    organization=org,
                    model_name=model_name,
                    is_active=True,
                ).exists():
                    attrs["_model_name_warning"] = (
                        f'"{model_name}" is not an active connected model for this org. '
                        "Use the registered model_name from Model Connections (not LiteLLM model_id)."
                    )
        # M-22: a credential-scoped kill-switch targets a specific gateway API key
        # via its prefix. Validate the prefix belongs to the AUTHENTICATED caller's
        # org so one tenant cannot scope a switch onto another org's credential.
        # Only enforced when we have a request/org context (so context-free unit
        # tests and global-scope switches are unaffected). Uses the EFFECTIVE
        # (instance-merged) values above so partial updates cannot bypass it
        # (M-23).
        if request and api_key_prefix and model_name != KillSwitch.SCOPE_GLOBAL:
            org = getattr(getattr(request.user, "profile", None), "organization", None)
            if org:
                from core.models import GatewayAPIKey

                if not GatewayAPIKey.objects.filter(
                    organization=org,
                    prefix=api_key_prefix,
                ).exists():
                    raise serializers.ValidationError(
                        {
                            "api_key_prefix": (
                                "No gateway API key with this prefix exists for your "
                                "organization. Use a prefix from your own Gateway API Keys."
                            )
                        }
                    )
        # A reroute fallback must be a real, active connected model for the
        # caller's org — reject nonexistent / cross-org fallback models so a
        # kill-switch cannot reroute traffic to a model that does not exist or
        # belongs to another tenant. Gated on request/org context (context-free
        # unit tests unaffected) and only when a fallback is actually supplied.
        if request and action == "reroute" and fallback_model:
            org = getattr(getattr(request.user, "profile", None), "organization", None)
            if org:
                from core.models import LLMModelConfig

                if not LLMModelConfig.objects.filter(
                    organization=org,
                    model_name=fallback_model,
                    is_active=True,
                ).exists():
                    raise serializers.ValidationError(
                        {
                            "fallback_model": (
                                f'"{fallback_model}" is not an active connected model for your '
                                "organization. Use a model_name from Model Connections."
                            )
                        }
                    )
        return attrs


class KillSwitchActivateSerializer(serializers.Serializer):
    """Request body for activate/deactivate actions."""

    reason = serializers.CharField(required=False, allow_blank=True, default="")


# ── ModelState serializers ─────────────────────────────────────────────

class ModelStateSerializer(serializers.ModelSerializer):
    """Full ModelState representation."""

    class Meta:
        from core.models import ModelState
        model = ModelState
        fields = (
            "id",
            "model_name",
            "status",
            "risk_score",
            "threshold",
            "action",
            "fallback_model",
            "isolation_reason",
            "isolated_at",
            "isolated_until",
            "cooldown_seconds",
            "last_updated",
            "created_at",
        )
        read_only_fields = ("id", "risk_score", "isolated_at", "last_updated", "created_at")


class ModelStateUpdateSerializer(serializers.Serializer):
    """Update threshold/action/fallback configuration for a model."""

    threshold = serializers.FloatField(min_value=0, max_value=100, required=False)
    action = serializers.ChoiceField(choices=["block", "reroute", "alert"], required=False)
    fallback_model = serializers.CharField(required=False, allow_blank=True)
    cooldown_seconds = serializers.IntegerField(min_value=30, max_value=86400, required=False)

    def validate(self, attrs):
        if attrs.get("action") == "reroute" and not attrs.get("fallback_model"):
            raise serializers.ValidationError(
                {"fallback_model": "Fallback model is required for reroute action."}
            )
        return attrs


class ModelIsolateSerializer(serializers.Serializer):
    """Request body for manually isolating a model."""

    model_name = serializers.CharField(required=True)
    reason = serializers.CharField(required=False, allow_blank=True, default="")
    action = serializers.ChoiceField(choices=["block", "reroute", "alert"], default="block")
    fallback_model = serializers.CharField(required=False, allow_blank=True, default="")
    cooldown_seconds = serializers.IntegerField(min_value=30, max_value=86400, default=300)

    def validate(self, attrs):
        # M-20: this serializer previously had no validate(), so an isolate
        # request could set action=reroute with no fallback, or reroute a model
        # to itself (self-loop). Mirror ModelState.clean() since full_clean() is
        # not invoked on the DRF .save() path.
        from core.models import is_platform_managed_llm_model_name

        action = attrs.get("action", "block")
        model_name = (attrs.get("model_name") or "").strip()
        fallback_model = (attrs.get("fallback_model") or "").strip()
        if action == "reroute" and not fallback_model:
            raise serializers.ValidationError(
                {"fallback_model": "Fallback model is required for reroute action."}
            )
        if fallback_model and fallback_model == model_name:
            raise serializers.ValidationError(
                {"fallback_model": "fallback_model must differ from model_name (self-loop)."}
            )
        # A platform-managed ZeroShield guard model must never become a reroute
        # target — _resolve_runtime_model raises for guard models routed via
        # LiteLLM, so accepting it here just persists a config that fails at
        # enforcement time. (The view already rejects a guard model_name.)
        if fallback_model and is_platform_managed_llm_model_name(fallback_model):
            raise serializers.ValidationError(
                {"fallback_model": "ZeroShield guard models are platform-managed and cannot be a reroute target."}
            )
        # Reroute target must be a real, active connected model for the caller's
        # org — reject nonexistent / cross-org fallback models. Gated on request
        # context so context-free unit tests are unaffected.
        request = self.context.get("request")
        if action == "reroute" and fallback_model and request is not None:
            org = getattr(getattr(request.user, "profile", None), "organization", None)
            if org:
                from core.models import LLMModelConfig

                if not LLMModelConfig.objects.filter(
                    organization=org,
                    model_name=fallback_model,
                    is_active=True,
                ).exists():
                    raise serializers.ValidationError(
                        {
                            "fallback_model": (
                                f'"{fallback_model}" is not an active connected model for your '
                                "organization. Use a model_name from Model Connections."
                            )
                        }
                    )
        return attrs


class KillSwitchAuditLogSerializer(serializers.ModelSerializer):
    """Read-only audit log for kill-switch events."""

    class Meta:
        from core.models import KillSwitchAuditLog
        model = KillSwitchAuditLog
        fields = (
            "id",
            "event",
            "model_name",
            "risk_score",
            "threshold",
            "action",
            "reason",
            "request_id",
            "metadata",
            "triggered_by",
            "timestamp",
        )
