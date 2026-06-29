import { useState, useEffect, useCallback } from "react";
import { motion } from "motion/react";
import {
  Shield,
  ShieldCheck,
  Fingerprint,
  Key,
  FileWarning,
  Brain,
  ScrollText,
  Loader2,
  RotateCcw,
  AlertTriangle,
  CheckCircle2,
} from "lucide-react";
import { useAuth } from "../context/AuthContext";
import { InfoTooltip } from "./InfoTooltip";
import { ConfigSwitch } from "./firewall/primitives/ConfigSwitch";
import { ThresholdSlider } from "./firewall/primitives/ThresholdSlider";
import { Button } from "./ui/Button";
import { cn } from "../lib/utils";

const ACTION_OPTIONS = [
  { value: "block", label: "Block", hint: "Reject the response (HTTP 403) — nothing is delivered." },
  { value: "redact", label: "Redact", hint: "Mask the offending spans, deliver the sanitized text." },
  { value: "rewrite", label: "Rewrite", hint: "Re-generate a corrected response via the model, re-scanned before delivery; falls back to a safe message if the rewrite is unavailable or still unsafe." },
  { value: "flag", label: "Flag", hint: "Deliver as-is but mark for review / log an incident." },
  { value: "allow", label: "Allow", hint: "Take no action (monitoring only)." },
];

const DETECTORS = [
  {
    id: "pii",
    label: "PII / PD Leakage",
    icon: Fingerprint,
    description: "SSNs, emails, phone numbers, addresses",
    enableKey: "output_pii_enabled",
    actionKey: "output_pii_action",
    defaultEnabled: true,
    defaultAction: "redact",
  },
  {
    id: "credential",
    label: "Credential Exposure",
    icon: Key,
    description: "API keys, tokens, passwords, secrets",
    enableKey: "output_credential_enabled",
    actionKey: "output_credential_action",
    defaultEnabled: true,
    defaultAction: "block",
  },
  {
    id: "ip_leakage",
    label: "IP Leakage",
    icon: FileWarning,
    description: "Proprietary data, trade secrets, internal URLs",
    enableKey: "output_ip_leakage_enabled",
    actionKey: "output_ip_leakage_action",
    defaultEnabled: true,
    defaultAction: "flag",
  },
  {
    id: "policy",
    label: "Policy Violations",
    icon: ScrollText,
    description: "Org policy-engine verdicts enforced on the output path",
    enableKey: "output_policy_enabled",
    actionKey: "output_policy_action",
    defaultEnabled: true,
    defaultAction: "block",
  },
  {
    id: "hallucination",
    label: "Hallucination Risk",
    icon: Brain,
    description: "Factuality / grounding check against retrieved context",
    enableKey: "factuality_check_enabled",
    actionKey: "output_hallucination_action",
    defaultEnabled: true,
    defaultAction: "flag",
    hasThreshold: true,
  },
];

const MANAGED_KEYS = [
  "response_filtering_enabled",
  "output_incident_logging_enabled",
  "hallucination_grounding_threshold",
  ...DETECTORS.map((d) => d.enableKey),
  ...DETECTORS.map((d) => d.actionKey),
];

function buildLocalState(cfg) {
  const next = {
    response_filtering_enabled: cfg.response_filtering_enabled ?? true,
    output_incident_logging_enabled: cfg.output_incident_logging_enabled ?? true,
    hallucination_grounding_threshold:
      typeof cfg.hallucination_grounding_threshold === "number"
        ? cfg.hallucination_grounding_threshold
        : 0.2,
  };
  for (const d of DETECTORS) {
    next[d.enableKey] = cfg[d.enableKey] ?? d.defaultEnabled;
    next[d.actionKey] = cfg[d.actionKey] ?? d.defaultAction;
  }
  return next;
}

function ActionPillGroup({ detectorId, value, onChange, disabled }) {
  return (
    <div
      className={cn(
        "inline-flex max-w-full flex-wrap rounded-md border border-border bg-background p-0.5",
        disabled && "pointer-events-none opacity-40",
      )}
    >
      {ACTION_OPTIONS.map((option) => {
        const active = value === option.value;
        return (
          <motion.button
            key={option.value}
            type="button"
            title={option.hint}
            whileTap={disabled ? undefined : { scale: 0.95 }}
            disabled={disabled}
            onClick={() => onChange(option.value)}
            className={cn(
              "relative rounded px-2.5 py-1 text-xs font-medium font-mono transition-colors",
              "!transform-none hover:!transform-none",
              active ? "text-primary-foreground" : "text-muted-foreground hover:text-foreground",
            )}
          >
            {active && (
              <motion.span
                layoutId={`guard-action-${detectorId}`}
                transition={{ type: "spring", stiffness: 400, damping: 30 }}
                className="absolute inset-0 rounded bg-primary"
              />
            )}
            <span className="relative">{option.label}</span>
          </motion.button>
        );
      })}
    </div>
  );
}

export function OutputGuardrailControls({ onSaved, embedded = false, className = "" }) {
  const { fetchWithAuth } = useAuth();
  const [state, setState] = useState(null);
  const [serverState, setServerState] = useState(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState(null);
  const [success, setSuccess] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await fetchWithAuth("/api/firewall/config/");
      if (res.ok) {
        const data = await res.json();
        const local = buildLocalState(data);
        setState(local);
        setServerState(local);
      } else {
        setError("Failed to load guardrail configuration.");
      }
    } catch {
      setError("Network error loading guardrail configuration.");
    } finally {
      setLoading(false);
    }
  }, [fetchWithAuth]);

  useEffect(() => {
    load();
  }, [load]);

  const setField = (key, value) => {
    setState((prev) => ({ ...prev, [key]: value }));
    setSuccess(false);
  };

  const dirty = state && serverState && MANAGED_KEYS.some((k) => state[k] !== serverState[k]);
  const masterOff = state ? !state.response_filtering_enabled : false;

  const handleSave = async () => {
    if (!state) return;
    setSaving(true);
    setError(null);
    setSuccess(false);
    try {
      const payload = {};
      for (const k of MANAGED_KEYS) payload[k] = state[k];
      const res = await fetchWithAuth("/api/firewall/config/", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      if (res.ok) {
        const data = await res.json();
        const local = buildLocalState(data);
        setState(local);
        setServerState(local);
        setSuccess(true);
        setTimeout(() => setSuccess(false), 4000);
        if (typeof onSaved === "function") onSaved(data);
      } else if (res.status === 400) {
        const body = await res.json().catch(() => null);
        const detail = body
          ? Object.entries(body)
              .map(([k, v]) => `${k}: ${Array.isArray(v) ? v.join(", ") : v}`)
              .join("; ")
          : "Invalid configuration — a compliance floor may be blocking this change.";
        setError(detail);
      } else if (res.status === 403) {
        setError("You do not have permission to change guardrail configuration.");
      } else {
        setError("Failed to save guardrail configuration.");
      }
    } catch {
      setError("Network error saving guardrail configuration.");
    } finally {
      setSaving(false);
    }
  };

  const handleReset = () => {
    if (serverState) setState({ ...serverState });
    setError(null);
    setSuccess(false);
  };

  if (loading) {
    return (
      <div className={cn("flex items-center gap-2 p-4 text-sm text-muted-foreground", className)}>
        <Loader2 className="h-4 w-4 animate-spin" />
        Loading output guardrail controls…
      </div>
    );
  }

  if (!state) {
    return (
      <div className={cn("p-4 text-sm text-destructive", className)}>
        {error || "Output guardrail controls unavailable."}
      </div>
    );
  }

  const rootClass = cn(
    embedded
      ? ""
      : "rounded-xl border border-border bg-card shadow-elegant",
    className,
  );

  return (
    <div className={rootClass}>
      <div
        className={cn(
          "flex flex-wrap items-start justify-between gap-3",
          embedded ? "border-b border-border/60 pb-3" : "border-b border-border p-4",
        )}
      >
        <div className="flex items-start gap-3">
          {!embedded && (
            <div className="rounded-lg bg-accent p-2 text-accent-foreground ring-1 ring-border/60">
              <Shield className="h-5 w-5" />
            </div>
          )}
          <div>
            <h3 className="flex items-center gap-1.5 text-sm font-semibold text-foreground">
              Output Guardrail Controls
              <InfoTooltip text="Per-detector and per-action control for generator-level output guardrails. Changes apply org-wide to every model request." />
            </h3>
            <p className="mt-0.5 text-xs text-muted-foreground">
              Choose what each detector does to unsafe model output.
            </p>
          </div>
        </div>
        <div className="flex items-center gap-2 pt-0.5">
          <span className="text-xs font-medium text-muted-foreground">Enabled</span>
          <ConfigSwitch
            checked={state.response_filtering_enabled}
            onCheckedChange={(v) => setField("response_filtering_enabled", v)}
            label="Master output guardrail switch"
          />
        </div>
      </div>

      {masterOff && (
        <div className="flex items-center gap-2 border-b border-warn/30 bg-warn/10 px-3 py-2 text-xs text-warn-foreground">
          <AlertTriangle className="h-3.5 w-3.5 shrink-0" />
          Output guardrails are off — per-detector settings are saved but not enforced until you enable the master switch.
        </div>
      )}

      <div className={cn("divide-y divide-border/60", !embedded && "px-0")}>
        {DETECTORS.map((d) => {
          const Icon = d.icon;
          const enabled = state[d.enableKey];
          const rowDisabled = masterOff;
          const actionDisabled = rowDisabled || !enabled;
          return (
            <div
              key={d.id}
              className={cn(
                "grid grid-cols-1 gap-3 py-3 md:grid-cols-[1fr_auto_auto] md:items-center",
                embedded ? "" : "px-4",
              )}
            >
              <div className="flex min-w-0 items-start gap-3">
                <div className="rounded-lg bg-muted p-1.5">
                  <Icon className="h-4 w-4 text-muted-foreground" />
                </div>
                <div className="min-w-0">
                  <p className="text-sm font-medium text-foreground">{d.label}</p>
                  <p className="mt-0.5 text-xs text-muted-foreground">{d.description}</p>
                </div>
              </div>
              <div className="flex items-center gap-2 text-xs">
                <span className="text-muted-foreground">Detect</span>
                <ConfigSwitch
                  checked={enabled}
                  disabled={rowDisabled}
                  onCheckedChange={(v) => setField(d.enableKey, v)}
                  label={`Enable ${d.label} detection`}
                />
              </div>
              <ActionPillGroup
                detectorId={d.id}
                value={state[d.actionKey]}
                disabled={actionDisabled}
                onChange={(v) => setField(d.actionKey, v)}
              />

              {d.hasThreshold && enabled && !rowDisabled && (
                <div className="md:col-span-3">
                  <div className="rounded-md border border-border bg-background/60 px-3 py-2">
                    <p className="mb-2 text-xs text-muted-foreground">Grounding threshold</p>
                    <ThresholdSlider
                      value={state.hallucination_grounding_threshold}
                      onChange={(v) => setField("hallucination_grounding_threshold", v)}
                      format={(v) => v.toFixed(2)}
                    />
                  </div>
                </div>
              )}
            </div>
          );
        })}
      </div>

      <div
        className={cn(
          "flex flex-wrap items-center justify-between gap-3 border-t border-border/60 py-3",
          embedded ? "" : "px-4",
        )}
      >
        <div className="flex min-w-0 flex-1 items-start gap-3">
          <div className="rounded-lg bg-muted p-1.5">
            <ShieldCheck className="h-4 w-4 text-muted-foreground" />
          </div>
          <div>
            <p className="text-sm font-medium text-foreground">Security Incident Logging</p>
            <p className="mt-0.5 text-xs text-muted-foreground">
              Record redact / rewrite / flag actions as security incidents. Hard blocks are always logged.
            </p>
          </div>
        </div>
        <ConfigSwitch
          checked={state.output_incident_logging_enabled}
          onCheckedChange={(v) => setField("output_incident_logging_enabled", v)}
          label="Security incident logging"
        />
      </div>

      <div
        className={cn(
          "flex flex-wrap items-center justify-between gap-3 border-t border-border/60 pt-3",
          embedded ? "" : "px-4 pb-4",
        )}
      >
        <div className="min-w-0 text-xs">
          {error && (
            <span className="flex items-center gap-1.5 text-destructive">
              <AlertTriangle className="h-3.5 w-3.5 shrink-0" />
              <span className="truncate" title={error}>
                {error}
              </span>
            </span>
          )}
          {success && !error && (
            <span className="flex items-center gap-1.5 text-success">
              <CheckCircle2 className="h-3.5 w-3.5" />
              Guardrail configuration saved.
            </span>
          )}
        </div>
        <div className="flex shrink-0 items-center gap-2">
          <Button
            type="button"
            variant="ghost"
            size="sm"
            onClick={handleReset}
            disabled={!dirty || saving}
            className="text-foreground hover:bg-accent hover:text-accent-foreground"
          >
            <RotateCcw className="h-3.5 w-3.5" />
            Reset
          </Button>
          <Button
            type="button"
            size="sm"
            onClick={handleSave}
            disabled={!dirty || saving}
            className="bg-primary text-primary-foreground shadow-glow hover:opacity-90"
          >
            {saving ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : null}
            {saving ? "Saving…" : "Save guardrails"}
          </Button>
        </div>
      </div>
    </div>
  );
}

export default OutputGuardrailControls;
