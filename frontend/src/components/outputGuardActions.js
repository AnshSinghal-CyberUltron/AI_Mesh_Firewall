/**
 * T01 L01-2: operator REWRITE is unsupported.
 * UI must not offer it; stored rewrite values coerce to the detector default.
 */
export const OUTPUT_GUARD_ACTIONS = [
  { value: "block", label: "Block", hint: "Reject the response (HTTP 403) — nothing is delivered." },
  { value: "redact", label: "Redact", hint: "Mask the offending spans, deliver the sanitized text." },
  { value: "flag", label: "Flag", hint: "Deliver as-is but mark for review / log an incident." },
  { value: "allow", label: "Allow", hint: "Take no action (monitoring only)." },
];

export const UNSUPPORTED_OUTPUT_ACTIONS = new Set(["rewrite"]);

const SUPPORTED = new Set(OUTPUT_GUARD_ACTIONS.map((o) => o.value));

export function coerceOutputGuardAction(value, fallback = "redact") {
  const v = String(value || "").toLowerCase();
  if (UNSUPPORTED_OUTPUT_ACTIONS.has(v) || !SUPPORTED.has(v)) {
    return fallback;
  }
  return v;
}

export function guardrailPayloadWithoutRewrite(state, keys, detectorDefaults) {
  const payload = {};
  for (const k of keys) {
    let val = state[k];
    if (typeof k === "string" && k.endsWith("_action")) {
      val = coerceOutputGuardAction(val, detectorDefaults[k] || "redact");
    }
    payload[k] = val;
  }
  return payload;
}
