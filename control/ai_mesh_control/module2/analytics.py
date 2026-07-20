"""Aggregation helpers for Module 2 exposure, threat telemetry, and incidents."""

from __future__ import annotations

import logging
import re
from collections import Counter, defaultdict
from datetime import timedelta

from django.db.models import Q
from django.utils import timezone

from policy.constants import ACTION_BLOCK, ACTION_MONITOR, ACTION_REDACT
from policy.request_scoped_metrics import (
    collapse_events_by_request,
    iter_rows_from_queryset,
    merge_request_action,
    request_key,
)
from module2.display_sanitization import sanitize_incident_text

logger = logging.getLogger(__name__)

ROUTING_EVENT_Q = (
    Q(metadata__source="routing")
    | Q(metadata__event_type="model_routed")
    | Q(metadata__extra__source="routing")
)
REROUTED_FLAG_Q = Q(metadata__rerouted=True) | Q(metadata__extra__rerouted=True)

INJECTION_KEYWORDS = (
    "injection",
    "jailbreak",
    "prompt_attack",
    "llm01",
    "llm02",
)
PII_KEYWORDS = (
    "pii",
    "ssn",
    "credit_card",
    "phi",
    "gdpr",
    "redact",
)


def key_prefix_from_meta(meta: dict) -> str:
    return str(meta.get("key_prefix") or meta.get("api_key_prefix") or "").strip()


def prefixes_match(stored_prefix: str, meta_prefix: str) -> bool:
    """Case-insensitive API key prefix comparison."""
    left = str(stored_prefix or "").strip().lower()
    right = str(meta_prefix or "").strip().lower()
    return bool(left) and left == right


def prompt_snippet_from_meta(meta: dict | None, max_len: int = 200) -> str:
    """Extract a display-safe prompt preview from enforcement metadata."""
    data = meta or {}
    for field in (
        "prompt_snippet",
        "prompt_submitted",
        "original_prompt",
        "user_prompt",
        "input_preview",
        "input_text",
        "forwarded_prompt",
    ):
        direct = str(data.get(field) or "").strip()
        if direct:
            return direct[:max_len]

    lineage = data.get("prompt_lineage") or []
    if isinstance(lineage, list):
        for entry in lineage:
            if not isinstance(entry, dict):
                continue
            prompt = str(entry.get("prompt") or entry.get("text") or "").strip()
            if prompt:
                return prompt[:max_len]

    extra = data.get("extra")
    if isinstance(extra, dict):
        for field in (
            "prompt_snippet",
            "prompt_submitted",
            "prompt",
            "user_message",
            "query",
            "original_prompt",
            "input_preview",
        ):
            value = str(extra.get(field) or "").strip()
            if value:
                return value[:max_len]

    fallback = str(data.get("intent") or data.get("detail") or _meta_detail(data) or "").strip()
    return fallback[:max_len]


_USER_TURN_RE = re.compile(r"^\[user\]:\s*(.*)$", re.IGNORECASE | re.MULTILINE)


def event_prompt_from_meta(meta: dict | None, max_len: int = 500) -> str:
    """Return only the user turn that triggered this event (not prior chat turns)."""
    raw = prompt_snippet_from_meta(meta, max_len=10_000)
    if not raw:
        return ""

    user_turns = [m.group(1).strip() for m in _USER_TURN_RE.finditer(raw) if m.group(1).strip()]
    if user_turns:
        return user_turns[-1][:max_len]

    lines = [line.strip() for line in raw.split("\n") if line.strip()]
    for line in reversed(lines):
        if line.lower().startswith("[assistant]:"):
            continue
        user_match = re.match(r"^\[user\]:\s*(.*)$", line, re.IGNORECASE)
        if user_match:
            return user_match.group(1).strip()[:max_len]
        return line[:max_len]

    return raw[:max_len]


def build_recent_request_json(ev: dict, max_snippet: int = 500) -> dict:
    """SOC-friendly JSON row for the last-N request panel."""
    meta = dict(ev.get("metadata") or {})
    snippet = event_prompt_from_meta(meta, max_len=max_snippet)
    risk_score = meta.get("security_risk_score")
    lineage = [{"prompt": snippet, "risk_score": risk_score}] if snippet else []
    return {
        "event_id": ev.get("id"),
        "timestamp": ev.get("created_at").isoformat() if ev.get("created_at") else None,
        "action": ev.get("action"),
        "endpoint_id": ev.get("endpoint_id"),
        "model": meta.get("model"),
        "threat_type": meta.get("threat_type"),
        "event_type": meta.get("event_type"),
        "request_lane": event_source(meta),
        "owasp_code": meta.get("owasp_code"),
        "intent": meta.get("intent"),
        "detail": meta.get("detail"),
        "prompt_snippet": snippet,
        "prompt_lineage": lineage,
        "metadata": {
            "key_prefix": key_prefix_from_meta(meta),
            "source": meta.get("source"),
            "context_source": meta.get("context_source"),
            "pipeline_stage": meta.get("pipeline_stage"),
            "security_risk_score": meta.get("security_risk_score"),
        },
    }


def _meta_detail(meta: dict) -> str:
    """Detail string from top-level metadata or nested gateway extra envelope."""
    extra = meta.get("extra")
    if isinstance(extra, dict):
        nested = str(extra.get("detail") or "").strip()
        if nested:
            return nested
    return str(meta.get("detail") or "").strip()


def _is_rag_event_type(event_type: str) -> bool:
    """Gateway emits rag_pipeline, rag_query, rag_query_blocked, rag_ingest_*, etc."""
    et = str(event_type or "").lower()
    return et == "rag_pipeline" or et.startswith("rag_")


def _is_mcp_event_type(event_type: str) -> bool:
    """Gateway emits mcp_tool_call, mcp_blocked, and other mcp_* telemetry classes."""
    et = str(event_type or "").lower()
    return et == "mcp_tool_call" or et.startswith("mcp_")


def event_source(meta: dict) -> str:
    """Primary M2 dashboard lane (chat/rag/vector/mcp/threat_intel).

    SDK ``mcp_context`` injection stays **chat** lane; use ``metadata.context_source=mcp``
    to trace MCP-injected context separately from ``mcp_tool_call`` tool traffic.
    """
    event_type = str(meta.get("event_type") or "").lower()
    detail = _meta_detail(meta).lower()
    src = str(meta.get("source") or "").lower()
    threat_type = str(meta.get("threat_type") or "").lower()
    if "threat intel" in detail or "threat_intel" in src or threat_type.startswith("threat_intel"):
        return "threat_intel"
    if _is_mcp_event_type(event_type) or _looks_like_mcp_event(meta):
        return "mcp"
    if _is_rag_event_type(event_type):
        return "rag"
    if meta.get("collection") or meta.get("vector_collection") or meta.get("vector_namespace"):
        return "vector"
    return "chat"


def _looks_like_mcp_event(meta: dict) -> bool:
    """Best-effort MCP discriminator for legacy envelopes without event_type."""
    if meta.get("tools_invoked"):
        return True
    if meta.get("mcp_server") or meta.get("server_slug"):
        return True
    direction = str(meta.get("mcp_direction") or meta.get("scan_direction") or "").lower()
    return direction in {"inbound", "outbound"}


def classify_telemetry_bucket(meta: dict, action: str) -> str:
    """Map enforcement metadata to telemetry series keys.

    Threat-type buckets (injection, PII, IOC) take priority over generic key-attributed
    traffic so M2.3 KPI cards reflect full gateway enforcement, not IOC-only or
    behavior-only slices.
    """
    threat = str(meta.get("threat_type") or "").lower()
    category = str(meta.get("category") or meta.get("violation_type") or "").lower()
    combined = f"{threat} {category}"
    detail = _meta_detail(meta).lower()

    if event_source(meta) == "threat_intel":
        return "threat_intel_matches"
    if any(k in combined for k in INJECTION_KEYWORDS) or "injection" in detail or "jailbreak" in detail:
        return "injection_attempts"
    if action in (ACTION_BLOCK, ACTION_REDACT) and (
        any(k in combined for k in PII_KEYWORDS) or "pii" in detail
    ):
        return "pii_leaks"
    # Key-attributed traffic without injection/PII/IOC threat feeds M2.2 UEBA volume.
    if key_prefix_from_meta(meta):
        return "behavior_scoring"
    return "other"


def attack_vector_key(meta: dict) -> str:
    owasp = str(meta.get("owasp_code") or "").strip()
    if owasp:
        return owasp
    threat = str(meta.get("threat_type") or "unknown").strip()
    return threat or "unknown"


def hours_from_period(period: str) -> int:
    return {"1h": 1, "24h": 24, "7d": 168, "30d": 720}.get((period or "24h").lower(), 24)


def is_routing_event_metadata(meta: dict) -> bool:
    """True when enforcement metadata represents model-routing telemetry."""
    if not isinstance(meta, dict):
        return False
    source = str(meta.get("source") or "").lower()
    event_type = str(meta.get("event_type") or "").lower()
    if source == "routing" or event_type == "model_routed":
        return True
    extra = meta.get("extra")
    return isinstance(extra, dict) and str(extra.get("source") or "").lower() == "routing"


def is_rerouted_metadata(meta: dict) -> bool:
    """True when routing metadata captured a model reroute decision."""
    if not isinstance(meta, dict):
        return False
    if meta.get("rerouted") is True:
        return True
    extra = meta.get("extra")
    return isinstance(extra, dict) and extra.get("rerouted") is True


def count_monitored_events(events_qs) -> int:
    rows = iter_rows_from_queryset(events_qs, fields=("action", "metadata"))
    return sum(1 for item in collapse_events_by_request(rows) if item.had_monitor)


def count_rerouted_events(events_qs) -> int:
    rows = iter_rows_from_queryset(events_qs, fields=("action", "metadata"))
    return sum(1 for item in collapse_events_by_request(rows) if item.had_reroute)


def build_time_buckets(hours: int, since):
    bucket_hours = 1 if hours <= 24 else (6 if hours <= 168 else 24)
    count = max(hours // bucket_hours, 1)
    return [
        (
            since + timedelta(hours=i * bucket_hours),
            since + timedelta(hours=(i + 1) * bucket_hours),
        )
        for i in range(count)
    ]


def build_model_exposure_payload(events: list[dict], llm_map: dict[str, str], period: str) -> dict:
    stats = defaultdict(
        lambda: {
            "requests": 0,
            "blocked": 0,
            "redacted": 0,
            "latencies": [],
            "keys": set(),
        }
    )

    for item in collapse_events_by_request(events):
        meta = item.metadata or {}
        model = str(meta.get("model") or "unknown")
        stats[model]["requests"] += 1
        if item.action == "block":
            stats[model]["blocked"] += 1
        if item.action == "redact":
            stats[model]["redacted"] += 1
        if meta.get("latency_ms") is not None:
            try:
                stats[model]["latencies"].append(float(meta["latency_ms"]))
            except (TypeError, ValueError):
                pass
        prefix = key_prefix_from_meta(meta)
        if prefix:
            stats[model]["keys"].add(prefix)

    rows = []
    for model, s in stats.items():
        request_total = s["requests"]
        block_rate = (s["blocked"] / request_total) if request_total else 0.0
        redact_rate = (s["redacted"] / request_total) if request_total else 0.0
        latency_avg = (sum(s["latencies"]) / len(s["latencies"])) if s["latencies"] else 0.0
        exposure_score = min((0.7 * block_rate) + (0.2 * redact_rate) + (0.1 * min(latency_avg / 2000, 1)), 1.0)
        band = "high" if exposure_score >= 0.7 else "medium" if exposure_score >= 0.35 else "low"
        rows.append(
            {
                "model": model,
                "provider": llm_map.get(model, "unknown"),
                "requests": s["requests"],
                "blocked": s["blocked"],
                "redacted": s["redacted"],
                "block_rate_pct": round(block_rate * 100, 1),
                "redact_rate_pct": round(redact_rate * 100, 1),
                "avg_latency_ms": round(latency_avg, 1),
                "associated_keys": len(s["keys"]),
                "exposure_score": round(exposure_score, 3),
                "exposure_band": band,
            }
        )

    rows.sort(key=lambda x: (-x["exposure_score"], -x["requests"]))
    total_requests = sum(r["requests"] for r in rows)
    high_exposure = sum(1 for r in rows if r["exposure_band"] == "high")
    avg_block = (sum(r["block_rate_pct"] for r in rows) / len(rows)) if rows else 0.0
    avg_score = (sum(r["exposure_score"] for r in rows) / len(rows)) if rows else 0.0

    chart_rows = [
        {
            "model": r["model"],
            "exposure_score": r["exposure_score"],
            "exposure_band": r["exposure_band"],
            "block_rate_pct": r["block_rate_pct"],
            "requests": r["requests"],
        }
        for r in rows[:12]
    ]

    return {
        "period": period,
        "summary": {
            "active_models": len(rows),
            "high_exposure_models": high_exposure,
            "total_requests": total_requests,
            "avg_block_rate_pct": round(avg_block, 1),
            "avg_exposure_score": round(avg_score, 3),
        },
        "exposure_by_model": chart_rows,
        "models": rows,
    }


def build_threat_telemetry_payload(events: list[dict], period: str, since) -> dict:
    hours = hours_from_period(period)
    buckets = build_time_buckets(hours, since)
    series_keys = ("injection_attempts", "pii_leaks", "behavior_scoring", "threat_intel_matches")
    bucket_data = [
        {key: 0 for key in series_keys}
        for _ in buckets
    ]

    vector_counts: Counter[str] = Counter()
    totals = {key: 0 for key in series_keys}
    collapsed = collapse_events_by_request(events)
    totals["total_events"] = len(collapsed)

    for item in collapsed:
        meta = item.metadata or {}
        bucket_key = classify_telemetry_bucket(meta, item.action)
        if bucket_key in totals:
            totals[bucket_key] += 1
        vector_counts[attack_vector_key(meta)] += 1

        created_at = item.created_at
        if created_at is None:
            continue
        for idx, (start, end) in enumerate(buckets):
            if start <= created_at < end:
                if bucket_key in bucket_data[idx]:
                    bucket_data[idx][bucket_key] += 1
                break

    timeline = []
    for idx, (start, _end) in enumerate(buckets):
        point = {"timestamp": start.isoformat()}
        point.update(bucket_data[idx])
        point["total"] = sum(bucket_data[idx].values())
        timeline.append(point)

    top_vectors = [
        {"vector": vector, "count": count}
        for vector, count in vector_counts.most_common(10)
    ]

    return {
        "period": period,
        "summary": {
            "total_events": totals["total_events"],
            "requests_inspected": totals["total_events"],
            "injection_attempts": totals["injection_attempts"],
            "pii_leaks": totals["pii_leaks"],
            "behavior_scoring_events": totals["behavior_scoring"],
            "threat_intel_matches": totals["threat_intel_matches"],
        },
        "timeline": timeline,
        "top_attack_vectors": top_vectors,
    }


def build_lane_summary(events) -> dict:
    """Count events by lane (chat, rag, vector, mcp, threat_intel) for dashboard grid."""
    lanes = {
        "chat": {"total": 0, "blocked": 0},
        "rag": {"total": 0, "blocked": 0},
        "vector": {"total": 0, "blocked": 0},
        "mcp": {"total": 0, "blocked": 0},
        "threat_intel": {"total": 0, "blocked": 0},
    }
    for item in collapse_events_by_request(iter_rows_from_queryset(events, fields=("action", "metadata"))):
        meta = item.metadata or {}
        src = event_source(meta)
        lane = src if src in lanes else "chat"
        lanes[lane]["total"] += 1
        if item.action == "block":
            lanes[lane]["blocked"] += 1
    result = {}
    for lane, counts in lanes.items():
        total_events = counts["total"]
        block_rate = round(counts["blocked"] / total_events * 100, 1) if total_events else 0.0
        result[lane] = {
            "total": total_events,
            "blocked": counts["blocked"],
            "block_rate_pct": block_rate,
        }
    return result


def build_rag_pipeline_kpis(events) -> dict:
    """Per-stage breakdown for RAG pipeline funnel — mirrors RAGPipelineStageKpisView scoped to org.

    Stage rows prefer authoritative ``rag_pipeline`` per-stage telemetry. Top-level
    ``rag_query`` rows are used only as a fallback when no ``rag_pipeline`` stages
    were recorded for the same request_id (legacy / blocked-before-stages paths).
    ``rag_ingest_*`` events are counted separately — they must not inflate Query stage.
    """
    stage_names = ("query", "retriever", "ranker", "generator")
    stage_entries: dict[str, dict[str, dict]] = {stage: {} for stage in stage_names}
    escalation_by_request: dict[str, int] = {}
    pipeline_request_ids: set[str] = set()
    ingest_events = 0

    def _record_stage(stage: str, req_id: str, action: str, latency_ms: float, escalation_level: int) -> None:
        if stage not in stage_entries:
            return
        current = stage_entries[stage].get(req_id)
        if current is None:
            stage_entries[stage][req_id] = {
                "action": merge_request_action(None, action),
                "latency_ms": latency if latency > 0 else 0.0,
            }
        else:
            current["action"] = merge_request_action(current.get("action"), action)
            if current.get("latency_ms", 0) <= 0 and latency > 0:
                current["latency_ms"] = latency
        escalation_by_request[req_id] = max(escalation_level, escalation_by_request.get(req_id, 0))

    rows = list(events.values("action", "metadata"))

    for idx, ev in enumerate(rows):
        meta = ev.get("metadata") or {}
        if str(meta.get("event_type") or "").lower() != "rag_pipeline":
            continue
        stage = str(meta.get("pipeline_stage") or "").strip()
        if stage not in stage_entries:
            continue
        req_id = request_key(meta, idx)
        pipeline_request_ids.add(req_id)
        action = str(ev.get("action") or "allow").lower()
        latency = float(meta.get("latency_ms") or 0)
        try:
            escalation_level = int(meta.get("escalation_level", 0) or 0)
        except (TypeError, ValueError):
            escalation_level = 0
        _record_stage(stage, req_id, action, latency, escalation_level)

    for idx, ev in enumerate(rows):
        meta = ev.get("metadata") or {}
        event_type = str(meta.get("event_type") or "").lower()
        if event_type != "rag_query":
            continue
        req_id = request_key(meta, idx)
        if req_id in pipeline_request_ids:
            continue
        action = str(ev.get("action") or "allow").lower()
        latency = float(meta.get("latency_ms") or 0)
        try:
            escalation_level = int(meta.get("escalation_level", 0) or 0)
        except (TypeError, ValueError):
            escalation_level = 0
        blocked_stage = str(
            meta.get("blocked_at_stage") or meta.get("pipeline_stage") or ""
        ).strip()
        if blocked_stage in stage_entries:
            _record_stage(blocked_stage, req_id, action, latency, escalation_level)
        elif action == ACTION_BLOCK:
            # Blocked-before-stages / unknown stage: attribute to query (not
            # retriever). Prompt-injection blocks at query were previously
            # mis-counted under retriever, so Module 2 RAG health never moved.
            _record_stage("query", req_id, action, latency, escalation_level)
        else:
            _record_stage("query", req_id, action, latency, escalation_level)
            try:
                stages_executed = int(meta.get("stages_executed") or 0)
            except (TypeError, ValueError):
                stages_executed = 0
            if stages_executed >= 2:
                _record_stage("retriever", req_id, "allow", 0.0, escalation_level)

    for ev in rows:
        meta = ev.get("metadata") or {}
        if str(meta.get("event_type") or "").lower().startswith("rag_ingest"):
            ingest_events += 1

    result_stages = {}
    for stage, entries in stage_entries.items():
        actions = [entry.get("action") for entry in entries.values()]
        latencies = [entry.get("latency_ms", 0) for entry in entries.values() if entry.get("latency_ms", 0) > 0]
        result_stages[stage] = {
            "total": len(entries),
            "blocked": sum(1 for a in actions if a == ACTION_BLOCK),
            "flagged": sum(1 for a in actions if a == "flag"),
            "rewritten": sum(1 for a in actions if a == "rewrite"),
            "allowed": sum(1 for a in actions if a not in {ACTION_BLOCK, "flag", "rewrite"}),
            "avg_latency_ms": round(sum(latencies) / len(latencies), 2) if latencies else 0,
        }

    escalation_dist = {"normal": 0, "elevated": 0, "strict": 0}
    for level in escalation_by_request.values():
        if level <= 0:
            escalation_dist["normal"] += 1
        elif level == 1:
            escalation_dist["elevated"] += 1
        else:
            escalation_dist["strict"] += 1

    return {
        "stages": result_stages,
        "document_funnel": {
            "retrieved": result_stages["retriever"]["total"],
            "post_ranker": result_stages["ranker"]["total"] - result_stages["ranker"]["blocked"],
            "post_generator": result_stages["generator"]["total"] - result_stages["generator"]["blocked"],
        },
        "escalation_distribution": escalation_dist,
        "ingest_events": ingest_events,
    }


def build_mcp_activity_payload(events, top_n: int = 10) -> dict:
    """Aggregate MCP tool call events for the MCP Risk page."""
    tool_violation_counts: Counter = Counter()
    tool_total_counts: Counter = Counter()
    server_counts: Counter = Counter()
    direction_counts = {
        "inbound": {"total": 0, "blocked": 0},
        "outbound": {"total": 0, "blocked": 0},
        "unknown": {"total": 0, "blocked": 0},
    }
    unique_tools: set = set()

    mcp_rows = [
        row
        for row in iter_rows_from_queryset(events, fields=("action", "metadata"))
        if _looks_like_mcp_event(row.get("metadata") or {})
        or str((row.get("metadata") or {}).get("event_type") or "").lower() == "mcp_tool_call"
    ]
    collapsed = collapse_events_by_request(mcp_rows)
    total = len(collapsed)
    blocked = sum(1 for item in collapsed if item.action == "block")
    redacted = sum(1 for item in collapsed if item.action == "redact")

    for item in collapsed:
        meta = item.metadata or {}
        is_violation = item.action in ("block", "redact")
        tools = meta.get("tools_invoked") or []
        if isinstance(tools, str):
            tools = [tools]
        for tool in tools:
            if not tool:
                continue
            unique_tools.add(str(tool))
            tool_name = str(tool)
            tool_total_counts[tool_name] += 1
            if is_violation:
                tool_violation_counts[tool_name] += 1

        server = str(meta.get("mcp_server") or meta.get("server_slug") or "unknown")
        server_counts[server] += 1

        direction = str(meta.get("mcp_direction") or meta.get("scan_direction") or "").lower()
        if direction not in direction_counts:
            direction = "unknown"
        direction_counts[direction]["total"] += 1
        if is_violation:
            direction_counts[direction]["blocked"] += 1

    sorted_tools = sorted(
        tool_total_counts,
        key=lambda tool: (
            -tool_violation_counts[tool],
            -tool_total_counts[tool],
            str(tool),
        ),
    )
    tool_ledger = [
        {
            "tool": tool,
            "total_calls": tool_total_counts[tool],
            "violations": tool_violation_counts[tool],
        }
        for tool in sorted_tools[:top_n]
    ]
    top_servers = [
        {"server": server, "total": count}
        for server, count in server_counts.most_common(top_n)
    ]

    return {
        "summary": {
            "total_events": total,
            "blocked_tool_calls": blocked,
            "redacted_calls": redacted,
            # Backward-compatible alias used by older clients.
            "redacted_arguments": redacted,
            "unique_tools": len(unique_tools),
        },
        "tool_ledger": tool_ledger,
        "direction_split": direction_counts,
        "top_servers": top_servers,
    }


def build_vector_exposure_payload(events, top_n: int = 12) -> dict:
    """Aggregate vector DB events by collection/namespace for RAG Health page."""
    collection_stats: dict = defaultdict(lambda: {"total": 0, "blocked": 0, "redacted": 0})

    vector_rows = [
        row
        for row in iter_rows_from_queryset(events, fields=("action", "metadata"))
        if (row.get("metadata") or {}).get("collection")
        or (row.get("metadata") or {}).get("vector_collection")
        or (row.get("metadata") or {}).get("vector_namespace")
    ]
    for item in collapse_events_by_request(vector_rows):
        meta = item.metadata or {}
        collection = (
            str(meta.get("collection") or meta.get("vector_collection") or "").strip()
            or str(meta.get("vector_namespace") or "").strip()
            or "unknown"
        )
        collection_stats[collection]["total"] += 1
        if item.action == "block":
            collection_stats[collection]["blocked"] += 1
        elif item.action == "redact":
            collection_stats[collection]["redacted"] += 1

    rows = []
    for collection, stats in collection_stats.items():
        total_events = stats["total"]
        block_rate = round(stats["blocked"] / total_events * 100, 1) if total_events else 0.0
        rows.append(
            {
                "collection": collection,
                "total": stats["total"],
                "blocked": stats["blocked"],
                "redacted": stats["redacted"],
                "block_rate_pct": block_rate,
            }
        )
    rows.sort(key=lambda x: (-x["block_rate_pct"], -x["total"]))
    return {"collections": rows[:top_n]}


def build_stage_hit_distribution(events) -> list:
    """Count threat-intel matched events grouped by pipeline_stage."""
    stage_counts: Counter = Counter()
    for item in collapse_events_by_request(iter_rows_from_queryset(events, fields=("metadata",))):
        meta = item.metadata or {}
        src = event_source(meta)
        if src != "threat_intel":
            continue
        stage = str(meta.get("pipeline_stage") or "ingress").strip() or "ingress"
        stage_counts[stage] += 1
    return [{"stage": stage, "count": count} for stage, count in stage_counts.most_common()]


def _incident_stats_queryset(qs):
    """Join-free queryset for status counts (select_related breaks values().annotate)."""
    return qs.model.objects.filter(pk__in=qs.values("pk"))


def _incident_by_source_counts(qs) -> dict:
    by_source: Counter = Counter()
    for incident in qs.select_related("enforcement_event").iterator(chunk_size=200):
        meta = {}
        if incident.enforcement_event_id and incident.enforcement_event:
            meta = incident.enforcement_event.metadata or {}
        by_source[event_source(meta)] += 1
    return dict(by_source)


def build_incident_queue_summary(qs, org_id=None) -> dict:
    """Aggregate incident queue stats for the Incidents SOC KPI strip."""
    from django.db.models import Count

    stats_qs = _incident_stats_queryset(qs)
    status_map = {
        row["status"]: row["c"]
        for row in stats_qs.values("status").annotate(c=Count("id"))
    }
    active_statuses = ("open", "investigating", "escalated")
    active_count = sum(status_map.get(s, 0) for s in active_statuses)

    return {
        "total": sum(status_map.values()),
        "open": status_map.get("open", 0),
        "investigating": status_map.get("investigating", 0),
        "escalated": status_map.get("escalated", 0),
        "resolved": status_map.get("resolved", 0),
        "active": active_count,
        "critical_high": stats_qs.filter(
            status__in=active_statuses,
            severity__in=("critical", "high"),
        ).count(),
        "by_source": _incident_by_source_counts(qs),
    }


def invalidate_incident_summary_cache(org_id) -> None:
    """Drop legacy cached KPI blobs after incident mutations."""
    if org_id is None:
        return
    from django.core.cache import cache

    try:
        cache.delete(f"module2:incident_summary:{org_id}")
    except Exception:
        logger.warning(
            "Failed to invalidate incident summary cache for org %s",
            org_id,
            exc_info=True,
        )


def serialize_incident_row(incident, serializer_data: dict) -> dict:
    meta = {}
    if incident.enforcement_event_id and incident.enforcement_event:
        meta = incident.enforcement_event.metadata or {}
    return {
        **serializer_data,
        "title": sanitize_incident_text(serializer_data.get("title", "")),
        "notes": sanitize_incident_text(serializer_data.get("notes", "")),
        "source": event_source(meta),
        "key_prefix": sanitize_incident_text(key_prefix_from_meta(meta)),
        "model": meta.get("model") or "",
        "project_id": meta.get("project_id") or "",
        "threat_type": meta.get("threat_type") or "",
    }


def paginate_queryset(qs, page: int, page_size: int):
    total = qs.count()
    page = max(page, 1)
    page_size = min(max(page_size, 1), 100)
    start = (page - 1) * page_size
    end = start + page_size
    return total, qs[start:end], page, page_size
