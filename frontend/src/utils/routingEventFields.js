/**
 * Shared field extraction for model_routed / routing enforcement events.
 * Gateway route metadata is stored under metadata.extra after telemetry drain.
 */

export function getEventMetadata(event) {
  return event?.metadata || {};
}

export function getRoutingExtra(event) {
  const meta = getEventMetadata(event);
  const extra = meta.extra;
  return extra && typeof extra === "object" ? extra : {};
}

export function getRequestedModel(event) {
  const meta = getEventMetadata(event);
  const extra = getRoutingExtra(event);
  const value =
    extra.original_model ||
    meta.original_model ||
    meta.requested_model ||
    meta.model_requested ||
    "";
  return String(value || "").trim() || "auto";
}

export function getRoutedModel(event) {
  const meta = getEventMetadata(event);
  const extra = getRoutingExtra(event);
  const value =
    extra.routed_model ||
    extra.selected_model ||
    meta.routed_model ||
    meta.selected_model ||
    meta.model ||
    event?.model ||
    "";
  return String(value || "").trim();
}

export function getRoutingContext(event) {
  const meta = getEventMetadata(event);
  const extra = getRoutingExtra(event);
  const policySummary = extra.policy_summary || meta.policy_summary || "";
  const decisionSource = extra.decision_source || meta.decision_source || "";
  const reason = extra.routing_reason || meta.routing_reason || "";
  const parts = [policySummary, decisionSource, reason].filter(Boolean);
  if (parts.length > 0) {
    return parts.join(" · ");
  }
  const action = event?.action || meta.action || "";
  return action ? `Action: ${action}` : "—";
}

export function isRoutingEvent(event) {
  const meta = getEventMetadata(event);
  const source = String(event?.source || meta.source || "").toLowerCase();
  const eventType = String(meta.event_type || "").toLowerCase();
  return source === "routing" || eventType === "model_routed";
}

export function filterRoutingEvents(events = []) {
  if (!Array.isArray(events)) return [];
  return events.filter(isRoutingEvent);
}
