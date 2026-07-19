/**
 * Port of frontend/src/utils/inputScanExplain.js (demo vanilla copy).
 */
const INJECTION_THREATS = new Set([
  "prompt_injection",
  "jailbreak",
  "goal_hijacking",
  "tool_overreach",
  "indirect_injection",
  "injection",
]);

function isInjectionThreat(threatType) {
  const t = String(threatType || "").trim().toLowerCase();
  return INJECTION_THREATS.has(t) || t.endsWith("_injection");
}

function formatThreatLabel(threatType) {
  const t = String(threatType || "").trim();
  if (!t || t === "none" || t === "clean") return "";
  return t.replace(/_/g, " ");
}

export function inputScanHasTechnicalDetails(stage = {}) {
  return Boolean(
    stage.guard_reason
    || (Array.isArray(stage.matched_patterns) && stage.matched_patterns.length > 0)
    || (Array.isArray(stage.guard_findings) && stage.guard_findings.length > 0)
    || stage.detail
  );
}

export function summarizeInputScanStage(stage = {}) {
  const action = String(stage.action || "allow").toLowerCase();
  const threat = formatThreatLabel(stage.threat_type);
  const scanOutcome = String(stage.scan_outcome || "").toLowerCase();
  const analyzedOnly = scanOutcome === "analyzed" || stage.redact_noop === true;
  const recommended = String(stage.recommended_action || "").toLowerCase();

  let summary = "";

  if (action === "block" && isInjectionThreat(stage.threat_type)) {
    const detail = String(stage.detail || "").trim();
    summary = threat
      ? `Input scan blocked for ${threat}${detail ? ` — ${detail}` : ""}.`
      : "Input scan detected a jailbreak or injection pattern and blocked the request.";
  } else if (action === "block") {
    const detail = String(stage.detail || "").trim();
    summary = threat
      ? `Input scan blocked the request due to ${threat}${detail ? ` — ${detail}` : ""}.`
      : (detail || "Input scan blocked the request.");
  } else if (action === "redact") {
    summary = threat
      ? `Input scan masked ${threat} in the prompt before it reached the model.`
      : "Input scan redacted sensitive content before the model call.";
  } else if (analyzedOnly) {
    if (threat) {
      summary = `Policy already masked sensitive fields. Input scan analyzed the remaining text and noted ${threat}; no further masking was applied.`;
    } else {
      summary = "Policy already masked sensitive fields. Input scan analyzed the remaining text; no further masking was applied.";
    }
    if (recommended && recommended !== action && recommended !== "allow") {
      summary += ` The guard model recommended ${recommended.toUpperCase()} (shown for audit).`;
    }
  } else if (action === "flag" && threat) {
    summary = `Input scan flagged ${threat} and allowed the request to continue under monitor posture.`;
  } else if (threat) {
    summary = `Input scan completed with no enforcement action; ${threat} was noted.`;
  } else {
    summary = "Input scan completed with no threats detected.";
  }

  return {
    summary,
    technical: {
      guard_reason: stage.guard_reason || "",
      detail: stage.detail || "",
      matched_patterns: stage.matched_patterns || [],
      guard_findings: stage.guard_findings || [],
      recommended_action: stage.recommended_action || "",
      tier: stage.tier || "",
      threat_type: stage.threat_type || "",
      scan_outcome: stage.scan_outcome || "",
      redact_noop: stage.redact_noop === true,
    },
  };
}
