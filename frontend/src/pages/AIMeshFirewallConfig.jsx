import { useState, useEffect, useCallback } from "react";
import {
  Settings, Save, RotateCcw, AlertCircle, CheckCircle2,
  Shield, Lock, Activity, FileText, Database, Zap, Filter, Ban, Eye, Info,
  Loader2,
} from "lucide-react";
import { useAuth } from "../context/AuthContext";
import { ModelConnectionPanel } from "../components/ModelConnectionPanel";
import { DatabaseConnectionPanel } from "../components/DatabaseConnectionPanel";
import { ZeroShieldGuardModelTestPanel } from "../components/BedrockTestPanel";
import {
  ZEROSHIELD_TIER1_LABEL,
  ZEROSHIELD_TIER2_LABEL,
} from "../constants/zeroshieldBrand";

const DEFAULT_CONFIG = {
  firewallEnabled: true,
  enforcementMode: "block",
  logLevel: "detailed",
  requestsPerMinute: 1000,
  burstLimit: 150,
  rateLimitEnabled: true,
  piiDetectionEnabled: true,
  toxicityThreshold: 0.7,
  contentFilteringEnabled: true,
  blockedKeywords: "password, secret, api_key, token",
  allowedModels: "gpt-4, gpt-3.5-turbo, claude-3",
  defaultModel: "gpt-4",
  modelIsolationEnabled: true,
  promptInjectionThreshold: 0.8,
  jailbreakDetectionEnabled: true,
  semanticAnalysisEnabled: true,
  tier2ExecutionMode: "sync_pre_llm",
  tier2StreamHoldEnabled: false,
  tier2StreamHoldTimeoutMs: 1200,
  maxResponseTokens: 4096,
  responseFilteringEnabled: true,
  factualityCheckEnabled: false,
  ragEnabled: true,
  ragMaxDocuments: 5,
  ragRelevanceThreshold: 0.75,
  vectorDbIsolation: true,
  threatIntelEnabled: true,
  autoBlockThreats: true,
  threatScoreThreshold: 75,
  auditLoggingEnabled: true,
  retentionDays: 90,
  complianceFrameworks: ["SOC2", "ISO27001"],
  alertingEnabled: true,
  criticalAlertThreshold: 90,
  alertRecipients: "security-team@company.com",
};

function apiToFrontend(data) {
  return {
    firewallEnabled: data.firewall_enabled ?? DEFAULT_CONFIG.firewallEnabled,
    enforcementMode: data.enforcement_mode ?? DEFAULT_CONFIG.enforcementMode,
    logLevel: data.log_level ?? DEFAULT_CONFIG.logLevel,
    rateLimitEnabled: data.rate_limit_enabled ?? DEFAULT_CONFIG.rateLimitEnabled,
    requestsPerMinute: data.requests_per_minute ?? DEFAULT_CONFIG.requestsPerMinute,
    burstLimit: data.burst_limit ?? DEFAULT_CONFIG.burstLimit,
    contentFilteringEnabled: data.content_filtering_enabled ?? DEFAULT_CONFIG.contentFilteringEnabled,
    piiDetectionEnabled: data.pii_detection_enabled ?? DEFAULT_CONFIG.piiDetectionEnabled,
    toxicityThreshold: data.toxicity_threshold ?? DEFAULT_CONFIG.toxicityThreshold,
    blockedKeywords: data.blocked_keywords ?? DEFAULT_CONFIG.blockedKeywords,
    modelIsolationEnabled: data.model_isolation_enabled ?? DEFAULT_CONFIG.modelIsolationEnabled,
    allowedModels: data.allowed_models ?? DEFAULT_CONFIG.allowedModels,
    defaultModel: data.default_model ?? DEFAULT_CONFIG.defaultModel,
    jailbreakDetectionEnabled: data.jailbreak_detection_enabled ?? DEFAULT_CONFIG.jailbreakDetectionEnabled,
    semanticAnalysisEnabled: data.semantic_analysis_enabled ?? DEFAULT_CONFIG.semanticAnalysisEnabled,
    tier2ExecutionMode: data.tier2_execution_mode ?? DEFAULT_CONFIG.tier2ExecutionMode,
    tier2StreamHoldEnabled: data.tier2_stream_hold_enabled ?? DEFAULT_CONFIG.tier2StreamHoldEnabled,
    tier2StreamHoldTimeoutMs: data.tier2_stream_hold_timeout_ms ?? DEFAULT_CONFIG.tier2StreamHoldTimeoutMs,
    promptInjectionThreshold: data.prompt_injection_threshold ?? DEFAULT_CONFIG.promptInjectionThreshold,
    responseFilteringEnabled: data.response_filtering_enabled ?? DEFAULT_CONFIG.responseFilteringEnabled,
    factualityCheckEnabled: data.factuality_check_enabled ?? DEFAULT_CONFIG.factualityCheckEnabled,
    maxResponseTokens: data.max_response_tokens ?? DEFAULT_CONFIG.maxResponseTokens,
    ragEnabled: data.rag_enabled ?? DEFAULT_CONFIG.ragEnabled,
    vectorDbIsolation: data.vector_db_isolation ?? DEFAULT_CONFIG.vectorDbIsolation,
    ragMaxDocuments: data.rag_max_documents ?? DEFAULT_CONFIG.ragMaxDocuments,
    ragRelevanceThreshold: data.rag_relevance_threshold ?? DEFAULT_CONFIG.ragRelevanceThreshold,
    threatIntelEnabled: data.threat_intel_enabled ?? DEFAULT_CONFIG.threatIntelEnabled,
    autoBlockThreats: data.auto_block_threats ?? DEFAULT_CONFIG.autoBlockThreats,
    threatScoreThreshold: data.threat_score_threshold ?? DEFAULT_CONFIG.threatScoreThreshold,
    auditLoggingEnabled: data.audit_logging_enabled ?? DEFAULT_CONFIG.auditLoggingEnabled,
    retentionDays: data.retention_days ?? DEFAULT_CONFIG.retentionDays,
    complianceFrameworks: data.compliance_frameworks ?? DEFAULT_CONFIG.complianceFrameworks,
    alertingEnabled: data.alerting_enabled ?? DEFAULT_CONFIG.alertingEnabled,
    criticalAlertThreshold: data.critical_alert_threshold ?? DEFAULT_CONFIG.criticalAlertThreshold,
    alertRecipients: data.alert_recipients ?? DEFAULT_CONFIG.alertRecipients,
    updatedAt: data.updated_at || null,
  };
}

function frontendToApi(config) {
  return {
    firewall_enabled: config.firewallEnabled,
    enforcement_mode: config.enforcementMode,
    log_level: config.logLevel,
    rate_limit_enabled: config.rateLimitEnabled,
    requests_per_minute: config.requestsPerMinute,
    burst_limit: config.burstLimit,
    content_filtering_enabled: config.contentFilteringEnabled,
    pii_detection_enabled: config.piiDetectionEnabled,
    toxicity_threshold: config.toxicityThreshold,
    blocked_keywords: config.blockedKeywords,
    model_isolation_enabled: config.modelIsolationEnabled,
    allowed_models: config.allowedModels,
    default_model: config.defaultModel,
    jailbreak_detection_enabled: config.jailbreakDetectionEnabled,
    semantic_analysis_enabled: config.semanticAnalysisEnabled,
    tier2_execution_mode: config.tier2ExecutionMode,
    tier2_stream_hold_enabled: config.tier2StreamHoldEnabled,
    tier2_stream_hold_timeout_ms: config.tier2StreamHoldTimeoutMs,
    prompt_injection_threshold: config.promptInjectionThreshold,
    response_filtering_enabled: config.responseFilteringEnabled,
    factuality_check_enabled: config.factualityCheckEnabled,
    max_response_tokens: config.maxResponseTokens,
    rag_enabled: config.ragEnabled,
    vector_db_isolation: config.vectorDbIsolation,
    rag_max_documents: config.ragMaxDocuments,
    rag_relevance_threshold: config.ragRelevanceThreshold,
    threat_intel_enabled: config.threatIntelEnabled,
    auto_block_threats: config.autoBlockThreats,
    threat_score_threshold: config.threatScoreThreshold,
    audit_logging_enabled: config.auditLoggingEnabled,
    retention_days: config.retentionDays,
    compliance_frameworks: config.complianceFrameworks,
    alerting_enabled: config.alertingEnabled,
    critical_alert_threshold: config.criticalAlertThreshold,
    alert_recipients: config.alertRecipients,
  };
}

export function AIMeshFirewallConfig() {
  const { fetchWithAuth } = useAuth();
  const [config, setConfig] = useState(DEFAULT_CONFIG);
  const [serverConfig, setServerConfig] = useState(null);
  const [loading, setLoading] = useState(true);
  const [isSaving, setIsSaving] = useState(false);
  const [saveSuccess, setSaveSuccess] = useState(false);
  const [error, setError] = useState(null);

  const fetchConfig = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await fetchWithAuth("/api/firewall/config/");
      if (res.ok) {
        const data = await res.json();
        const mapped = apiToFrontend(data);
        setConfig(mapped);
        setServerConfig(mapped);
      } else if (res.status === 401) {
        setError("Authentication required. Please log in again.");
      } else {
        setError("Failed to load firewall configuration.");
      }
    } catch {
      setError("Network error loading configuration.");
    } finally {
      setLoading(false);
    }
  }, [fetchWithAuth]);

  useEffect(() => {
    fetchConfig();
  }, [fetchConfig]);

  const handleSave = async () => {
    setIsSaving(true);
    setSaveSuccess(false);
    setError(null);
    try {
      const payload = frontendToApi(config);
      const res = await fetchWithAuth("/api/firewall/config/", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      if (res.ok) {
        const data = await res.json();
        const mapped = apiToFrontend(data);
        setConfig(mapped);
        setServerConfig(mapped);
        setSaveSuccess(true);
        setTimeout(() => setSaveSuccess(false), 4000);
      } else if (res.status === 400) {
        const body = await res.json().catch(() => null);
        const detail = body
          ? Object.entries(body).map(([k, v]) => `${k}: ${Array.isArray(v) ? v.join(", ") : v}`).join("; ")
          : "Invalid configuration values.";
        setError(detail);
      } else {
        setError("Failed to save configuration.");
      }
    } catch {
      setError("Network error saving configuration.");
    } finally {
      setIsSaving(false);
    }
  };

  const handleReset = () => {
    if (serverConfig) {
      setConfig({ ...serverConfig });
    } else {
      setConfig({ ...DEFAULT_CONFIG });
    }
    setError(null);
  };

  if (loading) {
    return (
      <div className="flex items-center justify-center h-64">
        <Loader2 className="w-8 h-8 text-teal-500 animate-spin" />
        <span className="ml-3 text-sm text-slate-600 dark:text-slate-400">Loading firewall configuration...</span>
      </div>
    );
  }

  return (
    <div className="space-y-6">
      {/* Page Header */}
      <div className="bg-white dark:bg-slate-800 rounded-xl border-2 border-slate-200 dark:border-slate-700 shadow-sm p-6">
        <div className="flex items-start justify-between">
          <div className="flex items-start gap-4">
            <div className="w-14 h-14 bg-gradient-to-br from-violet-500 to-purple-600 rounded-xl flex items-center justify-center shadow-md">
              <Settings className="w-7 h-7 text-white" strokeWidth={2.5} />
            </div>
            <div>
              <h1 className="text-2xl font-bold text-slate-900 dark:text-slate-100 mb-1">Firewall Configuration & Policy Settings</h1>
              <p className="text-sm text-slate-600 dark:text-slate-400 mb-3">
                Configure enforcement policies, thresholds, and security controls for AI Mesh Firewall
              </p>
              <div className="flex items-center gap-3">
                <div className="flex items-center gap-2 px-3 py-1.5 bg-blue-50 dark:bg-blue-900/20 rounded-lg border border-blue-200 dark:border-blue-800">
                  <FileText className="w-4 h-4 text-blue-600" strokeWidth={2.5} />
                  <span className="text-xs font-semibold text-blue-700">Configuration Mode</span>
                </div>
                <div className="flex items-center gap-2 px-3 py-1.5 bg-amber-50 dark:bg-amber-900/20 rounded-lg border border-amber-200 dark:border-amber-800">
                  <AlertCircle className="w-4 h-4 text-amber-600" strokeWidth={2.5} />
                  <span className="text-xs font-semibold text-amber-700">Changes require save to take effect</span>
                </div>
                {config.updatedAt && (
                  <div className="flex items-center gap-2 px-3 py-1.5 bg-slate-50 dark:bg-slate-800/50 rounded-lg border border-slate-200 dark:border-slate-700">
                    <span className="text-xs text-slate-500 dark:text-slate-400">
                      Last saved: {new Date(config.updatedAt).toLocaleString()}
                    </span>
                  </div>
                )}
              </div>
            </div>
          </div>
          <div className="flex items-center gap-2">
            <button onClick={handleReset} className="flex items-center gap-2 px-4 py-2.5 bg-slate-100 dark:bg-slate-700 text-slate-700 dark:text-slate-300 rounded-lg hover:bg-slate-200 transition-colors border border-slate-300 dark:border-slate-600">
              <RotateCcw className="w-4 h-4" /><span className="text-sm font-medium">Reset</span>
            </button>
            <button
              onClick={handleSave} disabled={isSaving}
              className="flex items-center gap-2 px-4 py-2.5 bg-gradient-to-r from-teal-500 to-cyan-600 text-white rounded-lg hover:from-teal-600 hover:to-cyan-700 transition-all shadow-md disabled:opacity-50 disabled:cursor-not-allowed"
            >
              {isSaving ? (
                <><div className="w-4 h-4 border-2 border-white border-t-transparent rounded-full animate-spin"></div><span className="text-sm font-medium">Saving...</span></>
              ) : (
                <><Save className="w-4 h-4" /><span className="text-sm font-medium">Save Configuration</span></>
              )}
            </button>
          </div>
        </div>
        {saveSuccess && (
          <div className="mt-4 flex items-center gap-3 p-4 bg-emerald-50 dark:bg-emerald-900/20 border border-emerald-200 dark:border-emerald-800 rounded-lg">
            <CheckCircle2 className="w-5 h-5 text-emerald-600" />
            <div>
              <p className="text-sm font-semibold text-emerald-900 dark:text-emerald-100">Configuration saved successfully</p>
              <p className="text-xs text-emerald-700">Changes are now active across all firewall enforcement points via Redis hot-reload</p>
            </div>
          </div>
        )}
        {error && (
          <div className="mt-4 flex items-center gap-3 p-4 bg-red-50 dark:bg-red-900/20 border border-red-200 dark:border-red-800 rounded-lg">
            <AlertCircle className="w-5 h-5 text-red-600" />
            <div>
              <p className="text-sm font-semibold text-red-900 dark:text-red-100">Error</p>
              <p className="text-xs text-red-700">{error}</p>
            </div>
          </div>
        )}
      </div>

      <ModelConnectionPanel showProviderForm={true} showConnectionsTable={false} />

      {/* Configuration Sections */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        <ConfigSection title="General Firewall Settings" icon={Shield} iconColor="from-teal-500 to-cyan-600">
          <ToggleField label="Firewall Enabled" description="Master switch for all firewall enforcement"
            checked={config.firewallEnabled} onChange={(v) => setConfig({ ...config, firewallEnabled: v })} />
          <SelectField label="Enforcement Mode" description="How the firewall handles policy violations"
            value={config.enforcementMode} onChange={(v) => setConfig({ ...config, enforcementMode: v })}
            options={[
              { value: "block", label: "Block - Reject violating requests" },
              { value: "monitor", label: "Monitor - Log violations, allow traffic" },
              { value: "audit", label: "Audit - Log only, no alerts" },
            ]} />
          <SelectField label="Log Level" description="Detail level for security logs"
            value={config.logLevel} onChange={(v) => setConfig({ ...config, logLevel: v })}
            options={[
              { value: "minimal", label: "Minimal - Errors only" },
              { value: "standard", label: "Standard - Errors + warnings" },
              { value: "detailed", label: "Detailed - Full request/response" },
              { value: "verbose", label: "Verbose - Debug information" },
            ]} />
        </ConfigSection>

        <ConfigSection title="Rate Limiting & Throttling" icon={Activity} iconColor="from-blue-500 to-indigo-600">
          <ToggleField label="Rate Limiting Enabled" description="Protect against abuse and DDoS"
            checked={config.rateLimitEnabled} onChange={(v) => setConfig({ ...config, rateLimitEnabled: v })} />
          <NumberField label="Requests Per Minute" description="Maximum requests allowed globally per minute"
            value={config.requestsPerMinute} onChange={(v) => setConfig({ ...config, requestsPerMinute: v })}
            min={1} max={10000} unit="req/min" />
          <NumberField label="Burst Limit" description="Maximum requests in a short burst"
            value={config.burstLimit} onChange={(v) => setConfig({ ...config, burstLimit: v })}
            min={1} max={1000} unit="requests" />
        </ConfigSection>

        <ConfigSection title="Content Filtering & Detection" icon={Filter} iconColor="from-orange-500 to-red-600">
          <ToggleField label="Content Filtering Enabled" description="Scan and filter sensitive content"
            checked={config.contentFilteringEnabled} onChange={(v) => setConfig({ ...config, contentFilteringEnabled: v })} />
          <ToggleField label="PII Detection" description="Detect and redact personally identifiable information"
            checked={config.piiDetectionEnabled} onChange={(v) => setConfig({ ...config, piiDetectionEnabled: v })} />
          <SliderField label="Toxicity Threshold" description="Minimum score to flag toxic content (0-1)"
            value={config.toxicityThreshold} onChange={(v) => setConfig({ ...config, toxicityThreshold: v })}
            min={0} max={1} step={0.1} />
          <TextAreaField label="Blocked Keywords" description="Comma-separated list of keywords to block"
            value={config.blockedKeywords} onChange={(v) => setConfig({ ...config, blockedKeywords: v })}
            placeholder="password, secret, api_key, token" />
        </ConfigSection>

        <ConfigSection title="Model Governance & Routing" icon={Database} iconColor="from-purple-500 to-pink-600">
          <ToggleField label="Model Isolation Enabled" description="Enforce strict model boundaries"
            checked={config.modelIsolationEnabled} onChange={(v) => setConfig({ ...config, modelIsolationEnabled: v })} />
          <TextAreaField label="Allowed Models" description="Comma-separated list of approved models"
            value={config.allowedModels} onChange={(v) => setConfig({ ...config, allowedModels: v })}
            placeholder="gpt-4, claude-3, llama-2" />
          <SelectField label="Default Model" description="Fallback model when none specified"
            value={config.defaultModel} onChange={(v) => setConfig({ ...config, defaultModel: v })}
            options={[
              { value: "gpt-4", label: "GPT-4" },
              { value: "gpt-3.5-turbo", label: "GPT-3.5 Turbo" },
              { value: "claude-3", label: "Claude 3" },
            ]} />
        </ConfigSection>

        <ConfigSection title="Prompt Security & Injection Protection" icon={Lock} iconColor="from-red-500 to-orange-600">
          <ToggleField label="Jailbreak Detection" description="Detect and block prompt injection attempts"
            checked={config.jailbreakDetectionEnabled} onChange={(v) => setConfig({ ...config, jailbreakDetectionEnabled: v })} />
          <ToggleField label="Semantic Analysis" description={`Deep analysis of prompt intent (${ZEROSHIELD_TIER2_LABEL})`}
            checked={config.semanticAnalysisEnabled} onChange={(v) => setConfig({ ...config, semanticAnalysisEnabled: v })} />
          <SelectField label="Guard Model Execution Mode" description="Choose if the ZeroShield guard model runs before or after LLM forwarding"
            value={config.tier2ExecutionMode} onChange={(v) => setConfig({ ...config, tier2ExecutionMode: v })}
            options={[
              { value: "sync_pre_llm", label: "Sync Pre-LLM (recommended for strict blocking)" },
              { value: "async_post_llm", label: "Async Post-LLM (lower latency, post-response enforcement)" },
            ]} />
          <ToggleField label="Guard Model Stream Hold" description="Allow a short pre-stream hold window in async mode for block semantics"
            checked={config.tier2StreamHoldEnabled} onChange={(v) => setConfig({ ...config, tier2StreamHoldEnabled: v })} />
          <NumberField label="Guard Model Stream Hold Timeout" description="Max hold time before stream starts when hold mode is enabled"
            value={config.tier2StreamHoldTimeoutMs} onChange={(v) => setConfig({ ...config, tier2StreamHoldTimeoutMs: v })}
            min={500} max={2000} unit="ms" />
          <SliderField label="Prompt Injection Threshold" description="Sensitivity for injection detection (0-1)"
            value={config.promptInjectionThreshold} onChange={(v) => setConfig({ ...config, promptInjectionThreshold: v })}
            min={0} max={1} step={0.1} />
        </ConfigSection>

        <ConfigSection title="Response Guardrails & Validation" icon={Eye} iconColor="from-cyan-500 to-teal-600">
          <ToggleField label="Response Filtering Enabled" description="Scan and filter model responses"
            checked={config.responseFilteringEnabled} onChange={(v) => setConfig({ ...config, responseFilteringEnabled: v })} />
          <ToggleField label="Factuality Check" description="Verify response accuracy (hallucination detection)"
            checked={config.factualityCheckEnabled} onChange={(v) => setConfig({ ...config, factualityCheckEnabled: v })} />
          <NumberField label="Max Response Tokens" description="Maximum tokens in model response"
            value={config.maxResponseTokens} onChange={(v) => setConfig({ ...config, maxResponseTokens: v })}
            min={100} max={32000} unit="tokens" />
        </ConfigSection>

        <ConfigSection title="RAG Security & Vector DB Protection" icon={Database} iconColor="from-green-500 to-emerald-600">
          <ToggleField label="RAG Enabled" description="Enable Retrieval-Augmented Generation"
            checked={config.ragEnabled} onChange={(v) => setConfig({ ...config, ragEnabled: v })} />
          <ToggleField label="Vector DB Isolation" description="Enforce tenant isolation in vector databases"
            checked={config.vectorDbIsolation} onChange={(v) => setConfig({ ...config, vectorDbIsolation: v })} />
          <NumberField label="Max RAG Documents" description="Maximum documents to retrieve"
            value={config.ragMaxDocuments} onChange={(v) => setConfig({ ...config, ragMaxDocuments: v })}
            min={1} max={20} unit="documents" />
          <SliderField label="Relevance Threshold" description="Minimum similarity score for retrieval (0-1)"
            value={config.ragRelevanceThreshold} onChange={(v) => setConfig({ ...config, ragRelevanceThreshold: v })}
            min={0} max={1} step={0.05} />
        </ConfigSection>

        <ConfigSection title="Threat Intelligence Integration" icon={Zap} iconColor="from-amber-500 to-yellow-600">
          <ToggleField label="Threat Intelligence Enabled" description="Integrate with threat intelligence feeds"
            checked={config.threatIntelEnabled} onChange={(v) => setConfig({ ...config, threatIntelEnabled: v })} />
          <ToggleField label="Auto-Block Threats" description="Automatically block known threat actors"
            checked={config.autoBlockThreats} onChange={(v) => setConfig({ ...config, autoBlockThreats: v })} />
          <NumberField label="Threat Score Threshold" description="Minimum score to trigger blocking (0-100)"
            value={config.threatScoreThreshold} onChange={(v) => setConfig({ ...config, threatScoreThreshold: v })}
            min={0} max={100} unit="score" />
        </ConfigSection>

        <ConfigSection title="Audit Logging & Compliance" icon={FileText} iconColor="from-indigo-500 to-purple-600">
          <ToggleField label="Audit Logging Enabled" description="Comprehensive audit trail for compliance"
            checked={config.auditLoggingEnabled} onChange={(v) => setConfig({ ...config, auditLoggingEnabled: v })} />
          <NumberField label="Retention Days" description="How long to retain audit logs"
            value={config.retentionDays} onChange={(v) => setConfig({ ...config, retentionDays: v })}
            min={1} max={365} unit="days" />
          <MultiSelectField label="Compliance Frameworks" description="Active compliance frameworks"
            value={config.complianceFrameworks} onChange={(v) => setConfig({ ...config, complianceFrameworks: v })}
            options={["SOC2", "ISO27001", "HIPAA", "GDPR", "PCI-DSS", "NIST"]} />
        </ConfigSection>

        <ConfigSection title="Security Alerting & Notifications" icon={Ban} iconColor="from-rose-500 to-red-600">
          <ToggleField label="Alerting Enabled" description="Send alerts for security events"
            checked={config.alertingEnabled} onChange={(v) => setConfig({ ...config, alertingEnabled: v })} />
          <NumberField label="Critical Alert Threshold" description="Risk score to trigger critical alerts (0-100)"
            value={config.criticalAlertThreshold} onChange={(v) => setConfig({ ...config, criticalAlertThreshold: v })}
            min={0} max={100} unit="score" />
          <TextField label="Alert Recipients" description="Email addresses for security alerts"
            value={config.alertRecipients} onChange={(v) => setConfig({ ...config, alertRecipients: v })}
            placeholder="security-team@company.com" />
        </ConfigSection>
      </div>

      {/* Configuration Impact */}
      <div className="text-slate-900 dark:text-slate-100 bg-white dark:bg-slate-800 rounded-xl border-2 border-slate-200 dark:border-slate-700 shadow-sm p-6">
        <div className="flex items-start gap-3 mb-4">
          <div className="w-10 h-10 bg-gradient-to-br from-blue-500 to-indigo-600 rounded-lg flex items-center justify-center">
            <Info className="w-5 h-5 text-white" />
          </div>
          <div>
            <h3 className="text-lg font-semibold text-slate-900 dark:text-slate-100 mb-1">Configuration Impact & Audit Trail</h3>
            <p className="text-sm text-slate-600 dark:text-slate-400">
              Changes to this configuration affect real-time firewall enforcement, analytics dashboards, and compliance reporting
            </p>
          </div>
        </div>
        <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
          <ImpactCard icon={Shield} title="Enforcement Impact" description="Affects request blocking, rate limiting, and policy decisions"
            items={[
              `${config.enforcementMode === "block" ? "Blocking" : "Monitoring"} mode active`,
              `Rate limit: ${config.requestsPerMinute} req/min`,
              `${config.contentFilteringEnabled ? "Content filtering enabled" : "Content filtering disabled"}`,
            ]} />
          <ImpactCard icon={Activity} title="Dashboard Impact" description="Updates metrics, graphs, and real-time analytics"
            items={["Real-time threat detection charts", "Risk distribution graphs", "Activity feed and logs"]} />
          <ImpactCard icon={FileText} title="Compliance Impact" description="Affects audit trails and regulatory reporting"
            items={[
              `${config.complianceFrameworks.length} frameworks active`,
              `${config.retentionDays}-day log retention`,
              `${config.auditLoggingEnabled ? "Full audit trail" : "Limited logging"}`,
            ]} />
        </div>
      </div>

      {/* Validation Warnings */}
      <div className="space-y-3">
        {!config.firewallEnabled && (
          <div className="flex items-start gap-3 p-4 bg-red-50 dark:bg-red-900/20 border-2 border-red-300 rounded-lg">
            <AlertCircle className="w-5 h-5 text-red-600 flex-shrink-0 mt-0.5" />
            <div>
              <p className="text-sm font-semibold text-red-900 dark:text-red-100">Critical Warning: Firewall Disabled</p>
              <p className="text-xs text-red-700 mt-1">All AI traffic is currently unprotected. Enable firewall immediately to restore security controls.</p>
            </div>
          </div>
        )}
        {config.enforcementMode === "monitor" && (
          <div className="flex items-start gap-3 p-4 bg-amber-50 dark:bg-amber-900/20 border border-amber-300 rounded-lg">
            <AlertCircle className="w-5 h-5 text-amber-600 flex-shrink-0 mt-0.5" />
            <div>
              <p className="text-sm font-semibold text-amber-900 dark:text-amber-100">Monitor Mode Active</p>
              <p className="text-xs text-amber-700 mt-1">Violations are logged but not blocked. Switch to "Block" mode for active protection.</p>
            </div>
          </div>
        )}
        {!config.threatIntelEnabled && (
          <div className="flex items-start gap-3 p-4 bg-blue-50 dark:bg-blue-900/20 border border-blue-200 dark:border-blue-800 rounded-lg">
            <Info className="w-5 h-5 text-blue-600 flex-shrink-0 mt-0.5" />
            <div>
              <p className="text-sm font-semibold text-blue-900 dark:text-blue-100">Threat Intelligence Disabled</p>
              <p className="text-xs text-blue-700 mt-1">Enable threat intelligence to leverage real-time threat feeds and automated blocking.</p>
            </div>
          </div>
        )}
      </div>

      {/* Service Connections & Tools */}
      <ModelConnectionPanel showProviderForm={false} showConnectionsTable={true} />
      <DatabaseConnectionPanel />
      <ZeroShieldGuardModelTestPanel />
    </div>
  );
}

function ConfigSection({ title, icon, iconColor, children }) {
  const IconComponent = icon;
  return (
    <div className="bg-white dark:bg-slate-800 rounded-xl border border-slate-200 dark:border-slate-700 shadow-sm p-6">
      <div className="flex items-center gap-3 mb-5 pb-4 border-b border-slate-200 dark:border-slate-700">
        <div className={`w-9 h-9 bg-gradient-to-br ${iconColor} rounded-lg flex items-center justify-center shadow-sm`}>
          <IconComponent className="w-5 h-5 text-white" strokeWidth={2.5} />
        </div>
        <h3 className="text-base font-semibold text-slate-900 dark:text-slate-100">{title}</h3>
      </div>
      <div className="space-y-5">{children}</div>
    </div>
  );
}

function ToggleField({ label, description, checked, onChange }) {
  return (
    <div className="flex items-start justify-between gap-4">
      <div className="flex-1">
        <label className="text-sm font-medium text-slate-900 dark:text-slate-100 block mb-1">{label}</label>
        <p className="text-xs text-slate-500 dark:text-slate-400">{description}</p>
      </div>
      <button
        onClick={() => onChange(!checked)}
        className={`relative inline-flex h-6 w-11 flex-shrink-0 cursor-pointer rounded-full border-2 border-transparent transition-colors duration-200 ease-in-out focus:outline-none focus:ring-2 focus:ring-teal-500 focus:ring-offset-2 ${checked ? "bg-teal-600" : "bg-slate-300"}`}
      >
        <span className={`pointer-events-none inline-block h-5 w-5 transform rounded-full bg-white dark:bg-slate-800 shadow ring-0 transition duration-200 ease-in-out ${checked ? "translate-x-5" : "translate-x-0"}`} />
      </button>
    </div>
  );
}

function SelectField({ label, description, value, onChange, options }) {
  return (
    <div>
      <label className="text-sm font-medium text-slate-900 dark:text-slate-100 block mb-1">{label}</label>
      <p className="text-xs text-slate-500 dark:text-slate-400 mb-2">{description}</p>
      <select
        value={value} onChange={(e) => onChange(e.target.value)}
        className="w-full px-3 py-2 bg-white dark:bg-slate-800 border border-slate-300 dark:border-slate-600 rounded-lg text-sm text-slate-900 dark:text-slate-100 focus:ring-2 focus:ring-teal-500 focus:border-teal-500 transition-colors"
      >
        {options.map((option) => (<option key={option.value} value={option.value}>{option.label}</option>))}
      </select>
    </div>
  );
}

function NumberField({ label, description, value, onChange, min, max, unit }) {
  return (
    <div>
      <label className="text-sm font-medium text-slate-900 dark:text-slate-100 block mb-1">{label}</label>
      <p className="text-xs text-slate-500 dark:text-slate-400 mb-2">{description}</p>
      <div className="flex items-center gap-2">
        <input
          type="number" value={value} onChange={(e) => onChange(Number(e.target.value))} min={min} max={max}
          className="flex-1 px-3 py-2 bg-white dark:bg-slate-800 border border-slate-300 dark:border-slate-600 rounded-lg text-sm text-slate-900 dark:text-slate-100 focus:ring-2 focus:ring-teal-500 focus:border-teal-500 transition-colors"
        />
        <span className="text-xs font-medium text-slate-600 dark:text-slate-400 w-20">{unit}</span>
      </div>
    </div>
  );
}

function SliderField({ label, description, value, onChange, min, max, step }) {
  return (
    <div>
      <div className="flex items-center justify-between mb-1">
        <label className="text-sm font-medium text-slate-900 dark:text-slate-100">{label}</label>
        <span className="text-sm font-semibold text-teal-600">{value.toFixed(2)}</span>
      </div>
      <p className="text-xs text-slate-500 dark:text-slate-400 mb-2">{description}</p>
      <input
        type="range" value={value} onChange={(e) => onChange(Number(e.target.value))}
        min={min} max={max} step={step}
        className="text-slate-900 dark:text-slate-100 w-full h-2 bg-slate-200 rounded-lg appearance-none cursor-pointer accent-teal-600"
      />
    </div>
  );
}

function TextField({ label, description, value, onChange, placeholder }) {
  return (
    <div>
      <label className="text-sm font-medium text-slate-900 dark:text-slate-100 block mb-1">{label}</label>
      <p className="text-xs text-slate-500 dark:text-slate-400 mb-2">{description}</p>
      <input
        type="text" value={value} onChange={(e) => onChange(e.target.value)} placeholder={placeholder}
        className="w-full px-3 py-2 bg-white dark:bg-slate-800 border border-slate-300 dark:border-slate-600 rounded-lg text-sm text-slate-900 dark:text-slate-100 focus:ring-2 focus:ring-teal-500 focus:border-teal-500 transition-colors"
      />
    </div>
  );
}

function TextAreaField({ label, description, value, onChange, placeholder }) {
  return (
    <div>
      <label className="text-sm font-medium text-slate-900 dark:text-slate-100 block mb-1">{label}</label>
      <p className="text-xs text-slate-500 dark:text-slate-400 mb-2">{description}</p>
      <textarea
        value={value} onChange={(e) => onChange(e.target.value)} placeholder={placeholder} rows={3}
        className="w-full px-3 py-2 bg-white dark:bg-slate-800 border border-slate-300 dark:border-slate-600 rounded-lg text-sm text-slate-900 dark:text-slate-100 focus:ring-2 focus:ring-teal-500 focus:border-teal-500 transition-colors resize-none"
      />
    </div>
  );
}

function MultiSelectField({ label, description, value, onChange, options }) {
  const toggleOption = (option) => {
    if (value.includes(option)) {
      onChange(value.filter((v) => v !== option));
    } else {
      onChange([...value, option]);
    }
  };
  return (
    <div>
      <label className="text-sm font-medium text-slate-900 dark:text-slate-100 block mb-1">{label}</label>
      <p className="text-xs text-slate-500 dark:text-slate-400 mb-2">{description}</p>
      <div className="flex flex-wrap gap-2">
        {options.map((option) => (
          <button
            key={option} onClick={() => toggleOption(option)}
            className={`px-3 py-1.5 rounded-lg text-xs font-medium transition-colors ${
              value.includes(option)
                ? "bg-teal-100 dark:bg-teal-800/30 text-teal-700 border-2 border-teal-500"
                : "bg-slate-100 dark:bg-slate-700 text-slate-600 dark:text-slate-400 border border-slate-300 dark:border-slate-600 hover:bg-slate-200"
            }`}
          >
            {option}
          </button>
        ))}
      </div>
    </div>
  );
}

function ImpactCard({ icon, title, description, items }) {
  const IconComponent = icon;
  return (
    <div className="p-4 bg-slate-50 dark:bg-slate-800/50 rounded-lg border border-slate-200 dark:border-slate-700">
      <div className="flex items-center gap-2 mb-2">
        <IconComponent className="w-4 h-4 text-teal-600" />
        <h4 className="text-sm font-semibold text-slate-900 dark:text-slate-100">{title}</h4>
      </div>
      <p className="text-xs text-slate-600 dark:text-slate-400 mb-3">{description}</p>
      <ul className="space-y-1">
        {items.map((item, index) => (
          <li key={index} className="flex items-start gap-2">
            <div className="w-1 h-1 bg-teal-500 rounded-full mt-1.5 flex-shrink-0"></div>
            <span className="text-xs text-slate-700 dark:text-slate-300">{item}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}