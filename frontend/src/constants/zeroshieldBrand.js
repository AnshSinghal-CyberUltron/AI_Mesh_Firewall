/**
 * Client-facing names for ZeroShield security models and scan tiers.
 * Do not surface upstream provider names (e.g. Bedrock) in UI copy.
 */

/** Default routed / simulator model id exposed to clients. Never surface the
 *  upstream size/provider (no "guard", "claude-haiku", or Bedrock). */
export const ZEROSHIELD_GUARD_MODEL = "zeroshield-model";

/** Human-readable product name for the platform ML model */
export const ZEROSHIELD_GUARD_MODEL_LABEL = "ZeroShield Model";

/** Tier-1 deterministic pattern engine */
export const ZEROSHIELD_TIER1_LABEL = "ZeroShield Pattern Engine";

/** Tier-2 semantic / ML model (ZeroShield-hosted platform model) */
export const ZEROSHIELD_TIER2_LABEL = "ZeroShield Model";

/** Routing adjudicator display name */
export const ZEROSHIELD_ADJUDICATOR_LABEL = "ZeroShield Policy Adjudicator";

const ROUTING_REASON_REPLACEMENTS = [
  ["bedrock adjudicator", ZEROSHIELD_ADJUDICATOR_LABEL],
  // Never surface the upstream platform model id/size/provider in operator copy.
  // The platform guard/adjudicator runs on Bedrock Claude Haiku; match it by SHAPE
  // (regex) so the exact deployable id is never embedded verbatim in the shipped
  // bundle (the gateway already scrubs server-side as the authoritative layer).
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

/** Sanitize routing reason text for operator-facing UI (never show Bedrock). */
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

/** Human-readable label for routing decision_source codes. */
export function formatDecisionSource(source) {
  const key = String(source || "").trim().toLowerCase();
  if (!key) return "";
  return DECISION_SOURCE_LABELS[key] || key.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
}

/** Routing sources that pick a model without operator intervention reroute. */
const ROUTINE_ROUTING_SOURCES = new Set([
  "policy_adjudicator",
  "weighted",
  "weighted_fastpath",
  "weighted_fallback",
  "routing_disabled",
  "no_routing_models",
  "simulator_bedrock_boto3_global",
]);

/** True when Model Routing should show REROUTE (forced intervention), not routine selection. */
export function isRoutingReroute(requested, selected, routing = {}) {
  const source = String(routing.decision_source || routing.trigger_source || "").toLowerCase();
  // Kill-switch / isolation reroutes are rendered on the kill_switch stage.
  if (["kill_switch", "model_state", "isolation"].includes(source)) {
    return false;
  }
  const req = String(requested || "").trim();
  const sel = String(selected || "").trim();
  if (!req || !sel || req.toLowerCase() === "auto" || req === sel) {
    return false;
  }
  if (!source || ROUTINE_ROUTING_SOURCES.has(source)) {
    return false;
  }
  return Boolean(routing.rerouted);
}

/** Log viewer service filter label (maps to gateway log service name internally).
 *  Must use the public product name — never the "Guard Model" branding. */
export const ZEROSHIELD_ML_LOG_SERVICE = ZEROSHIELD_GUARD_MODEL_LABEL;

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
  if (t.includes("bedrock")) return ZEROSHIELD_TIER2_LABEL;
  return tier;
}

const PLATFORM_GUARD_MODEL_NAMES = new Set([
  ZEROSHIELD_GUARD_MODEL,
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

/**
 * True when a model is CONNECTED with a usable key for inference / simulators /
 * governance — either a stored encrypted key (`api_key_set`) OR a gateway
 * env-var key reference (`api_key_env_var`, BYOK-via-env). The backend
 * `api_key_set` flag is encrypted-key-only by design (it drives the credential
 * display), so callers deciding "is this model connected?" must also honor the
 * env-var reference, otherwise BYOK-via-env models are wrongly shown as
 * "API key missing" / "No inference model connected".
 */
export function modelHasUsableKey(m) {
  if (!m) return false;
  const provider = String(m.provider || "").toLowerCase();
  // Mirror gateway inference eligibility: Ollama and Bedrock can run without a
  // stored encrypted key (local daemon / gateway AWS credential chain).
  if (provider === "ollama" || provider === "aws_bedrock" || provider === "bedrock") {
    return true;
  }
  return Boolean(m.api_key_set || m.api_key_env_var);
}

/** Substrings of any reserved platform/guard/BYOK upstream id or codename. A
 *  match anywhere in the value means it must never reach a user-facing surface
 *  and is collapsed to the single product label "ZeroShield Model". */
const RESERVED_MODEL_SUBSTRINGS = [
  "bedrock",
  "claude-haiku",
  "haiku",
  "anthropic",
  "zeroshield-guard",
  ZEROSHIELD_GUARD_MODEL, // "zeroshield-model" — already the public id, normalize to the label
];

/** Label fragments that are guard-model branding leaks ("...Guard Model",
 *  "ZeroShield Guard") and must render as the product label instead. */
const RESERVED_LABEL_FRAGMENTS = [
  "guard model",
  "zeroshield guard",
];

/** True when a model id/name/label is a reserved platform/guard/BYOK value
 *  that must never surface verbatim (raw upstream id OR guard-model branding). */
export function isReservedModelLabel(value) {
  if (!value) return false;
  const v = String(value).toLowerCase();
  if (RESERVED_MODEL_SUBSTRINGS.some((s) => v.includes(s))) return true;
  if (RESERVED_LABEL_FRAGMENTS.some((s) => v.includes(s))) return true;
  return false;
}

/**
 * Sanitize any model id, name, or label for display. Maps ANY reserved
 * platform/guard/BYOK upstream id (bedrock, claude-haiku, haiku,
 * anthropic, zeroshield-guard, zeroshield-model) OR any guard-model branding
 * label ("Guard Model", "ZeroShield Guard Model", "ZeroShield Guard") to the
 * single client-facing product name "ZeroShield Model". Non-reserved org/BYOK
 * model names pass through unchanged.
 */
export function sanitizeModelLabel(value) {
  if (!value) return value;
  return isReservedModelLabel(value) ? ZEROSHIELD_GUARD_MODEL_LABEL : value;
}

/** Map model id from API responses for display. Alias of sanitizeModelLabel
 *  kept for existing call sites (liveGateway). */
export function formatModelDisplayName(modelId) {
  return sanitizeModelLabel(modelId);
}
