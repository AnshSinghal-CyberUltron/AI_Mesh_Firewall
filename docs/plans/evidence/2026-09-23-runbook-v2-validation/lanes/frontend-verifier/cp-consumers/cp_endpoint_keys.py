#!/usr/bin/env python3
"""Per-endpoint EnforcementEvent.metadata key sets, derived from reads.tsv
(produced by cp_metadata_keys.py) + a hand-built reachability map whose entries
were verified by reading each view (file:line cited in the final report).

Selector = (file suffix, class or "", function). Extra = explicit keys used to build
rollup facts the endpoint serves from (analytics_rollup.refresh_org_rollups), since the
fact builder is not called in the request path.
"""
import csv
import os

HERE = os.path.dirname(os.path.abspath(__file__))
rows = list(csv.DictReader(open(os.path.join(HERE, "reads.tsv")), delimiter="\t"))

SV = "policy/security_views.py"
M16 = "policy/module_16.py"
FMC = "policy/firewall_module_classifier.py"
SQL = "policy/analytics_sql.py"
RU = "policy/analytics_rollup.py"
AV = "policy/analytics_views.py"
TR = "policy/telemetry_resolution.py"

SERIALIZE = [
    (SV, "ThreatFeedView", "_serialize_threat_feed_page"), (SV, "", "_enrich_scan_detail_metadata"),
    (SV, "", "_synthesize_pipeline_from_stage_metrics"), (SV, "", "_synthesize_routing_only_pipeline"),
    (SV, "", "_tools_invoked_with_model"), (SV, "", "_sanitized_meta_for_client"),
]
CLASSIFIER = [(FMC, "", "_norm_source"), (FMC, "", "_norm_fields"), (FMC, "", "event_matches_module"),
              (FMC, "", "specialty_modules_for_event"), (M16, "", "is_module_16_enforcement")]
GROUP_FACT = {"source", "event_type", "module", "module_id", "owasp_code", "owasp_codes", "threat_category",
              "threat_type", "is_audit_log", "is_isolation_event", "trigger_source", "pii_detected",
              "security_risk_score"}  # analytics_rollup.py:37-50 + :240-241 (annotate_is_critical)
ROW_REQ_FACT = {"security_risk_score", "latency_ms", "request_id"}  # analytics_rollup.py:170-178

ENDPOINTS = {
    "/api/security/threat-feed/": ([(SV, "ThreatFeedView", "get"), (SV, "ThreatFeedView", "_get_module_16_threat_feed"),
                                    (M16, "", "module_16_enforcement_q"), (M16, "", "merge_module_16_feed_items")]
                                   + SERIALIZE + [(M16, "", "is_module_16_enforcement")], set()),
    "/api/security/threat-feed/<pk>/": ([(SV, "ThreatFeedEventDetailView", "get"), (SV, "", "_merge_related_scan_metadata"),
                                         (SV, "", "_pipeline_io_fingerprint"), (SV, "", "_merge_pipeline_trace_stages")]
                                        + SERIALIZE, set()),
    "/api/security/soc-kpis/": ([(SV, "SocKpisView", "get"), (SQL, "", "soc_kpis_from_events"),
                                 (SQL, "", "annotate_request_key"), (RU, "", "soc_kpis_from_rollup"),
                                 (RU, "", "_merge_request_partition")], ROW_REQ_FACT),
    "/api/security/module-kpis/": ([(SV, "ModuleKpisView", "get"), (SQL, "", "annotate_is_critical"),
                                    (RU, "", "module_kpis_from_rollup"), (RU, "", "_module_groups_from_events"),
                                    (RU, "", "_accumulate_module"), (RU, "", "_is_critical_meta")]
                                   + CLASSIFIER, set()),
    "/api/security/module-trends/": ([(SV, "ModuleTrendsView", "get"), (SQL, "", "annotate_is_critical"),
                                      (RU, "", "fill_module_trends_from_rollup"), (RU, "", "_trend_rows"),
                                      (RU, "", "_module_groups_from_events"), (RU, "", "_accumulate_module"),
                                      (RU, "", "_is_critical_meta")] + CLASSIFIER, set()),
    "/api/security/attack-vector-trends/": ([(SV, "AttackVectorTrendsView", "get"), (SV, "", "_event_to_vectors_simple"),
                                             (SV, "", "_get_owasp_codes"), (RU, "", "fill_vector_buckets_from_rollup"),
                                             (RU, "", "_trend_rows"), (RU, "", "_module_groups_from_events"),
                                             ], set()),
    "/api/security/module-charts/<id>/": ([(SV, "ModuleChartsView", "*"), (FMC, "", "module_enforcement_q"),
                                           (M16, "", "module_16_enforcement_q")], set()),
    "/api/security/owasp-stats/": ([(SV, "OwaspStatsView", "get")], set()),
    "/api/security/rag-pipeline-kpis/": ([(SV, "RAGPipelineStageKpisView", "get"), (SQL, "", "annotate_escalation_level")], set()),
    "/api/security/rag-pipeline-trace/<rid>/": ([(SV, "RAGPipelineTraceView", "get"), (SQL, "", "annotate_escalation_level")], set()),
    "/api/policies/analytics/": ([(AV, "PolicyAnalyticsView", "get")], set()),
    "/api/policies/top-violators/": ([(AV, "TopViolatorsView", "get"), (TR, "", "metadata_policy_codes")], set()),
    "/api/policy/top-rules/": ([(AV, "TopRulesView", "get"), (AV, "", "_aggregate_top_rules"),
                                (TR, "", "metadata_policy_codes"), (TR, "", "metadata_rule_names")], set()),
    "/api/notifications/": ([("policy/notification_views.py", "NotificationsListView", "get")], set()),
    "(drain) SecurityIncident rows -> /api/security/incidents/": ([("core/tasks.py", "", "_auto_create_review_items_and_incidents")], set()),
    "(drain) isolation bell Notification rows -> /api/notifications/": ([("core/isolation_notify.py", "", "create_isolation_bell_notifications_for_events")], set()),
}


def match(row, sel):
    f, cls, fn = sel
    if not row["file:line"].split(":")[0].endswith(f):
        return False
    if cls and row["class"] != cls:
        return False
    if not cls and row["class"]:
        return False
    return fn == "*" or row["func"] == fn


out = []
for ep, (sels, extra) in ENDPOINTS.items():
    top, nested, sites = set(extra), set(), set()
    for r in rows:
        if r["cat"] not in ("ee_read", "fact_read") or r["key"].startswith("<"):
            continue
        if any(match(r, s) for s in sels):
            if r["key"] == "is_critical":
                continue  # derived fact key, not a gateway key
            if r["path"]:
                nested.add(f'{r["path"]}.{r["key"]}')
            else:
                top.add(r["key"])
            sites.add(r["file:line"].replace("control/ai_mesh_control/", ""))
    out.append((ep, top, nested, sites))

with open(os.path.join(HERE, "endpoint_keys.txt"), "w") as fh:
    for ep, top, nested, sites in out:
        line = (f"{ep}\n  top-level ({len(top)}): {', '.join(sorted(top))}\n"
                f"  nested ({len(nested)}): {', '.join(sorted(nested))}\n"
                f"  read sites: {', '.join(sorted(sites, key=lambda s: (s.split(':')[0], int(s.split(':')[1]))))}\n")
        fh.write(line)
        print(line)
