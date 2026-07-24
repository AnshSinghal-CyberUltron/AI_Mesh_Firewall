/**
 * MCPConnectorPanel — ZeroShield MCP integration management.
 *
 * Backend proxy: /api/mcp-connector/*
 * Stdio transport: production delegates to per-org Docker sandbox (mcp-broker);
 * dev may use in-process gateway spawn when MCP_STDIO_IN_PROCESS=true.
 */

import { useState, useEffect, useCallback, useRef, useMemo } from "react";
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
  Sparkles,
  X,
} from "lucide-react";
import { useAuth } from "../context/AuthContext";
import { toAbsoluteGatewayUrl, resolveMcpGatewayBaseUrl } from "../utils/environmentUrls";
import { ServerTier2Manager } from "./ServerTier2Manager";
import { ServerTier1Manager } from "./ServerTier1Manager";
import { ZEROSHIELD_TIER2_LABEL } from "../constants/zeroshieldBrand";

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
  // Stdio MCP servers run in a per-org Docker sandbox (mcp-broker → sandbox-agent)
  // when MCP_STDIO_IN_PROCESS=false (production default). Dev may use in-process
  // spawn inside the gateway container (MCP_STDIO_IN_PROCESS=true). Command must be
  // an allow-listed interpreter (npx/node/python/python3); package runtime deps must
  // exist in the sandbox image. Failures surface in last_sync_error, not silent disconnect.
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
  { name: "Semgrep MCP", transport: "stdio", description: "Code security scanning via Semgrep MCP", command: "npx", args: ["-y", "mcp-server-semgrep"], host_tools: ["pip:semgrep"] },
  { name: "Host tools demo (cowsay)", transport: "stdio", description: "Minimal MCP server that shells out to cowsay (declares pip:cowsay host CLI)", command: "python3", args: ["-m", "agent.host_tools_demo_mcp"], host_tools: ["pip:cowsay"] },
  { name: "Memory MCP", transport: "stdio", description: "Knowledge graph memory via official MCP memory server", command: "npx", args: ["-y", "@modelcontextprotocol/server-memory"] },
  { name: "Filesystem MCP", transport: "stdio", description: "Official MCP filesystem server (sandbox /data/mcp-auth scope)", command: "npx", args: ["-y", "@modelcontextprotocol/server-filesystem", "/data/mcp-auth"] },
  { name: "Fetch MCP", transport: "stdio", description: "HTTP fetch MCP server (mcp-server-fetch, a uvx/Python package)", command: "uvx", args: ["mcp-server-fetch"] },
  { name: "Everything MCP", transport: "stdio", description: "Official MCP reference/test server (tools echo, add, etc.)", command: "npx", args: ["-y", "@modelcontextprotocol/server-everything"] },
  { name: "Vibe Check MCP", transport: "stdio", description: "Vibe Check MCP for plan/goal alignment", command: "npx", args: ["-y", "@pv-bhat/vibe-check-mcp", "start", "--stdio"] },
];

const MCP_HOST_TOOLS_KEY = "MCP_HOST_TOOLS";

const formatHostToolsSpec = (entries) => {
  if (!Array.isArray(entries) || entries.length === 0) return "";
  return entries
    .map((entry) => {
      if (typeof entry === "string") return entry.trim();
      const manager = (entry?.manager || "pip").trim();
      const pkg = (entry?.package || entry?.name || "").trim();
      return manager === "pip" ? pkg : `${manager}:${pkg}`;
    })
    .filter(Boolean)
    .join(" ");
};

const makeEmptyAddForm = () => ({
  name: "",
  url: "",
  transport: "streamable-http",
  command: "",
  args: [],
  env_vars: {},
  host_tools: [],
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

  // Include stdio-specific fields.
  // Args/env are parsed from the RAW TEXT buffers (args_text/env_text) at submit —
  // never per keystroke — so commas/spaces/newlines survive typing (bug #1 fix).
  // Fall back to the array/object when a preset set them without a text buffer.
  if (form.transport === "stdio") {
    payload.command = form.command || "";
    payload.args =
      typeof form.args_text === "string"
        ? form.args_text.split(",").map((s) => s.trim()).filter(Boolean)
        : Array.isArray(form.args)
        ? form.args
        : [];
    payload.env_vars =
      typeof form.env_text === "string"
        ? (() => {
            const vars = {};
            form.env_text.split("\n").forEach((line) => {
              const idx = line.indexOf("=");
              if (idx > 0) vars[line.slice(0, idx).trim()] = line.slice(idx + 1).trim();
            });
            return vars;
          })()
        : form.env_vars && typeof form.env_vars === "object"
        ? form.env_vars
        : {};
    const hostToolsText =
      typeof form.host_tools_text === "string" ? form.host_tools_text.trim() : "";
    if (hostToolsText) {
      payload.env_vars[MCP_HOST_TOOLS_KEY] = hostToolsText;
    } else if (!payload.env_vars[MCP_HOST_TOOLS_KEY]) {
      const catalogSpec = formatHostToolsSpec(form.host_tools);
      if (catalogSpec) payload.env_vars[MCP_HOST_TOOLS_KEY] = catalogSpec;
    }
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

/**
 * True when an arg refers to the `mcp-remote` package in any npm form — bare
 * (`mcp-remote`), version-pinned (`mcp-remote@0.1.17`, `mcp-remote@latest`),
 * scoped (`@scope/mcp-remote`), or a path. The old checks matched only the bare /
 * path forms, so a version-pinned "Linear Remote" (`mcp-remote@0.1.17`) was not
 * recognized as OAuth-needing → no Authorize button + URL-extraction returned null.
 */
const isMcpRemoteArg = (a) => {
  if (typeof a !== "string") return false;
  const bare = a.replace(/@[^@/]+$/, ""); // strip a trailing @version, keep an @scope
  return bare === "mcp-remote" || bare.endsWith("/mcp-remote");
};

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
    // CLEANUP-11: h-full so every stat card fills its grid cell → equal heights;
    // items-start top-aligns the value/label across cards so the big numbers line up
    // regardless of label length.
    <Card className="shadow-none h-full">
      <CardContent className="flex items-start gap-3 p-4">
        {Icon ? (
          <div className={`flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-slate-100 dark:bg-slate-700/60 ${toneStyles[tone]}`}>
            <Icon className="h-5 w-5" aria-hidden="true" />
          </div>
        ) : null}
        <div className="min-w-0">
          <p className="text-lg font-semibold leading-tight text-slate-900 dark:text-white tabular-nums">{value}</p>
          {/* CLEANUP-11: wrap (leading-tight) instead of truncate — a label like
              "Servers connected" must never clip mid-word ("Servers connec…"). */}
          <p className="text-[11px] leading-tight text-slate-500 dark:text-slate-400">{label}</p>
          {sub ? <p className="text-[10px] leading-tight text-slate-400 dark:text-slate-500 mt-0.5">{sub}</p> : null}
        </div>
      </CardContent>
    </Card>
  );
}

const EVENTS_PAGE_SIZE = 50;

// Org Tier-2 master (null=Inherit, true=Enabled, false=Disabled). Relocated to the
// MCP Servers tab header when the standalone "Scan Controls" tab was retired — the
// gateway gates ALL per-server Tier-2 scans on this org flag, so it must stay
// reachable alongside each server's "Manage Tier-2" button.
const TIER2_MASTER_OPTIONS = [
  { value: "inherit", label: "Inherit" },
  { value: "enabled", label: "Enabled" },
  { value: "disabled", label: "Disabled" },
];
const TIER2_MASTER_PAYLOAD = { inherit: null, enabled: true, disabled: false };

const TABS = [
  { id: "servers", label: "MCP Servers", icon: Server },
  { id: "tools", label: "Tool Discovery", icon: Wrench },
  { id: "execute", label: "Tool Execution", icon: Play },
  { id: "observability", label: "Observability", icon: BarChart3 },
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
  // Register flow (bug #2): connect-first inline; keep the modal open during the
  // connection + tool-discovery attempt; only close after tools are discovered.
  const [addConnecting, setAddConnecting] = useState(false);
  const [addConnectError, setAddConnectError] = useState(null);
  const [addConnectErrorCode, setAddConnectErrorCode] = useState(null); // stable client-facing code (CP17)
  const [addServerId, setAddServerId] = useState(null); // created row id (retry re-syncs it)

  /* ── OAuth upstream authorization ── */
  const [oauthBusy, setOauthBusy] = useState(null); // server id currently authorizing

  /* ── tools ── */
  const [tools, setTools] = useState([]);
  const [executeServerSlug, setExecuteServerSlug] = useState("");
  const [executeToolKey, setExecuteToolKey] = useState("");
  const [executeArguments, setExecuteArguments] = useState("{}");
  const [executeResult, setExecuteResult] = useState(null);
  const [executeBusy, setExecuteBusy] = useState(false);

  /* ── org Tier-2 master (Inherit/Enabled/Disabled) — relocated from the retired
     Scan Controls tab; gates ALL per-server Tier-2 scans org-wide ── */
  const [mcpTier2, setMcpTier2] = useState(null); // null=inherit, true=enabled, false=disabled
  const [tier2Saving, setTier2Saving] = useState(false);

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
  // Scan-controls row count — when 0, gateway skips Tier-1/Tier-2; the
  // read-only server scan-state badge must not imply active scanning (UI honesty).
  const [scanControlsByServer, setScanControlsByServer] = useState(null); // Set<server_id> with rows; null = loading
  const [scanControlsOrgWide, setScanControlsOrgWide] = useState(false);

  // Per-server, server-centric control modals opened from each server card's
  // "Manage Tier-1" / "Manage Tier-2" buttons. Each holds the target server obj.
  const [tier1Server, setTier1Server] = useState(null);
  const [tier2Server, setTier2Server] = useState(null);

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

  const loadScanControlsConfigured = useCallback(async () => {
    try {
      const res = await fetchWithAuth("/api/mcp-connector/scan-controls/");
      if (!res.ok) return;
      const data = await res.json();
      const rows = Array.isArray(data) ? data : data.results ?? [];
      // Per-server honesty (red-team wf_66e76405): a per-server scan-control row governs ONLY its own
      // server; an org-wide row (no server) applies to every server. The old single org-wide boolean
      // made one server's Tier-2 row light up "Scanning on" on EVERY other server card.
      const perServer = new Set();
      let orgWide = false;
      for (const r of rows) {
        const sid = r.server_id ?? r.server ?? null;
        if (sid == null || sid === "") orgWide = true;
        else perServer.add(String(sid));
      }
      setScanControlsByServer(perServer);
      setScanControlsOrgWide(orgWide);
    } catch {
      // leave prior value; do not flip honesty banner on transient failure
    }
  }, [fetchWithAuth]);

  // Org Tier-2 master (firewall/config.mcp_tier2_enabled). The gateway gates ALL
  // per-server Tier-2 scans on this org flag, so the MCP Servers tab surfaces it.
  const loadTier2Master = useCallback(async () => {
    try {
      const res = await fetchWithAuth("/api/firewall/config/");
      if (!res.ok) return;
      const data = await res.json();
      setMcpTier2(data.mcp_tier2_enabled ?? null);
    } catch {
      /* optional — leave prior value on transient failure */
    }
  }, [fetchWithAuth]);

  // CP44 (impeccable perf): memoize derived lists so they don't recompute on
  // every render (audit dim 2 — 0 useMemo → filtered/sorted lists recomputed).
  const executableTools = useMemo(
    () => (Array.isArray(tools) ? tools : [])
      .filter((tool) => tool?.enabled !== false)
      .filter((tool) => !executeServerSlug || tool.server_slug === executeServerSlug)
      .sort((left, right) => {
        const leftLabel = `${left.server_name || left.server_slug || ""}/${left.name || ""}`;
        const rightLabel = `${right.server_name || right.server_slug || ""}/${right.name || ""}`;
        return leftLabel.localeCompare(rightLabel);
      }),
    [tools, executeServerSlug],
  );

  const selectedExecuteTool = useMemo(
    () => executableTools.find((tool) => makeExecuteToolKey(tool) === executeToolKey) || null,
    [executableTools, executeToolKey],
  );

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

  // Fetch that retries once on a transient 503 (db_unavailable) — the control
  // plane returns 503 + Retry-After when the Postgres pool is briefly exhausted
  // under load, instead of a hard 500, so the tab recovers real data on retry. (CP26)
  const fetchWithRetry = useCallback(async (url) => {
    let res = await fetchWithAuth(url);
    if (res.status === 503) {
      const wait = Math.min(5, Number(res.headers.get("Retry-After")) || 2) * 1000;
      await new Promise((r) => setTimeout(r, wait));
      res = await fetchWithAuth(url);
    }
    return res;
  }, [fetchWithAuth]);

  const loadEvents = useCallback(async (hours = obsHours) => {
    try {
      const qs = hours > 0 ? `?limit=500&hours=${hours}` : `?limit=500`;
      const res = await fetchWithRetry(`/api/mcp-connector/events/${qs}`);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      setEvents(Array.isArray(data) ? data : data.results ?? []);
      setEventsVisible(EVENTS_PAGE_SIZE); // BUG FIX (c): reset paging on reload
    } catch { setEvents([]); }
  }, [fetchWithRetry, obsHours]);

  const loadEventSummary = useCallback(async (hours = obsHours) => {
    try {
      const qs = hours > 0 ? `?hours=${hours}` : ``;
      const res = await fetchWithRetry(`/api/mcp-connector/events/summary/${qs}`);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      setEventSummary(await res.json());
    } catch { setEventSummary(null); }
  }, [fetchWithRetry, obsHours]);

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
    if (tab === "servers") { loadServers(); loadOrgGatewayKey(); loadHealth(); loadScanControlsConfigured(); loadTier2Master(); }
    if (tab === "tools") loadTools();
    if (tab === "execute") loadTools();
    if (tab === "observability") { loadEvents(); }
  }, [tab, loadServers, loadTools, loadHealth, loadEvents, loadOrgGatewayKey, loadScanControlsConfigured, loadTier2Master]);

  /* The header-strip decision StatCards (Allowed / Blocked / Redact·Monitor)
     are ALWAYS visible regardless of the active tab, so the decision summary
     must load on mount (and whenever the obs time-lens changes) — not only on
     the observability tab. Without this it reads 0/0/0 on the default tab. */
  useEffect(() => { loadEventSummary(); }, [loadEventSummary]);

  /* ────────── actions ────────── */

  const addServer = async () => {
    setError(null);
    setAddConnectError(null);
    setAddConnectErrorCode(null);
    try {
      // Step 1 — create the registration (skip if a prior attempt already created
      // it and only the connection failed; a retry then just re-connects).
      let serverId = addServerId;
      if (!serverId) {
        setAddSaving(true);
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
        serverId = data.id;
        setAddServerId(serverId);
        if (data.default_gateway_key) {
          setOrgGatewayKey({
            has_gateway_key: true,
            prefix: data.default_gateway_key_prefix,
            key: data.default_gateway_key,
            name: `MCP Default Key`,
          });
        }
        setAddSaving(false);
      } else {
        // Retry after a failed connect — apply any form edits to the existing row
        // (so "fix + retry" actually takes effect) before re-connecting (CP08).
        setAddSaving(true);
        const payload = buildServerPayload(addForm);
        const patchRes = await fetchWithAuth(`/api/mcp-connector/servers/${serverId}/`, {
          method: "PATCH",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload),
        });
        if (!patchRes.ok) {
          const body = await patchRes.json().catch(() => ({}));
          throw new Error(body.detail || body.error || `HTTP ${patchRes.status}`);
        }
        setAddSaving(false);
      }

      // Step 2 (bug #2 / CP07) — attempt the MCP connection + tool discovery INLINE.
      // Keep the modal OPEN during the attempt; only close after tools are found.
      setAddConnecting(true);
      const syncRes = await fetchWithAuth(`/api/mcp-connector/servers/${serverId}/tools/`, {
        method: "POST",
      });
      const syncData = await syncRes.json().catch(() => ({}));
      const toolCount = Array.isArray(syncData.tools)
        ? syncData.tools.length
        : typeof syncData.synced === "number"
        ? syncData.synced
        : 0;
      const syncErr =
        syncData.error || syncData.last_sync_error || (!syncRes.ok ? `HTTP ${syncRes.status}` : null);

      if (syncErr || toolCount === 0) {
        // Failure (CP08) — clear inline error, modal stays open, user fixes + retries.
        setAddConnectError(
          syncErr
            ? `Connection failed: ${syncErr}`
            : "Connected, but the server exposed no tools. Check the command / URL / auth and retry."
        );
        // CP17: surface the STABLE client-facing error code (the message already
        // carries the branded summary + correlation "(Ref: …)").
        setAddConnectErrorCode(syncData.error_code || null);
        const httpOAuthPending =
          addForm.auth_type === "oauth" &&
          (addForm.transport === "streamable-http" || addForm.transport === "sse");
        if (httpOAuthPending) {
          // B2: show the pending-authorization card immediately (never a hidden 0-tools row).
          await loadServers();
        }
      } else {
        // Success (CP09) — tools discovered → close + add to the list (never 0 tools).
        setAddOpen(false);
        setAddForm(makeEmptyAddForm());
        setAddServerId(null);
        await loadServers();
        toast(`Registered "${addForm.name}" — ${toolCount} tools discovered`, { tone: "success" });
      }
    } catch (e) {
      setAddConnectError(`Add server failed: ${e.message}`);
      setAddConnectErrorCode(null);
      toast(`Add server failed: ${e.message}`, { tone: "error" });
    } finally {
      setAddSaving(false);
      setAddConnecting(false);
    }
  };

  // Cancel/close the Add modal. If a row was created but never connected (tools
  // not discovered), delete it so it never appears in the list with 0 tools (bug #2).
  const cancelAdd = async () => {
    if (addServerId) {
      await fetchWithAuth(`/api/mcp-connector/servers/${addServerId}/`, { method: "DELETE" }).catch(() => {});
    }
    setAddServerId(null);
    setAddConnectError(null);
    setAddConnectErrorCode(null);
    setAddForm(makeEmptyAddForm());
    setAddOpen(false);
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
        host_tools: preset.host_tools || [],
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
        const hostSpec = formatHostToolsSpec(preset.host_tools);
        if (hostSpec) payload.env_vars = { [MCP_HOST_TOOLS_KEY]: hostSpec };
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
      // Bug #3: an OAuth-required preset must not look "done" on register — the
      // operator has to authorize before it becomes usable. Direct them to the
      // Authorize action instead of a plain "Registered" success.
      const presetNeedsOAuth =
        (preset.transport === "stdio" &&
          (preset.args || []).some(isMcpRemoteArg)) ||
        preset.suggestedAuthType === "oauth";
      if (presetNeedsOAuth) {
        toast(`Registered "${preset.name}" — click Authorize to activate`, { tone: "success" });
        setError(
          `"${preset.name}" requires authorization. Click "Authorize" on its card to complete OAuth before syncing tools.`
        );
      } else {
        toast(`Registered "${preset.name}"`, { tone: "success" });
      }
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
      const body = await res.json().catch(() => ({}));
      if (!res.ok) {
        throw new Error(body.detail || body.error || `HTTP ${res.status}`);
      }
      if (body.error || body.connection_status === "failed") {
        const errMsg = body.error || "upstream discovery error";
        setError(`Sync failed for server: ${errMsg}`);
        toast(`Sync failed: ${errMsg}`, { tone: "error" });
        await loadServerTools(serverId);
        await loadServers();
        return { ok: false, error: errMsg };
      }
      setError(null);
      toast(`Synced ${body.synced ?? ""} tool${body.synced === 1 ? "" : "s"}`.replace(/\s+/g, " ").trim(), { tone: "success" });
      await loadServerTools(serverId);
      await loadServers();
      return { ok: true };
    } catch (e) {
      setError(`Sync tools failed: ${e.message}`);
      toast(`Sync tools failed: ${e.message}`, { tone: "error" });
      return { ok: false, error: e.message };
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

  /* ── org Tier-2 master save ── */
  // PUT only the changed field (documented partial update) — re-sending the whole
  // config re-validates unrelated siblings and can 400 for reasons unrelated to
  // Tier-2. value: null=Inherit, true=Enabled, false=Disabled.
  const saveTier2Master = async (value) => {
    if (tier2Saving) return;
    setTier2Saving(true);
    const prev = mcpTier2;
    setMcpTier2(value); // optimistic
    try {
      const res = await fetchWithAuth("/api/firewall/config/", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ mcp_tier2_enabled: value }),
      });
      if (!res.ok) {
        let detail = `HTTP ${res.status}`;
        try {
          const err = await res.json();
          detail = err?.mcp_tier2_enabled?.[0] || err?.detail || detail;
        } catch {
          /* keep status */
        }
        throw new Error(detail);
      }
      const updated = await res.json().catch(() => ({ mcp_tier2_enabled: value }));
      setMcpTier2(updated.mcp_tier2_enabled ?? value);
      loadHealth(); // refresh the "Tier-2 MCP scan" status card
      toast(
        value === null
          ? "Tier-2 set to Inherit (org default)"
          : value
            ? "Tier-2 enabled for this org"
            : "Tier-2 disabled for this org",
        { tone: "success" }
      );
    } catch (e) {
      setMcpTier2(prev); // rollback
      toast(e.message || "Failed to save Tier-2 setting", { tone: "error" });
    } finally {
      setTier2Saving(false);
    }
  };

  // NOTE (MCP collapse — Phase 4): per-tool `scan_action` and server
  // `default_scan_action` enforcement were retired from this surface. A
  // server's/tool's enforcement action is now a single Policy concern — managed
  // per-server via each row's "Manage Tier-1" button (org-wide view lives on the
  // separate Policy Management page). Registration + enable/disable is the gate here.

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

  /** Check whether a server uses mcp-remote (needs gateway-side OAuth authorization). */
  const serverNeedsOAuth = (srv) =>
    srv.transport === "stdio" &&
    Array.isArray(srv.args) &&
    srv.args.some(isMcpRemoteArg);

  /**
   * Whether a server is eligible for the control-plane OAuth 2.1 flow
   * (startControlOAuth). OAuth 2.1 authorization-code is HTTP-only — it needs an
   * HTTP MCP endpoint URL for RFC 9728/8414 discovery. Guarding on transport+URL
   * here (in addition to the backend serializer reject) is defense-in-depth:
   * even a legacy/invalid stdio row with auth_type="oauth" must NEVER surface the
   * control authorize button (which 400s "Server has no URL") — that dup/broken
   * button is MCP OAuth bugs #1/#2. Mutually exclusive with serverNeedsOAuth,
   * which only fires for stdio.
   */
  const serverUsesHttpOAuth = (srv) =>
    srv.auth_type === "oauth" &&
    !!srv.url &&
    (srv.transport === "streamable-http" || srv.transport === "sse");

  /**
   * A server that requires OAuth but has not completed it yet — it must be
   * AUTHORIZED before it is usable (fixes bug #3: freshly-registered OAuth
   * servers showing up as a generic "0 tools / Unknown" card that misleads the
   * operator into syncing before auth). Two tracked cases:
   *  - HTTP OAuth 2.1 (auth_type="oauth"): control persists oauth_authorized.
   *  - stdio mcp-remote (Linear): the token lives gateway-side, so the control
   *    row has no oauth_authorized flag — treat "never synced, no tools, not
   *    connected" as awaiting authorization.
   */
  const serverAwaitingAuth = (srv) => {
    if (srv.auth_type === "oauth") return !srv.oauth_authorized;
    if (serverNeedsOAuth(srv)) {
      // "Awaiting authorization" = genuinely not yet resolved (never synced /
      // unknown / syncing), NOT a server that was attempted and definitively
      // FAILED for a concrete non-auth reason (storage limit, crash, DNS, …).
      // A failed server must show its real "Failed" status + branded error so the
      // operator acts on the actual cause — a "Pending authorization / Authorize"
      // affordance is a dead end that can't fix a storage failure. (A true auth
      // failure surfaces via needs_reauth, which the caller already excludes and
      // which renders its own "needs re-auth" badge.)
      return (srv.tools_count ?? 0) === 0
        && srv.connection_status !== "connected"
        && srv.connection_status !== "failed";
    }
    return false;
  };

  /**
   * Whether the manual "Sync tools" action must be blocked pending auth. Only
   * gated for the TRACKABLE http-oauth case (startControlOAuth auto-syncs on
   * success, so a pre-auth manual sync is always a wasted, failing call). stdio
   * mcp-remote is intentionally NOT gated — its tokens are gateway-side and a
   * post-authorize sync is exactly how its tools get discovered, so gating it
   * would deadlock (no control-row signal ever flips).
   */
  const syncBlockedForAuth = (srv) => srv.auth_type === "oauth" && !srv.oauth_authorized;

  const isAuthRelatedSyncError = (srv) => {
    const err = (srv.last_sync_error || "").toLowerCase();
    if (!err) return false;
    return (
      srv.needs_reauth ||
      err.includes("re-authenticat") ||
      err.includes("re-authorize") ||
      err.includes("unauthorized") ||
      err.includes("401") ||
      err.includes("oauth") ||
      err.includes("invalid_token") ||
      err.includes("invalid token") ||
      err.includes("rejected your oauth")
    );
  };

  const isOAuthConfigMismatch = (srv) => {
    const err = srv.last_sync_error || "";
    return err.includes("re-authorizing will not help") || err.includes("OAuth configuration");
  };

  const oauthExpiresWithin24h = (srv) => {
    if (!srv.oauth_token_expires_at) return false;
    const exp = new Date(srv.oauth_token_expires_at).getTime();
    const now = Date.now();
    return exp > now && exp - now < 24 * 60 * 60 * 1000;
  };

  /** Extract the MCP server URL from mcp-remote args. */
  const extractMcpRemoteUrl = (args) => {
    if (!Array.isArray(args)) return null;
    for (let i = 0; i < args.length; i++) {
      if (isMcpRemoteArg(args[i])) {
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
    // If popups are blocked entirely (Cursor/Electron embedded browser), popup is
    // null → we fall back to a SAME-TAB redirect below.
    const popup = window.open("about:blank", "mcp-oauth", "width=600,height=700");

    setOauthBusy(srv.id);
    setError(null);
    try {
      const gwBase = resolveMcpGatewayBaseUrl();
      if (!gwBase) {
        if (popup) popup.close();
        throw new Error("Gateway URL is not configured.");
      }

      // oauth/start now requires a gateway key whose org matches the URL (it used
      // to be unauthenticated — a cross-org breach). Send the org gateway key.
      const res = await fetch(`${gwBase}/gateway/${orgSlug}/mcp/${srv.server_slug}/oauth/start`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          ...(orgGatewayKey?.key ? { Authorization: `Bearer ${orgGatewayKey.key}` } : {}),
        },
        body: JSON.stringify({ server_url: serverUrl }),
      });
      const data = await res.clone().json().catch(async () => ({
        error: (await res.text().catch(() => "")) || null,
      }));
      if (!res.ok) {
        if (popup) popup.close();
        throw new Error(data.error || `HTTP ${res.status}`);
      }

      if (!popup) {
        // Popup blocked → same-tab fallback (never blocked). Remember the server
        // so we auto-sync its tools when the callback returns us to the app.
        try {
          localStorage.setItem("mcp_oauth_pending", JSON.stringify({ id: srv.id }));
        } catch { /* storage disabled — return handler just won't auto-sync */ }
        window.location.assign(data.authorize_url);
        return;
      }
      // Navigate the pre-opened popup to OAuth URL
      popup.location.href = data.authorize_url;
    } catch (e) {
      if (popup && !popup.closed) popup.close();
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
    // If the browser blocks popups entirely (e.g. the Cursor/Electron embedded
    // browser), popup is null → we fall back to a SAME-TAB redirect below, which
    // is never blocked.
    const popup = window.open("about:blank", "mcp-oauth-2-1", "width=620,height=760");
    setOauthBusy(srv.id);
    setError(null);
    try {
      const res = await fetchWithAuth(`/api/mcp-connector/servers/${srv.id}/oauth/authorize/`, { method: "POST" });
      const data = await res.json().catch(() => ({}));
      if (!res.ok || !data.authorize_url) {
        if (popup) popup.close();
        throw new Error(data.error || data.detail || `HTTP ${res.status}`);
      }
      if (!popup) {
        // Popup blocked → same-tab fallback. Remember which server so we
        // auto-sync its tools when the callback returns us to the app.
        try {
          localStorage.setItem("mcp_oauth_pending", JSON.stringify({ id: srv.id }));
        } catch { /* storage disabled — return handler just won't auto-sync */ }
        window.location.assign(data.authorize_url);
        return;
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
              const syncOutcome = await syncServerTools(srv.id);
              if (syncOutcome?.ok) {
                toast("OAuth authorized", { tone: "success" });
              } else if (!syncOutcome?.error) {
                toast("OAuth authorized", { tone: "success" });
              }
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

  // Same-tab OAuth return: when the popup is blocked we redirect the whole tab
  // to the provider; the control callback then bounces the browser back here.
  // On mount, if we stashed a pending server, refresh + auto-sync it (mirrors
  // the popup path's poll auto-sync) so tools populate without a manual click.
  useEffect(() => {
    let pending = null;
    try {
      pending = JSON.parse(localStorage.getItem("mcp_oauth_pending") || "null");
    } catch { pending = null; }
    if (!pending?.id) return;
    try { localStorage.removeItem("mcp_oauth_pending"); } catch { /* ignore */ }
    (async () => {
      await loadServers();
      try {
        const r = await fetchWithAuth(`/api/mcp-connector/servers/${pending.id}/`);
        if (r.ok) {
          const s = await r.json();
          if (s.oauth_authorized) {
            await syncServerTools(pending.id);
            toast("OAuth authorized — tools synced", { tone: "success" });
          }
        }
      } catch { /* transient — user can sync manually */ }
    })();
    // eslint-disable-next-line react-hooks/exhaustive-deps
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

  const connectedCount = useMemo(
    () => servers.filter((s) => connectionInfo(s.connection_status).badge === "success").length,
    [servers],
  );
  const toolsDiscovered = useMemo(
    () => servers.reduce((acc, s) => acc + (s.tools_count || 0), 0),
    [servers],
  );
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
                      className="h-9 w-9"
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
                      className="h-9 w-9"
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
    // B2: a server that requires OAuth but has not completed it must read as a
    // DISTINCT "Pending authorization" state — never a normal-looking card with a
    // grey "Unknown" status + "0 tools" (which misleads the operator into thinking
    // it is ready/empty). needs_reauth has its own badge and takes precedence.
    const awaitingAuth = !srv.needs_reauth && serverAwaitingAuth(srv);
    const showAuthBanner = srv.needs_reauth || (srv.connection_status === "failed" && isAuthRelatedSyncError(srv));
    const configMismatch = showAuthBanner && isOAuthConfigMismatch(srv);
    const tokenExpiringSoon = serverUsesHttpOAuth(srv) && oauthExpiresWithin24h(srv) && !showAuthBanner;
    return (
      <Card key={srv.id}>
        <CardContent className="p-4">
          {showAuthBanner && (
            <div
              className={`mb-3 rounded-lg border px-3 py-2.5 flex flex-wrap items-start justify-between gap-3 ${
                configMismatch
                  ? "border-sky-300 bg-sky-50 dark:border-sky-800 dark:bg-sky-950/40"
                  : "border-amber-300 bg-amber-50 dark:border-amber-800 dark:bg-amber-950/40"
              }`}
              data-testid={`mcp-auth-alert-${srv.server_slug}`}
            >
              <div className="flex items-start gap-2 min-w-0 flex-1">
                <AlertTriangle className={`w-4 h-4 shrink-0 mt-0.5 ${configMismatch ? "text-sky-600 dark:text-sky-400" : "text-amber-600 dark:text-amber-400"}`} />
                <div className="min-w-0">
                  <p className={`text-xs font-semibold ${configMismatch ? "text-sky-900 dark:text-sky-100" : "text-amber-900 dark:text-amber-100"}`}>
                    {configMismatch ? "OAuth configuration mismatch" : "OAuth token expired or rejected"}
                  </p>
                  <p className={`text-[11px] mt-0.5 break-all ${configMismatch ? "text-sky-800 dark:text-sky-200" : "text-amber-800 dark:text-amber-200"}`}>
                    {configMismatch
                      ? (srv.last_sync_error || "The upstream rejected a valid token — check scopes, audience, and account access.")
                      : (srv.last_sync_error || "Re-authorize this server to restore tool discovery and calls.")}
                  </p>
                </div>
              </div>
              {(serverUsesHttpOAuth(srv) || serverNeedsOAuth(srv)) && (
                <Button
                  variant="outline"
                  size="sm"
                  className="shrink-0"
                  onClick={() => (serverUsesHttpOAuth(srv) ? startControlOAuth(srv) : startOAuth(srv))}
                  disabled={oauthBusy === srv.id}
                >
                  {oauthBusy === srv.id ? <Loader2 className="w-3 h-3 animate-spin" /> : <Shield className="w-3 h-3" />}
                  Re-authorize
                </Button>
              )}
            </div>
          )}
          {tokenExpiringSoon && (
            <div className="mb-3 rounded-lg border border-amber-200 bg-amber-50/80 dark:border-amber-800 dark:bg-amber-950/30 px-3 py-2 text-[11px] text-amber-800 dark:text-amber-200 flex items-center gap-2">
              <Clock className="w-3.5 h-3.5 shrink-0" />
              OAuth token expires within 24 hours — re-authorize soon to avoid sync interruptions.
            </div>
          )}
          <div className="flex flex-col gap-3 lg:flex-row lg:items-start lg:justify-between">
            <div className="min-w-0 flex-1">
              <h4 className="font-medium text-slate-900 dark:text-white flex items-center gap-2 flex-wrap">
                <Server className="w-4 h-4 text-teal-500 shrink-0" />
                {srv.name}
                {srv.is_active && <CheckCircle className="w-3.5 h-3.5 text-emerald-500 shrink-0" />}
                {awaitingAuth ? (
                  <Badge variant="warning">
                    <span className="mr-1.5 inline-block w-1.5 h-1.5 rounded-full bg-amber-500" />
                    Pending authorization
                  </Badge>
                ) : (
                  <Badge variant={conn.badge}>
                    <span className={`mr-1.5 inline-block w-1.5 h-1.5 rounded-full ${conn.dot}`} />
                    {conn.label}
                  </Badge>
                )}
                {srv.risk_level && srv.risk_level !== "low" && (
                  <Badge variant={risk}>{srv.risk_level} risk</Badge>
                )}
                {srv.needs_reauth && <Badge variant="warning">needs re-auth</Badge>}
              </h4>
              <p className="text-xs text-slate-500 dark:text-slate-400 mt-1 break-all">{srv.url}</p>
              {srv.description && <p className="text-xs text-slate-400 mt-0.5">{srv.description}</p>}
              <div className="flex items-center gap-3 mt-2 flex-wrap">
                {awaitingAuth ? (
                  <span className="text-[10px] text-amber-600 dark:text-amber-400 flex items-center gap-1">
                    <Wrench className="w-3 h-3" /> Authorize to load tools
                  </span>
                ) : srv.tools_count != null && (
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
                  <div className="flex min-w-0 items-center gap-1.5">
                    <code className="min-w-0 flex-1 text-[10px] bg-slate-100 dark:bg-slate-700 px-2 py-1 rounded font-mono text-slate-600 dark:text-slate-300 break-all">
                      {absUrl}
                    </code>
                    <Tooltip content="Copy gateway URL only">
                      <Button
                        variant="ghost"
                        size="icon"
                        className="h-9 w-9 shrink-0"
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
            <div className="flex flex-wrap items-center gap-2 min-w-0 lg:max-w-[min(100%,28rem)] lg:justify-end shrink-0">
              <Badge variant="secondary">{srv.transport}</Badge>
              {/* Every transport runs in the per-org sandbox (bug #3 / CP15) — the
                  gateway never dials the upstream directly, so show it for ALL. */}
              <Badge variant="secondary" className="inline-flex items-center gap-1" title="Executes in your org's isolated per-org sandbox; the gateway never dials the upstream directly.">
                <Shield className="w-3 h-3" /> Sandboxed
              </Badge>
              {/* MCP collapse (Phase 4): a server's enforcement action is now a
                  single Policy concern. Show a compact read-only scan state plus
                  a CTA that routes to the MCP Security Policies tab. */}
              {scanControlsByServer !== null && (
                (scanControlsByServer.has(String(srv.id)) || scanControlsOrgWide) ? (
                  <Badge
                    variant="success"
                    className="text-[10px]"
                    title="A Tier-2 scan is configured for this server (or an org-wide scan control). Tier-1 enforcement is defined in this server's Policies (Manage Tier-1)."
                  >
                    Tier-2 on
                  </Badge>
                ) : (
                  <Badge
                    variant="secondary"
                    className="text-[10px] text-amber-700 dark:text-amber-300"
                    title="No Tier-2 scan configured for this server. Configure Tier-1 (Policies) and Tier-2 per server below."
                  >
                    Tier-2 off
                  </Badge>
                )
              )}
              {/* Server-centric control lane: focused per-server management for
                  each tier. Tier-1 = MCP Security Policies bound to this server;
                  Tier-2 = the ZeroShield model scan on/off + tool scoping. */}
              <Button
                variant="outline"
                size="sm"
                onClick={() => setTier1Server(srv)}
                className="text-[11px] text-indigo-600 dark:text-indigo-400"
                title="Manage the MCP Security Policies that apply to this server (Tier-1)"
              >
                <Shield className="w-3 h-3" />
                Manage Tier-1
              </Button>
              <Button
                variant="outline"
                size="sm"
                onClick={() => setTier2Server(srv)}
                className="text-[11px] text-violet-600 dark:text-violet-400"
                title="Manage the ZeroShield model scan for this server (Tier-2)"
              >
                <Sparkles className="w-3 h-3" />
                Manage Tier-2
              </Button>
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
              {serverUsesHttpOAuth(srv) && (
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
              <Tooltip
                content={
                  syncBlockedForAuth(srv)
                    ? "Authorize this server before syncing tools"
                    : "Sync tools from server"
                }
              >
                <Button
                  variant="ghost"
                  size="icon"
                  className="h-9 w-9"
                  aria-label="Sync tools from server"
                  onClick={() => syncServerTools(srv.id)}
                  disabled={syncingServer === srv.id || syncBlockedForAuth(srv)}
                >
                  {syncingServer === srv.id ? <Loader2 className="w-4 h-4 animate-spin" /> : <RefreshCw className="w-4 h-4" />}
                </Button>
              </Tooltip>
              <Tooltip content="View tool controls">
                <Button
                  variant="ghost"
                  size="icon"
                  className="h-9 w-9"
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
                  className="h-9 w-9 text-red-500 hover:text-red-700"
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
                      {/* Per-tool enforcement action retired here — governed by
                          MCP Security Policies. Enable/disable is the gate. */}
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

      {/* Org Tier-2 master — relocated from the retired "Scan Controls" tab. The
          gateway gates ALL per-server Tier-2 scans on this org flag, so it stays
          reachable here next to each server's "Manage Tier-2" button. */}
      <Card>
        <CardContent className="flex flex-col gap-3 p-4 sm:flex-row sm:items-center sm:justify-between">
          <div className="flex items-start gap-3">
            <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl bg-violet-100 dark:bg-violet-900/40 text-violet-600 dark:text-violet-300">
              <Sparkles className="h-5 w-5" aria-hidden="true" />
            </div>
            <div>
              <div className="flex flex-wrap items-center gap-2">
                <h3 className="text-sm font-semibold text-slate-900 dark:text-slate-100">
                  Tier-2 ({ZEROSHIELD_TIER2_LABEL}) — org master
                </h3>
                <Badge variant={mcpTier2 === null ? "secondary" : mcpTier2 ? "success" : "danger"}>
                  {mcpTier2 === null ? "Inherit (org default)" : mcpTier2 ? "Enabled" : "Disabled"}
                </Badge>
              </div>
              <p className="mt-1 text-xs text-slate-500 dark:text-slate-400">
                Must be enabled for the org before any per-server Tier-2 scan runs.
              </p>
            </div>
          </div>
          <div className="flex items-center gap-2 self-start sm:self-center">
            {tier2Saving && <Spinner className="h-4 w-4 text-violet-500" />}
            <SegmentedControl
              aria-label="Org MCP Tier-2 mode"
              value={mcpTier2 === null ? "inherit" : mcpTier2 ? "enabled" : "disabled"}
              onChange={(v) => saveTier2Master(TIER2_MASTER_PAYLOAD[v])}
              options={TIER2_MASTER_OPTIONS}
            />
          </div>
        </CardContent>
      </Card>

      {servers.length === 0 && !loading ? (
        <EmptyState
          icon={Server}
          title="No MCP servers registered yet"
          description='Click "Register Server" to connect an MCP server for centralized discovery and governance.'
          action={<Button onClick={() => { setAddServerId(null); setAddConnectError(null); setAddConnectErrorCode(null); setAddOpen(true); }}><Plus className="w-4 h-4" /> Register Server</Button>}
        />
      ) : (
        <div className="grid grid-cols-1 gap-3">
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
                onChange={(e) => {
                  const nextTransport = e.target.value;
                  const httpOAuth =
                    nextTransport === "streamable-http" || nextTransport === "sse";
                  setAddForm((prev) => ({
                    ...prev,
                    transport: nextTransport,
                    // OAuth 2.1 is HTTP-only (bugs #1/#2): drop it when switching
                    // to a non-HTTP transport so we never build a stdio+oauth row.
                    auth_type:
                      prev.auth_type === "oauth" && !httpOAuth ? "none" : prev.auth_type,
                  }));
                }}
                aria-label="Transport"
              >
                {TRANSPORT_OPTIONS.map((t) => (
                  <option key={t.value} value={t.value} disabled={!t.supported}>
                    {t.label}
                  </option>
                ))}
              </Select>
              <p className="text-xs text-slate-500 mt-1">
                Every transport (HTTP, SSE, WebSocket, Stdio) executes inside your
                organization&apos;s isolated per-org sandbox (mcp-broker &rarr; sandbox-agent);
                the gateway never connects to the upstream MCP server directly.
                (Dev may run stdio in-process when MCP_STDIO_IN_PROCESS=true.)
              </p>
              <span className="mt-1.5 inline-flex items-center gap-1 text-[11px] font-medium text-teal-700 dark:text-teal-300 bg-teal-50 dark:bg-teal-900/30 border border-teal-200 dark:border-teal-800 rounded px-1.5 py-0.5">
                <Shield className="w-3 h-3" /> Sandboxed &mdash; all transports run in your org&apos;s isolated sandbox
              </span>
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
                <code> python</code>, <code>python3</code>, <code>uvx</code>) — not a path. Production
                runs inside your org&apos;s Docker sandbox. If the MCP server shells out to an extra
                CLI binary, declare it under <strong>Host CLI tools</strong> (installed on demand
                before startup) or set <code>MCP_HOST_TOOLS</code> in environment variables.
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
                  // RAW-TEXT source of truth (bug #1 fix): the input holds the exact
                  // typed string so commas/spaces are never dropped by an array
                  // round-trip; parsed to args[] only at submit (buildServerPayload).
                  // Fall back to the array join when a preset populated args[].
                  value={
                    addForm.args_text !== undefined
                      ? addForm.args_text
                      : Array.isArray(addForm.args)
                      ? addForm.args.join(", ")
                      : ""
                  }
                  onChange={(e) => setAddForm({ ...addForm, args_text: e.target.value })}
                  placeholder="-y, @playwright/mcp@latest"
                />
              </div>
              <div>
                <label className="block text-sm font-medium mb-1">Host CLI tools (one per line or space-separated)</label>
                <textarea
                  className="w-full border rounded-lg px-3 py-2 text-sm font-mono dark:bg-slate-800 dark:border-slate-600"
                  rows={2}
                  value={
                    addForm.host_tools_text !== undefined
                      ? addForm.host_tools_text
                      : formatHostToolsSpec(addForm.host_tools)
                  }
                  onChange={(e) => setAddForm({ ...addForm, host_tools_text: e.target.value })}
                  placeholder={"pip:semgrep\nnpm:some-cli"}
                />
                <p className="text-xs text-teal-600/80 dark:text-teal-400/80 mt-1">
                  Installed into the sandbox before the server starts. Prefix with <code>pip:</code>,{" "}
                  <code>uv:</code>, or <code>npm:</code> (bare name defaults to pip).
                </p>
              </div>
              <div>
                <label className="block text-sm font-medium mb-1">Environment Variables (KEY=VALUE, one per line)</label>
                <textarea
                  className="w-full border rounded-lg px-3 py-2 text-sm font-mono dark:bg-slate-800 dark:border-slate-600"
                  rows={2}
                  // RAW-TEXT source of truth (bug #1 fix): hold the exact typed text so
                  // separators/newlines survive; parsed to env_vars{} only at submit.
                  value={
                    addForm.env_text !== undefined
                      ? addForm.env_text
                      : addForm.env_vars && typeof addForm.env_vars === "object"
                      ? Object.entries(addForm.env_vars).map(([k, v]) => `${k}=${v}`).join("\n")
                      : ""
                  }
                  onChange={(e) => setAddForm({ ...addForm, env_text: e.target.value })}
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
                {AUTH_OPTIONS.filter(
                  (opt) =>
                    opt.value !== "oauth" ||
                    addForm.transport === "streamable-http" ||
                    addForm.transport === "sse"
                ).map((opt) => (
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
          <div className="flex-1 min-w-0">
            {addConnectError && (
              <div role="alert">
                <p className="text-sm text-rose-600 dark:text-rose-400">{addConnectError}</p>
                {addConnectErrorCode && (
                  <p className="mt-0.5 text-xs font-mono text-rose-500/80 dark:text-rose-400/70">
                    Error code: {addConnectErrorCode}
                  </p>
                )}
              </div>
            )}
          </div>
          <Button variant="secondary" onClick={cancelAdd} disabled={addSaving || addConnecting}>Cancel</Button>
          <Button
            onClick={addServer}
            disabled={addSaving || addConnecting || !addForm.name || (addForm.transport === "stdio" ? !addForm.command : !addForm.url)}
          >
            {(addSaving || addConnecting) && <Loader2 className="w-4 h-4 animate-spin" />}
            {addSaving
              ? "Creating…"
              : addConnecting
              ? "Connecting & discovering tools…"
              : addServerId
              ? "Retry connection"
              : "Register"}
          </Button>
        </DialogFooter>
      </Dialog>

      {/* ── Manage Tier-2 (per-server ZeroShield model scan) ── */}
      <Dialog
        open={!!tier2Server}
        onClose={() => setTier2Server(null)}
        labelledBy="mcp-tier2-manage-title"
      >
        {tier2Server && (
          <>
            <DialogHeader
              id="mcp-tier2-manage-title"
              title={`Tier-2 scan · ${tier2Server.name}`}
              description="Turn the ZeroShield model scan on for this server; when on, choose all tools or specific tools."
              onClose={() => setTier2Server(null)}
            />
            <DialogBody>
              <ServerTier2Manager
                server={tier2Server}
                fetchWithAuth={fetchWithAuth}
                onChanged={loadScanControlsConfigured}
              />
            </DialogBody>
          </>
        )}
      </Dialog>

      {/* ── Manage Tier-1 (per-server MCP Security Policies) ── */}
      <Dialog
        open={!!tier1Server}
        onClose={() => setTier1Server(null)}
        labelledBy="mcp-tier1-manage-title"
        className="max-w-5xl"
      >
        {tier1Server && (
          <>
            <DialogHeader
              id="mcp-tier1-manage-title"
              title={`Tier-1 policies · ${tier1Server.name}`}
              description="Enable/disable the policies and rules that apply to this server. Each rule targets this server's tools, Apply-To, scope, and action."
              onClose={() => setTier1Server(null)}
            />
            <DialogBody>
              <ServerTier1Manager
                key={tier1Server.id}
                server={tier1Server}
                fetchWithAuth={fetchWithAuth}
              />
            </DialogBody>
          </>
        )}
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
            // Aggregated across all servers → a bare tool.name collides when two servers
            // expose the same tool (e.g. multiple `everything`/echo presets). Key on
            // server_slug::name (+idx tiebreaker), matching the Execute tab (line ~2004).
            <Card key={`${makeExecuteToolKey(tool)}-${idx}`}>
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
              aria-label="Tool arguments (JSON)"
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
                {ai && <Badge variant={ai.badge} className="px-1 py-0 text-[10px]">{ai.label}</Badge>}
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
                    {eventSummary.top_tools.map((t, idx) => (
                      // CLEANUP-13: a tool with an empty name rendered as a blank row
                      // (just a count) — label it "(unnamed)" and give it a stable key
                      // so the list reads intentionally, never a floating number.
                      <div key={t.tool_name || `__unnamed-${idx}`} className="flex items-center justify-between text-xs">
                        <span className={`font-mono truncate ${t.tool_name ? "text-slate-700 dark:text-slate-300" : "italic text-slate-400 dark:text-slate-500"}`}>
                          {t.tool_name || "(unnamed)"}
                        </span>
                        <span className="text-slate-500 font-medium shrink-0 ml-2 tabular-nums">{t.count}</span>
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
            className="ml-auto h-9 w-9 text-red-500 hover:text-red-700"
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
            <TabsContent value="observability">{renderObservability()}</TabsContent>
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
