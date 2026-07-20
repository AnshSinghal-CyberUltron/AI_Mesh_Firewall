import { useState, useEffect, useCallback, useRef } from "react";
import {
  Plus, Pencil, Trash2, X, Loader2, Shield, ChevronDown, ChevronRight,
  CheckCircle, AlertTriangle, RefreshCw, Upload, Search, Filter, Activity,
} from "lucide-react";
import { useAuth } from "../context/AuthContext";
import { InfoTooltip } from "./InfoTooltip";
import { getPolicyDomainUi, resolvePolicyCreateBehavior } from "../utils/policyCreateBehavior";
import { syncModule2AfterTelemetryChange } from "../utils/crossModuleSync";
import { PolicyDomainSwitcher } from "./PolicyDomainSwitcher";

const PIPELINE_STAGE_OPTIONS = [
  { value: "", label: "All stages" },
  { value: "query", label: "Query stage" },
  { value: "retriever", label: "Retriever stage" },
  { value: "ranker", label: "Ranker stage" },
  { value: "generator", label: "Generator stage" },
];

const SEVERITY_CONFIG = {
  CRITICAL: { bg: "bg-red-100 dark:bg-red-800/30", text: "text-red-700 dark:text-red-300", border: "border-red-200 dark:border-red-800" },
  HIGH: { bg: "bg-orange-100 dark:bg-orange-800/30", text: "text-orange-700 dark:text-orange-300", border: "border-orange-200 dark:border-orange-800" },
  MEDIUM: { bg: "bg-yellow-100 dark:bg-yellow-800/30", text: "text-yellow-700 dark:text-yellow-300", border: "border-yellow-200 dark:border-yellow-800" },
  LOW: { bg: "bg-green-100 dark:bg-green-800/30", text: "text-green-700 dark:text-green-300", border: "border-green-200 dark:border-green-800" },
};

const ACTION_CONFIG = {
  block: { bg: "bg-red-100 dark:bg-red-800/30", text: "text-red-700 dark:text-red-300" },
  redact: { bg: "bg-amber-100 dark:bg-amber-800/30", text: "text-amber-700 dark:text-amber-300" },
  monitor: { bg: "bg-blue-100 dark:bg-blue-800/30", text: "text-blue-700 dark:text-blue-300" },
};

// Rule enabled/disabled badge styles as separate literals — keeps the active
// emerald tint and the disabled slate text in different strings so the
// detector's gray-on-color heuristic doesn't false-positive on the ternary.
const RULE_STATUS_ACTIVE = "bg-emerald-100 dark:bg-emerald-800/30 text-emerald-700 dark:text-emerald-300";
const RULE_STATUS_DISABLED = "bg-slate-100 dark:bg-slate-700 text-slate-500 dark:text-slate-400";

const EMPTY_POLICY_FORM = {
  name: "",
  code: "",
  category: "",
  severity: "MEDIUM",
  description: "",
  enabled: true,
  priority: 0,
  metadata: {},
  scope: "pipeline",
  // G7: comma-separated JSON-path-like field names to redact from MCP
  // tool responses when this policy matches (e.g. "ssn, email, api_key").
  // Recursive: matches keys at any nesting depth (NFKC-normalized).
  redaction_fields: "",
  // G8: actor allowlists. Empty string = wildcard (policy applies to all
  // actors in that dimension). Backend stores as ArrayField; UI keeps a
  // free-text comma-separated representation for ergonomics.
  allowed_user_ids: "",   // integers, e.g. "3, 7, 12"
  allowed_agent_ids: "",  // API key prefixes (8 chars each), e.g. "V-OwdAAx, abc12def"
  allowed_roles: "",      // role names, e.g. "admin, analyst"
};

const EMPTY_RULE_FORM = {
  name: "",
  rule_type: "keywords",
  condition: { keywords: [], field: "prompt" },
  action: "block",
  redaction_config: {},
  priority: 0,
  enabled: true,
  description: "",
  pipeline_stage: "",
  target_tool: "",
};

const POLICY_SCOPE_OPTIONS = [
  { value: "pipeline", label: "Pipeline Policies" },
  { value: "rag", label: "RAG Policies" },
  { value: "mcp", label: "MCP Policies" },
];

const POLICY_DOMAINS = POLICY_SCOPE_OPTIONS.map((option) => option.value);

function normalizePolicyScope(policy) {
  // Prefer the backend policy_domain field (authoritative source)
  const domain = String(policy?.policy_domain || "").trim().toLowerCase();
  if (domain === "global" || domain === "") {
    return "pipeline";
  }
  if (["pipeline", "rag", "mcp"].includes(domain)) {
    return domain;
  }

  // Legacy fallback: infer from metadata or name heuristics
  const rawScope = String(policy?.metadata?.policy_scope || "").trim().toLowerCase();
  if (rawScope === "pcm") return "mcp";
  if (rawScope === "global" || rawScope === "") {
    return "pipeline";
  }
  if (["pipeline", "rag", "mcp"].includes(rawScope)) {
    return rawScope;
  }

  const haystack = [policy?.name, policy?.code, policy?.category, policy?.description]
    .filter(Boolean)
    .join(" ")
    .toLowerCase();

  if (/\b(mcp|pcm|context|tool|agent)\b/.test(haystack)) return "mcp";
  if (/\b(rag|retriev|vector|knowledge|embed)\b/.test(haystack)) return "rag";
  if (/\b(pipeline|stage|workflow|route|orchestrat)\b/.test(haystack)) return "pipeline";
  return "pipeline";
}

function buildPolicyPayload(form, enforcedScope, mcpServerId, scopeLocked = false) {
  const resolvedScope = (scopeLocked && enforcedScope && enforcedScope !== "all")
    ? enforcedScope
    : (form.scope || "pipeline");
  // G7/G8 helpers: convert UI comma-strings into the array shapes the
  // backend's PostgreSQL ArrayField columns expect. Empty / whitespace
  // input must produce [] (wildcard — "applies to everyone"), NOT
  // undefined, otherwise the DRF serializer would keep the previous DB
  // value on PATCH and the admin's intent to "clear the allowlist"
  // would be silently dropped.
  const splitCSV = (s) =>
    String(s || "")
      .split(",")
      .map((x) => x.trim())
      .filter(Boolean);
  const splitCSVInts = (s) =>
    splitCSV(s)
      .map((x) => parseInt(x, 10))
      .filter((n) => Number.isInteger(n));
  const payload = {
    name: form.name,
    code: form.code,
    category: form.category,
    severity: form.severity,
    description: form.description,
    enabled: form.enabled,
    priority: form.priority,
    policy_domain: resolvedScope,
    metadata: {
      ...(form.metadata || {}),
      policy_scope: resolvedScope,
    },
    redaction_fields: splitCSV(form.redaction_fields),
    allowed_user_ids: splitCSVInts(form.allowed_user_ids),
    allowed_agent_ids: splitCSV(form.allowed_agent_ids),
    allowed_roles: splitCSV(form.allowed_roles),
    ...(form.version != null ? { version: form.version } : {}),
  };
  if (mcpServerId) payload.mcp_server = mcpServerId;
  return payload;
}

function normalizeNextUrl(nextUrl) {
  if (!nextUrl) return null;
  if (nextUrl.startsWith("http://") || nextUrl.startsWith("https://")) {
    try {
      const parsed = new URL(nextUrl);
      return `${parsed.pathname}${parsed.search}`;
    } catch {
      return null;
    }
  }
  return nextUrl;
}

async function readErrorResponse(res) {
  const text = await res.text();
  if (!text) return `HTTP ${res.status}`;
  try {
    const body = JSON.parse(text);
    return body.detail || body.policy_domain?.[0] || body.non_field_errors?.[0] || text;
  } catch {
    return text;
  }
}

function SeverityBadge({ severity }) {
  const cfg = SEVERITY_CONFIG[severity] || SEVERITY_CONFIG.MEDIUM;
  return (
    <span className={`inline-flex px-2 py-0.5 rounded-full text-[10px] font-semibold uppercase ${cfg.bg} ${cfg.text}`}>
      {severity}
    </span>
  );
}

function ActionBadge({ action }) {
  const cfg = ACTION_CONFIG[action] || ACTION_CONFIG.monitor;
  return (
    <span className={`inline-flex px-2 py-0.5 rounded-full text-[10px] font-semibold uppercase ${cfg.bg} ${cfg.text}`}>
      {action}
    </span>
  );
}

function StatCard({ icon: Icon, label, value, tone = "teal" }) {
  const toneClass = {
    teal: "bg-teal-50 text-teal-700 dark:bg-teal-900/30 dark:text-teal-200",
    violet: "bg-violet-50 text-violet-700 dark:bg-violet-900/30 dark:text-violet-200",
    amber: "bg-amber-50 text-amber-700 dark:bg-amber-900/30 dark:text-amber-200",
    rose: "bg-rose-50 text-rose-700 dark:bg-rose-900/30 dark:text-rose-200",
  }[tone];

  return (
    <div className="rounded-xl border border-slate-200/80 dark:border-slate-700 bg-white/90 dark:bg-slate-900/50 p-4 shadow-sm">
      <div className="flex items-center justify-between">
        <span className="text-[11px] font-medium uppercase tracking-wide text-slate-500 dark:text-slate-400">{label}</span>
        <span className={`inline-flex h-7 w-7 items-center justify-center rounded-lg ${toneClass}`}>
          <Icon className="h-3.5 w-3.5" />
        </span>
      </div>
      <p className="mt-2 text-2xl font-semibold text-slate-900 dark:text-slate-100">{value}</p>
    </div>
  );
}

function PolicyModal({ title, form, setForm, onSubmit, onClose, submitting, error, scopeLocked = false, onRequestVectorCreate }) {
  // B5: while a submit is in-flight, suppress backdrop-close and disable the
  // close X button so a stray click cannot discard the user's draft mid-save
  // (race could also leave a server-side write half-applied with no UI).
  const guardedClose = submitting ? () => {} : onClose;
  const domainUi = getPolicyDomainUi(form.scope || "pipeline");
  const resolvedTitle = title?.includes("Edit") ? title : domainUi.createTitle;
  useEffect(() => {
    const onKey = (e) => { if (e.key === "Escape" && !submitting) onClose(); };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [submitting, onClose]);
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center">
      <div className="fixed inset-0 bg-black/40" onClick={guardedClose} />
      <div role="dialog" aria-modal="true" aria-label={resolvedTitle} className="relative bg-white dark:bg-slate-800 rounded-xl shadow-xl border border-slate-200 dark:border-slate-700 w-full max-w-lg max-h-[90vh] overflow-y-auto p-6">
        <div className="flex items-center justify-between mb-4">
          <h3 className="text-base font-semibold text-slate-900 dark:text-slate-100">{resolvedTitle}</h3>
          <button
            onClick={guardedClose}
            disabled={submitting}
            aria-label={submitting ? "Saving… close disabled" : "Close"}
            className="p-1 hover:bg-slate-100 dark:hover:bg-slate-700 rounded transition-colors disabled:opacity-40 disabled:cursor-not-allowed"
          >
            <X className="w-4 h-4 text-slate-500 dark:text-slate-400" />
          </button>
        </div>
        {error && (
          <div className="mb-4 p-3 bg-red-50 dark:bg-red-900/20 border border-red-200 dark:border-red-800 rounded-lg text-xs text-red-700 dark:text-red-300">
            {error}
          </div>
        )}
        <div className="mb-4 rounded-lg border border-teal-200/80 dark:border-teal-800/60 bg-teal-50/60 dark:bg-teal-900/20 px-3 py-2.5">
          <p className="text-[11px] font-semibold uppercase tracking-wide text-teal-800 dark:text-teal-200">
            {scopeLocked ? `Policy section: ${domainUi.label}` : `${domainUi.label} policy`}
          </p>
          <p className="mt-1 text-xs text-slate-600 dark:text-slate-300">{domainUi.description}</p>
          {scopeLocked ? (
            <p className="mt-2 text-[11px] text-slate-500 dark:text-slate-400">
              Domain is set by the active tab. Switch tabs to create under Pipeline, RAG, MCP, or Vector.
            </p>
          ) : null}
        </div>
        <form onSubmit={onSubmit} className="space-y-4">
          <div>
            <label className="block text-xs font-medium text-slate-700 dark:text-slate-300 mb-1">Name *</label>
            <input
              type="text"
              required
              autoFocus
              value={form.name}
              onChange={(e) => setForm({ ...form, name: e.target.value })}
              placeholder="e.g. Prompt Injection Blocker"
              className="text-slate-900 dark:text-slate-100 bg-white dark:bg-slate-800 w-full px-3 py-2 border border-slate-200 dark:border-slate-700 rounded-lg text-sm focus:ring-2 focus:ring-teal-500 focus:border-transparent"
            />
          </div>
          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="block text-xs font-medium text-slate-700 dark:text-slate-300 mb-1">Code *</label>
              <input
                type="text"
                required
                value={form.code}
                onChange={(e) => setForm({ ...form, code: e.target.value })}
                placeholder="e.g. INJECTION_BLOCK"
                className="text-slate-900 dark:text-slate-100 bg-white dark:bg-slate-800 w-full px-3 py-2 border border-slate-200 dark:border-slate-700 rounded-lg text-sm focus:ring-2 focus:ring-teal-500 focus:border-transparent"
              />
            </div>
            <div>
              <label className="block text-xs font-medium text-slate-700 dark:text-slate-300 mb-1">Category</label>
              <input
                type="text"
                value={form.category}
                onChange={(e) => setForm({ ...form, category: e.target.value })}
                placeholder="e.g. Injection Prevention"
                className="text-slate-900 dark:text-slate-100 bg-white dark:bg-slate-800 w-full px-3 py-2 border border-slate-200 dark:border-slate-700 rounded-lg text-sm focus:ring-2 focus:ring-teal-500 focus:border-transparent"
              />
            </div>
          </div>
          {!scopeLocked ? (
            <div className="space-y-2">
              <PolicyDomainSwitcher
                value={form.scope || "pipeline"}
                onChange={(domain) => {
                  if (domain === "vector") {
                    // Vector is a separate backend resource (/api/vector-policies/)
                    // with its own form/model. Hand off to the dedicated Vector
                    // modal instead of morphing this generic /api/policies/ form.
                    onRequestVectorCreate?.();
                    return;
                  }
                  setForm({ ...form, scope: domain });
                }}
              />
              {form.scope === "pipeline" || form.scope === "rag" ? (
                <p className="text-[11px] text-slate-500 dark:text-slate-400">
                  {form.scope === "pipeline" ? "Pipeline" : "RAG"} policies use the baseline fields below. Target a
                  specific stage (query / retriever / ranker / generator) per-rule after the policy is created.
                </p>
              ) : null}
            </div>
          ) : null}
          <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
            <div>
              <label className="block text-xs font-medium text-slate-700 dark:text-slate-300 mb-1">Severity</label>
              <select
                value={form.severity}
                onChange={(e) => setForm({ ...form, severity: e.target.value })}
                className="text-slate-900 dark:text-slate-100 bg-white dark:bg-slate-800 w-full px-3 py-2 border border-slate-200 dark:border-slate-700 rounded-lg text-sm focus:ring-2 focus:ring-teal-500"
              >
                <option value="CRITICAL">Critical</option>
                <option value="HIGH">High</option>
                <option value="MEDIUM">Medium</option>
                <option value="LOW">Low</option>
              </select>
            </div>
            <div>
              <label className="block text-xs font-medium text-slate-700 dark:text-slate-300 mb-1">Priority</label>
              <input
                type="number"
                min="0"
                value={form.priority}
                onChange={(e) => setForm({ ...form, priority: parseInt(e.target.value, 10) || 0 })}
                className="text-slate-900 dark:text-slate-100 bg-white dark:bg-slate-800 w-full px-3 py-2 border border-slate-200 dark:border-slate-700 rounded-lg text-sm focus:ring-2 focus:ring-teal-500 focus:border-transparent"
              />
            </div>
            <div className="flex items-end pb-1">
              <label className="flex items-center gap-2 cursor-pointer">
                <input
                  type="checkbox"
                  checked={form.enabled}
                  onChange={(e) => setForm({ ...form, enabled: e.target.checked })}
                  className="text-slate-900 dark:text-slate-100 bg-white dark:bg-slate-800 w-4 h-4 rounded border-slate-300 dark:border-slate-600 text-teal-600 focus:ring-teal-500"
                />
                <span className="text-xs font-medium text-slate-700 dark:text-slate-300">Enabled</span>
              </label>
            </div>
          </div>
          <div>
            <label className="block text-xs font-medium text-slate-700 dark:text-slate-300 mb-1">Description</label>
            <textarea
              value={form.description}
              onChange={(e) => setForm({ ...form, description: e.target.value })}
              rows={3}
              placeholder="Describe what this policy does..."
              className="text-slate-900 dark:text-slate-100 bg-white dark:bg-slate-800 w-full px-3 py-2 border border-slate-200 dark:border-slate-700 rounded-lg text-sm focus:ring-2 focus:ring-teal-500 focus:border-transparent resize-none"
            />
          </div>
          {/*
            G7 + G8 advanced controls. Each input is a comma-separated
            free-text field; `buildPolicyPayload` splits them into the
            ArrayField shapes the backend expects. ``confirmClear`` (see
            below) protects against accidentally wiping a populated
            allowlist \u2014 going from a real list to an empty string flips
            the policy from "scoped to N actors" to "applies to everyone",
            which is a security-relevant widening and warrants a prompt.
          */}
          {/*
            G8 advanced actor-scope controls. MCP-only: per-user/agent/role
            allowlists are an MCP tool-call concept and would be confusing
            (and unenforced) on global/pipeline/rag policies, so the whole
            block is hidden unless the policy is in the MCP section.

            G7 response-field redaction was REMOVED here: "what to redact"
            now lives entirely inside MCP rules (action=redact, scope=key +
            key name), eliminating the previous double-entry where the same
            redaction targets were set both on the policy and on its rules.

            Each input is a comma-separated free-text field; `buildPolicyPayload`
            splits them into the ArrayField shapes the backend expects.
            ``confirmClear`` protects against accidentally wiping a populated
            allowlist \u2014 going from a real list to an empty string flips the
            policy from "scoped to N actors" to "applies to everyone".
          */}
          {(form.scope === "mcp") && (() => {
            const confirmClear = (fieldKey, label) => (next) => {
              const prev = String(form[fieldKey] || "").trim();
              const wantsClear = !String(next || "").trim();
              if (prev && wantsClear) {
                // window.confirm is intentional: this control panel is
                // already gated behind an authenticated admin session, so
                // a synchronous browser-native dialog is acceptable UX
                // and removes any chance of a stale React state racing
                // with the API call.
                const ok = window.confirm(
                  `Clearing \"${label}\" widens this policy to apply to ALL ${label.toLowerCase()}. Continue?`,
                );
                if (!ok) return;
              }
              setForm({ ...form, [fieldKey]: next });
            };
            return (
              <div className="space-y-3 pt-2 border-t border-slate-200 dark:border-slate-700">
                <p className="text-[11px] uppercase tracking-wide text-slate-500 dark:text-slate-400">
                  Advanced {'\u00b7'} Actor Scope (MCP only)
                </p>
                <p className="text-[11px] text-slate-500 dark:text-slate-400 -mt-2">
                  Restrict who this policy applies to. Leave empty to apply to everyone.
                  Configure what to redact/block in the policy's rules below.
                </p>
                <div>
                  <label className="block text-xs font-medium text-slate-700 dark:text-slate-300 mb-1">
                    Allowed user IDs (G8)
                  </label>
                  <input
                    type="text"
                    value={form.allowed_user_ids || ""}
                    onChange={(e) => confirmClear("allowed_user_ids", "Allowed user IDs")(e.target.value)}
                    placeholder="empty = all users"
                    className="text-slate-900 dark:text-slate-100 bg-white dark:bg-slate-800 w-full px-3 py-2 border border-slate-200 dark:border-slate-700 rounded-lg text-sm focus:ring-2 focus:ring-teal-500"
                  />
                </div>
                <div>
                  <label className="block text-xs font-medium text-slate-700 dark:text-slate-300 mb-1">
                    Allowed agent IDs / API key prefixes (G8)
                  </label>
                  <input
                    type="text"
                    value={form.allowed_agent_ids || ""}
                    onChange={(e) => confirmClear("allowed_agent_ids", "Allowed agent IDs")(e.target.value)}
                    placeholder="empty = all agents"
                    className="text-slate-900 dark:text-slate-100 bg-white dark:bg-slate-800 w-full px-3 py-2 border border-slate-200 dark:border-slate-700 rounded-lg text-sm focus:ring-2 focus:ring-teal-500"
                  />
                </div>
                <div>
                  <label className="block text-xs font-medium text-slate-700 dark:text-slate-300 mb-1">
                    Allowed roles (G8)
                  </label>
                  <input
                    type="text"
                    value={form.allowed_roles || ""}
                    onChange={(e) => confirmClear("allowed_roles", "Allowed roles")(e.target.value)}
                    placeholder="empty = all roles"
                    className="text-slate-900 dark:text-slate-100 bg-white dark:bg-slate-800 w-full px-3 py-2 border border-slate-200 dark:border-slate-700 rounded-lg text-sm focus:ring-2 focus:ring-teal-500"
                  />
                </div>
              </div>
            );
          })()}
          <div className="flex items-center justify-end gap-2 pt-2">
            <button
              type="button"
              onClick={guardedClose}
              className="px-4 py-2 text-sm text-slate-600 dark:text-slate-400 hover:bg-slate-100 dark:hover:bg-slate-700 rounded-lg transition-colors"
            >
              Cancel
            </button>
            <button
              type="submit"
              disabled={submitting}
              className="flex items-center gap-1.5 px-4 py-2 bg-teal-600 hover:bg-teal-700 disabled:bg-teal-400 text-white text-sm font-medium rounded-lg transition-colors"
            >
              {submitting && <Loader2 className="w-3.5 h-3.5 animate-spin" />}
              {title.includes("Edit") ? "Update Policy" : "Create Policy"}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}

function RuleModal({
  title,
  form,
  setForm,
  onSubmit,
  onClose,
  submitting,
  error,
  isMcp = false,
  isPipelineOrRag = false,
  presets = [],
}) {
  const handleConditionChange = (key, value) => {
    setForm({ ...form, condition: { ...form.condition, [key]: value } });
  };

  // MCP "match mode": preset (built-in detector) | regex | keywords.
  // Derived from the rule shape so editing an existing rule restores the
  // right control. Switching mode clears the other mode's condition keys
  // so we never send an ambiguous rule (e.g. both preset AND regex).
  const matchMode = form.condition?.preset
    ? "preset"
    : form.rule_type === "keywords"
      ? "keywords"
      : "regex";
  const setMatchMode = (mode) => {
    const nextCond = { ...(form.condition || {}) };
    delete nextCond.preset;
    delete nextCond.regex;
    delete nextCond.keywords;
    if (mode === "preset") {
      nextCond.preset = presets[0]?.key || "credit_card";
      setForm({ ...form, rule_type: "regex", condition: nextCond });
    } else if (mode === "keywords") {
      nextCond.keywords = [];
      setForm({ ...form, rule_type: "keywords", condition: nextCond });
    } else {
      nextCond.regex = "";
      setForm({ ...form, rule_type: "regex", condition: nextCond });
    }
  };

  // Mirror PolicyModal's B5 guard: while a save is in flight, the backdrop / X /
  // Cancel must NOT discard the draft (which could leave a half-applied write).
  const guardedClose = submitting ? () => {} : onClose;
  useEffect(() => {
    const onKey = (e) => { if (e.key === "Escape" && !submitting) onClose(); };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [submitting, onClose]);

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center">
      <div className="fixed inset-0 bg-black/40" onClick={guardedClose} />
      <div role="dialog" aria-modal="true" aria-label={title} className="relative bg-white dark:bg-slate-800 rounded-xl shadow-xl border border-slate-200 dark:border-slate-700 w-full max-w-lg p-6 max-h-[90vh] overflow-y-auto">
        <div className="flex items-center justify-between mb-4">
          <h3 className="text-base font-semibold text-slate-900 dark:text-slate-100">{title}</h3>
          <button onClick={guardedClose} aria-label="Close" title="Close" className="p-1 hover:bg-slate-100 dark:hover:bg-slate-700 rounded transition-colors">
            <X className="w-4 h-4 text-slate-500 dark:text-slate-400" />
          </button>
        </div>
        {error && (
          <div className="mb-4 p-3 bg-red-50 dark:bg-red-900/20 border border-red-200 dark:border-red-800 rounded-lg text-xs text-red-700 dark:text-red-300">
            {error}
          </div>
        )}
        <form onSubmit={onSubmit} className="space-y-4">
          <div>
            <label className="block text-xs font-medium text-slate-700 dark:text-slate-300 mb-1">Rule Name *</label>
            <input
              type="text"
              required
              autoFocus
              value={form.name}
              onChange={(e) => setForm({ ...form, name: e.target.value })}
              placeholder="e.g. Block SSN patterns"
              className="text-slate-900 dark:text-slate-100 bg-white dark:bg-slate-800 w-full px-3 py-2 border border-slate-200 dark:border-slate-700 rounded-lg text-sm focus:ring-2 focus:ring-teal-500 focus:border-transparent"
            />
          </div>
          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="block text-xs font-medium text-slate-700 dark:text-slate-300 mb-1">
                {isMcp ? "Match Using" : "Rule Type"}
              </label>
              {isMcp ? (
                <select
                  value={matchMode}
                  onChange={(e) => setMatchMode(e.target.value)}
                  className="text-slate-900 dark:text-slate-100 bg-white dark:bg-slate-800 w-full px-3 py-2 border border-slate-200 dark:border-slate-700 rounded-lg text-sm focus:ring-2 focus:ring-teal-500"
                >
                  <option value="preset">Built-in preset (PII / secret)</option>
                  <option value="regex">Custom regex</option>
                  <option value="keywords">Custom keywords</option>
                </select>
              ) : (
                <select
                  value={form.rule_type}
                  onChange={(e) => setForm({ ...form, rule_type: e.target.value })}
                  className="text-slate-900 dark:text-slate-100 bg-white dark:bg-slate-800 w-full px-3 py-2 border border-slate-200 dark:border-slate-700 rounded-lg text-sm focus:ring-2 focus:ring-teal-500"
                >
                  <option value="keywords">Keywords</option>
                  <option value="regex">Regex</option>
                  <option value="pattern">Pattern</option>
                </select>
              )}
            </div>
            <div>
              <label className="block text-xs font-medium text-slate-700 dark:text-slate-300 mb-1">Action</label>
              <select
                value={form.action}
                onChange={(e) => setForm({ ...form, action: e.target.value })}
                className="text-slate-900 dark:text-slate-100 bg-white dark:bg-slate-800 w-full px-3 py-2 border border-slate-200 dark:border-slate-700 rounded-lg text-sm focus:ring-2 focus:ring-teal-500"
              >
                <option value="block">Block</option>
                <option value="redact">Redact</option>
                <option value="monitor">Monitor</option>
              </select>
            </div>
          </div>
          {/* What to match against */}
          {isMcp && matchMode === "preset" ? (
            <div>
              <label className="block text-xs font-medium text-slate-700 dark:text-slate-300 mb-1">Preset</label>
              <select
                value={form.condition?.preset || ""}
                onChange={(e) => handleConditionChange("preset", e.target.value)}
                className="text-slate-900 dark:text-slate-100 bg-white dark:bg-slate-800 w-full px-3 py-2 border border-slate-200 dark:border-slate-700 rounded-lg text-sm focus:ring-2 focus:ring-teal-500"
              >
                {presets.length === 0 && <option value="">Loading presets…</option>}
                {presets.map((p) => (
                  <option key={p.key} value={p.key}>{p.label}</option>
                ))}
              </select>
              {(() => {
                const sel = presets.find((p) => p.key === form.condition?.preset);
                return sel?.description ? (
                  <p className="mt-1 text-[11px] text-slate-500 dark:text-slate-400">{sel.description}</p>
                ) : null;
              })()}
            </div>
          ) : (
            <div>
              <label className="block text-xs font-medium text-slate-700 dark:text-slate-300 mb-1">
                {(isMcp ? matchMode === "regex" : form.rule_type === "regex") ? "Regex Pattern" : "Keywords (comma-separated)"}
              </label>
              {(isMcp ? matchMode === "regex" : form.rule_type === "regex") ? (
                <input
                  type="text"
                  value={form.condition.regex || ""}
                  onChange={(e) => handleConditionChange("regex", e.target.value)}
                  placeholder="e.g. \\b\\d{3}-\\d{2}-\\d{4}\\b"
                  className="text-slate-900 dark:text-slate-100 bg-white dark:bg-slate-800 w-full px-3 py-2 border border-slate-200 dark:border-slate-700 rounded-lg text-sm font-mono focus:ring-2 focus:ring-teal-500 focus:border-transparent"
                />
              ) : (
                <input
                  type="text"
                  value={Array.isArray(form.condition.keywords) ? form.condition.keywords.join(", ") : ""}
                  onChange={(e) =>
                    handleConditionChange(
                      "keywords",
                      e.target.value.split(",").map((k) => k.trim()).filter(Boolean)
                    )
                  }
                  placeholder="e.g. ignore, override, bypass"
                  className="text-slate-900 dark:text-slate-100 bg-white dark:bg-slate-800 w-full px-3 py-2 border border-slate-200 dark:border-slate-700 rounded-lg text-sm focus:ring-2 focus:ring-teal-500 focus:border-transparent"
                />
              )}
            </div>
          )}
          {isMcp ? (
            <div>
              <label className="block text-xs font-medium text-slate-700 dark:text-slate-300 mb-1">Target MCP tool (optional)</label>
              <input
                type="text"
                value={form.target_tool || ""}
                onChange={(e) => setForm({ ...form, target_tool: e.target.value })}
                placeholder="empty = all tools (e.g. filesystem_read)"
                className="text-slate-900 dark:text-slate-100 bg-white dark:bg-slate-800 w-full px-3 py-2 border border-slate-200 dark:border-slate-700 rounded-lg text-sm focus:ring-2 focus:ring-teal-500 focus:border-transparent"
              />
            </div>
          ) : null}
          {isPipelineOrRag ? (
            <div>
              <label className="block text-xs font-medium text-slate-700 dark:text-slate-300 mb-1">Pipeline stage</label>
              <select
                value={form.pipeline_stage || ""}
                onChange={(e) => setForm({ ...form, pipeline_stage: e.target.value })}
                className="text-slate-900 dark:text-slate-100 bg-white dark:bg-slate-800 w-full px-3 py-2 border border-slate-200 dark:border-slate-700 rounded-lg text-sm focus:ring-2 focus:ring-teal-500"
              >
                {PIPELINE_STAGE_OPTIONS.map((opt) => (
                  <option key={opt.value || "all"} value={opt.value}>{opt.label}</option>
                ))}
              </select>
              <p className="mt-1 text-[11px] text-slate-500 dark:text-slate-400">
                Limit this rule to a single RAG/pipeline stage, or leave as all stages.
              </p>
            </div>
          ) : null}
          {isMcp ? (
            <div className="grid grid-cols-2 gap-3">
              <div>
                <label className="block text-xs font-medium text-slate-700 dark:text-slate-300 mb-1">Apply To</label>
                <select
                  value={form.condition?.direction || "both"}
                  onChange={(e) => handleConditionChange("direction", e.target.value)}
                  className="text-slate-900 dark:text-slate-100 bg-white dark:bg-slate-800 w-full px-3 py-2 border border-slate-200 dark:border-slate-700 rounded-lg text-sm focus:ring-2 focus:ring-teal-500"
                >
                  <option value="input">Input (tool arguments)</option>
                  <option value="output">Output (tool response)</option>
                  <option value="both">Both</option>
                </select>
              </div>
              <div>
                <label className="block text-xs font-medium text-slate-700 dark:text-slate-300 mb-1">Scope</label>
                <select
                  value={form.condition?.scope || "entire"}
                  onChange={(e) => handleConditionChange("scope", e.target.value)}
                  className="text-slate-900 dark:text-slate-100 bg-white dark:bg-slate-800 w-full px-3 py-2 border border-slate-200 dark:border-slate-700 rounded-lg text-sm focus:ring-2 focus:ring-teal-500"
                >
                  <option value="entire">Entire payload</option>
                  <option value="key">Specific key</option>
                </select>
              </div>
              {form.condition?.scope === "key" && (
                <div className="col-span-2">
                  <label className="block text-xs font-medium text-slate-700 dark:text-slate-300 mb-1">Key name</label>
                  <input
                    type="text"
                    value={form.condition?.key || ""}
                    onChange={(e) => handleConditionChange("key", e.target.value)}
                    placeholder="e.g. ssn, account_number, email"
                    className="text-slate-900 dark:text-slate-100 bg-white dark:bg-slate-800 w-full px-3 py-2 border border-slate-200 dark:border-slate-700 rounded-lg text-sm focus:ring-2 focus:ring-teal-500"
                  />
                  <p className="mt-1 text-[11px] text-slate-500 dark:text-slate-400">
                    Case-insensitive key match at any depth in the tool args / response.
                  </p>
                </div>
              )}
              <div className="col-span-2">
                <label className="block text-xs font-medium text-slate-700 dark:text-slate-300 mb-1">Priority</label>
                <input
                  type="number"
                  min="0"
                  value={form.priority}
                  onChange={(e) => setForm({ ...form, priority: parseInt(e.target.value, 10) || 0 })}
                  className="text-slate-900 dark:text-slate-100 bg-white dark:bg-slate-800 w-full px-3 py-2 border border-slate-200 dark:border-slate-700 rounded-lg text-sm focus:ring-2 focus:ring-teal-500 focus:border-transparent"
                />
              </div>
            </div>
          ) : (
            <div className="grid grid-cols-2 gap-3">
              <div>
                <label className="block text-xs font-medium text-slate-700 dark:text-slate-300 mb-1">Field</label>
                <select
                  value={form.condition.field || "prompt"}
                  onChange={(e) => handleConditionChange("field", e.target.value)}
                  className="text-slate-900 dark:text-slate-100 bg-white dark:bg-slate-800 w-full px-3 py-2 border border-slate-200 dark:border-slate-700 rounded-lg text-sm focus:ring-2 focus:ring-teal-500"
                >
                  <option value="prompt">Prompt</option>
                  <option value="response">Response</option>
                  <option value="both">Both</option>
                </select>
              </div>
              <div>
                <label className="block text-xs font-medium text-slate-700 dark:text-slate-300 mb-1">Priority</label>
                <input
                  type="number"
                  min="0"
                  value={form.priority}
                  onChange={(e) => setForm({ ...form, priority: parseInt(e.target.value, 10) || 0 })}
                  className="text-slate-900 dark:text-slate-100 bg-white dark:bg-slate-800 w-full px-3 py-2 border border-slate-200 dark:border-slate-700 rounded-lg text-sm focus:ring-2 focus:ring-teal-500 focus:border-transparent"
                />
              </div>
            </div>
          )}
          {form.action === "redact" && (
            <div>
              <label className="block text-xs font-medium text-slate-700 dark:text-slate-300 mb-1">Redaction Replacement</label>
              <input
                type="text"
                value={form.redaction_config.replacement || ""}
                onChange={(e) =>
                  setForm({
                    ...form,
                    redaction_config: { ...form.redaction_config, replacement: e.target.value },
                  })
                }
                placeholder="e.g. [REDACTED]"
                className="text-slate-900 dark:text-slate-100 bg-white dark:bg-slate-800 w-full px-3 py-2 border border-slate-200 dark:border-slate-700 rounded-lg text-sm focus:ring-2 focus:ring-teal-500 focus:border-transparent"
              />
            </div>
          )}
          <div>
            <label className="block text-xs font-medium text-slate-700 dark:text-slate-300 mb-1">Description</label>
            <textarea
              value={form.description}
              onChange={(e) => setForm({ ...form, description: e.target.value })}
              rows={2}
              placeholder="What does this rule do?"
              className="text-slate-900 dark:text-slate-100 bg-white dark:bg-slate-800 w-full px-3 py-2 border border-slate-200 dark:border-slate-700 rounded-lg text-sm focus:ring-2 focus:ring-teal-500 focus:border-transparent resize-none"
            />
          </div>
          <div className="flex items-center gap-2">
            <input
              type="checkbox"
              checked={form.enabled}
              onChange={(e) => setForm({ ...form, enabled: e.target.checked })}
              className="text-slate-900 dark:text-slate-100 bg-white dark:bg-slate-800 w-4 h-4 rounded border-slate-300 dark:border-slate-600 text-teal-600 focus:ring-teal-500"
            />
            <span className="text-xs font-medium text-slate-700 dark:text-slate-300">Rule Enabled</span>
          </div>
          <div className="flex items-center justify-end gap-2 pt-2">
            <button
              type="button"
              onClick={guardedClose}
              className="px-4 py-2 text-sm text-slate-600 dark:text-slate-400 hover:bg-slate-100 dark:hover:bg-slate-700 rounded-lg transition-colors"
            >
              Cancel
            </button>
            <button
              type="submit"
              disabled={submitting}
              className="flex items-center gap-1.5 px-4 py-2 bg-teal-600 hover:bg-teal-700 disabled:bg-teal-400 text-white text-sm font-medium rounded-lg transition-colors"
            >
              {submitting && <Loader2 className="w-3.5 h-3.5 animate-spin" />}
              {title.includes("Edit") ? "Update Rule" : "Add Rule"}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}

function EnableToggle({ checked, disabled, onChange, ariaLabel }) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      aria-label={ariaLabel}
      disabled={disabled}
      onClick={(e) => {
        e.stopPropagation();
        onChange(!checked);
      }}
      className={`relative inline-flex h-5 w-9 flex-shrink-0 items-center rounded-full transition-colors ${
        checked ? "bg-teal-600" : "bg-slate-300 dark:bg-slate-600"
      } ${disabled ? "opacity-50 cursor-not-allowed" : "cursor-pointer"}`}
    >
      <span
        className={`inline-block h-3.5 w-3.5 transform rounded-full bg-white transition-transform ${
          checked ? "translate-x-4" : "translate-x-1"
        }`}
      />
    </button>
  );
}

function RulesTable({ rules, loading, onAddRule, onEditRule, onDeleteRule, onToggleRule, ruleToggleLoadingId }) {
  if (loading) {
    return (
      <div className="flex items-center justify-center py-4">
        <Loader2 className="w-4 h-4 text-teal-500 animate-spin" />
        <span className="ml-2 text-xs text-slate-500 dark:text-slate-400">Loading rules...</span>
      </div>
    );
  }

  return (
    <div className="mt-2">
      <div className="flex items-center justify-between mb-2">
        <span className="text-xs font-medium text-slate-600 dark:text-slate-400">
          {rules.length} rule{rules.length !== 1 ? "s" : ""}
        </span>
        <button
          onClick={onAddRule}
          className="flex items-center gap-1 px-2 py-1 text-[10px] font-medium text-teal-600 dark:text-teal-400 hover:bg-teal-50 dark:bg-teal-900/20 rounded transition-colors"
        >
          <Plus className="w-3 h-3" />
          Add Rule
        </button>
      </div>
      {rules.length === 0 ? (
        <div className="text-center py-3 text-xs text-slate-500 dark:text-slate-400 bg-slate-50 dark:bg-slate-800/50 rounded-lg">
          No rules defined. Add rules to activate this policy.
        </div>
      ) : (
        <div className="border border-slate-100 dark:border-slate-700/50 rounded-lg overflow-x-auto">
          <table className="w-full min-w-[520px] text-xs">
            <thead>
              <tr className="bg-slate-50 dark:bg-slate-800/50">
                <th className="px-2 py-1.5 text-left font-semibold text-slate-500 dark:text-slate-300 uppercase">Name</th>
                <th className="px-2 py-1.5 text-left font-semibold text-slate-500 dark:text-slate-300 uppercase">Type</th>
                <th className="px-2 py-1.5 text-left font-semibold text-slate-500 dark:text-slate-300 uppercase">Action</th>
                <th className="px-2 py-1.5 text-left font-semibold text-slate-500 dark:text-slate-300 uppercase">Priority</th>
                <th className="px-2 py-1.5 text-left font-semibold text-slate-500 dark:text-slate-300 uppercase">Status</th>
                <th className="px-2 py-1.5 text-right font-semibold text-slate-500 dark:text-slate-300 uppercase w-12"></th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100 dark:divide-slate-700">
              {rules.map((rule) => (
                <tr key={rule.id} className="hover:bg-slate-50 dark:hover:bg-slate-700">
                  <td className="px-2 py-1.5 font-medium text-slate-700 dark:text-slate-300">{rule.name}</td>
                  <td className="px-2 py-1.5 text-slate-500 dark:text-slate-400 font-mono">{rule.rule_type}</td>
                  <td className="px-2 py-1.5"><ActionBadge action={rule.action} /></td>
                  <td className="px-2 py-1.5 text-slate-500 dark:text-slate-400">{rule.priority}</td>
                  <td className="px-2 py-1.5">
                    <div className="flex items-center gap-2">
                      <EnableToggle
                        checked={rule.enabled !== false}
                        disabled={ruleToggleLoadingId === rule.id}
                        ariaLabel={`${rule.enabled !== false ? "Disable" : "Enable"} rule ${rule.name}`}
                        onChange={(next) => onToggleRule?.(rule, next)}
                      />
                      <span className={`inline-flex px-1.5 py-0.5 rounded text-[10px] font-medium ${rule.enabled ? RULE_STATUS_ACTIVE : RULE_STATUS_DISABLED}`}>
                        {rule.enabled ? "Active" : "Disabled"}
                      </span>
                    </div>
                  </td>
                  <td className="px-2 py-1.5 text-right">
                    <button
                      onClick={() => onEditRule(rule)}
                      className="p-1 hover:bg-slate-100 dark:hover:bg-slate-700 rounded text-slate-400 hover:text-slate-600 transition-colors mr-1"
                      aria-label="Edit rule"
                      title="Edit Rule"
                    >
                      <Pencil className="w-3 h-3" />
                    </button>
                    <button
                      onClick={() => onDeleteRule(rule.id)}
                      className="p-1 hover:bg-red-50 dark:hover:bg-red-900/20 rounded text-red-400 hover:text-red-600 transition-colors"
                      aria-label="Delete rule"
                      title="Delete Rule"
                    >
                      <Trash2 className="w-3 h-3" />
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

export function PolicyManagementPanel({
  title = "Security Policies",
  description = "Manage enforcement policies and rules for the scanning pipeline",
  infoTooltip = "Security policies define rules for blocking, redacting, or monitoring AI traffic. Create policies with rules, then click 'Compile & Push' to activate. Rules match against prompts and responses using regex patterns.",
  scope = "all",
  showCompileButton = true,
  showFilters = true,
  emptyStateMessage,
  mcpServerSlug = null,
  mcpServerId = null,
  externalCreateSignal = 0,
  externalCreateScope = "pipeline",
  externalCreateScopeLocked = false,
  // Invoked when the user picks "Vector" in the in-panel domain switcher. Vector
  // is a separate resource, so the parent closes this generic modal and opens the
  // dedicated Vector create modal. Optional: when absent, the switcher has no
  // Vector hand-off (e.g. the MCP-server-scoped usage in MCPManagerPanel).
  onRequestVectorCreate,
}) {
  const { fetchWithAuth, user } = useAuth();
  // B3: gate the Compile & Push button on admin role. Backend already enforces
  // IsAdminOrSuperuser, but a visible button that returns 403 is misleading UX
  // and trains users to ignore errors. Match Sidebar/Header admin pattern.
  const isAdminUser =
    !!user && (user.is_superuser || (user.roles || []).includes("platform_admin"));
  const [policies, setPolicies] = useState([]);
  const [loading, setLoading] = useState(true);
  const [expandedPolicy, setExpandedPolicy] = useState(null);
  const [policyRules, setPolicyRules] = useState({});
  const [rulesLoading, setRulesLoading] = useState({});
  const [createModalOpen, setCreateModalOpen] = useState(false);
  const [createScopeLocked, setCreateScopeLocked] = useState(scope !== "all");
  const [editModalOpen, setEditModalOpen] = useState(false);
  const [ruleModalOpen, setRuleModalOpen] = useState(false);
  const [ruleTargetPolicyId, setRuleTargetPolicyId] = useState(null);
  const [editRuleId, setEditRuleId] = useState(null);
  const [policyForm, setPolicyForm] = useState({ ...EMPTY_POLICY_FORM });
  const [editPolicyId, setEditPolicyId] = useState(null);
  const [ruleForm, setRuleForm] = useState({ ...EMPTY_RULE_FORM });
  const [submitting, setSubmitting] = useState(false);
  const [formError, setFormError] = useState(null);
  const [actionLoading, setActionLoading] = useState(null);
  const [compileStatus, setCompileStatus] = useState(null);
  const [compiling, setCompiling] = useState(false);
  const [searchTerm, setSearchTerm] = useState("");
  const [severityFilter, setSeverityFilter] = useState("");
  const [enabledFilter, setEnabledFilter] = useState("");
  const [loadError, setLoadError] = useState(null);
  // Data-driven MCP preset catalogue (key/label/description). Fetched once
  // so the MCP rule builder can render the preset dropdown without
  // hard-coding the list in the client.
  const [mcpPresets, setMcpPresets] = useState([]);
  // External create triggers are monotonic signals from parent pages.
  // Guard against replay on tab/scope changes: only consume each signal once.
  const lastHandledExternalCreateSignal = useRef(0);

  const fetchPolicies = useCallback(async () => {
    setLoading(true);
    setLoadError(null);
    try {
      const normalizedScope = scope === "global" ? "pipeline" : scope;
      const requestedDomains = scope === "all" ? POLICY_DOMAINS : [normalizedScope || "pipeline"];
      const allPolicies = [];

      for (const domain of requestedDomains) {
        const params = new URLSearchParams();
        params.set("policy_domain", domain);
        if (severityFilter) params.set("severity", severityFilter);
        if (enabledFilter === "enabled") params.set("enabled", "true");
        if (enabledFilter === "disabled") params.set("enabled", "false");
        if (mcpServerSlug) params.set("mcp_server_slug", mcpServerSlug);

        let nextUrl = `/api/policies/?${params.toString()}`;
        let pageCount = 0;

        while (nextUrl && pageCount < 20) {
          const res = await fetchWithAuth(nextUrl);
          if (!res.ok) {
            throw new Error(await readErrorResponse(res));
          }
          const data = await res.json();
          if (Array.isArray(data)) {
            allPolicies.push(...data);
            nextUrl = null;
          } else {
            allPolicies.push(...(data.results || []));
            nextUrl = normalizeNextUrl(data.next || null);
          }
          pageCount += 1;
        }
      }

      // The all-domain flow can return duplicates when backend includes shared entries.
      const dedupedPolicies = Array.from(new Map(allPolicies.map((policy) => [policy.id, policy])).values());
      setPolicies(dedupedPolicies);
    } catch (err) {
      setLoadError(err.message || "Failed to load policies");
    } finally {
      setLoading(false);
    }
  }, [fetchWithAuth, severityFilter, enabledFilter, scope, mcpServerSlug]);

  useEffect(() => {
    fetchPolicies();
  }, [fetchPolicies]);

  useEffect(() => {
    if (!externalCreateSignal) return;
    if (externalCreateSignal <= lastHandledExternalCreateSignal.current) return;
    lastHandledExternalCreateSignal.current = externalCreateSignal;
    const behavior = resolvePolicyCreateBehavior(externalCreateScope || scope, "header");
    setPolicyForm({ ...EMPTY_POLICY_FORM, scope: behavior.scope });
    setCreateScopeLocked(Boolean(externalCreateScopeLocked));
    setFormError(null);
    setCreateModalOpen(true);
  }, [externalCreateSignal, externalCreateScope, externalCreateScopeLocked, scope]);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const res = await fetchWithAuth("/api/policies/mcp-presets/");
        if (!res.ok) return;
        const data = await res.json();
        if (!cancelled) setMcpPresets(Array.isArray(data?.presets) ? data.presets : []);
      } catch {
        /* non-fatal: rule builder falls back to custom regex/keywords */
      }
    })();
    return () => { cancelled = true; };
  }, [fetchWithAuth]);

  const fetchRules = useCallback(
    async (policyId) => {
      setRulesLoading((prev) => ({ ...prev, [policyId]: true }));
      try {
        let nextUrl = `/api/policies/${policyId}/rules/`;
        const allRules = [];
        let pageCount = 0;

        while (nextUrl && pageCount < 20) {
          const res = await fetchWithAuth(nextUrl);
          if (!res.ok) {
            throw new Error(await readErrorResponse(res));
          }
          const data = await res.json();
          if (Array.isArray(data)) {
            allRules.push(...data);
            nextUrl = null;
          } else {
            allRules.push(...(data.results || []));
            nextUrl = normalizeNextUrl(data.next || null);
          }
          pageCount += 1;
        }

        setPolicyRules((prev) => ({
          ...prev,
          [policyId]: allRules,
        }));
      } catch (err) {
        setFormError(err.message || "Failed to load rules");
      } finally {
        setRulesLoading((prev) => ({ ...prev, [policyId]: false }));
      }
    },
    [fetchWithAuth]
  );

  const toggleExpand = (policyId) => {
    if (expandedPolicy === policyId) {
      setExpandedPolicy(null);
    } else {
      setExpandedPolicy(policyId);
      if (!policyRules[policyId]) {
        fetchRules(policyId);
      }
    }
  };

  const handleCreatePolicy = async (e) => {
    e.preventDefault();
    setFormError(null);
    setSubmitting(true);
    try {
      const payload = buildPolicyPayload(policyForm, scope, mcpServerId, createScopeLocked);
      const res = await fetchWithAuth("/api/policies/", {
        method: "POST",
        body: JSON.stringify(payload),
      });
      if (!res.ok) {
        const text = await res.text();
        let errBody = {};
        try { errBody = JSON.parse(text); } catch {}
        throw new Error(errBody.detail || errBody.name?.[0] || errBody.code?.[0] || text || `HTTP ${res.status}`);
      }
      setCreateModalOpen(false);
      setPolicyForm({ ...EMPTY_POLICY_FORM });
      await fetchPolicies();
      syncModule2AfterTelemetryChange("policy-create");
    } catch (err) {
      setFormError(err.message || "Failed to create policy");
    } finally {
      setSubmitting(false);
    }
  };

  const handleEditPolicy = async (e) => {
    e.preventDefault();
    setFormError(null);
    setSubmitting(true);
    try {
      const payload = buildPolicyPayload(policyForm, scope, mcpServerId, scope !== "all");
      const res = await fetchWithAuth(`/api/policies/${editPolicyId}/`, {
        method: "PATCH",
        body: JSON.stringify(payload),
      });
      if (!res.ok) {
        const text = await res.text();
        let errBody = {};
        try { errBody = JSON.parse(text); } catch {}
        throw new Error(errBody.detail || text || `HTTP ${res.status}`);
      }
      setEditModalOpen(false);
      setEditPolicyId(null);
      setPolicyForm({ ...EMPTY_POLICY_FORM });
      await fetchPolicies();
      syncModule2AfterTelemetryChange("policy-edit");
    } catch (err) {
      setFormError(err.message || "Failed to update policy");
    } finally {
      setSubmitting(false);
    }
  };

  const openEditModal = (policy) => {
    setPolicyForm({
      name: policy.name,
      code: policy.code,
      category: policy.category || "",
      severity: policy.severity,
      description: policy.description || "",
      enabled: policy.enabled,
      priority: policy.priority || 0,
      metadata: policy.metadata || {},
      scope: normalizePolicyScope(policy),
      version: policy.version,
      // G7/G8: server returns arrays, UI edits as comma-separated text.
      // Use Array.isArray guard because legacy policies created before
      // migration 0029 may surface these as null on stale browser tabs.
      redaction_fields: Array.isArray(policy.redaction_fields) ? policy.redaction_fields.join(", ") : "",
      allowed_user_ids: Array.isArray(policy.allowed_user_ids) ? policy.allowed_user_ids.join(", ") : "",
      allowed_agent_ids: Array.isArray(policy.allowed_agent_ids) ? policy.allowed_agent_ids.join(", ") : "",
      allowed_roles: Array.isArray(policy.allowed_roles) ? policy.allowed_roles.join(", ") : "",
    });
    setEditPolicyId(policy.id);
    setFormError(null);
    setEditModalOpen(true);
  };

  const handleTogglePolicyEnabled = async (policy, nextEnabled) => {
    const loadingKey = `toggle-${policy.id}`;
    setActionLoading(loadingKey);
    setFormError(null);
    const previous = policy.enabled;
    setPolicies((prev) => prev.map((p) => (p.id === policy.id ? { ...p, enabled: nextEnabled } : p)));
    try {
      const res = await fetchWithAuth(`/api/policies/${policy.id}/`, {
        method: "PATCH",
        body: JSON.stringify({ enabled: nextEnabled, version: policy.version }),
      });
      if (!res.ok) {
        const text = await res.text();
        let errBody = {};
        try { errBody = JSON.parse(text); } catch {}
        throw new Error(errBody.detail || text || `HTTP ${res.status}`);
      }
      const updated = await res.json();
      setPolicies((prev) => prev.map((p) => (p.id === policy.id ? { ...p, ...updated } : p)));
      // Signals debounce compile; admins can nudge gateway sync immediately.
      fetchWithAuth("/api/policies/compile/", { method: "POST" }).catch(() => {});
      syncModule2AfterTelemetryChange("policy-toggle");
    } catch (err) {
      setPolicies((prev) => prev.map((p) => (p.id === policy.id ? { ...p, enabled: previous } : p)));
      setFormError(err.message || "Failed to update policy status");
    } finally {
      setActionLoading(null);
    }
  };

  const handleToggleRuleEnabled = async (policyId, rule, nextEnabled) => {
    setActionLoading(`rule-toggle-${rule.id}`);
    setFormError(null);
    const previous = rule.enabled !== false;
    setPolicyRules((prev) => ({
      ...prev,
      [policyId]: (prev[policyId] || []).map((r) => (r.id === rule.id ? { ...r, enabled: nextEnabled } : r)),
    }));
    try {
      const res = await fetchWithAuth(`/api/policies/rules/${rule.id}/`, {
        method: "PATCH",
        body: JSON.stringify({ enabled: nextEnabled }),
      });
      if (!res.ok) {
        const text = await res.text();
        let errBody = {};
        try { errBody = JSON.parse(text); } catch {}
        throw new Error(errBody.detail || text || `HTTP ${res.status}`);
      }
      const updated = await res.json();
      setPolicyRules((prev) => ({
        ...prev,
        [policyId]: (prev[policyId] || []).map((r) => (r.id === rule.id ? { ...r, ...updated } : r)),
      }));
      fetchWithAuth("/api/policies/compile/", { method: "POST" }).catch(() => {});
      syncModule2AfterTelemetryChange("policy-rule-toggle");
    } catch (err) {
      setPolicyRules((prev) => ({
        ...prev,
        [policyId]: (prev[policyId] || []).map((r) => (r.id === rule.id ? { ...r, enabled: previous } : r)),
      }));
      setFormError(err.message || "Failed to update rule status");
    } finally {
      setActionLoading(null);
    }
  };

  const handleDeletePolicy = async (id) => {
    if (!window.confirm("Delete this policy and all its rules? This cannot be undone.")) return;
    setActionLoading(id);
    setFormError(null);
    try {
      // B2: previously the response status was never inspected; a 403/404/500
      // returned here would silently "succeed" and the UI would refetch as if
      // the policy were gone. Surface the failure so the user can retry.
      const res = await fetchWithAuth(`/api/policies/${id}/`, { method: "DELETE" });
      if (!res.ok) {
        let detail = `HTTP ${res.status}`;
        try {
          const text = await res.text();
          if (text) {
            try {
              const body = JSON.parse(text);
              detail = body.detail || body.error || text;
            } catch {
              detail = text;
            }
          }
        } catch {
          /* ignore body read failure */
        }
        throw new Error(detail);
      }
      await fetchPolicies();
      syncModule2AfterTelemetryChange("policy-delete");
    } catch (err) {
      setFormError(err.message || "Failed to delete policy");
    } finally {
      setActionLoading(null);
    }
  };

  const handleCreateRule = async (e) => {
    e.preventDefault();
    setFormError(null);
    setSubmitting(true);
    try {
      const res = await fetchWithAuth(`/api/policies/${ruleTargetPolicyId}/rules/`, {
        method: "POST",
        body: JSON.stringify(ruleForm),
      });
      if (!res.ok) {
        const text = await res.text();
        let errBody = {};
        try { errBody = JSON.parse(text); } catch {}
        throw new Error(errBody.detail || errBody.name?.[0] || text || `HTTP ${res.status}`);
      }
      setRuleModalOpen(false);
      setEditRuleId(null);
      setRuleForm({ ...EMPTY_RULE_FORM });
      await fetchRules(ruleTargetPolicyId);
      syncModule2AfterTelemetryChange("policy-rule-create");
    } catch (err) {
      setFormError(err.message || "Failed to create rule");
    } finally {
      setSubmitting(false);
    }
  };

  const handleEditRule = async (e) => {
    e.preventDefault();
    setFormError(null);
    setSubmitting(true);
    try {
      const res = await fetchWithAuth(`/api/policies/rules/${editRuleId}/`, {
        method: "PATCH",
        body: JSON.stringify(ruleForm),
      });
      if (!res.ok) {
        const text = await res.text();
        let errBody = {};
        try { errBody = JSON.parse(text); } catch {}
        throw new Error(errBody.detail || errBody.name?.[0] || text || `HTTP ${res.status}`);
      }
      setRuleModalOpen(false);
      setEditRuleId(null);
      setRuleForm({ ...EMPTY_RULE_FORM });
      await fetchRules(ruleTargetPolicyId);
      syncModule2AfterTelemetryChange("policy-rule-edit");
    } catch (err) {
      setFormError(err.message || "Failed to update rule");
    } finally {
      setSubmitting(false);
    }
  };

  const handleDeleteRule = async (policyId, ruleId) => {
    if (!window.confirm("Delete this rule?")) return;
    setFormError(null);
    try {
      // B1: previously this swallowed all errors silently — a failed DELETE
      // (auth expiry, optimistic-lock conflict, server error) left the rule
      // visible and the user assumed success. Now we check res.ok and surface
      // the failure via setFormError so the user can retry.
      const res = await fetchWithAuth(`/api/policies/rules/${ruleId}/`, { method: "DELETE" });
      if (!res.ok) {
        let detail = `HTTP ${res.status}`;
        try {
          const text = await res.text();
          if (text) {
            try {
              const body = JSON.parse(text);
              detail = body.detail || body.error || text;
            } catch {
              detail = text;
            }
          }
        } catch {
          /* ignore body read failure */
        }
        throw new Error(detail);
      }
      await fetchRules(policyId);
      syncModule2AfterTelemetryChange("policy-rule-delete");
    } catch (err) {
      setFormError(err.message || "Failed to delete rule");
    }
  };

  const openRuleModal = (policyId, rule = null) => {
    setRuleTargetPolicyId(policyId);
    setEditRuleId(rule?.id || null);
    const targetPolicy = policies.find((p) => p.id === policyId);
    const targetIsMcp = targetPolicy ? normalizePolicyScope(targetPolicy) === "mcp" : false;
    setRuleForm(rule ? {
      name: rule.name || "",
      rule_type: rule.rule_type || "keywords",
      condition: rule.condition || { keywords: [], field: "prompt" },
      action: rule.action || "block",
      redaction_config: rule.redaction_config || {},
      priority: rule.priority || 0,
      enabled: rule.enabled !== false,
      description: rule.description || "",
      pipeline_stage: rule.pipeline_stage || "",
      target_tool: rule.target_tool || "",
    } : (targetIsMcp ? {
      // New MCP rule defaults: built-in preset, redact, both directions,
      // whole-payload scope — the most common "protect PII everywhere" case.
      name: "",
      rule_type: "regex",
      condition: { preset: mcpPresets[0]?.key || "credit_card", direction: "both", scope: "entire" },
      action: "redact",
      redaction_config: {},
      priority: 0,
      enabled: true,
      description: "",
      pipeline_stage: "",
      target_tool: "",
    } : { ...EMPTY_RULE_FORM }));
    setFormError(null);
    setRuleModalOpen(true);
  };

  const handleCompile = async () => {
    setCompiling(true);
    setCompileStatus(null);
    try {
      const res = await fetchWithAuth("/api/policies/compile/", { method: "POST" });
      if (res.ok) {
        const statusRes = await fetchWithAuth("/api/policies/compile/status/");
        if (statusRes.ok) {
          const data = await statusRes.json();
          setCompileStatus({ success: true, data });
        } else {
          // B4: do not lie. The compile POST succeeded but we could not
          // confirm the resulting Redis state. Mark as partial so the UI can
          // render a warning state instead of a green "Compiled successfully".
          setCompileStatus({
            success: true,
            partial: true,
            data: {
              message:
                "Compile triggered; status fetch failed — verify via /api/policies/compile/status/ or redis-cli.",
            },
          });
        }
        syncModule2AfterTelemetryChange("policy-compile");
      } else {
        const text = await res.text();
        setCompileStatus({ success: false, error: text || "Compilation failed" });
      }
    } catch (err) {
      setCompileStatus({ success: false, error: err.message });
    } finally {
      setCompiling(false);
    }
  };

  const filteredPolicies = policies.filter((p) => {
    const policyScope = normalizePolicyScope(p);
    if (scope !== "all" && policyScope !== scope) {
      return false;
    }
    if (searchTerm) {
      const term = searchTerm.toLowerCase();
      return (
        p.name.toLowerCase().includes(term) ||
        p.code.toLowerCase().includes(term) ||
        (p.category || "").toLowerCase().includes(term)
      );
    }
    return true;
  });

  const totalPolicies = policies.length;
  const enabledPolicies = policies.filter((policy) => policy.enabled !== false).length;
  const criticalPolicies = policies.filter((policy) => policy.severity === "CRITICAL").length;
  // True total across ALL policies from the backend's per-policy rule_count,
  // refined by the live count for any policy the user has expanded (whose rules
  // are actually loaded). Summing only policyRules — which is populated lazily on
  // expand — made this headline KPI read 0 until a policy was opened.
  const totalRules = policies.reduce(
    (sum, p) => sum + (Array.isArray(policyRules[p.id]) ? policyRules[p.id].length : (p.rule_count || 0)),
    0,
  );

  const resolvedEmptyStateMessage = emptyStateMessage || (
    scope === "all"
      ? "No policies created. Create one to define enforcement rules."
      : "No policies in this section yet. Create one to start managing this policy family."
  );

  const formatDate = (iso) => {
    if (!iso) return "--";
    return new Date(iso).toISOString().substring(0, 16).replace("T", " ");
  };

  return (
    <div className="rounded-2xl border border-slate-200/80 dark:border-slate-700 bg-gradient-to-br from-white via-white to-slate-50 dark:from-slate-900 dark:via-slate-900 dark:to-slate-800 shadow-sm p-6">
      <div className="flex flex-col gap-4 lg:flex-row lg:items-start lg:justify-between mb-5">
        <div>
          <h3 className="text-lg font-semibold text-slate-900 dark:text-slate-100 flex items-center gap-2">
            <Shield className="h-5 w-5 text-teal-600 dark:text-teal-400" />
            {title}
            <InfoTooltip title="How to Use">{infoTooltip}</InfoTooltip>
          </h3>
          <p className="text-sm text-slate-500 dark:text-slate-400 mt-1 max-w-2xl">
            {description}
          </p>
        </div>
        <div className="flex items-center gap-2">
          {showCompileButton && isAdminUser ? (
            <button
              onClick={handleCompile}
              disabled={compiling}
              className="inline-flex items-center gap-2 px-4 py-2 bg-white dark:bg-slate-800 border border-slate-200 dark:border-slate-700 hover:bg-slate-50 dark:hover:bg-slate-700 text-slate-700 dark:text-slate-200 text-xs font-medium rounded-xl transition-colors disabled:opacity-50"
              title="Compile and push policies to gateway"
            >
              {compiling ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Upload className="w-3.5 h-3.5" />}
              Compile & Push
            </button>
          ) : null}
          <button
            onClick={() => {
              const behavior = resolvePolicyCreateBehavior(scope, "panel");
              setPolicyForm({ ...EMPTY_POLICY_FORM, scope: behavior.scope });
              setCreateScopeLocked(scope !== "all");
              setFormError(null);
              setCreateModalOpen(true);
            }}
            className="inline-flex items-center gap-2 px-4 py-2 bg-teal-600 hover:bg-teal-700 text-white text-xs font-semibold rounded-xl transition-colors shadow-sm"
          >
            <Plus className="w-3.5 h-3.5" />
            Create Policy
          </button>
        </div>
      </div>

      <div className="grid grid-cols-2 lg:grid-cols-4 gap-3 mb-5">
        <StatCard icon={Shield} label="Total Policies" value={totalPolicies} tone="teal" />
        <StatCard icon={CheckCircle} label="Enabled Policies" value={enabledPolicies} tone="violet" />
        <StatCard icon={AlertTriangle} label="Critical Severity" value={criticalPolicies} tone="rose" />
        <StatCard icon={Activity} label="Total Rules" value={totalRules} tone="amber" />
      </div>

      {compileStatus && (
        // B4: distinguish success / partial (compile fired but status fetch
        // failed) / error. Without the partial path the UI showed a green
        // "Policies compiled" banner even when we had no proof the gateway
        // cache had been updated.
        <div
          className={`mb-4 p-3 rounded-lg text-xs flex items-start gap-2 ${
            compileStatus.success && !compileStatus.partial
              ? "bg-emerald-50 dark:bg-emerald-900/20 border border-emerald-200 dark:border-emerald-800 text-emerald-700 dark:text-emerald-300"
              : compileStatus.partial
              ? "bg-amber-50 dark:bg-amber-900/20 border border-amber-200 dark:border-amber-800 text-amber-700 dark:text-amber-300"
              : "bg-red-50 dark:bg-red-900/20 border border-red-200 dark:border-red-800 text-red-700 dark:text-red-300"
          }`}
        >
          {compileStatus.success && !compileStatus.partial ? (
            <CheckCircle className="w-4 h-4 flex-shrink-0 mt-0.5" />
          ) : (
            <AlertTriangle className="w-4 h-4 flex-shrink-0 mt-0.5" />
          )}
          <div>
            {compileStatus.success && !compileStatus.partial
              ? "Policies compiled and pushed to gateway successfully."
              : compileStatus.partial
              ? compileStatus.data?.message || "Compile triggered; status unknown."
              : `Compilation failed: ${compileStatus.error}`}
            {compileStatus.data?.policy_count != null && (
              <span className="ml-1">({compileStatus.data.policy_count} policies, {compileStatus.data.rule_count || 0} rules)</span>
            )}
          </div>
          <button onClick={() => setCompileStatus(null)} aria-label="Dismiss compile status" title="Dismiss" className="ml-auto p-0.5 hover:bg-slate-100 dark:hover:bg-slate-700/60 rounded transition-colors">
            <X className="w-3 h-3" />
          </button>
        </div>
      )}

      {loadError ? (
        <div className="mb-4 p-3 rounded-lg text-xs flex items-start gap-2 bg-red-50 dark:bg-red-900/20 border border-red-200 dark:border-red-800 text-red-700 dark:text-red-300">
          <AlertTriangle className="w-4 h-4 flex-shrink-0 mt-0.5" />
          <span>{loadError}</span>
        </div>
      ) : null}

      {showFilters ? (
        <div className="rounded-xl border border-slate-200 dark:border-slate-700 bg-white/80 dark:bg-slate-900/40 p-3 mb-4">
          <div className="flex items-center gap-2 mb-2">
            <Filter className="w-3.5 h-3.5 text-slate-500 dark:text-slate-400" />
            <span className="text-xs font-semibold tracking-wide uppercase text-slate-500 dark:text-slate-400">
              Filters
            </span>
          </div>
          <div className="flex flex-col md:flex-row md:items-center gap-2">
          <div className="relative flex-1">
            <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-slate-400" />
            <input
              type="text"
              value={searchTerm}
              onChange={(e) => setSearchTerm(e.target.value)}
              placeholder="Search policies..."
              className="text-slate-900 dark:text-slate-100 bg-white dark:bg-slate-800 w-full pl-8 pr-3 py-2.5 border border-slate-200 dark:border-slate-700 rounded-xl text-xs focus:ring-2 focus:ring-teal-500 focus:border-transparent"
            />
          </div>
          <select
            value={enabledFilter}
            onChange={(e) => setEnabledFilter(e.target.value)}
            aria-label="Filter policies by status"
            className="bg-white dark:bg-slate-800 px-3 py-2.5 border border-slate-200 dark:border-slate-700 rounded-xl text-xs text-slate-700 dark:text-slate-300 focus:ring-2 focus:ring-teal-500"
          >
            <option value="">All statuses</option>
            <option value="enabled">Enabled only</option>
            <option value="disabled">Disabled only</option>
          </select>
          <select
            value={severityFilter}
            onChange={(e) => setSeverityFilter(e.target.value)}
            aria-label="Filter policies by severity"
            className="bg-white dark:bg-slate-800 px-3 py-2.5 border border-slate-200 dark:border-slate-700 rounded-xl text-xs text-slate-700 dark:text-slate-300 focus:ring-2 focus:ring-teal-500"
          >
            <option value="">All Severities</option>
            <option value="CRITICAL">Critical</option>
            <option value="HIGH">High</option>
            <option value="MEDIUM">Medium</option>
            <option value="LOW">Low</option>
          </select>
          <button onClick={fetchPolicies} className="inline-flex items-center justify-center p-2.5 hover:bg-slate-100 dark:hover:bg-slate-700 rounded-xl transition-colors border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-800" aria-label="Refresh" title="Refresh">
            <RefreshCw className="w-3.5 h-3.5 text-slate-500 dark:text-slate-400" />
          </button>
          </div>
        </div>
      ) : null}

      {loading ? (
        <div className="flex items-center justify-center py-8">
          <Loader2 className="w-5 h-5 text-teal-500 animate-spin" />
          <span className="ml-2 text-sm text-slate-500 dark:text-slate-400">Loading policies...</span>
        </div>
      ) : filteredPolicies.length === 0 ? (
        <div className="text-center py-8 text-sm text-slate-500 dark:text-slate-400">
          {policies.length === 0
            ? resolvedEmptyStateMessage
            : "No policies match your search criteria."}
        </div>
      ) : (
        <div className="space-y-3">
          {filteredPolicies.map((policy) => (
            <div key={policy.id} className="border border-slate-200/90 dark:border-slate-700 rounded-xl overflow-hidden bg-white dark:bg-slate-900/30 shadow-sm">
              <div
                className="flex items-center gap-3 px-4 py-3.5 hover:bg-slate-50 dark:hover:bg-slate-800 cursor-pointer transition-colors"
                onClick={() => toggleExpand(policy.id)}
              >
                <button
                  className="flex-shrink-0 text-slate-400"
                  aria-label={expandedPolicy === policy.id ? "Collapse policy" : "Expand policy"}
                  title={expandedPolicy === policy.id ? "Collapse policy" : "Expand policy"}
                >
                  {expandedPolicy === policy.id
                    ? <ChevronDown className="w-4 h-4" />
                    : <ChevronRight className="w-4 h-4" />}
                </button>
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2">
                    <span className="text-sm font-semibold text-slate-900 dark:text-slate-100 truncate">{policy.name}</span>
                    <span className="text-[10px] font-mono text-slate-500 dark:text-slate-300 bg-slate-100 dark:bg-slate-700 px-2 py-0.5 rounded-md">{policy.code}</span>
                    <SeverityBadge severity={policy.severity} />
                    {!policy.enabled && (
                      <span className="inline-flex px-1.5 py-0.5 rounded text-[10px] font-medium bg-slate-100 dark:bg-slate-700 text-slate-500 dark:text-slate-400">
                        Disabled
                      </span>
                    )}
                  </div>
                  <div className="flex items-center flex-wrap gap-2.5 mt-1">
                    {policy.category && (
                      <span className="text-[10px] text-slate-500 dark:text-slate-400 bg-slate-50 dark:bg-slate-800 border border-slate-200 dark:border-slate-700 rounded-md px-1.5 py-0.5">{policy.category}</span>
                    )}
                    <span className="text-[10px] text-slate-500 dark:text-slate-400">
                      {policy.rule_count != null ? `${policy.rule_count} rules` : ""}
                    </span>
                    <span className="text-[10px] text-slate-500 dark:text-slate-400">Priority: {policy.priority}</span>
                  </div>
                </div>
                <div className="flex items-center gap-2 flex-shrink-0" onClick={(e) => e.stopPropagation()}>
                  <EnableToggle
                    checked={policy.enabled !== false}
                    disabled={actionLoading === `toggle-${policy.id}`}
                    ariaLabel={`${policy.enabled ? "Disable" : "Enable"} policy ${policy.name}`}
                    onChange={(next) => handleTogglePolicyEnabled(policy, next)}
                  />
                  {policy.is_system ? (
                    <span className="text-[10px] font-medium uppercase tracking-wide text-violet-600 dark:text-violet-300">
                      System
                    </span>
                  ) : null}
                  {actionLoading === policy.id || actionLoading === `toggle-${policy.id}` ? (
                    <Loader2 className="w-4 h-4 text-teal-500 animate-spin" />
                  ) : (
                    <>
                      <button
                        onClick={() => openEditModal(policy)}
                        className="p-1.5 hover:bg-slate-200 dark:hover:bg-slate-700 rounded-lg text-slate-500 dark:text-slate-300 transition-colors"
                        aria-label="Edit policy"
                        title="Edit Policy"
                      >
                        <Pencil className="w-3.5 h-3.5" />
                      </button>
                      {!policy.is_system ? (
                        <button
                          onClick={() => handleDeletePolicy(policy.id)}
                          className="p-1.5 hover:bg-red-50 dark:hover:bg-red-900/20 rounded text-red-400 hover:text-red-600 transition-colors"
                          aria-label="Delete policy"
                          title="Delete Policy"
                        >
                          <Trash2 className="w-3.5 h-3.5" />
                        </button>
                      ) : null}
                    </>
                  )}
                </div>
              </div>
              {expandedPolicy === policy.id && (
                <div className="px-4 pb-3 border-t border-slate-100 dark:border-slate-700/50 bg-slate-50/80 dark:bg-slate-800/40">
                  {policy.description && (
                    <p className="text-xs text-slate-600 dark:text-slate-400 mt-2 mb-2">{policy.description}</p>
                  )}
                  <div className="flex flex-wrap items-center gap-4 text-[10px] text-slate-500 dark:text-slate-400 mb-2">
                    <span>Version: {policy.version}</span>
                    <span>Created: {formatDate(policy.created_at)}</span>
                    <span>Updated: {formatDate(policy.updated_at)}</span>
                  </div>
                  <RulesTable
                    rules={policyRules[policy.id] || []}
                    loading={rulesLoading[policy.id]}
                    onAddRule={() => openRuleModal(policy.id)}
                    onEditRule={(rule) => openRuleModal(policy.id, rule)}
                    onDeleteRule={(ruleId) => handleDeleteRule(policy.id, ruleId)}
                    onToggleRule={(rule, next) => handleToggleRuleEnabled(policy.id, rule, next)}
                    ruleToggleLoadingId={String(actionLoading || "").startsWith("rule-toggle-") ? Number(String(actionLoading).replace("rule-toggle-", "")) : null}
                  />
                </div>
              )}
            </div>
          ))}
        </div>
      )}

      {createModalOpen && (
        <PolicyModal
          title={getPolicyDomainUi(policyForm.scope || scope).createTitle}
          form={policyForm}
          setForm={setPolicyForm}
          onSubmit={handleCreatePolicy}
          onClose={() => {
            setCreateModalOpen(false);
            setCreateScopeLocked(scope !== "all");
          }}
          submitting={submitting}
          error={formError}
          scopeLocked={createScopeLocked}
          onRequestVectorCreate={
            onRequestVectorCreate
              ? () => {
                  // Close this generic modal and let the parent open the Vector
                  // create modal (separate resource at /api/vector-policies/).
                  setCreateModalOpen(false);
                  setCreateScopeLocked(scope !== "all");
                  onRequestVectorCreate();
                }
              : undefined
          }
        />
      )}

      {editModalOpen && (
        <PolicyModal
          title="Edit Policy"
          form={policyForm}
          setForm={setPolicyForm}
          onSubmit={handleEditPolicy}
          onClose={() => { setEditModalOpen(false); setEditPolicyId(null); }}
          submitting={submitting}
          error={formError}
          scopeLocked={scope !== "all"}
        />
      )}

      {ruleModalOpen && (
        <RuleModal
          title={editRuleId ? "Edit Rule" : "Add Rule"}
          form={ruleForm}
          setForm={setRuleForm}
          onSubmit={editRuleId ? handleEditRule : handleCreateRule}
          onClose={() => { setRuleModalOpen(false); setEditRuleId(null); }}
          submitting={submitting}
          error={formError}
          isMcp={(() => {
            const tp = policies.find((p) => p.id === ruleTargetPolicyId);
            return tp ? normalizePolicyScope(tp) === "mcp" : false;
          })()}
          isPipelineOrRag={(() => {
            const tp = policies.find((p) => p.id === ruleTargetPolicyId);
            const domain = tp ? normalizePolicyScope(tp) : "";
            return domain === "pipeline" || domain === "rag";
          })()}
          presets={mcpPresets}
        />
      )}
    </div>
  );
}
