"""Publish an organization's gateway-v2 plan document.

The document is the JSON shape ``plan.document`` decodes; this module does not
import gateway_v2. It is stored in ``core_v2_plan_document`` with the org slug
as ``org_id`` (GW36 S2), where the state projector signs it into the PLAN
record every gateway loads. Control never writes ``{rv2}:plan:*``: that key
belongs to the signed feed.

For an org mapped by ``AMF_V2_AUDIT_ORG_MAP`` a dev copy, with the mapped
alias as ``org_id``, also goes to ``{rv2}:devplan:{alias}`` for the dev
profile. A failed dev write never fails the save.
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
    """The dev profile's per-org plan key. The dev key stays a fallback and is not written here.

    Distinct from the signed feed's ``{rv2}:plan:{org}`` so no reader ever sees two formats
    under one key.
    """
    return f"{{rv2}}:devplan:{org_id}"


LEGACY_PLAN_PATTERN = "{rv2}:plan:*"
"""Where control wrote the unsigned document before GW36 S2. Purged by
``sync_v2_plan_documents --purge-legacy-plan-keys`` (raw documents only)."""


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


def publish_for_org(org: Any) -> dict[str, object]:
    """Recompile one organization's document and store it. Returns the console's status note.

    Rebuilt INSIDE a row lock on the organization, from the rows as committed: two saves racing
    each publish the newest state, whichever commits last (a document built from a payload
    passed in could land older than the save it followed). ``unavailable`` leaves the stored
    document as it was, and the projector keeps serving it.
    """
    if org is None or not getattr(org, "pk", None):
        return {"status": "skipped", "reason": "no_org"}
    from django.db import transaction

    from auth.models import Organization

    try:
        with transaction.atomic():
            locked = Organization.objects.select_for_update().get(pk=org.pk)
            payload = _firewall_payload(locked)
            rows, unsupported = policy_drafts(_policy_rows(locked.slug))
            raw = document_bytes(locked.slug, payload, rows)
            revision = _store(locked, raw)
    except Exception:
        logger.exception("gateway-v2 plan document was not stored for org %s", org.pk)
        return {"status": "unavailable", "org_id": getattr(org, "slug", None)}
    return {
        "status": "published",
        "org_id": locked.slug,
        "revision": revision,
        "rules": raw.count(b'"rule_id"'),
        "unsupported": unsupported,
        "dev_copy": _dev_copy(locked.slug, payload, rows),
    }


def _store(org: Any, raw: bytes) -> int:
    """Upsert the document; the revision rises only when the body changed."""
    from core.models import V2PlanDocument

    text = raw.decode("utf-8")
    document = V2PlanDocument.objects.filter(organization=org).first()
    if document is None:
        return V2PlanDocument.objects.create(organization=org, body=text, revision=1).revision
    if document.body != text:
        document.body = text
        document.revision += 1
        document.save(update_fields=["body", "revision", "updated_at"])
    return document.revision


def _dev_copy(
    slug: str,
    payload: Mapping[str, Any],
    rows: Sequence[Mapping[str, Any]],
) -> str:
    alias = gateway_org_for(slug, os.environ.get("AMF_V2_AUDIT_ORG_MAP", ""))
    if alias is None:
        return "not_mapped"
    try:
        _write(alias, document_bytes(alias, payload, rows))
    except Exception:
        logger.exception("gateway-v2 dev plan copy was not written for %s", alias)
        return "unavailable"
    return "written"


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
    """Raises on failure: a document missing the org's policies must never be stored."""
    from policy.models import Policy

    rows = Policy.objects.filter(
        organization__slug=slug,
        enabled=True,
        policy_domain__in=_DOMAINS,
    ).prefetch_related("rules")
    return [_policy_record(policy) for policy in rows]


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
    """The org's saved config, or the model defaults when it never saved one.

    The defaults are the platform default plan (GW36 §9.3) and match v1, where
    ``FirewallConfig.load`` creates the row with them. This used to publish
    "firewall off" for an org with no row, and also when the import failed.
    """
    from core.firewall_config_serializer import FirewallConfigSerializer
    from core.models import FirewallConfig

    row = FirewallConfig.objects.filter(organization=org).first()
    if row is None:
        row = FirewallConfig(organization=org)
    return dict(FirewallConfigSerializer(row).data)


def _write(org_id: str, raw: bytes) -> None:
    import redis
    from django.conf import settings

    client = redis.Redis.from_url(settings.REDIS_URL, socket_timeout=2, socket_connect_timeout=2)
    try:
        client.set(plan_key(org_id), raw)
    finally:
        client.close()


def is_raw_document(value: bytes | str | None) -> bool:
    """A pre-GW36 unsigned document, not a signed feed envelope (which carries ``sig``)."""
    if value is None:
        return False
    try:
        parsed = json.loads(value)
    except (ValueError, UnicodeDecodeError):
        return False
    return isinstance(parsed, dict) and "rules" in parsed and "sig" not in parsed
