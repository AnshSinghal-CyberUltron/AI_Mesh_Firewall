"""Aggregation helpers for Module 2 exposure, threat telemetry, and incidents."""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import timedelta

from django.utils import timezone

from policy.constants import ACTION_BLOCK, ACTION_REDACT

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


def event_source(meta: dict) -> str:
    event_type = str(meta.get("event_type") or "").lower()
    if event_type == "mcp_tool_call" or _looks_like_mcp_event(meta):
        return "mcp"
    if event_type == "rag_pipeline":
        return "rag"
    if meta.get("collection") or meta.get("vector_collection") or meta.get("vector_namespace"):
        return "vector"
    detail = str(meta.get("detail") or "").lower()
    src = str(meta.get("source") or "").lower()
    threat_type = str(meta.get("threat_type") or "").lower()
    if "threat intel" in detail or "threat_intel" in src or threat_type.startswith("threat_intel"):
        return "threat_intel"
    if key_prefix_from_meta(meta):
        return "ueba"
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
    """Map enforcement metadata to telemetry series keys."""
    threat = str(meta.get("threat_type") or "").lower()
    category = str(meta.get("category") or meta.get("violation_type") or "").lower()
    combined = f"{threat} {category}"

    if any(k in combined for k in INJECTION_KEYWORDS) or "injection" in str(meta.get("detail") or "").lower():
        return "injection_attempts"
    if action == ACTION_REDACT and any(k in combined for k in PII_KEYWORDS):
        return "pii_leaks"
    if event_source(meta) == "threat_intel":
        return "threat_intel_matches"
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

    for ev in events:
        meta = ev.get("metadata") or {}
        model = str(meta.get("model") or "unknown")
        stats[model]["requests"] += 1
        if ev.get("action") == ACTION_BLOCK:
            stats[model]["blocked"] += 1
        if ev.get("action") == ACTION_REDACT:
            stats[model]["redacted"] += 1
        if meta.get("latency_ms"):
            stats[model]["latencies"].append(float(meta["latency_ms"]))
        prefix = key_prefix_from_meta(meta)
        if prefix:
            stats[model]["keys"].add(prefix)

    rows = []
    for model, s in stats.items():
        total = s["requests"] or 1
        block_rate = s["blocked"] / total
        redact_rate = s["redacted"] / total
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
    totals["total_events"] = len(events)

    for ev in events:
        meta = ev.get("metadata") or {}
        bucket_key = classify_telemetry_bucket(meta, ev.get("action") or "")
        if bucket_key in totals:
            totals[bucket_key] += 1
        vector_counts[attack_vector_key(meta)] += 1

        created_at = ev.get("created_at")
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
            "injection_attempts": totals["injection_attempts"],
            "pii_leaks": totals["pii_leaks"],
            "behavior_scoring_events": totals["behavior_scoring"],
            "threat_intel_matches": totals["threat_intel_matches"],
        },
        "timeline": timeline,
        "top_attack_vectors": top_vectors,
    }


def build_lane_summary(events) -> dict:
    """Count events by lane (chat, rag, vector, mcp, threat_intel, ueba) for dashboard grid."""
    lanes = {
        "chat": {"total": 0, "blocked": 0},
        "rag": {"total": 0, "blocked": 0},
        "vector": {"total": 0, "blocked": 0},
        "mcp": {"total": 0, "blocked": 0},
    }
    for ev in events.values("action", "metadata"):
        meta = ev.get("metadata") or {}
        src = event_source(meta)
        lane = src if src in lanes else "chat"
        lanes[lane]["total"] += 1
        if ev.get("action") == ACTION_BLOCK:
            lanes[lane]["blocked"] += 1
    result = {}
    for lane, counts in lanes.items():
        total = counts["total"] or 1
        result[lane] = {
            "total": counts["total"],
            "blocked": counts["blocked"],
            "block_rate_pct": round(counts["blocked"] / total * 100, 1),
        }
    return result


def build_rag_pipeline_kpis(events) -> dict:
    """Per-stage breakdown for RAG pipeline funnel — mirrors RAGPipelineStageKpisView scoped to org."""
    stages = {
        s: {"total": 0, "blocked": 0, "flagged": 0, "rewritten": 0, "allowed": 0, "_latencies": []}
        for s in ("query", "retriever", "ranker", "generator")
    }
    escalation_dist = {"normal": 0, "elevated": 0, "strict": 0}

    for ev in events.values("action", "metadata"):
        meta = ev.get("metadata") or {}
        if meta.get("event_type") != "rag_pipeline":
            continue
        stage = meta.get("pipeline_stage", "")
        if stage not in stages:
            continue
        stages[stage]["total"] += 1
        action = ev.get("action", "allow")
        if action == ACTION_BLOCK:
            stages[stage]["blocked"] += 1
        elif action == "flag":
            stages[stage]["flagged"] += 1
        elif action == "rewrite":
            stages[stage]["rewritten"] += 1
        else:
            stages[stage]["allowed"] += 1
        lat = meta.get("latency_ms", 0)
        if lat:
            stages[stage]["_latencies"].append(float(lat))
        level = meta.get("escalation_level", 0)
        if level == 0:
            escalation_dist["normal"] += 1
        elif level == 1:
            escalation_dist["elevated"] += 1
        else:
            escalation_dist["strict"] += 1

    result_stages = {}
    for stage, data in stages.items():
        lats = data.pop("_latencies")
        data["avg_latency_ms"] = round(sum(lats) / len(lats), 2) if lats else 0
        result_stages[stage] = data

    return {
        "stages": result_stages,
        "document_funnel": {
            "retrieved": result_stages["retriever"]["total"],
            "post_ranker": result_stages["ranker"]["total"] - result_stages["ranker"]["blocked"],
            "post_generator": result_stages["generator"]["total"] - result_stages["generator"]["blocked"],
        },
        "escalation_distribution": escalation_dist,
    }


def build_mcp_activity_payload(events, top_n: int = 10) -> dict:
    """Aggregate MCP tool call events for the MCP Risk page."""
    tool_counts: Counter = Counter()
    server_counts: Counter = Counter()
    direction_counts = {"inbound": {"total": 0, "blocked": 0}, "outbound": {"total": 0, "blocked": 0}}
    total = 0
    blocked = 0
    redacted = 0
    unique_tools: set = set()

    for ev in events.values("action", "metadata"):
        meta = ev.get("metadata") or {}
        if not _looks_like_mcp_event(meta) and str(meta.get("event_type") or "").lower() != "mcp_tool_call":
            continue
        total += 1
        action = ev.get("action", "allow")
        if action == ACTION_BLOCK:
            blocked += 1
        elif action == ACTION_REDACT:
            redacted += 1

        tools = meta.get("tools_invoked") or []
        if isinstance(tools, str):
            tools = [tools]
        for tool in tools:
            if tool:
                tool_counts[str(tool)] += 1
                unique_tools.add(str(tool))

        server = str(meta.get("mcp_server") or meta.get("server_slug") or "unknown")
        server_counts[server] += 1

        direction = str(meta.get("mcp_direction") or meta.get("scan_direction") or "inbound").lower()
        if direction not in direction_counts:
            direction = "inbound"
        direction_counts[direction]["total"] += 1
        if action in (ACTION_BLOCK, ACTION_REDACT):
            direction_counts[direction]["blocked"] += 1

    tool_ledger = [
        {"tool": tool, "violations": count}
        for tool, count in tool_counts.most_common(top_n)
    ]
    top_servers = [
        {"server": server, "total": count}
        for server, count in server_counts.most_common(top_n)
    ]

    return {
        "summary": {
            "total_events": total,
            "blocked_tool_calls": blocked,
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

    for ev in events.values("action", "metadata"):
        meta = ev.get("metadata") or {}
        if not (
            meta.get("collection")
            or meta.get("vector_collection")
            or meta.get("vector_namespace")
        ):
            continue
        collection = (
            str(meta.get("collection") or meta.get("vector_collection") or "").strip()
            or str(meta.get("vector_namespace") or "").strip()
            or "unknown"
        )
        collection_stats[collection]["total"] += 1
        action = ev.get("action", "allow")
        if action == ACTION_BLOCK:
            collection_stats[collection]["blocked"] += 1
        elif action == ACTION_REDACT:
            collection_stats[collection]["redacted"] += 1

    rows = []
    for collection, stats in collection_stats.items():
        total = stats["total"] or 1
        block_rate = round(stats["blocked"] / total * 100, 1)
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
    for ev in events.values("metadata"):
        meta = ev.get("metadata") or {}
        src = event_source(meta)
        if src != "threat_intel":
            continue
        stage = str(meta.get("pipeline_stage") or "ingress").strip() or "ingress"
        stage_counts[stage] += 1
    return [{"stage": stage, "count": count} for stage, count in stage_counts.most_common()]


def serialize_incident_row(incident, serializer_data: dict) -> dict:
    meta = {}
    if incident.enforcement_event_id and incident.enforcement_event:
        meta = incident.enforcement_event.metadata or {}
    return {
        **serializer_data,
        "source": event_source(meta),
        "key_prefix": key_prefix_from_meta(meta),
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
