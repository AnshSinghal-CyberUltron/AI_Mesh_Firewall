// Pure helpers for the honest pipeline-trace timeline (TRACE_UI_CONTRACT.md).
// Kept framework-free so they can be unit-tested with `node --test`.

// Per-stage action -> small status-badge label (honest per-stage; never a single
// global action smeared across every stage).
export const ACTION_BADGE = {
  block: "blocked",
  redact: "redacted",
  rewrite: "rewritten",
  flag: "flagged",
  monitor: "monitored",
  reroute: "rerouted",
  skip: "skipped",
  allow: "allowed",
};

export const STAGE_LABELS = {
  auth: "Auth",
  rate_limit: "Rate Limit",
  policy: "Policy Engine",
  policy_redact: "Policy Redaction",
  input_scan: "Input Scan (Tier-1/2)",
  route: "Model Routing",
  model_routing: "Model Routing",
  kill_switch: "Kill-Switch",
  llm: "Model Call",
  model_input: "Model Input",
  model_output: "Model Output",
  output_guard: "Output Guard",
  output_guardrail: "Output Guard",
};

// Contract honesty rule: a `redact` whose scrubber was a no-op on the egress bytes
// is displayed as `flag`, never a phantom redaction (parity with the gateway at
// main.py:1566, which relabels redact->flag when the bytes are unchanged).
export function honestStageAction(stage) {
  let a = String((stage && stage.action) || "allow").toLowerCase();
  const noop = stage && ((stage.metadata && stage.metadata.redact_noop) || stage.redact_noop);
  if (a === "redact" && noop) a = "flag";
  return a;
}

// Build honest timeline nodes from the gateway's REAL pipeline_trace.stages[].
// Each node carries its OWN action; stages render in array order per the contract.
// Returns plain data objects (no JSX) so the render layer stays dumb + testable.
export function buildHonestTraceStages(realStages, finalAction, ctx = {}) {
  const out = [];
  if (ctx.promptSnippet) {
    out.push({ id: "input", label: "User Input", content: ctx.promptSnippet });
  }
  (realStages || []).forEach((s, idx) => {
    const name = s.stage || s.name || "stage";
    const a = honestStageAction(s);
    const tags = [];
    if (s.detection_tier || s.tier) tags.push(`tier: ${s.detection_tier || s.tier}`);
    if (s.threat_type) tags.push(`category: ${String(s.threat_type).replace(/_/g, " ")}`);
    (s.matched_policy_names || s.matched_policies || []).forEach((p) => tags.push(`policy: ${p}`));
    (s.matched_rules || s.matched_rule_names || []).forEach((r) => tags.push(`rule: ${r}`));
    const lat = typeof s.latency_ms === "number" ? s.latency_ms : null;
    out.push({
      id: name,
      label: `${idx + 1}. ${STAGE_LABELS[name] || name.replace(/_/g, " ")}`,
      content: s.detail || s.reason || "",
      badge: ACTION_BADGE[a] || a,
      highlight: a !== "allow" && a !== "skip",
      highlightAction: a,
      tags,
      metrics: lat && lat > 0 ? [{ label: "Latency", value: `${Math.round(lat * 10) / 10}ms` }] : undefined,
    });
  });
  const fa = String(finalAction || "allow").toLowerCase();
  out.push({
    id: "final_output",
    label: "Final Response to Client",
    content:
      fa === "block"
        ? "[Response blocked — not delivered to client]"
        : fa === "redact"
          ? ctx.sanitizedOutput || "(sensitive spans redacted)"
          : ctx.rawOutput || "(delivered)",
    badge: ACTION_BADGE[fa] || fa,
    highlight: fa !== "allow",
    highlightAction: fa === "block" ? "block" : fa,
  });
  return out;
}

// Locate the gateway's real per-stage trace on a telemetry event, across the
// several envelope shapes the gateway/telemetry use.
export function extractRealStages(event) {
  if (!event) return [];
  const meta = event.metadata || {};
  const extra = meta.extra || {};
  const zs = event.zeroshield || meta.zeroshield || extra.zeroshield || {};
  const trace =
    zs.pipeline_trace || meta.pipeline_trace || extra.pipeline_trace || event.pipeline_trace || null;
  return Array.isArray(trace && trace.stages)
    ? trace.stages.filter((s) => s && (s.stage || s.name))
    : [];
}

export function extractFinalAction(event, fallback) {
  const meta = (event && event.metadata) || {};
  const extra = meta.extra || {};
  const zs = (event && event.zeroshield) || meta.zeroshield || extra.zeroshield || {};
  const trace = zs.pipeline_trace || meta.pipeline_trace || extra.pipeline_trace || (event && event.pipeline_trace) || {};
  return trace.final_action || (event && event.action) || fallback || "allow";
}
