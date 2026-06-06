import { Component, useCallback, useRef, useState } from "react";
import { modelListSignature } from "../utils/modelListSignature";
import { FirewallConfigProvider, useFirewallConfig } from "../hooks/useFirewallConfig";
import { Zap, GitBranch, Database, Eye, Shield, AlertTriangle, Filter } from "lucide-react";
import { GatewayKeyPanel } from "../components/GatewayKeyPanel";
import { KillSwitchPanel } from "../components/KillSwitchPanel";
import { AttackSimulatorPanel } from "../components/AttackSimulatorPanel";
import { ModelConnectionPanel } from "../components/ModelConnectionPanel";
import { ModelGovernancePanel } from "../components/ModelGovernancePanel";
import { RoutingAuditPanel } from "../components/RoutingAuditPanel";
import { RoutingGovernancePanel } from "../components/RoutingGovernancePanel";
import { OutputGovernancePanel } from "../components/OutputGovernancePanel";
import { OutputGuardrailEngineCard } from "../components/OutputGuardrailEngineCard";
import { OutputGuardrailCharts } from "../components/OutputGuardrailCharts";
import { OutputGuardrailControls } from "../components/OutputGuardrailControls";
import { RAGPipelineTelemetry } from "../components/RAGPipelineTelemetry";
import { RAGFeatureTestPanel } from "../components/RAGFeatureTestPanel";
import { RAGAttackTrustSimulator } from "../components/RAGAttackTrustSimulator";
import { DatabaseConnectionPanel } from "../components/DatabaseConnectionPanel";
import { MCPGuardrailSimulator } from "../components/simulator/MCPGuardrailSimulator";
import { ModelRoutingSimulator } from "../components/simulator/ModelRoutingSimulator";
import { IsolationOpsSimulator } from "../components/simulator/IsolationOpsSimulator";
import { OrgIsolationBanner } from "../components/OrgIsolationBanner";
import { RAGIngestionPanel } from "../components/simulator/RAGIngestionPanel";
import { VectorProviderConfigPanel } from "../components/rag/VectorProviderConfigPanel";
import { CollectionManagerPanel } from "../components/rag/CollectionManagerPanel";
import { SemanticSearchPanel } from "../components/rag/SemanticSearchPanel";
import { RAGSetupGuide } from "../components/rag/RAGSetupGuide";
import { ModelStatePanel } from "../components/ModelStatePanel";
import { FirewallModulePage } from "../components/FirewallModulePage";
import { MCPConnectorPanel } from "../components/MCPConnectorPanel";
import { Firewall12EnterprisePage } from "../components/Firewall12EnterprisePage";

class FirewallModuleErrorBoundary extends Component {
  constructor(props) {
    super(props);
    this.state = { hasError: false };
  }

  static getDerivedStateFromError() {
    return { hasError: true };
  }

  render() {
    if (this.state.hasError) {
      return (
        <div className="rounded-xl border border-red-200 bg-red-50 p-6 text-sm text-red-800 dark:border-red-900/60 dark:bg-red-950/30 dark:text-red-200">
          <h2 className="text-base font-semibold">Multi-Model Governance failed to render</h2>
          <p className="mt-2">
            One of the routing panels threw an error while loading. The rest of the app remains usable, and this tab no longer blanks the screen.
          </p>
        </div>
      );
    }

    return this.props.children;
  }
}

// 1.1 AI Gateway & Traffic Ingress
export function Firewall11Page({ onViewResults, onViewLogDetail, children }) {
  const flowNodes = [
    { label: "Client", format: (summary) => `${summary.total.toLocaleString()} requests`, color: "blue" },
    { label: "Auth Layer", format: (summary) => `${summary.allowed.toLocaleString()} passed`, color: "teal" },
    { label: "Rate Limiter", format: (summary) => `${summary.blocked.toLocaleString()} blocked`, color: "purple" },
    { label: "Gateway", format: (summary) => `${summary.critical.toLocaleString()} critical`, color: "emerald" },
  ];

  return (
    <FirewallModulePage
      moduleId="1.1"
      title="AI Gateway & Traffic Ingress"
      description="The network entry point for every model request. This page now centers ingress pressure, gateway controls, and live evidence instead of the shared generic telemetry frame."
      icon={Zap}
      flowNodes={flowNodes}
      onViewResults={onViewResults}
      onViewLogDetail={onViewLogDetail}
      controlPanels={[<GatewayKeyPanel key="gateway-keys" />]}
      simulatorPanels={[<AttackSimulatorPanel key="attack-sim" />]}
      inspectionPanels={children ? [children] : []}
    />
  );
}

// 1.2 Policy Management
export function Firewall12Page({ onViewResults, onViewLogDetail, children }) {
  return <Firewall12EnterprisePage onViewResults={onViewResults} onViewLogDetail={onViewLogDetail} children={children} />;
}

// 1.3 RAG & Vector DB Firewall
export function Firewall13Page({ onViewResults, onViewLogDetail, children }) {
  const flowNodes = [
    { label: "Query", format: (summary) => `${summary.total.toLocaleString()} routed`, color: "teal" },
    { label: "Retriever", format: (summary) => `${summary.allowed.toLocaleString()} scoped`, color: "purple" },
    { label: "Vector DB", format: (summary) => `${summary.blocked.toLocaleString()} denied`, color: "indigo" },
    { label: "Generator", format: (summary) => `${summary.redacted.toLocaleString()} sanitized`, color: "cyan" },
  ];

  return (
    <FirewallModulePage
      moduleId="1.3"
      title="RAG & Vector DB Firewall"
      description="A unified retrieval-security workspace: provider setup and connection testing, collection management, secure ingestion, query/attack/trust simulation, and pipeline monitoring."
      icon={Database}
      flowNodes={flowNodes}
      onViewResults={onViewResults}
      onViewLogDetail={onViewLogDetail}
      controlPanels={[
        <RAGSetupGuide key="rag-setup-guide" />,
        <VectorProviderConfigPanel key="vector-providers" />,
        <DatabaseConnectionPanel key="db-connection" />,
        <CollectionManagerPanel key="collection-manager" />,
        <RAGIngestionPanel key="rag-ingestion" />,
      ]}
      simulatorPanels={[
        <SemanticSearchPanel key="semantic-search" />,
        <RAGFeatureTestPanel key="rag-feature-test" />,
        <RAGAttackTrustSimulator key="rag-attack-trust" />,
      ]}
      inspectionPanels={[
        <RAGPipelineTelemetry key="rag-telemetry" />,
        ...(children ? [children] : []),
      ]}
    />
  );
}

// 1.4 Context Assembly & MCP Guardrails
export function Firewall14Page({ onViewResults, onViewLogDetail, children }) {
  const flowNodes = [
    { label: "Context Fields", format: (summary) => `${summary.total.toLocaleString()} assembled`, color: "blue" },
    { label: "PII Redaction", format: (summary) => `${summary.redacted.toLocaleString()} sanitized`, color: "amber" },
    { label: "Size Check", format: (summary) => `${summary.blocked.toLocaleString()} denied`, color: "purple" },
    { label: "Final Context", format: (summary) => `${summary.allowed.toLocaleString()} approved`, color: "emerald" },
  ];

  return (
    <FirewallModulePage
      moduleId="1.4"
      title="Context Assembly & MCP Guardrails"
      description="The control surface for context minimization, MCP tool scope, data-access guardrails, and field-level redaction before generation."
      icon={Eye}
      flowNodes={flowNodes}
      onViewResults={onViewResults}
      onViewLogDetail={onViewLogDetail}
      controlPanels={[<MCPConnectorPanel key="mcp-connector" />]}
      simulatorPanels={[<MCPGuardrailSimulator key="mcp-simulator" />]}
      inspectionPanels={[
        ...(children ? [children] : []),
      ]}
    />
  );
}

// 1.5 Multi-Model Governance & Routing
export function Firewall15Page(props) {
  return (
    <FirewallModuleErrorBoundary>
      <FirewallConfigProvider>
        <Firewall15PageInner {...props} />
      </FirewallConfigProvider>
    </FirewallModuleErrorBoundary>
  );
}

function Firewall15PageInner({ onViewResults, onViewLogDetail }) {
  const { invalidate } = useFirewallConfig();
  const lastModelsSigRef = useRef("");

  const handleConnectionsMutated = useCallback(() => {
    lastModelsSigRef.current = "";
    invalidate();
  }, [invalidate]);

  const handleModelsChanged = useCallback(
    (models) => {
      const sig = modelListSignature(models);
      if (sig === lastModelsSigRef.current) return;
      lastModelsSigRef.current = sig;
      invalidate();
    },
    [invalidate],
  );

  const flowNodes = [
    { label: "Request", format: (summary) => `${summary.total.toLocaleString()} decisions`, color: "blue" },
    { label: "Router", format: (summary) => `${summary.allowed.toLocaleString()} routed`, color: "purple" },
    { label: "Model Pool", format: (summary) => `${summary.blocked.toLocaleString()} denied`, color: "cyan" },
    { label: "Execute", format: (summary) => `${summary.critical.toLocaleString()} high-risk`, color: "emerald" },
  ];

  return (
    <FirewallModulePage
      moduleId="1.5"
      title="Multi-Model Governance & AI Mesh Routing"
      description="A model-mesh governance surface for routing decisions, failover behavior, provider controls, and policy-aware execution targets."
      icon={Shield}
      flowNodes={flowNodes}
      onViewResults={onViewResults}
      onViewLogDetail={onViewLogDetail}
      controlPanels={[
        <GatewayKeyPanel key="gateway-keys-routing" />,
        <ModelConnectionPanel
          key="model-connections"
          showProviderForm={false}
          showGatewayCatalog={false}
          onModelsChanged={handleModelsChanged}
          onConnectionsMutated={handleConnectionsMutated}
        />,
        <ModelGovernancePanel key="model-governance" />,
        <RoutingGovernancePanel key="routing-governance" />,
      ]}
      simulatorPanels={[<ModelRoutingSimulator key="routing-simulator" />]}
      footerPanels={[<RoutingAuditPanel key="routing-audit" />]}
    />
  );
}

// 1.6 Inline Model Isolation & Kill-Switch
export function Firewall16Page({ onViewResults, onViewLogDetail, children }) {
  const flowNodes = [
    { label: "Model Active", format: (summary) => `${summary.total.toLocaleString()} watched`, color: "emerald" },
    { label: "Risk Analysis", format: (summary) => `${summary.critical.toLocaleString()} critical`, color: "amber" },
    { label: "Threshold", format: (summary) => `${summary.blocked.toLocaleString()} contained`, color: "orange" },
    { label: "Status", format: (summary) => `${summary.allowed.toLocaleString()} passed`, color: "emerald" },
  ];

  return (
    <FirewallModulePage
      moduleId="1.6"
      title="Inline Model Isolation & Kill-Switch"
      description="An incident-style workspace for model containment, threshold breaches, circuit-breaker state, and emergency isolation controls."
      icon={AlertTriangle}
      flowNodes={flowNodes}
      onViewResults={onViewResults}
      onViewLogDetail={onViewLogDetail}
      controlPanels={[
        <OrgIsolationBanner key="org-isolation-banner" />,
        <GatewayKeyPanel key="gateway-keys-isolation" />,
        <ModelStatePanel key="model-state" />,
        <KillSwitchPanel key="kill-switch" />,
      ]}
      simulatorPanels={[<IsolationOpsSimulator key="isolation-ops" />]}
      inspectionPanels={children ? [children] : []}
    />
  );
}

// 1.7 Generator-Level Output Guardrails
export function Firewall17Page({ onViewResults, onViewLogDetail }) {
  return (
    <FirewallModulePage
      moduleId="1.7"
      title="Generator-Level Output Guardrails"
      description="Real-time output governance: every model response is scanned for PII, credentials, hallucinations, and IP leakage before delivery. No black boxes."
      icon={Filter}
      onViewResults={onViewResults}
      onViewLogDetail={onViewLogDetail}
      showEvidenceSection={false}
      controlPanels={[
        <OutputGuardrailControls key="output-guardrail-controls" />,
        <OutputGuardrailEngineCard key="engine-card" />,
        <OutputGuardrailCharts key="output-charts" />,
        <OutputGovernancePanel key="output-governance" />,
      ]}
    />
  );
}
