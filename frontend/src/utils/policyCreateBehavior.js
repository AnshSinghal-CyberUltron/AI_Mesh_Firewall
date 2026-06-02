const POLICY_TABS = new Set(["global", "pipeline", "rag", "mcp"]);
const INSPECTION_TABS = new Set(["analytics", "vector"]);

function normalizeSection(section) {
  const normalized = String(section || "").toLowerCase();
  if (POLICY_TABS.has(normalized) || INSPECTION_TABS.has(normalized)) {
    return normalized;
  }
  return "global";
}

function normalizeSource(source) {
  const normalized = String(source || "").toLowerCase();
  if (normalized === "header" || normalized === "panel") {
    return normalized;
  }
  return "header";
}

export function resolvePolicyCreateBehavior(activeSection, createSource) {
  const section = normalizeSection(activeSection);
  const source = normalizeSource(createSource);
  const fromInspectionTab = INSPECTION_TABS.has(section);

  const targetSection = fromInspectionTab ? "global" : section;
  const scope = POLICY_TABS.has(targetSection) ? targetSection : "global";

  return {
    createSource: source,
    fromSection: section,
    targetSection,
    scope,
    shouldSwitchSection: targetSection !== section,
    shouldOpenCreateModal: true,
  };
}

