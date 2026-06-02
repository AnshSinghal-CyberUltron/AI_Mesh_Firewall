/**
 * MCPConnectorPanel — ZeroShield MCP integration management.
 *
 * Backend proxy: /api/mcp-connector/*
 */

import { useState, useEffect, useCallback, useRef } from "react";
import {
  Server,
  Shield,
  CheckCircle,
  Loader2,
  RefreshCw,
  Plus,
  Trash2,
  Tag,
  Wrench,
  Play,
  Activity,
  AlertTriangle,
  Copy,
  Eye,
  EyeOff,
  BarChart3,
  Clock,
  Ban,
  Hash,
  Key,
  Layers,
  ArrowRight,
} from "lucide-react";
import { useAuth } from "../context/AuthContext";
import { toAbsoluteGatewayUrl, resolveGatewayBaseUrl } from "../utils/environmentUrls";
import { MCPScanControlMatrix } from "./MCPScanControlMatrix";
import { PolicyManagementPanel } from "./PolicyManagementPanel";

import { Card, CardContent } from "./ui/Card";
import { Button } from "./ui/Button";
import { Badge } from "./ui/Badge";
import { Spinner } from "./ui/Spinner";
import { Tabs, TabsList, TabsTrigger, TabsContent } from "./ui/Tabs";
import { Dialog, DialogHeader, DialogBody, DialogFooter } from "./ui/Dialog";
import { Switch } from "./ui/Switch";
import { Tooltip } from "./ui/Tooltip";
import { Select } from "./ui/Select";
import { SegmentedControl } from "./ui/SegmentedControl";
import { ToastProvider, useToast } from "./ui/Toast";
import { EmptyState } from "./ui/EmptyState";
import { Skeleton } from "./ui/Skeleton";
import { PanelHeader } from "./ui/PanelHeader";
import {
  connectionInfo,
  riskBadge,
  sensitivityBadge,
  actionInfo,
  decisionInfo,
} from "../lib/mcpColors";

/* ────────────── helpers ────────────── */

const TRANSPORT_OPTIONS = [
  { value: "streamable-http", label: "Streamable HTTP", supported: true },
  { value: "sse", label: "SSE", supported: true },
  // stdio spawns a subprocess INSIDE the gateway container. The gateway now
  // ships node/npx/python/python3, so npx-based servers (Linear/Playwright)
  // launch. The command must be one of the allow-listed interpreters and the
  // package's own runtime dependency must be present in the container (e.g.
  // Semgrep MCP needs the `semgrep` binary). Failures surface a clear
  // actionable error in last_sync_error rather than a silent "disconnected".
  { value: "stdio", label: "Stdio (subprocess)", supported: true },
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
  { value: "oauth", label: "OAuth 2.1 (authorize via provider)" },
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

/** Compact stat card for the status strip. */
function StatCard({ icon: Icon, label, value, sub, tone = "slate" }) {
  const toneStyles = {
    slate: "text-slate-500 dark:text-slate-400",
    teal: "text-teal-600 dark:text-teal-400",
    emerald: "text-emerald-600 dark:text-emerald-400",
    red: "text-red-600 dark:text-red-400",
    amber: "text-amber-600 dark:text-amber-400",
    blue: "text-blue-600 dark:text-blue-400",
  };
  return (
    <Card className="shadow-none">
      <CardContent className="flex items-center gap-3 p-4">
        {Icon ? (
          <div className={`flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-slate-100 dark:bg-slate-700/60 ${toneStyles[tone]}`}>
            <Icon className="h-5 w-5" aria-hidden="true" />
          </div>
        ) : null}
        <div className="min-w-0">
          <p className="text-lg font-semibold leading-tight text-slate-900 dark:text-white tabular-nums">{value}</p>
          <p className="text-[11px] text-slate-500 dark:text-slate-400 truncate">{label}</p>
          {sub ? <p className="text-[10px] text-slate-400 dark:text-slate-500 truncate">{sub}</p> : null}
        </div>
      </CardContent>
    </Card>
  );
}

const EVENTS_PAGE_SIZE = 50;

const TABS = [
  { id: "servers", label: "MCP Servers", icon: Server },
  { id: "tools", label: "Tool Discovery", icon: Wrench },
  { id: "execute", label: "Tool Execution", icon: Play },
  { id: "scan-matrix", label: "Scan Controls", icon: Layers },
  { id: "protection", label: "MCP Security Policies", icon: Shield },
  { id: "observability", label: "Observability", icon: BarChart3 },
  { id: "health", label: "Services Health", icon: Activity },
];

/* ════════════════════════════════════════════════════ */
function MCPConnectorPanelInner() {
  const { fetchWithAuth, user } = useAuth();
  const { toast } = useToast();

  const [tab, setTab] = useState("servers");
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

  /* ── MCP policy server filter ── */
  const [policyServerFilter, setPolicyServerFilter] = useState("");

  /* ── health ── */
  const [health, setHealth] = useState(null);

  /* ── observability ── */
  const [events, setEvents] = useState([]);
  const [eventSummary, setEventSummary] = useState(null);
  // BUG FIX (c): client-side pagination instead of rendering all 500 events.
  const [eventsVisible, setEventsVisible] = useState(EVENTS_PAGE_SIZE);

  /* ── server tools (per-server control) ── */
  const [serverToolsMap, setServerToolsMap] = useState({});
  const [expandedServer, setExpandedServer] = useState(null);
  const [copiedEndpoint, setCopiedEndpoint] = useState(null);
  const [syncingServer, setSyncingServer] = useState(null);
  const [keyRevealed, setKeyRevealed] = useState(false);

  /* ── org gateway key (auto-provisioned for MCP) ── */
  const [orgGatewayKey, setOrgGatewayKey] = useState(null); // { has_gateway_key, prefix, key, ... }

  /* ── OAuth polling interval (BUG FIX a: tracked + cleaned up) ── */
  const oauthPollRef = useRef(null);

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
      setEventsVisible(EVENTS_PAGE_SIZE); // BUG FIX (c): reset paging on reload
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
    if (tab === "protection") { loadServers(); }
    if (tab === "observability") { loadEvents(); loadEventSummary(); }
    if (tab === "health") loadHealth();
  }, [tab, loadServers, loadTools, loadHealth, loadEvents, loadEventSummary, loadOrgGatewayKey]);

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
      toast(`Registered "${payload.name}"`, { tone: "success" });
    } catch (e) {
      setError(`Add server failed: ${e.message}`);
      toast(`Add server failed: ${e.message}`, { tone: "error" });
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
      toast("Server deleted", { tone: "success" });
    } catch (e) {
      setError(`Delete failed: ${e.message}`);
      toast(`Delete failed: ${e.message}`, { tone: "error" });
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
      toast(`Registered "${preset.name}"`, { tone: "success" });
    } catch (e) {
      setError(`Preset registration failed: ${e.message}`);
      toast(`Preset registration failed: ${e.message}`, { tone: "error" });
    } finally {
      setAddSaving(false);
    }
  };

  const syncServerTools = async (serverId) => {
    setSyncingServer(serverId);
    try {
      const res = await fetchWithAuth(`/api/mcp-connector/servers/${serverId}/tools/`, { method: "POST" });
      // A2 contract: the sync endpoint returns HTTP 200 even on upstream
      // failure, carrying { synced, pruned, error, connection_status } in the
      // body. A transport/handshake/auth error is reported via body.error with
      // connection_status="failed" — NOT via a non-2xx status. So we must
      // inspect the body instead of relying on res.ok alone.
      const body = await res.json().catch(() => ({}));
      if (!res.ok) {
        throw new Error(body.detail || body.error || `HTTP ${res.status}`);
      }
      if (body.error || body.connection_status === "failed") {
        setError(`Sync failed for server: ${body.error || "upstream discovery error"}`);
        toast(`Sync failed: ${body.error || "upstream discovery error"}`, { tone: "error" });
      } else {
        setError(null);
        toast(`Synced ${body.synced ?? ""} tool${body.synced === 1 ? "" : "s"}`.replace(/\s+/g, " ").trim(), { tone: "success" });
      }
      await loadServerTools(serverId);
      await loadServers();
    } catch (e) {
      setError(`Sync tools failed: ${e.message}`);
      toast(`Sync tools failed: ${e.message}`, { tone: "error" });
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
      toast(`Toggle tool failed: ${e.message}`, { tone: "error" });
    }
  };

  // Per-tool scan enforcement override. "inherit" falls back to the server's
  // default_scan_action (which itself defaults to "tag"). Block short-circuits
  // at the gateway.
  const setToolScanAction = async (serverId, toolName, action) => {
    try {
      const res = await fetchWithAuth(`/api/mcp-connector/servers/${serverId}/tools/${encodeURIComponent(toolName)}/`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ scan_action: action }),
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      await loadServerTools(serverId);
      toast(`Tool scan action: ${action}`, { tone: "success" });
    } catch (e) {
      setError(`Update scan action failed: ${e.message}`);
      toast(`Update scan action failed: ${e.message}`, { tone: "error" });
    }
  };

  // Server-level default scan enforcement action.
  const setServerScanDefault = async (serverId, action) => {
    try {
      const res = await fetchWithAuth(`/api/mcp-connector/servers/${serverId}/`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ default_scan_action: action }),
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      await loadServers();
      toast(`Server scan default: ${action}`, { tone: "success" });
    } catch (e) {
      setError(`Update server scan default failed: ${e.message}`);
      toast(`Update server scan default failed: ${e.message}`, { tone: "error" });
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
      toast(`OAuth start failed: ${message}`, { tone: "error" });
    } finally {
      setOauthBusy(null);
    }
  };

  /**
   * Control-plane OAuth 2.1 flow for HTTP MCP servers (auth_type === "oauth").
   * Unlike startOAuth (gateway-side, for stdio mcp-remote), this calls the
   * control authorize endpoint which performs RFC 9728/8414 discovery + RFC
   * 7591 dynamic client registration, then returns a provider consent URL.
   * The popup completes at the control callback, which exchanges the code and
   * stores the per-org encrypted token. We poll the org-scoped server detail
   * for oauth_authorized, then auto-sync tools. No credential ever touches the
   * browser — the token lives only in the org's encrypted DB row.
   */
  const startControlOAuth = async (srv) => {
    // Pre-open popup SYNCHRONOUSLY (same call stack as click) to dodge blockers.
    const popup = window.open("about:blank", "mcp-oauth-2-1", "width=620,height=760");
    if (!popup) {
      setError("Popup blocked. Please allow popups for this site and try again.");
      return;
    }
    setOauthBusy(srv.id);
    setError(null);
    try {
      const res = await fetchWithAuth(`/api/mcp-connector/servers/${srv.id}/oauth/authorize/`, { method: "POST" });
      const data = await res.json().catch(() => ({}));
      if (!res.ok || !data.authorize_url) {
        popup.close();
        throw new Error(data.error || data.detail || `HTTP ${res.status}`);
      }
      popup.location.href = data.authorize_url;

      // BUG FIX (a): clear any previous poll before starting a new one.
      if (oauthPollRef.current) {
        clearInterval(oauthPollRef.current);
        oauthPollRef.current = null;
      }

      // Poll the org-scoped server detail until the callback stores the token.
      let elapsed = 0;
      const interval = setInterval(async () => {
        elapsed += 3000;
        try {
          const r = await fetchWithAuth(`/api/mcp-connector/servers/${srv.id}/`);
          if (r.ok) {
            const s = await r.json();
            if (s.oauth_authorized) {
              clearInterval(interval);
              oauthPollRef.current = null; // BUG FIX (a): clear ref on success
              setOauthBusy(null);
              if (!popup.closed) popup.close();
              await loadServers();
              await syncServerTools(srv.id);
              toast("OAuth authorized", { tone: "success" });
              return;
            }
          }
        } catch (_) { /* transient — keep polling */ }
        if (elapsed >= 180000 || popup.closed) {
          clearInterval(interval);
          oauthPollRef.current = null; // BUG FIX (a): clear ref on timeout/close
          setOauthBusy(null);
          await loadServers();
        }
      }, 3000);
      oauthPollRef.current = interval; // BUG FIX (a): track for unmount cleanup
    } catch (e) {
      if (popup && !popup.closed) popup.close();
      setOauthBusy(null);
      setError(`OAuth authorize failed: ${e.message}`);
      toast(`OAuth authorize failed: ${e.message}`, { tone: "error" });
    }
  };

  // BUG FIX (a): clear the OAuth poll interval on unmount.
  useEffect(() => {
    return () => {
      if (oauthPollRef.current) {
        clearInterval(oauthPollRef.current);
        oauthPollRef.current = null;
      }
    };
  }, []);

  // BUG FIX (b): OAuth popup completion handler wrapped in useCallback with
  // explicit deps so the listener never reads a stale `servers` closure.
  const handleOAuthMessage = useCallback(
    (event) => {
      if (event.data?.type === "mcp-oauth-complete") {
        loadServers();
        // Auto-sync tools after successful OAuth
        const srv = servers.find((s) => s.server_slug === event.data.server);
        if (srv) {
          setTimeout(() => syncServerTools(srv.id), 1000);
        }
      }
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [servers, loadServers]
  );

  // Listen for OAuth popup completion
  useEffect(() => {
    window.addEventListener("message", handleOAuthMessage);
    return () => window.removeEventListener("message", handleOAuthMessage);
  }, [handleOAuthMessage]);

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

  /* ────────── derived status-strip values ────────── */

  const connectedCount = servers.filter(
    (s) => connectionInfo(s.connection_status).badge === "success"
  ).length;
  const toolsDiscovered = servers.reduce((acc, s) => acc + (s.tools_count || 0), 0);
  const tier2On =
    health?.mcp_tier2_enabled ??
    health?.tier2_enabled ??
    (Array.isArray(health?.builtin_detectors) && health.builtin_detectors.length > 0
      ? true
      : null);
  const decisionCounts = eventSummary?.decisions || {};

  /* ────────── renderers ────────── */

  const renderGatewayKeyBanner = () => {
    if (orgGatewayKey?.has_gateway_key) {
      const masked = orgGatewayKey.prefix
        ? `${orgGatewayKey.prefix}••••••••••••••••`
        : "••••••••••••••••";
      return (
        <Card className="border-teal-200 bg-teal-50/60 dark:border-teal-800 dark:bg-teal-900/20 shadow-none">
          <CardContent className="flex items-start gap-3 p-4">
            <Key className="w-4 h-4 text-teal-600 dark:text-teal-400 shrink-0 mt-0.5" />
            <div className="flex-1 min-w-0">
              <p className="text-xs font-medium text-teal-800 dark:text-teal-300">Gateway API Key Active</p>
              <div className="mt-1.5 flex items-center gap-2 flex-wrap">
                <code className="text-[11px] bg-teal-100 dark:bg-teal-900/40 px-2 py-1 rounded font-mono text-teal-700 dark:text-teal-300 break-all">
                  {keyRevealed && orgGatewayKey.key ? orgGatewayKey.key : masked}
                </code>
                {orgGatewayKey.key && (
                  <Tooltip content={keyRevealed ? "Hide key" : "Reveal key"}>
                    <Button
                      variant="ghost"
                      size="icon"
                      className="h-7 w-7"
                      aria-label={keyRevealed ? "Hide gateway API key" : "Reveal gateway API key"}
                      onClick={() => setKeyRevealed((v) => !v)}
                    >
                      {keyRevealed ? <EyeOff className="w-3.5 h-3.5" /> : <Eye className="w-3.5 h-3.5" />}
                    </Button>
                  </Tooltip>
                )}
                {orgGatewayKey.key && (
                  <Tooltip content="Copy gateway API key">
                    <Button
                      variant="ghost"
                      size="icon"
                      className="h-7 w-7"
                      aria-label="Copy gateway API key"
                      onClick={() => {
                        navigator.clipboard.writeText(orgGatewayKey.key);
                        setCopiedEndpoint("gw-key");
                        setTimeout(() => setCopiedEndpoint(null), 3000);
                        toast("Gateway key copied", { tone: "success" });
                      }}
                    >
                      {copiedEndpoint === "gw-key"
                        ? <CheckCircle className="w-3.5 h-3.5 text-teal-500" />
                        : <Copy className="w-3.5 h-3.5" />}
                    </Button>
                  </Tooltip>
                )}
              </div>
              {orgGatewayKey.key && (
                <p className="text-[10px] text-amber-600 dark:text-amber-400 mt-1.5 flex items-center gap-1">
                  <AlertTriangle className="w-3 h-3" />
                  Copy this key now — it won&apos;t be shown again.
                </p>
              )}
            </div>
          </CardContent>
        </Card>
      );
    }
    if (orgGatewayKey && !orgGatewayKey.has_gateway_key) {
      return (
        <Card className="border-amber-200 bg-amber-50/60 dark:border-amber-800 dark:bg-amber-900/20 shadow-none">
          <CardContent className="flex items-center gap-3 p-4">
            <Key className="w-4 h-4 text-amber-600 dark:text-amber-400 shrink-0" />
            <p className="flex-1 text-xs text-amber-800 dark:text-amber-300">
              No gateway API key provisioned. MCP Config snippets will use a placeholder.
            </p>
            <Button variant="default" size="sm" onClick={provisionGatewayKey} className="bg-amber-600 hover:bg-amber-700">
              Provision Key
            </Button>
          </CardContent>
        </Card>
      );
    }
    return null;
  };

  const renderServerCard = (srv) => {
    const conn = connectionInfo(srv.connection_status);
    const risk = riskBadge(srv.risk_level);
    const absUrl = srv.gateway_endpoint ? toAbsoluteGatewayUrl(srv.gateway_endpoint) : null;
    return (
      <Card key={srv.id}>
        <CardContent className="p-4">
          <div className="flex items-start justify-between gap-3">
            <div className="flex-1 min-w-0">
              <h4 className="font-medium text-slate-900 dark:text-white flex items-center gap-2 flex-wrap">
                <Server className="w-4 h-4 text-teal-500 shrink-0" />
                {srv.name}
                {srv.is_active && <CheckCircle className="w-3.5 h-3.5 text-emerald-500 shrink-0" />}
                <Badge variant={conn.badge}>
                  <span className={`mr-1.5 inline-block w-1.5 h-1.5 rounded-full ${conn.dot}`} />
                  {conn.label}
                </Badge>
                {srv.risk_level && srv.risk_level !== "low" && (
                  <Badge variant={risk}>{srv.risk_level} risk</Badge>
                )}
                {srv.needs_reauth && <Badge variant="warning">needs re-auth</Badge>}
              </h4>
              <p className="text-xs text-slate-500 dark:text-slate-400 mt-1 break-all">{srv.url}</p>
              {srv.description && <p className="text-xs text-slate-400 mt-0.5">{srv.description}</p>}
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
              {srv.last_sync_error && (
                <div className="mt-2 flex items-start gap-1.5 text-[10px] text-red-700 dark:text-red-300 bg-red-50 dark:bg-red-900/20 border border-red-200 dark:border-red-800/40 rounded px-2 py-1">
                  <AlertTriangle className="w-3 h-3 shrink-0 mt-0.5" />
                  <span className="break-all">{srv.last_sync_error}</span>
                </div>
              )}
              {srv.gateway_endpoint && (
                <div className="mt-2 space-y-1.5">
                  <div className="flex items-center gap-1.5">
                    <code className="text-[10px] bg-slate-100 dark:bg-slate-700 px-2 py-1 rounded font-mono text-slate-600 dark:text-slate-300 break-all">
                      {absUrl}
                    </code>
                    <Tooltip content="Copy gateway URL only">
                      <Button
                        variant="ghost"
                        size="icon"
                        className="h-7 w-7"
                        aria-label="Copy gateway URL"
                        onClick={() => copyEndpoint(absUrl)}
                      >
                        {copiedEndpoint === absUrl
                          ? <CheckCircle className="w-3.5 h-3.5 text-emerald-500" />
                          : <Copy className="w-3.5 h-3.5" />}
                      </Button>
                    </Tooltip>
                  </div>
                  <Button
                    variant="outline"
                    size="sm"
                    onClick={() => copyMcpConfig(srv)}
                    className="text-[11px]"
                    title="Copy full MCP config JSON with auth headers (paste into VS Code / Cursor mcp.json)"
                  >
                    {copiedEndpoint === `config:${srv.id}`
                      ? <><CheckCircle className="w-3 h-3 text-emerald-500" /> Config Copied!</>
                      : <><Copy className="w-3 h-3" /> {orgGatewayKey?.key ? "Copy MCP Config" : "Copy MCP Config (key placeholder)"}</>}
                  </Button>
                </div>
              )}
            </div>
            <div className="flex items-center gap-2 shrink-0">
              <Badge variant="secondary">{srv.transport}</Badge>
              <Select
                value={srv.default_scan_action || "tag"}
                onChange={(e) => setServerScanDefault(srv.id, e.target.value)}
                aria-label="Default scan enforcement"
                className="w-auto text-[11px] py-1"
                title="Default scan enforcement after Tier-1/Tier-2 (tag = observe only)"
              >
                <option value="tag">Scan: tag</option>
                <option value="redact">Scan: redact</option>
                <option value="block">Scan: block</option>
              </Select>
              {serverNeedsOAuth(srv) && (
                <Tooltip content="Authorize OAuth — opens popup for upstream provider login">
                  <Button
                    variant="outline"
                    size="sm"
                    onClick={() => startOAuth(srv)}
                    disabled={oauthBusy === srv.id}
                    className="text-indigo-600 dark:text-indigo-400"
                  >
                    {oauthBusy === srv.id ? <Loader2 className="w-3 h-3 animate-spin" /> : <Shield className="w-3 h-3" />}
                    Authorize
                  </Button>
                </Tooltip>
              )}
              {srv.auth_type === "oauth" && (
                <Tooltip
                  content={srv.oauth_authorized
                    ? "Re-authorize OAuth 2.1 — opens provider consent popup"
                    : "Authorize OAuth 2.1 — token is stored encrypted per-org"}
                >
                  <Button
                    variant="outline"
                    size="sm"
                    onClick={() => startControlOAuth(srv)}
                    disabled={oauthBusy === srv.id}
                    className={srv.oauth_authorized ? "text-emerald-600 dark:text-emerald-400" : "text-indigo-600 dark:text-indigo-400"}
                  >
                    {oauthBusy === srv.id ? <Loader2 className="w-3 h-3 animate-spin" /> : <Shield className="w-3 h-3" />}
                    {srv.oauth_authorized ? "Re-authorize" : "Authorize"}
                  </Button>
                </Tooltip>
              )}
              <Tooltip content="Sync tools from server">
                <Button
                  variant="ghost"
                  size="icon"
                  className="h-8 w-8"
                  aria-label="Sync tools from server"
                  onClick={() => syncServerTools(srv.id)}
                  disabled={syncingServer === srv.id}
                >
                  {syncingServer === srv.id ? <Loader2 className="w-4 h-4 animate-spin" /> : <RefreshCw className="w-4 h-4" />}
                </Button>
              </Tooltip>
              <Tooltip content="View tool controls">
                <Button
                  variant="ghost"
                  size="icon"
                  className="h-8 w-8"
                  aria-label="View tool controls"
                  onClick={() => {
                    if (expandedServer === srv.id) {
                      setExpandedServer(null);
                    } else {
                      setExpandedServer(srv.id);
                      if (!serverToolsMap[srv.id]) loadServerTools(srv.id);
                    }
                  }}
                >
                  <Eye className="w-4 h-4" />
                </Button>
              </Tooltip>
              <Tooltip content="Delete server">
                <Button
                  variant="ghost"
                  size="icon"
                  className="h-8 w-8 text-red-500 hover:text-red-700"
                  aria-label="Delete server"
                  onClick={() => deleteServerById(srv.id)}
                >
                  <Trash2 className="w-4 h-4" />
                </Button>
              </Tooltip>
            </div>
          </div>

          {/* Expanded tool controls */}
          {expandedServer === srv.id && (
            <div className="mt-3 border-t border-slate-200 dark:border-slate-700 pt-3">
              <div className="flex items-center justify-between mb-2">
                <span className="text-xs font-medium text-slate-600 dark:text-slate-300">Tool Controls</span>
                <Button
                  variant="link"
                  size="sm"
                  onClick={() => syncServerTools(srv.id)}
                  disabled={syncingServer === srv.id}
                  className="h-auto p-0 text-xs"
                >
                  {syncingServer === srv.id ? <Loader2 className="w-3 h-3 animate-spin" /> : <RefreshCw className="w-3 h-3" />}
                  Sync
                </Button>
              </div>
              {(!serverToolsMap[srv.id] || serverToolsMap[srv.id].length === 0) && (
                <p className="text-[10px] text-slate-400 text-center py-3">
                  No tools synced. Click &quot;Sync&quot; to discover tools from this server.
                </p>
              )}
              <div className="space-y-1.5">
                {(serverToolsMap[srv.id] || []).map((tool) => (
                  <div key={tool.tool_name} className="flex items-center justify-between bg-slate-50 dark:bg-slate-900 rounded px-3 py-2 gap-3">
                    <div className="flex items-center gap-2 min-w-0">
                      <Switch
                        checked={!!tool.enabled}
                        onCheckedChange={() => toggleToolEnabled(srv.id, tool.tool_name, tool.enabled)}
                        label={tool.enabled ? `Disable ${tool.tool_name}` : `Enable ${tool.tool_name}`}
                      />
                      <span className={`text-xs font-mono truncate ${tool.enabled ? "text-slate-900 dark:text-white" : "text-slate-400 line-through"}`}>
                        {tool.tool_name}
                      </span>
                    </div>
                    <div className="flex items-center gap-2 shrink-0">
                      <Select
                        value={tool.scan_action || "inherit"}
                        onChange={(e) => setToolScanAction(srv.id, tool.tool_name, e.target.value)}
                        aria-label={`Scan enforcement for ${tool.tool_name}`}
                        className="w-auto text-[10px] py-1"
                        title="Scan enforcement for this tool (inherit = server default)"
                      >
                        <option value="inherit">Scan: inherit</option>
                        <option value="tag">Scan: tag</option>
                        <option value="redact">Scan: redact</option>
                        <option value="block">Scan: block</option>
                      </Select>
                      <Badge variant={sensitivityBadge(tool.sensitivity)}>{tool.sensitivity}</Badge>
                    </div>
                  </div>
                ))}
              </div>
            </div>
          )}
        </CardContent>
      </Card>
    );
  };

  const renderServers = () => (
    <div className="space-y-4">
      <div className="flex items-center justify-between gap-3 flex-wrap">
        <p className="text-sm text-slate-500 dark:text-slate-400 max-w-xl">
          MCP servers managed by the ZeroShield MCP control plane.
          Organizations can connect their own or public MCP servers here.
        </p>
        <div className="flex gap-2 flex-wrap justify-end">
          {MCP_PRESETS.map((preset) => (
            <Button
              key={preset.name}
              variant="outline"
              size="sm"
              onClick={() => registerPreset(preset)}
              disabled={addSaving}
              title={`Quick register ${preset.name}`}
            >
              {preset.name}
              {preset.requiresAuth && <span className="text-[10px] text-amber-600">(auth)</span>}
            </Button>
          ))}
        </div>
      </div>

      {renderGatewayKeyBanner()}

      {servers.length === 0 && !loading ? (
        <EmptyState
          icon={Server}
          title="No MCP servers registered yet"
          description='Click "Register Server" to connect an MCP server for centralized discovery and governance.'
          action={<Button onClick={() => setAddOpen(true)}><Plus className="w-4 h-4" /> Register Server</Button>}
        />
      ) : (
        <div className="grid gap-3">
          {servers.map((srv) => renderServerCard(srv))}
        </div>
      )}

      {/* ── Add Server Modal ── */}
      <Dialog open={addOpen} onClose={() => setAddOpen(false)} labelledBy="mcp-add-server-title">
        <DialogHeader
          id="mcp-add-server-title"
          title="Register MCP Server"
          description="Register an MCP server endpoint for centralized ZeroShield discovery and governance."
          onClose={() => setAddOpen(false)}
        />
        <DialogBody>
          {/* Connection section */}
          <section className="space-y-3">
            <h4 className="text-xs font-semibold uppercase tracking-wide text-slate-400">Connection</h4>
            <div>
              <label className="block text-sm font-medium mb-1">Name</label>
              <input
                className="w-full border rounded-lg px-3 py-2 text-sm dark:bg-slate-800 dark:border-slate-600"
                value={addForm.name}
                onChange={(e) => setAddForm({ ...addForm, name: e.target.value })}
                placeholder="my-mcp-server"
              />
            </div>
            <div>
              <label className="block text-sm font-medium mb-1">URL</label>
              <input
                className="w-full border rounded-lg px-3 py-2 text-sm dark:bg-slate-800 dark:border-slate-600"
                value={addForm.url}
                onChange={(e) => setAddForm({ ...addForm, url: e.target.value })}
                placeholder={addForm.transport === "stdio" ? "(not required for stdio)" : "https://my-server.example.com/mcp"}
                disabled={addForm.transport === "stdio"}
              />
            </div>
            <div>
              <label className="block text-sm font-medium mb-1">Transport</label>
              <Select
                value={addForm.transport}
                onChange={(e) => setAddForm({ ...addForm, transport: e.target.value })}
                aria-label="Transport"
              >
                {TRANSPORT_OPTIONS.map((t) => (
                  <option key={t.value} value={t.value} disabled={!t.supported}>
                    {t.label}
                  </option>
                ))}
              </Select>
              <p className="text-xs text-slate-500 mt-1">
                Supports HTTP, SSE, WebSocket, and Stdio (subprocess) transports.
                Stdio runs the command inside the gateway container.
              </p>
            </div>
            <div>
              <label className="block text-sm font-medium mb-1">Description</label>
              <textarea
                className="w-full border rounded-lg px-3 py-2 text-sm dark:bg-slate-800 dark:border-slate-600"
                rows={2}
                value={addForm.description}
                onChange={(e) => setAddForm({ ...addForm, description: e.target.value })}
                placeholder="Optional description"
              />
            </div>
          </section>

          {/* Stdio-specific section */}
          {addForm.transport === "stdio" && (
            <section className="border border-teal-200 dark:border-teal-800 rounded-lg p-3 space-y-3 bg-teal-50/50 dark:bg-teal-900/20">
              <h4 className="text-xs font-semibold uppercase tracking-wide text-teal-700 dark:text-teal-300">Stdio Transport Settings</h4>
              <p className="text-xs text-teal-700/80 dark:text-teal-300/80">
                Command must be an allow-listed interpreter (<code>npx</code>, <code>node</code>,
                <code> python</code>, <code>python3</code>) — not a path. The package's own
                runtime dependency must also be installed in the gateway container
                (e.g. Semgrep MCP requires the <code>semgrep</code> binary). Missing
                dependencies surface a clear error below the server.
              </p>
              <div>
                <label className="block text-sm font-medium mb-1">Command</label>
                <input
                  className="w-full border rounded-lg px-3 py-2 text-sm dark:bg-slate-800 dark:border-slate-600"
                  value={addForm.command || ""}
                  onChange={(e) => setAddForm({ ...addForm, command: e.target.value })}
                  placeholder="npx"
                />
              </div>
              <div>
                <label className="block text-sm font-medium mb-1">Arguments (comma-separated)</label>
                <input
                  className="w-full border rounded-lg px-3 py-2 text-sm dark:bg-slate-800 dark:border-slate-600"
                  value={Array.isArray(addForm.args) ? addForm.args.join(", ") : ""}
                  onChange={(e) => setAddForm({ ...addForm, args: e.target.value.split(",").map((s) => s.trim()).filter(Boolean) })}
                  placeholder="-y, @playwright/mcp@latest"
                />
              </div>
              <div>
                <label className="block text-sm font-medium mb-1">Environment Variables (KEY=VALUE, one per line)</label>
                <textarea
                  className="w-full border rounded-lg px-3 py-2 text-sm font-mono dark:bg-slate-800 dark:border-slate-600"
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
            </section>
          )}

          {/* Authentication section */}
          <section className="border border-slate-200 dark:border-slate-700 rounded-lg p-3 space-y-3">
            <h4 className="text-xs font-semibold uppercase tracking-wide text-slate-400">Authentication</h4>
            <div>
              <label className="block text-sm font-medium mb-1">Upstream Authentication</label>
              <Select
                value={addForm.auth_type}
                onChange={(e) => setAddForm({ ...addForm, auth_type: e.target.value })}
                aria-label="Upstream authentication type"
              >
                {AUTH_OPTIONS.map((opt) => (
                  <option key={opt.value} value={opt.value}>{opt.label}</option>
                ))}
              </Select>
            </div>

            {addForm.auth_type === "bearer" && (
              <div>
                <label className="block text-sm font-medium mb-1">Bearer Token</label>
                <input
                  type="password"
                  className="w-full border rounded-lg px-3 py-2 text-sm dark:bg-slate-800 dark:border-slate-600"
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
                    className="w-full border rounded-lg px-3 py-2 text-sm dark:bg-slate-800 dark:border-slate-600"
                    value={addForm.auth_username}
                    onChange={(e) => setAddForm({ ...addForm, auth_username: e.target.value })}
                    placeholder="username"
                  />
                </div>
                <div>
                  <label className="block text-sm font-medium mb-1">Password</label>
                  <input
                    type="password"
                    className="w-full border rounded-lg px-3 py-2 text-sm dark:bg-slate-800 dark:border-slate-600"
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
                        className="w-full border rounded-lg px-3 py-2 text-sm dark:bg-slate-800 dark:border-slate-600"
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
                        className="w-full border rounded-lg px-3 py-2 text-sm dark:bg-slate-800 dark:border-slate-600"
                        value={header.value}
                        onChange={(e) => {
                          const nextHeaders = [...(addForm.auth_headers || [])];
                          nextHeaders[index] = { ...nextHeaders[index], value: e.target.value };
                          setAddForm({ ...addForm, auth_headers: nextHeaders });
                        }}
                        placeholder="Bearer ..."
                      />
                    </div>
                    <Button
                      type="button"
                      variant="ghost"
                      size="icon"
                      aria-label="Remove header"
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
                    </Button>
                  </div>
                ))}

                <Button
                  type="button"
                  variant="secondary"
                  size="sm"
                  onClick={() => {
                    setAddForm({
                      ...addForm,
                      auth_headers: [...(addForm.auth_headers || []), { key: "", value: "" }],
                    });
                  }}
                >
                  <Plus className="w-3 h-3" />
                  Add Header
                </Button>
              </div>
            )}

            {addForm.auth_type === "query_param" && (
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
                <div>
                  <label className="block text-sm font-medium mb-1">Param Key</label>
                  <input
                    className="w-full border rounded-lg px-3 py-2 text-sm dark:bg-slate-800 dark:border-slate-600"
                    value={addForm.auth_query_param_key}
                    onChange={(e) => setAddForm({ ...addForm, auth_query_param_key: e.target.value })}
                    placeholder="api_key"
                  />
                </div>
                <div>
                  <label className="block text-sm font-medium mb-1">Param Value</label>
                  <input
                    type="password"
                    className="w-full border rounded-lg px-3 py-2 text-sm dark:bg-slate-800 dark:border-slate-600"
                    value={addForm.auth_query_param_value}
                    onChange={(e) => setAddForm({ ...addForm, auth_query_param_value: e.target.value })}
                    placeholder="secret value"
                  />
                </div>
              </div>
            )}
          </section>
        </DialogBody>
        <DialogFooter>
          <Button variant="secondary" onClick={() => setAddOpen(false)}>Cancel</Button>
          <Button
            onClick={addServer}
            disabled={addSaving || !addForm.name || (addForm.transport === "stdio" ? !addForm.command : !addForm.url)}
          >
            {addSaving && <Loader2 className="w-4 h-4 animate-spin" />}
            Register
          </Button>
        </DialogFooter>
      </Dialog>
    </div>
  );

  const renderTools = () => (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <p className="text-sm text-slate-500 dark:text-slate-400">
          Tools discovered across all connected MCP servers.
        </p>
        <Tooltip content="Refresh">
          <Button variant="ghost" size="icon" aria-label="Refresh tools" onClick={loadTools}>
            <RefreshCw className="w-4 h-4" />
          </Button>
        </Tooltip>
      </div>

      {loading && (
        <div className="grid gap-2">
          {[0, 1, 2].map((i) => <Skeleton key={i} className="h-16 w-full" />)}
        </div>
      )}

      {!loading && Array.isArray(tools) && tools.length === 0 ? (
        <EmptyState
          icon={Wrench}
          title="No tools discovered yet"
          description="Register MCP servers first, then tools will appear here."
        />
      ) : (
        <div className="grid gap-2">
          {(Array.isArray(tools) ? tools : []).map((tool, idx) => (
            <Card key={tool.name || idx}>
              <CardContent className="p-3">
                <div className="flex items-start justify-between gap-2">
                  <div className="min-w-0">
                    <h4 className="font-mono text-sm font-medium text-slate-900 dark:text-white truncate">
                      {tool.name}
                    </h4>
                    {tool.description && (
                      <p className="text-xs text-slate-500 mt-0.5">{tool.description}</p>
                    )}
                  </div>
                  {tool.server_name && <Badge variant="info">{tool.server_name}</Badge>}
                </div>
                {tool.inputSchema && (
                  <details className="mt-2">
                    <summary className="text-xs text-slate-400 cursor-pointer">Input Schema</summary>
                    <pre className="text-[10px] mt-1 bg-slate-50 dark:bg-slate-900 rounded p-2 overflow-x-auto">
                      {JSON.stringify(tool.inputSchema, null, 2)}
                    </pre>
                  </details>
                )}
              </CardContent>
            </Card>
          ))}
        </div>
      )}
    </div>
  );

  const renderPolicies = () => {
    const selectedServer = servers.find((s) => s.server_slug === policyServerFilter);
    return (
      <div className="space-y-4">
        <div className="flex items-center gap-3 flex-wrap">
          <label className="text-sm font-medium text-slate-700 dark:text-slate-300">Filter policies by server:</label>
          <div className="min-w-[220px]">
            <Select
              value={policyServerFilter}
              onChange={(e) => setPolicyServerFilter(e.target.value)}
              aria-label="Filter policies by server"
            >
              <option value="">All MCP servers (org-wide)</option>
              {servers.map((s) => (
                <option key={s.id} value={s.server_slug}>{s.name} ({s.server_slug})</option>
              ))}
            </Select>
          </div>
          {policyServerFilter && (
            <Badge variant="info">
              <Server className="w-3 h-3 mr-1" />
              Filtering: {selectedServer?.name || policyServerFilter}
            </Badge>
          )}
        </div>
        <PolicyManagementPanel
          key={policyServerFilter || "__all__"}
          title="MCP Security Policies"
          description={
            policyServerFilter
              ? `Active rules for "${selectedServer?.name || policyServerFilter}" plus org-wide MCP policies — the single enforcement layer for every MCP tool call.`
              : "Organization-wide MCP policies — the single enforcement layer applied in-band to every MCP tool call across all servers."
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

  const renderScanMatrix = () => (
    <MCPScanControlMatrix fetchWithAuth={fetchWithAuth} servers={servers} />
  );

  const renderProtection = () => renderPolicies();

  const renderExecute = () => (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <p className="text-sm text-slate-500 dark:text-slate-400">
          Invoke a discovered MCP tool through ZeroShield orchestration.
        </p>
        <Tooltip content="Refresh tools">
          <Button variant="ghost" size="icon" aria-label="Refresh tools" onClick={loadTools}>
            <RefreshCw className="w-4 h-4" />
          </Button>
        </Tooltip>
      </div>

      <Card>
        <CardContent className="p-4 space-y-3">
          <div>
            <label className="block text-sm font-medium mb-1">Server</label>
            <Select
              value={executeServerSlug}
              onChange={(e) => setExecuteServerSlug(e.target.value)}
              aria-label="Execution server"
            >
              <option value="">Select server...</option>
              {servers.map((server) => (
                <option key={server.id} value={server.server_slug}>
                  {server.name} ({server.server_slug})
                </option>
              ))}
            </Select>
            <p className="mt-1 text-[11px] text-slate-500 dark:text-slate-400">
              Scope execution to one MCP server so the backend can route the tool call deterministically.
            </p>
          </div>

          <div>
            <label className="block text-sm font-medium mb-1">Tool</label>
            <Select
              value={executeToolKey}
              onChange={(e) => setExecuteToolKey(e.target.value)}
              disabled={!executeServerSlug}
              aria-label="Tool to execute"
            >
              <option value="">{executeServerSlug ? "Select tool..." : "Choose a server first..."}</option>
              {executableTools.map((tool, idx) => (
                <option key={`${makeExecuteToolKey(tool)}-${idx}`} value={makeExecuteToolKey(tool)}>
                  {tool.name || `tool-${idx}`} {tool.server_name ? `• ${tool.server_name}` : ""}
                </option>
              ))}
            </Select>
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
                <div className="flex flex-wrap justify-end gap-1">
                  {selectedExecuteTool.server_name && <Badge variant="info">{selectedExecuteTool.server_name}</Badge>}
                  {selectedExecuteTool.transport && <Badge variant="secondary">{selectedExecuteTool.transport}</Badge>}
                  {selectedExecuteTool.source && <Badge variant="outline">{selectedExecuteTool.source}</Badge>}
                </div>
              </div>
              {getToolSchema(selectedExecuteTool) && (
                <div className="flex items-center justify-between gap-3">
                  <p className="text-[11px] text-slate-500 dark:text-slate-400">
                    This tool publishes an input schema. Use it to seed valid JSON arguments.
                  </p>
                  <Button
                    type="button"
                    variant="outline"
                    size="sm"
                    onClick={() => setExecuteArguments(JSON.stringify(buildExampleFromSchema(getToolSchema(selectedExecuteTool)), null, 2))}
                  >
                    Fill from Schema
                  </Button>
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
              className="w-full border rounded-lg px-3 py-2 text-xs font-mono dark:bg-slate-800 dark:border-slate-600"
              rows={6}
              value={executeArguments}
              onChange={(e) => setExecuteArguments(e.target.value)}
            />
          </div>

          <div className="flex justify-end">
            <Button onClick={executeTool} disabled={executeBusy || !selectedExecuteTool}>
              {executeBusy ? <Loader2 className="w-4 h-4 animate-spin" /> : <Play className="w-4 h-4" />}
              Execute Tool
            </Button>
          </div>
        </CardContent>
      </Card>

      {executeResult && (
        <Card>
          <CardContent className="p-4">
            <div className="flex items-center justify-between gap-3 mb-2">
              <h4 className="text-sm font-medium">Execution Response</h4>
              <div className="flex flex-wrap gap-2">
                {executeResult.decision && (
                  <Badge variant={decisionInfo(executeResult.decision).badge}>
                    {decisionInfo(executeResult.decision).label}
                  </Badge>
                )}
                {executeResult.request_id && (
                  <Badge variant="secondary" className="font-mono">{executeResult.request_id}</Badge>
                )}
                {executeResult.status && (
                  <Badge variant="warning">HTTP {executeResult.status}</Badge>
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
          </CardContent>
        </Card>
      )}
    </div>
  );

  /** Compact two-tier scan-trace pipeline view (Tier-1 → Tier-2). */
  const renderScanTrace = (trace) => {
    if (!Array.isArray(trace) || trace.length === 0) return null;
    return (
      <div className="mt-2 flex flex-wrap items-center gap-1.5">
        {trace.map((step, i) => {
          const tier = step.tier || step.tier_label || `tier${i + 1}`;
          const dir = step.direction || step.io || "";
          const act = step.action || step.scan_action;
          const findings = step.finding_count ?? (Array.isArray(step.findings) ? step.findings.length : null);
          const ai = act ? actionInfo(act) : null;
          return (
            <div key={i} className="flex items-center gap-1.5">
              {i > 0 && <ArrowRight className="w-3 h-3 text-slate-300 dark:text-slate-600" />}
              <span className="inline-flex items-center gap-1 rounded border border-slate-200 dark:border-slate-700 bg-slate-50 dark:bg-slate-900 px-1.5 py-0.5 text-[10px]">
                <span className="font-semibold text-slate-600 dark:text-slate-300">{String(tier).replace(/_/g, " ")}</span>
                {dir && <span className="text-slate-400">{dir}</span>}
                {ai && <Badge variant={ai.badge} className="px-1 py-0 text-[9px]">{ai.label}</Badge>}
                {findings != null && findings > 0 && (
                  <span className="text-amber-600 dark:text-amber-400">{findings} finding{findings === 1 ? "" : "s"}</span>
                )}
              </span>
            </div>
          );
        })}
      </div>
    );
  };

  const renderObservability = () => {
    const visibleEvents = events.slice(0, eventsVisible);
    return (
      <div className="space-y-4">
        <div className="flex items-center justify-between gap-3 flex-wrap">
          <p className="text-sm text-slate-500 dark:text-slate-400 max-w-xl">
            Real-time MCP policy enforcement events. All tool calls are audited with pre-flight and post-flight checks.
          </p>
          <div className="flex items-center gap-2">
            <SegmentedControl
              value={String(obsHours)}
              onChange={(v) => {
                const h = Number(v);
                setObsHours(h);
                loadEvents(h);
                loadEventSummary(h);
              }}
              aria-label="Time window"
              options={[
                { value: "1", label: "1h" },
                { value: "24", label: "24h" },
                { value: "168", label: "7d" },
                { value: "720", label: "30d" },
                { value: "0", label: "All" },
              ]}
            />
            <Tooltip content="Refresh">
              <Button variant="ghost" size="icon" aria-label="Refresh events" onClick={() => { loadEvents(); loadEventSummary(); }}>
                <RefreshCw className="w-4 h-4" />
              </Button>
            </Tooltip>
          </div>
        </div>

        {/* Summary cards */}
        {eventSummary && (
          <div className="grid grid-cols-2 sm:grid-cols-4 lg:grid-cols-6 gap-3">
            <Card className="text-center shadow-none">
              <CardContent className="p-4">
                <p className="text-2xl font-bold text-slate-900 dark:text-white">{eventSummary.total || 0}</p>
                <p className="text-xs text-slate-500 mt-1">Total Events</p>
              </CardContent>
            </Card>
            {["allow", "block", "redact", "monitor", "error"].map((d) => {
              const di = decisionInfo(d);
              const count = decisionCounts[d] || 0;
              return (
                <Card key={d} className="text-center shadow-none">
                  <CardContent className="p-4">
                    <p className="text-2xl font-bold text-slate-900 dark:text-white tabular-nums">{count}</p>
                    <Badge variant={di.badge} className="mt-1">{di.label}</Badge>
                  </CardContent>
                </Card>
              );
            })}
          </div>
        )}

        {/* Top tools and users */}
        {eventSummary && (
          <div className="grid gap-4 sm:grid-cols-2">
            {eventSummary.top_tools && eventSummary.top_tools.length > 0 && (
              <Card>
                <CardContent className="p-4">
                  <h4 className="text-sm font-medium text-slate-900 dark:text-white mb-3 flex items-center gap-2">
                    <Wrench className="w-4 h-4 text-teal-500" /> Top Tools
                  </h4>
                  <div className="space-y-2">
                    {eventSummary.top_tools.map((t) => (
                      <div key={t.tool_name} className="flex items-center justify-between text-xs">
                        <span className="font-mono text-slate-700 dark:text-slate-300 truncate">{t.tool_name}</span>
                        <span className="text-slate-500 font-medium shrink-0 ml-2">{t.count}</span>
                      </div>
                    ))}
                  </div>
                </CardContent>
              </Card>
            )}
            {eventSummary.top_users && eventSummary.top_users.length > 0 && (
              <Card>
                <CardContent className="p-4">
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
                </CardContent>
              </Card>
            )}
          </div>
        )}

        {/* Event list */}
        <div>
          <h4 className="text-sm font-medium text-slate-900 dark:text-white mb-2">
            Recent Events
            {events.length > 0 && (
              <span className="ml-2 text-xs font-normal text-slate-400">
                showing {visibleEvents.length} of {events.length}
              </span>
            )}
          </h4>
          {events.length === 0 ? (
            <EmptyState
              icon={BarChart3}
              title="No MCP events recorded yet"
              description="Execute a tool call to see enforcement events appear here."
            />
          ) : (
            <>
              <div className="space-y-2">
                {visibleEvents.map((evt) => {
                  const di = decisionInfo(evt.decision);
                  return (
                    <Card key={evt.id || evt.request_id}>
                      <CardContent className="p-3">
                        <div className="flex items-center justify-between gap-2">
                          <div className="flex items-center gap-2 flex-wrap">
                            <Badge variant={di.badge}>{di.label}</Badge>
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
                        {(evt.metadata?.scan_pipeline || Array.isArray(evt.metadata?.scan_trace)) && (
                          <p className="text-[10px] text-slate-500 mt-1 font-mono">
                            pipeline: {evt.metadata?.scan_pipeline || "two_tier"}
                            {Array.isArray(evt.metadata?.scan_trace) && evt.metadata.scan_trace.length > 0
                              ? ` · ${evt.metadata.scan_trace.length} trace step(s)`
                              : ""}
                            {evt.metadata?.scan_action ? ` · action: ${evt.metadata.scan_action}` : ""}
                            {evt.metadata?.monitored ? " · monitored" : ""}
                          </p>
                        )}
                        {renderScanTrace(evt.metadata?.scan_trace)}
                        {Array.isArray(evt.compliance_tags) && evt.compliance_tags.length > 0 && (
                          <div className="flex items-center gap-1 mt-1.5 flex-wrap">
                            <span className="text-[10px] text-slate-400">Compliance:</span>
                            {evt.compliance_tags.map((tag) => (
                              <Badge key={tag} variant="warning">{tag}</Badge>
                            ))}
                            {Array.isArray(evt.scan_findings) && evt.scan_findings.length > 0 && (
                              <span className="text-[10px] text-slate-400">
                                ({evt.scan_findings.length} scan finding{evt.scan_findings.length === 1 ? "" : "s"})
                              </span>
                            )}
                          </div>
                        )}
                        {evt.username && (
                          <p className="text-[10px] text-slate-400 mt-0.5">User: {evt.username}</p>
                        )}
                      </CardContent>
                    </Card>
                  );
                })}
              </div>
              {/* BUG FIX (c): Load more instead of rendering all 500 events. */}
              {eventsVisible < events.length && (
                <div className="flex justify-center mt-3">
                  <Button
                    variant="outline"
                    size="sm"
                    onClick={() => setEventsVisible((v) => v + EVENTS_PAGE_SIZE)}
                  >
                    Load more ({events.length - eventsVisible} remaining)
                  </Button>
                </div>
              )}
            </>
          )}
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
        <Tooltip content="Refresh">
          <Button variant="ghost" size="icon" aria-label="Refresh health" onClick={loadHealth}>
            <RefreshCw className="w-4 h-4" />
          </Button>
        </Tooltip>
      </div>

      {!health && !loading && (
        <EmptyState icon={Activity} title="Unable to load service health" />
      )}

      {health && (
        <div className="space-y-4">
          {/* Policy Engine — the single source of MCP enforcement */}
          <Card>
            <CardContent className="p-5" aria-label="Policy engine status">
              <div className="flex items-center gap-3 mb-3">
                <div className="w-10 h-10 rounded-lg bg-orange-50 dark:bg-orange-900/20 flex items-center justify-center">
                  <Ban className="w-5 h-5 text-orange-600" />
                </div>
                <div className="flex-1">
                  <h4 className="font-medium text-slate-900 dark:text-white">ZeroShield Policy Engine</h4>
                  <p className="text-xs text-slate-500">Inline Regex, Keyword &amp; Pattern Enforcement</p>
                </div>
                <StatusDot status={health.mcp_firewall?.status} />
              </div>
              <Badge
                variant={
                  health.mcp_firewall?.status === "healthy"
                    ? "success"
                    : health.mcp_firewall?.status === "not_configured"
                    ? "secondary"
                    : "danger"
                }
              >
                {health.mcp_firewall?.status || "unknown"}
              </Badge>
              {health.mcp_firewall?.detail && (
                <p className="text-xs text-slate-500 mt-2">{health.mcp_firewall.detail}</p>
              )}
            </CardContent>
          </Card>

          {/* Built-in detectors — the unified guard set enforced in-band on every call */}
          {Array.isArray(health.builtin_detectors) && health.builtin_detectors.length > 0 && (
            <Card>
              <CardContent className="p-5" aria-label="Built-in detectors">
                <div className="flex items-center gap-3 mb-3">
                  <div className="w-10 h-10 rounded-lg bg-violet-50 dark:bg-violet-900/20 flex items-center justify-center">
                    <Shield className="w-5 h-5 text-violet-600" />
                  </div>
                  <div>
                    <h4 className="font-medium text-slate-900 dark:text-white">Built-in Detectors</h4>
                    <p className="text-xs text-slate-500">Enforced in-band by the policy engine on every tool call</p>
                  </div>
                </div>
                <div className="flex flex-wrap gap-1.5">
                  {health.builtin_detectors.map((d) => (
                    <Badge key={d} variant="info">
                      <CheckCircle className="w-3 h-3 mr-1" />
                      {String(d).replace(/_/g, " ")}
                    </Badge>
                  ))}
                </div>
              </CardContent>
            </Card>
          )}
        </div>
      )}
    </div>
  );

  /* ────────── main render ────────── */

  return (
    <div className="space-y-4">
      <PanelHeader
        icon={Server}
        title="MCP Guardrails"
        description="Register, govern, and observe Model Context Protocol servers through the ZeroShield control plane."
        actions={
          <>
            <Tooltip content="Refresh">
              <Button variant="outline" size="icon" aria-label="Refresh" onClick={loadServers}>
                <RefreshCw className="w-4 h-4" />
              </Button>
            </Tooltip>
            <Button onClick={() => setAddOpen(true)}>
              <Plus className="w-4 h-4" /> Register Server
            </Button>
          </>
        }
      />

      {/* Status strip */}
      <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-3">
        <StatCard
          icon={Server}
          tone="teal"
          value={`${connectedCount}/${servers.length}`}
          label="Servers connected"
        />
        <StatCard icon={Wrench} tone="blue" value={toolsDiscovered} label="Tools discovered" />
        <StatCard
          icon={Layers}
          tone={tier2On === true ? "emerald" : tier2On === false ? "slate" : "slate"}
          value={tier2On === true ? "On" : tier2On === false ? "Off" : "—"}
          label="Tier-2 MCP scan"
        />
        <StatCard icon={CheckCircle} tone="emerald" value={decisionCounts.allow || 0} label="Allowed" />
        <StatCard icon={Ban} tone="red" value={decisionCounts.block || 0} label="Blocked" />
        <StatCard
          icon={Tag}
          tone="amber"
          value={(decisionCounts.redact || 0) + (decisionCounts.monitor || 0)}
          label="Redact / Monitor"
          sub={`${decisionCounts.redact || 0} redact · ${decisionCounts.monitor || 0} monitor`}
        />
      </div>

      {/* Error banner */}
      {error && (
        <div className="flex items-center gap-2 p-3 rounded-lg bg-red-50 dark:bg-red-900/20 text-red-700 dark:text-red-300 text-sm">
          <AlertTriangle className="w-4 h-4 shrink-0" />
          {error}
          <Button
            variant="ghost"
            size="icon"
            className="ml-auto h-7 w-7 text-red-500 hover:text-red-700"
            aria-label="Dismiss error"
            onClick={() => setError(null)}
          >
            <X className="w-4 h-4" />
          </Button>
        </div>
      )}

      <Tabs value={tab} onValueChange={setTab}>
        <TabsList className="sticky top-0 z-10 bg-white/80 dark:bg-slate-900/80 backdrop-blur supports-[backdrop-filter]:bg-white/60 supports-[backdrop-filter]:dark:bg-slate-900/60">
          {TABS.map(({ id, label, icon: Icon }) => {
            const count =
              id === "servers" ? servers.length :
              id === "tools" ? tools.length :
              id === "observability" ? events.length :
              null;
            return (
              <TabsTrigger key={id} value={id} icon={Icon} count={count != null && count > 0 ? count : null}>
                {label}
              </TabsTrigger>
            );
          })}
        </TabsList>

        {/* Loading */}
        {loading && (
          <div className="flex items-center justify-center py-6">
            <Spinner size="sm" className="text-teal-500" />
          </div>
        )}

        {!loading && (
          <div className="pt-4">
            <TabsContent value="servers">{renderServers()}</TabsContent>
            <TabsContent value="tools">{renderTools()}</TabsContent>
            <TabsContent value="execute">{renderExecute()}</TabsContent>
            <TabsContent value="scan-matrix">{renderScanMatrix()}</TabsContent>
            <TabsContent value="protection">{renderProtection()}</TabsContent>
            <TabsContent value="observability">{renderObservability()}</TabsContent>
            <TabsContent value="health">{renderHealth()}</TabsContent>
          </div>
        )}
      </Tabs>
    </div>
  );
}

/* ════════════════════════════════════════════════════ */
export function MCPConnectorPanel() {
  return (
    <ToastProvider>
      <MCPConnectorPanelInner />
    </ToastProvider>
  );
}
