/**
 * Human-language routing decision summaries for operator UI.
 * Raw trace/audit fields stay unchanged; this only formats display copy.
 */

import {
  formatRoutingReason,
  isRoutingReroute,
} from "../constants/zeroshieldBrand.js";

const COMPONENT_LABELS = {
  risk_component: "risk profile",
  cost_component: "cost",
  latency_component: "response time",
  priority_component: "model priority",
};

const SCORE_COMPONENT_KEYS = [
  "cost_component",
  "latency_component",
  "risk_component",
  "priority_component",
];

function parseDecisionFactors(factors) {
  const parsed = {};
  for (const item of factors || []) {
    const text = String(item || "").trim();
    const eq = text.indexOf("=");
    if (eq <= 0) continue;
    const key = text.slice(0, eq).trim();
    const val = Number.parseFloat(text.slice(eq + 1));
    if (!Number.isNaN(val)) parsed[key] = val;
  }
  return parsed;
}

function weightKeyForComponent(componentKey) {
  return String(componentKey || "").replace(/_component$/, "");
}

/** Top 1–2 scoring dimensions that actually differentiated (score > 0). */
function topDifferentiators(parsed, weights) {
  return SCORE_COMPONENT_KEYS
    .map((key) => {
      const score = parsed[key] ?? 0;
      const wKey = weightKeyForComponent(key);
      const weight = Number(weights?.[wKey]) || 0;
      return {
        key,
        label: COMPONENT_LABELS[key] || key,
        score,
        contribution: score * weight,
      };
    })
    .filter((row) => row.score > 0.01)
    .sort((a, b) => {
      if (b.contribution !== a.contribution) return b.contribution - a.contribution;
      return b.score - a.score;
    })
    .slice(0, 2)
    .map((row) => row.label);
}

function extractSensitivity(policySummary, factors) {
  const fromFactor = (factors || []).find((f) => String(f).startsWith("data_sensitivity="));
  if (fromFactor) return String(fromFactor).split("=")[1]?.trim() || "";
  const match = String(policySummary || "").match(/sensitivity=(\w+)/i);
  return match ? match[1] : "";
}

function isInactiveModelRemap(routingReason) {
  return /not active in router model groups/i.test(String(routingReason || ""));
}

function normalizeRequested(routing) {
  return String(routing.requested_model || routing.original_model || "auto").trim();
}

function normalizeSelected(routing) {
  return String(routing.routed_model || routing.selected_model || "").trim();
}

function factorPresent(factors, needle) {
  return (factors || []).some((f) => String(f).includes(needle));
}

function buildBecauseClause({ tops, sensitivity, inactiveRemap, sensFallback, scoreTie }) {
  const parts = [];
  if (sensFallback) {
    parts.push("no model met the requested sensitivity so the gateway soft-fell back to the best available model");
  } else if (sensitivity) {
    parts.push(`it meets ${sensitivity} data-sensitivity requirements`);
  }
  if (tops.length > 0) {
    parts.push(`it ranked best on ${tops.join(" and ")}`);
  }
  if (scoreTie) {
    parts.push("top candidates tied on score (list-order / adjudicator broke the tie)");
  }
  if (inactiveRemap) {
    parts.push("your requested model is not in the active router pool");
  }
  if (parts.length === 0) return "";
  return ` because ${parts.join(", and ")}`;
}

/**
 * @param {object} routing — resolveRoutingDecision() / model_routing stage shape
 * @returns {{ summary: string, technical: object }}
 */
export function summarizeRoutingDecision(routing = {}) {
  const requested = normalizeRequested(routing);
  const selected = normalizeSelected(routing);
  const source = String(routing.decision_source || "").trim().toLowerCase();
  const count = Number(routing.candidate_count) || 0;
  const rerouted = isRoutingReroute(requested, selected, routing);
  const rawReason = routing.routing_reason || "";
  const factors = Array.isArray(routing.decision_factors) ? routing.decision_factors : [];
  const weights = routing.weights && typeof routing.weights === "object" ? routing.weights : {};
  const parsed = parseDecisionFactors(factors);
  const tops = topDifferentiators(parsed, weights);
  const sensitivity = extractSensitivity(routing.policy_summary, factors);
  const inactiveRemap = isInactiveModelRemap(rawReason)
    || Boolean(routing.remapped_from)
    || factorPresent(factors, "inactive_model_remapped");
  const sensFallback = Boolean(routing.sensitivity_fallback)
    || factorPresent(factors, "sensitivity_unsatisfiable_fallback");
  const scoreTie = Boolean(routing.score_tie) || factorPresent(factors, "score_tie");
  const because = buildBecauseClause({
    tops, sensitivity, inactiveRemap, sensFallback, scoreTie,
  });

  const technical = {
    routing_reason: formatRoutingReason(rawReason, { decisionSource: source }),
    policy_summary: routing.policy_summary || "",
    decision_factors: factors,
    weights,
    routing_score: routing.routing_score ?? null,
    candidate_count: count || null,
    guard_reason: routing.guard_reason || "",
    decision_source: source,
    fallback_chain: Array.isArray(routing.fallback_chain) ? routing.fallback_chain : [],
    sensitivity_fallback: sensFallback,
    score_tie: scoreTie,
    remapped_from: routing.remapped_from || "",
    candidate_scores: Array.isArray(routing.candidate_scores) ? routing.candidate_scores : [],
  };

  let summary = "";

  if (source === "kill_switch" || source === "model_state") {
    const label = source === "kill_switch" ? "Kill switch" : "Model isolation";
    summary = `${label} redirected traffic from ${requested} to ${selected} because the requested model is disabled or isolated.`;
  } else if (source === "routing_disabled") {
    const used = selected || requested;
    const modelLabel = used === "auto" ? "the default model" : used;
    summary = `Org routing is off — the gateway used ${modelLabel} directly without running routing policy.`;
  } else if (
    source === "deterministic_weighted" ||
    // Legacy sources, retained so historical audit rows still explain themselves.
    // All of these were produced by the removed Bedrock adjudicator path.
    source === "policy_adjudicator" ||
    source === "weighted" ||
    source === "weighted_fastpath" ||
    source === "weighted_fallback" ||
    source === ""
  ) {
    if (rerouted) {
      summary = `You asked for ${requested}; routing policy selected ${selected} instead`;
      if (count > 0) summary += ` from ${count} eligible models`;
      summary += `${because}.`;
    } else {
      summary = `Routing policy selected ${selected || requested}`;
      if (count > 0) summary += ` as the best fit among ${count} eligible models`;
      summary += `${because}.`;
    }
  } else {
    const formatted = formatRoutingReason(rawReason, { decisionSource: source });
    summary = formatted.split("\n")[0]?.trim() || `Routed to ${selected || requested}.`;
  }

  return { summary, technical };
}

/** True when the technical blob has anything worth showing in a collapse panel. */
export function routingHasTechnicalDetails(technical) {
  if (!technical || typeof technical !== "object") return false;
  if (technical.routing_reason) return true;
  if (technical.policy_summary) return true;
  if (Array.isArray(technical.decision_factors) && technical.decision_factors.length > 0) return true;
  if (technical.weights && Object.keys(technical.weights).length > 0) return true;
  if (Number(technical.routing_score) > 0) return true;
  if (Number(technical.candidate_count) > 0) return true;
  if (technical.guard_reason) return true;
  if (Array.isArray(technical.fallback_chain) && technical.fallback_chain.length > 0) return true;
  // Without this, an event whose ONLY populated field is candidate_scores would hide
  // the entire technical panel (RoutingTechnicalDetails returns null on false).
  if (Array.isArray(technical.candidate_scores) && technical.candidate_scores.length > 0) return true;
  return false;
}
