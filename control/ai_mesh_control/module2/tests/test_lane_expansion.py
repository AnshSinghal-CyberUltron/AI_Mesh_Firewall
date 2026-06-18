"""Tests for the Module 2 four-lane expansion.

Two layers:
- LaneHelperUnitTests (SimpleTestCase, no DB) — pure analytics helpers fed
  with fake querysets.
- LaneExpansionApiTests (TestCase, DB) — the new /rag/health/ and /mcp/risk/
  endpoints, dashboard lane keys, stage_hit_distribution, and incident
  source filters.
"""

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from module2.analytics import (
    build_lane_summary,
    build_mcp_activity_payload,
    build_rag_pipeline_kpis,
    build_stage_hit_distribution,
    build_vector_exposure_payload,
    count_monitored_events,
    count_rerouted_events,
    event_source,
)
from policy.constants import ACTION_BLOCK, ACTION_MONITOR, ACTION_REDACT

User = get_user_model()


class FakeQS:
    """Mimics the minimal queryset surface the analytics helpers use."""

    def __init__(self, rows):
        self._rows = rows

    def values(self, *fields):
        return [{f: row.get(f) for f in fields} for row in self._rows]


def _row(action="allow", **meta):
    return {"action": action, "metadata": meta}


class EventSourceLaneTests(SimpleTestCase):
    def test_mcp_lane(self):
        self.assertEqual(event_source({"event_type": "mcp_tool_call"}), "mcp")
        self.assertEqual(event_source({"tools_invoked": ["read_file"]}), "mcp")

    def test_rag_lane(self):
        self.assertEqual(event_source({"event_type": "rag_pipeline"}), "rag")

    def test_vector_lane_via_collection(self):
        self.assertEqual(event_source({"collection": "docs"}), "vector")
        self.assertEqual(event_source({"vector_collection": "kb"}), "vector")
        self.assertEqual(event_source({"vector_namespace": "ns1"}), "vector")

    def test_threat_intel_lane(self):
        self.assertEqual(event_source({"source": "threat_intel"}), "threat_intel")
        self.assertEqual(event_source({"threat_type": "threat_intel_match"}), "threat_intel")

    def test_ueba_lane(self):
        self.assertEqual(event_source({"key_prefix": "zs_abc"}), "ueba")

    def test_chat_default(self):
        self.assertEqual(event_source({}), "chat")

    def test_event_type_beats_collection(self):
        # An MCP event with a collection key still classifies as mcp.
        self.assertEqual(
            event_source({"event_type": "mcp_tool_call", "collection": "docs"}), "mcp"
        )


class LaneHelperUnitTests(SimpleTestCase):
    def test_build_lane_summary_counts_and_block_rate(self):
        qs = FakeQS([
            _row(ACTION_BLOCK, event_type="mcp_tool_call"),
            _row("allow", event_type="mcp_tool_call"),
            _row(ACTION_BLOCK, event_type="rag_pipeline", pipeline_stage="query"),
            _row("allow", collection="docs"),
            _row("allow"),
            # ueba/threat_intel lanes fold into chat in the dashboard grid
            _row("allow", key_prefix="zs_x"),
        ])
        summary = build_lane_summary(qs)
        self.assertEqual(set(summary.keys()), {"chat", "rag", "vector", "mcp"})
        self.assertEqual(summary["mcp"]["total"], 2)
        self.assertEqual(summary["mcp"]["blocked"], 1)
        self.assertEqual(summary["mcp"]["block_rate_pct"], 50.0)
        self.assertEqual(summary["rag"]["total"], 1)
        self.assertEqual(summary["vector"]["total"], 1)
        self.assertEqual(summary["chat"]["total"], 2)

    def test_build_lane_summary_zero_events_has_zero_block_rate(self):
        summary = build_lane_summary(FakeQS([]))
        for lane in ("chat", "rag", "vector", "mcp"):
            self.assertEqual(summary[lane]["total"], 0)
            self.assertEqual(summary[lane]["blocked"], 0)
            self.assertEqual(summary[lane]["block_rate_pct"], 0.0)

    def test_build_rag_pipeline_kpis_stages_and_funnel(self):
        qs = FakeQS([
            _row("allow", event_type="rag_pipeline", pipeline_stage="query"),
            _row(ACTION_BLOCK, event_type="rag_pipeline", pipeline_stage="retriever"),
            _row("allow", event_type="rag_pipeline", pipeline_stage="retriever", latency_ms=100),
            _row("allow", event_type="rag_pipeline", pipeline_stage="ranker"),
            _row(ACTION_BLOCK, event_type="rag_pipeline", pipeline_stage="generator"),
            # non-RAG event must be ignored
            _row(ACTION_BLOCK, event_type="mcp_tool_call"),
        ])
        kpis = build_rag_pipeline_kpis(qs)
        stages = kpis["stages"]
        self.assertEqual(stages["query"]["total"], 1)
        self.assertEqual(stages["retriever"]["total"], 2)
        self.assertEqual(stages["retriever"]["blocked"], 1)
        self.assertEqual(stages["retriever"]["avg_latency_ms"], 100.0)
        self.assertEqual(stages["generator"]["blocked"], 1)
        funnel = kpis["document_funnel"]
        self.assertEqual(funnel["retrieved"], 2)
        self.assertEqual(funnel["post_ranker"], 1)   # 1 ranker total - 0 blocked
        self.assertEqual(funnel["post_generator"], 0)  # 1 generator - 1 blocked
        self.assertIn("escalation_distribution", kpis)

    def test_build_mcp_activity_payload_ledger_direction_servers(self):
        qs = FakeQS([
            _row(ACTION_BLOCK, event_type="mcp_tool_call", tools_invoked=["execute_sql"],
                 mcp_server="srv-a", mcp_direction="inbound"),
            _row(ACTION_REDACT, event_type="mcp_tool_call", tools_invoked=["read_file"],
                 server_slug="srv-b", scan_direction="outbound"),
            # legacy MCP envelope without event_type still counts
            _row("allow", tools_invoked=["read_file"], server_slug="srv-b"),
            _row("allow", event_type="mcp_tool_call", tools_invoked="execute_sql",
                 server_slug="srv-a"),
            # non-MCP rows ignored
            _row(ACTION_BLOCK, event_type="rag_pipeline", pipeline_stage="query"),
        ])
        payload = build_mcp_activity_payload(qs)
        self.assertEqual(payload["summary"]["total_events"], 4)
        self.assertEqual(payload["summary"]["blocked_tool_calls"], 1)
        self.assertEqual(payload["summary"]["redacted_arguments"], 1)
        self.assertEqual(payload["summary"]["unique_tools"], 2)

        ledger = {r["tool"]: r["violations"] for r in payload["tool_ledger"]}
        self.assertEqual(ledger["execute_sql"], 1)
        self.assertEqual(ledger["read_file"], 1)

        direction = payload["direction_split"]
        self.assertEqual(direction["inbound"]["total"], 3)  # explicit + default + legacy
        self.assertEqual(direction["inbound"]["blocked"], 1)
        self.assertEqual(direction["outbound"]["total"], 1)
        self.assertEqual(direction["outbound"]["blocked"], 1)  # redact counts

        servers = {r["server"]: r["total"] for r in payload["top_servers"]}
        self.assertEqual(servers["srv-a"], 2)
        self.assertEqual(servers["srv-b"], 2)

    def test_build_vector_exposure_payload_ranks_by_block_rate(self):
        qs = FakeQS([
            _row(ACTION_BLOCK, collection="secrets"),
            _row("allow", collection="secrets"),
            _row("allow", vector_collection="public"),
            _row(ACTION_REDACT, vector_namespace="ns-pii"),
            _row("allow", model="gpt-4o"),  # non-vector event must be ignored
        ])
        payload = build_vector_exposure_payload(qs)
        rows = {r["collection"]: r for r in payload["collections"]}
        self.assertEqual(rows["secrets"]["total"], 2)
        self.assertEqual(rows["secrets"]["blocked"], 1)
        self.assertEqual(rows["secrets"]["block_rate_pct"], 50.0)
        self.assertEqual(rows["public"]["blocked"], 0)
        self.assertEqual(rows["ns-pii"]["redacted"], 1)
        self.assertNotIn("unknown", rows)
        # highest block rate first
        self.assertEqual(payload["collections"][0]["collection"], "secrets")

    def test_build_stage_hit_distribution_only_threat_intel(self):
        qs = FakeQS([
            _row(ACTION_BLOCK, source="threat_intel", pipeline_stage="query"),
            _row(ACTION_BLOCK, source="threat_intel", pipeline_stage="query"),
            _row(ACTION_BLOCK, source="threat_intel"),  # no stage -> ingress
            _row(ACTION_BLOCK, event_type="mcp_tool_call"),  # not threat intel
        ])
        dist = build_stage_hit_distribution(qs)
        as_map = {r["stage"]: r["count"] for r in dist}
        self.assertEqual(as_map["query"], 2)
        self.assertEqual(as_map["ingress"], 1)
        self.assertNotIn("mcp_tool_call", as_map)


class LaneExpansionApiTests(TestCase):
    def setUp(self):
        from auth.models import Organization, UserProfile

        self.org = Organization.objects.create(name="Lane Org", slug="lane-org")
        self.user = User.objects.create_user(username="laneuser", password="pass-lane")
        profile, _ = UserProfile.objects.get_or_create(user=self.user)
        profile.organization = self.org
        profile.save(update_fields=["organization"])

        self.client = APIClient()
        self.client.force_authenticate(user=self.user)

    def _event(self, action, **meta):
        from policy.models import EnforcementEvent

        return EnforcementEvent.objects.create(
            organization=self.org,
            action=action,
            metadata=meta,
        )

    def _incident(self, title, **meta):
        from policy.models import SecurityIncident

        ev = self._event(ACTION_BLOCK, **meta)
        return SecurityIncident.objects.create(
            organization=self.org,
            enforcement_event=ev,
            title=title,
            severity="high",
            status="open",
        )

    def test_rag_health_returns_kpis_and_vector_exposure(self):
        self._event("allow", event_type="rag_pipeline", pipeline_stage="query")
        self._event(ACTION_BLOCK, event_type="rag_pipeline", pipeline_stage="retriever")
        self._event(ACTION_BLOCK, collection="finance_docs")

        resp = self.client.get("/api/module2/rag/health/?period=24h")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["period"], "24h")
        self.assertIn("rag_pipeline_kpis", data)
        self.assertIn("vector_exposure", data)
        self.assertEqual(data["rag_pipeline_kpis"]["stages"]["retriever"]["blocked"], 1)
        collections = {r["collection"] for r in data["vector_exposure"]["collections"]}
        self.assertIn("finance_docs", collections)

    def test_mcp_risk_returns_ledger_direction_servers(self):
        self._event(
            ACTION_BLOCK,
            event_type="mcp_tool_call",
            tools_invoked=["execute_sql"],
            server_slug="stub",
            mcp_direction="inbound",
        )
        self._event(
            ACTION_REDACT,
            event_type="mcp_tool_call",
            tools_invoked=["read_file"],
            server_slug="stub",
            mcp_direction="outbound",
        )

        resp = self.client.get("/api/module2/mcp/risk/?period=24h")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["summary"]["total_events"], 2)
        self.assertEqual(data["summary"]["blocked_tool_calls"], 1)
        self.assertEqual(data["summary"]["redacted_arguments"], 1)
        tools = {r["tool"] for r in data["tool_ledger"]}
        self.assertEqual(tools, {"execute_sql", "read_file"})
        self.assertEqual(data["direction_split"]["inbound"]["blocked"], 1)
        self.assertEqual(data["direction_split"]["outbound"]["blocked"], 1)
        self.assertEqual(data["top_servers"][0]["server"], "stub")

    def test_dashboard_includes_lane_keys_and_incident_source(self):
        self._event(ACTION_BLOCK, event_type="mcp_tool_call", tools_invoked=["t"])
        self._event("allow", event_type="rag_pipeline", pipeline_stage="query")
        self._incident("MCP incident", event_type="mcp_tool_call", tools_invoked=["t"])

        resp = self.client.get("/api/module2/dashboard/?period=24h")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        for key in ("lane_summary", "incidents_snapshot", "threat_trend", "key_risk_distribution"):
            self.assertIn(key, data)
        self.assertGreaterEqual(data["lane_summary"]["mcp"]["total"], 1)
        self.assertGreaterEqual(data["lane_summary"]["rag"]["total"], 1)
        self.assertNotIn("mcp_summary", data)
        self.assertNotIn("model_exposure", data)
        self.assertNotIn("rag_funnel", data)
        snapshot = data["incidents_snapshot"]
        self.assertTrue(snapshot)
        self.assertEqual(snapshot[0]["source"], "mcp")

    def test_dashboard_kpis_include_monitored_and_rerouted(self):
        self._event(ACTION_MONITOR, source="policy", threat_type="prompt_injection")
        self._event(
            "allow",
            source="routing",
            event_type="request",
            rerouted=True,
            original_model="gpt-4o",
            selected_model="gpt-4o-mini",
            extra={"source": "routing", "rerouted": True},
        )
        self._event(
            "allow",
            source="routing",
            event_type="request",
            rerouted=False,
            extra={"source": "routing", "rerouted": False},
        )

        resp = self.client.get("/api/module2/dashboard/?period=24h")
        self.assertEqual(resp.status_code, 200)
        kpis = resp.json()["kpis"]
        self.assertEqual(kpis["monitored"], 1)
        self.assertEqual(kpis["rerouted"], 1)

    def test_dashboard_rerouted_kpi_counts_hoisted_and_nested_flags(self):
        self._event(
            "allow",
            event_type="model_routed",
            source="routing",
            rerouted=True,
            original_model="claude-3-opus",
            selected_model="claude-3-haiku",
        )
        self._event(
            "allow",
            source="routing",
            extra={"rerouted": True, "original_model": "gpt-4o", "selected_model": "gpt-4o-mini"},
        )

        from policy.models import EnforcementEvent

        events = EnforcementEvent.objects.filter(organization=self.org)
        self.assertEqual(count_monitored_events(events), 0)
        self.assertEqual(count_rerouted_events(events), 2)

        resp = self.client.get("/api/module2/dashboard/?period=24h")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["kpis"]["rerouted"], 2)

    def test_threat_telemetry_includes_stage_hit_distribution(self):
        self._event(ACTION_BLOCK, source="threat_intel", pipeline_stage="query")
        resp = self.client.get("/api/module2/threat-intel/telemetry/?period=24h")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn("stage_hit_distribution", data)
        stages = {r["stage"]: r["count"] for r in data["stage_hit_distribution"]}
        self.assertEqual(stages.get("query"), 1)
        self.assertIn("ioc_library", data)
        self.assertIn("total", data["ioc_library"])

    def test_incident_source_filters(self):
        self._incident("MCP case", event_type="mcp_tool_call", tools_invoked=["x"])
        self._incident("MCP legacy case", tools_invoked=["legacy_tool"], server_slug="stub")
        self._incident("RAG case", event_type="rag_pipeline", pipeline_stage="retriever")
        self._incident("Vector case", collection="kb_docs")
        self._incident("Chat case", model="gpt-4o")

        for source, expected_title in (
            ("mcp", "MCP case"),
            ("rag", "RAG case"),
            ("vector", "Vector case"),
            ("chat", "Chat case"),
        ):
            resp = self.client.get(f"/api/module2/incidents/?source={source}")
            self.assertEqual(resp.status_code, 200, source)
            titles = [r["title"] for r in resp.json()["results"]]
            if source == "mcp":
                self.assertEqual(titles, ["MCP legacy case", "MCP case"], f"source={source} -> {titles}")
            else:
                self.assertEqual(titles, [expected_title], f"source={source} -> {titles}")

    def test_new_endpoints_require_auth(self):
        anon = APIClient()
        for path in ("/api/module2/rag/health/", "/api/module2/mcp/risk/"):
            resp = anon.get(path)
            self.assertIn(resp.status_code, (401, 403), path)
