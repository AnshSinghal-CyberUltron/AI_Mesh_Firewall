"""
Policy analytics, top-rules, and top-violators APIs for Policy Management and SOC.
"""

from collections import defaultdict
from datetime import timedelta

from django.db.models import Count, Q
from django.utils import timezone
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from core.models import Endpoint
from policy.constants import ACTION_BLOCK, ACTION_MONITOR, ACTION_REDACT
from policy.models import EnforcementEvent, Policy, Rule
from policy.security_views import _enforcement_events_for_request
from policy.telemetry_resolution import metadata_policy_codes, metadata_rule_names


def _enforcement_rate(
    blocked: int,
    redacted: int,
    total: int,
    *,
    monitored: int = 0,
) -> float:
    """SOC enforcement rate: violations that were blocked, redacted, or monitored."""
    enforced = blocked + redacted + monitored
    return round((enforced / total * 100) if total else 0, 1)


def _count_actions(evs) -> dict[str, int]:
    return {
        "blocked": evs.filter(action=ACTION_BLOCK).count(),
        "redacted": evs.filter(action=ACTION_REDACT).count(),
        "monitored": evs.filter(action=ACTION_MONITOR).count(),
    }


def _performance_status(effectiveness: float) -> str:
    if effectiveness >= 98:
        return "EXCELLENT"
    if effectiveness >= 95:
        return "GOOD"
    if effectiveness >= 90:
        return "NEEDS REVIEW"
    return "LOW"


def _normalize_policy_category(raw: str | None) -> str:
    if raw is None or not str(raw).strip():
        return "uncategorized"
    return str(raw).strip().lower()


POLICY_CATEGORY_LABELS = {
    "pii": "PII Detection",
    "jailbreak": "Jailbreak",
    "prompt injection": "Prompt Injection",
    "prompt_injection": "Prompt Injection",
    "data protection": "PII Detection",
    "dlp compliance": "Data Loss Prevention",
    "uncategorized": "Uncategorized",
}


def _category_display_label(normalized_key: str, raw_category: str | None) -> str:
    if normalized_key in POLICY_CATEGORY_LABELS:
        return POLICY_CATEGORY_LABELS[normalized_key]
    if raw_category and str(raw_category).strip():
        return str(raw_category).strip()
    return "Uncategorized"


def _build_category_performance(policies_base, events) -> list[dict]:
    """Aggregate enforcement stats per normalized policy category (one row per category)."""
    cat_policy_ids: dict[str, list[int]] = defaultdict(list)
    cat_raw: dict[str, str | None] = {}
    for policy in policies_base.only("id", "category"):
        norm = _normalize_policy_category(policy.category)
        cat_policy_ids[norm].append(policy.id)
        if norm not in cat_raw:
            cat_raw[norm] = policy.category

    category_performance = []
    for norm_key in sorted(cat_policy_ids.keys()):
        policy_ids = cat_policy_ids[norm_key]
        raw_cat = cat_raw.get(norm_key)
        display_cat = _category_display_label(norm_key, raw_cat)
        evs = events.filter(policy_id__in=policy_ids)
        total_v = evs.count()
        counts = _count_actions(evs)
        effectiveness = _enforcement_rate(
            counts["blocked"], counts["redacted"], total_v, monitored=counts["monitored"]
        )
        category_performance.append(
            {
                "category": display_cat,
                "categoryKey": norm_key,
                "policies": len(policy_ids),
                "totalViolations": total_v,
                "blocked": counts["blocked"],
                "redacted": counts["redacted"],
                "monitored": counts["monitored"],
                "blockRate": round((counts["blocked"] / total_v * 100) if total_v else 0, 1),
                "redactRate": round((counts["redacted"] / total_v * 100) if total_v else 0, 1),
                "monitorRate": round((counts["monitored"] / total_v * 100) if total_v else 0, 1),
                "effectiveness": effectiveness,
                "status": _performance_status(effectiveness),
                "avgResponseTime": None,
            }
        )
    return category_performance


class PolicyAnalyticsView(APIView):
    """
    GET /api/policies/analytics/
    Returns effectiveness trend, violations by hour, and category performance.
    """

    permission_classes = [IsAuthenticated]

    def get(self, request):
        from auth.utils import get_request_organization

        org = get_request_organization(request)

        now = timezone.now()
        days = min(int(request.query_params.get("days", 14)), 90)
        since = now - timedelta(days=days)

        base_events = EnforcementEvent.objects.filter(created_at__gte=since)
        events = _enforcement_events_for_request(request, base_events)

        # Effectiveness trend: per-day enforcement rate (blocked + redacted) / total
        daily = defaultdict(lambda: {"total": 0, "blocked": 0, "redacted": 0, "monitored": 0})
        for ev in events.values("created_at", "action"):
            d = ev["created_at"].date() if ev["created_at"] else None
            if d:
                daily[str(d)]["total"] += 1
                if ev["action"] == ACTION_BLOCK:
                    daily[str(d)]["blocked"] += 1
                elif ev["action"] == ACTION_REDACT:
                    daily[str(d)]["redacted"] += 1
                elif ev["action"] == ACTION_MONITOR:
                    daily[str(d)]["monitored"] += 1
        effectiveness_trend = []
        for d in sorted(daily.keys()):
            row = daily[d]
            effectiveness_trend.append(
                {
                    "date": d,
                    "effectiveness": _enforcement_rate(
                        row["blocked"],
                        row["redacted"],
                        row["total"],
                        monitored=row["monitored"],
                    ),
                }
            )

        # ── shared category-tagger ────────────────────────────────────────────
        def _empty_cats():
            return {
                "jailbreak": 0,
                "piiDetection": 0,
                "promptInjection": 0,
                "sourceCode": 0,
                "toolOverreach": 0,
                "agenticThreat": 0,
            }

        def _tag_event(meta, bucket):
            codes = meta.get("owasp_codes")
            if codes is not None:
                codes = [str(c).strip().upper() for c in codes if c]
            else:
                single = (meta.get("owasp_code") or "").strip().upper()
                codes = [single] if single else []
            category = (meta.get("threat_category") or "").lower()
            source = meta.get("source", "")
            if "jailbreak" in category or "LLM04" in codes:
                bucket["jailbreak"] += 1
            if "data" in category or "LLM06" in codes or meta.get("pii_detected"):
                bucket["piiDetection"] += 1
            if "prompt" in category or "injection" in category or "LLM01" in codes:
                bucket["promptInjection"] += 1
            if source == "mcp_scan" or any(c.startswith("MCP") for c in codes):
                bucket["toolOverreach"] += 1
            if source == "agentic_scan" or any(c.startswith("AGENTIC") for c in codes):
                bucket["agenticThreat"] += 1
            if "source" in category or "code" in category:
                bucket["sourceCode"] += 1

        # Violations by hour of day (0-23)
        hour_buckets = defaultdict(_empty_cats)
        for ev in events.values("created_at", "metadata"):
            meta = ev.get("metadata") or {}
            h = ev["created_at"].hour if ev.get("created_at") else 0
            _tag_event(meta, hour_buckets[h])
        violations_by_hour = [{"hour": f"{h:02d}:00", **hour_buckets[h]} for h in range(24)]

        # Violations by calendar day (one row per date in the window)
        day_buckets: dict = defaultdict(_empty_cats)
        for ev in events.values("created_at", "metadata"):
            meta = ev.get("metadata") or {}
            d = str(ev["created_at"].date()) if ev.get("created_at") else None
            if d:
                _tag_event(meta, day_buckets[d])
        all_days = [(since + timedelta(days=i)).date() for i in range(days + 1)]
        violations_by_day = [{"date": str(d), **day_buckets.get(str(d), _empty_cats())} for d in all_days]

        # Violations by ISO week (week starting Monday, label = Mon date)
        week_buckets: dict = defaultdict(_empty_cats)
        week_labels: dict = {}
        for ev in events.values("created_at", "metadata"):
            meta = ev.get("metadata") or {}
            dt = ev.get("created_at")
            if dt:
                iso_key = dt.strftime("%G-W%V")
                week_start = dt.date() - timedelta(days=dt.weekday())
                week_labels[iso_key] = str(week_start)
                _tag_event(meta, week_buckets[iso_key])
        violations_by_week = [{"week": week_labels[k], **week_buckets[k]} for k in sorted(week_buckets.keys())]

        # Category performance: one row per normalized Policy.category (org-scoped)
        policies_base = Policy.objects.filter(enabled=True)
        if org is not None:
            policies_base = policies_base.filter(organization=org)
        category_performance = _build_category_performance(policies_base, events)

        # Period-wide totals for metric cards
        total_violations = events.count()
        totals = _count_actions(events)
        total_blocked = totals["blocked"]
        total_redacted = totals["redacted"]
        total_monitored = totals["monitored"]
        avg_effectiveness = _enforcement_rate(
            total_blocked, total_redacted, total_violations, monitored=total_monitored
        )

        # Overall avg response time: not yet computed (no timing in EnforcementEvent); expose for UI
        avg_response_time = None

        return Response(
            {
                "effectiveness_trend": effectiveness_trend,
                "violations_by_hour": violations_by_hour,
                "violations_by_day": violations_by_day,
                "violations_by_week": violations_by_week,
                "category_performance": category_performance,
                # Period-level aggregates (used by metric cards)
                "total_violations": total_violations,
                "total_blocked": total_blocked,
                "total_redacted": total_redacted,
                "total_monitored": total_monitored,
                "avg_effectiveness": avg_effectiveness,
                "avg_response_time": avg_response_time,
            }
        )


class TopViolatorsView(APIView):
    """
    GET /api/policies/top-violators/?days=30&limit=10
    Returns the endpoints (and optional user_id) with the most enforcement events
    in the selected window, along with the distinct policy codes they triggered and
    the highest risk score seen.  When endpoint_id is null the row is grouped by
    user_id instead.
    """

    permission_classes = [IsAuthenticated]

    def get(self, request):
        from auth.utils import get_request_organization

        org = get_request_organization(request)

        days = min(int(request.query_params.get("days", 30)), 90)
        limit = min(int(request.query_params.get("limit", 10)), 50)
        since = timezone.now() - timedelta(days=days)

        base_events = EnforcementEvent.objects.filter(created_at__gte=since)
        events = _enforcement_events_for_request(request, base_events)

        # ── group by endpoint_id first ────────────────────────────────────────
        endpoint_stats = (
            events.filter(endpoint_id__isnull=False)
            .values("endpoint_id")
            .annotate(
                total_violations=Count("id"),
                blocked=Count("id", filter=Q(action=ACTION_BLOCK)),
            )
            .order_by("-total_violations")[:limit]
        )

        # Bulk-fetch Endpoint names in one query (org-scoped)
        endpoint_ids = [r["endpoint_id"] for r in endpoint_stats]
        endpoints_qs = Endpoint.objects.filter(id__in=endpoint_ids)
        if org is not None:
            endpoints_qs = endpoints_qs.filter(organization=org)
        endpoints_by_id = {ep.id: ep for ep in endpoints_qs}

        # For each endpoint, collect distinct policy codes and max risk score
        events_for_ep = list(events.filter(endpoint_id__in=endpoint_ids).values("endpoint_id", "policy_id", "metadata"))
        ep_policy_ids: dict = defaultdict(set)
        ep_metadata_codes: dict = defaultdict(set)
        ep_risk: dict = defaultdict(int)
        for ev in events_for_ep:
            eid = ev["endpoint_id"]
            if ev["policy_id"]:
                ep_policy_ids[eid].add(ev["policy_id"])
            ep_metadata_codes[eid].update(metadata_policy_codes(ev.get("metadata")))
            score = (ev.get("metadata") or {}).get("security_risk_score", 0) or 0
            if score > ep_risk[eid]:
                ep_risk[eid] = score

        # ── also include events with no endpoint but a user_id ───────────────
        user_stats = (
            events.filter(endpoint_id__isnull=True, user_id__isnull=False)
            .values("user_id")
            .annotate(
                total_violations=Count("id"),
                blocked=Count("id", filter=Q(action=ACTION_BLOCK)),
            )
            .order_by("-total_violations")[:limit]
        )
        user_ids = [r["user_id"] for r in user_stats]
        events_for_user = list(
            events.filter(endpoint_id__isnull=True, user_id__in=user_ids).values("user_id", "policy_id", "metadata")
        )
        user_policy_ids: dict = defaultdict(set)
        user_metadata_codes: dict = defaultdict(set)
        user_risk: dict = defaultdict(int)
        for ev in events_for_user:
            uid = ev["user_id"]
            if ev["policy_id"]:
                user_policy_ids[uid].add(ev["policy_id"])
            user_metadata_codes[uid].update(metadata_policy_codes(ev.get("metadata")))
            score = (ev.get("metadata") or {}).get("security_risk_score", 0) or 0
            if score > user_risk[uid]:
                user_risk[uid] = score

        # Bulk-fetch policy codes (endpoint + user FK ids)
        all_policy_ids = {
            pid for pids in ep_policy_ids.values() for pid in pids
        } | {pid for pids in user_policy_ids.values() for pid in pids}
        policy_codes = {p.id: p.code for p in Policy.objects.filter(id__in=all_policy_ids).only("id", "code")}

        out = []
        for row in endpoint_stats:
            eid = row["endpoint_id"]
            ep = endpoints_by_id.get(eid)
            codes = sorted(
                {policy_codes.get(pid, f"POL-{pid}") for pid in ep_policy_ids.get(eid, [])}
                | ep_metadata_codes.get(eid, set())
            )
            out.append(
                {
                    "endpoint_id": eid,
                    "endpoint_name": ep.name if ep else f"Endpoint {eid}",
                    "endpoint_identifier": ep.identifier if ep else str(eid),
                    "endpoint_status": ep.status if ep else "unknown",
                    "total_violations": row["total_violations"],
                    "blocked": row["blocked"],
                    "violated_policy_codes": codes,
                    "risk_score": ep_risk.get(eid, 0),
                }
            )

        for row in user_stats:
            uid = row["user_id"]
            codes_u = sorted(
                {policy_codes.get(pid, f"POL-{pid}") for pid in user_policy_ids.get(uid, [])}
                | user_metadata_codes.get(uid, set())
            )
            risk_u = user_risk.get(uid, 0)
            out.append(
                {
                    "endpoint_id": None,
                    "endpoint_name": f"User {uid}",
                    "endpoint_identifier": f"user-{uid}",
                    "endpoint_status": "—",
                    "total_violations": row["total_violations"],
                    "blocked": row["blocked"],
                    "violated_policy_codes": codes_u,
                    "risk_score": risk_u,
                }
            )

        # Sort combined list and trim to limit
        out.sort(key=lambda x: x["total_violations"], reverse=True)
        return Response(out[:limit])


def _aggregate_top_rules(events, *, limit: int) -> list[dict]:
    """Aggregate rule stats from FK-linked events and metadata-only telemetry rows."""
    by_rule_id: dict[int, dict] = defaultdict(
        lambda: {"triggered": 0, "blocked": 0, "redacted": 0, "monitored": 0}
    )
    by_name: dict[str, dict] = defaultdict(
        lambda: {"triggered": 0, "blocked": 0, "redacted": 0, "monitored": 0, "policy_code": ""}
    )

    for ev in events.values("rule_id", "action", "metadata"):
        action = ev["action"]
        meta = ev.get("metadata") or {}

        if ev["rule_id"]:
            bucket = by_rule_id[ev["rule_id"]]
        else:
            names = metadata_rule_names(meta)
            if not names:
                continue
            name = names[0]
            bucket = by_name[name]
            codes = metadata_policy_codes(meta)
            if codes and not bucket["policy_code"]:
                bucket["policy_code"] = codes[0]

        bucket["triggered"] += 1
        if action == ACTION_BLOCK:
            bucket["blocked"] += 1
        elif action == ACTION_REDACT:
            bucket["redacted"] += 1
        elif action == ACTION_MONITOR:
            bucket["monitored"] += 1

    rows: list[dict] = []
    for rule_id, stats in by_rule_id.items():
        rows.append({"rule_id": rule_id, "rule_name": None, **stats})
    for rule_name, stats in by_name.items():
        rows.append(
            {
                "rule_id": None,
                "rule_name": rule_name,
                "policy_code": stats["policy_code"],
                "triggered": stats["triggered"],
                "blocked": stats["blocked"],
                "redacted": stats["redacted"],
                "monitored": stats["monitored"],
            }
        )

    rows.sort(key=lambda r: r["triggered"], reverse=True)
    return rows[:limit]


class TopRulesView(APIView):
    """
    GET /api/policy/top-rules/?days=14&limit=10
    Returns rules with highest triggered/blocked counts from EnforcementEvent.
    """

    permission_classes = [IsAuthenticated]

    def get(self, request):
        limit = min(int(request.query_params.get("limit", 10)), 50)
        days = min(int(request.query_params.get("days", 14)), 90)
        since_dt = timezone.now() - timedelta(days=days)
        since = request.query_params.get("since")
        if since:
            try:
                since_dt = timezone.datetime.fromisoformat(since.replace("Z", "+00:00"))
            except Exception:
                pass

        base_events = EnforcementEvent.objects.filter(created_at__gte=since_dt)
        events = _enforcement_events_for_request(request, base_events)
        aggregated = _aggregate_top_rules(events, limit=limit)

        from auth.utils import get_request_organization

        org = get_request_organization(request)

        rule_ids = [r["rule_id"] for r in aggregated if r.get("rule_id")]
        rules = {r.id: r for r in Rule.objects.filter(id__in=rule_ids).select_related("policy")}
        rules_qs = Rule.objects.filter(policy__enabled=True, enabled=True)
        if org is not None:
            rules_qs = rules_qs.filter(policy__organization=org)
        rules_applied_count = rules_qs.count()

        out = []
        for row in aggregated:
            rule = rules.get(row["rule_id"]) if row.get("rule_id") else None
            triggered = row["triggered"]
            blocked = row["blocked"]
            redacted = row["redacted"]
            monitored = row.get("monitored", 0)
            effectiveness = _enforcement_rate(
                blocked, redacted, triggered, monitored=monitored
            )
            rule_name = rule.name if rule else (row.get("rule_name") or "Unknown rule")
            policy_code = (
                rule.policy.code
                if rule and rule.policy
                else (row.get("policy_code") or "")
            )
            rule_id_label = (
                f"{policy_code}-R{rule.id}"
                if rule and rule.policy
                else (f"{policy_code}-R?" if policy_code else f"R?-{rule_name}")
            )
            out.append(
                {
                    "ruleId": rule_id_label,
                    "ruleName": rule_name,
                    "description": (rule.description or "") if rule else "",
                    "policyCode": policy_code,
                    "appliedTo": f"{rules_applied_count} rules",
                    "triggered": triggered,
                    "blocked": blocked,
                    "redacted": redacted,
                    "monitored": monitored,
                    "effectiveness": effectiveness,
                }
            )
        return Response(out)
