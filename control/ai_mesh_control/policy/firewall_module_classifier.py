"""
Firewall module event classification — backend source of truth for KPI/trend/chart bucketing.

Aligned with frontend MODULE_FILTERS in firewall-module-utils.js.
Module 1.1 is the gateway intake superset (all events); 1.2–1.7 are overlapping specialty lenses.
"""

from __future__ import annotations

from django.db.models import Q

from policy.module_16 import is_module_16_enforcement, module_16_enforcement_q

MODULE_IDS = ("1.1", "1.2", "1.3", "1.4", "1.5", "1.6", "1.7")

# OR-semantics per field group — keep in sync with frontend MODULE_FILTERS
MODULE_FILTER_SPECS: dict[str, dict] = {
    "1.1": {},
    "1.2": {
        "event_types": ["rag_pipeline"],
        "threat_types": ["data_leakage", "pii", "rag_poisoning"],
        "owasp_prefixes": ["LLM06", "LLM08"],
    },
    "1.3": {
        "event_types": ["rag_pipeline", "embedding_request", "vector_query"],
        "threat_types": ["rag_poisoning"],
        "owasp_prefixes": ["LLM08"],
    },
    "1.4": {"sources": ["mcp_scan"], "owasp_prefixes": ["MCP"]},
    "1.5": {
        "sources": ["routing", "agentic_scan"],
        "event_types": ["model_routed"],
        "owasp_prefixes": ["AGENTIC"],
    },
    "1.6": {"module_16": True},
    "1.7": {"event_types": ["output_guard", "output_scan"]},
}

MODULE_PRESSURE_METRIC: dict[str, str] = {
    "1.1": "blocked",
    "1.2": "blocked",
    "1.3": "blocked",
    "1.4": "redacted",
    "1.5": "blocked",
    "1.6": "critical",
    "1.7": "blocked",
}

CRITICAL_THRESHOLD = 80


def _norm_source(meta: dict | None, source: str = "") -> str:
    if source:
        return str(source).lower()
    if not meta:
        return ""
    return str(meta.get("source") or "").lower()


def _norm_fields(meta: dict | None) -> tuple[str, str, str, str]:
    meta = meta or {}
    owasp = str(meta.get("owasp_code") or "").strip().upper()
    threat_type = str(meta.get("threat_type") or "").lower()
    event_type = str(meta.get("event_type") or "").lower()
    module_id = str(meta.get("module_id") or meta.get("module") or "")
    return owasp, threat_type, event_type, module_id


def event_matches_module(module_id: str, meta: dict | None, *, source: str = "") -> bool:
    """Return True when enforcement metadata belongs to a specialty module lane."""
    if module_id == "1.1":
        return True

    spec = MODULE_FILTER_SPECS.get(module_id, {})
    if not spec:
        return False

    if spec.get("module_16"):
        return is_module_16_enforcement(meta)

    owasp, threat_type, event_type, stamped_module = _norm_fields(meta)
    src = _norm_source(meta, source)

    if stamped_module == module_id:
        return True

    if spec.get("sources") and any(src == str(s).lower() for s in spec["sources"]):
        return True
    if spec.get("event_types") and any(event_type == str(et).lower() for et in spec["event_types"]):
        return True
    if spec.get("threat_types") and any(threat_type == str(tt).lower() for tt in spec["threat_types"]):
        return True
    if spec.get("owasp_prefixes") and owasp:
        if any(owasp.startswith(str(p).upper()) for p in spec["owasp_prefixes"]):
            return True

    return False


def specialty_modules_for_event(meta: dict | None, *, source: str = "") -> list[str]:
    """Return specialty module ids (1.2–1.7) matching this event."""
    matched: list[str] = []
    for mid in MODULE_IDS:
        if mid == "1.1":
            continue
        if event_matches_module(mid, meta, source=source):
            matched.append(mid)
    return matched


def module_enforcement_q(module_id: str) -> Q:
    """Django Q filter for ModuleChartsView / queryset scoping."""
    if module_id == "1.1":
        return Q()

    spec = MODULE_FILTER_SPECS.get(module_id, {})
    if not spec:
        return Q(pk__in=[])

    if spec.get("module_16"):
        return module_16_enforcement_q()

    q = Q()
    for src in spec.get("sources", []):
        q |= Q(metadata__source=src)
    for prefix in spec.get("owasp_prefixes", []):
        q |= Q(metadata__owasp_code__startswith=prefix)
    for tt in spec.get("threat_types", []):
        q |= Q(metadata__threat_type=tt)
    for et in spec.get("event_types", []):
        q |= Q(metadata__event_type=et)
    q |= Q(metadata__module_id=module_id) | Q(metadata__module=module_id)

    return q


def empty_bucket() -> dict:
    return {"total": 0, "blocked": 0, "redacted": 0, "critical": 0}


def increment_bucket(
    bucket: dict,
    *,
    is_blocked: bool,
    is_redacted: bool,
    is_critical: bool,
) -> None:
    bucket["total"] += 1
    if is_blocked:
        bucket["blocked"] += 1
    if is_redacted:
        bucket["redacted"] += 1
    if is_critical:
        bucket["critical"] += 1


def bucket_pressure(bucket: dict, module_id: str) -> int:
    metric = MODULE_PRESSURE_METRIC.get(module_id, "blocked")
    return int(bucket.get(metric, 0) or 0)
