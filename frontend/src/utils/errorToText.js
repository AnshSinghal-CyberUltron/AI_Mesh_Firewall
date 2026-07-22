/**
 * Coerce gateway/API error payloads to display-safe strings.
 * OpenAI-style envelopes use nested { message, type, code } objects.
 */
export function errorToText(err) {
  if (!err) return "";
  if (typeof err === "string") return err;
  if (typeof err === "object") {
    return err.message || err.detail || err.error?.message || JSON.stringify(err);
  }
  return String(err);
}

/**
 * Operator-facing routing failure text for Module 1.5 simulator.
 */
export function formatRoutingError(data, httpStatus) {
  const code = String(data?.code || data?.error?.code || "").toLowerCase();
  const blockedBy = String(data?.blocked_by || "").toLowerCase();
  const routing = data?.zeroshield?.routing || {};
  const routingReason = routing?.routing_reason || routing?.reroute_reason || "";

  if (
    code === "compliance_routing_unsatisfiable"
    || blockedBy === "compliance_routing"
    || String(data?.error || "").includes("compliance_routing_unsatisfiable")
  ) {
    return (
      routingReason
      || "No connected model satisfies the required compliance/sensitivity for this request. "
        + "Tag at least one active model with restricted sensitivity and the required compliance tags, "
        + "or lower the sensitivity requirement."
    );
  }

  if (code === "model_not_allowed" || String(data?.message || "").includes("global allowlist")) {
    return errorToText(data?.message) || "Selected model is not in the firewall allowed-models list.";
  }

  if (httpStatus === 503 || code === "service_unavailable") {
    return errorToText(data?.message) || "Gateway routing is temporarily unavailable. Retry in a moment.";
  }

  if (httpStatus >= 500) {
    return errorToText(data?.message || data?.error) || "Gateway error while evaluating routing. Retry or check gateway health.";
  }

  return errorToText(data?.message || data?.error) || "Routing request failed.";
}
