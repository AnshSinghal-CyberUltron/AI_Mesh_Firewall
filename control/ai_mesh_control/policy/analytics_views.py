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
from policy.constants import ACTION_BLOCK, ACTION_REDACT
from policy.models import EnforcementEvent, Policy, Rule
from policy.security_views import _enforcement_events_for_request


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

        # Effectiveness trend: per-day { date, effectiveness } (effectiveness = blocked/total * 100)
        daily = defaultdict(lambda: {"total": 0, "blocked": 0})
        for ev in events.values("created_at", "action"):
            d = ev["created_at"].date() if ev["created_at"] else None
            if d:
                daily[str(d)]["total"] += 1
                if ev["action"] == ACTION_BLOCK:
                    daily[str(d)]["blocked"] += 1
        effectiveness_trend = []
        for d in sorted(daily.keys()):
            t = daily[d]["total"]
            b = daily[d]["blocked"]
            effectiveness_trend.append(
                {
                    "date": d,
                    "effectiveness": round((b / t * 100) if t else 0, 1),
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

        # Category performance: per Policy.category (org-scoped)
        policies_base = Policy.objects.filter(enabled=True)
        if org is not None:
            policies_base = policies_base.filter(organization=org)
        categories = policies_base.values("category").distinct()
        category_performance = []
        for c in categories:
            cat = c["category"] or "Uncategorized"
            policies_qs = policies_base.filter(category=c["category"])
            policy_ids = list(policies_qs.values_list("id", flat=True))
            evs = events.filter(policy_id__in=policy_ids)
            total_v = evs.count()
            blocked_v = evs.filter(action=ACTION_BLOCK).count()
            effectiveness = round((blocked_v / total_v * 100) if total_v else 0, 1)
            if effectiveness >= 98:
                status = "EXCELLENT"
            elif effectiveness >= 95:
                status = "GOOD"
            elif effectiveness >= 90:
                status = "NEEDS REVIEW"
            else:
                status = "LOW"
            category_performance.append(
                {
                    "category": cat,
                    "policies": policies_qs.count(),
                    "totalViolations": total_v,
                    "effectiveness": effectiveness,
                    "status": status,
                    "avgResponseTime": None,  # Reserved for future response-time metrics
                }
            )

        # Period-wide totals for metric cards
        total_violations = events.count()
        total_blocked = events.filter(action=ACTION_BLOCK).count()
        total_redacted = events.filter(action=ACTION_REDACT).count()
        avg_effectiveness = round((total_blocked / total_violations * 100) if total_violations else 0, 1)

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
        ep_policies: dict = defaultdict(set)
        ep_risk: dict = defaultdict(int)
        for ev in events_for_ep:
            eid = ev["endpoint_id"]
            if ev["policy_id"]:
                ep_policies[eid].add(ev["policy_id"])
            score = (ev.get("metadata") or {}).get("security_risk_score", 0) or 0
            if score > ep_risk[eid]:
                ep_risk[eid] = score

        # Bulk-fetch policy codes
        all_policy_ids = {pid for pids in ep_policies.values() for pid in pids}
        policy_codes = {p.id: p.code for p in Policy.objects.filter(id__in=all_policy_ids).only("id", "code")}

        out = []
        for row in endpoint_stats:
            eid = row["endpoint_id"]
            ep = endpoints_by_id.get(eid)
            codes = sorted(policy_codes.get(pid, f"POL-{pid}") for pid in ep_policies.get(eid, []))
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
        for row in user_stats:
            uid = row["user_id"]
            evs_u = list(events.filter(endpoint_id__isnull=True, user_id=uid).values("policy_id", "metadata"))
            codes_u = sorted(
                {policy_codes.get(ev["policy_id"], f"POL-{ev['policy_id']}") for ev in evs_u if ev["policy_id"]}
            )
            risk_u = (
                max(((ev.get("metadata") or {}).get("security_risk_score", 0) or 0) for ev in evs_u) if evs_u else 0
            )
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


class TopRulesView(APIView):
    """
    GET /api/policy/top-rules/?limit=10
    Returns rules with highest triggered/blocked counts from EnforcementEvent.
    """

    permission_classes = [IsAuthenticated]

    def get(self, request):
        limit = min(int(request.query_params.get("limit", 10)), 50)
        since = request.query_params.get("since")
        if since:
            try:
                since_dt = timezone.datetime.fromisoformat(since.replace("Z", "+00:00"))
            except Exception:
                since_dt = timezone.now() - timedelta(days=7)
        else:
            since_dt = timezone.now() - timedelta(days=7)

        base_events = EnforcementEvent.objects.filter(created_at__gte=since_dt, rule_id__isnull=False)
        events = _enforcement_events_for_request(request, base_events)
        rule_stats = (
            events.values("rule_id")
            .annotate(
                triggered=Count("id"),
                blocked=Count("id", filter=Q(action=ACTION_BLOCK)),
            )
            .order_by("-triggered")[:limit]
        )

        from auth.utils import get_request_organization

        org = get_request_organization(request)

        rule_ids = [r["rule_id"] for r in rule_stats]
        rules = {r.id: r for r in Rule.objects.filter(id__in=rule_ids).select_related("policy")}
        rules_qs = Rule.objects.filter(policy__enabled=True, enabled=True)
        if org is not None:
            rules_qs = rules_qs.filter(policy__organization=org)
        rules_applied_count = rules_qs.count()

        out = []
        for r in rule_stats:
            rule = rules.get(r["rule_id"])
            if not rule:
                continue
            triggered = r["triggered"]
            blocked = r["blocked"]
            effectiveness = round((blocked / triggered * 100) if triggered else 0, 1)
            out.append(
                {
                    "ruleId": f"{rule.policy.code}-R{rule.id}" if rule.policy else f"R{rule.id}",
                    "ruleName": rule.name,
                    "description": rule.description or "",
                    "policyCode": rule.policy.code if rule.policy else "",
                    "appliedTo": f"{rules_applied_count} rules",
                    "triggered": triggered,
                    "blocked": blocked,
                    "effectiveness": effectiveness,
                }
            )
        return Response(out)
