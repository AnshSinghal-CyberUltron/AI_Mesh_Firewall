"""
Dashboard summary API for Executive Summary and other overview pages.
"""

from collections import defaultdict
from datetime import timedelta

from django.http import HttpResponse
from django.db.models import Avg, Count, DurationField, ExpressionWrapper, F, Q
from django.utils import timezone
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from core.models import Agent, Endpoint
from policy.constants import ACTION_BLOCK, ACTION_REDACT
from policy.models import ComplianceViolation, EnforcementEvent, Policy, Rule
from policy.analytics_concurrency import AnalyticsConcurrencyMixin
from policy.security_views import OWASP_ALL_VECTORS, _enforcement_events_for_request
try:
    from third_party_integrations.export_reporter import ExportReporter
except ModuleNotFoundError:
    class ExportReporter:  # noqa: D101 — export fallback for firewall SKU
        def _generate_html(self, template: str, context: dict) -> str:
            import json
            body = json.dumps(context, indent=2, default=str)
            return f"<!DOCTYPE html><html><body><h1>AI Mesh Firewall report</h1><pre>{body}</pre></body></html>"


def _dashboard_org_context(request):
    """
    Return (org, endpoint_ids) for dashboard scoping.
    - org None and endpoint_ids None: superuser, no filter (use all data).
    - org set and endpoint_ids list: filter by these endpoint ids (and org for Policy).
    - org None and endpoint_ids []: non-superuser without org, show empty.
    """
    from auth.utils import get_request_organization
    org = get_request_organization(request)
    if org is not None:
        endpoint_ids = list(Endpoint.objects.filter(organization=org).values_list("id", flat=True))
        return org, endpoint_ids
    if getattr(request, "user", None) and request.user.is_superuser:
        return None, None
    return None, []


class DashboardSummaryView(APIView):
    """
    GET /api/dashboard/summary/
    Returns aggregate counts and rates for dashboard hero and summary cards.
    """

    permission_classes = [IsAuthenticated]

    def get(self, request):
        org, endpoint_ids = _dashboard_org_context(request)
        now = timezone.now()
        thirty_days_ago = now - timedelta(days=30)

        # Only short-circuit for the genuine no-org case (non-superuser without an
        # organization). A real org with ZERO endpoints can still have thousands of
        # telemetry-drain events (organization set, endpoint_id NULL); those are
        # counted via the org-FK scoping below — so don't zero it out here.
        if org is None and endpoint_ids is not None and len(endpoint_ids) == 0:
            return Response(
                {
                    "endpoint_count": 0,
                    "total_threats_30d": 0,
                    "block_rate": 0.0,
                    "active_policies_count": 0,
                    "rules_applied": 0,
                    "total_agents": 0,
                    "agents_by_type": {},
                    "mttr_minutes": None,
                    "owasp_coverage_percent": None,
                    "compliance_count": None,
                    "enforcement_rate": None,
                    # Backwards compat: keep legacy field (unused by new UI)
                    "false_positive_percent": None,
                }
            )

        endpoints_qs = Endpoint.objects.all()
        agents_qs = Agent.objects.all()
        policies_qs = Policy.objects.filter(enabled=True)
        if org is not None:
            endpoints_qs = endpoints_qs.filter(organization=org)
            agents_qs = agents_qs.filter(endpoint__organization=org)
            policies_qs = policies_qs.filter(organization=org)

        endpoint_count = endpoints_qs.count()
        active_policies_count = policies_qs.count()
        rules_applied = Rule.objects.filter(policy__in=policies_qs, enabled=True).count()

        # Scope by org FK (telemetry-drain events have organization set, endpoint NULL)
        # in addition to legacy endpoint scoping; matches ModuleKpisView.
        events_30d = _enforcement_events_for_request(
            request, EnforcementEvent.objects.filter(created_at__gte=thirty_days_ago)
        )
        total_threats_30d = events_30d.count()
        blocked_30d = events_30d.filter(action=ACTION_BLOCK).count()
        redacted_30d = events_30d.filter(action=ACTION_REDACT).count()
        block_rate = (blocked_30d / total_threats_30d * 100) if total_threats_30d else 0
        enforcement_rate = (
            ((blocked_30d + redacted_30d) / total_threats_30d * 100) if total_threats_30d else None
        )

        agent_counts = agents_qs.values("agent_type").annotate(count=Count("id"))
        agents_by_type = {row["agent_type"]: row["count"] for row in agent_counts}
        total_agents = sum(agents_by_type.values())

        # MTTR: average time from creation to resolution for resolved events in the last 30 days
        resolved_events_30d = _enforcement_events_for_request(
            request,
            EnforcementEvent.objects.filter(
                incident_status="resolved",
                resolved_at__isnull=False,
                created_at__gte=thirty_days_ago,
            ),
        )
        avg_duration = resolved_events_30d.annotate(
            duration=ExpressionWrapper(
                F("resolved_at") - F("created_at"),
                output_field=DurationField(),
            )
        ).aggregate(avg=Avg("duration"))["avg"]
        mttr_minutes = round(avg_duration.total_seconds() / 60, 1) if avg_duration else None

        # OWASP Coverage: average block rate across OWASP vectors that had detections (last 30 days)
        owasp_events = events_30d.values("metadata", "action")
        by_code = defaultdict(lambda: {"detected": 0, "blocked": 0})
        for ev in owasp_events:
            meta = ev.get("metadata") or {}
            codes = meta.get("owasp_codes")
            if codes is None:
                single = (meta.get("owasp_code") or "").strip().upper()
                codes = [single] if single else []
            else:
                codes = [str(c).strip().upper() for c in codes if c]
            for code in codes:
                if code and code in OWASP_ALL_VECTORS:
                    by_code[code]["detected"] += 1
                    if ev["action"] == ACTION_BLOCK:
                        by_code[code]["blocked"] += 1

        coverages = []
        for code in OWASP_ALL_VECTORS:
            d, b = by_code[code]["detected"], by_code[code]["blocked"]
            if d > 0:
                coverages.append((b / d) * 100)

        owasp_coverage_percent = round(sum(coverages) / len(coverages), 1) if coverages else None

        # Compliance: count frameworks with >= 95% resolution rate (scoped by org events)
        compliance_qs = ComplianceViolation.objects.all()
        if endpoint_ids is not None:
            compliance_qs = compliance_qs.filter(
                enforcement_event__endpoint_id__in=endpoint_ids
            )
        compliance_fw = list(
            compliance_qs.values("framework").annotate(
                total=Count("id"),
                resolved=Count("id", filter=Q(status="resolved")),
            )
        )
        if compliance_fw:
            compliant_frameworks = sum(
                1 for fw in compliance_fw
                if fw["total"] and (fw["resolved"] / fw["total"]) >= 0.95
            )
            compliance_count = compliant_frameworks
        else:
            compliance_count = None

        return Response(
            {
                "endpoint_count": endpoint_count,
                "total_threats_30d": total_threats_30d,
                "block_rate": round(block_rate, 1),
                "active_policies_count": active_policies_count,
                "rules_applied": rules_applied,
                "total_agents": total_agents,
                "agents_by_type": agents_by_type,
                "mttr_minutes": mttr_minutes,
                "owasp_coverage_percent": owasp_coverage_percent,
                "compliance_count": compliance_count,
                "enforcement_rate": round(enforcement_rate, 1) if enforcement_rate is not None else None,
                # Backwards compat: keep legacy field (unused by new UI)
                "false_positive_percent": None,
            }
        )


class DashboardReportView(APIView):
    """
    GET /api/dashboard/report/?days=30&export_format=html
    Returns an Executive Summary report as a downloadable document.
    """

    permission_classes = [IsAuthenticated]

    def get(self, request):
        # IMPORTANT: Do not use query param name "format" here.
        # DRF reserves "?format=" for renderer/content-negotiation and will 404
        # for unsupported renderers (e.g., html) before this view runs.
        fmt = (request.query_params.get("export_format") or "html").lower()
        if fmt not in ("html",):
            return Response({"detail": "Unsupported export_format. Use export_format=html."}, status=400)

        days = min(int(request.query_params.get("days", 30)), 365)
        org, endpoint_ids = _dashboard_org_context(request)
        since = timezone.now() - timedelta(days=days)

        # Reuse the same scoping logic as summary.
        endpoints_qs = Endpoint.objects.all()
        agents_qs = Agent.objects.all()
        policies_qs = Policy.objects.filter(enabled=True)
        if org is not None:
            endpoints_qs = endpoints_qs.filter(organization=org)
            agents_qs = agents_qs.filter(endpoint__organization=org)
            policies_qs = policies_qs.filter(organization=org)
        elif endpoint_ids is not None and len(endpoint_ids) == 0:
            endpoints_qs = endpoints_qs.none()
            agents_qs = agents_qs.none()
            policies_qs = policies_qs.none()

        events_qs = _enforcement_events_for_request(
            request, EnforcementEvent.objects.filter(created_at__gte=since)
        )

        summary = {
            "period_days": days,
            "endpoint_count": endpoints_qs.count(),
            "total_agents": agents_qs.count(),
            "active_policies_count": policies_qs.count(),
            "rules_applied": Rule.objects.filter(policy__in=policies_qs, enabled=True).count(),
            "total_events": events_qs.count(),
            "blocked_events": events_qs.filter(action=ACTION_BLOCK).count(),
            "redacted_events": events_qs.filter(action=ACTION_REDACT).count(),
        }

        # Lightweight highlights (avoid huge payloads).
        recent_events = list(
            events_qs.order_by("-created_at").values("id", "action", "created_at", "endpoint_id", "user_id", "metadata")[:50]
        )

        reporter = ExportReporter()
        html = reporter._generate_html(
            "executive_summary",
            {
                "summary": summary,
                "recent_events": recent_events,
            },
        )

        ts = timezone.now().strftime("%Y-%m-%d")
        filename = f"AI-Mesh-Firewall-Executive-Report-{ts}.html"
        resp = HttpResponse(html, content_type="text/html; charset=utf-8")
        resp["Content-Disposition"] = f'attachment; filename="{filename}"'
        return resp

class ComplianceSummaryView(APIView):
    """
    GET /api/dashboard/compliance/?days=30
    Returns per-framework compliance aggregates derived from ComplianceViolation rows.
    """

    permission_classes = [IsAuthenticated]

    def get(self, request):
        org, endpoint_ids = _dashboard_org_context(request)
        days = min(int(request.query_params.get("days", 30)), 365)
        since = timezone.now() - timedelta(days=days)

        cv_qs = ComplianceViolation.objects.filter(created_at__gte=since)
        if endpoint_ids is not None:
            cv_qs = cv_qs.filter(enforcement_event__endpoint_id__in=endpoint_ids)
        frameworks = list(
            cv_qs.values("framework").annotate(
                total=Count("id"),
                resolved=Count("id", filter=Q(status="resolved")),
                pending=Count("id", filter=Q(status="open")),
                avg_resolution=Avg(
                    ExpressionWrapper(
                        F("resolved_at") - F("created_at"),
                        output_field=DurationField(),
                    ),
                    filter=Q(status="resolved", resolved_at__isnull=False),
                ),
            )
        )

        results = []
        for fw in frameworks:
            total = fw["total"]
            resolved = fw["resolved"]
            rate = round((resolved / total * 100), 1) if total else 0.0
            avg_res = fw.get("avg_resolution")
            avg_minutes = None
            if avg_res:
                try:
                    avg_minutes = round(avg_res.total_seconds() / 60)
                except Exception:
                    avg_minutes = None
            results.append(
                {
                    "framework": fw["framework"],
                    "totalViolations": total,
                    "resolved": resolved,
                    "pending": fw["pending"],
                    "resolutionRate": rate,
                    "avgResolutionTime": avg_minutes,
                    "lastAudit": None,
                    "status": "COMPLIANT" if rate >= 95 else "AT RISK",
                }
            )

        total_violations = sum(r["totalViolations"] for r in results)
        total_resolved = sum(r["resolved"] for r in results)
        overall_score = (
            round((total_resolved / total_violations * 100), 1) if total_violations else None
        )
        compliant_count = sum(1 for r in results if r["status"] == "COMPLIANT")

        return Response(
            {
                "frameworks": results,
                "overall_score": overall_score,
                "compliant_frameworks": compliant_count,
                "total_pending": sum(r["pending"] for r in results),
            }
        )


class DashboardAgentsView(APIView):
    """
    GET /api/dashboard/agents/
    Returns agent list with endpoint and enforcement stats for SOC (JWT). Optional limit (default 50).
    """

    permission_classes = [IsAuthenticated]

    def get(self, request):
        org, endpoint_ids = _dashboard_org_context(request)
        limit = min(int(request.query_params.get("limit", 50)), 200)
        since = timezone.now() - timedelta(days=7)
        agents_qs = Agent.objects.select_related("endpoint").order_by("-updated_at")
        if org is not None:
            agents_qs = agents_qs.filter(endpoint__organization=org)
        elif endpoint_ids is not None and len(endpoint_ids) == 0:
            agents_qs = agents_qs.none()
        agents = agents_qs[:limit]
        rules_qs = Rule.objects.filter(policy__enabled=True, enabled=True)
        if org is not None:
            rules_qs = rules_qs.filter(policy__organization=org)
        rules_applied = rules_qs.count()
        out = []
        for agent in agents:
            evs = EnforcementEvent.objects.filter(agent=agent, created_at__gte=since)
            triggered = evs.count()
            blocked = evs.filter(action=ACTION_BLOCK).count()
            meta = agent.metadata or {}
            endpoint = agent.endpoint
            ep_meta = endpoint.metadata or {} if endpoint else {}
            endpoint_username = (ep_meta.get("endpoint_username") or "").strip() or None
            user_display = f"User {agent.user_id}" if agent.user_id is not None else None
            out.append(
                {
                    "agent_id": str(agent.id),
                    "agent_type": agent.get_agent_type_display()
                    if hasattr(agent, "get_agent_type_display")
                    else agent.agent_type,
                    "name": agent.name,
                    "user": user_display or "—",
                    "endpoint_username": endpoint_username,
                    "device": endpoint.identifier if endpoint else "—",
                    "rules_applied": rules_applied,
                    "rules_triggered": triggered,
                    "total_requests": meta.get("total_requests") or triggered,
                    "blocked": blocked,
                    "allowed": (meta.get("allowed") or max(0, triggered - blocked)),
                    "redacted": meta.get("redacted") or 0,
                    "block_rate": round((blocked / triggered * 100) if triggered else 0, 1),
                    "avg_risk": meta.get("avg_risk") or 0,
                    "copilot_services": ", ".join(ep_meta.get("active_copilots") or []) or "—",
                    "status": agent.status.upper() if agent.status else "ACTIVE",
                }
            )
        return Response(out)


def _model_to_provider(model_str):
    """Derive provider from model string."""
    if not model_str or model_str == "Unknown":
        return "Unknown"
    m = (model_str or "").lower()
    if m.startswith("gpt-") or "openai" in m:
        return "OpenAI"
    if m.startswith("claude-") or "anthropic" in m:
        return "Anthropic"
    if "gemini" in m or "google" in m or "palm" in m:
        return "Google"
    if "bedrock" in m or "amazon" in m:
        return "Amazon"
    if "llama" in m or "ollama" in m or "mistral" in m:
        return "Local/Open"
    return "Other"


class ModelUsageView(AnalyticsConcurrencyMixin, APIView):
    """
    GET /api/dashboard/model-usage/?days=30
    Aggregates EnforcementEvent by metadata.model; returns per-model usage stats.
    """

    permission_classes = [IsAuthenticated]

    def get(self, request):
        org, endpoint_ids = _dashboard_org_context(request)
        days = min(int(request.query_params.get("days", 30)), 365)
        since = timezone.now() - timedelta(days=days)
        events_qs = _enforcement_events_for_request(
            request, EnforcementEvent.objects.filter(created_at__gte=since)
        ).values("metadata", "action")
        events = events_qs

        by_model = defaultdict(lambda: {"total": 0, "blocked": 0, "allowed": 0, "risk_scores": []})
        from core.model_state_bootstrap import canonicalize_model_name_safe
        for ev in events:
            meta = ev.get("metadata") or {}
            model = meta.get("model") or "Unknown"
            model = str(model).strip() or "Unknown"
            # P5c: collapse raw guard/upstream model ids to the public label so the
            # model-usage breakdown never leaks bedrock/claude-haiku/gpt-oss ids.
            model = canonicalize_model_name_safe(model) or "Unknown"
            by_model[model]["total"] += 1
            if ev["action"] == ACTION_BLOCK:
                by_model[model]["blocked"] += 1
            else:
                by_model[model]["allowed"] += 1
            rs = meta.get("security_risk_score")
            if rs is not None:
                try:
                    by_model[model]["risk_scores"].append(float(rs))
                except (TypeError, ValueError):
                    pass

        results = []
        for model, data in sorted(by_model.items(), key=lambda x: -x[1]["total"]):
            total = data["total"]
            blocked = data["blocked"]
            allowed = data["allowed"]
            block_rate = round(blocked / total * 100, 2) if total else 0.0
            avg_risk = (
                round(sum(data["risk_scores"]) / len(data["risk_scores"]), 1)
                if data["risk_scores"] else 0.0
            )
            if block_rate < 5:
                health = "Healthy"
            elif block_rate < 10:
                health = "Warning"
            else:
                health = "Critical"
            results.append({
                "model": model,
                "provider": _model_to_provider(model),
                "totalRequests": total,
                "allowed": allowed,
                "blocked": blocked,
                "blockRate": block_rate,
                "avgRiskScore": avg_risk,
                "health": health,
            })
        return Response(results)


class AIServicesView(APIView):
    """
    GET /api/dashboard/ai-services/
    Aggregates Endpoint metadata (active_copilots, detected_services) with threats from EnforcementEvent.
    """

    permission_classes = [IsAuthenticated]

    def get(self, request):
        org, endpoint_ids_list = _dashboard_org_context(request)
        since = timezone.now() - timedelta(hours=24)
        endpoints = Endpoint.objects.all()
        if org is not None:
            endpoints = endpoints.filter(organization=org)
        elif endpoint_ids_list is not None and len(endpoint_ids_list) == 0:
            return Response([])
        endpoint_ids = list(endpoints.values_list("id", flat=True))

        # threats_24h per endpoint
        threats_by_ep = {}
        if endpoint_ids:
            counts = (
                EnforcementEvent.objects.filter(endpoint_id__in=endpoint_ids, created_at__gte=since)
                .values("endpoint_id")
                .annotate(cnt=Count("id"))
            )
            threats_by_ep = {row["endpoint_id"]: row["cnt"] for row in counts}

        # Aggregate services: { service: { endpoints: set(), threat_sum: int } }
        by_service = defaultdict(lambda: {"endpoint_ids": set(), "threat_sum": 0})
        for ep in endpoints:
            meta = ep.metadata or {}
            services = list(meta.get("active_copilots") or []) + list(meta.get("detected_services") or [])
            threats = threats_by_ep.get(ep.id, 0)
            for svc in services:
                svc = str(svc).strip()
                if svc:
                    by_service[svc]["endpoint_ids"].add(ep.id)
                    by_service[svc]["threat_sum"] += threats

        results = [
            {"service": svc, "endpoints": len(data["endpoint_ids"]), "threats": data["threat_sum"]}
            for svc, data in sorted(by_service.items(), key=lambda x: -len(x[1]["endpoint_ids"]))
        ]
        return Response(results)


class RiskDistributionView(APIView):
    """
    GET /api/dashboard/risk-distribution/?days=30&buckets=default|fine
    Buckets EnforcementEvent by metadata.security_risk_score.
    Default: Low/Medium/High. Fine: 0-20, 21-40, 41-60, 61-80, 81-100.
    """

    permission_classes = [IsAuthenticated]

    _FINE_BUCKETS = [
        (0, 20, "0-20", "#10b981"),
        (21, 40, "21-40", "#84cc16"),
        (41, 60, "41-60", "#f59e0b"),
        (61, 80, "61-80", "#f97316"),
        (81, 100, "81-100", "#ef4444"),
    ]

    def get(self, request):
        org, endpoint_ids = _dashboard_org_context(request)
        days = min(int(request.query_params.get("days", 30)), 365)
        buckets_mode = request.query_params.get("buckets", "default").lower()
        since = timezone.now() - timedelta(days=days)
        events_qs = _enforcement_events_for_request(
            request, EnforcementEvent.objects.filter(created_at__gte=since)
        ).values("metadata")
        events = events_qs

        if buckets_mode == "fine":
            fine_counts = [0] * len(self._FINE_BUCKETS)
            for ev in events:
                meta = ev.get("metadata") or {}
                score = meta.get("security_risk_score")
                try:
                    s = float(score) if score is not None else 0.0
                except (TypeError, ValueError):
                    s = 0.0
                s = max(0, min(100, s))
                for i, (lo, hi, _, _) in enumerate(self._FINE_BUCKETS):
                    if lo <= s <= hi:
                        fine_counts[i] += 1
                        break
            results = [
                {"name": f"{label}: {cnt}", "value": cnt, "color": color, "range": label}
                for (_, _, label, color), cnt in zip(self._FINE_BUCKETS, fine_counts)
            ]
        else:
            low, medium, high = 0, 0, 0
            for ev in events:
                meta = ev.get("metadata") or {}
                score = meta.get("security_risk_score")
                try:
                    s = float(score) if score is not None else 0.0
                except (TypeError, ValueError):
                    s = 0.0
                if s <= 33:
                    low += 1
                elif s <= 66:
                    medium += 1
                else:
                    high += 1
            results = [
                {"name": "Low Risk", "value": low, "color": "#10b981"},
                {"name": "Medium Risk", "value": medium, "color": "#f59e0b"},
                {"name": "High Risk", "value": high, "color": "#ef4444"},
            ]
        return Response(results)
