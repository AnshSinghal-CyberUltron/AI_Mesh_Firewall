/**
 * MCP scan control matrix — CRUD for Tier-1/Tier-2 scan rows + effective preview.
 *
 * Tier-1 = static scanners (always-on gate). Tier-2 = Bedrock semantic scan that
 * only runs after Tier-1 passes, when enabled. Precedence inside a tier/direction:
 * tool > server > org; higher priority wins within the same scope.
 */

import { useCallback, useEffect, useMemo, useState } from "react";
import {
  AlertTriangle,
  Pencil,
  Plus,
  RefreshCw,
  Shield,
  ShieldCheck,
  Sparkles,
  Target,
  Trash2,
} from "lucide-react";

import { Button } from "./ui/Button";
import { Badge } from "./ui/Badge";
import { Card, CardContent } from "./ui/Card";
import { Spinner } from "./ui/Spinner";
import { Select } from "./ui/Select";
import { Switch } from "./ui/Switch";
import { SegmentedControl } from "./ui/SegmentedControl";
import { InfoHint } from "./ui/Tooltip";
import { EmptyState } from "./ui/EmptyState";
import { Skeleton } from "./ui/Skeleton";
import { PanelHeader } from "./ui/PanelHeader";
import { Table, THead, TBody, TR, TH, TD } from "./ui/Table";
import { Dialog, DialogHeader, DialogBody, DialogFooter } from "./ui/Dialog";
import { useToast } from "./ui/Toast";
import { ACTION, actionInfo } from "../lib/mcpColors";
import { ZEROSHIELD_TIER2_LABEL } from "../constants/zeroshieldBrand";
import { cn } from "../lib/utils";

const SCOPE_RANK = { tool: 3, server: 2, org: 1 };

const DEFAULT_TIER1 = {
  tier: "tier1",
  enabled: true,
  target_mode: "entire",
  key_path: "",
  strict_mode: "fail_open",
  action: "inherit",
};

const DEFAULT_TIER2 = {
  tier: "tier2",
  enabled: false,
  target_mode: "entire",
  key_path: "",
  strict_mode: "strict",
  action: "inherit",
};

function directionMatches(controlDir, scanDir) {
  if (controlDir === "both") return true;
  return controlDir === scanDir;
}

function scopeMatches(row, serverId, toolName) {
  const scope = row.scope_type || "org";
  if (scope === "org") return !row.server_id;
  if (scope === "server") {
    return row.server_id && String(row.server_id) === String(serverId) && !row.tool_name;
  }
  if (scope === "tool") {
    return (
      row.server_id
      && String(row.server_id) === String(serverId)
      && (row.tool_name || "") === (toolName || "")
    );
  }
  return false;
}

function pickControl(rows, tier, scanDirection, serverId, toolName) {
  const candidates = rows.filter((row) => {
    if (row.tier !== tier) return false;
    const ctrlDir = row.direction || "both";
    if (!directionMatches(ctrlDir, scanDirection)) return false;
    return scopeMatches(row, serverId, toolName);
  });

  if (!candidates.length) {
    const base = tier === "tier1" ? DEFAULT_TIER1 : DEFAULT_TIER2;
    return { ...base, direction: scanDirection, control_id: null };
  }

  const best = candidates.reduce((a, b) => {
    const rankA = SCOPE_RANK[a.scope_type || "org"] || 0;
    const rankB = SCOPE_RANK[b.scope_type || "org"] || 0;
    if (rankB !== rankA) return rankB > rankA ? b : a;
    return (b.priority || 0) > (a.priority || 0) ? b : a;
  });

  return {
    tier,
    enabled: Boolean(best.enabled),
    direction: scanDirection,
    scope_type: best.scope_type || "org",
    target_mode: best.target_mode || "entire",
    key_path: (best.key_path || "").trim(),
    strict_mode: best.strict_mode || (tier === "tier2" ? "strict" : "fail_open"),
    action: best.action || "inherit",
    priority: best.priority || 0,
    control_id: best.id,
  };
}

export function resolveEffectiveControls(rows, serverId, toolName = "") {
  return {
    tier1_input: pickControl(rows, "tier1", "input", serverId, toolName),
    tier1_output: pickControl(rows, "tier1", "output", serverId, toolName),
    tier2_input: pickControl(rows, "tier2", "input", serverId, toolName),
    tier2_output: pickControl(rows, "tier2", "output", serverId, toolName),
  };
}

const EMPTY_FORM = {
  tier: "tier1",
  enabled: true,
  direction: "both",
  scope_type: "org",
  server: "",
  tool_name: "",
  target_mode: "entire",
  key_path: "",
  strict_mode: "fail_open",
  action: "inherit",
  priority: 0,
};

const ACTION_HELP = [
  `Inherit — ${ACTION.inherit.help}`,
  `Monitor — ${ACTION.monitor.help}`,
  `Redact — ${ACTION.redact.help}`,
  `Block — ${ACTION.block.help}`,
].join("\n");

const DIRECTION_LABEL = { both: "Input + Output", input: "Input", output: "Output" };

/** Color-coded action badge driven by mcpColors.actionInfo(). */
function ActionBadge({ action }) {
  const info = actionInfo(action);
  return <Badge variant={info.badge}>{info.label}</Badge>;
}

function ScopeCell({ row }) {
  const scope = row.scope_type || "org";
  return (
    <div className="flex flex-col gap-0.5">
      <span className="font-medium text-slate-700 dark:text-slate-200 capitalize">{scope}</span>
      {(row.server_slug || row.tool_name) ? (
        <span className="text-[11px] text-slate-400 dark:text-slate-500 font-mono truncate max-w-[14rem]">
          {row.server_slug || ""}
          {row.tool_name ? ` · ${row.tool_name}` : ""}
        </span>
      ) : null}
    </div>
  );
}

function TargetCell({ row }) {
  if (row.target_mode === "key_path" && row.key_path) {
    return (
      <span className="font-mono text-[11px] text-slate-600 dark:text-slate-300">{row.key_path}</span>
    );
  }
  return <span className="text-slate-400 dark:text-slate-500">Entire payload</span>;
}

/**
 * One tier table section. accent controls the header tint so it is instantly
 * clear which tier a row belongs to (Tier-1 blue, Tier-2 violet).
 */
function TierSection({ tier, title, subtitle, icon: Icon, accent, rows, onEdit, onDelete }) {
  const palette = accent === "tier1"
    ? {
        head: "bg-blue-50 dark:bg-blue-900/20 text-blue-700 dark:text-blue-300 border-blue-200 dark:border-blue-800/60",
        iconWrap: "bg-blue-100 dark:bg-blue-900/40 text-blue-600 dark:text-blue-300",
        rule: "border-l-2 border-l-blue-400/60 dark:border-l-blue-500/50",
      }
    : {
        head: "bg-violet-50 dark:bg-violet-900/20 text-violet-700 dark:text-violet-300 border-violet-200 dark:border-violet-800/60",
        iconWrap: "bg-violet-100 dark:bg-violet-900/40 text-violet-600 dark:text-violet-300",
        rule: "border-l-2 border-l-violet-400/60 dark:border-l-violet-500/50",
      };

  return (
    <Card className={cn("overflow-hidden", palette.rule)}>
      <div className={cn("flex items-start gap-3 border-b px-4 py-3", palette.head)}>
        <div className={cn("flex h-8 w-8 shrink-0 items-center justify-center rounded-lg", palette.iconWrap)}>
          <Icon className="h-4 w-4" aria-hidden="true" />
        </div>
        <div className="min-w-0">
          <div className="flex items-center gap-2">
            <h3 className="text-sm font-semibold">{title}</h3>
            <Badge variant="outline">{rows.length} {rows.length === 1 ? "row" : "rows"}</Badge>
          </div>
          <p className="mt-0.5 text-xs opacity-80">{subtitle}</p>
        </div>
      </div>

      {rows.length === 0 ? (
        <EmptyState
          className="py-8"
          icon={Icon}
          title="No custom rows"
          description={
            tier === "tier1"
              ? "Defaults apply: Tier-1 is on for every scope."
              : "Defaults apply: Tier-2 is off unless enabled by a row or the org control above."
          }
        />
      ) : (
        <Table>
          <THead>
            <TR>
              <TH className="w-14">On</TH>
              <TH>Direction</TH>
              <TH>Scope</TH>
              <TH>Target</TH>
              <TH>Strict</TH>
              <TH>
                <span className="inline-flex items-center gap-1">
                  Action
                  <InfoHint content={ACTION_HELP} />
                </span>
              </TH>
              <TH className="w-12 text-right">Pri</TH>
              <TH className="w-20 text-right">Edit</TH>
            </TR>
          </THead>
          <TBody>
            {rows.map((row) => (
              <TR key={row.id} className="hover:bg-slate-50 dark:hover:bg-slate-800/50">
                <TD>
                  {row.enabled ? (
                    <Badge variant="success">On</Badge>
                  ) : (
                    <Badge variant="secondary">Off</Badge>
                  )}
                </TD>
                <TD className="capitalize">{DIRECTION_LABEL[row.direction] || row.direction}</TD>
                <TD><ScopeCell row={row} /></TD>
                <TD><TargetCell row={row} /></TD>
                <TD>
                  {row.strict_mode === "strict" ? (
                    <Badge variant="warning">Strict</Badge>
                  ) : (
                    <Badge variant="outline">Fail open</Badge>
                  )}
                </TD>
                <TD><ActionBadge action={row.action} /></TD>
                <TD className="text-right tabular-nums text-slate-500 dark:text-slate-400">{row.priority}</TD>
                <TD className="text-right">
                  <div className="flex justify-end gap-1">
                    <Button
                      variant="ghost"
                      size="icon"
                      className="h-8 w-8"
                      aria-label={`Edit ${tier} ${row.direction} ${row.scope_type} control`}
                      onClick={() => onEdit(row)}
                    >
                      <Pencil className="h-3.5 w-3.5" />
                    </Button>
                    <Button
                      variant="ghost"
                      size="icon"
                      className="h-8 w-8 text-slate-500 hover:text-red-600 dark:hover:text-red-400"
                      aria-label={`Delete ${tier} ${row.direction} ${row.scope_type} control`}
                      onClick={() => onDelete(row.id)}
                    >
                      <Trash2 className="h-3.5 w-3.5" />
                    </Button>
                  </div>
                </TD>
              </TR>
            ))}
          </TBody>
        </Table>
      )}
    </Card>
  );
}

/** One resolved (tier, direction) line in the sticky preview sidebar. */
function PreviewRow({ label, ctrl }) {
  return (
    <div className="flex items-center justify-between gap-2 rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-800/60 px-3 py-2">
      <div className="min-w-0">
        <p className="text-xs font-medium text-slate-700 dark:text-slate-200">{label}</p>
        <p className="text-[11px] text-slate-400 dark:text-slate-500">
          {ctrl.enabled ? "Enabled" : "Disabled"}
          {" · "}
          {ctrl.control_id ? `#${ctrl.control_id}` : "default"}
          {ctrl.key_path ? ` · ${ctrl.key_path}` : ""}
        </p>
      </div>
      <ActionBadge action={ctrl.action} />
    </div>
  );
}

function EffectivePreview({ effective, scopeLabel }) {
  return (
    <Card className="lg:sticky lg:top-4">
      <div className="flex items-center gap-2 border-b border-slate-200 dark:border-slate-700 px-4 py-3">
        <Target className="h-4 w-4 text-teal-600 dark:text-teal-400" aria-hidden="true" />
        <h3 className="text-sm font-semibold text-slate-900 dark:text-slate-100">Effective preview</h3>
      </div>
      <CardContent className="space-y-3 p-4">
        <p className="text-xs text-slate-500 dark:text-slate-400">
          Resolving for <span className="font-medium text-slate-700 dark:text-slate-200">{scopeLabel}</span>
        </p>

        <div className="space-y-2">
          <p className="text-[11px] font-semibold uppercase tracking-wide text-blue-600 dark:text-blue-400">
            Tier-1 (static gate)
          </p>
          <PreviewRow label="Input" ctrl={effective.tier1_input} />
          <PreviewRow label="Output" ctrl={effective.tier1_output} />
        </div>

        <div className="space-y-2">
          <p className="text-[11px] font-semibold uppercase tracking-wide text-violet-600 dark:text-violet-400">
            Tier-2 ({ZEROSHIELD_TIER2_LABEL})
          </p>
          <PreviewRow label="Input" ctrl={effective.tier2_input} />
          <PreviewRow label="Output" ctrl={effective.tier2_output} />
        </div>

        <p className="text-[11px] leading-relaxed text-slate-400 dark:text-slate-500">
          Winner per (tier · direction): tool &gt; server &gt; org, then highest priority.
        </p>
      </CardContent>
    </Card>
  );
}

const TIER2_OPTIONS = [
  { value: "inherit", label: "Inherit", payload: null },
  { value: "enabled", label: "Enabled", payload: true },
  { value: "disabled", label: "Disabled", payload: false },
];

export function MCPScanControlMatrix({ fetchWithAuth, servers = [] }) {
  const { toast } = useToast();

  const [controls, setControls] = useState([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const [serverFilter, setServerFilter] = useState("");
  const [toolFilter, setToolFilter] = useState("");
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [editing, setEditing] = useState(null);
  const [form, setForm] = useState(EMPTY_FORM);
  const [saving, setSaving] = useState(false);
  const [mcpTier2, setMcpTier2] = useState(null);
  const [firewallConfig, setFirewallConfig] = useState(null);
  const [tier2Saving, setTier2Saving] = useState(false);

  const loadControls = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await fetchWithAuth("/api/mcp-connector/scan-controls/");
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      setControls(Array.isArray(data) ? data : []);
    } catch (e) {
      setError(e.message || "Failed to load scan controls");
    } finally {
      setLoading(false);
    }
  }, [fetchWithAuth]);

  const loadFirewall = useCallback(async () => {
    try {
      const res = await fetchWithAuth("/api/firewall/config/");
      if (!res.ok) return;
      const data = await res.json();
      setFirewallConfig(data);
      setMcpTier2(data.mcp_tier2_enabled);
    } catch {
      /* optional */
    }
  }, [fetchWithAuth]);

  useEffect(() => {
    loadControls();
    loadFirewall();
  }, [loadControls, loadFirewall]);

  const filteredControls = useMemo(() => {
    if (!serverFilter) return controls;
    return controls.filter((c) => {
      if (c.scope_type === "org") return true;
      return String(c.server_id || "") === String(serverFilter);
    });
  }, [controls, serverFilter]);

  const selectedServer = servers.find((s) => String(s.id) === String(serverFilter));

  const effective = useMemo(() => {
    if (!serverFilter) return null;
    return resolveEffectiveControls(controls, serverFilter, toolFilter);
  }, [controls, serverFilter, toolFilter]);

  const tier1Rows = useMemo(
    () => filteredControls.filter((c) => c.tier === "tier1"),
    [filteredControls]
  );
  const tier2Rows = useMemo(
    () => filteredControls.filter((c) => c.tier === "tier2"),
    [filteredControls]
  );
  const tier2Active = useMemo(
    () => controls.filter((c) => c.tier === "tier2" && c.enabled).length,
    [controls]
  );

  const openCreate = () => {
    setEditing(null);
    setForm({
      ...EMPTY_FORM,
      server: serverFilter || "",
      tool_name: toolFilter || "",
      scope_type: serverFilter ? (toolFilter ? "tool" : "server") : "org",
    });
    setDrawerOpen(true);
  };

  const openEdit = (row) => {
    setEditing(row);
    setForm({
      tier: row.tier,
      enabled: row.enabled,
      direction: row.direction,
      scope_type: row.scope_type,
      server: row.server_id || "",
      tool_name: row.tool_name || "",
      target_mode: row.target_mode,
      key_path: row.key_path || "",
      strict_mode: row.strict_mode,
      action: row.action || "inherit",
      priority: row.priority || 0,
    });
    setDrawerOpen(true);
  };

  const saveControl = async () => {
    setSaving(true);
    setError(null);
    const payload = {
      tier: form.tier,
      enabled: form.enabled,
      direction: form.direction,
      scope_type: form.scope_type,
      target_mode: form.target_mode,
      key_path: form.target_mode === "key_path" ? form.key_path : "",
      strict_mode: form.strict_mode,
      action: form.action,
      priority: Number(form.priority) || 0,
      tool_name: form.scope_type === "tool" ? form.tool_name : "",
    };
    if (form.scope_type === "server" || form.scope_type === "tool") {
      payload.server = form.server;
    }

    try {
      const url = editing
        ? `/api/mcp-connector/scan-controls/${editing.id}/`
        : "/api/mcp-connector/scan-controls/";
      const res = await fetchWithAuth(url, {
        method: editing ? "PATCH" : "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        const msg = body.detail || body.key_path?.[0] || `HTTP ${res.status}`;
        throw new Error(typeof msg === "string" ? msg : JSON.stringify(msg));
      }
      setDrawerOpen(false);
      toast(editing ? "Scan control updated" : "Scan control created", { tone: "success" });
      await loadControls();
    } catch (e) {
      setError(e.message);
      toast(e.message || "Failed to save scan control", { tone: "error" });
    } finally {
      setSaving(false);
    }
  };

  const deleteControl = async (id) => {
    if (!window.confirm("Delete this scan control row?")) return;
    setError(null);
    try {
      const res = await fetchWithAuth(`/api/mcp-connector/scan-controls/${id}/`, {
        method: "DELETE",
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      toast("Scan control deleted", { tone: "success" });
      await loadControls();
    } catch (e) {
      setError(e.message);
      toast(e.message || "Failed to delete scan control", { tone: "error" });
    }
  };

  const saveMcpTier2 = async (value) => {
    setTier2Saving(true);
    setError(null);
    try {
      const nextConfig = { ...(firewallConfig || {}), mcp_tier2_enabled: value };
      const res = await fetchWithAuth("/api/firewall/config/", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(nextConfig),
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const updated = await res.json().catch(() => nextConfig);
      setFirewallConfig(updated);
      setMcpTier2(updated.mcp_tier2_enabled);
      toast("Org Tier-2 setting saved", { tone: "success" });
    } catch (e) {
      setError(e.message);
      toast(e.message || "Failed to save Tier-2 setting", { tone: "error" });
    } finally {
      setTier2Saving(false);
    }
  };

  const tier2State = mcpTier2 === null ? "inherit" : mcpTier2 ? "enabled" : "disabled";
  const tier2StateLabel =
    mcpTier2 === null ? "Inherit (org default)" : mcpTier2 ? "Enabled" : "Disabled";
  const tier2BadgeVariant = mcpTier2 === null ? "secondary" : mcpTier2 ? "success" : "danger";

  const scopeLabel = selectedServer
    ? `${selectedServer.name}${toolFilter ? ` / ${toolFilter}` : ""}`
    : "org-wide (no server selected)";

  return (
    <div className="space-y-5">
      <PanelHeader
        icon={ShieldCheck}
        title="MCP scan controls"
        description={`Configure the Tier-1 static gate and conditional Tier-2 ${ZEROSHIELD_TIER2_LABEL} scans per scope. Precedence: tool > server > org; higher priority wins within a scope.`}
        actions={
          <>
            <Button
              variant="outline"
              size="icon"
              aria-label="Refresh scan controls"
              onClick={loadControls}
              disabled={loading}
            >
              <RefreshCw className={cn("h-4 w-4", loading && "animate-spin")} />
            </Button>
            <Button onClick={openCreate}>
              <Plus className="h-4 w-4" />
              Add control
            </Button>
          </>
        }
      />

      {error && (
        <div
          role="alert"
          className="flex items-center gap-2 rounded-lg bg-red-50 dark:bg-red-900/20 px-3 py-2.5 text-sm text-red-700 dark:text-red-300"
        >
          <AlertTriangle className="h-4 w-4 shrink-0" />
          {error}
        </div>
      )}

      {/* Tier-2 org enable — prominent card */}
      <Card>
        <CardContent className="flex flex-col gap-4 p-4 sm:flex-row sm:items-center sm:justify-between">
          <div className="flex items-start gap-3">
            <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl bg-violet-100 dark:bg-violet-900/40 text-violet-600 dark:text-violet-300">
              <Sparkles className="h-5 w-5" aria-hidden="true" />
            </div>
            <div>
              <div className="flex flex-wrap items-center gap-2">
                <h3 className="text-sm font-semibold text-slate-900 dark:text-slate-100">
                  Tier-2 ({ZEROSHIELD_TIER2_LABEL}) for this org
                </h3>
                <Badge variant={tier2BadgeVariant}>{tier2StateLabel}</Badge>
                <InfoHint content={`Tier-2 runs the ${ZEROSHIELD_TIER2_LABEL} semantic scan only after the Tier-1 static gate passes. Inherit defers to the org global Tier-2 default; Enabled/Disabled force it for MCP tool calls.`} />
              </div>
              <p className="mt-1 text-xs text-slate-500 dark:text-slate-400">
                {tier2Active} Tier-2 {tier2Active === 1 ? "row" : "rows"} active across all scopes.
              </p>
            </div>
          </div>
          <div className="flex items-center gap-2 self-start sm:self-center">
            {tier2Saving && <Spinner className="h-4 w-4 text-violet-500" />}
            <SegmentedControl
              aria-label="Org MCP Tier-2 mode"
              value={tier2State}
              onChange={(v) => {
                if (tier2Saving) return;
                const opt = TIER2_OPTIONS.find((o) => o.value === v);
                if (opt) saveMcpTier2(opt.payload);
              }}
              options={TIER2_OPTIONS.map(({ value, label }) => ({ value, label }))}
            />
          </div>
        </CardContent>
      </Card>

      {/* Scope filter */}
      <Card>
        <CardContent className="flex flex-wrap items-end gap-4 p-4">
          <label className="flex flex-col gap-1 text-sm">
            <span className="font-medium text-slate-600 dark:text-slate-300">Preview / filter server</span>
            <div className="min-w-[14rem]">
              <Select
                aria-label="Preview server"
                value={serverFilter}
                onChange={(e) => {
                  setServerFilter(e.target.value);
                  setToolFilter("");
                }}
              >
                <option value="">All (org-wide rows)</option>
                {servers.map((s) => (
                  <option key={s.id} value={s.id}>
                    {s.name}
                  </option>
                ))}
              </Select>
            </div>
          </label>
          {serverFilter && (
            <label className="flex flex-col gap-1 text-sm">
              <span className="font-medium text-slate-600 dark:text-slate-300">Preview tool (optional)</span>
              <input
                type="text"
                value={toolFilter}
                onChange={(e) => setToolFilter(e.target.value)}
                placeholder="tool name"
                aria-label="Preview tool name"
                className="min-w-[14rem] rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-800 px-3 py-2 text-sm text-slate-800 dark:text-slate-100 focus:outline-none focus:ring-2 focus:ring-teal-500"
              />
            </label>
          )}
        </CardContent>
      </Card>

      {/* Two-column: tier tables + sticky preview */}
      <div className="grid gap-5 lg:grid-cols-3">
        <div className="space-y-5 lg:col-span-2">
          {loading ? (
            <>
              <Card>
                <CardContent className="space-y-3 p-4">
                  <Skeleton className="h-5 w-48" />
                  <Skeleton className="h-4 w-full" />
                  <Skeleton className="h-4 w-full" />
                  <Skeleton className="h-4 w-3/4" />
                </CardContent>
              </Card>
              <Card>
                <CardContent className="space-y-3 p-4">
                  <Skeleton className="h-5 w-48" />
                  <Skeleton className="h-4 w-full" />
                  <Skeleton className="h-4 w-2/3" />
                </CardContent>
              </Card>
            </>
          ) : controls.length === 0 ? (
            <Card>
              <EmptyState
                icon={Shield}
                title="No scan controls yet"
                description="Defaults apply: the Tier-1 static gate is on and Tier-2 is off. Add a control to customize scanning per org, server, or tool."
                action={
                  <Button onClick={openCreate}>
                    <Plus className="h-4 w-4" />
                    Add control
                  </Button>
                }
              />
            </Card>
          ) : (
            <>
              <TierSection
                tier="tier1"
                accent="tier1"
                icon={Shield}
                title="Tier-1 — Static scanners (always-on gate)"
                subtitle="Policy + preset detectors. Runs first on every call that matches the scope."
                rows={tier1Rows}
                onEdit={openEdit}
                onDelete={deleteControl}
              />
              <TierSection
                tier="tier2"
                accent="tier2"
                icon={Sparkles}
                title={`Tier-2 — ${ZEROSHIELD_TIER2_LABEL} semantic`}
                subtitle="Runs only after Tier-1 passes, when Tier-2 is enabled for the org/scope."
                rows={tier2Rows}
                onEdit={openEdit}
                onDelete={deleteControl}
              />
            </>
          )}
        </div>

        <div className="lg:col-span-1">
          {effective ? (
            <EffectivePreview effective={effective} scopeLabel={scopeLabel} />
          ) : (
            <Card className="lg:sticky lg:top-4">
              <EmptyState
                className="py-10"
                icon={Target}
                title="Effective preview"
                description="Select a server above to see the winning Tier-1 / Tier-2 control per direction for that scope."
              />
            </Card>
          )}
        </div>
      </div>

      {/* Create / edit dialog */}
      <Dialog open={drawerOpen} onClose={() => setDrawerOpen(false)} labelledBy="scan-control-dialog-title">
        <DialogHeader
          id="scan-control-dialog-title"
          title={editing ? "Edit scan control" : "New scan control"}
          description="Define which tier, direction, scope, target, and action applies."
          onClose={() => setDrawerOpen(false)}
        />
        <DialogBody>
          {/* Tier + enabled */}
          <div className="grid gap-4 sm:grid-cols-2">
            <label className="flex flex-col gap-1.5 text-sm">
              <span className="font-medium text-slate-700 dark:text-slate-200">Tier</span>
              <Select
                aria-label="Tier"
                value={form.tier}
                onChange={(e) => setForm({ ...form, tier: e.target.value })}
              >
                <option value="tier1">Tier 1 (static gate)</option>
                <option value="tier2">Tier 2 ({ZEROSHIELD_TIER2_LABEL})</option>
              </Select>
            </label>
            <div className="flex flex-col gap-1.5 text-sm">
              <span className="font-medium text-slate-700 dark:text-slate-200">Enabled</span>
              <div className="flex h-9 items-center gap-2">
                <Switch
                  checked={form.enabled}
                  onCheckedChange={(v) => setForm({ ...form, enabled: v })}
                  label="Control enabled"
                />
                <span className="text-sm text-slate-500 dark:text-slate-400">
                  {form.enabled ? "Active" : "Inactive"}
                </span>
              </div>
            </div>
          </div>

          {/* Direction */}
          <label className="flex flex-col gap-1.5 text-sm">
            <span className="font-medium text-slate-700 dark:text-slate-200">Direction</span>
            <Select
              aria-label="Direction"
              value={form.direction}
              onChange={(e) => setForm({ ...form, direction: e.target.value })}
            >
              <option value="both">Both (input + output)</option>
              <option value="input">Input only</option>
              <option value="output">Output only</option>
            </Select>
          </label>

          {/* Scope group */}
          <div className="space-y-4 rounded-xl border border-slate-200 dark:border-slate-700 p-4">
            <p className="text-xs font-semibold uppercase tracking-wide text-slate-400 dark:text-slate-500">Scope</p>
            <label className="flex flex-col gap-1.5 text-sm">
              <span className="font-medium text-slate-700 dark:text-slate-200">Applies to</span>
              <Select
                aria-label="Scope type"
                value={form.scope_type}
                onChange={(e) => setForm({ ...form, scope_type: e.target.value })}
              >
                <option value="org">Organization (all servers)</option>
                <option value="server">Single server</option>
                <option value="tool">Single tool</option>
              </Select>
            </label>
            {(form.scope_type === "server" || form.scope_type === "tool") && (
              <label className="flex flex-col gap-1.5 text-sm">
                <span className="font-medium text-slate-700 dark:text-slate-200">Server</span>
                <Select
                  aria-label="Server"
                  value={form.server}
                  onChange={(e) => setForm({ ...form, server: e.target.value })}
                >
                  <option value="">Select server</option>
                  {servers.map((s) => (
                    <option key={s.id} value={s.id}>
                      {s.name}
                    </option>
                  ))}
                </Select>
              </label>
            )}
            {form.scope_type === "tool" && (
              <label className="flex flex-col gap-1.5 text-sm">
                <span className="font-medium text-slate-700 dark:text-slate-200">Tool name</span>
                <input
                  value={form.tool_name}
                  onChange={(e) => setForm({ ...form, tool_name: e.target.value })}
                  placeholder="e.g. send_email"
                  aria-label="Tool name"
                  className="rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-800 px-3 py-2 text-sm text-slate-800 dark:text-slate-100 focus:outline-none focus:ring-2 focus:ring-teal-500"
                />
              </label>
            )}
          </div>

          {/* Target group — smart field */}
          <div className="space-y-3 rounded-xl border border-slate-200 dark:border-slate-700 p-4">
            <div className="flex items-center justify-between gap-2">
              <p className="text-xs font-semibold uppercase tracking-wide text-slate-400 dark:text-slate-500">Target</p>
              <SegmentedControl
                aria-label="Target mode"
                value={form.target_mode}
                onChange={(v) => setForm({ ...form, target_mode: v })}
                options={[
                  { value: "entire", label: "Entire payload" },
                  { value: "key_path", label: "Specific key" },
                ]}
              />
            </div>
            {form.target_mode === "key_path" ? (
              <label className="flex flex-col gap-1.5 text-sm">
                <span className="font-medium text-slate-700 dark:text-slate-200">Key path</span>
                <input
                  value={form.key_path}
                  onChange={(e) => setForm({ ...form, key_path: e.target.value })}
                  placeholder="arguments.email or ssn"
                  aria-label="Key path"
                  className="rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-800 px-3 py-2 font-mono text-sm text-slate-800 dark:text-slate-100 focus:outline-none focus:ring-2 focus:ring-teal-500"
                />
              </label>
            ) : (
              <p className="text-xs text-slate-500 dark:text-slate-400">
                Scans the entire request/response payload.
              </p>
            )}
          </div>

          {/* Action + strict + priority */}
          <div className="grid gap-4 sm:grid-cols-2">
            <label className="flex flex-col gap-1.5 text-sm">
              <span className="inline-flex items-center gap-1 font-medium text-slate-700 dark:text-slate-200">
                Action
                <InfoHint content={ACTION_HELP} />
              </span>
              <Select
                aria-label="Action"
                value={form.action}
                onChange={(e) => setForm({ ...form, action: e.target.value })}
              >
                <option value="inherit">Inherit (server default)</option>
                <option value="monitor">Monitor (detect, allow)</option>
                <option value="redact">Redact</option>
                <option value="block">Block</option>
              </Select>
            </label>
            <label className="flex flex-col gap-1.5 text-sm">
              <span className="font-medium text-slate-700 dark:text-slate-200">Strict mode</span>
              <Select
                aria-label="Strict mode"
                value={form.strict_mode}
                onChange={(e) => setForm({ ...form, strict_mode: e.target.value })}
              >
                <option value="fail_open">Fail open (allow on scanner error)</option>
                <option value="strict">Strict (block on scanner error)</option>
              </Select>
            </label>
          </div>

          <label className="flex flex-col gap-1.5 text-sm">
            <span className="font-medium text-slate-700 dark:text-slate-200">Priority</span>
            <input
              type="number"
              value={form.priority}
              onChange={(e) => setForm({ ...form, priority: e.target.value })}
              aria-label="Priority"
              className="w-32 rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-800 px-3 py-2 text-sm text-slate-800 dark:text-slate-100 focus:outline-none focus:ring-2 focus:ring-teal-500"
            />
            <span className="text-xs text-slate-400 dark:text-slate-500">
              Higher wins among rows with the same scope.
            </span>
          </label>
        </DialogBody>
        <DialogFooter>
          <Button variant="outline" onClick={() => setDrawerOpen(false)} disabled={saving}>
            Cancel
          </Button>
          <Button onClick={saveControl} disabled={saving}>
            {saving && <Spinner className="h-4 w-4" />}
            {editing ? "Save changes" : "Create control"}
          </Button>
        </DialogFooter>
      </Dialog>
    </div>
  );
}
