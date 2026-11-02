"""Publish a saved FirewallConfig as a gateway-v2 plan document.

The document is the JSON shape ``plan.document`` decodes. This module does
not import gateway_v2. A missing org map or a failed Redis write does not
undo the saved row.
"""

from __future__ import annotations

import json
import logging
import os
from collections.abc import Mapping, Sequence
from typing import Any

from core.firewall_v2_map import drafts_for
from core.v2_audit_adapter import parse_org_map

logger = logging.getLogger(__name__)

PUBLISHED_PLAN_KEY = "{rv2}:dev:firewall-plan"
_PRIORITY = 100
_DOMAINS = ("pipeline", "rag", "mcp")
_ALL_SURFACES = ("chat", "embeddings", "rag", "vector", "mcp")
_DEFAULT_SURFACES = {
    "pipeline": ("chat", "embeddings"),
    "rag": ("rag", "vector"),
    "mcp": ("mcp",),
}
_CATEGORIES = {
    "prompt_injection": "prompt_injection",
    "injection": "prompt_injection",
    "jailbreak": "jailbreak",
    "pii": "pii",
    "phi": "pii",
    "secret": "secret",
    "credential": "credential",
    "pci": "credential",
    "command": "command",
    "toxicity": "toxicity",
    "exfil": "exfil",
    "dos": "dos",
}
_STAGES = {"", "query", "retriever", "ranker", "generator"}
_UNSUPPORTED_ACTIONS = {"rewrite", "model_downgrade"}


def gateway_org_for(slug: str, raw_map: str) -> str | None:
    """The one gateway org mapped to this console slug. Ambiguous maps return none."""
    matches = [source for source, target in parse_org_map(raw_map).items() if target == slug]
    if len(matches) != 1:
        return None
    return matches[0]


def plan_key(org_id: str) -> str:
    """The per-org plan key. The dev key stays a fallback and is not written here."""
    return f"{{rv2}}:plan:{org_id}"


def document_bytes(
    org_id: str,
    payload: Mapping[str, Any],
    policy_rows: Sequence[Mapping[str, Any]] = (),
) -> bytes:
    """Firewall drafts plus policy rules. An empty firewall list means the firewall is off."""
    rules = [{**row, "priority": _PRIORITY} for row in drafts_for(payload)]
    rules.extend(policy_rows)
    body = {"org_id": org_id, "rules": rules, "streaming_mode": "incremental"}
    return json.dumps(body, separators=(",", ":"), sort_keys=True).encode("utf-8")


def policy_drafts(
    policies: Sequence[Mapping[str, Any]],
) -> tuple[list[dict[str, Any]], list[str]]:
    """Enabled pipeline, rag, and mcp rules, plus the names of actions the plan omits."""
    drafts: list[dict[str, Any]] = []
    unsupported: list[str] = []
    for policy in policies:
        if not policy.get("enabled", True):
            continue
        domain = str(policy.get("policy_domain") or "")
        if domain not in _DOMAINS:
            continue
        for rule in policy.get("rules") or []:
            if not isinstance(rule, Mapping) or not rule.get("enabled", True):
                continue
            draft = _policy_rule(policy, rule, domain, unsupported)
            if draft is not None:
                drafts.append(draft)
    return drafts, unsupported


def publish_saved_plan(slug: str | None, payload: Mapping[str, Any]) -> dict[str, object]:
    """Write ``{rv2}:plan:{org}``. ``skipped`` and ``unavailable`` leave that key untouched."""
    org_id = gateway_org_for(slug or "", os.environ.get("AMF_V2_AUDIT_ORG_MAP", ""))
    if org_id is None:
        return {"status": "skipped", "reason": "org_not_mapped"}
    rows, unsupported = policy_drafts(_policy_rows(slug or ""))
    raw = document_bytes(org_id, payload, rows)
    try:
        _write(org_id, raw)
    except Exception:
        logger.exception("gateway-v2 plan was not written for org %s", org_id)
        return {"status": "unavailable", "org_id": org_id, "unsupported": unsupported}
    return {
        "status": "published",
        "org_id": org_id,
        "key": plan_key(org_id),
        "rules": raw.count(b'"rule_id"'),
        "unsupported": unsupported,
    }


def publish_for_org(org: Any) -> dict[str, object]:
    """Republish one organization's firewall drafts and enabled policy rules."""
    slug = getattr(org, "slug", None)
    return publish_saved_plan(slug, _firewall_payload(org))


def _policy_rule(
    policy: Mapping[str, Any],
    rule: Mapping[str, Any],
    domain: str,
    unsupported: list[str],
) -> dict[str, Any] | None:
    action = str(rule.get("action") or "").strip().lower()
    label = f"{policy.get('code')}/{rule.get('name') or rule.get('id')}"
    if action in _UNSUPPORTED_ACTIONS:
        unsupported.append(f"{label} ({action})")
        return None
    mode = "enforce"
    if action == "monitor":
        mode = "monitor"
        action = "flag"
    elif action not in {"allow", "flag", "redact", "block"}:
        unsupported.append(f"{label} ({action or 'unknown'})")
        return None
    condition = rule.get("condition") if isinstance(rule.get("condition"), Mapping) else {}
    surfaces = _surfaces(domain, condition)
    if not surfaces:
        return None
    scope = _scope(condition)
    built = _condition(rule, condition, scope)
    if built is None:
        return None
    return {
        "rule_id": f"pol-{policy.get('code')}-{rule.get('id')}",
        "category": _category(policy.get("category")),
        "scope": scope,
        "action": action,
        "mode": mode,
        "on_unavailable": "fail_closed",
        "threshold": None,
        "priority": _priority(rule, policy),
        "surfaces": surfaces,
        "condition": built,
    }


def _surfaces(domain: str, condition: Mapping[str, Any]) -> list[str]:
    raw = condition.get("surfaces")
    if not isinstance(raw, list) or not raw:
        return list(_DEFAULT_SURFACES[domain])
    names = [str(item).strip().lower() for item in raw]
    if "global" in names:
        return list(_ALL_SURFACES)
    return [name for name in names if name in _ALL_SURFACES]


def _scope(condition: Mapping[str, Any]) -> str:
    field = str(condition.get("field") or condition.get("direction") or "both").strip().lower()
    if field in {"prompt", "input"}:
        return "input"
    if field in {"response", "output"}:
        return "output"
    return "both"


def _condition(
    rule: Mapping[str, Any],
    condition: Mapping[str, Any],
    scope: str,
) -> dict[str, Any] | None:
    kind = str(rule.get("rule_type") or "keywords")
    keywords = condition.get("keywords") if isinstance(condition.get("keywords"), list) else []
    words = tuple(str(item) for item in keywords if isinstance(item, str) and item)
    pattern = str(condition.get("regex") or condition.get("pattern") or "")
    detector = str(condition.get("detector_class") or "")
    stage = str(rule.get("pipeline_stage") or "")
    if stage not in _STAGES:
        stage = ""
    if kind == "keywords" and not words:
        return None
    if kind in {"regex", "pattern"} and not pattern.strip():
        return None
    if kind == "detector" and not detector.strip():
        return None
    if kind not in {"keywords", "regex", "pattern", "detector"}:
        return None
    return {
        "kind": kind,
        "field": str(condition.get("field") or ""),
        "direction": scope,
        "keywords": list(words),
        "pattern": pattern if kind in {"regex", "pattern"} else "",
        "detector_class": detector if kind == "detector" else "",
        "pipeline_stage": stage,
    }


def _priority(rule: Mapping[str, Any], policy: Mapping[str, Any]) -> int:
    for value in (rule.get("priority"), policy.get("priority")):
        if isinstance(value, int) and not isinstance(value, bool):
            return value
    return 200


def _category(value: object) -> str:
    key = str(value or "").strip().lower().replace(" ", "_")
    return _CATEGORIES.get(key, "exfil")


def _policy_rows(slug: str) -> list[dict[str, Any]]:
    try:
        from policy.models import Policy

        rows = Policy.objects.filter(
            organization__slug=slug,
            enabled=True,
            policy_domain__in=_DOMAINS,
        ).prefetch_related("rules")
        return [_policy_record(policy) for policy in rows]
    except Exception:
        logger.exception("policy rules were not loaded for %s", slug)
        return []


def _policy_record(policy: Any) -> dict[str, Any]:
    return {
        "code": policy.code,
        "policy_domain": policy.policy_domain,
        "enabled": policy.enabled,
        "category": policy.category,
        "priority": policy.priority,
        "rules": [
            {
                "id": rule.id,
                "name": rule.name,
                "enabled": rule.enabled,
                "rule_type": rule.rule_type,
                "action": rule.action,
                "priority": rule.priority,
                "pipeline_stage": rule.pipeline_stage,
                "condition": rule.condition if isinstance(rule.condition, dict) else {},
            }
            for rule in policy.rules.all()
        ],
    }


def _firewall_payload(org: Any) -> dict[str, Any]:
    try:
        from core.firewall_config_serializer import FirewallConfigSerializer
        from core.models import FirewallConfig
    except Exception:
        logger.exception("firewall config was not loaded")
        return {"firewall_enabled": False}
    row = FirewallConfig.objects.filter(organization=org).first()
    if row is None:
        return {"firewall_enabled": False}
    return FirewallConfigSerializer(row).data


def _write(org_id: str, raw: bytes) -> None:
    import redis
    from django.conf import settings

    client = redis.Redis.from_url(settings.REDIS_URL, socket_timeout=2, socket_connect_timeout=2)
    try:
        client.set(plan_key(org_id), raw)
    finally:
        client.close()
