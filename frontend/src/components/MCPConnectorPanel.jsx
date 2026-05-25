/**
 * MCPConnectorPanel — ZeroShield MCP integration management.
 *
 * Backend proxy: /api/mcp-connector/*
 */

import { useState, useEffect, useCallback } from "react";
import {
  Server,
  Shield,
  CheckCircle,
  Loader2,
  RefreshCw,
  Plus,
  Trash2,
  X,
  Tag,
  Wrench,
  Play,
  Activity,
  AlertTriangle,
  Copy,
  Eye,
  BarChart3,
  Clock,
  Ban,
  Zap,
  Power,
  Hash,
  ToggleLeft,
  ToggleRight,
  Key,
} from "lucide-react";
import { useAuth } from "../context/AuthContext";
import { toAbsoluteGatewayUrl, resolveGatewayBaseUrl } from "../utils/environmentUrls";
import { PolicyManagementPanel } from "./PolicyManagementPanel";

/* ────────────── helpers ────────────── */

const TRANSPORT_OPTIONS = [
  { value: "streamable-http", label: "Streamable HTTP", supported: true },
  { value: "sse", label: "SSE", supported: true },
  { value: "stdio", label: "Stdio", supported: true },
  { value: "websocket", label: "WebSocket", supported: true },
];

const SUPPORTED_TRANSPORT_VALUES = new Set(
  TRANSPORT_OPTIONS.filter((opt) => opt.supported).map((opt) => opt.value)
);

const AUTH_OPTIONS = [
  { value: "none", label: "None" },
  { value: "bearer", label: "Bearer Token" },
  { value: "basic", label: "Basic Auth" },
  { value: "authheaders", label: "Custom Header" },
  { value: "query_param", label: "Query Parameter" },
];

const MCP_PRESETS = [
  {
    name: "GitHub MCP",
    url: "https://api.githubcopilot.com/mcp/",
    transport: "streamable-http",
    description: "GitHub Copilot MCP endpoint",
    requiresAuth: true,
    suggestedAuthType: "bearer",
  },
  {
    name: "Linear MCP",
    transport: "stdio",
    description: "Linear project management via mcp-remote (OAuth handled automatically)",
    command: "npx",
    args: ["-y", "mcp-remote", "https://mcp.linear.app/mcp"],
  },
  { name: "Context7 MCP", url: "https://mcp.context7.com/mcp", transport: "streamable-http", description: "Context7 docs MCP endpoint" },

  { name: "Playwright MCP", transport: "stdio", description: "Browser automation via Playwright MCP", command: "npx", args: ["-y", "@playwright/mcp@latest"] },
  { name: "Semgrep MCP", transport: "stdio", description: "Code security scanning via Semgrep MCP", command: "npx", args: ["-y", "mcp-server-semgrep"] },
];

const makeEmptyAddForm = () => ({
  name: "",
  url: "",
  transport: "streamable-http",
  command: "",
  args: [],
  env_vars: {},
  description: "",
  auth_type: "none",
  auth_token: "",
  auth_username: "",
  auth_password: "",
  auth_header_key: "",
  auth_header_value: "",
  auth_headers: [{ key: "", value: "" }],
  auth_query_param_key: "",
  auth_query_param_value: "",
});

const normalizeAuthHeaders = (headers) => {
  if (!Array.isArray(headers)) return [];

  const deduped = [];
  const seen = new Set();
  headers.forEach((h) => {
    const key = (h?.key || "").trim();
    const value = h?.value;
    if (!key || value == null || value === "") return;
    const keyNorm = key.toLowerCase();
    if (seen.has(keyNorm)) return;
    seen.add(keyNorm);
    deduped.push({ key, value: String(value) });
  });
  return deduped;
};

const buildServerPayload = (form) => {
  const payload = {
    name: form.name,
    url: form.url || "",
    transport: form.transport,
    description: form.description,
    auth_type: form.auth_type,
    auth_token: form.auth_token,
    auth_username: form.auth_username,
    auth_password: form.auth_password,
    auth_query_param_key: form.auth_query_param_key,
    auth_query_param_value: form.auth_query_param_value,
  };

  // Include stdio-specific fields
  if (form.transport === "stdio") {
    payload.command = form.command || "";
    payload.args = Array.isArray(form.args) ? form.args : [];
    payload.env_vars = form.env_vars && typeof form.env_vars === "object" ? form.env_vars : {};
  }

  if (form.auth_type === "authheaders") {
    const headers = normalizeAuthHeaders(form.auth_headers);
    payload.auth_headers = headers;
    if (headers.length > 0) {
      payload.auth_header_key = headers[0].key;
      payload.auth_header_value = headers[0].value;
    }
  }

  return payload;
};

const getToolSchema = (tool) => tool?.inputSchema || tool?.input_schema || null;

const makeExecuteToolKey = (tool) => `${tool?.server_slug || ""}::${tool?.name || ""}`;

const buildExampleFromSchema = (schema, depth = 0) => {
  if (!schema || depth > 3) return {};

  if (schema.example !== undefined) return schema.example;
  if (Array.isArray(schema.examples) && schema.examples.length > 0) return schema.examples[0];
  if (schema.default !== undefined) return schema.default;

  const schemaType = Array.isArray(schema.type) ? schema.type[0] : schema.type;
  if (schema.enum?.length) return schema.enum[0];

  if (schemaType === "object" || schema.properties) {
    return Object.entries(schema.properties || {}).reduce((acc, [key, value]) => {
      acc[key] = buildExampleFromSchema(value, depth + 1);
      return acc;
    }, {});
  }

  if (schemaType === "array") {
    return schema.items ? [buildExampleFromSchema(schema.items, depth + 1)] : [];
  }

  if (schemaType === "number" || schemaType === "integer") return 0;
  if (schemaType === "boolean") return false;
  return "";
};

const formatExecutionError = (body, status) => {
  const parts = [];
  const primary = body?.detail || body?.error || `HTTP ${status}`;
  if (primary) parts.push(primary);
  if (body?.reason) parts.push(`Reason: ${body.reason}`);
  if (Array.isArray(body?.matched_policies) && body.matched_policies.length) {
    parts.push(`Policies: ${body.matched_policies.join(", ")}`);
  }
  if (Array.isArray(body?.validation_errors) && body.validation_errors.length) {
    const sample = body.validation_errors.slice(0, 2).map((entry) => `${entry.field}: ${entry.message}`);
    parts.push(`Validation: ${sample.join("; ")}`);
  }
  if (body?.request_id) parts.push(`Request: ${body.request_id}`);
  return parts.join(" | ");
};

const extractExecutionPreview = (payload) => {
  const result = payload?.result;
  if (!result) return "";

  if (Array.isArray(result?.content)) {
    return result.content
      .map((item) => (item?.text ? String(item.text) : ""))
      .filter(Boolean)
      .join("\n\n");
  }

  if (typeof result === "string") return result;
  if (typeof result?.text === "string") return result.text;
  return "";
};

function StatusDot({ status }) {
  const meta = {
    healthy: { color: "bg-emerald-500", ring: "ring-emerald-300/40", label: "Healthy" },
    unhealthy: { color: "bg-amber-500", ring: "ring-amber-300/40", label: "Degraded" },
    unreachable: { color: "bg-red-500", ring: "ring-red-300/40", label: "Unreachable" },
    not_configured: { color: "bg-slate-400", ring: "ring-slate-300/40", label: "Not configured" },
  }[status] || { color: "bg-slate-400", ring: "ring-slate-300/40", label: status || "Unknown" };
  return (
    <span
      role="img"
      aria-label={`Status: ${meta.label}`}
      title={meta.label}
      className={`inline-block w-2.5 h-2.5 rounded-full ring-2 ${meta.color} ${meta.ring}`}
    />
  );
}

const CONNECTION_STATUS_STYLES = {
  connected: "bg-emerald-100 text-emerald-700 dark:bg-emerald-800/30 dark:text-emerald-300",
  disconnected: "bg-red-100 text-red-700 dark:bg-red-800/30 dark:text-red-300",
  unknown: "bg-slate-100 text-slate-600 dark:bg-slate-700 dark:text-slate-300",
  syncing: "bg-blue-100 text-blue-700 dark:bg-blue-800/30 dark:text-blue-300",
};

const RISK_LEVEL_STYLES = {
  low: "bg-emerald-100 text-emerald-700 dark:bg-emerald-800/30 dark:text-emerald-300",
  medium: "bg-amber-100 text-amber-700 dark:bg-amber-800/30 dark:text-amber-300",
  high: "bg-orange-100 text-orange-700 dark:bg-orange-800/30 dark:text-orange-300",
  critical: "bg-red-100 text-red-700 dark:bg-red-800/30 dark:text-red-300",
};

const SENSITIVITY_STYLES = {
  low: "bg-slate-100 text-slate-600 dark:bg-slate-700 dark:text-slate-300",
  medium: "bg-amber-100 text-amber-700 dark:bg-amber-800/30 dark:text-amber-300",
  high: "bg-orange-100 text-orange-700 dark:bg-orange-800/30 dark:text-orange-300",
  critical: "bg-red-100 text-red-700 dark:bg-red-800/30 dark:text-red-300",
};

const TABS = [
  { id: "servers", label: "MCP Servers", icon: Server },
  { id: "tools", label: "Tool Discovery", icon: Wrench },
  { id: "execute", label: "Tool Execution", icon: Play },
  { id: "protection", label: "MCP Protection", icon: Shield },
  { id: "observability", label: "Observability", icon: BarChart3 },
  { id: "health", label: "Services Health", icon: Activity },
];

/* ════════════════════════════════════════════════════ */
export function MCPConnectorPanel() {
  const { fetchWithAuth, user } = useAuth();

  const [tab, setTab] = useState("servers");
  const [protectionView, setProtectionView] = useState("policies");
  const [obsHours, setObsHours] = useState(24); // 0 = all-time, 1/24/168/720
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);

  /* ── servers ── */
  const [servers, setServers] = useState([]);
  const [addOpen, setAddOpen] = useState(false);
  const [addForm, setAddForm] = useState(makeEmptyAddForm());
  const [addSaving, setAddSaving] = useState(false);

  /* ── OAuth upstream authorization ── */
  const [oauthBusy, setOauthBusy] = useState(null); // server id currently authorizing

  /* ── tools ── */
  const [tools, setTools] = useState([]);
  const [executeServerSlug, setExecuteServerSlug] = useState("");
  const [executeToolKey, setExecuteToolKey] = useState("");
  const [executeArguments, setExecuteArguments] = useState("{}");
  const [executeResult, setExecuteResult] = useState(null);
  const [executeBusy, setExecuteBusy] = useState(false);

  /* ── guardrails ── */
  const [guardrails, setGuardrails] = useState([]);
  const [grOpen, setGrOpen] = useState(false);
  const [grForm, setGrForm] = useState({
    name: "", description: "",
    input_policy: { enabled: true, pii_redaction: true, block: ["injection_attack"] },
    output_policy: { enabled: true, block: ["policy_violation"] },
    is_default: false,
  });
  const [grSaving, setGrSaving] = useState(false);

  /* ── MCP policy server filter ── */
  const [policyServerFilter, setPolicyServerFilter] = useState("");

  /* ── health ── */
  const [health, setHealth] = useState(null);

  /* ── observability ── */
  const [events, setEvents] = useState([]);
  const [eventSummary, setEventSummary] = useState(null);

  /* ── server tools (per-server control) ── */
  const [serverToolsMap, setServerToolsMap] = useState({});
  const [expandedServer, setExpandedServer] = useState(null);
  const [copiedEndpoint, setCopiedEndpoint] = useState(null);
  const [syncingServer, setSyncingServer] = useState(null);

  /* ── org gateway key (auto-provisioned for MCP) ── */
  const [orgGatewayKey, setOrgGatewayKey] = useState(null); // { has_gateway_key, prefix, key, ... }

  /* ────────── loaders ────────── */

  const loadServers = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await fetchWithAuth("/api/mcp-connector/servers/");
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      setServers(Array.isArray(data) ? data : data.results ?? []);
    } catch (e) {
      setError(`Failed to load servers: ${e.message}`);
    } finally {
      setLoading(false);
    }
  }, [fetchWithAuth]);

  const executableTools = (Array.isArray(tools) ? tools : [])
    .filter((tool) => tool?.enabled !== false)
    .filter((tool) => !executeServerSlug || tool.server_slug === executeServerSlug)
    .sort((left, right) => {
      const leftLabel = `${left.server_name || left.server_slug || ""}/${left.name || ""}`;
      const rightLabel = `${right.server_name || right.server_slug || ""}/${right.name || ""}`;
      return leftLabel.localeCompare(rightLabel);
    });

  const selectedExecuteTool = executableTools.find((tool) => makeExecuteToolKey(tool) === executeToolKey) || null;

  useEffect(() => {
    if (!selectedExecuteTool && executeToolKey) {
      setExecuteToolKey("");
    }
  }, [selectedExecuteTool, executeToolKey]);

  const loadTools = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await fetchWithAuth("/api/mcp-connector/tools/");
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      setTools(Array.isArray(data) ? data : data.results ?? data.tools ?? []);
    } catch (e) {
      setError(`Failed to load tools: ${e.message}`);
    } finally {
      setLoading(false);
    }
  }, [fetchWithAuth]);

  const loadGuardrails = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await fetchWithAuth("/api/mcp-connector/guardrails/");
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      setGuardrails(Array.isArray(data) ? data : data.results ?? []);
    } catch (e) {
      setError(`Failed to load guardrails: ${e.message}`);
    } finally {
      setLoading(false);
    }
  }, [fetchWithAuth]);

  const loadHealth = useCallback(async () => {
    try {
      const res = await fetchWithAuth("/api/mcp-connector/health/");
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      setHealth(await res.json());
    } catch {
      setHealth(null);
    }
  }, [fetchWithAuth]);

  const loadEvents = useCallback(async (hours = obsHours) => {
    try {
      const qs = hours > 0 ? `?limit=500&hours=${hours}` : `?limit=500`;
      const res = await fetchWithAuth(`/api/mcp-connector/events/${qs}`);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      setEvents(Array.isArray(data) ? data : data.results ?? []);
    } catch { setEvents([]); }
  }, [fetchWithAuth, obsHours]);

  const loadEventSummary = useCallback(async (hours = obsHours) => {
    try {
      const qs = hours > 0 ? `?hours=${hours}` : ``;
      const res = await fetchWithAuth(`/api/mcp-connector/events/summary/${qs}`);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      setEventSummary(await res.json());
    } catch { setEventSummary(null); }
  }, [fetchWithAuth, obsHours]);

  const loadServerTools = useCallback(async (serverId) => {
    try {
      const res = await fetchWithAuth(`/api/mcp-connector/servers/${serverId}/tools/`);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      setServerToolsMap((prev) => ({ ...prev, [serverId]: Array.isArray(data) ? data : data.results ?? [] }));
    } catch { /* silent */ }
  }, [fetchWithAuth]);

  const loadOrgGatewayKey = useCallback(async () => {
    try {
      const res = await fetchWithAuth("/api/mcp-connector/org-gateway-key/");
      if (res.ok) setOrgGatewayKey(await res.json());
    } catch { /* silent */ }
  }, [fetchWithAuth]);

  const provisionGatewayKey = useCallback(async () => {
    try {
      const res = await fetchWithAuth("/api/mcp-connector/org-gateway-key/", { method: "POST" });
      if (res.ok) {
        const data = await res.json();
        setOrgGatewayKey(data);
        return data;
      }
    } catch { /* silent */ }
    return null;
  }, [fetchWithAuth]);

  /* ── tab-switched loaders ── */
  useEffect(() => {
    if (tab === "servers") { loadServers(); loadOrgGatewayKey(); }
    if (tab === "tools") loadTools();
    if (tab === "execute") loadTools();
    if (tab === "protection") { loadServers(); loadGuardrails(); }
    if (tab === "observability") { loadEvents(); loadEventSummary(); }
    if (tab === "health") loadHealth();
  }, [tab, loadServers, loadTools, loadGuardrails, loadHealth, loadEvents, loadEventSummary, loadOrgGatewayKey]);

  /* ────────── actions ────────── */

  const addServer = async () => {
    setAddSaving(true);
    setError(null);
    try {
      const payload = buildServerPayload(addForm);
      const res = await fetchWithAuth("/api/mcp-connector/servers/", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body.detail || body.error || `HTTP ${res.status}`);
      }
      const data = await res.json().catch(() => ({}));
      // Capture auto-provisioned gateway key from response
      if (data.default_gateway_key) {
        setOrgGatewayKey({
          has_gateway_key: true,
          prefix: data.default_gateway_key_prefix,
          key: data.default_gateway_key,
          name: `MCP Default Key`,
        });
      }
      setAddOpen(false);
      setAddForm(makeEmptyAddForm());
      await loadServers();
    } catch (e) {
      setError(`Add server failed: ${e.message}`);
    } finally {
      setAddSaving(false);
    }
  };

  const deleteServerById = async (pk) => {
    if (!window.confirm("Delete this MCP server registration?")) return;
    try {
      const res = await fetchWithAuth(`/api/mcp-connector/servers/${pk}/`, { method: "DELETE" });
      if (!res.ok && res.status !== 204) throw new Error(`HTTP ${res.status}`);
      await loadServers();
    } catch (e) {
      setError(`Delete failed: ${e.message}`);
    }
  };

  const addGuardrail = async () => {
    setGrSaving(true);
    setError(null);
    try {
      const res = await fetchWithAuth("/api/mcp-connector/guardrails/", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(grForm),
      });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body.detail || body.error || `HTTP ${res.status}`);
      }
      setGrOpen(false);
      setGrForm({
        name: "", description: "",
        input_policy: { enabled: true, pii_redaction: true, block: ["injection_attack"] },
        output_policy: { enabled: true, block: ["policy_violation"] },
        is_default: false,
      });
      await loadGuardrails();
    } catch (e) {
      setError(`Add guardrail failed: ${e.message}`);
    } finally {
      setGrSaving(false);
    }
  };

  const registerPreset = async (preset) => {
    if (preset.requiresAuth) {
      setAddForm({
        ...makeEmptyAddForm(),
        name: preset.name,
        url: preset.url || "",
        transport: preset.transport,
        description: preset.description,
        command: preset.command || "",
        args: preset.args || [],
        auth_type: preset.suggestedAuthType || "bearer",
      });
      setAddOpen(true);
      setError(`${preset.name} requires credentials. Add auth details, then register from the modal.`);
      return;
    }

    setAddSaving(true);
    setError(null);
    try {
      const payload = {
        name: preset.name,
        url: preset.url || "",
        transport: preset.transport,
        description: preset.description,
      };
      if (preset.transport === "stdio") {
        payload.command = preset.command || "";
        payload.args = preset.args || [];
      }
      const res = await fetchWithAuth("/api/mcp-connector/servers/", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body.detail || body.error || `HTTP ${res.status}`);
      }
      await loadServers();
    } catch (e) {
      setError(`Preset registration failed: ${e.message}`);
    } finally {
      setAddSaving(false);
    }
  };

  const syncServerTools = async (serverId) => {
    setSyncingServer(serverId);
    try {
      const res = await fetchWithAuth(`/api/mcp-connector/servers/${serverId}/tools/`, { method: "POST" });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      await loadServerTools(serverId);
      await loadServers();
    } catch (e) {
      setError(`Sync tools failed: ${e.message}`);
    } finally {
      setSyncingServer(null);
    }
  };

  const toggleToolEnabled = async (serverId, toolName, currentEnabled) => {
    try {
      const res = await fetchWithAuth(`/api/mcp-connector/servers/${serverId}/tools/${encodeURIComponent(toolName)}/`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ enabled: !currentEnabled }),
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      await loadServerTools(serverId);
    } catch (e) {
      setError(`Toggle tool failed: ${e.message}`);
    }
  };

  const copyEndpoint = (endpoint) => {
    navigator.clipboard.writeText(endpoint).then(() => {
      setCopiedEndpoint(endpoint);
      setTimeout(() => setCopiedEndpoint(null), 2000);
    });
  };

  /** Build a ready-to-paste MCP server config entry for VS Code / Cursor mcp.json. */
  const buildMcpConfigSnippet = (srv) => {
    const url = toAbsoluteGatewayUrl(srv.gateway_endpoint);
    const slug = srv.server_slug || srv.name?.toLowerCase().replace(/\s+/g, "-") || "mcp-server";
    const key = `zeroshield-${slug}`;
    // Use the auto-provisioned key if available, otherwise VS Code input prompt
    const authValue = orgGatewayKey?.key
      ? `Bearer ${orgGatewayKey.key}`
      : "Bearer ${input:ZEROSHIELD_GW_API_KEY}";
    const entry = {
      url,
      type: "http",
      headers: {
        Authorization: authValue,
      },
    };
    // Output as "key": { ... } so the user can paste into the servers section of mcp.json
    return `${JSON.stringify(key)}: ${JSON.stringify(entry, null, 2)}`;
  };

  const copyMcpConfig = (srv) => {
    const snippet = buildMcpConfigSnippet(srv);
    navigator.clipboard.writeText(snippet).then(() => {
      setCopiedEndpoint(`config:${srv.id}`);
      setTimeout(() => setCopiedEndpoint(null), 3000);
    });
  };

  /* ── Upstream OAuth helpers ── */

  /** Check whether a server uses mcp-remote (needs OAuth authorization). */
  const serverNeedsOAuth = (srv) =>
    srv.transport === "stdio" &&
    Array.isArray(srv.args) &&
    srv.args.some((a) => a === "mcp-remote" || (typeof a === "string" && a.endsWith("/mcp-remote")));

  /** Extract the MCP server URL from mcp-remote args. */
  const extractMcpRemoteUrl = (args) => {
    if (!Array.isArray(args)) return null;
    for (let i = 0; i < args.length; i++) {
      if (args[i] === "mcp-remote" || (typeof args[i] === "string" && args[i].endsWith("/mcp-remote"))) {
        for (let j = i + 1; j < args.length; j++) {
          if (!args[j].startsWith("-")) return args[j];
        }
      }
    }
    return null;
  };

  /** Start OAuth flow for an upstream MCP server (opens popup). */
  const startOAuth = async (srv) => {
    const orgSlug = user?.organization?.slug;
    if (!orgSlug) {
      setError("Cannot determine organization. Please reload the page.");
      return;
    }
    const serverUrl = extractMcpRemoteUrl(srv.args);
    if (!serverUrl) {
      setError("Cannot determine MCP server URL from args.");
      return;
    }
    
    // UX-02 FIX: Pre-open popup SYNCHRONOUSLY before async fetch to avoid popup blocker.
    // Browser will block window.open() after await unless it's in the same call stack as user click.
    const popup = window.open("about:blank", "mcp-oauth", "width=600,height=700");
    if (!popup) {
      setError("Popup blocked. Please allow popups for this site and try again.");
      return;
    }
    
    setOauthBusy(srv.id);
    setError(null);
    try {
      const gwBase = resolveGatewayBaseUrl();
      if (!gwBase) {
        popup.close();
        throw new Error("Gateway URL is not configured.");
      }

      const res = await fetch(`${gwBase}/gateway/${orgSlug}/mcp/${srv.server_slug}/oauth/start`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ server_url: serverUrl }),
      });
      const data = await res.clone().json().catch(async () => ({
        error: (await res.text().catch(() => "")) || null,
      }));
      if (!res.ok) {
        popup.close();
        throw new Error(data.error || `HTTP ${res.status}`);
      }

      // Navigate the pre-opened popup to OAuth URL
      popup.location.href = data.authorize_url;
    } catch (e) {
      if (!popup.closed) popup.close();
      const rawMessage = e?.message || "Unknown error";
      const message = /failed to fetch|about:blank|networkerror|load failed/i.test(rawMessage)
        ? "Browser blocked the gateway request. Check gateway CORS or network configuration."
        : rawMessage;
      setError(`OAuth start failed: ${message}`);
    } finally {
      setOauthBusy(null);
    }
  };

  // Listen for OAuth popup completion
  useEffect(() => {
    const handler = (event) => {
      if (event.data?.type === "mcp-oauth-complete") {
        loadServers();
        // Auto-sync tools after successful OAuth
        const srv = servers.find((s) => s.server_slug === event.data.server);
        if (srv) {
          setTimeout(() => syncServerTools(srv.id), 1000);
        }
      }
    };
    window.addEventListener("message", handler);
    return () => window.removeEventListener("message", handler);
  }, [servers, loadServers]); // eslint-disable-line react-hooks/exhaustive-deps

  const executeTool = async () => {
    if (!selectedExecuteTool) {
      setError("Select a server-scoped tool before execution.");
      return;
    }

    setExecuteBusy(true);
    setError(null);
    setExecuteResult(null);
    try {
      let parsedArgs = {};
      if (executeArguments.trim()) {
        try {
          parsedArgs = JSON.parse(executeArguments);
        } catch (jsonErr) {
          setError(`Invalid JSON arguments: ${jsonErr.message}`);
          setExecuteBusy(false);
          return;
        }
      }
      const res = await fetchWithAuth("/api/mcp-connector/tools/call/", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          name: selectedExecuteTool.name,
          arguments: parsedArgs,
          server_slug: selectedExecuteTool.server_slug || executeServerSlug,
        }),
      });
      const body = await res.json().catch(() => ({}));
      if (!res.ok) {
        setExecuteResult({ ...body, error: true, status: res.status });
        throw new Error(formatExecutionError(body, res.status));
      }
      setExecuteResult(body);
    } catch (e) {
      setError(`Tool execution failed: ${e.message}`);
    } finally {
      setExecuteBusy(false);
    }
  };

  const deleteGuardrail = async (pk) => {
    if (!window.confirm("Delete this guardrail profile?")) return;
    try {
      const res = await fetchWithAuth(`/api/mcp-connector/guardrails/${pk}/`, { method: "DELETE" });
      if (!res.ok && res.status !== 204) throw new Error(`HTTP ${res.status}`);
      await loadGuardrails();
    } catch (e) {
      setError(`Delete failed: ${e.message}`);
    }
  };

  /* ────────── renderers ────────── */

  const renderServers = () => (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <p className="text-sm text-slate-500 dark:text-slate-400">
          MCP servers managed by the ZeroShield MCP control plane.
          Organizations can connect their own or public MCP servers here.
        </p>
        <div className="flex gap-2 flex-wrap justify-end">
          {MCP_PRESETS.map((preset) => (
            <button
              key={preset.name}
              onClick={() => registerPreset(preset)}
              disabled={addSaving}
              className="px-2.5 py-1.5 text-xs rounded border border-slate-200 dark:border-slate-700 text-slate-700 dark:text-slate-300 hover:bg-slate-50 dark:hover:bg-slate-700 disabled:opacity-50"
              title={`Quick register ${preset.name}`}
            >
              {preset.name}
              {preset.requiresAuth && <span className="ml-1 text-[10px] text-amber-600">(auth)</span>}
            </button>
          ))}
          <button onClick={loadServers} className="btn-icon" title="Refresh">
            <RefreshCw className="w-4 h-4" />
          </button>
          <button onClick={() => setAddOpen(true)} className="btn-primary text-sm flex items-center gap-1">
            <Plus className="w-4 h-4" /> Register Server
          </button>
        </div>
      </div>

      {servers.length === 0 && !loading && (
        <div className="text-center py-12 text-slate-400 dark:text-slate-500">
          <Server className="w-10 h-10 mx-auto mb-3 opacity-40" />
          <p>No MCP servers registered yet.</p>
          <p className="text-xs mt-1">Click &quot;Register Server&quot; to connect an MCP server.</p>
        </div>
      )}

      {/* Gateway API Key banner */}
      {orgGatewayKey?.has_gateway_key ? (
        <div className="flex items-center gap-3 p-3 rounded-lg border border-emerald-200 dark:border-emerald-800 bg-emerald-50 dark:bg-emerald-900/20">
          <Key className="w-4 h-4 text-emerald-600 dark:text-emerald-400 shrink-0" />
          <div className="flex-1 min-w-0">
            <p className="text-xs font-medium text-emerald-800 dark:text-emerald-300">
              Gateway API Key Active
              {orgGatewayKey.prefix && (
                <span className="ml-2 font-mono text-emerald-600 dark:text-emerald-400">
                  {orgGatewayKey.prefix}...
                </span>
              )}
            </p>
            {orgGatewayKey.key && (
              <div className="mt-1 flex items-center gap-2">
                <code className="text-[10px] bg-emerald-100 dark:bg-emerald-900/40 px-2 py-0.5 rounded font-mono text-emerald-700 dark:text-emerald-300 break-all">
                  {orgGatewayKey.key}
                </code>
                <button
                  onClick={() => {
                    navigator.clipboard.writeText(orgGatewayKey.key);
                    setCopiedEndpoint("gw-key");
                    setTimeout(() => setCopiedEndpoint(null), 3000);
                  }}
                  className="text-emerald-500 hover:text-emerald-700 transition-colors"
                  title="Copy gateway API key"
                >
                  {copiedEndpoint === "gw-key"
                    ? <CheckCircle className="w-3.5 h-3.5" />
                    : <Copy className="w-3.5 h-3.5" />}
                </button>
              </div>
            )}
            {orgGatewayKey.key && (
              <p className="text-[10px] text-amber-600 dark:text-amber-400 mt-1">
                <AlertTriangle className="w-3 h-3 inline mr-1" />
                Copy this key now — it won&apos;t be shown again.
              </p>
            )}
          </div>
        </div>
      ) : orgGatewayKey && !orgGatewayKey.has_gateway_key ? (
        <div className="flex items-center gap-3 p-3 rounded-lg border border-amber-200 dark:border-amber-800 bg-amber-50 dark:bg-amber-900/20">
          <Key className="w-4 h-4 text-amber-600 dark:text-amber-400 shrink-0" />
          <div className="flex-1">
            <p className="text-xs text-amber-800 dark:text-amber-300">
              No gateway API key provisioned. MCP Config snippets will use a placeholder.
            </p>
          </div>
          <button
            onClick={provisionGatewayKey}
            className="text-xs px-3 py-1.5 rounded bg-amber-600 text-white hover:bg-amber-700 font-medium shrink-0"
          >
            Provision Key
          </button>
        </div>
      ) : null}

      <div className="grid gap-3">
        {servers.map((srv) => (
          <div key={srv.id} className="bg-white dark:bg-slate-800 border border-slate-200 dark:border-slate-700 rounded-lg p-4">
            <div className="flex items-start justify-between">
              <div className="flex-1 min-w-0">
                <h4 className="font-medium text-slate-900 dark:text-white flex items-center gap-2 flex-wrap">
                  <Server className="w-4 h-4 text-blue-500 shrink-0" />
                  {srv.name}
                  {srv.is_active && <CheckCircle className="w-3.5 h-3.5 text-emerald-500 shrink-0" />}
                  {/* Connection status chip */}
                  <span className={`text-[10px] px-1.5 py-0.5 rounded font-medium ${CONNECTION_STATUS_STYLES[srv.connection_status] || CONNECTION_STATUS_STYLES.unknown}`}>
                    {srv.connection_status || "unknown"}
                  </span>
                  {/* Risk level badge */}
                  {srv.risk_level && srv.risk_level !== "low" && (
                    <span className={`text-[10px] px-1.5 py-0.5 rounded font-medium ${RISK_LEVEL_STYLES[srv.risk_level] || RISK_LEVEL_STYLES.low}`}>
                      {srv.risk_level} risk
                    </span>
                  )}
                </h4>
                <p className="text-xs text-slate-500 dark:text-slate-400 mt-1">{srv.url}</p>
                {srv.description && <p className="text-xs text-slate-400 mt-0.5">{srv.description}</p>}
                {/* Tools count + last sync */}
                <div className="flex items-center gap-3 mt-2 flex-wrap">
                  {srv.tools_count != null && (
                    <span className="text-[10px] text-slate-500 flex items-center gap-1">
                      <Wrench className="w-3 h-3" /> {srv.tools_count} tools
                    </span>
                  )}
                  {srv.last_sync_at && (
                    <span className="text-[10px] text-slate-500 flex items-center gap-1">
                      <Clock className="w-3 h-3" /> Synced {new Date(srv.last_sync_at).toLocaleString()}
                    </span>
                  )}
                </div>
                {/* Gateway endpoint with copy */}
                {srv.gateway_endpoint && (
                  <div className="mt-2 space-y-1">
                    <div className="flex items-center gap-1.5">
                      <code className="text-[10px] bg-slate-100 dark:bg-slate-700 px-2 py-0.5 rounded font-mono text-slate-600 dark:text-slate-300 break-all">
                        {toAbsoluteGatewayUrl(srv.gateway_endpoint)}
                      </code>
                      <button
                        onClick={() => copyEndpoint(toAbsoluteGatewayUrl(srv.gateway_endpoint))}
                        className="text-slate-400 hover:text-blue-500 transition-colors"
                        title="Copy gateway URL only"
                      >
                        {copiedEndpoint === toAbsoluteGatewayUrl(srv.gateway_endpoint)
                          ? <CheckCircle className="w-3.5 h-3.5 text-emerald-500" />
                          : <Copy className="w-3.5 h-3.5" />}
                      </button>
                    </div>
                    <button
                      onClick={() => copyMcpConfig(srv)}
                      className="inline-flex items-center gap-1 text-[10px] font-medium text-blue-600 dark:text-blue-400 hover:text-blue-700 dark:hover:text-blue-300 bg-blue-50 dark:bg-blue-900/30 px-2 py-0.5 rounded transition-colors"
                      title="Copy full MCP config JSON with auth headers (paste into VS Code / Cursor mcp.json)"
                    >
                      {copiedEndpoint === `config:${srv.id}`
                        ? <><CheckCircle className="w-3 h-3 text-emerald-500" /> Config Copied!</>
                        : <><Copy className="w-3 h-3" /> {orgGatewayKey?.key ? "Copy MCP Config" : "Copy MCP Config (key placeholder)"}</>}
                    </button>
                  </div>
                )}
              </div>
              <div className="flex items-center gap-2 shrink-0 ml-3">
                <span className="text-xs px-2 py-0.5 rounded bg-slate-100 dark:bg-slate-700 text-slate-600 dark:text-slate-300">
                  {srv.transport}
                </span>
                {/* OAuth Authorize button for mcp-remote servers */}
                {serverNeedsOAuth(srv) && (
                  <button
                    onClick={() => startOAuth(srv)}
                    disabled={oauthBusy === srv.id}
                    className="text-xs px-2 py-1 rounded bg-indigo-50 dark:bg-indigo-900/30 text-indigo-600 dark:text-indigo-400 hover:bg-indigo-100 dark:hover:bg-indigo-900/50 disabled:opacity-50 flex items-center gap-1 font-medium"
                    title="Authorize OAuth — opens popup for upstream provider login"
                  >
                    {oauthBusy === srv.id
                      ? <Loader2 className="w-3 h-3 animate-spin" />
                      : <Shield className="w-3 h-3" />}
                    Authorize
                  </button>
                )}
                <button
                  onClick={() => syncServerTools(srv.id)}
                  disabled={syncingServer === srv.id}
                  className="text-blue-500 hover:text-blue-700 disabled:opacity-50"
                  title="Sync tools from server"
                >
                  {syncingServer === srv.id
                    ? <Loader2 className="w-4 h-4 animate-spin" />
                    : <RefreshCw className="w-4 h-4" />}
                </button>
                <button
                  onClick={() => {
                    if (expandedServer === srv.id) {
                      setExpandedServer(null);
                    } else {
                      setExpandedServer(srv.id);
                      if (!serverToolsMap[srv.id]) loadServerTools(srv.id);
                    }
                  }}
                  className="text-slate-500 hover:text-slate-700 dark:text-slate-400 dark:hover:text-slate-200"
                  title="View tool controls"
                >
                  <Eye className="w-4 h-4" />
                </button>
                <button onClick={() => deleteServerById(srv.id)} className="text-red-500 hover:text-red-700" title="Delete">
                  <Trash2 className="w-4 h-4" />
                </button>
              </div>
            </div>
            {/* Expanded tool controls */}
            {expandedServer === srv.id && (
              <div className="mt-3 border-t border-slate-200 dark:border-slate-700 pt-3">
                <div className="flex items-center justify-between mb-2">
                  <span className="text-xs font-medium text-slate-600 dark:text-slate-300">Tool Controls</span>
                  <button
                    onClick={() => syncServerTools(srv.id)}
                    disabled={syncingServer === srv.id}
                    className="text-xs text-blue-500 hover:text-blue-700 flex items-center gap-1"
                  >
                    {syncingServer === srv.id ? <Loader2 className="w-3 h-3 animate-spin" /> : <RefreshCw className="w-3 h-3" />}
                    Sync
                  </button>
                </div>
                {(!serverToolsMap[srv.id] || serverToolsMap[srv.id].length === 0) && (
                  <p className="text-[10px] text-slate-400 text-center py-3">
                    No tools synced. Click &quot;Sync&quot; to discover tools from this server.
                  </p>
                )}
                <div className="space-y-1.5">
                  {(serverToolsMap[srv.id] || []).map((tool) => (
                    <div key={tool.tool_name} className="flex items-center justify-between bg-slate-50 dark:bg-slate-900 rounded px-3 py-2">
                      <div className="flex items-center gap-2 min-w-0">
                        <button
                          onClick={() => toggleToolEnabled(srv.id, tool.tool_name, tool.enabled)}
                          className="shrink-0"
                          title={tool.enabled ? "Disable tool" : "Enable tool"}
                        >
                          {tool.enabled
                            ? <ToggleRight className="w-5 h-5 text-emerald-500" />
                            : <ToggleLeft className="w-5 h-5 text-slate-400" />}
                        </button>
                        <span className={`text-xs font-mono truncate ${tool.enabled ? "text-slate-900 dark:text-white" : "text-slate-400 line-through"}`}>
                          {tool.tool_name}
                        </span>
                      </div>
                      <span className={`text-[10px] px-1.5 py-0.5 rounded font-medium shrink-0 ${SENSITIVITY_STYLES[tool.sensitivity] || SENSITIVITY_STYLES.low}`}>
                        {tool.sensitivity}
                      </span>
                    </div>
                  ))}
                </div>
              </div>
            )}
          </div>
        ))}
      </div>

      {/* ── Add Server Modal ── */}
      {addOpen && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40">
          <div className="bg-white dark:bg-slate-800 rounded-xl shadow-xl w-full max-w-lg p-6 relative">
            <button onClick={() => setAddOpen(false)} className="absolute top-3 right-3 text-slate-400 hover:text-slate-600">
              <X className="w-5 h-5" />
            </button>
            <h3 className="text-lg font-semibold mb-4">Register MCP Server</h3>
            <p className="text-xs text-slate-500 mb-4">
              Register an MCP server endpoint for centralized ZeroShield discovery and governance.
            </p>
            <div className="space-y-3">
              <div>
                <label className="block text-sm font-medium mb-1">Name</label>
                <input
                  className="w-full border rounded-lg px-3 py-2 text-sm dark:bg-slate-700 dark:border-slate-600"
                  value={addForm.name}
                  onChange={(e) => setAddForm({ ...addForm, name: e.target.value })}
                  placeholder="my-mcp-server"
                />
              </div>
              <div>
                <label className="block text-sm font-medium mb-1">URL</label>
                <input
                  className="w-full border rounded-lg px-3 py-2 text-sm dark:bg-slate-700 dark:border-slate-600"
                  value={addForm.url}
                  onChange={(e) => setAddForm({ ...addForm, url: e.target.value })}
                  placeholder={addForm.transport === "stdio" ? "(not required for stdio)" : "https://my-server.example.com/mcp"}
                  disabled={addForm.transport === "stdio"}
                />
              </div>
              <div>
                <label className="block text-sm font-medium mb-1">Transport</label>
                <select
                  className="w-full border rounded-lg px-3 py-2 text-sm dark:bg-slate-700 dark:border-slate-600"
                  value={addForm.transport}
                  onChange={(e) => setAddForm({ ...addForm, transport: e.target.value })}
                >
                  {TRANSPORT_OPTIONS.map((t) => (
                    <option key={t.value} value={t.value} disabled={!t.supported}>
                      {t.label}
                    </option>
                  ))}
                </select>
                <p className="text-xs text-slate-500 mt-1">
                  Supports HTTP, SSE, Stdio (subprocess), and WebSocket transports.
                </p>
              </div>

              {/* Stdio-specific fields */}
              {addForm.transport === "stdio" && (
                <div className="border border-blue-200 dark:border-blue-800 rounded-lg p-3 space-y-3 bg-blue-50/50 dark:bg-blue-900/20">
                  <p className="text-xs font-medium text-blue-700 dark:text-blue-300">Stdio Transport Settings</p>
                  <div>
                    <label className="block text-sm font-medium mb-1">Command</label>
                    <input
                      className="w-full border rounded-lg px-3 py-2 text-sm dark:bg-slate-700 dark:border-slate-600"
                      value={addForm.command || ""}
                      onChange={(e) => setAddForm({ ...addForm, command: e.target.value })}
                      placeholder="npx"
                    />
                  </div>
                  <div>
                    <label className="block text-sm font-medium mb-1">Arguments (comma-separated)</label>
                    <input
                      className="w-full border rounded-lg px-3 py-2 text-sm dark:bg-slate-700 dark:border-slate-600"
                      value={Array.isArray(addForm.args) ? addForm.args.join(", ") : ""}
                      onChange={(e) => setAddForm({ ...addForm, args: e.target.value.split(",").map((s) => s.trim()).filter(Boolean) })}
                      placeholder="-y, @playwright/mcp@latest"
                    />
                  </div>
                  <div>
                    <label className="block text-sm font-medium mb-1">Environment Variables (KEY=VALUE, one per line)</label>
                    <textarea
                      className="w-full border rounded-lg px-3 py-2 text-sm font-mono dark:bg-slate-700 dark:border-slate-600"
                      rows={2}
                      value={
                        addForm.env_vars && typeof addForm.env_vars === "object"
                          ? Object.entries(addForm.env_vars).map(([k, v]) => `${k}=${v}`).join("\n")
                          : ""
                      }
                      onChange={(e) => {
                        const vars = {};
                        e.target.value.split("\n").forEach((line) => {
                          const idx = line.indexOf("=");
                          if (idx > 0) vars[line.slice(0, idx).trim()] = line.slice(idx + 1).trim();
                        });
                        setAddForm({ ...addForm, env_vars: vars });
                      }}
                      placeholder="GITHUB_TOKEN=ghp_xxx"
                    />
                  </div>
                </div>
              )}
              <div>
                <label className="block text-sm font-medium mb-1">Description</label>
                <textarea
                  className="w-full border rounded-lg px-3 py-2 text-sm dark:bg-slate-700 dark:border-slate-600"
                  rows={2}
                  value={addForm.description}
                  onChange={(e) => setAddForm({ ...addForm, description: e.target.value })}
                  placeholder="Optional description"
                />
              </div>

              <div className="border border-slate-200 dark:border-slate-700 rounded-lg p-3 space-y-3">
                <div>
                  <label className="block text-sm font-medium mb-1">Upstream Authentication</label>
                  <select
                    className="w-full border rounded-lg px-3 py-2 text-sm dark:bg-slate-700 dark:border-slate-600"
                    value={addForm.auth_type}
                    onChange={(e) => setAddForm({ ...addForm, auth_type: e.target.value })}
                  >
                    {AUTH_OPTIONS.map((opt) => (
                      <option key={opt.value} value={opt.value}>{opt.label}</option>
                    ))}
                  </select>
                </div>

                {addForm.auth_type === "bearer" && (
                  <div>
                    <label className="block text-sm font-medium mb-1">Bearer Token</label>
                    <input
                      type="password"
                      className="w-full border rounded-lg px-3 py-2 text-sm dark:bg-slate-700 dark:border-slate-600"
                      value={addForm.auth_token}
                      onChange={(e) => setAddForm({ ...addForm, auth_token: e.target.value })}
                      placeholder="Paste MCP access token"
                    />
                  </div>
                )}

                {addForm.auth_type === "basic" && (
                  <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
                    <div>
                      <label className="block text-sm font-medium mb-1">Username</label>
                      <input
                        className="w-full border rounded-lg px-3 py-2 text-sm dark:bg-slate-700 dark:border-slate-600"
                        value={addForm.auth_username}
                        onChange={(e) => setAddForm({ ...addForm, auth_username: e.target.value })}
                        placeholder="username"
                      />
                    </div>
                    <div>
                      <label className="block text-sm font-medium mb-1">Password</label>
                      <input
                        type="password"
                        className="w-full border rounded-lg px-3 py-2 text-sm dark:bg-slate-700 dark:border-slate-600"
                        value={addForm.auth_password}
                        onChange={(e) => setAddForm({ ...addForm, auth_password: e.target.value })}
                        placeholder="password"
                      />
                    </div>
                  </div>
                )}

                {addForm.auth_type === "authheaders" && (
                  <div className="space-y-2">
                    {(Array.isArray(addForm.auth_headers) ? addForm.auth_headers : []).map((header, index) => (
                      <div key={`auth-header-${index}`} className="grid grid-cols-1 sm:grid-cols-[1fr_1fr_auto] gap-2 items-end">
                        <div>
                          <label className="block text-sm font-medium mb-1">Header Key</label>
                          <input
                            className="w-full border rounded-lg px-3 py-2 text-sm dark:bg-slate-700 dark:border-slate-600"
                            value={header.key}
                            onChange={(e) => {
                              const nextHeaders = [...(addForm.auth_headers || [])];
                              nextHeaders[index] = { ...nextHeaders[index], key: e.target.value };
                              setAddForm({ ...addForm, auth_headers: nextHeaders });
                            }}
                            placeholder="Authorization"
                          />
                        </div>
                        <div>
                          <label className="block text-sm font-medium mb-1">Header Value</label>
                          <input
                            type="password"
                            className="w-full border rounded-lg px-3 py-2 text-sm dark:bg-slate-700 dark:border-slate-600"
                            value={header.value}
                            onChange={(e) => {
                              const nextHeaders = [...(addForm.auth_headers || [])];
                              nextHeaders[index] = { ...nextHeaders[index], value: e.target.value };
                              setAddForm({ ...addForm, auth_headers: nextHeaders });
                            }}
                            placeholder="Bearer ..."
                          />
                        </div>
                        <button
                          type="button"
                          className="btn-icon"
                          title="Remove Header"
                          onClick={() => {
                            const nextHeaders = (addForm.auth_headers || []).filter((_, idx) => idx !== index);
                            setAddForm({
                              ...addForm,
                              auth_headers: nextHeaders.length > 0 ? nextHeaders : [{ key: "", value: "" }],
                            });
                          }}
                        >
                          <Trash2 className="w-4 h-4" />
                        </button>
                      </div>
                    ))}

                    <button
                      type="button"
                      className="btn-secondary text-xs inline-flex items-center gap-1"
                      onClick={() => {
                        setAddForm({
                          ...addForm,
                          auth_headers: [...(addForm.auth_headers || []), { key: "", value: "" }],
                        });
                      }}
                    >
                      <Plus className="w-3 h-3" />
                      Add Header
                    </button>
                  </div>
                )}

                {addForm.auth_type === "query_param" && (
                  <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
                    <div>
                      <label className="block text-sm font-medium mb-1">Param Key</label>
                      <input
                        className="w-full border rounded-lg px-3 py-2 text-sm dark:bg-slate-700 dark:border-slate-600"
                        value={addForm.auth_query_param_key}
                        onChange={(e) => setAddForm({ ...addForm, auth_query_param_key: e.target.value })}
                        placeholder="api_key"
                      />
                    </div>
                    <div>
                      <label className="block text-sm font-medium mb-1">Param Value</label>
                      <input
                        type="password"
                        className="w-full border rounded-lg px-3 py-2 text-sm dark:bg-slate-700 dark:border-slate-600"
                        value={addForm.auth_query_param_value}
                        onChange={(e) => setAddForm({ ...addForm, auth_query_param_value: e.target.value })}
                        placeholder="secret value"
                      />
                    </div>
                  </div>
                )}
              </div>
            </div>
            <div className="flex justify-end gap-2 mt-5">
              <button onClick={() => setAddOpen(false)} className="btn-secondary text-sm">Cancel</button>
              <button
                onClick={addServer}
                disabled={addSaving || !addForm.name || (addForm.transport === "stdio" ? !addForm.command : !addForm.url)}
                className="btn-primary text-sm flex items-center gap-1"
              >
                {addSaving && <Loader2 className="w-4 h-4 animate-spin" />}
                Register
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );

  const renderTools = () => (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <p className="text-sm text-slate-500 dark:text-slate-400">
          Tools discovered across all connected MCP servers.
        </p>
        <button onClick={loadTools} className="btn-icon" title="Refresh">
          <RefreshCw className="w-4 h-4" />
        </button>
      </div>

      {Array.isArray(tools) && tools.length === 0 && !loading && (
        <div className="text-center py-12 text-slate-400 dark:text-slate-500">
          <Wrench className="w-10 h-10 mx-auto mb-3 opacity-40" />
          <p>No tools discovered yet.</p>
          <p className="text-xs mt-1">Register MCP servers first, then tools will appear here.</p>
        </div>
      )}

      <div className="grid gap-2">
        {(Array.isArray(tools) ? tools : []).map((tool, idx) => (
          <div key={tool.name || idx} className="bg-white dark:bg-slate-800 border border-slate-200 dark:border-slate-700 rounded-lg p-3">
            <div className="flex items-start justify-between">
              <div>
                <h4 className="font-mono text-sm font-medium text-slate-900 dark:text-white">
                  {tool.name}
                </h4>
                {tool.description && (
                  <p className="text-xs text-slate-500 mt-0.5">{tool.description}</p>
                )}
              </div>
              {tool.server_name && (
                <span className="text-[10px] px-1.5 py-0.5 rounded bg-blue-50 dark:bg-blue-900/20 text-blue-600 dark:text-blue-300">
                  {tool.server_name}
                </span>
              )}
            </div>
            {tool.inputSchema && (
              <details className="mt-2">
                <summary className="text-xs text-slate-400 cursor-pointer">Input Schema</summary>
                <pre className="text-[10px] mt-1 bg-slate-50 dark:bg-slate-900 rounded p-2 overflow-x-auto">
                  {JSON.stringify(tool.inputSchema, null, 2)}
                </pre>
              </details>
            )}
          </div>
        ))}
      </div>
    </div>
  );

  const renderPolicies = () => {
    const selectedServer = servers.find((s) => s.server_slug === policyServerFilter);
    return (
      <div className="space-y-4">
        <div className="flex items-center gap-3 flex-wrap">
          <label className="text-sm font-medium text-slate-700 dark:text-slate-300">Scope to server:</label>
          <select
            className="border rounded-lg px-3 py-1.5 text-sm dark:bg-slate-700 dark:border-slate-600 min-w-[200px]"
            value={policyServerFilter}
            onChange={(e) => setPolicyServerFilter(e.target.value)}
          >
            <option value="">All MCP servers (org-wide)</option>
            {servers.map((s) => (
              <option key={s.id} value={s.server_slug}>{s.name} ({s.server_slug})</option>
            ))}
          </select>
          {policyServerFilter && (
            <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded bg-blue-100 dark:bg-blue-800/30 text-blue-700 dark:text-blue-300 text-xs">
              <Server className="w-3 h-3" />
              Filtering: {selectedServer?.name || policyServerFilter}
            </span>
          )}
        </div>
        <PolicyManagementPanel
          key={policyServerFilter || "__all__"}
          title="MCP Security Policies"
          description={
            policyServerFilter
              ? `Policies scoped to server "${selectedServer?.name || policyServerFilter}" + org-wide MCP policies`
              : "Organization-wide MCP policies (apply to all MCP servers)"
          }
          scope="mcp"
          mcpServerSlug={policyServerFilter || null}
          mcpServerId={selectedServer?.id || null}
          showCompileButton={true}
          showFilters={true}
        />
      </div>
    );
  };

  const renderGuardrails = () => (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <p className="text-sm text-slate-500 dark:text-slate-400">
          Guardrail profiles for MCP traffic. Apply PII redaction, injection blocking,
          and compliance policies through ZeroShield enforcement.
        </p>
        <div className="flex gap-2">
          <button onClick={loadGuardrails} className="btn-icon" title="Refresh">
            <RefreshCw className="w-4 h-4" />
          </button>
          <button onClick={() => setGrOpen(true)} className="btn-primary text-sm flex items-center gap-1">
            <Plus className="w-4 h-4" /> New Profile
          </button>
        </div>
      </div>

      {guardrails.length === 0 && !loading && (
        <div className="text-center py-12 text-slate-400 dark:text-slate-500">
          <Shield className="w-10 h-10 mx-auto mb-3 opacity-40" />
          <p>No guardrail profiles defined.</p>
          <p className="text-xs mt-1">Create a profile to enforce PII redaction, injection blocking, etc.</p>
        </div>
      )}

      <div className="grid gap-3">
        {guardrails.map((gr) => (
          <div key={gr.id} className="bg-white dark:bg-slate-800 border border-slate-200 dark:border-slate-700 rounded-lg p-4">
            <div className="flex items-start justify-between">
              <div>
                <h4 className="font-medium text-slate-900 dark:text-white flex items-center gap-2">
                  <Shield className="w-4 h-4 text-violet-500" />
                  {gr.name}
                  {gr.is_default && (
                    <span className="text-[10px] px-1.5 py-0.5 rounded bg-emerald-100 dark:bg-emerald-800/30 text-emerald-700 dark:text-emerald-300 font-medium">
                      DEFAULT
                    </span>
                  )}
                </h4>
                {gr.description && <p className="text-xs text-slate-500 mt-1">{gr.description}</p>}
              </div>
              <button onClick={() => deleteGuardrail(gr.id)} className="text-red-500 hover:text-red-700" title="Delete">
                <Trash2 className="w-4 h-4" />
              </button>
            </div>
            <div className="mt-3 grid grid-cols-1 sm:grid-cols-2 gap-3">
              <div>
                <span className="text-[10px] font-medium uppercase text-slate-400">Input Detectors</span>
                <div className="mt-1 flex flex-wrap gap-1">
                  {gr.input_policy?.enabled === false ? (
                    <span className="text-[10px] text-slate-400 italic">Disabled</span>
                  ) : (gr.input_policy?.block || []).length > 0 ? (
                    (gr.input_policy.block || []).map((b) => (
                      <span key={b} className="text-[10px] px-1.5 py-0.5 rounded bg-red-100 dark:bg-red-800/30 text-red-700 dark:text-red-300 font-medium">
                        {b.replace(/_/g, " ")}
                      </span>
                    ))
                  ) : (
                    <span className="text-[10px] text-slate-400 italic">No blocking detectors</span>
                  )}
                  {gr.input_policy?.pii_redaction && (
                    <span className="text-[10px] px-1.5 py-0.5 rounded bg-amber-100 dark:bg-amber-800/30 text-amber-700 dark:text-amber-300 font-medium">
                      PII redaction
                    </span>
                  )}
                </div>
              </div>
              <div>
                <span className="text-[10px] font-medium uppercase text-slate-400">Output Detectors</span>
                <div className="mt-1 flex flex-wrap gap-1">
                  {gr.output_policy?.enabled === false ? (
                    <span className="text-[10px] text-slate-400 italic">Disabled</span>
                  ) : (gr.output_policy?.block || []).length > 0 ? (
                    (gr.output_policy.block || []).map((b) => (
                      <span key={b} className="text-[10px] px-1.5 py-0.5 rounded bg-red-100 dark:bg-red-800/30 text-red-700 dark:text-red-300 font-medium">
                        {b.replace(/_/g, " ")}
                      </span>
                    ))
                  ) : (
                    <span className="text-[10px] text-slate-400 italic">No blocking detectors</span>
                  )}
                </div>
              </div>
            </div>
            {/* Enforcement Chain: Policy → Scope → Effect */}
            <div className="mt-3 border-t border-slate-100 dark:border-slate-700 pt-3">
              <span className="text-[10px] font-medium uppercase text-slate-400 mb-1 block">Enforcement Chain</span>
              <div className="flex flex-wrap items-center gap-1.5 text-[11px]">
                <span className="inline-flex items-center gap-1 rounded bg-violet-100 dark:bg-violet-800/30 text-violet-700 dark:text-violet-300 px-2 py-0.5 font-medium">
                  <Shield className="w-3 h-3" /> {gr.name}
                </span>
                <span className="text-slate-400">→</span>
                <span className="inline-flex items-center rounded bg-blue-100 dark:bg-blue-800/30 text-blue-700 dark:text-blue-300 px-2 py-0.5">
                  {gr.is_default ? "All MCP servers (default)" : "Manually assigned servers"}
                </span>
                <span className="text-slate-400">→</span>
                <span className="inline-flex items-center rounded bg-slate-100 dark:bg-slate-700 text-slate-700 dark:text-slate-300 px-2 py-0.5">
                  {gr.input_policy?.enabled && gr.output_policy?.enabled ? "Input + Output" :
                   gr.input_policy?.enabled ? "Input only" :
                   gr.output_policy?.enabled ? "Output only" : "Inactive"}
                </span>
                <span className="text-slate-400">→</span>
                {(gr.input_policy?.block?.length > 0 || gr.output_policy?.block?.length > 0) ? (
                  <span className="inline-flex items-center rounded bg-red-100 dark:bg-red-800/30 text-red-700 dark:text-red-300 px-2 py-0.5 font-medium">
                    Blocks: {[...(gr.input_policy?.block || []), ...(gr.output_policy?.block || [])].join(", ")}
                  </span>
                ) : (
                  <span className="inline-flex items-center rounded bg-emerald-100 dark:bg-emerald-800/30 text-emerald-700 dark:text-emerald-300 px-2 py-0.5">
                    Monitor only
                  </span>
                )}
              </div>
            </div>
          </div>
        ))}
      </div>

      {/* ── Add Guardrail Modal — Category-based toggles ── */}
      {grOpen && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40">
          <div className="bg-white dark:bg-slate-800 rounded-xl shadow-xl w-full max-w-lg p-6 relative max-h-[90vh] overflow-y-auto">
            <button onClick={() => setGrOpen(false)} className="absolute top-3 right-3 text-slate-400 hover:text-slate-600">
              <X className="w-5 h-5" />
            </button>
            <h3 className="text-lg font-semibold mb-4">New Guardrail Profile</h3>
            <div className="space-y-3">
              <div>
                <label className="block text-sm font-medium mb-1">Name</label>
                <input
                  className="w-full border rounded-lg px-3 py-2 text-sm dark:bg-slate-700 dark:border-slate-600"
                  value={grForm.name}
                  onChange={(e) => setGrForm({ ...grForm, name: e.target.value })}
                  placeholder="PII + Injection Protection"
                />
              </div>
              <div>
                <label className="block text-sm font-medium mb-1">Description</label>
                <textarea
                  className="w-full border rounded-lg px-3 py-2 text-sm dark:bg-slate-700 dark:border-slate-600"
                  rows={2}
                  value={grForm.description}
                  onChange={(e) => setGrForm({ ...grForm, description: e.target.value })}
                />
              </div>
              <div className="flex items-center gap-2">
                <input
                  type="checkbox"
                  checked={grForm.is_default}
                  onChange={(e) => setGrForm({ ...grForm, is_default: e.target.checked })}
                  className="rounded"
                />
                <label className="text-sm">Apply by default to new MCP servers</label>
              </div>

              {/* ── Input Protection Toggles ── */}
              <div className="border border-slate-200 dark:border-slate-700 rounded-lg p-3">
                <div className="flex items-center justify-between mb-2">
                  <h4 className="text-sm font-medium text-slate-900 dark:text-white">Input Protection</h4>
                  <button
                    type="button"
                    onClick={() => setGrForm({
                      ...grForm,
                      input_policy: { ...grForm.input_policy, enabled: !grForm.input_policy?.enabled }
                    })}
                    className="shrink-0"
                    title={grForm.input_policy?.enabled ? "Disable input protection" : "Enable input protection"}
                  >
                    {grForm.input_policy?.enabled
                      ? <ToggleRight className="w-5 h-5 text-emerald-500" />
                      : <ToggleLeft className="w-5 h-5 text-slate-400" />}
                  </button>
                </div>
                {grForm.input_policy?.enabled && (
                  <div className="space-y-2">
                    {[
                      { key: "pii_redaction", label: "PII Redaction", desc: "Redact emails, SSNs, credit cards, phone numbers", builtin: true },
                      { key: "injection_attack", label: "Prompt Injection Blocking", desc: "Block prompt injection and jailbreak attempts", builtin: true },
                      { key: "sensitive_data", label: "Sensitive Data Detection", desc: "Detect API keys, passwords, secrets in input", builtin: true },
                      { key: "profanity", label: "Profanity Filter", desc: "Block profanity and offensive language", builtin: true },
                      { key: "topic_restriction", label: "Topic Restriction", desc: "Restrict off-topic or disallowed content categories", builtin: true },
                    ].map((detector) => {
                      const isBlocked = (grForm.input_policy?.block || []).includes(detector.key);
                      return (
                        <div key={detector.key} className="flex items-center justify-between bg-slate-50 dark:bg-slate-900 rounded px-3 py-2">
                          <div className="min-w-0">
                            <span className="text-xs font-medium text-slate-900 dark:text-white">{detector.label}</span>
                            <p className="text-[10px] text-slate-500">{detector.desc}</p>
                          </div>
                          <button
                            type="button"
                            onClick={() => {
                              const currentBlocks = grForm.input_policy?.block || [];
                              const newBlocks = isBlocked
                                ? currentBlocks.filter((b) => b !== detector.key)
                                : [...currentBlocks, detector.key];
                              setGrForm({
                                ...grForm,
                                input_policy: { ...grForm.input_policy, block: newBlocks },
                              });
                            }}
                            className="shrink-0 ml-2"
                          >
                            {isBlocked
                              ? <ToggleRight className="w-5 h-5 text-red-500" />
                              : <ToggleLeft className="w-5 h-5 text-slate-400" />}
                          </button>
                        </div>
                      );
                    })}
                  </div>
                )}
              </div>

              {/* ── Output Protection Toggles ── */}
              <div className="border border-slate-200 dark:border-slate-700 rounded-lg p-3">
                <div className="flex items-center justify-between mb-2">
                  <h4 className="text-sm font-medium text-slate-900 dark:text-white">Output Protection</h4>
                  <button
                    type="button"
                    onClick={() => setGrForm({
                      ...grForm,
                      output_policy: { ...grForm.output_policy, enabled: !grForm.output_policy?.enabled }
                    })}
                    className="shrink-0"
                    title={grForm.output_policy?.enabled ? "Disable output protection" : "Enable output protection"}
                  >
                    {grForm.output_policy?.enabled
                      ? <ToggleRight className="w-5 h-5 text-emerald-500" />
                      : <ToggleLeft className="w-5 h-5 text-slate-400" />}
                  </button>
                </div>
                {grForm.output_policy?.enabled && (
                  <div className="space-y-2">
                    {[
                      { key: "policy_violation", label: "Policy Violation Blocking", desc: "Block responses that violate organizational policies", builtin: true },
                      { key: "pii_leakage", label: "PII Leakage Prevention", desc: "Prevent PII from appearing in tool output", builtin: true },
                      { key: "hallucination", label: "Hallucination Detection", desc: "Flag fabricated or ungrounded content", builtin: false },
                      { key: "toxicity", label: "Toxicity Filter", desc: "Block toxic, harmful, or offensive output", builtin: false },
                      { key: "data_exfiltration", label: "Data Exfiltration Guard", desc: "Prevent unauthorized data extraction via tool output", builtin: true },
                    ].map((detector) => {
                      const isBlocked = (grForm.output_policy?.block || []).includes(detector.key);
                      const enkryptMissing = !detector.builtin && !health?.enkrypt_configured;
                      return (
                        <div key={detector.key} className={`flex items-center justify-between rounded px-3 py-2 ${enkryptMissing ? "bg-amber-50 dark:bg-amber-900/20 border border-amber-200 dark:border-amber-800" : "bg-slate-50 dark:bg-slate-900"}`}>
                          <div className="min-w-0">
                            <span className="text-xs font-medium text-slate-900 dark:text-white">{detector.label}</span>
                            {!detector.builtin && <span className="ml-1.5 text-[9px] px-1.5 py-0.5 rounded bg-amber-100 text-amber-700 dark:bg-amber-800 dark:text-amber-200 font-medium">Enkrypt API</span>}
                            <p className="text-[10px] text-slate-500">{detector.desc}</p>
                            {enkryptMissing && <p className="text-[10px] text-amber-600 dark:text-amber-400">Requires SECURE_MCP_GATEWAY_ADMIN_KEY</p>}
                          </div>
                          <button
                            type="button"
                            onClick={() => {
                              const currentBlocks = grForm.output_policy?.block || [];
                              const newBlocks = isBlocked
                                ? currentBlocks.filter((b) => b !== detector.key)
                                : [...currentBlocks, detector.key];
                              setGrForm({
                                ...grForm,
                                output_policy: { ...grForm.output_policy, block: newBlocks },
                              });
                            }}
                            className="shrink-0 ml-2"
                          >
                            {isBlocked
                              ? <ToggleRight className="w-5 h-5 text-red-500" />
                              : <ToggleLeft className="w-5 h-5 text-slate-400" />}
                          </button>
                        </div>
                      );
                    })}
                  </div>
                )}
              </div>
            </div>
            <div className="flex justify-end gap-2 mt-5">
              <button onClick={() => setGrOpen(false)} className="btn-secondary text-sm">Cancel</button>
              <button
                onClick={addGuardrail}
                disabled={grSaving || !grForm.name}
                className="btn-primary text-sm flex items-center gap-1"
              >
                {grSaving && <Loader2 className="w-4 h-4 animate-spin" />}
                Create Profile
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );

  const renderProtection = () => (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <p className="text-sm text-slate-500 dark:text-slate-400">
          Unified MCP protection — policy rules and guardrail profiles.
        </p>
        <div className="flex gap-1 bg-slate-100 dark:bg-slate-700 rounded-lg p-0.5">
          <button
            onClick={() => setProtectionView("policies")}
            className={`px-3 py-1 text-xs rounded font-medium transition-colors ${
              protectionView === "policies"
                ? "bg-white dark:bg-slate-600 text-slate-900 dark:text-white shadow-sm"
                : "text-slate-500 hover:text-slate-700 dark:text-slate-400"
            }`}
          >
            <Ban className="w-3 h-3 inline mr-1" />Policy Rules
          </button>
          <button
            onClick={() => setProtectionView("guardrails")}
            className={`px-3 py-1 text-xs rounded font-medium transition-colors ${
              protectionView === "guardrails"
                ? "bg-white dark:bg-slate-600 text-slate-900 dark:text-white shadow-sm"
                : "text-slate-500 hover:text-slate-700 dark:text-slate-400"
            }`}
          >
            <Shield className="w-3 h-3 inline mr-1" />Guardrail Profiles
          </button>
        </div>
      </div>
      {protectionView === "policies" ? renderPolicies() : renderGuardrails()}
    </div>
  );

  const renderExecute = () => (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <p className="text-sm text-slate-500 dark:text-slate-400">
          Invoke a discovered MCP tool through ZeroShield orchestration.
        </p>
        <button onClick={loadTools} className="btn-icon" title="Refresh tools">
          <RefreshCw className="w-4 h-4" />
        </button>
      </div>

      <div className="bg-white dark:bg-slate-800 border border-slate-200 dark:border-slate-700 rounded-lg p-4 space-y-3">
        <div>
          <label className="block text-sm font-medium mb-1">Server</label>
          <select
            className="w-full border rounded-lg px-3 py-2 text-sm dark:bg-slate-700 dark:border-slate-600"
            value={executeServerSlug}
            onChange={(e) => setExecuteServerSlug(e.target.value)}
          >
            <option value="">Select server...</option>
            {servers.map((server) => (
              <option key={server.id} value={server.server_slug}>
                {server.name} ({server.server_slug})
              </option>
            ))}
          </select>
          <p className="mt-1 text-[11px] text-slate-500 dark:text-slate-400">
            Scope execution to one MCP server so the backend can route the tool call deterministically.
          </p>
        </div>

        <div>
          <label className="block text-sm font-medium mb-1">Tool</label>
          <select
            className="w-full border rounded-lg px-3 py-2 text-sm dark:bg-slate-700 dark:border-slate-600"
            value={executeToolKey}
            onChange={(e) => setExecuteToolKey(e.target.value)}
            disabled={!executeServerSlug}
          >
            <option value="">{executeServerSlug ? "Select tool..." : "Choose a server first..."}</option>
            {executableTools.map((tool, idx) => (
              <option key={`${makeExecuteToolKey(tool)}-${idx}`} value={makeExecuteToolKey(tool)}>
                {tool.name || `tool-${idx}`} {tool.server_name ? `• ${tool.server_name}` : ""}
              </option>
            ))}
          </select>
          {executeServerSlug && (
            <p className="mt-1 text-[11px] text-slate-500 dark:text-slate-400">
              {executableTools.length} tool{executableTools.length === 1 ? "" : "s"} available on this server.
            </p>
          )}
        </div>

        {selectedExecuteTool && (
          <div className="rounded-lg border border-slate-200 dark:border-slate-700 bg-slate-50 dark:bg-slate-900/40 p-3 space-y-2">
            <div className="flex items-start justify-between gap-3">
              <div>
                <p className="text-sm font-medium text-slate-900 dark:text-white">{selectedExecuteTool.name}</p>
                {selectedExecuteTool.description && (
                  <p className="text-xs text-slate-500 dark:text-slate-400 mt-0.5">{selectedExecuteTool.description}</p>
                )}
              </div>
              <div className="flex flex-wrap justify-end gap-1 text-[10px]">
                {selectedExecuteTool.server_name && (
                  <span className="px-1.5 py-0.5 rounded bg-blue-50 dark:bg-blue-900/20 text-blue-600 dark:text-blue-300">
                    {selectedExecuteTool.server_name}
                  </span>
                )}
                {selectedExecuteTool.transport && (
                  <span className="px-1.5 py-0.5 rounded bg-slate-100 dark:bg-slate-700 text-slate-600 dark:text-slate-300">
                    {selectedExecuteTool.transport}
                  </span>
                )}
                {selectedExecuteTool.source && (
                  <span className="px-1.5 py-0.5 rounded bg-violet-50 dark:bg-violet-900/20 text-violet-600 dark:text-violet-300">
                    {selectedExecuteTool.source}
                  </span>
                )}
              </div>
            </div>
            {getToolSchema(selectedExecuteTool) && (
              <div className="flex items-center justify-between gap-3">
                <p className="text-[11px] text-slate-500 dark:text-slate-400">
                  This tool publishes an input schema. Use it to seed valid JSON arguments.
                </p>
                <button
                  type="button"
                  onClick={() => setExecuteArguments(JSON.stringify(buildExampleFromSchema(getToolSchema(selectedExecuteTool)), null, 2))}
                  className="px-2.5 py-1 text-xs rounded border border-slate-200 dark:border-slate-600 text-slate-700 dark:text-slate-300 hover:bg-white dark:hover:bg-slate-800"
                >
                  Fill from Schema
                </button>
              </div>
            )}
          </div>
        )}

        <div>
          <div className="flex items-center justify-between gap-2 mb-1">
            <label className="block text-sm font-medium">Arguments (JSON)</label>
            {selectedExecuteTool && getToolSchema(selectedExecuteTool) && (
              <details className="text-[11px] text-slate-500 dark:text-slate-400">
                <summary className="cursor-pointer">View schema</summary>
                <pre className="mt-2 w-[min(48rem,80vw)] max-h-64 overflow-auto rounded bg-slate-100 dark:bg-slate-950 p-2 text-[10px] text-left">
                  {JSON.stringify(getToolSchema(selectedExecuteTool), null, 2)}
                </pre>
              </details>
            )}
          </div>
          <textarea
            className="w-full border rounded-lg px-3 py-2 text-xs font-mono dark:bg-slate-700 dark:border-slate-600"
            rows={6}
            value={executeArguments}
            onChange={(e) => setExecuteArguments(e.target.value)}
          />
        </div>

        <div className="flex justify-end">
          <button
            onClick={executeTool}
            disabled={executeBusy || !selectedExecuteTool}
            className="btn-primary text-sm flex items-center gap-1 disabled:opacity-50"
          >
            {executeBusy ? <Loader2 className="w-4 h-4 animate-spin" /> : <Play className="w-4 h-4" />}
            Execute Tool
          </button>
        </div>
      </div>

      {executeResult && (
        <div className="bg-white dark:bg-slate-800 border border-slate-200 dark:border-slate-700 rounded-lg p-4">
          <div className="flex items-center justify-between gap-3 mb-2">
            <h4 className="text-sm font-medium">Execution Response</h4>
            <div className="flex flex-wrap gap-2 text-[10px]">
              {executeResult.decision && (
                <span className="px-1.5 py-0.5 rounded bg-emerald-50 dark:bg-emerald-900/20 text-emerald-600 dark:text-emerald-300 uppercase">
                  {executeResult.decision}
                </span>
              )}
              {executeResult.request_id && (
                <span className="px-1.5 py-0.5 rounded bg-slate-100 dark:bg-slate-700 text-slate-600 dark:text-slate-300 font-mono">
                  {executeResult.request_id}
                </span>
              )}
              {executeResult.status && (
                <span className="px-1.5 py-0.5 rounded bg-amber-50 dark:bg-amber-900/20 text-amber-600 dark:text-amber-300">
                  HTTP {executeResult.status}
                </span>
              )}
            </div>
          </div>
          {extractExecutionPreview(executeResult) && (
            <div className="mb-3 rounded border border-slate-200 dark:border-slate-700 bg-slate-50 dark:bg-slate-900 p-3 text-xs whitespace-pre-wrap text-slate-700 dark:text-slate-200">
              {extractExecutionPreview(executeResult)}
            </div>
          )}
          <pre className="text-xs bg-slate-50 dark:bg-slate-900 rounded p-3 overflow-x-auto">
            {JSON.stringify(executeResult, null, 2)}
          </pre>
        </div>
      )}
    </div>
  );

  const renderObservability = () => {
    const DECISION_COLORS = {
      allow: "text-emerald-600 bg-emerald-50 dark:bg-emerald-900/20 dark:text-emerald-300",
      block: "text-red-600 bg-red-50 dark:bg-red-900/20 dark:text-red-300",
      redact: "text-amber-600 bg-amber-50 dark:bg-amber-900/20 dark:text-amber-300",
      error: "text-orange-600 bg-orange-50 dark:bg-orange-900/20 dark:text-orange-300",
    };
    return (
      <div className="space-y-4">
        <div className="flex items-center justify-between gap-3">
          <p className="text-sm text-slate-500 dark:text-slate-400">
            Real-time MCP policy enforcement events. All tool calls are audited with pre-flight and post-flight checks.
          </p>
          <div className="flex items-center gap-2">
            <select
              value={obsHours}
              onChange={(e) => {
                const h = Number(e.target.value);
                setObsHours(h);
                loadEvents(h);
                loadEventSummary(h);
              }}
              className="text-xs rounded-md border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-800 px-2 py-1 text-slate-700 dark:text-slate-200"
              title="Time window"
            >
              <option value={1}>Last hour</option>
              <option value={24}>Last 24 hours</option>
              <option value={168}>Last 7 days</option>
              <option value={720}>Last 30 days</option>
              <option value={0}>All time</option>
            </select>
            <button onClick={() => { loadEvents(); loadEventSummary(); }} className="btn-icon" title="Refresh">
              <RefreshCw className="w-4 h-4" />
            </button>
          </div>
        </div>

        {/* Summary cards */}
        {eventSummary && (
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
            <div className="bg-white dark:bg-slate-800 border border-slate-200 dark:border-slate-700 rounded-lg p-4 text-center">
              <p className="text-2xl font-bold text-slate-900 dark:text-white">{eventSummary.total || 0}</p>
              <p className="text-xs text-slate-500 mt-1">Total Events</p>
            </div>
            {Object.entries(eventSummary.decisions || {}).map(([decision, count]) => (
              <div key={decision} className="bg-white dark:bg-slate-800 border border-slate-200 dark:border-slate-700 rounded-lg p-4 text-center">
                <p className={`text-2xl font-bold ${decision === "allow" ? "text-emerald-600" : decision === "block" ? "text-red-600" : "text-amber-600"}`}>
                  {count}
                </p>
                <p className="text-xs text-slate-500 mt-1 capitalize">{decision}</p>
              </div>
            ))}
          </div>
        )}

        {/* Top tools and users */}
        {eventSummary && (
          <div className="grid gap-4 sm:grid-cols-2">
            {eventSummary.top_tools && eventSummary.top_tools.length > 0 && (
              <div className="bg-white dark:bg-slate-800 border border-slate-200 dark:border-slate-700 rounded-lg p-4">
                <h4 className="text-sm font-medium text-slate-900 dark:text-white mb-3 flex items-center gap-2">
                  <Wrench className="w-4 h-4 text-blue-500" /> Top Tools
                </h4>
                <div className="space-y-2">
                  {eventSummary.top_tools.map((t) => (
                    <div key={t.tool_name} className="flex items-center justify-between text-xs">
                      <span className="font-mono text-slate-700 dark:text-slate-300 truncate">{t.tool_name}</span>
                      <span className="text-slate-500 font-medium shrink-0 ml-2">{t.count}</span>
                    </div>
                  ))}
                </div>
              </div>
            )}
            {eventSummary.top_users && eventSummary.top_users.length > 0 && (
              <div className="bg-white dark:bg-slate-800 border border-slate-200 dark:border-slate-700 rounded-lg p-4">
                <h4 className="text-sm font-medium text-slate-900 dark:text-white mb-3 flex items-center gap-2">
                  <Hash className="w-4 h-4 text-violet-500" /> Top Users
                </h4>
                <div className="space-y-2">
                  {eventSummary.top_users.map((u) => (
                    <div key={u.username || u.user_id} className="flex items-center justify-between text-xs">
                      <span className="text-slate-700 dark:text-slate-300 truncate">{u.username || u.user_id}</span>
                      <span className="text-slate-500 font-medium shrink-0 ml-2">{u.count}</span>
                    </div>
                  ))}
                </div>
              </div>
            )}
          </div>
        )}

        {/* Event list */}
        <div>
          <h4 className="text-sm font-medium text-slate-900 dark:text-white mb-2">Recent Events</h4>
          {events.length === 0 && (
            <div className="text-center py-8 text-slate-400">
              <BarChart3 className="w-10 h-10 mx-auto mb-3 opacity-40" />
              <p>No MCP events recorded yet.</p>
              <p className="text-xs mt-1">Execute a tool call to see enforcement events appear here.</p>
            </div>
          )}
          <div className="space-y-2">
            {events.map((evt) => (
              <div key={evt.id || evt.request_id} className="bg-white dark:bg-slate-800 border border-slate-200 dark:border-slate-700 rounded-lg p-3">
                <div className="flex items-center justify-between">
                  <div className="flex items-center gap-2 flex-wrap">
                    <span className={`text-[10px] px-1.5 py-0.5 rounded font-medium uppercase ${DECISION_COLORS[evt.decision] || ""}`}>
                      {evt.decision}
                    </span>
                    <span className="text-xs font-mono text-slate-700 dark:text-slate-300">{evt.tool_name}</span>
                    {evt.server_name && (
                      <span className="text-[10px] text-slate-400">on {evt.server_name}</span>
                    )}
                  </div>
                  <div className="flex items-center gap-2 text-[10px] text-slate-400 shrink-0">
                    {evt.latency_ms != null && <span>{evt.latency_ms}ms</span>}
                    {evt.timestamp && <span>{new Date(evt.timestamp).toLocaleString()}</span>}
                  </div>
                </div>
                {evt.policy_reason && (
                  <p className="text-[10px] text-slate-500 mt-1">{evt.policy_reason}</p>
                )}
                {evt.username && (
                  <p className="text-[10px] text-slate-400 mt-0.5">User: {evt.username}</p>
                )}
              </div>
            ))}
          </div>
        </div>
      </div>
    );
  };

  const renderHealth = () => (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <p className="text-sm text-slate-500 dark:text-slate-400">
          Status of ZeroShield MCP service planes.
        </p>
        <button onClick={loadHealth} className="btn-icon" title="Refresh">
          <RefreshCw className="w-4 h-4" />
        </button>
      </div>

      {!health && !loading && (
        <div className="text-center py-12 text-slate-400">
          <Activity className="w-10 h-10 mx-auto mb-3 opacity-40" />
          <p>Unable to load service health.</p>
        </div>
      )}

      {health && (
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {/* Registry plane */}
          <div className="bg-white dark:bg-slate-800 border border-slate-200 dark:border-slate-700 rounded-lg p-5">
            <div className="flex items-center gap-3 mb-3">
              <div className="w-10 h-10 rounded-lg bg-blue-50 dark:bg-blue-900/20 flex items-center justify-center">
                <Server className="w-5 h-5 text-blue-600" />
              </div>
              <div>
                <h4 className="font-medium text-slate-900 dark:text-white">ZeroShield Registry Plane</h4>
                <p className="text-xs text-slate-500">MCP Registry, Discovery &amp; Federation</p>
              </div>
              <StatusDot status={health.contextforge?.status} />
            </div>
            <div className="flex items-center gap-2">
              <span className={`text-xs font-medium px-2 py-0.5 rounded ${
                health.contextforge?.status === "healthy"
                  ? "bg-emerald-100 text-emerald-700 dark:bg-emerald-800/30 dark:text-emerald-300"
                  : "bg-red-100 text-red-700 dark:bg-red-800/30 dark:text-red-300"
              }`}>
                {health.contextforge?.status || "unknown"}
              </span>
            </div>
          </div>

          {/* Guardrail plane */}
          <div className="bg-white dark:bg-slate-800 border border-slate-200 dark:border-slate-700 rounded-lg p-5">
            <div className="flex items-center gap-3 mb-3">
              <div className="w-10 h-10 rounded-lg bg-violet-50 dark:bg-violet-900/20 flex items-center justify-center">
                <Shield className="w-5 h-5 text-violet-600" />
              </div>
              <div>
                <h4 className="font-medium text-slate-900 dark:text-white">ZeroShield Guardrail Plane</h4>
                <p className="text-xs text-slate-500">Guardrails, PII Redaction &amp; Compliance</p>
              </div>
              <StatusDot status={health.secure_mcp_gateway?.status} />
            </div>
            <span className={`text-xs font-medium px-2 py-0.5 rounded ${
              health.secure_mcp_gateway?.status === "healthy"
                ? "bg-emerald-100 text-emerald-700 dark:bg-emerald-800/30 dark:text-emerald-300"
                : health.secure_mcp_gateway?.status === "not_configured"
                ? "bg-slate-100 text-slate-600 dark:bg-slate-700 dark:text-slate-300"
                : "bg-red-100 text-red-700 dark:bg-red-800/30 dark:text-red-300"
            }`}>
              {health.secure_mcp_gateway?.status || "unknown"}
            </span>
          </div>

          {/* Policy plane — built-in engine */}
          <div className="bg-white dark:bg-slate-800 border border-slate-200 dark:border-slate-700 rounded-lg p-5">
            <div className="flex items-center gap-3 mb-3">
              <div className="w-10 h-10 rounded-lg bg-orange-50 dark:bg-orange-900/20 flex items-center justify-center">
                <Ban className="w-5 h-5 text-orange-600" />
              </div>
              <div>
                <h4 className="font-medium text-slate-900 dark:text-white">ZeroShield Policy Engine</h4>
                <p className="text-xs text-slate-500">Built-in Regex, Keyword &amp; Pattern Enforcement</p>
              </div>
              <StatusDot status={health.mcp_firewall?.status} />
            </div>
            <span className={`text-xs font-medium px-2 py-0.5 rounded ${
              health.mcp_firewall?.status === "healthy"
                ? "bg-emerald-100 text-emerald-700 dark:bg-emerald-800/30 dark:text-emerald-300"
                : health.mcp_firewall?.status === "not_configured"
                ? "bg-slate-100 text-slate-600 dark:bg-slate-700 dark:text-slate-300"
                : "bg-red-100 text-red-700 dark:bg-red-800/30 dark:text-red-300"
            }`}>
              {health.mcp_firewall?.status || "unknown"}
            </span>
            {health.mcp_firewall?.detail && (
              <p className="text-xs text-slate-500 mt-1">{health.mcp_firewall.detail}</p>
            )}
          </div>
        </div>
      )}
    </div>
  );

  /* ────────── main render ────────── */

  return (
    <div className="space-y-4">
      {/* Tab bar */}
      <div
        role="tablist"
        aria-label="MCP Connector sections"
        className="sticky top-0 z-10 flex gap-1 border-b border-slate-200 dark:border-slate-700 bg-white/80 dark:bg-slate-900/80 backdrop-blur supports-[backdrop-filter]:bg-white/60 supports-[backdrop-filter]:dark:bg-slate-900/60 overflow-x-auto scrollbar-thin"
      >
        {TABS.map(({ id, label, icon: Icon }) => {
          const count =
            id === "servers" ? servers.length :
            id === "tools" ? tools.length :
            id === "protection" ? guardrails.length :
            id === "observability" ? events.length :
            null;
          const isActive = tab === id;
          return (
            <button
              key={id}
              role="tab"
              aria-selected={isActive}
              aria-controls={`mcp-tab-${id}`}
              onClick={() => setTab(id)}
              className={`group flex items-center gap-1.5 px-3 py-2 text-sm font-medium border-b-2 transition-colors whitespace-nowrap focus:outline-none focus-visible:ring-2 focus-visible:ring-blue-500 focus-visible:ring-offset-1 dark:focus-visible:ring-offset-slate-900 rounded-t ${
                isActive
                  ? "border-blue-500 text-blue-600 dark:text-blue-400"
                  : "border-transparent text-slate-500 hover:text-slate-700 hover:border-slate-300 dark:text-slate-400 dark:hover:text-slate-200"
              }`}
            >
              <Icon className="w-4 h-4" />
              <span>{label}</span>
              {count != null && count > 0 && (
                <span
                  className={`ml-1 inline-flex items-center justify-center min-w-[1.25rem] h-5 px-1.5 rounded-full text-[10px] font-semibold tabular-nums ${
                    isActive
                      ? "bg-blue-100 text-blue-700 dark:bg-blue-900/50 dark:text-blue-200"
                      : "bg-slate-100 text-slate-600 group-hover:bg-slate-200 dark:bg-slate-800 dark:text-slate-300"
                  }`}
                  aria-label={`${count} ${label}`}
                >
                  {count > 999 ? "999+" : count}
                </span>
              )}
            </button>
          );
        })}
      </div>

      {/* Error banner */}
      {error && (
        <div className="flex items-center gap-2 p-3 rounded-lg bg-red-50 dark:bg-red-900/20 text-red-700 dark:text-red-300 text-sm">
          <AlertTriangle className="w-4 h-4 shrink-0" />
          {error}
          <button onClick={() => setError(null)} className="ml-auto text-red-500 hover:text-red-700">
            <X className="w-4 h-4" />
          </button>
        </div>
      )}

      {/* Loading */}
      {loading && (
        <div className="flex items-center justify-center py-6">
          <Loader2 className="w-5 h-5 animate-spin text-blue-500" />
        </div>
      )}

      {/* Tab content */}
      {!loading && tab === "servers" && renderServers()}
      {!loading && tab === "tools" && renderTools()}
      {!loading && tab === "execute" && renderExecute()}
      {!loading && tab === "protection" && renderProtection()}
      {!loading && tab === "observability" && renderObservability()}
      {!loading && tab === "health" && renderHealth()}
    </div>
  );
}
