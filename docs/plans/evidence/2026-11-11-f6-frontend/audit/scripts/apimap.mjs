// Static route -> API map: for each console view, walk its relative import graph
// and collect /api/... and /v1/... path literals (template params collapsed to {x}).
import fs from "node:fs";
import path from "node:path";
const SRC = process.argv[2];
const entries = {
  "/login (Login)": ["pages/Login.jsx"],
  "/ shell (DashboardLayout, Header, Sidebar)": ["components/layout/DashboardLayout.jsx"],
  "?tab=firewall (Overview)": ["pages/AIMeshFirewallOverview.jsx"],
  "?tab=firewall-1-1 Gateway & Traffic Ingress": ["components/GatewayKeyPanel.jsx", "components/AttackSimulatorPanel.jsx", "components/FirewallModulePage.jsx"],
  "?tab=firewall-1-2 Policy Management": ["components/Firewall12EnterprisePage.jsx"],
  "?tab=firewall-1-3 RAG & Vector DB": ["components/rag/RAGSetupGuide.jsx", "components/rag/VectorProviderConfigPanel.jsx", "components/DatabaseConnectionPanel.jsx", "components/rag/CollectionManagerPanel.jsx", "components/simulator/RAGIngestionPanel.jsx", "components/rag/SemanticSearchPanel.jsx", "components/RAGFeatureTestPanel.jsx", "components/RAGAttackTrustSimulator.jsx", "components/RAGPipelineTelemetry.jsx"],
  "?tab=firewall-1-4 Context Assembly & MCP": ["components/MCPConnectorPanel.jsx", "components/simulator/MCPGuardrailSimulator.jsx"],
  "?tab=firewall-1-5 Multi-Model Governance": ["components/ModelConnectionPanel.jsx", "components/ModelGovernancePanel.jsx", "components/RoutingGovernancePanel.jsx", "components/RoutingAuditPanel.jsx", "components/simulator/ModelRoutingSimulator.jsx", "components/GatewayKeyPanel.jsx"],
  "?tab=firewall-1-6 Model Isolation & Kill-Switch": ["components/KillSwitchPanel.jsx", "components/ModelStatePanel.jsx", "components/simulator/IsolationOpsSimulator.jsx", "components/OrgIsolationBanner.jsx"],
  "?tab=firewall-1-7 Output Guardrails": ["components/OutputGovernancePanel.jsx", "components/OutputGuardrailEngineCard.jsx", "components/OutputGuardrailCharts.jsx", "components/OutputGuardrailControls.jsx", "components/simulator/OutputGuardSimulator.jsx"],
  "?tab=firewall-config (Inputs)": ["pages/AIMeshFirewallConfig.jsx"],
  "results view (SubModuleResultsPage)": ["components/SubmoduleResultsPage.jsx"],
  "log detail (LogDetailPage)": ["components/LogDetailPage.jsx"],
  "?tab=profile": ["pages/Profile.jsx"],
  "?tab=settings": ["pages/Settings.jsx"],
};
const SHARED = new Set(["hooks/useFirewallData.js", "context/AuthContext.jsx", "hooks/useBackendHealth.js", "hooks/useRealtimeNotifications.js"]);
function resolve(from, spec) {
  if (!spec.startsWith(".")) return null;
  const base = path.resolve(path.dirname(from), spec);
  for (const c of [base, base + ".jsx", base + ".js", path.join(base, "index.js")]) if (fs.existsSync(c) && fs.statSync(c).isFile()) return c;
  return null;
}
function walk(file, seen) {
  if (seen.has(file)) return;
  seen.add(file);
  const s = fs.readFileSync(file, "utf8");
  for (const m of s.matchAll(/(?:import|export)[^'"]*?from\s*["']([^"']+)["']|import\(\s*["']([^"']+)["']\s*\)/g)) {
    const r = resolve(file, m[1] || m[2]);
    if (r && !r.endsWith(".test.js")) walk(r, seen);
  }
}
const out = {};
for (const [name, files] of Object.entries(entries)) {
  const seen = new Set();
  for (const f of files) walk(path.join(SRC, f), seen);
  const eps = new Set();
  for (const f of seen) {
    const rel = path.relative(SRC, f);
    if (SHARED.has(rel) && !files.includes(rel)) continue;
    const s = fs.readFileSync(f, "utf8");
    for (const m of s.matchAll(/["'`](\/(?:api|v1)\/[A-Za-z0-9_\-./${}?=&]*)/g)) {
      let p = m[1].split("?")[0].replace(/\$\{[^}]*\}/g, "{x}").replace(/\/+$/, "/");
      if (p.length > 6) eps.add(p);
    }
  }
  out[name] = { files: seen.size, endpoints: [...eps].sort() };
}
console.log(JSON.stringify(out, null, 1));
