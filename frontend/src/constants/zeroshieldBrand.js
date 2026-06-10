/**
 * Client-facing names for ZeroShield security models and scan tiers.
 * Do not surface upstream provider names (e.g. Bedrock) in UI copy.
 */

/** Default routed / simulator model id exposed to clients */
export const ZEROSHIELD_GUARD_MODEL = "zeroshield-guard-120b";

/** Human-readable product name for the ML guard model */
export const ZEROSHIELD_GUARD_MODEL_LABEL = "ZeroShield Guard Model";

/** Tier-1 deterministic pattern engine */
export const ZEROSHIELD_TIER1_LABEL = "ZeroShield Pattern Engine";

/** Tier-2 semantic / ML guard (ZeroShield-hosted) */
export const ZEROSHIELD_TIER2_LABEL = "ZeroShield Guard Model";

/** Routing adjudicator display name */
export const ZEROSHIELD_ADJUDICATOR_LABEL = "ZeroShield Policy Adjudicator";

const ROUTING_REASON_REPLACEMENTS = [
  ["Bedrock GPT OSS 120B adjudicator", ZEROSHIELD_ADJUDICATOR_LABEL],
  ["Bedrock GPT OSS 120B", ZEROSHIELD_ADJUDICATOR_LABEL],
  ["bedrock adjudicator", ZEROSHIELD_ADJUDICATOR_LABEL],
];

const DECISION_SOURCE_LABELS = {
  kill_switch: "Kill switch",
  model_state: "Model state isolation",
  policy_adjudicator: ZEROSHIELD_ADJUDICATOR_LABEL,
  routing_disabled: "Routing disabled",
  no_routing_models: "No routing models",
};

/** Sanitize routing reason text for operator-facing UI (never show Bedrock). */
export function formatRoutingReason(reason, { decisionSource } = {}) {
  let text = String(reason || "").trim();
  if (!text) return text;
  for (const [oldText, newText] of ROUTING_REASON_REPLACEMENTS) {
    text = text.split(oldText).join(newText);
  }
  if (decisionSource === "kill_switch" && !text.toLowerCase().includes("kill-switch")) {
    return text;
  }
  return text;
}

/** Human-readable label for routing decision_source codes. */
export function formatDecisionSource(source) {
  const key = String(source || "").trim().toLowerCase();
  if (!key) return "";
  return DECISION_SOURCE_LABELS[key] || key.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
}

/** True when requested and selected models differ (non-auto). */
export function isRoutingReroute(requested, selected, routing = {}) {
  if (routing.rerouted) return true;
  const req = String(requested || "").trim();
  const sel = String(selected || "").trim();
  return Boolean(req && sel && req.toLowerCase() !== "auto" && req !== sel);
}

/** Log viewer service filter label (maps to gateway log service name internally) */
export const ZEROSHIELD_ML_LOG_SERVICE = "Guard Model";

function isCleanThreatType(threatType) {
  const t = String(threatType || "").trim().toLowerCase();
  return !t || t === "none" || t === "clean";
}

function isTier2Scan(zs = {}) {
  const tier = String(zs.detection_tier || "").toLowerCase();
  return tier === "tier_2" || tier === "tier2" || tier === "input_scan";
}

/** Operator-facing summary for zeroshield / input_scan metadata. */
export function formatZeroshieldScanSummary(zs = {}) {
  const action = String(zs.action || "allow").toLowerCase();
  const tierLabel = formatDetectionTier(zs.detection_tier) || "—";
  const clean = zs.scan_outcome === "clean" || (action === "allow" && isCleanThreatType(zs.threat_type));
  const threatLabel = clean
    ? "No threat detected"
    : String(zs.threat_type || "—").replace(/_/g, " ");
  const risk = zs.risk_score ?? (clean && isTier2Scan(zs) ? 1 - (zs.confidence ?? 0) : null);
  const scoreLabel = clean && isTier2Scan(zs) ? "Risk score" : "Confidence";
  const scoreValue = clean && isTier2Scan(zs)
    ? (risk != null ? `${Math.round(Number(risk) * 100)}%` : "0%")
    : (zs.confidence != null ? `${(Number(zs.confidence) * 100).toFixed(0)}%` : "—");
  const detail = zs.guard_reason || zs.detail || zs.reason || "";
  return {
    tierLabel,
    threatLabel,
    scoreLabel,
    scoreValue,
    action: action.toUpperCase(),
    detail,
    clean,
  };
}

/** Map gateway detection_tier codes to client-facing labels (never show Bedrock). */
export function formatDetectionTier(tier) {
  if (tier == null || tier === "") return tier;
  const t = String(tier).toLowerCase();
  if (t === "tier_1" || t === "tier1") return ZEROSHIELD_TIER1_LABEL;
  if (t === "tier_2" || t === "tier2" || t === "input_scan") return ZEROSHIELD_TIER2_LABEL;
  if (t.includes("bedrock") || t.includes("gpt-oss")) return ZEROSHIELD_TIER2_LABEL;
  return tier;
}

const PLATFORM_GUARD_MODEL_NAMES = new Set([
  ZEROSHIELD_GUARD_MODEL,
  "bedrock-gpt-oss-120b",
  "bedrock-gpt-oss-120b-long-context",
]);

/** True for internal / ZeroShield guard entries (not shown in model governance UI). */
export function isPlatformManagedModel(model) {
  if (!model) return false;
  const name = String(model.model_name || "").trim().toLowerCase();
  const provider = String(model.provider || "").trim().toLowerCase();
  if (provider === "internal") return true;
  return PLATFORM_GUARD_MODEL_NAMES.has(name);
}

/** Org-owned models only — excludes platform guard model from governance tables. */
export function filterUserManagedModels(models) {
  return (models || []).filter((m) => !isPlatformManagedModel(m));
}

/** Map model id from API responses for display */
export function formatModelDisplayName(modelId) {
  if (!modelId) return modelId;
  const m = String(modelId).toLowerCase();
  if (m.includes("bedrock") || m.includes("gpt-oss") || m === ZEROSHIELD_GUARD_MODEL) {
    return ZEROSHIELD_GUARD_MODEL_LABEL;
  }
  return modelId;
}
