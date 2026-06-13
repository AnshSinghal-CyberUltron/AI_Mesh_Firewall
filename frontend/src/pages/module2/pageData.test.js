import test from "node:test";
import assert from "node:assert/strict";
import {
  buildExposureKpis,
  buildTelemetryKpis,
  exposureBandClass,
  formatAttackVectors,
  formatExposureChartData,
  formatTelemetryTimeline,
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
  assert.ok(sourceBadgeClass("ueba").includes("cyan"));
  assert.ok(sourceBadgeClass("threat_intel").includes("violet"));
});

test("sourceBadgeClass covers all four enforcement lanes", () => {
  assert.ok(sourceBadgeClass("rag").includes("purple"));
  assert.ok(sourceBadgeClass("mcp").includes("amber"));
  assert.ok(sourceBadgeClass("vector").includes("emerald"));
  assert.ok(sourceBadgeClass("chat").includes("sky"));
  assert.ok(sourceBadgeClass("unknown-lane").includes("slate"));
});

test("KPI builders attach analyst helpText to every card", () => {
  for (const kpi of buildExposureKpis(EXPOSURE_FIXTURE.summary)) {
    assert.ok(kpi.helpText && kpi.helpText.length > 10, `missing helpText: ${kpi.key}`);
  }
  for (const kpi of buildTelemetryKpis(TELEMETRY_FIXTURE.summary)) {
    assert.ok(kpi.helpText && kpi.helpText.length > 10, `missing helpText: ${kpi.key}`);
  }
});
