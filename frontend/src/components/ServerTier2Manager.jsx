/**
 * ServerTier2Manager — ONE connected server's Tier-2 (ZeroShield model) scan.
 *
 * Tier-2 is pure on/off, optionally scoped to specific tools. Each choice maps
 * to MCPScanControl rows (tier="tier2") carrying neutral defaults; only
 * scope_type + server + tool_name vary:
 *   server ON + all tools  → ONE { scope_type:"server", server } row
 *   server ON + specific   → one { scope_type:"tool", server, tool_name } row per tool
 *   server OFF             → no rows for that server
 *
 * This is the single implementation of the per-server Tier-2 surface. It is
 * rendered both by MCPScanControlMatrix (the Scan Controls tab) and by the
 * per-server "Manage Tier-2" modal on the MCP Servers list.
 */

import { useCallback, useEffect, useState } from "react";
import { Wrench } from "lucide-react";

import { Spinner } from "./ui/Spinner";
import { Switch } from "./ui/Switch";
import { SegmentedControl } from "./ui/SegmentedControl";
import { useToast } from "./ui/Toast";

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

const APPLY_OPTIONS = [
  { value: "all", label: "All tools" },
  { value: "specific", label: "Specific tools" },
];

/** Presentational card body — pure, driven entirely by props. */
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
            onCheckedChange={(v) => onToggle(v)}
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
              onChange={(v) => { if (!saving) onModeChange(v); }}
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
                        onCheckedChange={(v) => onToolToggle(tool.tool_name, v)}
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

/**
 * Stateful manager for one server's Tier-2 rows. Owns its own row + tool state
 * and persists exactly as the Scan Controls tab always has (server-wide row for
 * all-tools; per-tool rows for specific; delete-all on off). Calls `onChanged`
 * after any successful mutation so a parent can refresh derived state.
 */
export function ServerTier2Manager({ server, fetchWithAuth, onChanged, disabled = false }) {
  const { toast } = useToast();

  const [rows, setRows] = useState([]); // Tier-2 rows for THIS server
  const [tools, setTools] = useState(null);
  const [toolsLoading, setToolsLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  // Operator picked "Specific tools" but no tool rows exist yet — keeps the card
  // on/specific instead of collapsing to off on reload.
  const [specificPending, setSpecificPending] = useState(false);

  const loadRows = useCallback(async () => {
    try {
      const res = await fetchWithAuth("/api/mcp-connector/scan-controls/");
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      const all = Array.isArray(data) ? data : (data.results ?? []);
      setRows(
        all.filter(
          (c) => c.tier === "tier2" && String(c.server_id) === String(server.id)
        )
      );
    } catch (e) {
      toast(e.message || "Failed to load scan controls", { tone: "error" });
    }
  }, [fetchWithAuth, server.id, toast]);

  const loadTools = useCallback(async () => {
    if (tools || toolsLoading) return;
    setToolsLoading(true);
    try {
      const res = await fetchWithAuth(`/api/mcp-connector/servers/${server.id}/tools/`);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      const list = Array.isArray(data) ? data : (data.results ?? data.tools ?? []);
      setTools(list);
    } catch (e) {
      setTools([]);
      toast(`Failed to load tools: ${e.message}`, { tone: "error" });
    } finally {
      setToolsLoading(false);
    }
  }, [fetchWithAuth, server.id, tools, toolsLoading, toast]);

  useEffect(() => {
    loadRows();
  }, [loadRows]);

  // Lazy-load the tool list when this server is scoped to specific tools.
  useEffect(() => {
    if (rows.some((r) => r.scope_type === "tool")) loadTools();
  }, [rows, loadTools]);

  /* ── row writes (optimistic + rollback) ── */
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

  // One in-flight mutation: optimistically swap `rows`, run the create/delete
  // ops, then reload to reconcile real ids; roll back on error.
  const runMutation = async (optimistic, ops) => {
    if (saving) return;
    const snapshot = rows;
    setSaving(true);
    if (optimistic) setRows(optimistic);
    try {
      await ops();
      await loadRows();
      onChanged?.();
    } catch (e) {
      setRows(snapshot); // rollback
      toast(e.message || "Failed to update Tier-2 scan", { tone: "error" });
    } finally {
      setSaving(false);
    }
  };

  const optimisticServerRow = () => ({
    ...TIER2_ROW_BASE,
    id: `tmp-${server.id}`,
    scope_type: "server",
    server_id: server.id,
    server_slug: server.server_slug,
    tool_name: "",
  });

  /* ── derived state ── */
  const serverRow = rows.find((r) => r.scope_type === "server");
  const toolRows = rows.filter((r) => r.scope_type === "tool");
  const on = Boolean(serverRow) || toolRows.length > 0 || specificPending;
  const mode = serverRow ? "all" : toolRows.length > 0 || specificPending ? "specific" : "all";
  const selected = new Set(toolRows.map((r) => r.tool_name));

  const handleToggle = (nextOn) => {
    setSpecificPending(false);
    if (nextOn) {
      // OFF → ON: default to "all tools" (one server-wide row).
      runMutation([optimisticServerRow()], async () => {
        for (const r of rows) await deleteRow(r.id);
        await createRow({ ...TIER2_ROW_BASE, scope_type: "server", server: server.id, tool_name: "" });
      });
    } else {
      // ON → OFF: delete every Tier-2 row for this server.
      runMutation([], async () => {
        for (const r of rows) await deleteRow(r.id);
      });
    }
  };

  const handleModeChange = (nextMode) => {
    if (nextMode === "all") {
      // Collapse to a single server-wide row; drop per-tool rows.
      setSpecificPending(false);
      const optimistic = [serverRow ? { ...serverRow } : optimisticServerRow()];
      runMutation(optimistic, async () => {
        for (const r of toolRows) await deleteRow(r.id);
        if (!serverRow) {
          await createRow({ ...TIER2_ROW_BASE, scope_type: "server", server: server.id, tool_name: "" });
        }
      });
    } else {
      // Specific: drop the server-wide row, keep any per-tool rows.
      setSpecificPending(true);
      loadTools();
      const optimistic = rows.filter((r) => r.scope_type !== "server");
      runMutation(optimistic, async () => {
        if (serverRow) await deleteRow(serverRow.id);
      });
    }
  };

  const handleToolToggle = (toolName, checked) => {
    const existing = toolRows.find((r) => r.tool_name === toolName);
    if (checked) {
      if (existing) return;
      const optimistic = [
        ...rows,
        {
          ...TIER2_ROW_BASE,
          id: `tmp-${server.id}-${toolName}`,
          scope_type: "tool",
          server_id: server.id,
          server_slug: server.server_slug,
          tool_name: toolName,
        },
      ];
      runMutation(optimistic, async () => {
        await createRow({ ...TIER2_ROW_BASE, scope_type: "tool", server: server.id, tool_name: toolName });
      });
    } else {
      if (!existing) return;
      runMutation(rows.filter((r) => r.id !== existing.id), async () => {
        await deleteRow(existing.id);
      });
    }
  };

  return (
    <ServerTier2Card
      server={server}
      on={on}
      mode={mode}
      selected={selected}
      saving={saving}
      disabled={disabled}
      tools={tools}
      toolsLoading={toolsLoading}
      onToggle={handleToggle}
      onModeChange={handleModeChange}
      onToolToggle={handleToolToggle}
    />
  );
}
