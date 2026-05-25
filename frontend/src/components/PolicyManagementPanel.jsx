import { useState, useEffect, useCallback } from "react";
import {
  Plus, Pencil, Trash2, X, Loader2, Shield, ChevronDown, ChevronRight,
  CheckCircle, AlertTriangle, RefreshCw, Upload, Search, Filter,
} from "lucide-react";
import { useAuth } from "../context/AuthContext";
import { InfoTooltip } from "./InfoTooltip";

const SEVERITY_CONFIG = {
  CRITICAL: { bg: "bg-red-100 dark:bg-red-800/30", text: "text-red-700", border: "border-red-200 dark:border-red-800" },
  HIGH: { bg: "bg-orange-100 dark:bg-orange-800/30", text: "text-orange-700", border: "border-orange-200 dark:border-orange-800" },
  MEDIUM: { bg: "bg-yellow-100 dark:bg-yellow-800/30", text: "text-yellow-700", border: "border-yellow-200 dark:border-yellow-800" },
  LOW: { bg: "bg-green-100 dark:bg-green-800/30", text: "text-green-700", border: "border-green-200 dark:border-green-800" },
};

const ACTION_CONFIG = {
  block: { bg: "bg-red-100 dark:bg-red-800/30", text: "text-red-700" },
  redact: { bg: "bg-amber-100 dark:bg-amber-800/30", text: "text-amber-700" },
  monitor: { bg: "bg-blue-100 dark:bg-blue-800/30", text: "text-blue-700" },
};

const EMPTY_POLICY_FORM = {
  name: "",
  code: "",
  category: "",
  severity: "MEDIUM",
  description: "",
  enabled: true,
  priority: 0,
  metadata: {},
  scope: "global",
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
};

const POLICY_SCOPE_OPTIONS = [
  { value: "global", label: "Global Policies" },
  { value: "pipeline", label: "Pipeline Policies" },
  { value: "rag", label: "RAG Policies" },
  { value: "mcp", label: "MCP Policies" },
];

const POLICY_DOMAINS = POLICY_SCOPE_OPTIONS.map((option) => option.value);

function normalizePolicyScope(policy) {
  // Prefer the backend policy_domain field (authoritative source)
  const domain = String(policy?.policy_domain || "").trim().toLowerCase();
  if (["global", "pipeline", "rag", "mcp"].includes(domain)) {
    return domain;
  }

  // Legacy fallback: infer from metadata or name heuristics
  const rawScope = String(policy?.metadata?.policy_scope || "").trim().toLowerCase();
  if (rawScope === "pcm") return "mcp";
  if (["global", "pipeline", "rag", "mcp"].includes(rawScope)) {
    return rawScope;
  }

  const haystack = [policy?.name, policy?.code, policy?.category, policy?.description]
    .filter(Boolean)
    .join(" ")
    .toLowerCase();

  if (/\b(mcp|pcm|context|tool|agent)\b/.test(haystack)) return "mcp";
  if (/\b(rag|retriev|vector|knowledge|embed)\b/.test(haystack)) return "rag";
  if (/\b(pipeline|stage|workflow|route|orchestrat)\b/.test(haystack)) return "pipeline";
  return "global";
}

function buildPolicyPayload(form, enforcedScope, mcpServerId) {
  const resolvedScope = !enforcedScope || enforcedScope === "all" ? (form.scope || "global") : enforcedScope;
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

function PolicyModal({ title, form, setForm, onSubmit, onClose, submitting, error, scopeLocked = false }) {
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center">
      <div className="fixed inset-0 bg-black/40" onClick={onClose} />
      <div className="relative bg-white dark:bg-slate-800 rounded-xl shadow-xl border border-slate-200 dark:border-slate-700 w-full max-w-lg max-h-[90vh] overflow-y-auto p-6">
        <div className="flex items-center justify-between mb-4">
          <h3 className="text-base font-semibold text-slate-900 dark:text-slate-100">{title}</h3>
          <button onClick={onClose} className="p-1 hover:bg-slate-100 dark:hover:bg-slate-700 rounded transition-colors">
            <X className="w-4 h-4 text-slate-500 dark:text-slate-400" />
          </button>
        </div>
        {error && (
          <div className="mb-4 p-3 bg-red-50 dark:bg-red-900/20 border border-red-200 dark:border-red-800 rounded-lg text-xs text-red-700">
            {error}
          </div>
        )}
        <form onSubmit={onSubmit} className="space-y-4">
          <div>
            <label className="block text-xs font-medium text-slate-700 dark:text-slate-300 mb-1">Name *</label>
            <input
              type="text"
              required
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
          <div>
            <label className="block text-xs font-medium text-slate-700 dark:text-slate-300 mb-1">Policy Section</label>
            <select
              value={form.scope || "global"}
              onChange={(e) => setForm({ ...form, scope: e.target.value })}
              disabled={scopeLocked}
              className="text-slate-900 dark:text-slate-100 bg-white dark:bg-slate-800 w-full px-3 py-2 border border-slate-200 dark:border-slate-700 rounded-lg text-sm focus:ring-2 focus:ring-teal-500 disabled:bg-slate-50 dark:disabled:bg-slate-900/30 disabled:text-slate-500"
            >
              {POLICY_SCOPE_OPTIONS.map((option) => (
                <option key={option.value} value={option.value}>{option.label}</option>
              ))}
            </select>
            {scopeLocked ? (
              <p className="mt-1 text-[11px] text-slate-500 dark:text-slate-400">
                This section is fixed by the current panel. Move the policy from a different section if you need to reclassify it.
              </p>
            ) : null}
          </div>
          <div className="grid grid-cols-3 gap-3">
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
          <div className="flex items-center justify-end gap-2 pt-2">
            <button
              type="button"
              onClick={onClose}
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

function RuleModal({ title, form, setForm, onSubmit, onClose, submitting, error }) {
  const handleConditionChange = (key, value) => {
    setForm({ ...form, condition: { ...form.condition, [key]: value } });
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center">
      <div className="fixed inset-0 bg-black/40" onClick={onClose} />
      <div className="relative bg-white dark:bg-slate-800 rounded-xl shadow-xl border border-slate-200 dark:border-slate-700 w-full max-w-lg p-6 max-h-[90vh] overflow-y-auto">
        <div className="flex items-center justify-between mb-4">
          <h3 className="text-base font-semibold text-slate-900 dark:text-slate-100">{title}</h3>
          <button onClick={onClose} className="p-1 hover:bg-slate-100 dark:hover:bg-slate-700 rounded transition-colors">
            <X className="w-4 h-4 text-slate-500 dark:text-slate-400" />
          </button>
        </div>
        {error && (
          <div className="mb-4 p-3 bg-red-50 dark:bg-red-900/20 border border-red-200 dark:border-red-800 rounded-lg text-xs text-red-700">
            {error}
          </div>
        )}
        <form onSubmit={onSubmit} className="space-y-4">
          <div>
            <label className="block text-xs font-medium text-slate-700 dark:text-slate-300 mb-1">Rule Name *</label>
            <input
              type="text"
              required
              value={form.name}
              onChange={(e) => setForm({ ...form, name: e.target.value })}
              placeholder="e.g. Block SSN patterns"
              className="text-slate-900 dark:text-slate-100 bg-white dark:bg-slate-800 w-full px-3 py-2 border border-slate-200 dark:border-slate-700 rounded-lg text-sm focus:ring-2 focus:ring-teal-500 focus:border-transparent"
            />
          </div>
          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="block text-xs font-medium text-slate-700 dark:text-slate-300 mb-1">Rule Type</label>
              <select
                value={form.rule_type}
                onChange={(e) => setForm({ ...form, rule_type: e.target.value })}
                className="text-slate-900 dark:text-slate-100 bg-white dark:bg-slate-800 w-full px-3 py-2 border border-slate-200 dark:border-slate-700 rounded-lg text-sm focus:ring-2 focus:ring-teal-500"
              >
                <option value="keywords">Keywords</option>
                <option value="regex">Regex</option>
                <option value="pattern">Pattern</option>
              </select>
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
          <div>
            <label className="block text-xs font-medium text-slate-700 dark:text-slate-300 mb-1">
              {form.rule_type === "regex" ? "Regex Pattern" : "Keywords (comma-separated)"}
            </label>
            {form.rule_type === "regex" ? (
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
              onClick={onClose}
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

function RulesTable({ rules, loading, onAddRule, onEditRule, onDeleteRule }) {
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
          className="flex items-center gap-1 px-2 py-1 text-[10px] font-medium text-teal-600 hover:bg-teal-50 dark:bg-teal-900/20 rounded transition-colors"
        >
          <Plus className="w-3 h-3" />
          Add Rule
        </button>
      </div>
      {rules.length === 0 ? (
        <div className="text-center py-3 text-xs text-slate-400 bg-slate-50 dark:bg-slate-800/50 rounded-lg">
          No rules defined. Add rules to activate this policy.
        </div>
      ) : (
        <div className="border border-slate-100 dark:border-slate-700/50 rounded-lg overflow-hidden">
          <table className="w-full text-xs">
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
                    <span className={`inline-flex px-1.5 py-0.5 rounded text-[10px] font-medium ${rule.enabled ? "bg-emerald-100 dark:bg-emerald-800/30 text-emerald-700" : "bg-slate-100 dark:bg-slate-700 text-slate-500 dark:text-slate-400"}`}>
                      {rule.enabled ? "Active" : "Disabled"}
                    </span>
                  </td>
                  <td className="px-2 py-1.5 text-right">
                    <button
                      onClick={() => onEditRule(rule)}
                      className="p-1 hover:bg-slate-100 dark:hover:bg-slate-700 rounded text-slate-400 hover:text-slate-600 transition-colors mr-1"
                      title="Edit Rule"
                    >
                      <Pencil className="w-3 h-3" />
                    </button>
                    <button
                      onClick={() => onDeleteRule(rule.id)}
                      className="p-1 hover:bg-red-50 dark:hover:bg-red-900/20 rounded text-red-400 hover:text-red-600 transition-colors"
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
}) {
  const { fetchWithAuth } = useAuth();
  const [policies, setPolicies] = useState([]);
  const [loading, setLoading] = useState(true);
  const [expandedPolicy, setExpandedPolicy] = useState(null);
  const [policyRules, setPolicyRules] = useState({});
  const [rulesLoading, setRulesLoading] = useState({});
  const [createModalOpen, setCreateModalOpen] = useState(false);
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
  const [loadError, setLoadError] = useState(null);

  const fetchPolicies = useCallback(async () => {
    setLoading(true);
    setLoadError(null);
    try {
      const requestedDomains = scope === "all" ? POLICY_DOMAINS : [scope || "global"];
      const allPolicies = [];

      for (const domain of requestedDomains) {
        const params = new URLSearchParams();
        params.set("policy_domain", domain);
        if (severityFilter) params.set("severity", severityFilter);
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
  }, [fetchWithAuth, severityFilter, scope, mcpServerSlug]);

  useEffect(() => {
    fetchPolicies();
  }, [fetchPolicies]);

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
      const payload = buildPolicyPayload(policyForm, scope, mcpServerId);
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
      const payload = buildPolicyPayload(policyForm, scope, mcpServerId);
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
    });
    setEditPolicyId(policy.id);
    setFormError(null);
    setEditModalOpen(true);
  };

  const handleDeletePolicy = async (id) => {
    if (!window.confirm("Delete this policy and all its rules? This cannot be undone.")) return;
    setActionLoading(id);
    try {
      await fetchWithAuth(`/api/policies/${id}/`, { method: "DELETE" });
      await fetchPolicies();
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
    } catch (err) {
      setFormError(err.message || "Failed to update rule");
    } finally {
      setSubmitting(false);
    }
  };

  const handleDeleteRule = async (policyId, ruleId) => {
    if (!window.confirm("Delete this rule?")) return;
    try {
      await fetchWithAuth(`/api/policies/rules/${ruleId}/`, { method: "DELETE" });
      await fetchRules(policyId);
    } catch {
      // silent
    }
  };

  const openRuleModal = (policyId, rule = null) => {
    setRuleTargetPolicyId(policyId);
    setEditRuleId(rule?.id || null);
    setRuleForm(rule ? {
      name: rule.name || "",
      rule_type: rule.rule_type || "keywords",
      condition: rule.condition || { keywords: [], field: "prompt" },
      action: rule.action || "block",
      redaction_config: rule.redaction_config || {},
      priority: rule.priority || 0,
      enabled: rule.enabled !== false,
      description: rule.description || "",
    } : { ...EMPTY_RULE_FORM });
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
          setCompileStatus({ success: true, data: { message: "Compiled successfully" } });
        }
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
    <div className="bg-white dark:bg-slate-800 border border-slate-200 dark:border-slate-700 rounded-xl shadow-sm p-6">
      <div className="flex items-center justify-between mb-4">
        <div>
          <h3 className="text-base font-semibold text-slate-900 dark:text-slate-100 flex items-center">{title}<InfoTooltip title="How to Use">{infoTooltip}</InfoTooltip></h3>
          <p className="text-xs text-slate-500 dark:text-slate-400 mt-0.5">
            {description}
          </p>
        </div>
        <div className="flex items-center gap-2">
          {showCompileButton ? (
            <button
              onClick={handleCompile}
              disabled={compiling}
              className="flex items-center gap-1.5 px-3 py-2 bg-slate-100 dark:bg-slate-700 hover:bg-slate-200 text-slate-700 dark:text-slate-300 text-xs font-medium rounded-lg transition-colors disabled:opacity-50"
              title="Compile and push policies to gateway"
            >
              {compiling ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Upload className="w-3.5 h-3.5" />}
              Compile & Push
            </button>
          ) : null}
          <button
            onClick={() => {
              setPolicyForm({ ...EMPTY_POLICY_FORM, scope: scope === "all" ? "global" : scope });
              setFormError(null);
              setCreateModalOpen(true);
            }}
            className="flex items-center gap-1.5 px-3 py-2 bg-teal-600 hover:bg-teal-700 text-white text-xs font-medium rounded-lg transition-colors"
          >
            <Plus className="w-3.5 h-3.5" />
            Create Policy
          </button>
        </div>
      </div>

      {compileStatus && (
        <div className={`mb-4 p-3 rounded-lg text-xs flex items-start gap-2 ${compileStatus.success ? "bg-emerald-50 dark:bg-emerald-900/20 border border-emerald-200 dark:border-emerald-800 text-emerald-700" : "bg-red-50 dark:bg-red-900/20 border border-red-200 dark:border-red-800 text-red-700"}`}>
          {compileStatus.success ? <CheckCircle className="w-4 h-4 flex-shrink-0 mt-0.5" /> : <AlertTriangle className="w-4 h-4 flex-shrink-0 mt-0.5" />}
          <div>
            {compileStatus.success ? "Policies compiled and pushed to gateway successfully." : `Compilation failed: ${compileStatus.error}`}
            {compileStatus.data?.policy_count != null && (
              <span className="ml-1">({compileStatus.data.policy_count} policies, {compileStatus.data.rule_count || 0} rules)</span>
            )}
          </div>
          <button onClick={() => setCompileStatus(null)} className="ml-auto p-0.5 hover:bg-slate-100 dark:hover:bg-slate-700/60 rounded transition-colors">
            <X className="w-3 h-3" />
          </button>
        </div>
      )}

      {loadError ? (
        <div className="mb-4 p-3 rounded-lg text-xs flex items-start gap-2 bg-red-50 dark:bg-red-900/20 border border-red-200 dark:border-red-800 text-red-700">
          <AlertTriangle className="w-4 h-4 flex-shrink-0 mt-0.5" />
          <span>{loadError}</span>
        </div>
      ) : null}

      {showFilters ? (
        <div className="flex items-center gap-2 mb-4">
          <div className="relative flex-1">
            <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-slate-400" />
            <input
              type="text"
              value={searchTerm}
              onChange={(e) => setSearchTerm(e.target.value)}
              placeholder="Search policies..."
              className="text-slate-900 dark:text-slate-100 bg-white dark:bg-slate-800 w-full pl-8 pr-3 py-2 border border-slate-200 dark:border-slate-700 rounded-lg text-xs focus:ring-2 focus:ring-teal-500 focus:border-transparent"
            />
          </div>
          <select
            value={severityFilter}
            onChange={(e) => setSeverityFilter(e.target.value)}
            className="bg-white dark:bg-slate-800 px-3 py-2 border border-slate-200 dark:border-slate-700 rounded-lg text-xs text-slate-700 dark:text-slate-300 focus:ring-2 focus:ring-teal-500"
          >
            <option value="">All Severities</option>
            <option value="CRITICAL">Critical</option>
            <option value="HIGH">High</option>
            <option value="MEDIUM">Medium</option>
            <option value="LOW">Low</option>
          </select>
          <button onClick={fetchPolicies} className="p-2 hover:bg-slate-100 dark:hover:bg-slate-700 rounded-lg transition-colors" title="Refresh">
            <RefreshCw className="w-3.5 h-3.5 text-slate-500 dark:text-slate-400" />
          </button>
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
        <div className="space-y-2">
          {filteredPolicies.map((policy) => (
            <div key={policy.id} className="border border-slate-200 dark:border-slate-700 rounded-lg overflow-hidden">
              <div
                className="flex items-center gap-3 px-4 py-3 hover:bg-slate-50 dark:hover:bg-slate-700 cursor-pointer transition-colors"
                onClick={() => toggleExpand(policy.id)}
              >
                <button className="flex-shrink-0 text-slate-400">
                  {expandedPolicy === policy.id
                    ? <ChevronDown className="w-4 h-4" />
                    : <ChevronRight className="w-4 h-4" />}
                </button>
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2">
                    <span className="text-sm font-medium text-slate-900 dark:text-slate-100 truncate">{policy.name}</span>
                    <span className="text-[10px] font-mono text-slate-400 bg-slate-100 dark:bg-slate-700 px-1.5 py-0.5 rounded">{policy.code}</span>
                    <SeverityBadge severity={policy.severity} />
                    {!policy.enabled && (
                      <span className="inline-flex px-1.5 py-0.5 rounded text-[10px] font-medium bg-slate-100 dark:bg-slate-700 text-slate-500 dark:text-slate-400">
                        Disabled
                      </span>
                    )}
                  </div>
                  <div className="flex items-center gap-3 mt-0.5">
                    {policy.category && (
                      <span className="text-[10px] text-slate-500 dark:text-slate-400">{policy.category}</span>
                    )}
                    <span className="text-[10px] text-slate-400">
                      {policy.rule_count != null ? `${policy.rule_count} rules` : ""}
                    </span>
                    <span className="text-[10px] text-slate-400">Priority: {policy.priority}</span>
                  </div>
                </div>
                <div className="flex items-center gap-1 flex-shrink-0" onClick={(e) => e.stopPropagation()}>
                  {actionLoading === policy.id ? (
                    <Loader2 className="w-4 h-4 text-teal-500 animate-spin" />
                  ) : (
                    <>
                      <button
                        onClick={() => openEditModal(policy)}
                        className="p-1.5 hover:bg-slate-200 rounded text-slate-500 dark:text-slate-400 transition-colors"
                        title="Edit Policy"
                      >
                        <Pencil className="w-3.5 h-3.5" />
                      </button>
                      <button
                        onClick={() => handleDeletePolicy(policy.id)}
                        className="p-1.5 hover:bg-red-50 dark:hover:bg-red-900/20 rounded text-red-400 hover:text-red-600 transition-colors"
                        title="Delete Policy"
                      >
                        <Trash2 className="w-3.5 h-3.5" />
                      </button>
                    </>
                  )}
                </div>
              </div>
              {expandedPolicy === policy.id && (
                <div className="px-4 pb-3 border-t border-slate-100 dark:border-slate-700/50 bg-slate-50 dark:bg-slate-800/50/50">
                  {policy.description && (
                    <p className="text-xs text-slate-600 dark:text-slate-400 mt-2 mb-2">{policy.description}</p>
                  )}
                  <div className="flex items-center gap-4 text-[10px] text-slate-400 mb-2">
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
                  />
                </div>
              )}
            </div>
          ))}
        </div>
      )}

      {createModalOpen && (
        <PolicyModal
          title="Create Policy"
          form={policyForm}
          setForm={setPolicyForm}
          onSubmit={handleCreatePolicy}
          onClose={() => setCreateModalOpen(false)}
          submitting={submitting}
          error={formError}
          scopeLocked={scope !== "all"}
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
        />
      )}
    </div>
  );
}
