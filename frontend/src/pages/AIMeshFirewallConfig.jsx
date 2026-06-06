import { useState, useEffect, useCallback, useReducer, useMemo } from "react";
import { AlertCircle, Info } from "lucide-react";
import { useAuth } from "../context/AuthContext";
import { FIREWALL_SECTIONS } from "../components/firewall/constants";
import {
  DEFAULT_FIREWALL_CONFIG,
  configReducer,
  configsEqual,
  apiToFrontend,
  frontendToApi,
} from "../components/firewall/store";
import { ConfigHeader } from "../components/firewall/primitives/ConfigHeader";
import { SectionSkeleton } from "../components/firewall/primitives/SectionSkeleton";
import { LlmProviderCard, LlmConnectionsCard } from "../components/firewall/LlmConnectionsCard";
import { GeneralSettingsCard } from "../components/firewall/GeneralSettingsCard";
import { RateLimitingCard } from "../components/firewall/RateLimitingCard";
import { ContentFilteringCard } from "../components/firewall/ContentFilteringCard";
import { ModelGovernanceCard } from "../components/firewall/ModelGovernanceCard";
import { PromptSecurityCard } from "../components/firewall/PromptSecurityCard";
import { ResponseGuardrailsCard } from "../components/firewall/ResponseGuardrailsCard";
import { RagSecurityCard } from "../components/firewall/RagSecurityCard";
import { ThreatIntelCard } from "../components/firewall/ThreatIntelCard";
import { AuditComplianceCard } from "../components/firewall/AuditComplianceCard";
import { AlertingCard } from "../components/firewall/AlertingCard";
import { ImpactPanel } from "../components/firewall/ImpactPanel";
import { VectorDbCard, ZeroShieldTestCard } from "../components/firewall/VectorDbCard";

export function AIMeshFirewallConfig() {
  const { fetchWithAuth } = useAuth();
  const [config, dispatch] = useReducer(configReducer, DEFAULT_FIREWALL_CONFIG);
  const [savedSnapshot, setSavedSnapshot] = useState(DEFAULT_FIREWALL_CONFIG);
  const [connectedModels, setConnectedModels] = useState([]);
  const [loading, setLoading] = useState(true);
  const [showContent, setShowContent] = useState(false);
  const [isSaving, setIsSaving] = useState(false);
  const [saveSuccess, setSaveSuccess] = useState(false);
  const [error, setError] = useState(null);

  const isDirty = useMemo(() => !configsEqual(config, savedSnapshot), [config, savedSnapshot]);

  const onPatch = useCallback((key, value) => {
    dispatch({ type: "PATCH", key, value });
  }, []);

  const refreshConnectedModels = useCallback(async () => {
    try {
      const res = await fetchWithAuth("/api/firewall/config/");
      if (res.ok) {
        const data = await res.json();
        setConnectedModels(Array.isArray(data.connected_models) ? data.connected_models : []);
      }
    } catch {
      /* non-blocking */
    }
  }, [fetchWithAuth]);

  const fetchConfig = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await fetchWithAuth("/api/firewall/config/");
      if (res.ok) {
        const data = await res.json();
        const mapped = apiToFrontend(data);
        dispatch({ type: "REPLACE", payload: mapped });
        setSavedSnapshot(mapped);
        setConnectedModels(Array.isArray(data.connected_models) ? data.connected_models : []);
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

  useEffect(() => {
    if (loading) return undefined;
    const timer = setTimeout(() => setShowContent(true), 600);
    return () => clearTimeout(timer);
  }, [loading]);

  useEffect(() => {
    document.title = "Firewall Configuration & Policy Settings | ZeroShield";
    return () => {
      document.title = "ZeroShield";
    };
  }, []);

  const handleSave = async () => {
    setIsSaving(true);
    setSaveSuccess(false);
    setError(null);
    try {
      const res = await fetchWithAuth("/api/firewall/config/", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(frontendToApi(config)),
      });
      if (res.ok) {
        const data = await res.json();
        const mapped = apiToFrontend(data);
        dispatch({ type: "REPLACE", payload: mapped });
        setSavedSnapshot(mapped);
        setConnectedModels(Array.isArray(data.connected_models) ? data.connected_models : []);
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
    dispatch({ type: "REPLACE", payload: { ...savedSnapshot } });
    setError(null);
  };

  if (loading || !showContent) {
    return (
      <div className="fw-config-page">
        <div className="-mx-3 -mt-4 border-b border-border bg-card/90 px-3 backdrop-blur-md sm:-mx-4 sm:-mt-5 sm:px-4 md:-mx-6 md:px-6 lg:-mx-8 lg:px-8">
          <div className="mx-auto max-w-5xl py-4">
            <div className="fw-shimmer-block mb-2 h-7 w-64 max-w-full rounded-md" />
            <div className="fw-shimmer-block h-4 w-96 max-w-full rounded-md" />
          </div>
        </div>
        <div className="mx-auto max-w-5xl space-y-4 pb-12 pt-5">
          {FIREWALL_SECTIONS.slice(0, 6).map((s) => (
            <SectionSkeleton key={s.id} />
          ))}
        </div>
      </div>
    );
  }

  const cardProps = { config, onPatch, isSaving };

  return (
    <div className="fw-config-page">
      <ConfigHeader
        isDirty={isDirty}
        isSaving={isSaving}
        saveSuccess={saveSuccess}
        error={error}
        updatedAt={config.updatedAt}
        onReset={handleReset}
        onSave={handleSave}
      />

      <div className="mx-auto min-w-0 max-w-5xl space-y-4 pb-12 pt-5">
        <ImpactPanel config={config} index={0} />
        <LlmProviderCard onModelsChanged={refreshConnectedModels} index={1} />
        <GeneralSettingsCard {...cardProps} index={2} />
        <RateLimitingCard {...cardProps} index={3} />
        <ContentFilteringCard {...cardProps} index={4} />
        <ModelGovernanceCard {...cardProps} connectedModels={connectedModels} index={5} />
        <PromptSecurityCard {...cardProps} index={6} />
        <ResponseGuardrailsCard {...cardProps} onGuardrailsSaved={fetchConfig} index={7} />
        <RagSecurityCard {...cardProps} index={8} />
        <ThreatIntelCard {...cardProps} index={9} />
        <AuditComplianceCard {...cardProps} index={10} />
        <AlertingCard {...cardProps} index={11} />
        <LlmConnectionsCard onModelsChanged={refreshConnectedModels} index={12} />
        <VectorDbCard index={13} />
        <ZeroShieldTestCard index={14} />

        <div className="space-y-3">
          {!config.firewallEnabled && (
            <WarningBanner
              title="Critical Warning: Firewall Disabled"
              message="All AI traffic is currently unprotected. Enable firewall immediately to restore security controls."
              tone="destructive"
            />
          )}
          {config.enforcementMode === "monitor" && (
            <WarningBanner
              title="Monitor Mode Active"
              message='Violations are logged but not blocked. Switch to "Block" mode for active protection.'
              tone="warning"
            />
          )}
          {!config.threatIntelEnabled && (
            <WarningBanner
              title="Threat Intelligence Disabled"
              message="Enable threat intelligence to leverage real-time threat feeds and automated blocking."
              tone="info"
            />
          )}
        </div>
      </div>
    </div>
  );
}

function WarningBanner({ title, message, tone }) {
  const Icon = tone === "info" ? Info : AlertCircle;
  const toneClass =
    tone === "destructive"
      ? "border-destructive/30 bg-destructive/10 text-destructive"
      : tone === "warning"
        ? "border-warn/30 bg-warn/10 text-warn-foreground"
        : "border-primary/30 bg-primary/10 text-primary";

  return (
    <div className={`flex items-start gap-3 rounded-lg border p-4 ${toneClass}`}>
      <Icon className="mt-0.5 h-5 w-5 shrink-0" aria-hidden />
      <div>
        <p className="text-sm font-semibold text-foreground">{title}</p>
        <p className="mt-1 text-xs text-muted-foreground">{message}</p>
      </div>
    </div>
  );
}
