import test from "node:test";
import assert from "node:assert/strict";
import { summarizeRoutingDecision, routingHasTechnicalDetails } from "./routingExplain.js";

const SCAN_296170_FIXTURE = {
  requested_model: "openrouter/free",
  selected_model: "cohere/north-mini-code:free",
  routed_model: "cohere/north-mini-code:free",
  decision_source: "policy_adjudicator",
  routing_reason:
    "ZeroShield Policy Adjudicator selected 'cohere/north-mini-code:free' (score=0.2300) from 11 candidates. "
    + "Risk=0.85, latency_budget_ms=30000, estimated_tokens=545, token_budget_tpm=100000. "
    + "Weights: risk=20%, cost=10%, latency=10%, priority=60%.",
  policy_summary: "Model meets sensitivity=public, compliance=[]. Risk=0.00, latency_sla=30000ms.",
  decision_factors: [
    "model_score=0.2300",
    "risk_component=0.1500",
    "cost_component=1.0000",
    "latency_component=1.0000",
    "priority_component=0.0000",
    "candidates_evaluated=11",
    "data_sensitivity=public",
  ],
  weights: { risk: 0.2, cost: 0.1, latency: 0.1, priority: 0.6 },
  routing_score: 0.23,
  candidate_count: 11,
  rerouted: true,
};

test("summarizeRoutingDecision adjudicator reroute uses human language (scan 296170)", () => {
  const { summary, technical } = summarizeRoutingDecision(SCAN_296170_FIXTURE);
  assert.match(summary, /You asked for openrouter\/free/i);
  assert.match(summary, /cohere\/north-mini-code:free/i);
  assert.match(summary, /11 eligible models/i);
  assert.match(summary, /cost/i);
  assert.match(summary, /response time/i);
  assert.doesNotMatch(summary, /score=0\.2300/);
  assert.doesNotMatch(summary, /risk_component/);
  assert.ok(routingHasTechnicalDetails(technical));
  assert.match(technical.routing_reason, /ZeroShield Policy Adjudicator/);
});

test("summarizeRoutingDecision kill_switch reroute", () => {
  const { summary } = summarizeRoutingDecision({
    requested_model: "gpt-5.2",
    selected_model: "Haiku",
    decision_source: "kill_switch",
    routing_reason: "Kill-switch reroute: gpt-5.2 → Haiku.",
  });
  assert.match(summary, /Kill switch/i);
  assert.match(summary, /gpt-5\.2/);
  assert.match(summary, /Haiku/);
  assert.match(summary, /disabled or isolated/i);
});

test("summarizeRoutingDecision routing_disabled", () => {
  const { summary } = summarizeRoutingDecision({
    requested_model: "nemotron",
    selected_model: "nemotron",
    decision_source: "routing_disabled",
  });
  assert.match(summary, /did not run/i);
  assert.doesNotMatch(summary, /Org routing is off/i);
});

test("org routing on + routing_disabled is a request pin, not org-off", () => {
  const { summary } = summarizeRoutingDecision({
    requested_model: "Haiku",
    selected_model: "Haiku",
    decision_source: "routing_disabled",
    org_routing_enabled: true,
  });
  assert.match(summary, /this request pinned/i);
  assert.match(summary, /enable_routing: false/i);
  assert.doesNotMatch(summary, /Org routing is off/i);
});

test("org routing off + routing_disabled keeps org-off wording", () => {
  const { summary } = summarizeRoutingDecision({
    requested_model: "nemotron",
    selected_model: "nemotron",
    decision_source: "routing_disabled",
    org_routing_enabled: false,
  });
  assert.match(summary, /Org routing is off/i);
});

test("kill_switch with remaining candidates mentions routing policy", () => {
  const { summary } = summarizeRoutingDecision({
    requested_model: "mistral-nemo-cheap",
    selected_model: "Haiku",
    decision_source: "kill_switch",
    candidate_count: 2,
    org_routing_enabled: true,
  });
  assert.match(summary, /Kill switch/i);
  assert.match(summary, /Routing policy evaluated 2 remaining eligible models/i);
  assert.doesNotMatch(summary, /Org routing is off/i);
});

test("summarizeRoutingDecision inactive model remap hint", () => {
  const { summary } = summarizeRoutingDecision({
    requested_model: "nvidia/nemotron-3-super-120b-a12b:free",
    selected_model: "cohere/north-mini-code:free",
    decision_source: "policy_adjudicator",
    routing_reason:
      "Requested model 'nvidia/nemotron-3-super-120b-a12b:free' is not active in router model groups; remapping to 'cohere/north-mini-code:free'",
    candidate_count: 5,
    rerouted: true,
  });
  assert.match(summary, /not in the active router pool/i);
});

test("summarizeRoutingDecision deterministic_weighted", () => {
  const { summary } = summarizeRoutingDecision({
    selected_model: "Haiku",
    decision_source: "deterministic_weighted",
    candidate_count: 3,
  });
  assert.match(summary, /routing policy selected Haiku/i);
  assert.match(summary, /3 eligible models/i);
});

test("legacy weighted_fallback rows still explain themselves", () => {
  // The adjudicator is gone, so nothing emits weighted_fallback any more — but
  // historical audit rows carry it and must not fall through to the bare catch-all
  // (which drops the computed "because" clause entirely).
  const { summary } = summarizeRoutingDecision({
    selected_model: "Haiku",
    decision_source: "weighted_fallback",
    candidate_count: 3,
  });
  assert.match(summary, /Haiku/);
  assert.match(summary, /3 eligible models/i);
});

test("cost-dominant org_default public does not claim sensitivity as the why", () => {
  const { summary } = summarizeRoutingDecision({
    requested_model: "auto",
    selected_model: "gemini-flash-cheap",
    decision_source: "deterministic_weighted",
    candidate_count: 12,
    policy_summary: "Deterministic weighted selection (dominant dimension: cost)",
    decision_factors: [
      "risk_weight=0.15",
      "cost_weight=0.50",
      "latency_weight=0.20",
      "priority_weight=0.15",
      "request_risk=0.00",
      "data_sensitivity=public",
    ],
    weights: { risk: 0.15, cost: 0.5, latency: 0.2, priority: 0.15 },
    data_sensitivity_source: "org_default",
    candidate_scores: [
      {
        model_name: "gemini-flash-cheap",
        score: 0.840801,
        risk_component: 0.6667,
        cost_component: 0.971,
        latency_component: 0.8547,
        priority_component: 0.5625,
      },
    ],
  });
  assert.match(summary, /gemini-flash-cheap/);
  assert.match(summary, /cost/i);
  assert.doesNotMatch(summary, /public data-sensitivity/i);
});

test("live Attack Simulator stage without candidate_scores cites cost not public default", () => {
  const { summary } = summarizeRoutingDecision({
    requested_model: "Haiku",
    selected_model: "gemini-flash-cheap",
    rerouted: true,
    decision_source: "deterministic_weighted",
    candidate_count: 12,
    policy_summary: "Deterministic weighted selection (dominant dimension: cost)",
    decision_factors: [
      "risk_weight=0.15",
      "cost_weight=0.50",
      "latency_weight=0.20",
      "priority_weight=0.15",
      "request_risk=0.00",
      "data_sensitivity=public",
    ],
    weights: { risk: 0.15, cost: 0.5, latency: 0.2, priority: 0.15 },
  });
  assert.match(summary, /You asked for Haiku/i);
  assert.match(summary, /gemini-flash-cheap/);
  assert.match(summary, /cost/i);
  assert.doesNotMatch(summary, /public data-sensitivity/i);
});

test("Haiku preference versus gemini is reroute copy", () => {
  const { summary } = summarizeRoutingDecision({
    requested_model: "Haiku",
    selected_model: "gemini-flash-cheap",
    rerouted: true,
    decision_source: "deterministic_weighted",
    candidate_count: 12,
    policy_summary: "Deterministic weighted selection (dominant dimension: cost)",
    weights: { risk: 0.15, cost: 0.5, latency: 0.2, priority: 0.15 },
    candidate_scores: [
      {
        model_name: "gemini-flash-cheap",
        cost_component: 0.971,
        risk_component: 0.6667,
        latency_component: 0.8547,
        priority_component: 0.5625,
      },
    ],
  });
  assert.match(summary, /You asked for Haiku/i);
  assert.match(summary, /gemini-flash-cheap/);
  assert.match(summary, /12 eligible models/i);
});

test("summarizeRoutingDecision same model no reroute wording", () => {
  const { summary } = summarizeRoutingDecision({
    requested_model: "Haiku",
    selected_model: "Haiku",
    routed_model: "Haiku",
    decision_source: "policy_adjudicator",
    candidate_count: 4,
  });
  assert.match(summary, /best fit among 4 eligible models/i);
  assert.doesNotMatch(summary, /instead/i);
});
