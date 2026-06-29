const POLICY_TABS = new Set(["pipeline", "rag", "mcp"]);
const INSPECTION_TABS = new Set(["analytics", "vector"]);

export const POLICY_DOMAIN_UI = {
  pipeline: {
    label: "Pipeline",
    createTitle: "Create Pipeline Policy",
    description:
      "Rules for the live chat gateway path (/v1/chat/completions): input scan, routing, model output, and output guard stages.",
  },
  rag: {
    label: "RAG",
    createTitle: "Create RAG Policy",
    description:
      "Rules for retrieval pipelines: query, retriever, ranker, and generator stages in the RAG path.",
  },
  mcp: {
    label: "MCP",
    createTitle: "Create MCP Policy",
    description:
      "Rules for MCP tool calls. Restrict actors, target tools, and redact sensitive keys in tool args/responses.",
  },
  // Vector is a SEPARATE backend resource (/api/vector-policies/, its own model
  // and form). It still appears in the New Policy domain switcher so the panel
  // can morph to its dedicated 16-field form; selecting it hands off to the
  // VectorPolicyModal rather than POSTing through the generic /api/policies/ path.
  vector: {
    label: "Vector",
    createTitle: "Create Vector Policy",
    description:
      "Collection-level controls for vector DB retrieval: namespace isolation, allowed operations, embedding limits, and anomaly thresholds.",
  },
};

// Ordered domain list backing the in-panel New Policy domain switcher. The first
// four share the generic /api/policies/ resource; "vector" is a separate resource
// reached by handing off to the dedicated Vector modal.
export const POLICY_CREATE_DOMAINS = [
  { key: "pipeline", label: "Pipeline" },
  { key: "rag", label: "RAG" },
  { key: "mcp", label: "MCP" },
  { key: "vector", label: "Vector" },
];

export function getPolicyDomainUi(scope) {
  const key = normalizeSection(scope);
  if (key === "analytics") {
    return POLICY_DOMAIN_UI.pipeline;
  }
  return POLICY_DOMAIN_UI[key] || POLICY_DOMAIN_UI.pipeline;
}

function normalizeSection(section) {
  const normalized = String(section || "").toLowerCase();
  if (normalized === "global") {
    return "pipeline";
  }
  if (POLICY_TABS.has(normalized) || INSPECTION_TABS.has(normalized)) {
    return normalized;
  }
  return "pipeline";
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

  const targetSection = fromInspectionTab ? "pipeline" : section;
  const scope = POLICY_TABS.has(targetSection) ? targetSection : "pipeline";

  return {
    createSource: source,
    fromSection: section,
    targetSection,
    scope,
    lockScope: POLICY_TABS.has(targetSection),
    shouldSwitchSection: targetSection !== section,
    shouldOpenCreateModal: true,
    usesVectorModal: section === "vector",
  };
}
