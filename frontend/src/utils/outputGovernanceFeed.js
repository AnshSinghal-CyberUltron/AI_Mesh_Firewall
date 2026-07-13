/** Helpers for §1.7 Output Governance feeds (threat-feed collapse fix). */

const OUTPUT_EVENT_TYPES = new Set(["output_guard", "output_scan"]);

function eventType(event) {
  return String(event?.metadata?.event_type || "").toLowerCase();
}

function requestId(event) {
  const rid = event?.metadata?.request_id || event?.metadata?.pipeline_request_id;
  return typeof rid === "string" && rid.trim().length >= 8 ? rid.trim() : "";
}

function outputGuardStageAction(event) {
  const stages = event?.metadata?.pipeline_trace?.stages || [];
  const stage = stages.find((s) => (s?.name || s?.stage) === "output_guardrail");
  return String(stage?.action || "").toLowerCase();
}

export function isOutputGovernanceEvent(event) {
  const et = eventType(event);
  if (OUTPUT_EVENT_TYPES.has(et)) {
    return true;
  }
  const ogAction = outputGuardStageAction(event);
  return ogAction && ogAction !== "allow" && ogAction !== "skip";
}

function outputEventRank(event) {
  const et = eventType(event);
  if (et === "output_guard") return 0;
  if (et === "output_scan") return 1;
  const ogAction = outputGuardStageAction(event);
  if (ogAction && ogAction !== "allow" && ogAction !== "skip") return 2;
  return 9;
}

/** Prefer output_guard rows over collapsed request rows sharing a request_id. */
export function selectOutputGovernanceEvents(results = []) {
  const picked = new Map();
  const standalone = [];

  for (const event of results) {
    if (!isOutputGovernanceEvent(event)) {
      continue;
    }
    const rid = requestId(event);
    if (!rid) {
      standalone.push(event);
      continue;
    }
    const prev = picked.get(rid);
    if (!prev || outputEventRank(event) < outputEventRank(prev)) {
      picked.set(rid, event);
    }
  }

  const merged = [...standalone, ...picked.values()];
  merged.sort((a, b) => {
    const ta = Date.parse(a?.timestamp || "") || 0;
    const tb = Date.parse(b?.timestamp || "") || 0;
    return tb - ta;
  });
  return merged;
}

export function isRedactNoop(event) {
  const meta = event?.metadata || {};
  const extra = meta.extra || {};
  if (extra.redact_noop === true || meta.redact_noop === true) {
    return true;
  }
  const raw = extra.raw_output || meta.raw_output || "";
  const sanitized = extra.sanitized_output || meta.sanitized_output || "";
  return (
    String(event?.action || "").toLowerCase() === "redact"
    && raw
    && raw === sanitized
  );
}
