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
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from core.firewall_v2_controls import (
    REQUIRES_CONTROLS,
    controls_eligible,
    controls_for,
    keywords_of,
    new_rules_mode,
    threat_drafts,
)
from core.firewall_v2_map import (
    BUILTIN_PRIORITY,
    REWRITE_TEMPLATES,
    drafts_for,
    org_listed,
    retired_unchanged,
    rewrite_served_by_v2,
    surface_drafts,
)
from core.firewall_v2_map import _setting as _deployment_setting
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
# GW33: ``rewrite`` is built (template-driven, gated per org by ``rewrite_served_by_v2``).
# ``model_downgrade`` selects a model; routing is GW28's, so it stays listed as unsupported.
_UNSUPPORTED_ACTIONS = {"model_downgrade"}
_UNSUPPORTED_OWNER = {"model_downgrade": "GW28"}
#: Detector families whose bytes cannot be held in INCREMENTAL (D-9); a byte-altering or blocking
#: action on one of them is promoted to ``strict_withhold`` (D-20), and the promotion is reported.
_TEMPLATE_MAX = 128  # mirrors gateway_v2 REWRITE_TEMPLATE_MAX_CHARS; the compiler stays the authority
_STRICT_FAMILIES = ("output.exfil", "output.ip_leak", "output.grounding")
_BYTE_ALTERING = {"redact", "rewrite", "block"}
_EXFIL_ACTIONS = {"flag", "redact", "rewrite", "block"}


#: The gateway's reserved audit stream for failed credentials (GW35 Req 4.3/4.4). Mapping it to a
#: slug routes those records to that org's console; it is never a gateway org, so it must not make
#: the slug's dev-plan alias ambiguous.
_UNAUTHENTICATED_STREAM = "_unauthenticated"


def gateway_org_for(slug: str, raw_map: str) -> str | None:
    """The one gateway org mapped to this console slug. Ambiguous maps return none."""
    matches = [
        source
        for source, target in parse_org_map(raw_map).items()
        if target == slug and source != _UNAUTHENTICATED_STREAM
    ]
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


def streaming_mode_for(rules: Sequence[Mapping[str, Any]], strict_canary: bool = False) -> tuple[str, list[str]]:
    """``(mode, promoted rule ids)`` (D-20). Keyed on the detector class, never on the category.

    A rule that selects a built-in exfil / ip_leak / grounding detector with a byte-altering or
    blocking action cannot be held in INCREMENTAL, so the org is promoted to ``strict_withhold``
    (the latency cost is the documented price of enforcement). A canary rule promotes only for orgs
    in ``V2_CANARY_STRICT_ORGS`` (D-26). Customer rows never promote, whatever their category.
    """
    promoted: list[str] = []
    for row in rules:
        condition = row.get("condition")
        klass = str(condition.get("detector_class", "")) if isinstance(condition, Mapping) else ""
        if row.get("mode") == "off" or str(row.get("action")) not in _BYTE_ALTERING:
            continue
        if row.get("scope") not in {"output", "both"}:
            continue
        if klass.startswith(_STRICT_FAMILIES) or (strict_canary and klass == "output.canary"):
            promoted.append(str(row.get("rule_id")))
    return ("strict_withhold" if promoted else "incremental"), promoted


def platform_rows(org_slug: str | None) -> list[dict[str, Any]]:
    """Deployment-driven built-in rules, appended AFTER the customer-row priority stamping (D-32).

    Default-off: with the settings at their defaults no org receives exfil or canary protection,
    exactly as before this card. ``out_exfil`` (D-29) is emitted for orgs in ``V2_EXFIL_ORGS``;
    ``out_canary`` (D-11) for orgs in ``V2_CANARY_ORGS``.
    """
    rows: list[dict[str, Any]] = []
    if org_listed("V2_EXFIL_ORGS", org_slug):
        action = _deployment_setting("V2_EXFIL_ACTION").strip().lower()
        action = action if action in _EXFIL_ACTIONS else "block"
        hosts = [h.strip().lower() for h in _deployment_setting("V2_EXFIL_ALLOW_HOSTS").split(",")]
        rows.append(_platform_row("out_exfil", "exfil", "output.exfil", action,
                                  [h for h in hosts if h]))
    if org_listed("V2_CANARY_ORGS", org_slug):
        rows.append(_platform_row("out_canary", "canary", "output.canary", "block", []))
    return rows


def _platform_row(
    rule_id: str,
    category: str,
    detector_class: str,
    action: str,
    allow_hosts: list[str],
) -> dict[str, Any]:
    condition: dict[str, Any] = {
        "kind": "detector", "field": "", "direction": "output", "keywords": [], "pattern": "",
        "detector_class": detector_class, "pipeline_stage": "",
    }
    if allow_hosts:
        condition["allow_hosts"] = allow_hosts
    row: dict[str, Any] = {
        "rule_id": rule_id, "category": category, "scope": "output", "action": action,
        "mode": "enforce", "threshold": None, "priority": BUILTIN_PRIORITY, "surfaces": ["chat"],
        "on_unavailable": "fail_closed" if action in _BYTE_ALTERING else "fail_open",
        "condition": condition,
    }
    if action == "rewrite":
        row["rewrite_template"] = REWRITE_TEMPLATES[category]
    return row


def document_body(
    org_id: str,
    payload: Mapping[str, Any],
    policy_rows: Sequence[Mapping[str, Any]] = (),
    org_slug: str | None = None,
    *,
    controls_enabled: bool = False,
    rules_mode: str = "monitor",
) -> tuple[dict[str, Any], list[str]]:
    """The plan document and the rule ids D-20 promoted to ``strict_withhold``."""
    slug = org_slug if org_slug is not None else org_id
    # Customer/firewall rows get the default priority only when they carry none of their own, so
    # the built-in detector rows keep the explicit priority their emitter gave them (D-32).
    builtin = drafts_for(payload)
    threats = threat_drafts(payload, mode=rules_mode)
    rules = [{"priority": _PRIORITY, **row} for row in builtin]
    rules.extend({**row, "priority": _PRIORITY} for row in threats)
    # F4/F3: the same built-ins on the non-chat surfaces, behind the D-8 gate (chat rows unchanged).
    rules.extend(
        {"priority": _PRIORITY, **row} for row in surface_drafts([*builtin, *threats], rules_mode)
    )
    rules.extend(policy_rows)
    if payload.get("firewall_enabled") is not False:
        rules.extend(platform_rows(slug))
    mode, promoted = streaming_mode_for(
        rules, strict_canary=org_listed("V2_CANARY_STRICT_ORGS", slug),
    )
    body: dict[str, Any] = {"org_id": org_id, "rules": rules, "streaming_mode": mode}
    if controls_enabled:
        controls = controls_for(payload)
        if controls:
            body["controls"] = controls
            body["requires"] = [REQUIRES_CONTROLS]
    return body, promoted


def document_bytes(
    org_id: str,
    payload: Mapping[str, Any],
    policy_rows: Sequence[Mapping[str, Any]] = (),
    org_slug: str | None = None,
    *,
    controls_enabled: bool = False,
    rules_mode: str = "monitor",
) -> bytes:
    """Firewall drafts, threat rules, policy rules and (when enabled) the org ``controls``.

    An empty firewall list means the firewall is off. ``controls_enabled`` is the D-10 gate: the
    ``controls`` object and its ``requires`` marker are written only when the fleet gate is open
    for this org. ``rules_mode`` is the D-8 gate for the threat rows (``off|monitor|enforce``).
    """
    body, _promoted = document_body(
        org_id, payload, policy_rows, org_slug, controls_enabled=controls_enabled,
        rules_mode=rules_mode,
    )
    return json.dumps(body, separators=(",", ":"), sort_keys=True).encode("utf-8")
def keyword_row(payload: Mapping[str, Any], mode: str = "enforce") -> dict[str, Any] | None:
    """``blocked_keywords`` as one policy-shaped row for the existing ``_policy_rule`` path.

    Matching is the shipped matcher's: case-insensitive substring (``token`` also matches
    ``tokenizer``). Empty text, or only separators, yields no row. ``mode`` is the D-8 gate.
    """
    words = keywords_of(payload.get("blocked_keywords"))
    if not words or mode == "off":
        return None
    if not (payload.get("firewall_enabled", True) and payload.get("content_filtering_enabled", True)):
        return None
    monitor = mode == "monitor" or payload.get("enforcement_mode") == "monitor"
    action = "monitor" if monitor else "block"
    return {
        "code": "fw-blocked-keywords",
        "policy_domain": "pipeline",
        "enabled": True,
        "category": "exfil",
        "priority": _PRIORITY,
        "rules": [
            {
                "id": "keywords",
                "name": "blocked_keywords",
                "enabled": True,
                "rule_type": "keywords",
                "action": action,
                "priority": _PRIORITY,
                "pipeline_stage": "",
                "condition": {"keywords": words, "field": "input"},
            },
        ],
    }


def policy_drafts(
    policies: Sequence[Mapping[str, Any]],
    org_slug: str | None = None,
    *,
    output_enabled: bool = True,
    output_fallback: str = "flag",
    monitor: bool = False,
) -> tuple[list[dict[str, Any]], list[str]]:
    """Enabled pipeline, rag, and mcp rules, plus the names of actions the plan omits.

    ``org_slug`` gates a ``rewrite`` row (``rewrite_served_by_v2``); ``None`` is "not served".
    ``output_enabled`` and ``output_fallback`` are ``output_policy_enabled`` and
    ``output_policy_action`` (GW27, design section 10). Disabled: output-scope rules are dropped
    and both-scope rules keep their input half. Enabled: an output or both-scope rule whose own
    action is ``monitor`` or empty takes the fallback; an explicit own action wins. ``monitor`` is
    the global monitor posture; it forces ``mode=monitor`` on the rules the fallback decided.
    """
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
            draft = _policy_rule(
                policy, rule, domain, unsupported, org_slug,
                _OutputPolicy(output_enabled, output_fallback, monitor),
            )
            if draft is not None:
                drafts.append(draft)
    return drafts, unsupported


def publish_for_org(
    org: Any, *, publisher: str | None = None, notes: bool = True
) -> dict[str, object]:
    """Recompile one organization's document and store it. Returns the console's status note.

    Rebuilt INSIDE a row lock on the organization, from the rows as committed: two saves racing
    each publish the newest state, whichever commits last (a document built from a payload
    passed in could land older than the save it followed). The dev copy is written inside the
    same lock, so it lands in the same order as the stored documents. ``unavailable`` leaves the
    stored document as it was, and the projector keeps serving it.

    The document is stamped with ``publisher``, the fingerprint of the running publisher
    (``core.v2_plan_rollout.fingerprint``; computed here when not passed in), so the start-up
    rollout can tell which documents an older release or other settings built. ``notes=False``
    skips the console status note (a second config query and the compliance validation): the
    rollout discards it.
    """
    if org is None or not getattr(org, "pk", None):
        return {"status": "skipped", "reason": "no_org"}
    from auth.models import Organization
    from django.db import transaction

    try:
        if publisher is None:
            from core.v2_plan_rollout import fingerprint

            publisher = fingerprint()
        with transaction.atomic():
            locked = Organization.objects.select_for_update().get(pk=org.pk)
            payload = _firewall_payload(locked)
            eligible = controls_eligible(locked.slug)
            rules_mode = new_rules_mode()
            rows, unsupported = _drafts_for_org(locked.slug, payload, rules_mode)
            body, promoted = document_body(
                locked.slug, payload, rows, locked.slug, controls_enabled=eligible,
                rules_mode=rules_mode,
            )
            raw = json.dumps(body, separators=(",", ":"), sort_keys=True).encode("utf-8")
            revision = _store(locked, raw, publisher)
            dev_copy = _dev_copy(locked.slug, payload, rows, eligible, rules_mode)
    except Exception:
        logger.exception("gateway-v2 plan document was not stored for org %s", org.pk)
        return {"status": "unavailable", "org_id": getattr(org, "slug", None)}
    note: dict[str, object] = {
        "status": "published",
        "org_id": locked.slug,
        "revision": revision,
        "rules": raw.count(b'"rule_id"'),
        "unsupported": unsupported,
        "streaming_mode_promoted": promoted,
        "dev_copy": dev_copy,
    }
    if notes:
        note.update(_notes(locked, payload, eligible, rules_mode))
    return note


def _drafts_for_org(
    slug: str, payload: Mapping[str, Any], rules_mode: str
) -> tuple[list[dict[str, Any]], list[str]]:
    """Policy rows plus the keyword row, with the output-policy fields applied (GW27)."""
    return drafts_from_policies(slug, payload, rules_mode, _policy_rows(slug))


def drafts_from_policies(
    slug: str,
    payload: Mapping[str, Any],
    rules_mode: str,
    policies: Sequence[Mapping[str, Any]],
) -> tuple[list[dict[str, Any]], list[str]]:
    """:func:`_drafts_for_org` for policy records already read (the rollout's probe documents)."""
    policies = list(policies)
    keyword = keyword_row(payload, rules_mode)
    if keyword is not None:
        policies.append(keyword)
    fallback = str(payload.get("output_policy_action") or "flag").strip().lower()
    return policy_drafts(
        policies,
        slug,
        output_enabled=bool(payload.get("output_policy_enabled", True)),
        output_fallback=fallback,
        monitor=payload.get("enforcement_mode") == "monitor",
    )
def _notes(
    org: Any, payload: Mapping[str, Any], eligible: bool, rules_mode: str
) -> dict[str, object]:
    """The honest parts of the console status note: scope, semantics, withheld and retired."""
    notes: dict[str, object] = {
        "controls": "published" if eligible else "controls_withheld",
        "new_rules_mode": rules_mode,
        "org_limits_scope": "per gateway replica",
        "tpm_basis": "estimated tokens at admission; no response true-up",
        "keywords": "case-insensitive substring match",
        "retention": "stamped at write time; not retroactive; records without a stamp are kept",
        "threat_intel": "content-score floor; no actor reputation feed in v2",
        "retired_unchanged": retired_unchanged(payload),
    }
    try:
        from core.compliance import validate_compliance_requirements
        from core.models import FirewallConfig

        row = FirewallConfig.objects.filter(organization=org).first()
        notes["compliance_violations"] = (
            validate_compliance_requirements(row) if row is not None else []
        )
    except Exception:
        logger.exception("compliance violations were not computed for org %s", org.pk)
        notes["compliance_violations"] = "unavailable"
    return notes
def _store(org: Any, raw: bytes, publisher: str = "") -> int:
    """Upsert the document; the revision rises only when the body changed.

    ``publisher`` is stamped either way: a body another release or other settings would also
    build keeps its revision, and only its stamp moves to the running publisher.
    """
    from core.models import V2PlanDocument

    text = raw.decode("utf-8")
    document = V2PlanDocument.objects.filter(organization=org).first()
    if document is None:
        return V2PlanDocument.objects.create(
            organization=org, body=text, revision=1, publisher=publisher,
        ).revision
    if document.body != text:
        document.body = text
        document.revision += 1
        document.publisher = publisher
        document.save(update_fields=["body", "revision", "publisher", "updated_at"])
    elif document.publisher != publisher:
        V2PlanDocument.objects.filter(pk=document.pk).update(publisher=publisher)
    return document.revision


def _dev_copy(
    slug: str,
    payload: Mapping[str, Any],
    rows: Sequence[Mapping[str, Any]],
    controls_enabled: bool = False,
    rules_mode: str = "monitor",
) -> str:
    alias = gateway_org_for(slug, os.environ.get("AMF_V2_AUDIT_ORG_MAP", ""))
    if alias is None:
        return "not_mapped"
    try:
        _write(
            alias,
            document_bytes(
                alias, payload, rows, slug, controls_enabled=controls_enabled,
                rules_mode=rules_mode,
            ),
        )
    except Exception:
        logger.exception("gateway-v2 dev plan copy was not written for %s", alias)
        return "unavailable"
    return "written"


@dataclass(frozen=True)
class _OutputPolicy:
    """The ``output_policy_*`` fields and the global monitor posture, for one publish."""

    enabled: bool = True
    fallback: str = "flag"
    monitor: bool = False
_ACTIONS = {"allow", "flag", "redact", "block", "rewrite"}
_FALLBACK_ACTIONS = _ACTIONS - {"rewrite"}  # output_policy_action never takes rewrite (no template)
_DEFAULT_OUTPUT = _OutputPolicy()
def _output_action(own: str, fallback: str, monitor: bool) -> tuple[str, str] | None:
    """(action, mode) for an output or both-scope rule per the section 10 table, or None."""
    chosen = fallback if fallback in _FALLBACK_ACTIONS else "flag"
    if own in _ACTIONS:
        action, mode = own, "enforce"
    elif own == "monitor":
        action, mode = (chosen, "enforce") if chosen != "allow" else ("flag", "monitor")
    elif own == "":
        action, mode = chosen, "enforce"
    else:
        return None
    return action, "monitor" if monitor else mode
def _policy_rule(
    policy: Mapping[str, Any],
    rule: Mapping[str, Any],
    domain: str,
    unsupported: list[str],
    org_slug: str | None = None,
    output: _OutputPolicy = _DEFAULT_OUTPUT,
) -> dict[str, Any] | None:
    action = str(rule.get("action") or "").strip().lower()
    label = f"{policy.get('code')}/{rule.get('name') or rule.get('id')}"
    if action in _UNSUPPORTED_ACTIONS:
        unsupported.append(f"{label} ({action}): owner {_UNSUPPORTED_OWNER[action]}")
        return None
    template = ""
    if action == "rewrite":
        template = _row_template(policy, rule)
        if not rewrite_served_by_v2(org_slug) or not template:
            reason = "no template" if not template else "not enabled for this org"
            unsupported.append(f"{label} (rewrite: {reason})")
            return None
    condition = rule.get("condition") if isinstance(rule.get("condition"), Mapping) else {}
    surfaces = _surfaces(domain, condition)
    if not surfaces:
        return None
    scope = _scope(condition)
    mode = "enforce"
    if scope in {"output", "both"} and not output.enabled:
        if scope == "output":
            return None
        scope = "input"
    elif scope in {"output", "both"}:
        decided = _output_action(action, output.fallback, output.monitor)
        if decided is None:
            unsupported.append(f"{label} ({action})")
            return None
        action, mode = decided
    if scope == "input" or action not in _ACTIONS:
        if action == "monitor":
            mode = "monitor"
            action = "flag"
        elif action not in _ACTIONS:
            unsupported.append(f"{label} ({action or 'unknown'})")
            return None
    built = _condition(rule, condition, scope)
    if built is None:
        if str(condition.get("detector_class") or "").startswith("output."):
            # D-28: the `output.` detector namespace is the platform's; a customer row cannot
            # occupy it, so it is reported (loud) instead of being published.
            unsupported.append(f"{label} (reserved detector class)")
        return None
    row = {
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
    if template:
        row["rewrite_template"] = template
    return row


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
    if detector.strip().startswith("output."):
        return None  # reserved platform namespace (D-28): reported by the caller
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
    """Policy-management category to plan category.

    The ``"exfil"`` fallback is a LEGACY CATCH-ALL (GW26): a row whose category is not in
    ``_CATEGORIES`` lands there. It is kept because changing it would change the ``content_hash``
    of every already-published plan with such a row. Nothing keys on this label any more: the
    built-in output detectors are selected by detector identity (D-28), so a catch-all row is
    never promoted to strict, never builds a detector, and cannot govern a built-in finding.
    """
    key = str(value or "").strip().lower().replace(" ", "_")
    return _CATEGORIES.get(key, "exfil")


_TEMPLATE_FORBIDDEN = ("://", "![", "](", "<", ">", "@", "www.")


def _matches_own_pattern(condition: Mapping[str, Any], text: str) -> bool:
    pattern = str(condition.get("regex") or condition.get("pattern") or "")
    if not pattern:
        return False
    try:
        return re.search(pattern, text) is not None
    except re.error:
        return True  # an unusable pattern: do not forward a row template


def _row_template(policy: Mapping[str, Any], rule: Mapping[str, Any]) -> str:
    """The row's own template when it is demonstrably safe, else the platform one for its category.

    The plan compiler is the authority and refuses anything unsafe; a refusal would reject the
    WHOLE document, so the publisher only forwards a row template that already passes the same
    conservative syntactic rules and does not contain the rule's own keywords. Otherwise the
    platform table entry is used (``""`` when the category has none).
    """
    own = str(rule.get("rewrite_template") or "").strip()
    condition = rule.get("condition") if isinstance(rule.get("condition"), Mapping) else {}
    keywords = [str(k).casefold() for k in (condition.get("keywords") or []) if isinstance(k, str)]
    neutral = own.replace("{category}", "X").replace("{rule_id}", "X")
    safe = (
        bool(own)
        and len(own) <= _TEMPLATE_MAX
        and "{" not in neutral
        and "}" not in neutral
        and not any(token in neutral for token in _TEMPLATE_FORBIDDEN)
        and re.search(r"[A-Za-z0-9]\.[A-Za-z0-9]|\d\.\d|[A-Za-z0-9+/=_-]{16,}", neutral) is None
        and not any(word in own.casefold() for word in keywords)
        and not _matches_own_pattern(condition, own)
    )
    if safe:
        return own
    key = _category(policy.get("category"))
    return REWRITE_TEMPLATES.get(key, REWRITE_TEMPLATES["policy"])


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
