/**
 * Pure state<->PUT-payload mapping for the Tier-2 scan control
 * (policy-driven-detection Req 3.6). Single source of truth shared by
 * Tier2ScanControl.jsx and its unit test.
 *
 * The stored ``tier2_enabled`` config key is a NULLABLE TRI-STATE:
 *   null / undefined = Inherit (gateway default)
 *   true             = Enabled
 *   false            = Disabled
 *
 * The UI SegmentedControl uses string segments "inherit" / "enabled" /
 * "disabled". These helpers map both directions so the displayed segment
 * reflects the backend value AND a toggle builds the correct partial PUT
 * payload the control-plane firewall-config API expects.
 */

export const TIER2_SEGMENTS = ["inherit", "enabled", "disabled"];

// Segment -> tier2_enabled value sent in the partial PUT.
const SEGMENT_TO_VALUE = { inherit: null, enabled: true, disabled: false };

/**
 * Reflect a backend ``tier2_enabled`` value to the displayed segment.
 * @param {null|undefined|boolean} backendValue
 * @returns {"inherit"|"enabled"|"disabled"}
 */
export function resolveTier2SegmentValue(backendValue) {
  return backendValue === null || backendValue === undefined
    ? "inherit"
    : backendValue
      ? "enabled"
      : "disabled";
}

/**
 * Build the partial PUT payload for a chosen segment.
 * @param {"inherit"|"enabled"|"disabled"} segment
 * @returns {{ tier2_enabled: null|boolean }}
 */
export function buildTier2PutPayload(segment) {
  const value = segment in SEGMENT_TO_VALUE ? SEGMENT_TO_VALUE[segment] : null;
  return { tier2_enabled: value };
}
