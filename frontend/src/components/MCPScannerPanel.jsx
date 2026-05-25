import { useState, useEffect, useCallback, useRef } from "react";
import {
  Search, Server, Shield, AlertTriangle, CheckCircle, Loader2,
  ChevronDown, ChevronRight, RefreshCw, Eye, Activity,
  Cpu, HardDrive, Network, Zap, X, Play, Plus, Trash2,
} from "lucide-react";
import { InfoTooltip } from "./InfoTooltip";
import {
  getGatewayStorageKey,
  resolveGatewayBaseUrl,
} from "../utils/environmentUrls";

const GATEWAY_URL_KEY = getGatewayStorageKey();
const POLL_INTERVAL_MS = 2000;

const RISK_BADGE = {
  minimal:  { bg: "bg-emerald-100 dark:bg-emerald-800/30", text: "text-emerald-700", border: "border-emerald-200 dark:border-emerald-800" },
  low:      { bg: "bg-blue-100 dark:bg-blue-800/30",    text: "text-blue-700",    border: "border-blue-200 dark:border-blue-800" },
  medium:   { bg: "bg-amber-100 dark:bg-amber-800/30",   text: "text-amber-700",   border: "border-amber-200 dark:border-amber-800" },
  high:     { bg: "bg-orange-100 dark:bg-orange-800/30",  text: "text-orange-700",  border: "border-orange-200 dark:border-orange-800" },
  critical: { bg: "bg-red-100 dark:bg-red-800/30",     text: "text-red-700",     border: "border-red-200 dark:border-red-800" },
};

function RiskBadge({ category, score }) {
  const style = RISK_BADGE[category] || RISK_BADGE.minimal;
  return (
    <span className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-medium ${style.bg} ${style.text}`}>
      {score !== undefined && <span>{score.toFixed(1)}</span>}
      <span className="capitalize">{category}</span>
    </span>
  );
}

function StatusBadge({ status }) {
  const map = {
    running:   { bg: "bg-blue-100 dark:bg-blue-800/30",    text: "text-blue-700",    icon: Loader2, animate: true },
    completed: { bg: "bg-emerald-100 dark:bg-emerald-800/30", text: "text-emerald-700", icon: CheckCircle },
    failed:    { bg: "bg-red-100 dark:bg-red-800/30",     text: "text-red-700",     icon: AlertTriangle },
    idle:      { bg: "bg-slate-100 dark:bg-slate-700",   text: "text-slate-600 dark:text-slate-400",   icon: Activity },
  };
  const s = map[status] || map.idle;
  const Icon = s.icon;
  return (
    <span className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-medium ${s.bg} ${s.text}`}>
      <Icon className={`w-3 h-3 ${s.animate ? "animate-spin" : ""}`} />
      <span className="capitalize">{status}</span>
    </span>
  );
}

function KpiCard({ icon: Icon, label, value, sub, color = "blue" }) {
  const colorMap = {
    blue:   "bg-blue-50 dark:bg-blue-900/20 text-blue-600 border-blue-100 dark:border-blue-800",
    red:    "bg-red-50 dark:bg-red-900/20 text-red-600 border-red-100 dark:border-red-800",
    amber:  "bg-amber-50 dark:bg-amber-900/20 text-amber-600 border-amber-100 dark:border-amber-800",
    emerald:"bg-emerald-50 dark:bg-emerald-900/20 text-emerald-600 border-emerald-100 dark:border-emerald-800",
  };
  return (
    <div className={`rounded-lg border p-4 ${colorMap[color] || colorMap.blue}`}>
      <div className="flex items-center gap-2 mb-1">
        <Icon className="w-4 h-4" />
        <span className="text-xs font-medium uppercase tracking-wide opacity-80">{label}</span>
      </div>
      <div className="text-2xl font-bold">{value}</div>
      {sub && <div className="text-xs opacity-70 mt-0.5">{sub}</div>}
    </div>
  );
}

export function MCPScannerPanel() {
  const [gatewayUrl, setGatewayUrl] = useState(() => {
    return resolveGatewayBaseUrl();
  });
  const [scanStatus, setScanStatus] = useState("idle");
  const [scanResult, setScanResult] = useState(null);
  const [servers, setServers] = useState([]);
  const [expandedServer, setExpandedServer] = useState(null);
  const [selectedTool, setSelectedTool] = useState(null);
  const [scanHistory, setScanHistory] = useState([]);
  const [graphData, setGraphData] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const [showHistory, setShowHistory] = useState(false);
  const [registerModalOpen, setRegisterModalOpen] = useState(false);
  const [registerForm, setRegisterForm] = useState({
    name: "", transport: "http", host: "", port: "", url: "", command: "", args: "",
    auto_introspect: true,
  });
  const [registering, setRegistering] = useState(false);
  const pollRef = useRef(null);
  const defaultHost = (() => {
    if (typeof window === "undefined") return "";
    if (window.location.hostname) return window.location.hostname;
    try {
      const parsed = new URL(gatewayUrl);
      return parsed.hostname || "";
    } catch {
      return "";
    }
  })();

  const baseUrl = gatewayUrl.replace(/\/+$/, "");

  const gw = useCallback(async (path, options = {}) => {
    const res = await fetch(`${baseUrl}${path}`, {
      headers: { "Content-Type": "application/json", ...options.headers },
      ...options,
    });
    const data = await res.json();
    if (!res.ok && !data.ok) {
      throw new Error(data.error || `HTTP ${res.status}`);
    }
    return data;
  }, [baseUrl]);

  const loadStatus = useCallback(async () => {
    try {
      const data = await gw("/api/mcp/status");
      if (data.ok && data.data?.last_scan) {
        setScanResult(data.data.last_scan);
        if (data.data.last_scan.servers) {
          setServers(data.data.last_scan.servers);
        }
      }
    } catch {
      // Scanner may not be enabled
    }
  }, [gw]);

  const loadServers = useCallback(async () => {
    try {
      const data = await gw("/api/mcp/servers");
      if (data.ok) {
        setServers(data.data || []);
      }
    } catch {
      // ignore
    }
  }, [gw]);

  const loadHistory = useCallback(async () => {
    try {
      const data = await gw("/api/mcp/history");
      if (data.ok) {
        setScanHistory(data.data || []);
      }
    } catch {
      // ignore
    }
  }, [gw]);

  const loadGraph = useCallback(async () => {
    try {
      const data = await gw("/api/mcp/graph");
      if (data.ok) {
        setGraphData(data.data || null);
      }
    } catch {
      // ignore
    }
  }, [gw]);

  useEffect(() => {
    localStorage.setItem(GATEWAY_URL_KEY, gatewayUrl);
    loadStatus();
    loadHistory();
    loadGraph();
  }, [gatewayUrl, loadStatus, loadHistory, loadGraph]);

  const stopPolling = useCallback(() => {
    if (pollRef.current) {
      clearInterval(pollRef.current);
      pollRef.current = null;
    }
  }, []);

  const pollScan = useCallback((runId) => {
    stopPolling();
    pollRef.current = setInterval(async () => {
      try {
        const data = await gw(`/api/mcp/scan/${runId}`);
        if (data.ok) {
          setScanResult(data.data);
          if (data.data.status === "completed" || data.data.status === "failed") {
            setScanStatus(data.data.status);
            stopPolling();
            loadServers();
            loadHistory();
            loadGraph();
          }
        }
      } catch {
        stopPolling();
        setScanStatus("failed");
      }
    }, POLL_INTERVAL_MS);
  }, [gw, stopPolling, loadServers, loadHistory, loadGraph]);

  useEffect(() => {
    return () => stopPolling();
  }, [stopPolling]);

  const startScan = async (mode = "full") => {
    setError(null);
    setScanStatus("running");
    setLoading(true);
    try {
      const data = await gw("/api/mcp/scan", {
        method: "POST",
        body: JSON.stringify({ mode }),
      });
      if (data.ok && data.data?.run_id) {
        setScanResult(data.data);
        pollScan(data.data.run_id);
      } else {
        setScanStatus("failed");
        setError(data.error || "Scan failed to start");
      }
    } catch (err) {
      setScanStatus("failed");
      setError(`Failed to start scan: ${err.message}`);
    } finally {
      setLoading(false);
    }
  };

  const rescanServer = async (serverId) => {
    try {
      const data = await gw(`/api/mcp/servers/${serverId}/rescan`, { method: "POST" });
      if (data.ok) {
        loadServers();
        loadGraph();
      }
    } catch (err) {
      setError(`Rescan failed: ${err.message}`);
    }
  };

  const registerServer = async () => {
    setRegistering(true);
    setError(null);
    try {
      const payload = {
        name: registerForm.name,
        transport: registerForm.transport,
        auto_introspect: registerForm.auto_introspect,
      };
      if (registerForm.transport === "stdio") {
        payload.command = registerForm.command;
        payload.args = registerForm.args
          ? registerForm.args.split(/\s+/).filter(Boolean)
          : [];
      } else {
        payload.host = registerForm.host || defaultHost;
        payload.port = registerForm.port ? parseInt(registerForm.port, 10) : 0;
        payload.url = registerForm.url || undefined;
      }
      const data = await gw("/api/mcp/servers/register", {
        method: "POST",
        body: JSON.stringify(payload),
      });
      if (data.ok) {
        setRegisterModalOpen(false);
        setRegisterForm({
          name: "", transport: "http", host: "", port: "", url: "", command: "", args: "",
          auto_introspect: true,
        });
        loadServers();
        loadGraph();
      } else {
        setError(data.error || "Registration failed");
      }
    } catch (err) {
      setError(`Registration failed: ${err.message}`);
    } finally {
      setRegistering(false);
    }
  };

  const unregisterServer = async (serverId) => {
    try {
      const data = await gw(`/api/mcp/servers/${serverId}`, { method: "DELETE" });
      if (data.ok) {
        loadServers();
        loadGraph();
      }
    } catch (err) {
      setError(`Unregister failed: ${err.message}`);
    }
  };

  const loadToolDetail = async (serverId, toolName) => {
    try {
      const data = await gw(`/api/mcp/servers/${serverId}/tools/${encodeURIComponent(toolName)}`);
      if (data.ok) {
        setSelectedTool(data.data);
      }
    } catch (err) {
      setError(`Failed to load tool detail: ${err.message}`);
    }
  };

  const totalTools = scanResult?.total_tools ?? servers.reduce((a, s) => a + (s.tool_count || 0), 0);
  const highRisk = scanResult?.high_risk_count ?? 0;
  const criticalRisk = scanResult?.critical_count ?? 0;
  const avgRisk = scanResult?.average_risk ?? 0;

  return (
    <div className="mt-6 space-y-6">
      {/* Header */}
      <div className="bg-white dark:bg-slate-800 rounded-xl border border-slate-200 dark:border-slate-700 p-6">
        <div className="flex items-center justify-between mb-4">
          <div className="flex items-center gap-3">
            <div className="p-2 bg-indigo-100 dark:bg-indigo-800/30 dark:bg-indigo-900/30 rounded-lg">
              <Search className="w-5 h-5 text-indigo-600 dark:text-indigo-400" />
            </div>
            <div>
              <h3 className="text-lg font-semibold text-slate-900 dark:text-slate-100 dark:text-white flex items-center">
                MCP Server Scanner
                <span className="ml-2 inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium bg-indigo-100 dark:bg-indigo-800/30 text-indigo-700">
                  Gateway Scanner
                </span>
                <InfoTooltip title="How to Use">{"Scans MCP (Model Context Protocol) servers for security vulnerabilities. Enter an MCP server URL or stdio command to initiate a scan.\n\nExample: npx -y @anthropic/mcp-server-filesystem /tmp"}</InfoTooltip>
              </h3>
              <p className="text-sm text-slate-500 dark:text-slate-400">
                Discover, introspect, and assess risk of local MCP servers and their tools
              </p>
            </div>
          </div>
          <StatusBadge status={scanStatus} />
        </div>

        <div className="flex items-center gap-3">
          <div className="flex-1">
            <label className="text-xs font-medium text-slate-500 dark:text-slate-400 mb-1 block">Gateway URL</label>
            <input
              type="text"
              value={gatewayUrl}
              onChange={(e) => setGatewayUrl(e.target.value)}
              className="w-full px-3 py-1.5 text-sm rounded-lg border border-slate-300 dark:border-slate-600 bg-white dark:bg-slate-800 dark:bg-slate-700 text-slate-900 dark:text-slate-100 dark:text-white"
              placeholder="https://gateway.company.com"
            />
          </div>
          <div className="flex gap-2 pt-5">
            <button
              onClick={() => startScan("full")}
              disabled={scanStatus === "running"}
              className="inline-flex items-center gap-1.5 px-4 py-1.5 text-sm font-medium rounded-lg bg-indigo-600 text-white hover:bg-indigo-700 disabled:opacity-50 disabled:cursor-not-allowed"
            >
              {scanStatus === "running" ? (
                <Loader2 className="w-4 h-4 animate-spin" />
              ) : (
                <Play className="w-4 h-4" />
              )}
              Scan Now
            </button>
            <button
              onClick={() => startScan("fast")}
              disabled={scanStatus === "running"}
              className="inline-flex items-center gap-1.5 px-3 py-1.5 text-sm font-medium rounded-lg border border-slate-300 dark:border-slate-600 text-slate-700 dark:text-slate-300 hover:bg-slate-50 dark:hover:bg-slate-700 dark:hover:bg-slate-700 disabled:opacity-50"
              title="Fast scan: skips injection simulation and static analysis"
            >
              <Zap className="w-4 h-4" />
              Fast
            </button>
            <button
              onClick={() => setRegisterModalOpen(true)}
              className="inline-flex items-center gap-1.5 px-3 py-1.5 text-sm font-medium rounded-lg bg-emerald-600 text-white hover:bg-emerald-700"
            >
              <Plus className="w-4 h-4" />
              Register
            </button>
          </div>
        </div>

        {error && (
          <div className="mt-3 flex items-center gap-2 p-3 rounded-lg bg-red-50 dark:bg-red-900/20 border border-red-200 dark:border-red-800 text-red-700 dark:text-red-400 text-sm">
            <AlertTriangle className="w-4 h-4 shrink-0" />
            <span>{error}</span>
            <button onClick={() => setError(null)} className="ml-auto">
              <X className="w-4 h-4" />
            </button>
          </div>
        )}
      </div>

      {/* KPI Tiles */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
        <KpiCard icon={Server} label="Servers Discovered" value={servers.length || scanResult?.servers_discovered || 0} color="blue" />
        <KpiCard icon={HardDrive} label="Total Tools" value={totalTools} color="emerald" />
        <KpiCard icon={AlertTriangle} label="High + Critical" value={highRisk + criticalRisk} sub={`${highRisk} high, ${criticalRisk} critical`} color="red" />
        <KpiCard icon={Activity} label="Avg Risk Score" value={avgRisk.toFixed(1)} sub={scanResult?.duration_seconds ? `Scan: ${scanResult.duration_seconds}s` : ""} color="amber" />
      </div>

      {/* Servers Table */}
      {servers.length > 0 && (
        <div className="bg-white dark:bg-slate-800 rounded-xl border border-slate-200 dark:border-slate-700 overflow-hidden">
          <div className="px-6 py-4 border-b border-slate-200 dark:border-slate-700 flex items-center justify-between">
            <h4 className="text-sm font-semibold text-slate-900 dark:text-slate-100 dark:text-white">Discovered Servers</h4>
            <span className="text-xs text-slate-500 dark:text-slate-400">{servers.length} server(s)</span>
          </div>
          <div className="divide-y divide-slate-100 dark:divide-slate-700">
            {servers.map((server) => (
              <div key={server.server_id}>
                <div
                  className="px-6 py-3 flex items-center gap-4 cursor-pointer hover:bg-slate-50 dark:hover:bg-slate-700 dark:hover:bg-slate-700/50 transition-colors"
                  onClick={() => setExpandedServer(expandedServer === server.server_id ? null : server.server_id)}
                >
                  <span className="text-slate-400">
                    {expandedServer === server.server_id ? (
                      <ChevronDown className="w-4 h-4" />
                    ) : (
                      <ChevronRight className="w-4 h-4" />
                    )}
                  </span>
                  <Server className="w-4 h-4 text-slate-500 dark:text-slate-400" />
                  <div className="flex-1 min-w-0">
                    <div className="text-sm font-medium text-slate-900 dark:text-slate-100 dark:text-white truncate flex items-center gap-2">
                      {server.name || `${server.host}:${server.port}`}
                      <span className={`inline-flex items-center px-1.5 py-0.5 rounded text-[10px] font-mono uppercase ${
                        server.transport === "stdio"
                          ? "bg-violet-100 dark:bg-violet-800/30 text-violet-700"
                          : server.transport === "sse"
                            ? "bg-cyan-100 dark:bg-cyan-800/30 text-cyan-700"
                            : "bg-blue-100 dark:bg-blue-800/30 text-blue-700"
                      }`}>
                        {server.transport}
                      </span>
                    </div>
                    <div className="text-xs text-slate-500 dark:text-slate-400">
                      {server.transport === "stdio" && server.command
                        ? `${server.command} ${(server.args || []).join(" ")}`
                        : `${server.host}:${server.port}`}
                      {server.source && ` | ${server.source}`}
                      {server.pid && ` | PID ${server.pid}`}
                    </div>
                  </div>
                  <div className="flex items-center gap-3">
                    <span className="text-xs text-slate-500 dark:text-slate-400">{server.tool_count} tool(s)</span>
                    <RiskBadge category={server.risk_category} score={server.risk_score} />
                    <button
                      onClick={(e) => { e.stopPropagation(); rescanServer(server.server_id); }}
                      className="p-1 rounded hover:bg-slate-200 dark:hover:bg-slate-600 text-slate-400"
                      title="Rescan server"
                    >
                      <RefreshCw className="w-3.5 h-3.5" />
                    </button>
                    <button
                      onClick={(e) => { e.stopPropagation(); unregisterServer(server.server_id); }}
                      className="p-1 rounded hover:bg-red-100 dark:bg-red-800/30 dark:hover:bg-red-900/30 text-red-400 hover:text-red-600"
                      title="Unregister server"
                    >
                      <Trash2 className="w-3.5 h-3.5" />
                    </button>
                  </div>
                </div>

                {/* Expanded Server Detail Panel */}
                {expandedServer === server.server_id && (
                  <div className="px-6 pb-4">
                    <div className="ml-8 bg-slate-50 dark:bg-slate-800/50/50 rounded-lg border border-slate-200 dark:border-slate-700 overflow-hidden">
                      {/* Server Metadata Grid */}
                      <div className="p-4 grid grid-cols-2 md:grid-cols-3 gap-x-6 gap-y-3 text-xs border-b border-slate-200 dark:border-slate-700">
                        {server.transport && (
                          <div>
                            <span className="text-slate-400 uppercase text-[10px] block mb-0.5">Transport</span>
                            <span className="text-slate-700 dark:text-slate-300 font-mono">{server.transport}</span>
                          </div>
                        )}
                        {server.source && (
                          <div>
                            <span className="text-slate-400 uppercase text-[10px] block mb-0.5">Source</span>
                            <span className="text-slate-700 dark:text-slate-300 font-mono">{server.source}</span>
                          </div>
                        )}
                        {server.status && (
                          <div>
                            <span className="text-slate-400 uppercase text-[10px] block mb-0.5">Status</span>
                            <span className="text-slate-700 dark:text-slate-300">{server.status}</span>
                          </div>
                        )}
                        {server.config_path && (
                          <div className="col-span-2 md:col-span-3">
                            <span className="text-slate-400 uppercase text-[10px] block mb-0.5">Config Path</span>
                            <span className="text-slate-700 dark:text-slate-300 font-mono break-all">{server.config_path}</span>
                          </div>
                        )}
                        {server.command && (
                          <div className="col-span-2 md:col-span-3">
                            <span className="text-slate-400 uppercase text-[10px] block mb-0.5">Command</span>
                            <span className="text-slate-700 dark:text-slate-300 font-mono break-all">
                              {server.command} {(server.args || []).join(" ")}
                            </span>
                          </div>
                        )}
                        {server.url && (
                          <div className="col-span-2 md:col-span-3">
                            <span className="text-slate-400 uppercase text-[10px] block mb-0.5">URL</span>
                            <span className="text-slate-700 dark:text-slate-300 font-mono break-all">{server.url}</span>
                          </div>
                        )}
                        {server.pid && (
                          <div>
                            <span className="text-slate-400 uppercase text-[10px] block mb-0.5">PID</span>
                            <span className="text-slate-700 dark:text-slate-300 font-mono">{server.pid}</span>
                          </div>
                        )}
                      </div>

                      {/* Risk Factors */}
                      {server.risk_factors && server.risk_factors.length > 0 && (
                        <div className="px-4 py-3 border-b border-slate-200 dark:border-slate-700">
                          <span className="text-slate-400 uppercase text-[10px] block mb-1.5">Risk Factors</span>
                          <div className="flex flex-wrap gap-1.5">
                            {server.risk_factors.map((factor, i) => (
                              <span
                                key={i}
                                className="inline-flex items-center gap-1 px-2 py-0.5 rounded text-xs bg-amber-100 dark:bg-amber-800/30 dark:bg-amber-900/30 text-amber-700 dark:text-amber-400"
                              >
                                <AlertTriangle className="w-3 h-3" />
                                {factor}
                              </span>
                            ))}
                          </div>
                        </div>
                      )}

                      {/* Environment Variable Keys */}
                      {server.env_keys && server.env_keys.length > 0 && (
                        <div className="px-4 py-3 border-b border-slate-200 dark:border-slate-700">
                          <span className="text-slate-400 uppercase text-[10px] block mb-1.5">Sensitive Environment Variables</span>
                          <div className="flex flex-wrap gap-1.5">
                            {server.env_keys.map((key, i) => (
                              <span
                                key={i}
                                className="px-2 py-0.5 rounded text-xs font-mono bg-red-50 dark:bg-red-900/20 text-red-600 dark:text-red-400 border border-red-200 dark:border-red-800"
                              >
                                {key}
                              </span>
                            ))}
                          </div>
                        </div>
                      )}

                      {/* Tools Table */}
                      {server.tools && server.tools.length > 0 && (
                        <table className="w-full text-sm">
                          <thead>
                            <tr className="bg-slate-100 dark:bg-slate-700 dark:bg-slate-800 text-left">
                              <th className="px-4 py-2 text-xs font-medium text-slate-500 dark:text-slate-300 uppercase">Tool</th>
                              <th className="px-4 py-2 text-xs font-medium text-slate-500 dark:text-slate-300 uppercase">Description</th>
                              <th className="px-4 py-2 text-xs font-medium text-slate-500 dark:text-slate-300 uppercase text-center">Schema</th>
                              <th className="px-4 py-2 text-xs font-medium text-slate-500 dark:text-slate-300 uppercase text-center">Risk</th>
                              <th className="px-4 py-2 text-xs font-medium text-slate-500 dark:text-slate-300 uppercase text-center">Detail</th>
                            </tr>
                          </thead>
                          <tbody className="divide-y divide-slate-200 dark:divide-slate-700">
                            {server.tools.map((tool) => (
                              <tr key={tool.tool_id} className="transition-colors hover:bg-slate-50 dark:hover:bg-slate-700/50">
                                <td className="px-4 py-2 font-mono text-xs text-slate-900 dark:text-slate-100 dark:text-white">{tool.name}</td>
                                <td className="px-4 py-2 text-xs text-slate-500 dark:text-slate-400 max-w-xs truncate">
                                  {tool.description ? tool.description.slice(0, 80) : "--"}
                                </td>
                                <td className="px-4 py-2 text-center">
                                  {tool.schema_valid ? (
                                    <CheckCircle className="w-4 h-4 text-emerald-500 inline" />
                                  ) : (
                                    <AlertTriangle className="w-4 h-4 text-amber-500 inline" />
                                  )}
                                </td>
                                <td className="px-4 py-2 text-center">
                                  <RiskBadge category={tool.risk_category} score={tool.risk_score} />
                                </td>
                                <td className="px-4 py-2 text-center">
                                  <button
                                    onClick={() => loadToolDetail(server.server_id, tool.name)}
                                    className="p-1 rounded hover:bg-slate-200 dark:hover:bg-slate-600 text-indigo-500"
                                    title="View tool details"
                                  >
                                    <Eye className="w-4 h-4" />
                                  </button>
                                </td>
                              </tr>
                            ))}
                          </tbody>
                        </table>
                      )}

                      {/* No tools fallback */}
                      {(!server.tools || server.tools.length === 0) && (
                        <div className="p-4 text-center text-xs text-slate-500 dark:text-slate-400">
                          {server.transport === "stdio"
                            ? "No tools discovered yet for this stdio server. Run Rescan and verify command/auth prerequisites."
                            : "No tools discovered during introspection."}
                        </div>
                      )}
                    </div>

                    {/* Runtime Telemetry */}
                    {server.runtime && (
                      <div className="ml-8 mt-3 p-3 bg-slate-50 dark:bg-slate-800/50/50 rounded-lg border border-slate-200 dark:border-slate-700">
                        <div className="text-xs font-medium text-slate-500 dark:text-slate-400 uppercase mb-2">Runtime Telemetry</div>
                        <div className="grid grid-cols-2 md:grid-cols-5 gap-3 text-xs">
                          <div className="flex items-center gap-1">
                            <Cpu className="w-3.5 h-3.5 text-slate-400" />
                            <span className="text-slate-600 dark:text-slate-400">CPU: {server.runtime.cpu_percent}%</span>
                          </div>
                          <div className="flex items-center gap-1">
                            <HardDrive className="w-3.5 h-3.5 text-slate-400" />
                            <span className="text-slate-600 dark:text-slate-400">Mem: {server.runtime.memory_mb} MB</span>
                          </div>
                          <div className="flex items-center gap-1">
                            <Network className="w-3.5 h-3.5 text-slate-400" />
                            <span className="text-slate-600 dark:text-slate-400">Conns: {server.runtime.open_connections}</span>
                          </div>
                          <div>
                            <span className="text-slate-600 dark:text-slate-400">Files: {server.runtime.open_files}</span>
                          </div>
                          <div>
                            <span className="text-slate-600 dark:text-slate-400">
                              Uptime: {Math.round(server.runtime.uptime_seconds / 60)}m
                            </span>
                          </div>
                        </div>
                        {server.runtime.anomalies && server.runtime.anomalies.length > 0 && (
                          <div className="mt-2 flex flex-wrap gap-1">
                            {server.runtime.anomalies.map((a, i) => (
                              <span key={i} className="px-1.5 py-0.5 rounded text-xs bg-red-100 dark:bg-red-800/30 text-red-700">{a}</span>
                            ))}
                          </div>
                        )}
                      </div>
                    )}
                  </div>
                )}
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Empty state */}
      {servers.length === 0 && scanStatus !== "running" && (
        <div className="bg-white dark:bg-slate-800 rounded-xl border border-slate-200 dark:border-slate-700 p-12 text-center">
          <Search className="w-10 h-10 text-slate-300 dark:text-slate-600 dark:text-slate-400 mx-auto mb-3" />
          <p className="text-sm text-slate-500 dark:text-slate-400 mb-1">No MCP servers discovered yet</p>
          <p className="text-xs text-slate-400 dark:text-slate-300 dark:text-slate-400">
            Click "Scan Now" to discover MCP servers running on this host
          </p>
        </div>
      )}

      {/* Graph / Risk Distribution */}
      {graphData && graphData.nodes && graphData.nodes.length > 1 && (
        <div className="bg-white dark:bg-slate-800 rounded-xl border border-slate-200 dark:border-slate-700 p-6">
          <h4 className="text-sm font-semibold text-slate-900 dark:text-slate-100 dark:text-white mb-4">Risk Distribution</h4>
          <div className="space-y-2">
            {servers.map((server) => (
              <div key={server.server_id} className="flex items-center gap-3">
                <span className="text-xs text-slate-600 dark:text-slate-400 w-40 truncate">
                  {server.name || `${server.host}:${server.port}`}
                </span>
                <div className="flex-1 bg-slate-100 dark:bg-slate-700 rounded-full h-4 overflow-hidden">
                  <div
                    className={`h-full rounded-full transition-all ${
                      server.risk_score >= 8 ? "bg-red-500" :
                      server.risk_score >= 6 ? "bg-orange-500" :
                      server.risk_score >= 4 ? "bg-amber-500" :
                      server.risk_score >= 2 ? "bg-blue-500" : "bg-emerald-500"
                    }`}
                    style={{ width: `${Math.min(server.risk_score * 10, 100)}%` }}
                  />
                </div>
                <span className="text-xs font-mono text-slate-500 dark:text-slate-400 w-10 text-right">{server.risk_score.toFixed(1)}</span>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Scan History */}
      {scanHistory.length > 0 && (
        <div className="bg-white dark:bg-slate-800 rounded-xl border border-slate-200 dark:border-slate-700">
          <button
            className="w-full px-6 py-4 flex items-center justify-between hover:bg-slate-50 dark:hover:bg-slate-700 dark:hover:bg-slate-700/50 transition-colors"
            onClick={() => setShowHistory(!showHistory)}
          >
            <h4 className="text-sm font-semibold text-slate-900 dark:text-slate-100 dark:text-white">
              Scan History ({scanHistory.length})
            </h4>
            {showHistory ? <ChevronDown className="w-4 h-4 text-slate-400" /> : <ChevronRight className="w-4 h-4 text-slate-400" />}
          </button>
          {showHistory && (
            <div className="px-6 pb-4">
              <div className="space-y-2">
                {scanHistory.map((run) => (
                  <div
                    key={run.run_id}
                    className="flex items-center gap-4 p-3 rounded-lg bg-slate-50 dark:bg-slate-800/50/50 border border-slate-200 dark:border-slate-700 text-xs"
                  >
                    <StatusBadge status={run.status} />
                    <span className="text-slate-500 dark:text-slate-400">{run.mode}</span>
                    <span className="text-slate-600 dark:text-slate-400 dark:text-slate-300">
                      {run.servers_discovered} servers, {run.total_tools} tools
                    </span>
                    {run.high_risk_count > 0 && (
                      <span className="text-orange-600">{run.high_risk_count} high</span>
                    )}
                    {run.critical_count > 0 && (
                      <span className="text-red-600">{run.critical_count} critical</span>
                    )}
                    <span className="ml-auto text-slate-400">
                      {run.duration_seconds ? `${run.duration_seconds}s` : "--"}
                    </span>
                    <span className="text-slate-400">
                      {run.started_at ? new Date(run.started_at).toLocaleString() : "--"}
                    </span>
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>
      )}

      {/* Tool Detail Modal */}
      {selectedTool && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4">
          <div className="bg-white dark:bg-slate-800 rounded-xl border border-slate-200 dark:border-slate-700 w-full max-w-2xl max-h-[80vh] overflow-auto shadow-xl">
            <div className="sticky top-0 bg-white dark:bg-slate-800 px-6 py-4 border-b border-slate-200 dark:border-slate-700 flex items-center justify-between">
              <div>
                <h4 className="text-base font-semibold text-slate-900 dark:text-slate-100 dark:text-white font-mono">{selectedTool.name}</h4>
                <div className="flex items-center gap-2 mt-1">
                  <RiskBadge category={selectedTool.risk_category} score={selectedTool.risk_score} />
                  {selectedTool.schema_valid ? (
                    <span className="text-xs text-emerald-600 flex items-center gap-1">
                      <CheckCircle className="w-3 h-3" /> Valid Schema
                    </span>
                  ) : (
                    <span className="text-xs text-amber-600 flex items-center gap-1">
                      <AlertTriangle className="w-3 h-3" /> Invalid Schema
                    </span>
                  )}
                </div>
              </div>
              <button
                onClick={() => setSelectedTool(null)}
                className="p-1 rounded hover:bg-slate-200 dark:hover:bg-slate-600"
              >
                <X className="w-5 h-5 text-slate-500 dark:text-slate-400" />
              </button>
            </div>

            <div className="p-6 space-y-4">
              {selectedTool.description && (
                <div>
                  <h5 className="text-xs font-medium text-slate-500 dark:text-slate-400 uppercase mb-1">Description</h5>
                  <p className="text-sm text-slate-700 dark:text-slate-300">{selectedTool.description}</p>
                </div>
              )}

              {selectedTool.declared_permissions && selectedTool.declared_permissions.length > 0 && (
                <div>
                  <h5 className="text-xs font-medium text-slate-500 dark:text-slate-400 uppercase mb-1">Declared Permissions</h5>
                  <div className="flex flex-wrap gap-1">
                    {selectedTool.declared_permissions.map((p, i) => (
                      <span key={i} className="px-2 py-0.5 rounded text-xs bg-slate-100 dark:bg-slate-700 text-slate-700 dark:text-slate-300 font-mono">{p}</span>
                    ))}
                  </div>
                </div>
              )}

              {selectedTool.input_schema && (
                <div>
                  <h5 className="text-xs font-medium text-slate-500 dark:text-slate-400 uppercase mb-1">Input Schema</h5>
                  <pre className="p-3 rounded-lg bg-slate-50 dark:bg-slate-800/50 text-xs overflow-auto max-h-40 text-slate-700 dark:text-slate-300 font-mono">
                    {JSON.stringify(selectedTool.input_schema, null, 2)}
                  </pre>
                </div>
              )}

              {selectedTool.risk_factors && selectedTool.risk_factors.length > 0 && (
                <div>
                  <h5 className="text-xs font-medium text-slate-500 dark:text-slate-400 uppercase mb-1">Risk Factors</h5>
                  <ul className="space-y-1">
                    {selectedTool.risk_factors.map((f, i) => (
                      <li key={i} className="flex items-start gap-2 text-xs text-slate-600 dark:text-slate-400">
                        <AlertTriangle className="w-3 h-3 text-amber-500 shrink-0 mt-0.5" />
                        {f}
                      </li>
                    ))}
                  </ul>
                </div>
              )}

              {selectedTool.risk_explanation && selectedTool.risk_explanation.length > 0 && (
                <div>
                  <h5 className="text-xs font-medium text-slate-500 dark:text-slate-400 uppercase mb-1">Risk Breakdown</h5>
                  <div className="space-y-1">
                    {selectedTool.risk_explanation.map((item, i) => (
                      <div key={i} className="flex items-center justify-between text-xs p-2 rounded bg-slate-50 dark:bg-slate-800/50">
                        <span className="text-slate-600 dark:text-slate-400">{item.factor}</span>
                        <span className="font-mono text-slate-700 dark:text-slate-300">+{item.contribution.toFixed(1)}</span>
                      </div>
                    ))}
                  </div>
                </div>
              )}

              {selectedTool.injection_results && selectedTool.injection_results.length > 0 && (
                <div>
                  <h5 className="text-xs font-medium text-slate-500 dark:text-slate-400 uppercase mb-1">Injection Test Results</h5>
                  <div className="space-y-2">
                    {selectedTool.injection_results.map((r, i) => (
                      <div key={i} className={`p-3 rounded-lg border text-xs ${
                        r.reflected
                          ? "bg-red-50 dark:bg-red-900/20 border-red-200 dark:border-red-800"
                          : "bg-emerald-50 dark:bg-emerald-900/20 dark:bg-emerald-900/20 border-emerald-200 dark:border-emerald-800"
                      }`}>
                        <div className="flex items-center justify-between mb-1">
                          <span className="font-medium text-slate-700 dark:text-slate-300 capitalize">
                            {r.payload_type.replace(/_/g, " ")}
                          </span>
                          {r.reflected ? (
                            <span className="text-red-600 flex items-center gap-1">
                              <AlertTriangle className="w-3 h-3" /> Reflected
                            </span>
                          ) : (
                            <span className="text-emerald-600 flex items-center gap-1">
                              <CheckCircle className="w-3 h-3" /> Safe
                            </span>
                          )}
                        </div>
                        <div className="font-mono text-slate-500 dark:text-slate-400 truncate">{r.payload_text}</div>
                        {r.response_summary && (
                          <div className="mt-1 text-slate-400 truncate">Response: {r.response_summary}</div>
                        )}
                      </div>
                    ))}
                  </div>
                </div>
              )}
            </div>
          </div>
        </div>
      )}

      {/* Register Server Modal */}
      {registerModalOpen && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4">
          <div className="bg-white dark:bg-slate-800 rounded-xl border border-slate-200 dark:border-slate-700 w-full max-w-lg max-h-[90vh] overflow-y-auto shadow-xl">
            <div className="px-6 py-4 border-b border-slate-200 dark:border-slate-700 flex items-center justify-between">
              <h4 className="text-base font-semibold text-slate-900 dark:text-slate-100 dark:text-white">Register MCP Server</h4>
              <button
                onClick={() => setRegisterModalOpen(false)}
                className="p-1 rounded hover:bg-slate-200 dark:hover:bg-slate-600"
              >
                <X className="w-5 h-5 text-slate-500 dark:text-slate-400" />
              </button>
            </div>

            <div className="p-6 space-y-4">
              <div>
                <label className="block text-xs font-medium text-slate-500 dark:text-slate-400 mb-1">Server Name</label>
                <input
                  type="text"
                  value={registerForm.name}
                  onChange={(e) => setRegisterForm({ ...registerForm, name: e.target.value })}
                  className="w-full px-3 py-2 text-sm rounded-lg border border-slate-300 dark:border-slate-600 bg-white dark:bg-slate-700 text-slate-900 dark:text-white"
                  placeholder="e.g., linear or filesystem-tools"
                />
              </div>

              <div>
                <label className="block text-xs font-medium text-slate-500 dark:text-slate-400 mb-1">Transport</label>
                <select
                  value={registerForm.transport}
                  onChange={(e) => setRegisterForm({ ...registerForm, transport: e.target.value })}
                  className="w-full px-3 py-2 text-sm rounded-lg border border-slate-300 dark:border-slate-600 bg-white dark:bg-slate-700 text-slate-900 dark:text-white"
                >
                  <option value="http">HTTP — local or network MCP server</option>
                  <option value="sse">SSE — Server-Sent Events MCP server</option>
                  <option value="stdio">Stdio — process-based (npx / mcp-remote)</option>
                </select>
              </div>

              {registerForm.transport !== "stdio" ? (
                <>
                  <div className="grid grid-cols-3 gap-3">
                    <div className="col-span-2">
                      <label className="block text-xs font-medium text-slate-500 dark:text-slate-400 mb-1">Host</label>
                      <input
                        type="text"
                        value={registerForm.host}
                        onChange={(e) => setRegisterForm({ ...registerForm, host: e.target.value })}
                        className="w-full px-3 py-2 text-sm rounded-lg border border-slate-300 dark:border-slate-600 bg-white dark:bg-slate-700 text-slate-900 dark:text-white"
                        placeholder="mcp-host.internal"
                      />
                    </div>
                    <div>
                      <label className="block text-xs font-medium text-slate-500 dark:text-slate-400 mb-1">Port</label>
                      <input
                        type="number"
                        value={registerForm.port}
                        onChange={(e) => setRegisterForm({ ...registerForm, port: e.target.value })}
                        className="w-full px-3 py-2 text-sm rounded-lg border border-slate-300 dark:border-slate-600 bg-white dark:bg-slate-700 text-slate-900 dark:text-white"
                        placeholder="8080"
                      />
                    </div>
                  </div>
                  <div>
                    <label className="block text-xs font-medium text-slate-500 dark:text-slate-400 mb-1">URL (optional override)</label>
                    <input
                      type="text"
                      value={registerForm.url}
                      onChange={(e) => setRegisterForm({ ...registerForm, url: e.target.value })}
                      className="w-full px-3 py-2 text-sm rounded-lg border border-slate-300 dark:border-slate-600 bg-white dark:bg-slate-700 text-slate-900 dark:text-white"
                      placeholder="https://mcp.example.com/mcp"
                    />
                  </div>
                </>
              ) : (
                <>
                  {/* Stdio quick-pick */}
                  <div className="rounded-lg bg-slate-50 dark:bg-slate-900/60 border border-slate-200 dark:border-slate-700 p-3 space-y-2">
                    <p className="text-xs font-semibold text-slate-600 dark:text-slate-300">Quick presets</p>
                    <div className="flex flex-wrap gap-2">
                      {[
                        { label: "Linear", cmd: "npx", args: "-y mcp-remote https://mcp.linear.app/mcp" },
                        { label: "Filesystem", cmd: "npx", args: "-y @modelcontextprotocol/server-filesystem /tmp" },
                        { label: "GitHub", cmd: "npx", args: "-y @modelcontextprotocol/server-github" },
                      ].map((p) => (
                        <button
                          key={p.label}
                          type="button"
                          onClick={() => setRegisterForm({ ...registerForm, name: registerForm.name || p.label, command: p.cmd, args: p.args })}
                          className="px-3 py-1.5 text-xs rounded-lg border border-slate-300 dark:border-slate-600 text-slate-700 dark:text-slate-300 hover:bg-white dark:hover:bg-slate-700 transition-colors"
                        >
                          {p.label}
                        </button>
                      ))}
                    </div>
                  </div>

                  <div>
                    <label className="block text-xs font-medium text-slate-500 dark:text-slate-400 mb-1">Command</label>
                    <input
                      type="text"
                      value={registerForm.command}
                      onChange={(e) => setRegisterForm({ ...registerForm, command: e.target.value })}
                      className="w-full px-3 py-2 text-sm rounded-lg border border-slate-300 dark:border-slate-600 bg-white dark:bg-slate-700 text-slate-900 dark:text-white font-mono"
                      placeholder="npx"
                    />
                  </div>
                  <div>
                    <label className="block text-xs font-medium text-slate-500 dark:text-slate-400 mb-1">Arguments (space-separated)</label>
                    <input
                      type="text"
                      value={registerForm.args}
                      onChange={(e) => setRegisterForm({ ...registerForm, args: e.target.value })}
                      className="w-full px-3 py-2 text-sm rounded-lg border border-slate-300 dark:border-slate-600 bg-white dark:bg-slate-700 text-slate-900 dark:text-white font-mono"
                      placeholder="-y mcp-remote https://mcp.linear.app/mcp"
                    />
                  </div>

                  {/* Stdio info note */}
                  <div className="flex items-start gap-2.5 rounded-lg bg-amber-50 dark:bg-amber-900/20 border border-amber-200 dark:border-amber-700/50 px-3 py-2.5 text-xs text-amber-700 dark:text-amber-300">
                    <AlertTriangle className="w-3.5 h-3.5 flex-shrink-0 mt-0.5" />
                    <span>
                      Stdio servers are introspected during registration and scan runs by spawning the
                      configured process and requesting <strong>tools/list</strong>.
                      For <strong>Linear</strong> and other OAuth-protected MCP servers, complete the
                      browser auth flow when prompted.
                    </span>
                  </div>
                </>
              )}

              <div className="flex items-center gap-2">
                <input
                  type="checkbox"
                  id="auto_introspect"
                  checked={registerForm.auto_introspect}
                  onChange={(e) => setRegisterForm({ ...registerForm, auto_introspect: e.target.checked })}
                  className="rounded border-slate-300 dark:border-slate-600 text-indigo-600 focus:ring-indigo-500"
                />
                <label htmlFor="auto_introspect" className="text-sm text-slate-600 dark:text-slate-400">
                  Auto-introspect on registration (HTTP/SSE and mcp-remote stdio servers)
                </label>
              </div>
            </div>

            <div className="px-6 py-4 border-t border-slate-200 dark:border-slate-700 flex justify-end gap-3">
              <button
                onClick={() => setRegisterModalOpen(false)}
                className="px-4 py-2 text-sm font-medium rounded-lg border border-slate-300 dark:border-slate-600 text-slate-700 dark:text-slate-300 hover:bg-slate-50 dark:hover:bg-slate-700"
              >
                Cancel
              </button>
              <button
                onClick={registerServer}
                disabled={!registerForm.name || registering}
                className="inline-flex items-center gap-1.5 px-4 py-2 text-sm font-medium rounded-lg bg-indigo-600 text-white hover:bg-indigo-700 disabled:opacity-50 disabled:cursor-not-allowed"
              >
                {registering ? (
                  <Loader2 className="w-4 h-4 animate-spin" />
                ) : (
                  <Plus className="w-4 h-4" />
                )}
                Register Server
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
