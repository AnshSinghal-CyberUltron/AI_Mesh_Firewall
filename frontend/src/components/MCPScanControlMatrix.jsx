/**
 * MCP scan controls — minimal, operator-first surface.
 *   1. Tier-1 pointer — detection & enforcement live in MCP Security Policies
 *      (this page does not duplicate that config; it links there).
 *   2. Tier-2 (ZeroShield model) scan — pure on/off: an org master
 *      (Inherit / Enabled / Disabled) + a per-server enable optionally scoped
 *      to specific tools. Each choice maps to MCPScanControl rows (tier="tier2")
 *      carrying neutral defaults; only scope_type + server + tool_name vary:
 *        server ON + all tools  → ONE { scope_type:"server", server } row
 *        server ON + specific   → one { scope_type:"tool", server, tool_name } row per tool
 *        server OFF             → no rows for that server
 */

import { useCallback, useEffect, useMemo, useState } from "react";
import {
  AlertTriangle,
  ArrowRight,
  Globe,
  RefreshCw,
  ShieldCheck,
  Sparkles,
  Wrench,
} from "lucide-react";

import { Button } from "./ui/Button";
import { Badge } from "./ui/Badge";
import { Card, CardContent } from "./ui/Card";
import { Spinner } from "./ui/Spinner";
import { Switch } from "./ui/Switch";
import { SegmentedControl } from "./ui/SegmentedControl";
import { InfoHint } from "./ui/Tooltip";
import { EmptyState } from "./ui/EmptyState";
import { PanelHeader } from "./ui/PanelHeader";
import { useToast } from "./ui/Toast";
import { ZEROSHIELD_TIER2_LABEL } from "../constants/zeroshieldBrand";
import { cn } from "../lib/utils";

// Every Tier-2 row shares these neutral defaults — Tier-2 is pure on/off, so
// direction/action/target/strict/priority never vary here (they carry the
// model defaults). Only scope_type + server + tool_name change per row.
const TIER2_ROW_BASE = {
  tier: "tier2",
  direction: "both",
  action: "inherit",
  target_mode: "entire",
  key_path: "",
  strict_mode: "fail_open",
  priority: 0,
  enabled: true,
};

const TIER2_OPTIONS = [
  { value: "inherit", label: "Inherit" },
  { value: "enabled", label: "Enabled" },
  { value: "disabled", label: "Disabled" },
];
const TIER2_PAYLOAD = { inherit: null, enabled: true, disabled: false };

const APPLY_OPTIONS = [
  { value: "all", label: "All tools" },
  { value: "specific", label: "Specific tools" },
];

/** One connected server's Tier-2 on/off + optional per-tool scoping. */
function ServerTier2Card({
  server,
  on,
  mode,
  selected,
  saving,
  disabled,
  tools,
  toolsLoading,
  onToggle,
  onModeChange,
  onToolToggle,
}) {
  const toolCount = server.tools_count ?? null;

  return (
    <div className="rounded-xl border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-800/40">
      <div className="flex items-center justify-between gap-3 px-4 py-3">
        <div className="min-w-0">
          <p className="truncate text-sm font-semibold text-slate-900 dark:text-slate-100">
            {server.name}
          </p>
          <p className="mt-0.5 flex items-center gap-1 text-[11px] text-slate-500 dark:text-slate-400">
            <Wrench className="h-3 w-3" aria-hidden="true" />
            {toolCount != null ? `${toolCount} tool${toolCount === 1 ? "" : "s"}` : "connected"}
            {on && (
              <>
                <span aria-hidden="true">·</span>
                <span className="text-violet-600 dark:text-violet-400">
                  Tier-2 {mode === "all" ? "all tools" : `${selected.size} selected`}
                </span>
              </>
            )}
          </p>
        </div>
        <div className="flex shrink-0 items-center gap-2">
          {saving && <Spinner className="h-4 w-4 text-violet-500" />}
          <Switch
            checked={on}
            disabled={disabled || saving}
            onCheckedChange={(v) => onToggle(server, v)}
            label={`Tier-2 scan for ${server.name}`}
          />
        </div>
      </div>

      {on && (
        <div className="space-y-3 border-t border-slate-200 dark:border-slate-700 px-4 py-3">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <span className="text-xs font-medium text-slate-600 dark:text-slate-300">Apply to</span>
            <SegmentedControl
              aria-label={`Tier-2 scope for ${server.name}`}
              value={mode}
              onChange={(v) => { if (!saving) onModeChange(server, v); }}
              options={APPLY_OPTIONS}
            />
          </div>

          {mode === "specific" && (
            <div className="space-y-1.5">
              {toolsLoading ? (
                <div className="flex items-center gap-2 py-2 text-xs text-slate-500 dark:text-slate-400">
                  <Spinner className="h-3.5 w-3.5" /> Loading tools…
                </div>
              ) : !tools || tools.length === 0 ? (
                <p className="py-2 text-xs text-slate-400 dark:text-slate-500">
                  No tools discovered for this server yet.
                </p>
              ) : (
                <ul className="space-y-1">
                  {tools.map((tool) => (
                    <li
                      key={tool.tool_name}
                      className="flex items-center justify-between gap-3 rounded-lg bg-slate-50 dark:bg-slate-900/50 px-3 py-1.5"
                    >
                      <span className="truncate font-mono text-xs text-slate-700 dark:text-slate-300">
                        {tool.tool_name}
                      </span>
                      <Switch
                        checked={selected.has(tool.tool_name)}
                        disabled={saving}
                        onCheckedChange={(v) => onToolToggle(server, tool.tool_name, v)}
                        label={`Tier-2 scan for ${tool.tool_name}`}
                      />
                    </li>
                  ))}
                </ul>
              )}
              {selected.size === 0 && (
                <p className="text-[11px] text-amber-600 dark:text-amber-400">
                  No tools selected — Tier-2 won&apos;t scan any call on this server until you pick at least one.
                </p>
              )}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

export function MCPScanControlMatrix({ fetchWithAuth, servers = [], onControlsChanged, onOpenPolicies }) {
  const { toast } = useToast();

  const [rows, setRows] = useState([]); // Tier-2 controls only
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);

  const [mcpTier2, setMcpTier2] = useState(null);
  const [tier2Saving, setTier2Saving] = useState(false);

  const [toolsByServer, setToolsByServer] = useState({}); // serverId -> tool[]
  const [toolsLoading, setToolsLoading] = useState({}); // serverId -> bool
  const [savingServer, setSavingServer] = useState({}); // serverId -> bool
  // Servers where the operator picked "Specific tools" but no tool rows exist
  // yet — keeps the card on/specific instead of collapsing to off on reload.
  const [specificPending, setSpecificPending] = useState({}); // serverId -> bool

  const loadControls = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await fetchWithAuth("/api/mcp-connector/scan-controls/");
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      const all = Array.isArray(data) ? data : (data.results ?? []);
      setRows(all.filter((c) => c.tier === "tier2"));
      onControlsChanged?.();
    } catch (e) {
      setError(e.message || "Failed to load scan controls");
    } finally {
      setLoading(false);
    }
  }, [fetchWithAuth, onControlsChanged]);

  const loadTier2Master = useCallback(async () => {
    try {
      const res = await fetchWithAuth("/api/firewall/config/");
      if (!res.ok) return;
      const data = await res.json();
      setMcpTier2(data.mcp_tier2_enabled);
    } catch {
      /* optional */
    }
  }, [fetchWithAuth]);

  useEffect(() => {
    loadControls();
    loadTier2Master();
  }, [loadControls, loadTier2Master]);

  const loadToolsFor = useCallback(
    async (serverId) => {
      if (toolsByServer[serverId] || toolsLoading[serverId]) return;
      setToolsLoading((m) => ({ ...m, [serverId]: true }));
      try {
        const res = await fetchWithAuth(`/api/mcp-connector/servers/${serverId}/tools/`);
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const data = await res.json();
        const list = Array.isArray(data) ? data : (data.results ?? data.tools ?? []);
        setToolsByServer((m) => ({ ...m, [serverId]: list }));
      } catch (e) {
        setToolsByServer((m) => ({ ...m, [serverId]: [] }));
        toast(`Failed to load tools: ${e.message}`, { tone: "error" });
      } finally {
        setToolsLoading((m) => ({ ...m, [serverId]: false }));
      }
    },
    [fetchWithAuth, toolsByServer, toolsLoading, toast]
  );

  // Lazy-load tool lists for any server already scoped to specific tools.
  useEffect(() => {
    const ids = new Set(rows.filter((r) => r.scope_type === "tool").map((r) => String(r.server_id)));
    ids.forEach((sid) => {
      const srv = servers.find((s) => String(s.id) === sid);
      if (srv) loadToolsFor(srv.id);
    });
  }, [rows, servers, loadToolsFor]);

  const rowsForServer = useCallback(
    (serverId) => rows.filter((r) => String(r.server_id) === String(serverId)),
    [rows]
  );

  /* ── org Tier-2 master ── */
  const saveTier2Master = async (value) => {
    if (tier2Saving) return;
    setTier2Saving(true);
    setError(null);
    const prev = mcpTier2;
    setMcpTier2(value); // optimistic
    try {
      // PUT only the changed field (documented partial update) — re-sending the
      // whole config re-validates unrelated siblings and can 400 for reasons
      // unrelated to Tier-2. value: null=Inherit, true=Enabled, false=Disabled.
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
      setError(e.message);
      toast(e.message || "Failed to save Tier-2 setting", { tone: "error" });
    } finally {
      setTier2Saving(false);
    }
  };

  /* ── per-server Tier-2 row writes (optimistic + rollback) ── */
  const createRow = async (body) => {
    const res = await fetchWithAuth("/api/mcp-connector/scan-controls/", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    if (!res.ok) {
      const b = await res.json().catch(() => ({}));
      throw new Error(b.detail || b.server?.[0] || b.tool_name?.[0] || `HTTP ${res.status}`);
    }
    return res.json();
  };

  const deleteRow = async (id) => {
    const res = await fetchWithAuth(`/api/mcp-connector/scan-controls/${id}/`, { method: "DELETE" });
    if (!res.ok && res.status !== 204) throw new Error(`HTTP ${res.status}`);
  };

  // One in-flight mutation per server: optimistically swap `rows`, run the
  // create/delete ops, then reload to reconcile real ids; roll back on error.
  const runMutation = async (serverId, optimistic, ops) => {
    if (savingServer[serverId]) return;
    const snapshot = rows;
    setSavingServer((m) => ({ ...m, [serverId]: true }));
    setError(null);
    if (optimistic) setRows(optimistic);
    try {
      await ops();
      await loadControls();
      onControlsChanged?.();
    } catch (e) {
      setRows(snapshot); // rollback
      setError(e.message);
      toast(e.message || "Failed to update Tier-2 scan", { tone: "error" });
    } finally {
      setSavingServer((m) => ({ ...m, [serverId]: false }));
    }
  };

  const otherServerRows = (serverId) =>
    rows.filter((r) => String(r.server_id) !== String(serverId));

  const optimisticServerRow = (server) => ({
    ...TIER2_ROW_BASE,
    id: `tmp-${server.id}`,
    scope_type: "server",
    server_id: server.id,
    server_slug: server.server_slug,
    tool_name: "",
  });

  const handleServerToggle = (server, nextOn) => {
    const serverId = server.id;
    const current = rowsForServer(serverId);
    setSpecificPending((m) => ({ ...m, [serverId]: false }));
    if (nextOn) {
      // OFF → ON: default to "all tools" (one server-wide row).
      runMutation(serverId, [...otherServerRows(serverId), optimisticServerRow(server)], async () => {
        for (const r of current) await deleteRow(r.id);
        await createRow({ ...TIER2_ROW_BASE, scope_type: "server", server: serverId, tool_name: "" });
      });
    } else {
      // ON → OFF: delete every Tier-2 row for this server.
      runMutation(serverId, otherServerRows(serverId), async () => {
        for (const r of current) await deleteRow(r.id);
      });
    }
  };

  const handleModeChange = (server, mode) => {
    const serverId = server.id;
    const current = rowsForServer(serverId);
    const serverRow = current.find((r) => r.scope_type === "server");
    const toolRows = current.filter((r) => r.scope_type === "tool");
    if (mode === "all") {
      // Collapse to a single server-wide row; drop per-tool rows.
      setSpecificPending((m) => ({ ...m, [serverId]: false }));
      const optimistic = [
        ...otherServerRows(serverId),
        serverRow ? { ...serverRow } : optimisticServerRow(server),
      ];
      runMutation(serverId, optimistic, async () => {
        for (const r of toolRows) await deleteRow(r.id);
        if (!serverRow) {
          await createRow({ ...TIER2_ROW_BASE, scope_type: "server", server: serverId, tool_name: "" });
        }
      });
    } else {
      // Specific: drop the server-wide row, keep any per-tool rows.
      setSpecificPending((m) => ({ ...m, [serverId]: true }));
      loadToolsFor(serverId);
      const optimistic = rows.filter(
        (r) => !(String(r.server_id) === String(serverId) && r.scope_type === "server")
      );
      runMutation(serverId, optimistic, async () => {
        if (serverRow) await deleteRow(serverRow.id);
      });
    }
  };

  const handleToolToggle = (server, toolName, checked) => {
    const serverId = server.id;
    const existing = rowsForServer(serverId).find(
      (r) => r.scope_type === "tool" && r.tool_name === toolName
    );
    if (checked) {
      if (existing) return;
      const optimistic = [
        ...rows,
        {
          ...TIER2_ROW_BASE,
          id: `tmp-${serverId}-${toolName}`,
          scope_type: "tool",
          server_id: serverId,
          server_slug: server.server_slug,
          tool_name: toolName,
        },
      ];
      runMutation(serverId, optimistic, async () => {
        await createRow({ ...TIER2_ROW_BASE, scope_type: "tool", server: serverId, tool_name: toolName });
      });
    } else {
      if (!existing) return;
      runMutation(serverId, rows.filter((r) => r.id !== existing.id), async () => {
        await deleteRow(existing.id);
      });
    }
  };

  /* ── derived master-toggle presentation ── */
  const tier2State = mcpTier2 === null ? "inherit" : mcpTier2 ? "enabled" : "disabled";
  const tier2StateLabel =
    mcpTier2 === null ? "Inherit (org default)" : mcpTier2 ? "Enabled" : "Disabled";
  const tier2BadgeVariant = mcpTier2 === null ? "secondary" : mcpTier2 ? "success" : "danger";
  const perServerInactive = mcpTier2 === false;

  const activeCount = useMemo(() => {
    const ids = new Set(rows.map((r) => String(r.server_id)).filter(Boolean));
    return ids.size;
  }, [rows]);

  return (
    <div className="space-y-5">
      <PanelHeader
        icon={ShieldCheck}
        title="MCP scan controls"
        description={`Tier-1 detection & enforcement lives in MCP Security Policies. This page controls only the on/off Tier-2 ${ZEROSHIELD_TIER2_LABEL} scan.`}
        actions={
          <Button
            variant="outline"
            size="icon"
            aria-label="Refresh scan controls"
            onClick={loadControls}
            disabled={loading}
          >
            <RefreshCw className={cn("h-4 w-4", loading && "animate-spin")} />
          </Button>
        }
      />

      {/* Section 1 — Tier-1 pointer */}
      <Card>
        <CardContent className="flex flex-col gap-3 p-4 sm:flex-row sm:items-center sm:justify-between">
          <div className="flex items-start gap-3">
            <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-indigo-100 dark:bg-indigo-900/50 text-indigo-600 dark:text-indigo-300">
              <ShieldCheck className="h-4 w-4" aria-hidden="true" />
            </div>
            <div className="min-w-0">
              <p className="text-sm font-semibold text-slate-900 dark:text-slate-100">
                Tier-1 detection &amp; enforcement
              </p>
              <p className="mt-0.5 text-xs leading-relaxed text-slate-500 dark:text-slate-400">
                Tier-1 detection &amp; enforcement lives in MCP Security Policies. Each rule selects
                its server, tool(s) or all tools, Apply-To (input / output / both), scope (entire
                payload / specific key), and action (block / redact / tag / allow).
              </p>
            </div>
          </div>
          {typeof onOpenPolicies === "function" && (
            <Button className="shrink-0 self-start sm:self-center" onClick={onOpenPolicies}>
              Manage Tier-1 rules in Policies
              <ArrowRight className="h-4 w-4" aria-hidden="true" />
            </Button>
          )}
        </CardContent>
      </Card>

      {error && (
        <div
          role="alert"
          className="flex items-center gap-2 rounded-lg bg-red-50 dark:bg-red-900/20 px-3 py-2.5 text-sm text-red-700 dark:text-red-300"
        >
          <AlertTriangle className="h-4 w-4 shrink-0" />
          {error}
        </div>
      )}

      {/* Section 2 — Tier-2 org master */}
      <Card>
        <CardContent className="flex flex-col gap-4 p-4 sm:flex-row sm:items-center sm:justify-between">
          <div className="flex items-start gap-3">
            <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl bg-violet-100 dark:bg-violet-900/40 text-violet-600 dark:text-violet-300">
              <Sparkles className="h-5 w-5" aria-hidden="true" />
            </div>
            <div>
              <div className="flex flex-wrap items-center gap-2">
                <h3 className="text-sm font-semibold text-slate-900 dark:text-slate-100">
                  Tier-2 ({ZEROSHIELD_TIER2_LABEL}) — org master
                </h3>
                <Badge variant={tier2BadgeVariant}>{tier2StateLabel}</Badge>
                <InfoHint content={`Tier-2 must be enabled for the org before per-server scans run. When on, the ${ZEROSHIELD_TIER2_LABEL} model judges each MCP tool call (after Tier-1) and returns allow, block (with reason), or flag for review. Inherit defers to the org global default; Enabled/Disabled force it for MCP tool calls.`} />
              </div>
              <p className="mt-1 text-xs text-slate-500 dark:text-slate-400">
                Tier-2 must be enabled for the org before per-server scans run.
              </p>
            </div>
          </div>
          <div className="flex items-center gap-2 self-start sm:self-center">
            {tier2Saving && <Spinner className="h-4 w-4 text-violet-500" />}
            <SegmentedControl
              aria-label="Org MCP Tier-2 mode"
              value={tier2State}
              onChange={(v) => saveTier2Master(TIER2_PAYLOAD[v])}
              options={TIER2_OPTIONS}
            />
          </div>
        </CardContent>
      </Card>

      {perServerInactive && (
        <div className="flex items-center gap-2 rounded-lg bg-amber-50 dark:bg-amber-900/20 px-3 py-2.5 text-xs text-amber-700 dark:text-amber-300">
          <AlertTriangle className="h-4 w-4 shrink-0" />
          Tier-2 is disabled for the org — per-server scans below are inactive until you set the
          master to Inherit or Enabled.
        </div>
      )}

      {/* Section 2 — per-server list */}
      <Card>
        <div className="flex items-center justify-between gap-2 border-b border-slate-200 dark:border-slate-700 px-4 py-3">
          <div className="flex items-center gap-2">
            <h3 className="text-sm font-semibold text-slate-900 dark:text-slate-100">
              Per-server Tier-2 scan
            </h3>
            <InfoHint content="Turn Tier-2 on for a connected server, then choose whether it scans all of the server's tools or only specific ones." />
          </div>
          {activeCount > 0 && (
            <Badge variant="outline">
              {activeCount} server{activeCount === 1 ? "" : "s"} on
            </Badge>
          )}
        </div>
        <CardContent className="p-4">
          {servers.length === 0 ? (
            <EmptyState
              className="py-8"
              icon={Wrench}
              title="No connected servers"
              description="Register an MCP server on the MCP Servers tab, then return here to enable Tier-2 scanning for it."
            />
          ) : (
            <div className="space-y-3">
              {servers.map((server) => {
                const current = rowsForServer(server.id);
                const serverRow = current.find((r) => r.scope_type === "server");
                const toolRows = current.filter((r) => r.scope_type === "tool");
                const pending = !!specificPending[server.id];
                const on = Boolean(serverRow) || toolRows.length > 0 || pending;
                const mode = serverRow ? "all" : toolRows.length > 0 || pending ? "specific" : "all";
                const selected = new Set(toolRows.map((r) => r.tool_name));
                return (
                  <ServerTier2Card
                    key={server.id}
                    server={server}
                    on={on}
                    mode={mode}
                    selected={selected}
                    saving={!!savingServer[server.id]}
                    disabled={perServerInactive}
                    tools={toolsByServer[server.id]}
                    toolsLoading={!!toolsLoading[server.id]}
                    onToggle={handleServerToggle}
                    onModeChange={handleModeChange}
                    onToolToggle={handleToolToggle}
                  />
                );
              })}
            </div>
          )}
        </CardContent>
      </Card>

      {/* External / unregistered MCP servers — registration is the gate. */}
      <Card>
        <CardContent className="flex items-start gap-3 p-4">
          <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-slate-100 dark:bg-slate-800 text-slate-500 dark:text-slate-300">
            <Globe className="h-4 w-4" aria-hidden="true" />
          </div>
          <div className="min-w-0">
            <h4 className="text-sm font-semibold text-slate-900 dark:text-slate-100">
              External MCP servers
            </h4>
            <p className="mt-1 text-xs leading-relaxed text-slate-500 dark:text-slate-400">
              Unregistered third-party MCP servers can&apos;t be used — traffic to them is blocked
              until the server is registered on the{" "}
              <span className="font-medium text-slate-700 dark:text-slate-200">MCP Servers</span> tab.
              Once registered, every tool call is governed by your MCP Security Policies.
            </p>
          </div>
        </CardContent>
      </Card>
    </div>
  );
}
