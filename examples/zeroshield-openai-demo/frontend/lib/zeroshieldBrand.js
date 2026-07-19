/**
 * Port of frontend/src/constants/zeroshieldBrand.js (demo vanilla copy).
 * Keep in sync with the main console — do not surface upstream provider names.
 */
export const ZEROSHIELD_GUARD_MODEL = "zeroshield-model";
export const ZEROSHIELD_GUARD_MODEL_LABEL = "ZeroShield Model";
export const ZEROSHIELD_TIER1_LABEL = "ZeroShield Pattern Engine";
export const ZEROSHIELD_TIER2_LABEL = "ZeroShield Model";
export const ZEROSHIELD_ADJUDICATOR_LABEL = "ZeroShield Policy Adjudicator";

const ROUTING_REASON_REPLACEMENTS = [
  ["bedrock adjudicator", ZEROSHIELD_ADJUDICATOR_LABEL],
  [/(?:bedrock\/)?global\.anthropic\.claude-haiku[\w.:-]*/gi, ZEROSHIELD_GUARD_MODEL_LABEL],
  ["claude-haiku-4-5", ZEROSHIELD_GUARD_MODEL_LABEL],
];

const DECISION_SOURCE_LABELS = {
  kill_switch: "Kill switch",
  model_state: "Model state isolation",
  policy_adjudicator: ZEROSHIELD_ADJUDICATOR_LABEL,
  routing_disabled: "Routing disabled",
  no_routing_models: "No routing models",
  policy_engine: "Policy engine",
  gateway_auth: "Gateway authentication",
  org_rate_limit: "Org rate limit",
  zeroshield_guard_model: ZEROSHIELD_GUARD_MODEL_LABEL,
  pattern_engine: ZEROSHIELD_TIER1_LABEL,
  output_guard: "ZeroShield Output Guard",
  llm_request: "LLM request",
  llm_provider: "LLM provider",
};

export function formatRoutingReason(reason, { decisionSource } = {}) {
  let text = String(reason || "").trim();
  if (!text) return text;
  for (const [pattern, newText] of ROUTING_REASON_REPLACEMENTS) {
    text = pattern instanceof RegExp
      ? text.replace(pattern, newText)
      : text.split(pattern).join(newText);
  }
  if (decisionSource === "kill_switch" && !text.toLowerCase().includes("kill-switch")) {
    return text;
  }
  return text;
}

export function formatDecisionSource(source) {
  const key = String(source || "").trim().toLowerCase();
  if (!key) return "";
  return DECISION_SOURCE_LABELS[key] || key.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
}

export function isRoutingReroute(requested, selected, routing = {}) {
  if (routing.rerouted) return true;
  const req = String(requested || "").trim();
  const sel = String(selected || "").trim();
  return Boolean(req && sel && req.toLowerCase() !== "auto" && req !== sel);
}

function isCleanThreatType(threatType) {
  const t = String(threatType || "").trim().toLowerCase();
  return !t || t === "none" || t === "clean";
}

function isTier2Scan(zs = {}) {
  const tier = String(zs.detection_tier || zs.tier || "").toLowerCase();
  return tier === "tier_2" || tier === "tier2" || tier === "input_scan";
}

export function formatDetectionTier(tier) {
  if (tier == null || tier === "") return tier;
  const t = String(tier).toLowerCase();
  if (t === "tier_1" || t === "tier1") return ZEROSHIELD_TIER1_LABEL;
  if (t === "tier_2" || t === "tier2" || t === "input_scan") return ZEROSHIELD_TIER2_LABEL;
  if (t.includes("bedrock")) return ZEROSHIELD_TIER2_LABEL;
  return tier;
}

export function formatZeroshieldScanSummary(zs = {}) {
  const action = String(zs.action || "allow").toLowerCase();
  const tierLabel = formatDetectionTier(zs.detection_tier || zs.tier) || "—";
  const clean = zs.scan_outcome === "clean" || (action === "allow" && isCleanThreatType(zs.threat_type));
  const threatLabel = clean
    ? "No threat detected"
    : String(zs.threat_type || "—").replace(/_/g, " ");
  const risk = zs.risk_score ?? (clean && isTier2Scan(zs) ? 1 - (zs.confidence ?? 0) : null);
  const scoreLabel = clean && isTier2Scan(zs) ? "Risk score" : "Confidence";
  const scoreValue = clean && isTier2Scan(zs)
    ? (risk != null ? `${Math.round(Number(risk) * 100)}%` : "0%")
    : (zs.confidence != null ? `${(Number(zs.confidence) * 100).toFixed(0)}%` : "—");
  return {
    tierLabel,
    threatLabel,
    scoreLabel,
    scoreValue,
    action: action.toUpperCase(),
    detail: zs.guard_reason || zs.detail || zs.reason || "",
    clean,
  };
}
