import re

from rest_framework import serializers

from .models import Policy, PolicyVersion, Rule

# M-23: upper bound on a rule regex length. Anything longer than this is far
# beyond any legitimate pattern and is a ReDoS smell — reject at write time so
# a bad pattern never reaches the compiled bundle / hot evaluation path.
_MAX_REGEX_LEN = 1000

# FINDING-3 (ReDoS): catastrophic-backtracking shapes. A quantified group that
# is itself quantified — (X+)+, (X*)*, (X+)*, (X*)+, and the {m,n}/explicit-star
# variants — makes Python's backtracking engine run super-linearly: a ~6-char
# pattern like "(a+)+$" pins the sole sync executor thread for minutes on a
# short adversarial input. The write-time length cap does NOT catch these (they
# are tiny), so we reject the nested-unbounded-quantifier shape outright. This
# is a heuristic, not a full ReDoS solver: it targets the well-known nested
# quantifier family called out in the finding. Matching is done on the raw
# pattern source (backslashes stripped so an escaped literal like "\)" can't
# masquerade as a group boundary).
_NESTED_QUANTIFIER_RE = re.compile(
    r"\([^()]*[+*]\s*\)\s*[+*{]"  # (…+)+  (…*)*  (…+)*  (…*)+  (…+){m,n}
)

# R13: a quantified group whose body itself contains an *unbounded wildcard*
# repetition (``.*`` / ``.+``). Patterns like ``(.*a){8}`` or ``(.*a)+`` made a
# single eval run ~9.7s: the inner ``.*`` and the outer ``{n}``/``+`` produce
# the same exponential backtracking blow-up as the nested-quantifier family,
# but the body ends in a literal (``a``) so ``_NESTED_QUANTIFIER_RE`` misses it.
# Match a group containing ``.*``/``.+`` that is immediately followed by an
# outer quantifier (``+``/``*``/``{m,n}``).
_QUANTIFIED_WILDCARD_GROUP_RE = re.compile(
    r"\([^()]*\.[*+][^()]*\)\s*[+*{]"  # (.*…)+  (.+…)*  (.*a){n}
)

# R13: a quantified *alternation* group, e.g. ``(a|ab)+`` / ``(X|XY)*`` /
# ``(foo|foobar){n}``. When alternatives overlap (one is a prefix of another)
# the engine has multiple ways to match the same input under the outer
# quantifier → catastrophic backtracking. Detecting "a group that contains an
# unescaped alternation AND is immediately quantified" is a conservative
# superset of the prefix-overlap case and matches the well-known ReDoS family.
_QUANTIFIED_ALTERNATION_GROUP_RE = re.compile(
    r"\([^()]*\|[^()]*\)\s*[+*{]"  # (X|XY)+  (a|ab)*  (foo|foobar){n}
)


def _has_redos_shape(pattern):
    """Return True if ``pattern`` contains a catastrophic-backtracking shape.

    Conservative heuristics (NOT a full ReDoS solver) targeting the well-known
    families called out in the findings:

      * FINDING-3: nested unbounded quantifier — a group whose body ends in an
        unbounded quantifier (``+``/``*``) that is itself immediately followed
        by another quantifier (e.g. ``(a+)+``, ``(a*)*``).
      * R13: quantified wildcard group — a group containing ``.*``/``.+`` that
        is itself quantified (e.g. ``(.*a){8}``, ``(.*a)+``).
      * R13: quantified alternation group — a quantified group containing an
        alternation (e.g. ``(X|XY)+``, ``(a|ab)*``), the alternation-overlap
        backtracking family.

    Backslashes are stripped first so escaped parens / metacharacters can't
    spoof a group boundary or an alternation/wildcard token.
    """
    if not isinstance(pattern, str):
        return False
    # Drop escaped chars so "\(" / "\)" / "\+" / "\|" / "\." don't read as
    # structure (an escaped literal must not be mistaken for a metacharacter).
    stripped = re.sub(r"\\.", "", pattern)
    return bool(
        _NESTED_QUANTIFIER_RE.search(stripped)
        or _QUANTIFIED_WILDCARD_GROUP_RE.search(stripped)
        or _QUANTIFIED_ALTERNATION_GROUP_RE.search(stripped)
    )


def reject_unsupported_rule_action(value):
    """T01 L01-2: operator REWRITE is unsupported. model_downgrade stays valid."""
    if str(value or "").strip().lower() == "rewrite":
        raise serializers.ValidationError(
            "REWRITE is unsupported. Control rejects this action; use redact or block."
        )
    return value


def validate_condition(value):
    """M-23: schema + safety validation for a Rule.condition JSONField.

    Previously ``condition`` accepted any JSON and a malformed regex failed
    *silently* at evaluation time (the rule just never matched). This validator
    runs at write time so operators get immediate feedback:

      * condition must be a JSON object (dict);
      * any ``regex``/``pattern`` string must compile (re.error -> 400), be
        within ``_MAX_REGEX_LEN`` (cheap ReDoS guard), and NOT contain a
        nested unbounded-quantifier shape (FINDING-3 catastrophic-backtracking
        ReDoS guard).

    Empty / missing condition is allowed (keyword rules, presets). Returns the
    value unchanged on success; raises ValidationError otherwise.
    """
    if value in (None, ""):
        return value
    if not isinstance(value, dict):
        raise serializers.ValidationError("condition must be a JSON object.")

    pattern = value.get("regex") or value.get("pattern")
    if pattern is not None:
        if not isinstance(pattern, str):
            raise serializers.ValidationError("condition.regex/pattern must be a string.")
        if len(pattern) > _MAX_REGEX_LEN:
            raise serializers.ValidationError(
                f"condition regex exceeds {_MAX_REGEX_LEN} chars (possible ReDoS)."
            )
        if _has_redos_shape(pattern):
            raise serializers.ValidationError(
                "condition regex contains a catastrophic-backtracking shape "
                "(nested unbounded quantifier like (a+)+, quantified wildcard "
                "group like (.*a){n}, or quantified alternation like (X|XY)+) "
                "— rejected as ReDoS."
            )
        try:
            re.compile(pattern)
        except re.error as exc:
            raise serializers.ValidationError(f"condition regex does not compile: {exc}")
    return value


def validate_redaction_config(value):
    """F8: a redact rule's ``redaction_config.regex`` feeds gateway
    apply_redaction's ``.sub()`` — a catastrophic-backtracking pattern there pins
    a worker thread, yet write-time validation only ran on ``condition``. Apply
    the SAME ReDoS/compile/length checks to redaction_config.regex/pattern so a
    dangerous redaction pattern is rejected at write time. Empty/missing allowed.
    """
    if value in (None, ""):
        return value
    if not isinstance(value, dict):
        raise serializers.ValidationError("redaction_config must be a JSON object.")
    pattern = value.get("regex") or value.get("pattern")
    if pattern is not None:
        if not isinstance(pattern, str):
            raise serializers.ValidationError("redaction_config.regex/pattern must be a string.")
        if len(pattern) > _MAX_REGEX_LEN:
            raise serializers.ValidationError(
                f"redaction_config regex exceeds {_MAX_REGEX_LEN} chars (possible ReDoS)."
            )
        if _has_redos_shape(pattern):
            raise serializers.ValidationError(
                "redaction_config regex contains a catastrophic-backtracking shape "
                "(nested unbounded quantifier, quantified wildcard group, or "
                "quantified alternation) — rejected as ReDoS."
            )
        try:
            re.compile(pattern)
        except re.error as exc:
            raise serializers.ValidationError(f"redaction_config regex does not compile: {exc}")
    return value


class RuleSerializer(serializers.ModelSerializer):
    def validate_condition(self, value):
        return validate_condition(value)

    def validate_action(self, value):
        return reject_unsupported_rule_action(value)

    def validate_redaction_config(self, value):
        return validate_redaction_config(value)

    class Meta:
        model = Rule
        fields = [
            "id",
            "policy",
            "name",
            "rule_type",
            "condition",
            "action",
            "redaction_config",
            "priority",
            "enabled",
            "description",
            "pipeline_stage",
            "target_tool",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ("created_at", "updated_at")


class RuleWriteSerializer(serializers.ModelSerializer):
    def validate_condition(self, value):
        return validate_condition(value)

    def validate_action(self, value):
        return reject_unsupported_rule_action(value)

    def validate_redaction_config(self, value):
        return validate_redaction_config(value)

    class Meta:
        model = Rule
        fields = [
            "id",
            "name",
            "rule_type",
            "condition",
            "action",
            "redaction_config",
            "priority",
            "enabled",
            "description",
            "pipeline_stage",
            "target_tool",
        ]


class PolicySerializer(serializers.ModelSerializer):
    rules = RuleSerializer(many=True, read_only=True)
    mcp_server_slug = serializers.CharField(source="mcp_server.server_slug", read_only=True, default=None)
    mcp_server_name = serializers.CharField(source="mcp_server.name", read_only=True, default=None)

    class Meta:
        model = Policy
        fields = [
            "id",
            "name",
            "code",
            "category",
            "severity",
            "description",
            "enabled",
            "is_system",
            "priority",
            "metadata",
            "policy_domain",
            "mcp_server",
            "mcp_server_slug",
            "mcp_server_name",
            "version",
            "redaction_fields",
            "allowed_user_ids",
            "allowed_agent_ids",
            "allowed_roles",
            "rules",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ("created_at", "updated_at", "version", "is_system")


class PolicyListSerializer(serializers.ModelSerializer):
    rule_count = serializers.SerializerMethodField()
    mcp_server_slug = serializers.CharField(source="mcp_server.server_slug", read_only=True, default=None)
    mcp_server_name = serializers.CharField(source="mcp_server.name", read_only=True, default=None)

    class Meta:
        model = Policy
        fields = [
            "id",
            "name",
            "code",
            "category",
            "severity",
            "description",
            "enabled",
            "is_system",
            "priority",
            "rule_count",
            "metadata",
            "policy_domain",
            "mcp_server",
            "mcp_server_slug",
            "mcp_server_name",
            "version",
            "redaction_fields",
            "allowed_user_ids",
            "allowed_agent_ids",
            "allowed_roles",
            "created_at",
            "updated_at",
        ]

    def get_rule_count(self, obj):
        if not hasattr(obj, "rules"):
            return 0
        try:
            return len(obj.rules.all())
        except Exception:
            return obj.rules.count()


class PolicyListWithStatsSerializer(PolicyListSerializer):
    """Extends PolicyListSerializer with per-policy enforcement metrics."""

    violations = serializers.SerializerMethodField()
    blocked = serializers.SerializerMethodField()
    redacted = serializers.SerializerMethodField()
    effectiveness = serializers.SerializerMethodField()
    affected_users = serializers.SerializerMethodField()
    avg_response = serializers.SerializerMethodField()

    class Meta(PolicyListSerializer.Meta):
        fields = PolicyListSerializer.Meta.fields + [
            "violations",
            "blocked",
            "redacted",
            "effectiveness",
            "affected_users",
            "avg_response",
        ]

    def get_violations(self, obj):
        stats = self._get_stats(obj)
        return stats.get("violations", 0)

    def get_blocked(self, obj):
        stats = self._get_stats(obj)
        return stats.get("blocked", 0)

    def get_redacted(self, obj):
        stats = self._get_stats(obj)
        return stats.get("redacted", 0)

    def get_effectiveness(self, obj):
        stats = self._get_stats(obj)
        return stats.get("effectiveness", 0)

    def get_affected_users(self, obj):
        stats = self._get_stats(obj)
        return stats.get("affected_users", 0)

    def get_avg_response(self, obj):
        stats = self._get_stats(obj)
        return stats.get("avg_response")

    def _get_stats(self, obj):
        stats_map = self.context.get("policy_stats") or {}
        return stats_map.get(obj.id, {})


class PolicyWriteSerializer(serializers.ModelSerializer):
    version = serializers.IntegerField(
        required=False, allow_null=True, help_text="Client version for conflict check (PATCH)"
    )
    # FINDING-23: ``code`` uniqueness is scoped to the organization (see
    # validate_code below), not the whole table. Declare ``code`` explicitly
    # with ``validators=[]`` so DRF does NOT attach its auto-generated global
    # UniqueValidator (which queries Policy.objects.all() across every tenant
    # and leaks a cross-org existence oracle / denies the namespace globally).
    code = serializers.SlugField(
        max_length=64,
        validators=[],
        help_text="Unique identifier within the organization (e.g. POL001)",
    )
    mcp_server = serializers.PrimaryKeyRelatedField(
        queryset=Policy.mcp_server.field.related_model.objects.none(),
        required=False,
        allow_null=True,
        help_text="Optional MCP server UUID to bind this policy to (MCP domain only)",
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        request = self.context.get("request")
        if request is not None:
            from auth.utils import get_request_organization

            org = get_request_organization(request)
            mcp_model = Policy.mcp_server.field.related_model
            if org is not None:
                self.fields["mcp_server"].queryset = mcp_model.objects.filter(organization=org)
            elif getattr(request.user, "is_superuser", False):
                self.fields["mcp_server"].queryset = mcp_model.objects.all()
            else:
                self.fields["mcp_server"].queryset = mcp_model.objects.none()

    def validate_code(self, value):
        """FINDING-23: enforce ``code`` uniqueness PER ORGANIZATION only.

        The global table-level unique constraint is replaced by a composite
        UniqueConstraint(organization, code); uniqueness is therefore checked
        within the request's org. An identical code under a *different* org is
        allowed and must not leak the other tenant's existence. Falls back to a
        global check only when no org can be resolved (superuser / unscoped),
        which mirrors the pre-existing single-tenant behaviour.
        """
        request = self.context.get("request")
        org = None
        if request is not None:
            from auth.utils import get_request_organization

            org = get_request_organization(request)

        qs = Policy.objects.all()
        if org is not None:
            qs = qs.filter(organization=org)
        if self.instance is not None:
            qs = qs.exclude(pk=self.instance.pk)
        if qs.filter(code=value).exists():
            raise serializers.ValidationError("policy with this code already exists.")
        return value

    class Meta:
        model = Policy
        fields = [
            "id",
            "name",
            "code",
            "category",
            "severity",
            "description",
            "enabled",
            "priority",
            "metadata",
            "policy_domain",
            "mcp_server",
            "version",
            "redaction_fields",
            "allowed_user_ids",
            "allowed_agent_ids",
            "allowed_roles",
        ]
        extra_kwargs = {"version": {"read_only": False}}


class PolicyVersionSerializer(serializers.ModelSerializer):
    class Meta:
        model = PolicyVersion
        fields = ["id", "policy", "version", "snapshot", "comment", "created_at", "created_by"]
        read_only_fields = ["id", "policy", "version", "snapshot", "created_at", "created_by"]
