import { useState, useEffect, useCallback } from "react";
import {
  Shield, ShieldCheck, Fingerprint, Key, FileWarning, Brain,
  ScrollText, Loader2, Save, RotateCcw, AlertTriangle, CheckCircle2,
} from "lucide-react";
import { useAuth } from "../context/AuthContext";
import { InfoTooltip } from "./InfoTooltip";

// §1.7 Generator-Level Output Guardrails — full per-detector + per-action control.
// Self-contained: owns its GET/PUT against the shared /api/firewall/config/ singleton
// so it can be mounted in BOTH the §1.7 card surface and the Firewall Config page.

const ACTION_OPTIONS = [
  { value: "block", label: "Block", hint: "Reject the response (HTTP 403) — nothing is delivered." },
  { value: "redact", label: "Redact", hint: "Mask the offending spans, deliver the sanitized text." },
  { value: "rewrite", label: "Rewrite", hint: "Replace the response with a canned safe message." },
  { value: "flag", label: "Flag", hint: "Deliver as-is but mark for review / log an incident." },
  { value: "allow", label: "Allow", hint: "Take no action (monitoring only)." },
];

// Detector descriptors. `enableKey` toggles the detector; `actionKey` chooses the
// action. Hallucination reuses the existing factuality_check_enabled flag and
// exposes the grounding threshold slider.
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

// Managed keys — only these are sent in the (partial) PUT.
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

function Toggle({ checked, disabled, onChange, label }) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      aria-label={label}
      disabled={disabled}
      onClick={() => onChange(!checked)}
      className={`relative inline-flex h-5 w-9 shrink-0 items-center rounded-full transition-colors focus:outline-none focus-visible:ring-2 focus-visible:ring-indigo-500 ${
        disabled ? "opacity-40 cursor-not-allowed" : "cursor-pointer"
      } ${checked ? "bg-indigo-600" : "bg-slate-300 dark:bg-slate-600"}`}
    >
      <span
        className={`inline-block h-3.5 w-3.5 transform rounded-full bg-white transition-transform ${
          checked ? "translate-x-4.5" : "translate-x-1"
        }`}
        style={{ transform: checked ? "translateX(18px)" : "translateX(3px)" }}
      />
    </button>
  );
}

export function OutputGuardrailControls({ onSaved, className = "" }) {
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

  const dirty =
    state && serverState &&
    MANAGED_KEYS.some((k) => state[k] !== serverState[k]);

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
      <div className={`flex items-center gap-2 p-6 text-sm text-slate-500 dark:text-slate-400 ${className}`}>
        <Loader2 className="w-4 h-4 animate-spin" />
        Loading output guardrail controls…
      </div>
    );
  }

  if (!state) {
    return (
      <div className={`p-6 text-sm text-rose-600 dark:text-rose-400 ${className}`}>
        {error || "Output guardrail controls unavailable."}
      </div>
    );
  }

  return (
    <div className={`rounded-2xl border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-900/40 ${className}`}>
      {/* Header + master switch */}
      <div className="flex items-start justify-between gap-3 p-4 border-b border-slate-200 dark:border-slate-700">
        <div className="flex items-start gap-3">
          <div className="p-2 rounded-xl bg-indigo-100 dark:bg-indigo-900/40">
            <Shield className="w-5 h-5 text-indigo-600 dark:text-indigo-400" />
          </div>
          <div>
            <h3 className="text-sm font-semibold text-slate-800 dark:text-slate-100 flex items-center gap-1.5">
              Output Guardrail Controls
              <InfoTooltip text="Per-detector and per-action control for generator-level output guardrails. Changes apply org-wide to every model request." />
            </h3>
            <p className="text-xs text-slate-500 dark:text-slate-400 mt-0.5">
              Choose what each detector does to unsafe model output.
            </p>
          </div>
        </div>
        <div className="flex items-center gap-2 pt-0.5">
          <span className="text-xs font-medium text-slate-600 dark:text-slate-300">
            {state.response_filtering_enabled ? "Enabled" : "Disabled"}
          </span>
          <Toggle
            checked={state.response_filtering_enabled}
            onChange={(v) => setField("response_filtering_enabled", v)}
            label="Master output guardrail switch"
          />
        </div>
      </div>

      {masterOff && (
        <div className="flex items-center gap-2 px-4 py-2 text-xs text-amber-700 dark:text-amber-300 bg-amber-50 dark:bg-amber-900/20 border-b border-amber-200 dark:border-amber-800">
          <AlertTriangle className="w-3.5 h-3.5" />
          Output guardrails are off — per-detector settings are saved but not enforced until you enable the master switch.
        </div>
      )}

      {/* Detector rows */}
      <div className="divide-y divide-slate-100 dark:divide-slate-800">
        {DETECTORS.map((d) => {
          const Icon = d.icon;
          const enabled = state[d.enableKey];
          const rowDisabled = masterOff;
          const actionDisabled = rowDisabled || !enabled;
          return (
            <div key={d.id} className="p-4">
              <div className="flex items-center justify-between gap-3 flex-wrap">
                <div className="flex items-start gap-3 min-w-0">
                  <div className="p-1.5 rounded-lg bg-slate-100 dark:bg-slate-800">
                    <Icon className="w-4 h-4 text-slate-500 dark:text-slate-400" />
                  </div>
                  <div className="min-w-0">
                    <div className="text-sm font-medium text-slate-700 dark:text-slate-200">{d.label}</div>
                    <p className="text-[11px] text-slate-400 dark:text-slate-500 mt-0.5">{d.description}</p>
                  </div>
                </div>
                <div className="flex items-center gap-3">
                  <div className="flex items-center gap-1.5">
                    <span className="text-[11px] text-slate-500 dark:text-slate-400">Detect</span>
                    <Toggle
                      checked={enabled}
                      disabled={rowDisabled}
                      onChange={(v) => setField(d.enableKey, v)}
                      label={`Enable ${d.label} detection`}
                    />
                  </div>
                  <select
                    value={state[d.actionKey]}
                    disabled={actionDisabled}
                    onChange={(e) => setField(d.actionKey, e.target.value)}
                    aria-label={`${d.label} action`}
                    className={`text-xs rounded-lg border px-2 py-1.5 bg-white dark:bg-slate-800 border-slate-300 dark:border-slate-600 text-slate-700 dark:text-slate-200 focus:outline-none focus-visible:ring-2 focus-visible:ring-indigo-500 ${
                      actionDisabled ? "opacity-40 cursor-not-allowed" : "cursor-pointer"
                    }`}
                  >
                    {ACTION_OPTIONS.map((o) => (
                      <option key={o.value} value={o.value}>{o.label}</option>
                    ))}
                  </select>
                  <InfoTooltip
                    text={ACTION_OPTIONS.find((o) => o.value === state[d.actionKey])?.hint || ""}
                  />
                </div>
              </div>

              {/* Hallucination grounding threshold */}
              {d.hasThreshold && enabled && !rowDisabled && (
                <div className="mt-3 ml-10 flex items-center gap-3">
                  <span className="text-[11px] text-slate-500 dark:text-slate-400 whitespace-nowrap">
                    Grounding threshold
                  </span>
                  <input
                    type="range"
                    min={0}
                    max={1}
                    step={0.05}
                    value={state.hallucination_grounding_threshold}
                    onChange={(e) => setField("hallucination_grounding_threshold", parseFloat(e.target.value))}
                    aria-label="Hallucination grounding threshold"
                    className="flex-1 max-w-xs accent-indigo-600"
                  />
                  <span className="text-xs font-medium tabular-nums text-slate-600 dark:text-slate-300 w-10 text-right">
                    {state.hallucination_grounding_threshold.toFixed(2)}
                  </span>
                  <InfoTooltip text="Minimum grounding score below which a response is treated as a hallucination. Lower = stricter." />
                </div>
              )}
            </div>
          );
        })}
      </div>

      {/* Incident logging */}
      <div className="flex items-center justify-between gap-3 p-4 border-t border-slate-200 dark:border-slate-700">
        <div className="flex items-start gap-3">
          <div className="p-1.5 rounded-lg bg-slate-100 dark:bg-slate-800">
            <ShieldCheck className="w-4 h-4 text-slate-500 dark:text-slate-400" />
          </div>
          <div>
            <div className="text-sm font-medium text-slate-700 dark:text-slate-200">Security Incident Logging</div>
            <p className="text-[11px] text-slate-400 dark:text-slate-500 mt-0.5">
              Record redact / rewrite / flag actions as security incidents. Hard blocks are always logged.
            </p>
          </div>
        </div>
        <Toggle
          checked={state.output_incident_logging_enabled}
          onChange={(v) => setField("output_incident_logging_enabled", v)}
          label="Security incident logging"
        />
      </div>

      {/* Footer: status + actions */}
      <div className="flex items-center justify-between gap-3 p-4 border-t border-slate-200 dark:border-slate-700">
        <div className="min-w-0 text-xs">
          {error && (
            <span className="flex items-center gap-1.5 text-rose-600 dark:text-rose-400">
              <AlertTriangle className="w-3.5 h-3.5 shrink-0" />
              <span className="truncate" title={error}>{error}</span>
            </span>
          )}
          {success && !error && (
            <span className="flex items-center gap-1.5 text-emerald-600 dark:text-emerald-400">
              <CheckCircle2 className="w-3.5 h-3.5" />
              Guardrail configuration saved.
            </span>
          )}
        </div>
        <div className="flex items-center gap-2 shrink-0">
          <button
            type="button"
            onClick={handleReset}
            disabled={!dirty || saving}
            className={`inline-flex items-center gap-1.5 text-xs font-medium px-3 py-1.5 rounded-lg border border-slate-300 dark:border-slate-600 text-slate-600 dark:text-slate-300 ${
              !dirty || saving ? "opacity-40 cursor-not-allowed" : "hover:bg-slate-50 dark:hover:bg-slate-800"
            }`}
          >
            <RotateCcw className="w-3.5 h-3.5" />
            Reset
          </button>
          <button
            type="button"
            onClick={handleSave}
            disabled={!dirty || saving}
            className={`inline-flex items-center gap-1.5 text-xs font-semibold px-3.5 py-1.5 rounded-lg text-white ${
              !dirty || saving ? "bg-indigo-400 cursor-not-allowed" : "bg-indigo-600 hover:bg-indigo-700"
            }`}
          >
            {saving ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Save className="w-3.5 h-3.5" />}
            {saving ? "Saving…" : "Save guardrails"}
          </button>
        </div>
      </div>
    </div>
  );
}

export default OutputGuardrailControls;
