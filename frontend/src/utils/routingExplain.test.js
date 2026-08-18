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
  assert.match(summary, /routing is off/i);
  assert.match(summary, /without running routing policy/i);
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
