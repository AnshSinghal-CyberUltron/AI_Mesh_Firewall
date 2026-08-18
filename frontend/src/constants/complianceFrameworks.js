/**
 * Canonical compliance-framework identity, shared by the model form and routing UI.
 *
 * These tags are matched by the gateway's routing hard filter. They used to be free
 * text, so an operator typing "hipaa" on one model and "HIPAA" on another produced a
 * catalogue where a client asking for either got a partial match — or, when every model
 * used the other casing, a spurious "no compliant model" 403.
 *
 * Must stay in step with:
 *   control/ai_mesh_control/core/routing_compliance.py
 *   gateway/ai_mesh_gateway/llm_router.py::canonical_compliance_tag
 */

/** Frameworks an operator may attach to a model. Canonical value + display label. */
export const COMPLIANCE_FRAMEWORKS = [
  { value: "SOC2", label: "SOC 2" },
  { value: "ISO27001", label: "ISO 27001" },
  { value: "HIPAA", label: "HIPAA" },
  { value: "GDPR", label: "GDPR" },
  { value: "PCI_DSS", label: "PCI-DSS" },
  { value: "NIST", label: "NIST" },
];

export const COMPLIANCE_FRAMEWORK_VALUES = COMPLIANCE_FRAMEWORKS.map((f) => f.value);

/** Variants folded onto one identity. Keys are already canonicalized. */
const ALIASES = {
  SOC_2: "SOC2",
  SOC2_TYPE_II: "SOC2",
  SOC2_TYPE2: "SOC2",
  ISO_27001: "ISO27001",
  ISO_IEC_27001: "ISO27001",
  PCIDSS: "PCI_DSS",
  PCI: "PCI_DSS",
  HIPPA: "HIPAA", // common misspelling, seen in operator input
  NIST_CSF: "NIST",
  NIST_800_53: "NIST",
  GDPR_EU: "GDPR",
};

/** Normalize one token to canonical form ("" when empty/unrecognizable). */
export function canonicalComplianceTag(value) {
  const token = String(value ?? "")
    .trim()
    .toUpperCase()
    .replace(/[^A-Z0-9]+/g, "_")
    .replace(/^_+|_+$/g, "");
  if (!token) return "";
  return ALIASES[token] || token;
}

/** Human label for a tag, falling back to the canonical value. */
export function complianceLabel(value) {
  const canon = canonicalComplianceTag(value);
  return COMPLIANCE_FRAMEWORKS.find((f) => f.value === canon)?.label || canon;
}
