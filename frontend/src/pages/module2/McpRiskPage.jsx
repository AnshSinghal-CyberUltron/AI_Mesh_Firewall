import { useCallback, useEffect, useMemo, useState } from "react";
import { Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { RefreshCw, ArrowDown, ArrowUp } from "lucide-react";
import { useAuth } from "../../context/AuthContext";
import { createModule2Api } from "../../api/module2";
import { PageHeader } from "../../components/module2/PageHeader";
import { KPIBar } from "../../components/module2/KPIBar";
import { ChartCard } from "../../components/module2/ChartCard";
import { DataTable } from "../../components/module2/DataTable";
import { PeriodSelector } from "../../components/module2/PeriodSelector";
import { Module2ErrorState, Module2PageSkeleton } from "../../components/module2/PageStates";
import { ContextualAppBar } from "../../components/module2/ContextualAppBar";
import { ANALYST_BRIEF_TITLE, PAGE_BRIEFS } from "./pageCopy";

export function McpRiskPage() {
  const { fetchWithAuth } = useAuth();
  const api = useMemo(() => createModule2Api(fetchWithAuth), [fetchWithAuth]);
  const [period, setPeriod] = useState("24h");
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setData(await api.getMcpRisk(period));
    } catch (e) {
      setError(e.message || "Failed to load MCP risk data.");
      setData(null);
    } finally {
      setLoading(false);
    }
  }, [api, period]);

  useEffect(() => { load(); }, [load]);

  const summary = data?.summary || {};
  const kpiItems = [
    { key: "mcp-events", label: "Total MCP Events", value: summary.total_events ?? 0, helpText: "All MCP tool-call events scanned by Module 1.4 guardrails." },
    { key: "blocked-tools", label: "Blocked Tool Calls", value: summary.blocked_tool_calls ?? 0, color: "text-red-600", helpText: "Tool invocations denied—often SQL injection, path traversal, or forbidden args." },
    { key: "redacted-args", label: "Redacted Arguments", value: summary.redacted_arguments ?? 0, color: "text-amber-600", helpText: "Calls allowed after argument-level PII or secret redaction." },
    { key: "unique-tools", label: "Unique Tools", value: summary.unique_tools ?? 0, color: "text-violet-600", helpText: "Distinct tool names invoked—breadth may indicate agent sprawl." },
  ];

  const toolLedger = (data?.tool_ledger || []).map((t) => ({
    ...t,
    name: t.tool,
  }));

  const dirSplit = data?.direction_split || { inbound: { total: 0, blocked: 0 }, outbound: { total: 0, blocked: 0 } };

  return (
    <div>
      <ContextualAppBar title={ANALYST_BRIEF_TITLE} description={PAGE_BRIEFS.mcp} />
      <PageHeader
        title="MCP & Context Risk"
        subtitle="Tool execution ledger, argument violations, and directional scan results for Model Context Protocol traffic"
        actions={
          <>
            <PeriodSelector value={period} onChange={setPeriod} />
            <button
              type="button"
              onClick={load}
              className="rounded-lg border border-slate-200 p-2 dark:border-slate-600"
              aria-label="Refresh"
            >
              <RefreshCw className="h-4 w-4" />
            </button>
          </>
        }
      />

      {error && !data && <Module2ErrorState message={error} onRetry={load} />}
      {loading && !data && !error && <Module2PageSkeleton />}

      {data && (
        <>
          <KPIBar items={kpiItems} />

          <div className="mt-6 grid gap-4 lg:grid-cols-2">
            <ChartCard
              title="Tool Execution Ledger — Top 10 by Violations"
              titleHelpText="Tools ranked by policy violations—prioritize hardening or deny-listing the top entries."
            >
              {toolLedger.length === 0 ? (
                <p className="py-8 text-center text-sm text-slate-400">No MCP tool call events in this period</p>
              ) : (
                <ResponsiveContainer width="100%" height={260}>
                  <BarChart data={toolLedger} layout="vertical" margin={{ left: 10, right: 10 }}>
                    <CartesianGrid strokeDasharray="3 3" opacity={0.25} />
                    <XAxis type="number" fontSize={11} />
                    <YAxis dataKey="tool" type="category" fontSize={10} width={130} />
                    <Tooltip />
                    <Bar dataKey="violations" fill="#f59e0b" radius={[0, 4, 4, 0]} name="Violations" />
                  </BarChart>
                </ResponsiveContainer>
              )}
            </ChartCard>

            <ChartCard
              title="Directional Scan Splitter"
              titleHelpText="Inbound = argument scan failures on tool calls. Outbound = context drops on tool responses."
            >
              <div className="mt-2 grid grid-cols-2 gap-4">
                {["inbound", "outbound"].map((dir) => {
                  const stats = dirSplit[dir] || { total: 0, blocked: 0 };
                  const rate = stats.total ? Math.round((stats.blocked / stats.total) * 100) : 0;
                  const Icon = dir === "inbound" ? ArrowDown : ArrowUp;
                  const color = dir === "inbound" ? "text-sky-500" : "text-emerald-500";
                  const bg = dir === "inbound" ? "bg-sky-50 dark:bg-sky-900/20 border-sky-200 dark:border-sky-700" : "bg-emerald-50 dark:bg-emerald-900/20 border-emerald-200 dark:border-emerald-700";
                  return (
                    <div key={dir} className={`rounded-xl border p-4 ${bg}`}>
                      <div className="flex items-center gap-2 mb-3">
                        <Icon className={`h-5 w-5 ${color}`} />
                        <span className="text-sm font-semibold capitalize text-slate-700 dark:text-slate-200">{dir}</span>
                      </div>
                      <p className="text-2xl font-bold text-slate-900 dark:text-white">{stats.total.toLocaleString()}</p>
                      <p className="text-xs text-slate-500 mt-1">
                        <span className="text-red-500 font-medium">{stats.blocked}</span> violations
                        {" · "}<span className={`${color} font-medium`}>{rate}%</span> rate
                      </p>
                    </div>
                  );
                })}
              </div>
              <p className="mt-4 text-xs text-slate-400">
                Inbound = payload violations on tool calls entering the context.
                Outbound = context minimization drops on tool responses.
              </p>
            </ChartCard>
          </div>

          <div className="mt-6">
            <ChartCard
              title="Top MCP Servers by Violation Count"
              titleHelpText="MCP server endpoints ranked by violation volume—useful for connector-level remediation."
            >
              <DataTable
                columns={[
                  { key: "server", label: "Server / Slug", helpText: "Registered MCP server or connector slug." },
                  { key: "total", label: "Total Events", helpText: "Enforcement events attributed to this server." },
                ]}
                rows={data?.top_servers || []}
                emptyMessage="No MCP server activity in this period."
              />
            </ChartCard>
          </div>
        </>
      )}
    </div>
  );
}
