/**
 * Centralized MCP color/label semantics — single source of truth for status,
 * risk, sensitivity, per-tier scan ACTION, and MCPEvent DECISION. Badge values
 * map to the variants in components/ui/Badge.jsx.
 */

export const CONNECTION_STATUS = {
  connected: { label: "Connected", badge: "success", dot: "bg-teal-500" },
  active: { label: "Active", badge: "success", dot: "bg-teal-500" },
  healthy: { label: "Healthy", badge: "success", dot: "bg-teal-500" },
  failed: { label: "Failed", badge: "danger", dot: "bg-red-500" },
  error: { label: "Error", badge: "danger", dot: "bg-red-500" },
  unreachable: { label: "Unreachable", badge: "danger", dot: "bg-red-500" },
  pending: { label: "Pending", badge: "warning", dot: "bg-amber-500" },
  needs_reauth: { label: "Needs re-auth", badge: "warning", dot: "bg-amber-500" },
  unknown: { label: "Unknown", badge: "secondary", dot: "bg-slate-400" },
};

export const RISK = {
  low: "success",
  medium: "warning",
  high: "danger",
  critical: "danger",
  unknown: "secondary",
};

export const SENSITIVITY = {
  low: "secondary",
  medium: "info",
  high: "warning",
  critical: "danger",
};

/** Per-tier scan action — matches backend MCPScanControl.action choices. */
export const ACTION = {
  inherit: { label: "Inherit", badge: "secondary", help: "Defer to the server/tool default action." },
  monitor: { label: "Monitor", badge: "info", help: "Detect + log, but allow the call (observe-only)." },
  tag: { label: "Tag", badge: "secondary", help: "Observe-only (legacy alias of monitor)." },
  redact: { label: "Redact", badge: "warning", help: "Mask the matched content, then allow." },
  block: { label: "Block", badge: "danger", help: "Block the call when a finding matches." },
};

/** MCPEvent.decision — matches backend DECISION_CHOICES. */
export const DECISION = {
  allow: { label: "Allow", badge: "success" },
  monitor: { label: "Monitor", badge: "info" },
  redact: { label: "Redact", badge: "warning" },
  block: { label: "Block", badge: "danger" },
  error: { label: "Error", badge: "secondary" },
};

export const connectionInfo = (s) => CONNECTION_STATUS[String(s || "").toLowerCase()] || CONNECTION_STATUS.unknown;
export const riskBadge = (r) => RISK[String(r || "").toLowerCase()] || RISK.unknown;
export const sensitivityBadge = (s) => SENSITIVITY[String(s || "").toLowerCase()] || SENSITIVITY.low;
export const actionInfo = (a) => ACTION[String(a || "inherit").toLowerCase()] || ACTION.inherit;
export const decisionInfo = (d) => DECISION[String(d || "").toLowerCase()] || DECISION.error;
