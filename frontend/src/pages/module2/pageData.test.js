import test from "node:test";
import assert from "node:assert/strict";
import {
  buildUebaKpiItems,
  buildContainmentKpiItems,
  buildExposureKpis,
  buildIncidentKpiItems,
  buildRagKpis,
  buildTelemetryKpis,
  formatIncidentAge,
  formatRagDocumentFunnel,
  formatRagStageChartData,
  exposureBandClass,
  formatAttackVectors,
  formatExposureChartData,
  formatTelemetryTimeline,
  buildTickerAnalystFields,
  formatTickerAnalystSummary,
  mergeTickerFeed,
  patchIncidentSummaryForMutation,
  resolveEventLane,
  sourceBadgeClass,
} from "./pageData.js";

const EXPOSURE_FIXTURE = {
  summary: {
    active_models: 3,
    high_exposure_models: 1,
    total_requests: 500,
    avg_block_rate_pct: 12.5,
    avg_exposure_score: 0.42,
  },
  exposure_by_model: [
    { model: "gpt-4o", exposure_score: 0.8, exposure_band: "high", block_rate_pct: 25, requests: 200 },
    { model: "claude-3", exposure_score: 0.2, exposure_band: "low", block_rate_pct: 5, requests: 100 },
  ],
};

const TELEMETRY_FIXTURE = {
  summary: {
    total_events: 120,
    injection_attempts: 40,
    pii_leaks: 15,
    behavior_scoring_events: 50,
    threat_intel_matches: 5,
  },
  timeline: [
    {
      timestamp: "2026-06-08T10:00:00+00:00",
      injection_attempts: 4,
      pii_leaks: 1,
      behavior_scoring: 3,
      threat_intel_matches: 0,
      total: 8,
    },
  ],
  top_attack_vectors: [
    { vector: "LLM01", count: 22 },
    { vector: "LLM06", count: 11 },
  ],
};

test("formatExposureChartData maps models to chart rows with fill colors", () => {
  const rows = formatExposureChartData(EXPOSURE_FIXTURE.exposure_by_model);
  assert.equal(rows.length, 2);
  assert.equal(rows[0].name, "gpt-4o");
  assert.equal(rows[0].score, 0.8);
  assert.equal(rows[0].fill, "#ef4444");
});

test("buildExposureKpis renders summary cards", () => {
  const kpis = buildExposureKpis(EXPOSURE_FIXTURE.summary);
  assert.equal(kpis[0].value, 3);
  assert.equal(kpis[3].value, "12.5%");
});

test("formatTelemetryTimeline adds readable labels", () => {
  const timeline = formatTelemetryTimeline(TELEMETRY_FIXTURE.timeline);
  assert.ok(timeline[0].label.includes("06-08"));
});

test("formatAttackVectors maps vector counts for bar chart", () => {
  const vectors = formatAttackVectors(TELEMETRY_FIXTURE.top_attack_vectors);
  assert.deepEqual(vectors[0], { name: "LLM01", count: 22 });
});

test("buildTelemetryKpis renders telemetry summary cards", () => {
  const kpis = buildTelemetryKpis(TELEMETRY_FIXTURE.summary);
  assert.equal(kpis[1].value, 40);
  assert.equal(kpis[4].value, 5);
});

test("badge helpers return expected class fragments", () => {
  assert.ok(exposureBandClass("high").includes("red"));
  assert.ok(sourceBadgeClass("chat").includes("sky"));
});

test("sourceBadgeClass covers all four enforcement lanes", () => {
  assert.ok(sourceBadgeClass("rag").includes("purple"));
  assert.ok(sourceBadgeClass("mcp").includes("amber"));
  assert.ok(sourceBadgeClass("vector").includes("emerald"));
  assert.ok(sourceBadgeClass("chat").includes("sky"));
  assert.ok(sourceBadgeClass("unknown-lane").includes("slate"));
});

test("buildRagKpis aggregates stage and collection metrics", () => {
  const kpis = buildRagKpis(
    {
      stages: {
        query: { total: 10, blocked: 5 },
        retriever: { total: 8, blocked: 4 },
        ranker: { total: 0, blocked: 0 },
        generator: { total: 0, blocked: 0 },
      },
    },
    { collections: [{ block_rate_pct: 60 }, { block_rate_pct: 10 }] },
  );
  assert.equal(kpis[0].value, 10);
  assert.equal(kpis[1].value, 9);
  assert.equal(kpis[3].value, 1);
  assert.equal(kpis[4].value, "50%");
});

test("formatRagStageChartData computes allowed and block rate", () => {
  const rows = formatRagStageChartData({
    query: { total: 4, blocked: 4, flagged: 0, allowed: 0 },
    retriever: { total: 2, blocked: 1, flagged: 0, allowed: 1 },
  });
  assert.equal(rows[0].block_rate, 100);
  assert.equal(rows[1].allowed, 1);
});

test("formatRagDocumentFunnel shows survival percentages", () => {
  const steps = formatRagDocumentFunnel({ retrieved: 10, post_ranker: 6, post_generator: 3 });
  assert.equal(steps[1].pct, 60);
  assert.equal(steps[2].value, 3);
});

test("buildUebaKpiItems labels fleet vs period-scoped metrics", () => {
  const items = buildUebaKpiItems({
    summary: {
      total_keys: 5,
      active_keys: 4,
      total_events: 12,
      blocked_events: 3,
      keys_with_activity: 2,
      high_risk_keys: 1,
      disabled_keys: 0,
      active_kill_switches: 0,
    },
    periodLabel: "24 hours",
  });
  const byKey = Object.fromEntries(items.map((item) => [item.key, item]));
  assert.equal(byKey["total-keys"].sub, "Registered fleet");
  assert.equal(byKey["total-events"].sub, "Last 24 hours");
  assert.equal(byKey["high-risk-keys"].sub, "Active in 24 hours");
  assert.equal(byKey["total-events"].value, 12);
});

test("buildContainmentKpiItems marks clickable cards only when requested", () => {
  const readOnly = buildContainmentKpiItems({ disabledKeys: 2, activeKillSwitches: 1, clickable: false });
  assert.equal(readOnly[0].clickable, false);
  assert.equal(readOnly[1].value, 1);

  let panel = null;
  const interactive = buildContainmentKpiItems({
    disabledKeys: 1,
    activeKillSwitches: 3,
    clickable: true,
    activePanel: panel,
    onDisabledClick: () => { panel = "disabled"; },
    onKillSwitchClick: () => { panel = "kill-switch"; },
  });
  assert.equal(interactive[0].clickable, true);
  assert.equal(typeof interactive[0].onClick, "function");
  interactive[0].onClick();
  assert.equal(panel, "disabled");
});

test("mergeTickerFeed dedupes live events and merges incidents", () => {
  const merged = mergeTickerFeed(
    [
      { id: "1", action: "block", metadata: { event_type: "rag_pipeline" } },
      { id: "1", action: "block", metadata: { event_type: "rag_pipeline" } },
    ],
    [{ id: 9, title: "Open case", source: "mcp", severity: "high" }],
    5,
  );
  assert.equal(merged.length, 2);
  assert.equal(merged[0]._fromFeed, true);
  assert.equal(merged[1].title, "Open case");
});

test("formatTickerAnalystSummary explains enforcement events in plain language", () => {
  const summary = formatTickerAnalystSummary({
    action: "block",
    metadata: {
      event_type: "rag_pipeline",
      threat_type: "prompt_injection",
      model: "gpt-4o",
      key_prefix: "zs-abcd",
      detail: "Policy violation in query stage",
    },
  });
  assert.match(summary, /rag/i);
  assert.match(summary, /blocked/i);
  assert.match(summary, /prompt injection/i);
  assert.match(summary, /gpt-4o/i);
});

test("buildTickerAnalystFields includes routing and request metadata", () => {
  const fields = buildTickerAnalystFields({
    id: "42",
    action: "allow",
    timestamp: "2026-07-07T10:00:00+00:00",
    metadata: {
      event_type: "model_routed",
      original_model: "gpt-4o",
      routed_model: "gpt-4o-mini",
      rerouted: true,
      request_id: "zs-req-1",
    },
  });
  const labels = fields.map((f) => f.label);
  assert.ok(labels.includes("Model routing"));
  assert.ok(labels.includes("Request ID"));
  assert.match(fields.find((f) => f.label === "Model routing").value, /gpt-4o-mini/);
});

test("resolveEventLane prefers pipeline metadata over policy source", () => {
  assert.equal(resolveEventLane({ source: "policy", metadata: { event_type: "mcp_tool_call" } }), "mcp");
  assert.equal(resolveEventLane({ source: "rag", title: "Case" }), "rag");
});

test("KPI builders attach analyst helpText to every card", () => {
  for (const kpi of buildExposureKpis(EXPOSURE_FIXTURE.summary)) {
    assert.ok(kpi.helpText && kpi.helpText.length > 10, `missing helpText: ${kpi.key}`);
  }
  for (const kpi of buildTelemetryKpis(TELEMETRY_FIXTURE.summary)) {
    assert.ok(kpi.helpText && kpi.helpText.length > 10, `missing helpText: ${kpi.key}`);
  }
  for (const kpi of buildContainmentKpiItems({ disabledKeys: 0, activeKillSwitches: 0 })) {
    assert.ok(kpi.helpText && kpi.helpText.length > 10, `missing helpText: ${kpi.key}`);
  }
});

test("formatIncidentAge renders compact durations", () => {
  const hourAgo = new Date(Date.now() - 2 * 60 * 60 * 1000).toISOString();
  assert.equal(formatIncidentAge(hourAgo), "2h");
  assert.equal(formatIncidentAge(""), "—");
});

test("patchIncidentSummaryForMutation updates open and resolved counts on resolve", () => {
  const summary = {
    total: 10,
    active: 4,
    open: 3,
    investigating: 1,
    escalated: 0,
    resolved: 6,
    critical_high: 2,
    by_source: {},
  };
  const next = patchIncidentSummaryForMutation(summary, {
    action: "resolve",
    previousStatus: "open",
    severity: "high",
  });
  assert.equal(next.open, 2);
  assert.equal(next.resolved, 7);
  assert.equal(next.active, 3);
  assert.equal(next.critical_high, 1);
});

test("patchIncidentSummaryForMutation moves escalated counts on escalate", () => {
  const summary = {
    total: 5,
    active: 2,
    open: 2,
    investigating: 0,
    escalated: 0,
    resolved: 3,
    critical_high: 0,
    by_source: {},
  };
  const next = patchIncidentSummaryForMutation(summary, {
    action: "escalate",
    previousStatus: "open",
    severity: "medium",
  });
  assert.equal(next.open, 1);
  assert.equal(next.escalated, 1);
  assert.equal(next.active, 2);
});

test("buildIncidentKpiItems wires critical/high severity toggle", () => {
  const calls = [];
  const items = buildIncidentKpiItems(
    { active: 3, open: 2, escalated: 1, resolved: 0, critical_high: 2 },
    {
      severityFilter: "critical_high",
      onSeverityFilter: (v) => calls.push(v),
    },
  );
  const criticalHigh = items.find((k) => k.key === "critical-high");
  assert.equal(criticalHigh.active, true);
  criticalHigh.onClick();
  assert.deepEqual(calls, [""]);
});

test("buildIncidentKpiItems sets composite severity filter when inactive", () => {
  const calls = [];
  const items = buildIncidentKpiItems(
    { active: 1, open: 1, escalated: 0, resolved: 0, critical_high: 1 },
    {
      severityFilter: "",
      onSeverityFilter: (v) => calls.push(v),
    },
  );
  const criticalHigh = items.find((k) => k.key === "critical-high");
  assert.equal(criticalHigh.active, false);
  criticalHigh.onClick();
  assert.deepEqual(calls, ["critical_high"]);
});
