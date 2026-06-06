import { formatAllowedModelsForApi, parseAllowedModels } from "../../utils/firewallAllowlist";

export const DEFAULT_FIREWALL_CONFIG = {
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
  allowedModels: "",
  defaultModel: "",
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
  ragRelevanceThreshold: 0.7,
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
  updatedAt: null,
};

export function configReducer(state, action) {
  switch (action.type) {
    case "SET":
      return { ...state, ...action.payload };
    case "REPLACE":
      return { ...action.payload };
    case "PATCH":
      return { ...state, [action.key]: action.value };
    default:
      return state;
  }
}

export function configsEqual(a, b) {
  if (!a || !b) return false;
  const keys = Object.keys(DEFAULT_FIREWALL_CONFIG);
  return keys.every((k) => {
    if (k === "updatedAt") return true;
    const av = a[k];
    const bv = b[k];
    if (Array.isArray(av) && Array.isArray(bv)) {
      return av.length === bv.length && av.every((v, i) => v === bv[i]);
    }
    return av === bv;
  });
}

export function apiToFrontend(data) {
  return {
    firewallEnabled: data.firewall_enabled ?? DEFAULT_FIREWALL_CONFIG.firewallEnabled,
    enforcementMode: data.enforcement_mode ?? DEFAULT_FIREWALL_CONFIG.enforcementMode,
    logLevel: data.log_level ?? DEFAULT_FIREWALL_CONFIG.logLevel,
    rateLimitEnabled: data.rate_limit_enabled ?? DEFAULT_FIREWALL_CONFIG.rateLimitEnabled,
    requestsPerMinute: data.requests_per_minute ?? DEFAULT_FIREWALL_CONFIG.requestsPerMinute,
    burstLimit: data.burst_limit ?? DEFAULT_FIREWALL_CONFIG.burstLimit,
    contentFilteringEnabled: data.content_filtering_enabled ?? DEFAULT_FIREWALL_CONFIG.contentFilteringEnabled,
    piiDetectionEnabled: data.pii_detection_enabled ?? DEFAULT_FIREWALL_CONFIG.piiDetectionEnabled,
    toxicityThreshold: data.toxicity_threshold ?? DEFAULT_FIREWALL_CONFIG.toxicityThreshold,
    blockedKeywords: data.blocked_keywords ?? DEFAULT_FIREWALL_CONFIG.blockedKeywords,
    modelIsolationEnabled: data.model_isolation_enabled ?? DEFAULT_FIREWALL_CONFIG.modelIsolationEnabled,
    allowedModels: formatAllowedModelsForApi(
      data.allowed_models_list ?? parseAllowedModels(data.allowed_models),
    ),
    defaultModel: data.default_model ?? DEFAULT_FIREWALL_CONFIG.defaultModel,
    jailbreakDetectionEnabled: data.jailbreak_detection_enabled ?? DEFAULT_FIREWALL_CONFIG.jailbreakDetectionEnabled,
    semanticAnalysisEnabled: data.semantic_analysis_enabled ?? DEFAULT_FIREWALL_CONFIG.semanticAnalysisEnabled,
    tier2ExecutionMode: data.tier2_execution_mode ?? DEFAULT_FIREWALL_CONFIG.tier2ExecutionMode,
    tier2StreamHoldEnabled: data.tier2_stream_hold_enabled ?? DEFAULT_FIREWALL_CONFIG.tier2StreamHoldEnabled,
    tier2StreamHoldTimeoutMs: data.tier2_stream_hold_timeout_ms ?? DEFAULT_FIREWALL_CONFIG.tier2StreamHoldTimeoutMs,
    promptInjectionThreshold: data.prompt_injection_threshold ?? DEFAULT_FIREWALL_CONFIG.promptInjectionThreshold,
    responseFilteringEnabled: data.response_filtering_enabled ?? DEFAULT_FIREWALL_CONFIG.responseFilteringEnabled,
    factualityCheckEnabled: data.factuality_check_enabled ?? DEFAULT_FIREWALL_CONFIG.factualityCheckEnabled,
    maxResponseTokens: data.max_response_tokens ?? DEFAULT_FIREWALL_CONFIG.maxResponseTokens,
    ragEnabled: data.rag_enabled ?? DEFAULT_FIREWALL_CONFIG.ragEnabled,
    vectorDbIsolation: data.vector_db_isolation ?? DEFAULT_FIREWALL_CONFIG.vectorDbIsolation,
    ragMaxDocuments: data.rag_max_documents ?? DEFAULT_FIREWALL_CONFIG.ragMaxDocuments,
    ragRelevanceThreshold: data.rag_relevance_threshold ?? DEFAULT_FIREWALL_CONFIG.ragRelevanceThreshold,
    threatIntelEnabled: data.threat_intel_enabled ?? DEFAULT_FIREWALL_CONFIG.threatIntelEnabled,
    autoBlockThreats: data.auto_block_threats ?? DEFAULT_FIREWALL_CONFIG.autoBlockThreats,
    threatScoreThreshold: data.threat_score_threshold ?? DEFAULT_FIREWALL_CONFIG.threatScoreThreshold,
    auditLoggingEnabled: data.audit_logging_enabled ?? DEFAULT_FIREWALL_CONFIG.auditLoggingEnabled,
    retentionDays: data.retention_days ?? DEFAULT_FIREWALL_CONFIG.retentionDays,
    complianceFrameworks: data.compliance_frameworks ?? DEFAULT_FIREWALL_CONFIG.complianceFrameworks,
    alertingEnabled: data.alerting_enabled ?? DEFAULT_FIREWALL_CONFIG.alertingEnabled,
    criticalAlertThreshold: data.critical_alert_threshold ?? DEFAULT_FIREWALL_CONFIG.criticalAlertThreshold,
    alertRecipients: data.alert_recipients ?? DEFAULT_FIREWALL_CONFIG.alertRecipients,
    updatedAt: data.updated_at || null,
  };
}

export function frontendToApi(config) {
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
