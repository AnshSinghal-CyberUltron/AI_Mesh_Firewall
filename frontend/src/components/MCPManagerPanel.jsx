/**
 * MCPManagerPanel — Backend MCP lifecycle management.
 *
 * Provides:
 *  - Server connection management (connect/disconnect, status)
 *  - Tool enable/disable toggles with compliance tags
 *  - Per-tool policy CRUD (deny / allow / redact)
 *  - Audit log viewer
 *
 * Talks to the Django backend at /api/mcp/* (authenticated via JWT).
 */

import { useState, useEffect, useCallback, useRef } from "react";
import {
  Server,
  Shield,
  AlertTriangle,
  CheckCircle,
  Loader2,
  ChevronDown,
  ChevronRight,
  RefreshCw,
  Power,
  PowerOff,
  ToggleLeft,
  ToggleRight,
  Plus,
  Trash2,
  X,
  Eye,
  FileText,
  Clock,
  Tag,
  Lock,
  Copy,
  Link,
  ExternalLink,
} from "lucide-react";
import { useAuth } from "../context/AuthContext";
import { startVisibleInterval } from "../utils/visiblePoll.js";
import { InfoTooltip } from "./InfoTooltip";

/* ────────────────── helpers ────────────────── */

const STATUS_STYLE = {
  connected:    { bg: "bg-emerald-100 dark:bg-emerald-800/30", text: "text-emerald-700 dark:text-emerald-300", label: "Connected" },
  connecting:   { bg: "bg-blue-100 dark:bg-blue-800/30", text: "text-blue-700 dark:text-blue-300", label: "Connecting…" },
  disconnected: { bg: "bg-slate-100 dark:bg-slate-700", text: "text-slate-600 dark:text-slate-400", label: "Disconnected" },
  error:        { bg: "bg-red-100 dark:bg-red-800/30", text: "text-red-700 dark:text-red-300", label: "Error" },
};

function ServerStatusBadge({ status }) {
  const s = STATUS_STYLE[status] || STATUS_STYLE.disconnected;
  return (
    <span className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-medium ${s.bg} ${s.text}`}>
      {status === "connecting" && <Loader2 className="w-3 h-3 animate-spin" />}
      {status === "connected" && <CheckCircle className="w-3 h-3" />}
      {status === "error" && <AlertTriangle className="w-3 h-3" />}
      {s.label}
    </span>
  );
}

function ComplianceTag({ tag }) {
  const color = {
    pii: "bg-red-100 dark:bg-red-800/30 text-red-700 dark:text-red-300",
    ip: "bg-orange-100 dark:bg-orange-800/30 text-orange-700 dark:text-orange-300",
    regulated: "bg-amber-100 dark:bg-amber-800/30 text-amber-700 dark:text-amber-300",
    api_key: "bg-violet-100 dark:bg-violet-800/30 text-violet-700 dark:text-violet-300",
  }[tag] || "bg-slate-100 dark:bg-slate-700 text-slate-600 dark:text-slate-300";
  return (
    <span className={`inline-flex items-center gap-0.5 px-1.5 py-0.5 rounded text-[10px] font-medium uppercase ${color}`}>
      <Tag className="w-2.5 h-2.5" />
      {tag}
    </span>
  );
}

const TABS = [
  { id: "connections", label: "Connections", icon: Server },
  { id: "tools", label: "Tools", icon: ToggleRight },
  { id: "policies", label: "Policies", icon: Shield },
  { id: "activity", label: "Activity & Incidents", icon: AlertTriangle },
  { id: "audit", label: "Audit Log", icon: FileText },
];

const FOCUSABLE_SELECTOR = [
  "button:not([disabled])",
  "a[href]",
  "input:not([disabled])",
  "select:not([disabled])",
  "textarea:not([disabled])",
  '[tabindex]:not([tabindex="-1"])',
].join(",");

function useModalA11y(isOpen, onClose) {
  const modalRef = useRef(null);

  useEffect(() => {
    if (!isOpen) return;

    const previousFocus = document.activeElement;
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";

    const focusFirstElement = () => {
      const modalNode = modalRef.current;
      if (!modalNode) return;
      const focusable = Array.from(modalNode.querySelectorAll(FOCUSABLE_SELECTOR));
      if (focusable.length > 0) {
        focusable[0].focus();
      } else {
        modalNode.focus();
      }
    };

    focusFirstElement();

    const handleKeyDown = (event) => {
      if (event.key === "Escape") {
        event.preventDefault();
        onClose();
        return;
      }

      if (event.key !== "Tab") return;

      const modalNode = modalRef.current;
      if (!modalNode) return;

      const focusable = Array.from(modalNode.querySelectorAll(FOCUSABLE_SELECTOR));
      if (focusable.length === 0) {
        event.preventDefault();
        return;
      }

      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      const active = document.activeElement;

      if (!event.shiftKey && active === last) {
        event.preventDefault();
        first.focus();
      } else if (event.shiftKey && active === first) {
        event.preventDefault();
        last.focus();
      }
    };

    document.addEventListener("keydown", handleKeyDown);

    return () => {
      document.removeEventListener("keydown", handleKeyDown);
      document.body.style.overflow = previousOverflow;
      if (previousFocus && typeof previousFocus.focus === "function") {
        previousFocus.focus();
      }
    };
  }, [isOpen, onClose]);

  return modalRef;
}

/* ════════════════════════════════════════════════════════════════ */
export function MCPManagerPanel() {
  const { fetchWithAuth } = useAuth();

  const [tab, setTab] = useState("connections");
  const [servers, setServers] = useState([]);
  const [tools, setTools] = useState([]);
  const [policies, setPolicies] = useState([]);
  const [auditLogs, setAuditLogs] = useState([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const [enforcementStatus, setEnforcementStatus] = useState(null);

  /* ── server for which tools/policies are shown ── */
  const [selectedServerId, setSelectedServerId] = useState(null);
  const selectedServer = servers.find((s) => s.id === selectedServerId);

  /* ── add server modal ── */
  const [addServerOpen, setAddServerOpen] = useState(false);
  const addServerModalRef = useModalA11y(addServerOpen, () => setAddServerOpen(false));
  const [addServerForm, setAddServerForm] = useState({
    name: "", description: "", transport: "streamable_http",
    url: "", command: "", args: "",
  });
  const [addServerSaving, setAddServerSaving] = useState(false);

  /* ── policy create modal ── */
  const [policyModalOpen, setPolicyModalOpen] = useState(false);
  const policyModalRef = useModalA11y(policyModalOpen, () => setPolicyModalOpen(false));
  const [policyForm, setPolicyForm] = useState({
    name: "",
    rule_type: "tool_control",
    tool_id: "",
    action: "deny",
    priority: 100,
    is_active: true,
    // argument_constraint fields
    argument_rules: [{ param: "", operator: "no_injection", value: "", message: "" }],
    // context_constraint fields
    context_allowed_roles: "",
    context_denied_roles: "",
    context_hours_start: "",
    context_hours_end: "",
    // rate_limit fields
    rate_limit_window: "60",
    rate_limit_max_calls: "60",
    // risk_policy fields
    risk_threshold: "5.0",
    risk_action_critical: "deny",
    risk_action_high: "deny",
    risk_action_medium: "require_confirmation",
    risk_action_low: "allow",
    // legacy fields
    redact_fields: "",
    max_tokens: "",
  });
  const [saving, setSaving] = useState(false);

  /* ── expanded audit row ── */
  const [expandedAudit, setExpandedAudit] = useState(null);

  /* ── activity & incidents ── */
  const [activityEvents, setActivityEvents] = useState([]);
  const [incidents, setIncidents] = useState([]);

  /* ── audit filters ── */
  const [auditActionFilter, setAuditActionFilter] = useState("");
  const [auditToolFilter, setAuditToolFilter] = useState("");
  const [copiedGateway, setCopiedGateway] = useState(null);

  /* ────────── data loaders ────────── */

  const loadServers = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await fetchWithAuth("/api/mcp/servers/");
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      setServers(data.results ?? data);
    } catch (e) {
      setError(`Failed to load servers: ${e.message}`);
    } finally {
      setLoading(false);
    }
  }, [fetchWithAuth]);

  const loadEnforcementStatus = useCallback(async () => {
    try {
      const res = await fetchWithAuth("/api/mcp/enforcement-status/");
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      setEnforcementStatus(data);
    } catch {
      setEnforcementStatus(null);
    }
  }, [fetchWithAuth]);

  const loadToolsForServer = useCallback(
    async (serverId) => {
      if (!serverId) return;
      try {
        const res = await fetchWithAuth(`/api/mcp/servers/${serverId}/tools/`);
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const data = await res.json();
        setTools(data.results ?? data);
      } catch (e) {
        setError(`Failed to load tools: ${e.message}`);
      }
    },
    [fetchWithAuth],
  );

  const loadPolicies = useCallback(
    async (serverId) => {
      try {
        const url = serverId
          ? `/api/mcp/policies/?server=${serverId}`
          : "/api/mcp/policies/";
        const res = await fetchWithAuth(url);
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const data = await res.json();
        setPolicies(data.results ?? data);
      } catch (e) {
        setError(`Failed to load policies: ${e.message}`);
      }
    },
    [fetchWithAuth],
  );

  const loadAuditLogs = useCallback(
    async (serverId, extraFilters = {}) => {
      try {
        const params = new URLSearchParams();
        if (serverId) params.set("server_id", serverId);
        if (extraFilters.action) params.set("action", extraFilters.action);
        if (extraFilters.tool_name) params.set("tool_name", extraFilters.tool_name);
        const qs = params.toString();
        const url = `/api/mcp/audit-logs/${qs ? `?${qs}` : ""}`;
        const res = await fetchWithAuth(url);
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const data = await res.json();
        setAuditLogs(data.results ?? data);
      } catch (e) {
        setError(`Failed to load audit logs: ${e.message}`);
      }
    },
    [fetchWithAuth],
  );

  const loadActivityEvents = useCallback(async () => {
    try {
      const res = await fetchWithAuth("/api/security/threat-feed/?source=mcp_scan&limit=100");
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      setActivityEvents(data.results ?? data ?? []);
    } catch {
      setActivityEvents([]);
    }
  }, [fetchWithAuth]);

  const loadIncidents = useCallback(async () => {
    try {
      const res = await fetchWithAuth("/api/security/incidents/");
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      setIncidents(data.results ?? data ?? []);
    } catch {
      setIncidents([]);
    }
  }, [fetchWithAuth]);

  /* ── initial & tab-switched loads ── */
  useEffect(() => {
    loadServers();
    loadEnforcementStatus();
  }, [loadServers, loadEnforcementStatus]);

  useEffect(() => {
    if (tab === "tools" && selectedServerId) loadToolsForServer(selectedServerId);
    if (tab === "policies") loadPolicies(selectedServerId);
    if (tab === "activity") { loadActivityEvents(); loadIncidents(); }
    if (tab === "audit") loadAuditLogs(selectedServerId, { action: auditActionFilter, tool_name: auditToolFilter });
  }, [tab, selectedServerId, loadToolsForServer, loadPolicies, loadAuditLogs, loadActivityEvents, loadIncidents, auditActionFilter, auditToolFilter]);

  /* ────────── risk helpers ────────── */

  const RISK_STYLES = {
    critical: { bg: "bg-red-100 dark:bg-red-800/30", text: "text-red-700 dark:text-red-300" },
    high:     { bg: "bg-orange-100 dark:bg-orange-800/30", text: "text-orange-700 dark:text-orange-300" },
    medium:   { bg: "bg-amber-100 dark:bg-amber-800/30", text: "text-amber-700 dark:text-amber-300" },
    low:      { bg: "bg-blue-100 dark:bg-blue-800/30", text: "text-blue-700 dark:text-blue-300" },
    minimal:  { bg: "bg-slate-100 dark:bg-slate-700", text: "text-slate-600 dark:text-slate-300" },
  };

  const getRiskExplanation = (tool) => {
    const perms = tool.declared_permissions || [];
    const tags = tool.compliance_tags || [];
    const parts = [];
    if (perms.includes("exec")) parts.push("Can execute arbitrary commands");
    if (perms.includes("credential")) parts.push("Accesses credentials/secrets");
    if (perms.includes("filesystem")) parts.push("Accesses filesystem");
    if (perms.includes("database")) parts.push("Queries databases");
    if (perms.includes("network")) parts.push("Makes network requests");
    if (tags.includes("pii")) parts.push("Handles PII data");
    if (tags.includes("regulated")) parts.push("Touches regulated data");
    if (parts.length === 0) parts.push("No elevated permissions detected");
    return parts.join(". ") + ".";
  };

  const getRiskRecommendation = (tool) => {
    const cat = tool.risk_category || "minimal";
    if (cat === "critical") return "Disable unless absolutely required. Create deny policy.";
    if (cat === "high") return "Review permissions carefully. Apply redaction policy.";
    if (cat === "medium") return "Monitor usage. Consider parameter constraints.";
    return "Safe for general use.";
  };

  /* ────────── actions ────────── */

  const addServer = async () => {
    setAddServerSaving(true);
    setError(null);
    try {
      const f = addServerForm;
      const payload = { name: f.name, description: f.description, transport: f.transport };
      if (f.transport === "stdio") {
        payload.command = f.command;
        payload.args = f.args ? f.args.split(/\s+/).filter(Boolean) : [];
      } else {
        payload.url = f.url;
      }

      const res = await fetchWithAuth("/api/mcp/servers/", {
        method: "POST",
        body: JSON.stringify(payload),
      });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body.detail || JSON.stringify(body) || `HTTP ${res.status}`);
      }
      const newServer = await res.json();
      const createdServerId = newServer?.id || null;
      setAddServerOpen(false);
      setAddServerForm({
        name: "", description: "", transport: "streamable_http",
        url: "", command: "", args: "",
      });
      await loadServers();
      if (createdServerId) {
        await connectServer(createdServerId);
      }
    } catch (e) {
      setError(`Add server failed: ${e.message}`);
    } finally {
      setAddServerSaving(false);
    }
  };

  const handleOAuthPopup = (authUrl, serverId, existingPopup = null) => {
    const popup = existingPopup || window.open("", "mcp_oauth", "width=600,height=700");
    if (!popup) {
      setError("Please allow popups for OAuth authorization, then try connecting again.");
      return;
    }
    popup.location.href = authUrl;

    const redirectUri = `${window.location.origin}/oauth/callback`;
    const stop = startVisibleInterval(async () => {
      try {
        if (popup.closed) {
          stop();
          await loadServers();
          await connectServer(serverId);
          return;
        }
        const popupUrl = popup.location.href;
        if (popupUrl && popupUrl.startsWith(window.location.origin)) {
          stop();
          const params = new URL(popupUrl).searchParams;
          const code = params.get("code");
          const state = params.get("state");
          popup.close();

          if (code && state) {
            const cbRes = await fetchWithAuth("/api/mcp/oauth/callback/", {
              method: "POST",
              body: JSON.stringify({ code, state, redirect_uri: redirectUri }),
            });
            if (!cbRes.ok) {
              const body = await cbRes.json().catch(() => ({}));
              setError(`OAuth callback failed: ${body.error || cbRes.status}`);
              return;
            }
          }
          await loadServers();
          await connectServer(serverId);
        }
      } catch {
        // cross-origin access to popup.location throws until redirected back
      }
    }, 500);
  };

  const connectServer = async (serverId, options = {}) => {
    setError(null);
    const { preferPopup = false } = options;
    let preopenedPopup = null;

    // Pre-open popup within user gesture to avoid popup blockers when OAuth is required.
    if (preferPopup && typeof window !== "undefined") {
      preopenedPopup = window.open("", "mcp_oauth", "width=600,height=700");
    }

    try {
      const redirectUri = `${window.location.origin}/oauth/callback`;
      const res = await fetchWithAuth(`/api/mcp/servers/${serverId}/connect/`, {
        method: "POST",
        body: JSON.stringify({ redirect_uri: redirectUri }),
      });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        if (body.error === "oauth_required" && body.authorization_url) {
          // Auto-open OAuth popup — no manual config needed
          handleOAuthPopup(body.authorization_url, serverId, preopenedPopup);
          return;
        }
        if (preopenedPopup && !preopenedPopup.closed) preopenedPopup.close();
        if (body.error === "oauth_required") {
          throw new Error(body.message || "OAuth required but auto-discovery failed.");
        }
        throw new Error(body.error || body.message || `HTTP ${res.status}`);
      }
      if (preopenedPopup && !preopenedPopup.closed) preopenedPopup.close();
      loadServers();
    } catch (e) {
      if (preopenedPopup && !preopenedPopup.closed) preopenedPopup.close();
      setError(`Connect failed: ${e.message}`);
    }
  };

  const disconnectServer = async (serverId) => {
    setError(null);
    try {
      const res = await fetchWithAuth(`/api/mcp/servers/${serverId}/disconnect/`, {
        method: "POST",
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      loadServers();
    } catch (e) {
      setError(`Disconnect failed: ${e.message}`);
    }
  };

  const deleteServer = async (serverId) => {
    const srv = servers.find((s) => s.id === serverId);
    if (!window.confirm(`Permanently delete "${srv?.name || 'this server'}"? This will remove all tools, policies, and gateway sessions.`)) return;
    setError(null);
    try {
      const res = await fetchWithAuth(`/api/mcp/servers/${serverId}/`, {
        method: "DELETE",
      });
      if (res.status !== 204 && !res.ok) throw new Error(`HTTP ${res.status}`);
      if (selectedServerId === serverId) setSelectedServerId(null);
      loadServers();
    } catch (e) {
      setError(`Delete failed: ${e.message}`);
    }
  };

  const getGatewayUrl = (srv) => {
    if (!srv.gateway_slug) return null;
    const stored = localStorage.getItem("zeroshield_gateway_url");
    const base = stored || `http://${window.location.hostname}:8300`;
    return `${base}/mcp/zeroshield/${srv.gateway_slug}`;
  };

  const copyToClipboard = async (text) => {
    try {
      await navigator.clipboard.writeText(text);
    } catch {
      const ta = document.createElement("textarea");
      ta.value = text;
      document.body.appendChild(ta);
      ta.select();
      document.execCommand("copy");
      document.body.removeChild(ta);
    }
  };

  /** Build a ready-to-paste MCP server config entry for VS Code / Cursor mcp.json. */
  const buildMcpConfigSnippet = (srv) => {
    const url = getGatewayUrl(srv);
    if (!url) return null;
    const slug = srv.gateway_slug || srv.name?.toLowerCase().replace(/\s+/g, "-") || "mcp-server";
    const key = `zeroshield-${slug}`;
    const entry = {
      url,
      type: "http",
      headers: {
        Authorization: "Bearer ${input:ZEROSHIELD_GW_API_KEY}",
      },
    };
    return `${JSON.stringify(key)}: ${JSON.stringify(entry, null, 2)}`;
  };

  const copyMcpConfig = async (srv) => {
    const snippet = buildMcpConfigSnippet(srv);
    if (!snippet) return;
    await copyToClipboard(snippet);
    setCopiedGateway(`config:${srv.id}`);
    setTimeout(() => setCopiedGateway(null), 3000);
  };

  const toggleTool = async (toolId) => {
    setError(null);
    try {
      const tool = tools.find((t) => t.id === toolId);
      const res = await fetchWithAuth(
        `/api/mcp/servers/${selectedServerId}/tools/${toolId}/toggle/`,
        { method: "POST", body: JSON.stringify({ enabled: !(tool.is_enabled ?? tool.enabled) }) },
      );
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      loadToolsForServer(selectedServerId);
    } catch (e) {
      setError(`Toggle failed: ${e.message}`);
    }
  };

  const toggleVerboseLogging = async (serverId) => {
    setError(null);
    try {
      const srv = servers.find((s) => s.id === serverId);
      const res = await fetchWithAuth(
        `/api/mcp/servers/${serverId}/toggle-verbose-logging/`,
        { method: "POST", body: JSON.stringify({ verbose_logging: !srv.verbose_logging }) },
      );
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      loadServers();
    } catch (e) {
      setError(`Toggle logging failed: ${e.message}`);
    }
  };

  const deletePolicy = async (policyId) => {
    if (!window.confirm("Delete this policy?")) return;
    try {
      const res = await fetchWithAuth(`/api/mcp/policies/${policyId}/`, {
        method: "DELETE",
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      loadPolicies(selectedServerId);
    } catch (e) {
      setError(`Delete policy failed: ${e.message}`);
    }
  };

  const createPolicy = async () => {
    setSaving(true);
    setError(null);
    try {
      const payload = {
        name: policyForm.name || `Policy: ${policyForm.rule_type} ${policyForm.action}`,
        rule_type: policyForm.rule_type,
        server: selectedServerId || undefined,
        tool: policyForm.tool_id || undefined,
        action: policyForm.action,
        priority: parseInt(policyForm.priority, 10) || 100,
        is_active: policyForm.is_active,
        redaction_fields: policyForm.redact_fields
          ? policyForm.redact_fields.split(",").map((s) => s.trim()).filter(Boolean)
          : [],
      };
      if (policyForm.max_tokens) payload.max_output_tokens = parseInt(policyForm.max_tokens, 10);

      // Rule-type-specific fields
      if (policyForm.rule_type === "argument_constraint") {
        payload.argument_rules = policyForm.argument_rules
          .filter((r) => r.param && r.operator)
          .map((r) => ({
            param: r.param,
            operator: r.operator,
            value: r.operator === "in" || r.operator === "not_in"
              ? r.value.split(",").map((v) => v.trim()).filter(Boolean)
              : r.operator === "max_length" ? parseInt(r.value, 10) || 0
              : r.operator === "required" || r.operator === "no_injection" ? true
              : r.value,
            message: r.message || `Argument constraint on '${r.param}'`,
          }));
      }

      if (policyForm.rule_type === "context_constraint") {
        const conditions = {};
        if (policyForm.context_allowed_roles) {
          conditions.allowed_roles = policyForm.context_allowed_roles.split(",").map((s) => s.trim()).filter(Boolean);
        }
        if (policyForm.context_denied_roles) {
          conditions.denied_roles = policyForm.context_denied_roles.split(",").map((s) => s.trim()).filter(Boolean);
        }
        if (policyForm.context_hours_start && policyForm.context_hours_end) {
          conditions.allowed_hours = {
            start: parseInt(policyForm.context_hours_start, 10),
            end: parseInt(policyForm.context_hours_end, 10),
          };
        }
        payload.context_conditions = conditions;
      }

      if (policyForm.rule_type === "rate_limit") {
        payload.rate_limit_window = parseInt(policyForm.rate_limit_window, 10) || 60;
        payload.rate_limit_max_calls = parseInt(policyForm.rate_limit_max_calls, 10) || 60;
      }

      if (policyForm.rule_type === "risk_policy") {
        payload.risk_threshold = parseFloat(policyForm.risk_threshold) || 5.0;
        payload.risk_action_map = {
          critical: policyForm.risk_action_critical,
          high: policyForm.risk_action_high,
          medium: policyForm.risk_action_medium,
          low: policyForm.risk_action_low,
        };
      }

      const res = await fetchWithAuth("/api/mcp/policies/", {
        method: "POST",
        body: JSON.stringify(payload),
      });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body.detail || JSON.stringify(body) || `HTTP ${res.status}`);
      }
      setPolicyModalOpen(false);
      setPolicyForm({
        name: "", rule_type: "tool_control", tool_id: "", action: "deny", priority: 100, is_active: true,
        argument_rules: [{ param: "", operator: "no_injection", value: "", message: "" }],
        context_allowed_roles: "", context_denied_roles: "", context_hours_start: "", context_hours_end: "",
        rate_limit_window: "60", rate_limit_max_calls: "60",
        risk_threshold: "5.0", risk_action_critical: "deny", risk_action_high: "deny", risk_action_medium: "require_confirmation", risk_action_low: "allow",
        redact_fields: "", max_tokens: "",
      });
      loadPolicies(selectedServerId);
    } catch (e) {
      setError(`Create policy failed: ${e.message}`);
    } finally {
      setSaving(false);
    }
  };

  /* ════════════════════════ RENDER ════════════════════════ */
  return (
    <div className="mt-6 space-y-4">
      {/* Header */}
      <div className="bg-white dark:bg-slate-800 rounded-xl border border-slate-200 dark:border-slate-700 p-6">
        <div className="flex items-center justify-between mb-4">
          <div className="flex items-center gap-3">
            <div className="p-2 bg-violet-100 dark:bg-violet-800/30 rounded-lg">
              <Shield className="w-5 h-5 text-violet-600 dark:text-violet-400" />
            </div>
            <div>
              <h3 className="text-lg font-semibold text-slate-900 dark:text-white flex items-center">
                MCP Lifecycle Manager
                <span className="ml-2 inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium bg-violet-100 dark:bg-violet-800/30 text-violet-700">
                  Backend
                </span>
                <InfoTooltip title="How to Use">
                  {"Manage backend MCP server connections, toggle tools on/off, create per-tool deny/allow/redact policies, and review the full audit trail.\n\nServer list refreshes from /api/mcp/servers/."}
                </InfoTooltip>
              </h3>
              <p className="text-sm text-slate-500 dark:text-slate-400">
                Connect, govern, and audit MCP tool calls with policy enforcement
              </p>
            </div>
          </div>
          <div className="flex items-center gap-2">
            <button
              onClick={() => setAddServerOpen(true)}
              className="inline-flex items-center gap-1.5 px-3 py-1.5 text-sm font-medium rounded-lg bg-violet-600 text-white hover:bg-violet-700"
            >
              <Plus className="w-4 h-4" /> Add Server
            </button>
            <button
              onClick={loadServers}
              disabled={loading}
              className="inline-flex items-center gap-1.5 px-3 py-1.5 text-sm rounded-lg border border-slate-300 dark:border-slate-600 text-slate-700 dark:text-slate-300 hover:bg-slate-50 dark:hover:bg-slate-700 disabled:opacity-50"
            >
              {loading ? <Loader2 className="w-4 h-4 animate-spin" /> : <RefreshCw className="w-4 h-4" />}
              Refresh
            </button>
          </div>
        </div>

        {/* Tabs */}
        <div className="flex gap-1 border-b border-slate-200 dark:border-slate-700">
          {TABS.map(({ id, label, icon: Ico }) => (
            <button
              key={id}
              onClick={() => setTab(id)}
              className={`inline-flex items-center gap-1.5 px-4 py-2 text-sm font-medium border-b-2 transition-colors ${
                tab === id
                  ? "border-violet-600 text-violet-700 dark:text-violet-400"
                  : "border-transparent text-slate-500 hover:text-slate-700 dark:hover:text-slate-300"
              }`}
            >
              <Ico className="w-4 h-4" />
              {label}
            </button>
          ))}
        </div>

        {error && (
          <div className="mt-3 flex items-center gap-2 p-3 rounded-lg bg-red-50 dark:bg-red-900/20 border border-red-200 dark:border-red-800 text-red-700 dark:text-red-400 text-sm">
            <AlertTriangle className="w-4 h-4 shrink-0" />
            <span className="flex-1">{error}</span>
            <button onClick={() => setError(null)} aria-label="Dismiss error"><X className="w-4 h-4" /></button>
          </div>
        )}

        {enforcementStatus?.gateway_only_enforced && (
          <div className="mt-3 flex items-start gap-2 p-3 rounded-lg bg-amber-50 dark:bg-amber-900/20 border border-amber-200 dark:border-amber-800 text-amber-800 dark:text-amber-300 text-sm">
            <Lock className="w-4 h-4 mt-0.5 shrink-0" />
            <div>
              <p className="font-medium">Gateway-only MCP enforcement is active.</p>
              <p className="text-xs mt-0.5 opacity-90">
                Remote MCP servers must route through the AI Mesh Firewall gateway and stdio servers must use mcp-remote with a gateway URL.
              </p>
              {Array.isArray(enforcementStatus.allowed_gateway_origins) && enforcementStatus.allowed_gateway_origins.length > 0 && (
                <p className="text-xs mt-1 font-mono break-all">
                  Allowed origins: {enforcementStatus.allowed_gateway_origins.join(", ")}
                </p>
              )}
            </div>
          </div>
        )}
      </div>

      {/* ─── Tab: Connections ─── */}
      {tab === "connections" && (
        <div className="bg-white dark:bg-slate-800 rounded-xl border border-slate-200 dark:border-slate-700 overflow-hidden">
          <div className="px-6 py-4 border-b border-slate-200 dark:border-slate-700 flex items-center justify-between">
            <h4 className="text-sm font-semibold text-slate-900 dark:text-white">Backend MCP Servers</h4>
            <span className="text-xs text-slate-500 dark:text-slate-400">
              {servers.length} server(s)
            </span>
          </div>

          {servers.length === 0 && !loading && (
            <div className="p-10 text-center">
              <Server className="w-8 h-8 mx-auto text-slate-300 mb-2" />
              <p className="text-sm text-slate-500 dark:text-slate-400">No MCP servers registered yet.</p>
              <p className="text-xs text-slate-400 mt-1 mb-3">Add your first MCP server to start managing tool calls with guardrails.</p>
              <button
                onClick={() => setAddServerOpen(true)}
                className="inline-flex items-center gap-1.5 px-4 py-2 text-sm font-medium rounded-lg bg-violet-600 text-white hover:bg-violet-700"
              >
                <Plus className="w-4 h-4" /> Add Server
              </button>
            </div>
          )}

          <div className="divide-y divide-slate-100 dark:divide-slate-700">
            {servers.map((srv) => (
              <div key={srv.id} className="px-6 py-3 flex items-center gap-4 hover:bg-slate-50 dark:hover:bg-slate-700/50 transition-colors">
                <Server className="w-4 h-4 text-slate-500" />
                <div className="flex-1 min-w-0">
                  <div className="text-sm font-medium text-slate-900 dark:text-white truncate flex items-center gap-2">
                    {srv.name}
                    <span className={`inline-flex items-center px-1.5 py-0.5 rounded text-[10px] font-mono uppercase ${
                      srv.transport === "stdio"
                        ? "bg-violet-100 dark:bg-violet-800/30 text-violet-700 dark:text-violet-300"
                        : srv.transport === "sse"
                          ? "bg-cyan-100 dark:bg-cyan-800/30 text-cyan-700 dark:text-cyan-300"
                          : "bg-blue-100 dark:bg-blue-800/30 text-blue-700 dark:text-blue-300"
                    }`}>
                      {srv.transport}
                    </span>
                  </div>
                  <div className="text-xs text-slate-500 dark:text-slate-400 truncate">
                    {srv.url || (srv.command ? `${srv.command} ${(srv.args || []).join(" ")}` : "--")}
                  </div>
                  {srv.gateway_slug && (
                    <div className="mt-0.5 space-y-0.5">
                      <div className="flex items-center gap-1">
                        <Link className="w-3 h-3 text-indigo-400" />
                        <code className="text-[10px] text-indigo-500 dark:text-indigo-400 font-mono truncate">
                          {getGatewayUrl(srv)}
                        </code>
                        <button
                          onClick={(e) => { e.stopPropagation(); copyToClipboard(getGatewayUrl(srv)); }}
                          className="p-0.5 rounded hover:bg-indigo-100 dark:hover:bg-indigo-800/30 text-indigo-400 hover:text-indigo-600"
                          aria-label="Copy gateway URL"
                          title="Copy gateway URL only"
                        >
                          <Copy className="w-3 h-3" />
                        </button>
                      </div>
                      <button
                        onClick={(e) => { e.stopPropagation(); copyMcpConfig(srv); }}
                        className="inline-flex items-center gap-1 text-[10px] font-medium text-blue-600 dark:text-blue-400 hover:text-blue-700 dark:hover:text-blue-300 bg-blue-50 dark:bg-blue-900/30 px-2 py-0.5 rounded transition-colors"
                        title="Copy full MCP config JSON with auth headers (paste into VS Code / Cursor mcp.json)"
                      >
                        {copiedGateway === `config:${srv.id}`
                          ? <><CheckCircle className="w-3 h-3 text-emerald-500" /> Config Copied!</>
                          : <><Copy className="w-3 h-3" /> Copy MCP Config</>}
                      </button>
                    </div>
                  )}
                </div>

                <ServerStatusBadge status={srv.status} />

                <span className="text-xs text-slate-500 dark:text-slate-400">
                  {srv.tools?.length ?? srv.tool_count ?? 0} tool(s)
                </span>

                <div className="flex items-center gap-1.5">
                  {srv.status !== "connected" ? (
                    <button
                      onClick={() => connectServer(srv.id, { preferPopup: true })}
                      className="inline-flex items-center gap-1 px-2.5 py-1 text-xs font-medium rounded-lg bg-emerald-600 text-white hover:bg-emerald-700 dark:hover:bg-emerald-500"
                      title="Connect"
                    >
                      <Power className="w-3.5 h-3.5" /> Connect
                    </button>
                  ) : (
                    <button
                      onClick={() => disconnectServer(srv.id)}
                      className="inline-flex items-center gap-1 px-2.5 py-1 text-xs font-medium rounded-lg border border-slate-300 dark:border-slate-600 text-slate-700 dark:text-slate-300 hover:bg-slate-50 dark:hover:bg-slate-700"
                      title="Disconnect"
                    >
                      <PowerOff className="w-3.5 h-3.5" /> Disconnect
                    </button>
                  )}
                  <button
                    onClick={() => {
                      setSelectedServerId(srv.id);
                      setTab("tools");
                    }}
                    className="p-1.5 rounded hover:bg-slate-200 dark:hover:bg-slate-600 text-slate-500 dark:text-slate-300"
                    aria-label="View tools"
                    title="View tools"
                  >
                    <Eye className="w-3.5 h-3.5" />
                  </button>
                  <button
                    onClick={() => toggleVerboseLogging(srv.id)}
                    className={`p-1.5 rounded transition-colors ${
                      srv.verbose_logging
                        ? "bg-indigo-100 dark:bg-indigo-800/30 text-indigo-600 dark:text-indigo-400 hover:bg-indigo-200 dark:hover:bg-indigo-700/40"
                        : "hover:bg-slate-200 dark:hover:bg-slate-600 text-slate-400 dark:text-slate-500"
                    }`}
                    aria-label={srv.verbose_logging ? "Disable detailed logs" : "Enable detailed logs"}
                    title={srv.verbose_logging ? "Disable detailed logs" : "Enable detailed logs"}
                  >
                    <FileText className="w-3.5 h-3.5" />
                  </button>
                  <button
                    onClick={() => deleteServer(srv.id)}
                    className="p-1.5 rounded hover:bg-red-100 dark:hover:bg-red-900/30 text-slate-400 hover:text-red-600 dark:hover:text-red-400 transition-colors"
                    aria-label="Delete server"
                    title="Delete server"
                  >
                    <Trash2 className="w-3.5 h-3.5" />
                  </button>
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* ─── Tab: Tools ─── */}
      {tab === "tools" && (
        <div className="bg-white dark:bg-slate-800 rounded-xl border border-slate-200 dark:border-slate-700 overflow-hidden">
          {/* Server selector */}
          <div className="px-6 py-4 border-b border-slate-200 dark:border-slate-700 flex items-center gap-4">
            <label className="text-xs font-medium text-slate-500 dark:text-slate-400">Server</label>
            <select
              value={selectedServerId || ""}
              onChange={(e) => setSelectedServerId(e.target.value || null)}
              aria-label="MCP server"
              className="px-3 py-1.5 text-sm rounded-lg border border-slate-300 dark:border-slate-600 bg-white dark:bg-slate-700 text-slate-900 dark:text-white"
            >
              <option value="">— select —</option>
              {servers.map((s) => (
                <option key={s.id} value={s.id}>
                  {s.name} ({s.transport})
                </option>
              ))}
            </select>
            {selectedServer && (
              <ServerStatusBadge status={selectedServer.status} />
            )}
          </div>

          {!selectedServerId && (
            <div className="p-10 text-center text-sm text-slate-500 dark:text-slate-400">
              Select a server to manage its tools.
            </div>
          )}

          {selectedServerId && tools.length === 0 && (
            <div className="p-10 text-center text-sm text-slate-500 dark:text-slate-400">
              No tools discovered. Connect the server and run tool discovery first.
            </div>
          )}

          {selectedServerId && tools.length > 0 && (
            <table className="w-full text-sm">
              <thead>
                <tr className="bg-slate-50 dark:bg-slate-700 text-left">
                  <th className="px-4 py-2 text-xs font-medium text-slate-500 dark:text-slate-200 uppercase">Tool</th>
                  <th className="px-4 py-2 text-xs font-medium text-slate-500 dark:text-slate-200 uppercase">Description</th>
                  <th className="px-4 py-2 text-xs font-medium text-slate-500 dark:text-slate-200 uppercase text-center">Risk</th>
                  <th className="px-4 py-2 text-xs font-medium text-slate-500 dark:text-slate-200 uppercase text-center">Enabled</th>
                  <th className="px-4 py-2 text-xs font-medium text-slate-500 dark:text-slate-200 uppercase text-center">Compliance</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-200 dark:divide-slate-700">
                {tools.map((tool) => {
                  const riskCat = tool.risk_category || "minimal";
                  const riskStyle = RISK_STYLES[riskCat] || RISK_STYLES.minimal;
                  return (
                    <tr key={tool.id} className="hover:bg-slate-50 dark:hover:bg-slate-700/50 transition-colors">
                      <td className="px-4 py-2 font-mono text-xs text-slate-900 dark:text-white">{tool.name}</td>
                      <td className="px-4 py-2 text-xs text-slate-500 dark:text-slate-300 max-w-xs truncate" title={tool.description || "--"}>
                        {tool.description || "--"}
                      </td>
                      <td className="px-4 py-2">
                        <div className="flex flex-col items-center gap-1">
                          <span className={`inline-flex items-center px-2 py-0.5 rounded text-[10px] font-semibold uppercase ${riskStyle.bg} ${riskStyle.text}`}>
                            {riskCat} ({tool.risk_score ?? 0})
                          </span>
                          <span className="text-[10px] text-slate-400 dark:text-slate-500 max-w-[180px] text-center leading-tight" title={getRiskExplanation(tool)}>
                            {getRiskExplanation(tool).length > 60
                              ? getRiskExplanation(tool).slice(0, 57) + "…"
                              : getRiskExplanation(tool)}
                          </span>
                          <span className="text-[10px] italic text-slate-400 dark:text-slate-500" title={getRiskRecommendation(tool)}>
                            {getRiskRecommendation(tool).length > 50
                              ? getRiskRecommendation(tool).slice(0, 47) + "…"
                              : getRiskRecommendation(tool)}
                          </span>
                        </div>
                      </td>
                      <td className="px-4 py-2 text-center">
                        <button
                          onClick={() => toggleTool(tool.id)}
                          aria-label={(tool.is_enabled ?? tool.enabled) ? "Disable tool" : "Enable tool"}
                          title={(tool.is_enabled ?? tool.enabled) ? "Disable tool" : "Enable tool"}
                          className="group"
                        >
                          {(tool.is_enabled ?? tool.enabled) ? (
                            <ToggleRight className="w-6 h-6 text-emerald-500 group-hover:text-emerald-700 dark:group-hover:text-emerald-300" />
                          ) : (
                            <ToggleLeft className="w-6 h-6 text-slate-400 dark:text-slate-500 group-hover:text-slate-600 dark:group-hover:text-slate-300" />
                          )}
                        </button>
                      </td>
                      <td className="px-4 py-2 text-center">
                        <div className="flex flex-wrap gap-1 justify-center">
                          {(tool.compliance_tags && tool.compliance_tags.length > 0)
                            ? tool.compliance_tags.map((t) => <ComplianceTag key={t} tag={t} />)
                            : <span className="text-xs text-slate-400">—</span>}
                        </div>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          )}
        </div>
      )}

      {/* ─── Tab: Policies ─── */}
      {tab === "policies" && (
        <div className="bg-white dark:bg-slate-800 rounded-xl border border-slate-200 dark:border-slate-700 overflow-hidden">
          <div className="px-6 py-4 border-b border-slate-200 dark:border-slate-700 flex items-center justify-between">
            <div className="flex items-center gap-4">
              <h4 className="text-sm font-semibold text-slate-900 dark:text-white">Tool Policies</h4>
              <select
                value={selectedServerId || ""}
                onChange={(e) => setSelectedServerId(e.target.value || null)}
                aria-label="Filter policies by server"
                className="px-3 py-1 text-xs rounded-lg border border-slate-300 dark:border-slate-600 bg-white dark:bg-slate-700 text-slate-900 dark:text-white"
              >
                <option value="">All servers (global)</option>
                {servers.map((s) => (
                  <option key={s.id} value={s.id}>{s.name}</option>
                ))}
              </select>
            </div>
            <button
              onClick={() => setPolicyModalOpen(true)}
              className="inline-flex items-center gap-1.5 px-3 py-1.5 text-sm font-medium rounded-lg bg-violet-600 text-white hover:bg-violet-700"
            >
              <Plus className="w-4 h-4" /> New Policy
            </button>
          </div>

          {policies.length === 0 && (
            <div className="p-10 text-center text-sm text-slate-500 dark:text-slate-400">
              No policies configured. Click "New Policy" to create one.
            </div>
          )}

          {policies.length > 0 && (
            <table className="w-full text-sm">
              <thead>
                <tr className="bg-slate-50 dark:bg-slate-700 text-left">
                  <th className="px-4 py-2 text-xs font-medium text-slate-500 dark:text-slate-200 uppercase">Name</th>
                  <th className="px-4 py-2 text-xs font-medium text-slate-500 dark:text-slate-200 uppercase">Rule Type</th>
                  <th className="px-4 py-2 text-xs font-medium text-slate-500 dark:text-slate-200 uppercase">Tool</th>
                  <th className="px-4 py-2 text-xs font-medium text-slate-500 dark:text-slate-200 uppercase">Action</th>
                  <th className="px-4 py-2 text-xs font-medium text-slate-500 dark:text-slate-200 uppercase text-center">Priority</th>
                  <th className="px-4 py-2 text-xs font-medium text-slate-500 dark:text-slate-200 uppercase text-center">Active</th>
                  <th className="px-4 py-2 text-xs font-medium text-slate-500 dark:text-slate-200 uppercase">Details</th>
                  <th className="px-4 py-2 text-xs font-medium text-slate-500 dark:text-slate-200 uppercase text-center">Del</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-200 dark:divide-slate-700">
                {policies.map((p) => {
                  const ruleType = p.rule_type || "tool_control";
                  const ruleTypeColors = {
                    tool_control: "bg-slate-100 dark:bg-slate-600 text-slate-700 dark:text-slate-200",
                    argument_constraint: "bg-purple-100 dark:bg-purple-800/30 text-purple-700 dark:text-purple-300",
                    context_constraint: "bg-cyan-100 dark:bg-cyan-800/30 text-cyan-700 dark:text-cyan-300",
                    rate_limit: "bg-orange-100 dark:bg-orange-800/30 text-orange-700 dark:text-orange-300",
                    risk_policy: "bg-rose-100 dark:bg-rose-800/30 text-rose-700 dark:text-rose-300",
                  };
                  const ruleTypeLabels = {
                    tool_control: "Tool Control",
                    argument_constraint: "Arg Constraint",
                    context_constraint: "Context",
                    rate_limit: "Rate Limit",
                    risk_policy: "Risk Policy",
                  };
                  // Build details summary
                  let detailParts = [];
                  if (p.argument_rules?.length > 0) detailParts.push(`${p.argument_rules.length} arg rule(s)`);
                  if (p.context_conditions && Object.keys(p.context_conditions).length > 0) {
                    const cc = p.context_conditions;
                    if (cc.allowed_roles?.length) detailParts.push(`Roles: ${cc.allowed_roles.join(", ")}`);
                    if (cc.denied_roles?.length) detailParts.push(`Deny roles: ${cc.denied_roles.join(", ")}`);
                    if (cc.allowed_hours) detailParts.push(`Hours: ${cc.allowed_hours.start}-${cc.allowed_hours.end}`);
                  }
                  if (p.rate_limit_max_calls > 0) detailParts.push(`${p.rate_limit_max_calls}/${p.rate_limit_window}s`);
                  if (p.risk_threshold > 0) detailParts.push(`Risk ≥ ${p.risk_threshold}`);
                  if (p.redaction_fields?.length > 0) detailParts.push(`Redact: ${p.redaction_fields.join(", ")}`);
                  const detailStr = detailParts.join(" · ") || "--";
                  return (
                  <tr key={p.id} className="hover:bg-slate-50 dark:hover:bg-slate-700/50 transition-colors">
                    <td className="px-4 py-2 text-xs text-slate-900 dark:text-white max-w-[140px] truncate" title={p.name}>{p.name || "--"}</td>
                    <td className="px-4 py-2">
                      <span className={`inline-flex items-center px-2 py-0.5 rounded text-[10px] font-semibold ${ruleTypeColors[ruleType] || ruleTypeColors.tool_control}`}>
                        {ruleTypeLabels[ruleType] || ruleType}
                      </span>
                    </td>
                    <td className="px-4 py-2 font-mono text-xs text-slate-900 dark:text-white">{p.tool_name || "*"}</td>
                    <td className="px-4 py-2">
                      <span className={`inline-flex items-center px-2 py-0.5 rounded text-xs font-medium ${
                        p.action === "deny"
                          ? "bg-red-100 dark:bg-red-800/30 text-red-700 dark:text-red-300"
                          : p.action === "allow"
                            ? "bg-emerald-100 dark:bg-emerald-800/30 text-emerald-700 dark:text-emerald-300"
                            : p.action === "audit_only"
                              ? "bg-blue-100 dark:bg-blue-800/30 text-blue-700 dark:text-blue-300"
                              : p.action === "require_confirmation"
                                ? "bg-yellow-100 dark:bg-yellow-800/30 text-yellow-700 dark:text-yellow-300"
                                : "bg-amber-100 dark:bg-amber-800/30 text-amber-700 dark:text-amber-300"
                      }`}>
                        {p.action}
                      </span>
                    </td>
                    <td className="px-4 py-2 text-center text-xs text-slate-600 dark:text-slate-400">{p.priority}</td>
                    <td className="px-4 py-2 text-center">
                      {p.is_active ? (
                        <CheckCircle className="w-4 h-4 text-emerald-500 inline" />
                      ) : (
                        <X className="w-4 h-4 text-slate-400 inline" />
                      )}
                    </td>
                    <td className="px-4 py-2 text-xs text-slate-500 dark:text-slate-300 max-w-[200px] truncate" title={detailStr}>
                      {detailStr}
                    </td>
                    <td className="px-4 py-2 text-center">
                      <button
                        onClick={() => deletePolicy(p.id)}
                        aria-label="Delete policy"
                        title="Delete policy"
                        className="p-1 rounded hover:bg-red-100 dark:hover:bg-red-900/30 text-red-400 hover:text-red-600"
                      >
                        <Trash2 className="w-3.5 h-3.5" />
                      </button>
                    </td>
                  </tr>
                  );
                })}
              </tbody>
            </table>
          )}
        </div>
      )}

      {/* ─── Tab: Activity & Incidents ─── */}
      {tab === "activity" && (
        <div className="bg-white dark:bg-slate-800 rounded-xl border border-slate-200 dark:border-slate-700 overflow-hidden">
          <div className="px-6 py-4 border-b border-slate-200 dark:border-slate-700 flex items-center justify-between">
            <h4 className="text-sm font-semibold text-slate-900 dark:text-white">MCP Activity & Incidents</h4>
            <button
              onClick={() => { loadActivityEvents(); loadIncidents(); }}
              className="inline-flex items-center gap-1 px-2.5 py-1 text-xs rounded-lg border border-slate-300 dark:border-slate-600 text-slate-700 dark:text-slate-300 hover:bg-slate-50 dark:hover:bg-slate-700"
            >
              <RefreshCw className="w-3 h-3" /> Refresh
            </button>
          </div>

          <div className="grid md:grid-cols-2 gap-0 divide-x divide-slate-200 dark:divide-slate-700">
            {/* Activity Feed */}
            <div className="p-4">
              <h5 className="text-xs font-semibold text-slate-600 dark:text-slate-400 uppercase mb-3 flex items-center gap-1.5">
                <Clock className="w-3.5 h-3.5" /> Recent MCP Activity
              </h5>
              {activityEvents.length === 0 && (
                <p className="text-xs text-slate-400 text-center py-6">No MCP activity events found.</p>
              )}
              <div className="space-y-2 max-h-96 overflow-y-auto">
                {activityEvents.map((evt, idx) => (
                  <div key={evt.id || idx} className="flex items-start gap-2 p-2 rounded-lg bg-slate-50 dark:bg-slate-700/50 text-xs">
                    <span className={`inline-flex items-center px-1.5 py-0.5 rounded text-[10px] font-semibold shrink-0 ${
                      (evt.severity || evt.threat_level) === "critical"
                        ? "bg-red-100 dark:bg-red-800/30 text-red-700 dark:text-red-300"
                        : (evt.severity || evt.threat_level) === "high"
                          ? "bg-orange-100 dark:bg-orange-800/30 text-orange-700 dark:text-orange-300"
                          : (evt.severity || evt.threat_level) === "medium"
                            ? "bg-amber-100 dark:bg-amber-800/30 text-amber-700 dark:text-amber-300"
                            : "bg-blue-100 dark:bg-blue-800/30 text-blue-700 dark:text-blue-300"
                    }`}>
                      {evt.severity || evt.threat_level || "info"}
                    </span>
                    <div className="flex-1 min-w-0">
                      <div className="font-medium text-slate-900 dark:text-white truncate">
                        {evt.description || evt.event_type || evt.action || "MCP event"}
                      </div>
                      <div className="text-slate-400 mt-0.5">
                        {evt.tool_name && <span className="font-mono">{evt.tool_name}</span>}
                        {evt.server_name && <span> on {evt.server_name}</span>}
                        {evt.timestamp && <span> · {new Date(evt.timestamp || evt.created_at).toLocaleString()}</span>}
                      </div>
                    </div>
                  </div>
                ))}
              </div>
            </div>

            {/* Incidents */}
            <div className="p-4">
              <h5 className="text-xs font-semibold text-slate-600 dark:text-slate-400 uppercase mb-3 flex items-center gap-1.5">
                <AlertTriangle className="w-3.5 h-3.5" /> Security Incidents
              </h5>
              {incidents.length === 0 && (
                <p className="text-xs text-slate-400 text-center py-6">No security incidents recorded.</p>
              )}
              <div className="space-y-2 max-h-96 overflow-y-auto">
                {incidents.map((inc, idx) => (
                  <div key={inc.id || idx} className="p-3 rounded-lg border border-slate-200 dark:border-slate-600 text-xs">
                    <div className="flex items-center gap-2 mb-1">
                      <span className={`inline-flex items-center px-1.5 py-0.5 rounded text-[10px] font-semibold uppercase ${
                        inc.severity === "critical"
                          ? "bg-red-100 dark:bg-red-800/30 text-red-700 dark:text-red-300"
                          : inc.severity === "high"
                            ? "bg-orange-100 dark:bg-orange-800/30 text-orange-700 dark:text-orange-300"
                            : inc.severity === "medium"
                              ? "bg-amber-100 dark:bg-amber-800/30 text-amber-700 dark:text-amber-300"
                              : "bg-blue-100 dark:bg-blue-800/30 text-blue-700 dark:text-blue-300"
                      }`}>
                        {inc.severity || "low"}
                      </span>
                      <span className={`inline-flex items-center px-1.5 py-0.5 rounded text-[10px] font-medium ${
                        inc.status === "open"
                          ? "bg-red-50 dark:bg-red-900/20 text-red-600 dark:text-red-400"
                          : inc.status === "resolved"
                            ? "bg-emerald-50 dark:bg-emerald-900/20 text-emerald-600 dark:text-emerald-400"
                            : "bg-amber-50 dark:bg-amber-900/20 text-amber-600 dark:text-amber-400"
                      }`}>
                        {inc.status || "open"}
                      </span>
                      <span className="flex-1 font-medium text-slate-900 dark:text-white truncate">
                        {inc.title || inc.description || "Incident"}
                      </span>
                    </div>
                    {inc.description && <p className="text-slate-500 dark:text-slate-400 mt-1 line-clamp-2">{inc.description}</p>}
                    <div className="text-slate-400 mt-1">
                      {inc.created_at && <span>{new Date(inc.created_at).toLocaleString()}</span>}
                      {inc.assigned_to && <span> · Assigned: {inc.assigned_to}</span>}
                    </div>
                  </div>
                ))}
              </div>
            </div>
          </div>
        </div>
      )}

      {/* ─── Tab: Audit Log ─── */}
      {tab === "audit" && (
        <div className="bg-white dark:bg-slate-800 rounded-xl border border-slate-200 dark:border-slate-700 overflow-hidden">
          <div className="px-6 py-4 border-b border-slate-200 dark:border-slate-700 flex flex-wrap items-center gap-3">
            <h4 className="text-sm font-semibold text-slate-900 dark:text-white">Audit Trail</h4>
            <select
              value={selectedServerId || ""}
              onChange={(e) => setSelectedServerId(e.target.value || null)}
              aria-label="Filter audit log by server"
              className="px-3 py-1 text-xs rounded-lg border border-slate-300 dark:border-slate-600 bg-white dark:bg-slate-700 text-slate-900 dark:text-white"
            >
              <option value="">All servers</option>
              {servers.map((s) => (
                <option key={s.id} value={s.id}>{s.name}</option>
              ))}
            </select>
            <select
              value={auditActionFilter}
              onChange={(e) => setAuditActionFilter(e.target.value)}
              aria-label="Filter audit log by action"
              className="px-3 py-1 text-xs rounded-lg border border-slate-300 dark:border-slate-600 bg-white dark:bg-slate-700 text-slate-900 dark:text-white"
            >
              <option value="">All actions</option>
              <option value="execute">Execute</option>
              <option value="block">Block</option>
              <option value="redact">Redact</option>
              <option value="error">Error</option>
              <option value="connect">Connect</option>
              <option value="disconnect">Disconnect</option>
              <option value="discover">Discover</option>
            </select>
            <input
              type="text"
              value={auditToolFilter}
              onChange={(e) => setAuditToolFilter(e.target.value)}
              placeholder="Filter by tool name…"
              className="px-3 py-1 text-xs rounded-lg border border-slate-300 dark:border-slate-600 bg-white dark:bg-slate-700 text-slate-900 dark:text-white w-40"
            />
            <button
              onClick={() => loadAuditLogs(selectedServerId, { action: auditActionFilter, tool_name: auditToolFilter })}
              className="p-1 rounded hover:bg-slate-100 dark:hover:bg-slate-700 text-slate-400"
              aria-label="Refresh audit log"
              title="Refresh"
            >
              <RefreshCw className="w-3.5 h-3.5" />
            </button>
            <span className="text-xs text-slate-400 ml-auto">{auditLogs.length} record(s)</span>
          </div>

          {auditLogs.length === 0 && (
            <div className="p-10 text-center text-sm text-slate-500 dark:text-slate-400">
              No audit records match the current filters.
            </div>
          )}

          {auditLogs.length > 0 && (
            <div className="divide-y divide-slate-100 dark:divide-slate-700">
              {auditLogs.map((log) => (
                <div key={log.id}>
                  <div
                    className="px-6 py-3 flex items-center gap-3 cursor-pointer hover:bg-slate-50 dark:hover:bg-slate-700/50 transition-colors text-xs"
                    onClick={() => setExpandedAudit(expandedAudit === log.id ? null : log.id)}
                  >
                    <span className="text-slate-400">
                      {expandedAudit === log.id ? <ChevronDown className="w-3.5 h-3.5" /> : <ChevronRight className="w-3.5 h-3.5" />}
                    </span>
                    <Clock className="w-3.5 h-3.5 text-slate-400" />
                    <span className="text-slate-500 dark:text-slate-400 w-36 shrink-0">
                      {log.timestamp ? new Date(log.timestamp).toLocaleString() : "--"}
                    </span>
                    <span className={`inline-flex items-center px-2 py-0.5 rounded font-medium shrink-0 ${
                      log.action === "block"
                        ? "bg-red-100 dark:bg-red-800/30 text-red-700 dark:text-red-300"
                        : log.action === "execute"
                          ? "bg-emerald-100 dark:bg-emerald-800/30 text-emerald-700 dark:text-emerald-300"
                          : log.action === "error"
                            ? "bg-red-100 dark:bg-red-800/30 text-red-700 dark:text-red-300"
                            : "bg-amber-100 dark:bg-amber-800/30 text-amber-700 dark:text-amber-300"
                    }`}>
                      {log.action}
                    </span>
                    <span className="font-mono text-slate-900 dark:text-white truncate">
                      {log.tool_name || "(system)"}
                    </span>
                    {log.user_display && (
                      <span className="text-slate-400 shrink-0">by {log.user_display}</span>
                    )}
                    {log.server_name && (
                      <span className="text-slate-400 shrink-0">on {log.server_name}</span>
                    )}
                    {log.duration_ms != null && log.duration_ms > 0 && (
                      <span className="text-slate-400 shrink-0">{log.duration_ms}ms</span>
                    )}
                    {log.status && log.status !== "unknown" && (
                      <span className={`inline-flex items-center px-1.5 py-0.5 rounded text-[10px] font-medium shrink-0 ${
                        log.status === "error" || log.status === "blocked"
                          ? "bg-red-100 dark:bg-red-800/30 text-red-700 dark:text-red-300"
                          : "bg-emerald-100 dark:bg-emerald-800/30 text-emerald-700 dark:text-emerald-300"
                      }`}>
                        {log.status}
                      </span>
                    )}
                    {log.policy_action && (
                      <span className={`inline-flex items-center gap-0.5 px-1.5 py-0.5 rounded text-[10px] font-medium shrink-0 ${
                        log.policy_action === "deny"
                          ? "bg-red-100 dark:bg-red-800/30 text-red-700 dark:text-red-300"
                          : log.policy_action === "redact" || log.policy_action === "allow_with_redaction"
                            ? "bg-amber-100 dark:bg-amber-800/30 text-amber-700 dark:text-amber-300"
                            : "bg-emerald-100 dark:bg-emerald-800/30 text-emerald-700 dark:text-emerald-300"
                      }`}>
                        <Lock className="w-2.5 h-2.5" />
                        {log.policy_action}
                      </span>
                    )}
                  </div>

                  {expandedAudit === log.id && (
                    <div className="px-6 pb-4 ml-8">
                      <div className="bg-slate-50 dark:bg-slate-800/50 rounded-lg border border-slate-200 dark:border-slate-700 p-4 text-xs space-y-2">
                        {log.user_display && <div><span className="text-slate-400">User:</span> <span className="text-slate-700 dark:text-slate-300">{log.user_display}</span></div>}
                        {log.organization_name && <div><span className="text-slate-400">Organization:</span> <span className="text-slate-700 dark:text-slate-300">{log.organization_name}</span></div>}
                        {log.server_name && <div><span className="text-slate-400">Server:</span> <span className="text-slate-700 dark:text-slate-300">{log.server_name}</span></div>}
                        {log.risk_category && <div><span className="text-slate-400">Risk:</span> <span className="text-slate-700 dark:text-slate-300">{log.risk_category} (score: {log.risk_score ?? "n/a"})</span></div>}
                        {log.input_params && Object.keys(log.input_params).length > 0 && (
                          <div>
                            <span className="text-slate-400">Input (redacted):</span>
                            <pre className="mt-1 p-2 bg-white dark:bg-slate-800 rounded text-xs overflow-auto max-h-24 text-slate-700 dark:text-slate-300 font-mono">
                              {JSON.stringify(log.input_params, null, 2)}
                            </pre>
                          </div>
                        )}
                        {log.output_preview && (typeof log.output_preview === "string" ? log.output_preview : Object.keys(log.output_preview).length > 0) && (
                          <div>
                            <span className="text-slate-400">Output (redacted):</span>
                            <pre className="mt-1 p-2 bg-white dark:bg-slate-800 rounded text-xs overflow-auto max-h-24 text-slate-700 dark:text-slate-300 font-mono">
                              {typeof log.output_preview === "string"
                                ? log.output_preview
                                : JSON.stringify(log.output_preview, null, 2)}
                            </pre>
                          </div>
                        )}
                        {log.policy_decision && Object.keys(log.policy_decision).length > 0 && (
                          <div>
                            <span className="text-slate-400">Policy Decision:</span>
                            <pre className="mt-1 p-2 bg-white dark:bg-slate-800 rounded text-xs overflow-auto max-h-16 text-slate-700 dark:text-slate-300 font-mono">
                              {JSON.stringify(log.policy_decision, null, 2)}
                            </pre>
                          </div>
                        )}
                        {log.redacted_fields && log.redacted_fields.length > 0 && (
                          <div><span className="text-slate-400">Redacted Fields:</span> <span className="text-slate-700 dark:text-slate-300">{log.redacted_fields.join(", ")}</span></div>
                        )}
                        {log.compliance_tags && log.compliance_tags.length > 0 && (
                          <div className="flex items-center gap-1">
                            <span className="text-slate-400">Compliance:</span>
                            {log.compliance_tags.map((t) => <ComplianceTag key={t} tag={t} />)}
                          </div>
                        )}
                        {log.error_message && (
                          <div className="text-red-600 dark:text-red-400">
                            <span className="text-slate-400">Error:</span> {log.error_message}
                          </div>
                        )}
                        {log.duration_ms != null && (
                          <div><span className="text-slate-400">Duration:</span> {log.duration_ms}ms</div>
                        )}
                      </div>
                    </div>
                  )}
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      {/* ─── Add Server Modal ─── */}
      {addServerOpen && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4" role="dialog" aria-modal="true" aria-labelledby="add-server-title">
          <div ref={addServerModalRef} tabIndex={-1} className="bg-white dark:bg-slate-800 rounded-xl border border-slate-200 dark:border-slate-700 w-full max-w-lg shadow-xl max-h-[90vh] flex flex-col">
            <div className="px-6 py-4 border-b border-slate-200 dark:border-slate-700 flex items-center justify-between shrink-0">
              <h4 id="add-server-title" className="text-base font-semibold text-slate-900 dark:text-white">Add MCP Server</h4>
              <button onClick={() => setAddServerOpen(false)} className="p-1 rounded hover:bg-slate-200 dark:hover:bg-slate-600" aria-label="Close add server dialog">
                <X className="w-5 h-5 text-slate-500" />
              </button>
            </div>

            <div className="p-6 space-y-4 overflow-y-auto">
              {/* Name */}
              <div>
                <label className="block text-xs font-medium text-slate-500 dark:text-slate-400 mb-1">Server Name</label>
                <input
                  type="text"
                  autoFocus
                  value={addServerForm.name}
                  onChange={(e) => setAddServerForm({ ...addServerForm, name: e.target.value })}
                  className="w-full px-3 py-2 text-sm rounded-lg border border-slate-300 dark:border-slate-600 bg-white dark:bg-slate-700 text-slate-900 dark:text-white"
                  placeholder="e.g. Linear, GitHub, Slack"
                />
              </div>

              {/* Description */}
              <div>
                <label className="block text-xs font-medium text-slate-500 dark:text-slate-400 mb-1">Description <span className="text-slate-400">(optional)</span></label>
                <input
                  type="text"
                  value={addServerForm.description}
                  onChange={(e) => setAddServerForm({ ...addServerForm, description: e.target.value })}
                  className="w-full px-3 py-2 text-sm rounded-lg border border-slate-300 dark:border-slate-600 bg-white dark:bg-slate-700 text-slate-900 dark:text-white"
                  placeholder="Project management, code hosting, etc."
                />
              </div>

              {/* Transport */}
              <div>
                <label className="block text-xs font-medium text-slate-500 dark:text-slate-400 mb-1">Transport</label>
                <select
                  value={addServerForm.transport}
                  onChange={(e) => setAddServerForm({ ...addServerForm, transport: e.target.value })}
                  aria-label="Transport"
                  className="w-full px-3 py-2 text-sm rounded-lg border border-slate-300 dark:border-slate-600 bg-white dark:bg-slate-700 text-slate-900 dark:text-white"
                >
                  <option value="streamable_http">Streamable HTTP</option>
                  <option value="sse">SSE (Server-Sent Events)</option>
                  <option value="stdio">stdio (local process)</option>
                </select>
              </div>

              {/* URL (for HTTP/SSE) */}
              {addServerForm.transport !== "stdio" && (
                <div>
                  <label className="block text-xs font-medium text-slate-500 dark:text-slate-400 mb-1">Server URL</label>
                  <input
                    type="url"
                    value={addServerForm.url}
                    onChange={(e) => setAddServerForm({ ...addServerForm, url: e.target.value })}
                    className="w-full px-3 py-2 text-sm rounded-lg border border-slate-300 dark:border-slate-600 bg-white dark:bg-slate-700 text-slate-900 dark:text-white font-mono"
                    placeholder="https://mcp.linear.app/sse"
                  />
                  <p className="mt-1 text-xs text-slate-400 dark:text-slate-500">
                    If the server requires OAuth, it will be discovered and handled automatically.
                  </p>
                </div>
              )}

              {/* Command + Args (for stdio) */}
              {addServerForm.transport === "stdio" && (
                <div className="grid grid-cols-2 gap-4">
                  <div>
                    <label className="block text-xs font-medium text-slate-500 dark:text-slate-400 mb-1">Command</label>
                    <input
                      type="text"
                      value={addServerForm.command}
                      onChange={(e) => setAddServerForm({ ...addServerForm, command: e.target.value })}
                      className="w-full px-3 py-2 text-sm rounded-lg border border-slate-300 dark:border-slate-600 bg-white dark:bg-slate-700 text-slate-900 dark:text-white font-mono"
                      placeholder="npx"
                    />
                  </div>
                  <div>
                    <label className="block text-xs font-medium text-slate-500 dark:text-slate-400 mb-1">Arguments</label>
                    <input
                      type="text"
                      value={addServerForm.args}
                      onChange={(e) => setAddServerForm({ ...addServerForm, args: e.target.value })}
                      className="w-full px-3 py-2 text-sm rounded-lg border border-slate-300 dark:border-slate-600 bg-white dark:bg-slate-700 text-slate-900 dark:text-white font-mono"
                      placeholder="-y @org/mcp-server"
                    />
                  </div>
                </div>
              )}
            </div>

            <div className="px-6 py-4 border-t border-slate-200 dark:border-slate-700 flex justify-end gap-3 shrink-0">
              <button
                onClick={() => setAddServerOpen(false)}
                className="px-4 py-2 text-sm rounded-lg border border-slate-300 dark:border-slate-600 text-slate-700 dark:text-slate-300 hover:bg-slate-50 dark:hover:bg-slate-700"
              >
                Cancel
              </button>
              <button
                onClick={addServer}
                disabled={addServerSaving || !addServerForm.name.trim()}
                className="inline-flex items-center gap-1.5 px-4 py-2 text-sm font-medium rounded-lg bg-violet-600 text-white hover:bg-violet-700 disabled:opacity-50"
              >
                {addServerSaving ? <Loader2 className="w-4 h-4 animate-spin" /> : <Plus className="w-4 h-4" />}
                Create & Connect
              </button>
            </div>
          </div>
        </div>
      )}

      {/* ─── Create Policy Modal ─── */}
      {policyModalOpen && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4" role="dialog" aria-modal="true" aria-labelledby="create-policy-title">
          <div ref={policyModalRef} tabIndex={-1} className="bg-white dark:bg-slate-800 rounded-xl border border-slate-200 dark:border-slate-700 w-full max-w-2xl shadow-xl max-h-[90vh] flex flex-col">
            <div className="px-6 py-4 border-b border-slate-200 dark:border-slate-700 flex items-center justify-between shrink-0">
              <h4 id="create-policy-title" className="text-base font-semibold text-slate-900 dark:text-white">Create Structured Policy</h4>
              <button onClick={() => setPolicyModalOpen(false)} className="p-1 rounded hover:bg-slate-200 dark:hover:bg-slate-600" aria-label="Close create policy dialog">
                <X className="w-5 h-5 text-slate-500" />
              </button>
            </div>

            <div className="p-6 space-y-4 overflow-y-auto flex-1">
              {/* Name */}
              <div>
                <label className="block text-xs font-medium text-slate-500 dark:text-slate-400 mb-1">
                  Policy Name <span className="text-red-400">*</span>
                </label>
                <input
                  type="text"
                  autoFocus
                  value={policyForm.name}
                  onChange={(e) => setPolicyForm({ ...policyForm, name: e.target.value })}
                  className="w-full px-3 py-2 text-sm rounded-lg border border-slate-300 dark:border-slate-600 bg-white dark:bg-slate-700 text-slate-900 dark:text-white"
                  placeholder="e.g. Block SQL Injection in Arguments"
                />
              </div>

              {/* Rule Type Selector */}
              <div>
                <label className="block text-xs font-medium text-slate-500 dark:text-slate-400 mb-1">Rule Type</label>
                <div className="grid grid-cols-5 gap-1">
                  {[
                    { value: "tool_control", label: "Tool Control", color: "slate" },
                    { value: "argument_constraint", label: "Arg Constraint", color: "purple" },
                    { value: "context_constraint", label: "Context", color: "cyan" },
                    { value: "rate_limit", label: "Rate Limit", color: "orange" },
                    { value: "risk_policy", label: "Risk Policy", color: "rose" },
                  ].map(({ value, label }) => (
                    <button
                      key={value}
                      type="button"
                      onClick={() => setPolicyForm({ ...policyForm, rule_type: value })}
                      className={`px-2 py-1.5 text-[11px] font-medium rounded-lg border transition-colors ${
                        policyForm.rule_type === value
                          ? "border-violet-500 bg-violet-50 dark:bg-violet-900/30 text-violet-700 dark:text-violet-300 ring-1 ring-violet-500"
                          : "border-slate-300 dark:border-slate-600 text-slate-600 dark:text-slate-400 hover:bg-slate-50 dark:hover:bg-slate-700"
                      }`}
                    >
                      {label}
                    </button>
                  ))}
                </div>
              </div>

              {/* Tool Selector + Action + Priority */}
              <div className="grid grid-cols-3 gap-4">
                <div>
                  <label className="block text-xs font-medium text-slate-500 dark:text-slate-400 mb-1">Tool</label>
                  <select
                    value={policyForm.tool_id}
                    onChange={(e) => setPolicyForm({ ...policyForm, tool_id: e.target.value })}
                    aria-label="Policy tool scope"
                    className="w-full px-3 py-2 text-sm rounded-lg border border-slate-300 dark:border-slate-600 bg-white dark:bg-slate-700 text-slate-900 dark:text-white"
                  >
                    <option value="">All tools on server</option>
                    {tools.map((t) => (
                      <option key={t.id} value={t.id}>{t.name}</option>
                    ))}
                  </select>
                </div>
                <div>
                  <label className="block text-xs font-medium text-slate-500 dark:text-slate-400 mb-1">Action</label>
                  <select
                    value={policyForm.action}
                    onChange={(e) => setPolicyForm({ ...policyForm, action: e.target.value })}
                    aria-label="Policy action"
                    className="w-full px-3 py-2 text-sm rounded-lg border border-slate-300 dark:border-slate-600 bg-white dark:bg-slate-700 text-slate-900 dark:text-white"
                  >
                    <option value="deny">Deny</option>
                    <option value="allow">Allow</option>
                    <option value="allow_with_redaction">Allow + Redact</option>
                    <option value="audit_only">Audit Only</option>
                    <option value="require_confirmation">Require Confirmation</option>
                  </select>
                </div>
                <div>
                  <label className="block text-xs font-medium text-slate-500 dark:text-slate-400 mb-1">Priority</label>
                  <input
                    type="number"
                    value={policyForm.priority}
                    onChange={(e) => setPolicyForm({ ...policyForm, priority: e.target.value })}
                    className="w-full px-3 py-2 text-sm rounded-lg border border-slate-300 dark:border-slate-600 bg-white dark:bg-slate-700 text-slate-900 dark:text-white"
                    min={0} max={999}
                  />
                </div>
              </div>

              {/* ── argument_constraint section ── */}
              {policyForm.rule_type === "argument_constraint" && (
                <div className="border border-purple-200 dark:border-purple-800 rounded-lg p-4 space-y-3 bg-purple-50/50 dark:bg-purple-900/10">
                  <h5 className="text-xs font-semibold text-purple-700 dark:text-purple-300 uppercase">Argument Rules</h5>
                  {policyForm.argument_rules.map((rule, idx) => (
                    <div key={idx} className="grid grid-cols-12 gap-2 items-end">
                      <div className="col-span-3">
                        <label className="block text-[10px] text-slate-400 mb-0.5">Parameter</label>
                        <input
                          type="text"
                          value={rule.param}
                          onChange={(e) => {
                            const updated = [...policyForm.argument_rules];
                            updated[idx] = { ...updated[idx], param: e.target.value };
                            setPolicyForm({ ...policyForm, argument_rules: updated });
                          }}
                          className="w-full px-2 py-1.5 text-xs rounded border border-slate-300 dark:border-slate-600 bg-white dark:bg-slate-700 text-slate-900 dark:text-white font-mono"
                          placeholder="query"
                        />
                      </div>
                      <div className="col-span-3">
                        <label className="block text-[10px] text-slate-400 mb-0.5">Operator</label>
                        <select
                          value={rule.operator}
                          onChange={(e) => {
                            const updated = [...policyForm.argument_rules];
                            updated[idx] = { ...updated[idx], operator: e.target.value };
                            setPolicyForm({ ...policyForm, argument_rules: updated });
                          }}
                          aria-label={`Argument rule operator${rule.param ? ` for ${rule.param}` : ""}`}
                          className="w-full px-2 py-1.5 text-xs rounded border border-slate-300 dark:border-slate-600 bg-white dark:bg-slate-700 text-slate-900 dark:text-white"
                        >
                          <option value="no_injection">No Injection</option>
                          <option value="required">Required</option>
                          <option value="equals">Equals</option>
                          <option value="not_equals">Not Equals</option>
                          <option value="contains">Contains</option>
                          <option value="not_contains">Not Contains</option>
                          <option value="in">In Set</option>
                          <option value="not_in">Not In Set</option>
                          <option value="matches_regex">Matches Regex</option>
                          <option value="max_length">Max Length</option>
                          <option value="type_check">Type Check</option>
                        </select>
                      </div>
                      <div className="col-span-3">
                        <label className="block text-[10px] text-slate-400 mb-0.5">Value</label>
                        <input
                          type="text"
                          value={rule.value}
                          onChange={(e) => {
                            const updated = [...policyForm.argument_rules];
                            updated[idx] = { ...updated[idx], value: e.target.value };
                            setPolicyForm({ ...policyForm, argument_rules: updated });
                          }}
                          className="w-full px-2 py-1.5 text-xs rounded border border-slate-300 dark:border-slate-600 bg-white dark:bg-slate-700 text-slate-900 dark:text-white"
                          placeholder={rule.operator === "in" ? "val1,val2" : rule.operator === "max_length" ? "256" : ""}
                          disabled={rule.operator === "required" || rule.operator === "no_injection"}
                        />
                      </div>
                      <div className="col-span-2">
                        <label className="block text-[10px] text-slate-400 mb-0.5">Message</label>
                        <input
                          type="text"
                          value={rule.message}
                          onChange={(e) => {
                            const updated = [...policyForm.argument_rules];
                            updated[idx] = { ...updated[idx], message: e.target.value };
                            setPolicyForm({ ...policyForm, argument_rules: updated });
                          }}
                          className="w-full px-2 py-1.5 text-xs rounded border border-slate-300 dark:border-slate-600 bg-white dark:bg-slate-700 text-slate-900 dark:text-white"
                          placeholder="Error msg"
                        />
                      </div>
                      <div className="col-span-1 flex justify-center">
                        <button
                          type="button"
                          onClick={() => {
                            const updated = policyForm.argument_rules.filter((_, i) => i !== idx);
                            setPolicyForm({ ...policyForm, argument_rules: updated.length ? updated : [{ param: "", operator: "no_injection", value: "", message: "" }] });
                          }}
                          aria-label="Remove argument rule"
                          title="Remove argument rule"
                          className="p-1 rounded hover:bg-red-100 dark:hover:bg-red-900/30 text-red-400"
                        >
                          <X className="w-3.5 h-3.5" />
                        </button>
                      </div>
                    </div>
                  ))}
                  <button
                    type="button"
                    onClick={() => setPolicyForm({ ...policyForm, argument_rules: [...policyForm.argument_rules, { param: "", operator: "no_injection", value: "", message: "" }] })}
                    className="text-xs text-purple-600 dark:text-purple-400 hover:underline"
                  >
                    + Add Rule
                  </button>
                </div>
              )}

              {/* ── context_constraint section ── */}
              {policyForm.rule_type === "context_constraint" && (
                <div className="border border-cyan-200 dark:border-cyan-800 rounded-lg p-4 space-y-3 bg-cyan-50/50 dark:bg-cyan-900/10">
                  <h5 className="text-xs font-semibold text-cyan-700 dark:text-cyan-300 uppercase">Context Conditions</h5>
                  <div className="grid grid-cols-2 gap-4">
                    <div>
                      <label className="block text-[10px] text-slate-400 mb-0.5">Allowed Roles (comma-separated)</label>
                      <input
                        type="text"
                        value={policyForm.context_allowed_roles}
                        onChange={(e) => setPolicyForm({ ...policyForm, context_allowed_roles: e.target.value })}
                        className="w-full px-2 py-1.5 text-xs rounded border border-slate-300 dark:border-slate-600 bg-white dark:bg-slate-700 text-slate-900 dark:text-white"
                        placeholder="admin, editor"
                      />
                    </div>
                    <div>
                      <label className="block text-[10px] text-slate-400 mb-0.5">Denied Roles (comma-separated)</label>
                      <input
                        type="text"
                        value={policyForm.context_denied_roles}
                        onChange={(e) => setPolicyForm({ ...policyForm, context_denied_roles: e.target.value })}
                        className="w-full px-2 py-1.5 text-xs rounded border border-slate-300 dark:border-slate-600 bg-white dark:bg-slate-700 text-slate-900 dark:text-white"
                        placeholder="viewer, guest"
                      />
                    </div>
                  </div>
                  <div className="grid grid-cols-2 gap-4">
                    <div>
                      <label className="block text-[10px] text-slate-400 mb-0.5">Allowed Hours Start (0-23)</label>
                      <input
                        type="number"
                        value={policyForm.context_hours_start}
                        onChange={(e) => setPolicyForm({ ...policyForm, context_hours_start: e.target.value })}
                        className="w-full px-2 py-1.5 text-xs rounded border border-slate-300 dark:border-slate-600 bg-white dark:bg-slate-700 text-slate-900 dark:text-white"
                        min={0} max={23} placeholder="9"
                      />
                    </div>
                    <div>
                      <label className="block text-[10px] text-slate-400 mb-0.5">Allowed Hours End (0-24)</label>
                      <input
                        type="number"
                        value={policyForm.context_hours_end}
                        onChange={(e) => setPolicyForm({ ...policyForm, context_hours_end: e.target.value })}
                        className="w-full px-2 py-1.5 text-xs rounded border border-slate-300 dark:border-slate-600 bg-white dark:bg-slate-700 text-slate-900 dark:text-white"
                        min={0} max={24} placeholder="18"
                      />
                    </div>
                  </div>
                </div>
              )}

              {/* ── rate_limit section ── */}
              {policyForm.rule_type === "rate_limit" && (
                <div className="border border-orange-200 dark:border-orange-800 rounded-lg p-4 space-y-3 bg-orange-50/50 dark:bg-orange-900/10">
                  <h5 className="text-xs font-semibold text-orange-700 dark:text-orange-300 uppercase">Rate Limit Configuration</h5>
                  <div className="grid grid-cols-2 gap-4">
                    <div>
                      <label className="block text-[10px] text-slate-400 mb-0.5">Window (seconds)</label>
                      <input
                        type="number"
                        value={policyForm.rate_limit_window}
                        onChange={(e) => setPolicyForm({ ...policyForm, rate_limit_window: e.target.value })}
                        className="w-full px-2 py-1.5 text-xs rounded border border-slate-300 dark:border-slate-600 bg-white dark:bg-slate-700 text-slate-900 dark:text-white"
                        min={1} placeholder="60"
                      />
                    </div>
                    <div>
                      <label className="block text-[10px] text-slate-400 mb-0.5">Max Calls in Window</label>
                      <input
                        type="number"
                        value={policyForm.rate_limit_max_calls}
                        onChange={(e) => setPolicyForm({ ...policyForm, rate_limit_max_calls: e.target.value })}
                        className="w-full px-2 py-1.5 text-xs rounded border border-slate-300 dark:border-slate-600 bg-white dark:bg-slate-700 text-slate-900 dark:text-white"
                        min={1} placeholder="60"
                      />
                    </div>
                  </div>
                </div>
              )}

              {/* ── risk_policy section ── */}
              {policyForm.rule_type === "risk_policy" && (
                <div className="border border-rose-200 dark:border-rose-800 rounded-lg p-4 space-y-3 bg-rose-50/50 dark:bg-rose-900/10">
                  <h5 className="text-xs font-semibold text-rose-700 dark:text-rose-300 uppercase">Risk Policy Configuration</h5>
                  <div>
                    <label className="block text-[10px] text-slate-400 mb-0.5">Risk Threshold (0-10)</label>
                    <div className="flex items-center gap-3">
                      <input
                        type="range"
                        min="0" max="10" step="0.5"
                        value={policyForm.risk_threshold}
                        onChange={(e) => setPolicyForm({ ...policyForm, risk_threshold: e.target.value })}
                        aria-label="Risk threshold (0-10)"
                        className="flex-1"
                      />
                      <span className="text-sm font-mono text-slate-700 dark:text-slate-300 w-8 text-right">{policyForm.risk_threshold}</span>
                    </div>
                  </div>
                  <div className="grid grid-cols-4 gap-2">
                    {[
                      { key: "risk_action_critical", label: "Critical" },
                      { key: "risk_action_high", label: "High" },
                      { key: "risk_action_medium", label: "Medium" },
                      { key: "risk_action_low", label: "Low" },
                    ].map(({ key, label }) => (
                      <div key={key}>
                        <label className="block text-[10px] text-slate-400 mb-0.5">{label}</label>
                        <select
                          value={policyForm[key]}
                          onChange={(e) => setPolicyForm({ ...policyForm, [key]: e.target.value })}
                          aria-label={`Action for ${label} risk`}
                          className="w-full px-2 py-1.5 text-[11px] rounded border border-slate-300 dark:border-slate-600 bg-white dark:bg-slate-700 text-slate-900 dark:text-white"
                        >
                          <option value="deny">Deny</option>
                          <option value="require_confirmation">Confirm</option>
                          <option value="allow_with_redaction">Redact</option>
                          <option value="audit_only">Audit</option>
                          <option value="allow">Allow</option>
                        </select>
                      </div>
                    ))}
                  </div>
                </div>
              )}

              {/* Redact fields + Max tokens (shared) */}
              <div className="grid grid-cols-2 gap-4">
                <div>
                  <label className="block text-xs font-medium text-slate-500 dark:text-slate-400 mb-1">
                    Redact Fields <span className="text-slate-400">(comma-separated)</span>
                  </label>
                  <input
                    type="text"
                    value={policyForm.redact_fields}
                    onChange={(e) => setPolicyForm({ ...policyForm, redact_fields: e.target.value })}
                    className="w-full px-3 py-2 text-sm rounded-lg border border-slate-300 dark:border-slate-600 bg-white dark:bg-slate-700 text-slate-900 dark:text-white"
                    placeholder="ssn, credit_card"
                  />
                </div>
                <div>
                  <label className="block text-xs font-medium text-slate-500 dark:text-slate-400 mb-1">
                    Max Tokens <span className="text-slate-400">(optional)</span>
                  </label>
                  <input
                    type="number"
                    value={policyForm.max_tokens}
                    onChange={(e) => setPolicyForm({ ...policyForm, max_tokens: e.target.value })}
                    className="w-full px-3 py-2 text-sm rounded-lg border border-slate-300 dark:border-slate-600 bg-white dark:bg-slate-700 text-slate-900 dark:text-white"
                    placeholder="4096"
                  />
                </div>
              </div>

              {/* Active checkbox */}
              <div className="flex items-center gap-2">
                <input
                  type="checkbox"
                  id="policy_enabled"
                  checked={policyForm.is_active}
                  onChange={(e) => setPolicyForm({ ...policyForm, is_active: e.target.checked })}
                  className="rounded border-slate-300 dark:border-slate-600 text-violet-600"
                />
                <label htmlFor="policy_enabled" className="text-sm text-slate-600 dark:text-slate-400">
                  Policy is active immediately
                </label>
              </div>
            </div>

            <div className="px-6 py-4 border-t border-slate-200 dark:border-slate-700 flex justify-end gap-3 shrink-0">
              <button
                onClick={() => setPolicyModalOpen(false)}
                className="px-4 py-2 text-sm rounded-lg border border-slate-300 dark:border-slate-600 text-slate-700 dark:text-slate-300 hover:bg-slate-50 dark:hover:bg-slate-700"
              >
                Cancel
              </button>
              <button
                onClick={createPolicy}
                disabled={saving}
                className="inline-flex items-center gap-1.5 px-4 py-2 text-sm font-medium rounded-lg bg-violet-600 text-white hover:bg-violet-700 disabled:opacity-50"
              >
                {saving ? <Loader2 className="w-4 h-4 animate-spin" /> : <Plus className="w-4 h-4" />}
                Create Policy
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
