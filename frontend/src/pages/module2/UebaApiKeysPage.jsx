import { useCallback, useEffect, useMemo, useState } from "react";
import { Bar, BarChart, CartesianGrid, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { Loader2, RefreshCw } from "lucide-react";
import { useAuth } from "../../context/AuthContext";
import { createModule2Api } from "../../api/module2";
import { PageHeader } from "../../components/module2/PageHeader";
import { KPIBar } from "../../components/module2/KPIBar";
import { ChartCard } from "../../components/module2/ChartCard";
import { DataTable } from "../../components/module2/DataTable";
import { PeriodSelector } from "../../components/module2/PeriodSelector";
import { ContextualAppBar } from "../../components/module2/ContextualAppBar";
import { ANALYST_BRIEF_TITLE, PAGE_BRIEFS } from "./pageCopy";

export function UebaApiKeysPage() {
  const { fetchWithAuth } = useAuth();
  const api = useMemo(() => createModule2Api(fetchWithAuth), [fetchWithAuth]);
  const [period, setPeriod] = useState("24h");
  const [summary, setSummary] = useState(null);
  const [timeline, setTimeline] = useState(null);
  const [registry, setRegistry] = useState(null);
  const [selectedKey, setSelectedKey] = useState(null);
  const [behavior, setBehavior] = useState(null);
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [sum, tl, reg] = await Promise.all([
        api.getUebaSummary(period),
        api.getUebaTimeline(period),
        api.getUebaRegistry(),
      ]);
      setSummary(sum);
      setTimeline(tl);
      setRegistry(reg);
      if (!selectedKey && sum?.top_risky_keys?.length) {
        setSelectedKey(sum.top_risky_keys[0].key_id);
      }
    } finally {
      setLoading(false);
    }
  }, [api, period, selectedKey]);

  useEffect(() => {
    load();
  }, [load]);

  useEffect(() => {
    if (!selectedKey) return;
    (async () => {
      try {
        const detail = await api.getUebaBehavior(selectedKey, "7d");
        setBehavior(detail);
      } catch (_err) {
        setBehavior(null);
      }
    })();
  }, [api, selectedKey]);

  if (loading && !summary) {
    return (
      <div>
        <ContextualAppBar title={ANALYST_BRIEF_TITLE} description={PAGE_BRIEFS.ueba} />
        <div className="flex justify-center py-20">
          <Loader2 className="h-8 w-8 animate-spin text-teal-500" />
        </div>
      </div>
    );
  }

  const s = summary?.summary || {};
  const kpiItems = [
    { key: "total-keys", label: "Total Keys", value: s.total_keys ?? 0, helpText: "All API keys provisioned for this organization." },
    { key: "active-keys", label: "Active Keys", value: s.active_keys ?? 0, color: "text-teal-600", helpText: "Keys currently enabled and able to pass ingress auth." },
    { key: "keys-with-activity", label: "Keys With Activity", value: s.keys_with_activity ?? 0, helpText: "Keys with at least one enforcement event in this window." },
    { key: "high-risk-keys", label: "High Risk Keys", value: s.high_risk_keys ?? 0, color: "text-red-600", helpText: "Keys exceeding UEBA high-risk thresholds—investigate first." },
  ];

  return (
    <div>
      <ContextualAppBar title={ANALYST_BRIEF_TITLE} description={PAGE_BRIEFS.ueba} />
      <PageHeader
        title="API Key Behavior Analytics (UEBA)"
        subtitle="Behavior baselining, anomaly flags, and risk-scored key registry"
        actions={
          <>
            <PeriodSelector value={period} onChange={setPeriod} />
            <button onClick={load} className="rounded-lg border border-slate-200 p-2 dark:border-slate-600">
              <RefreshCw className="h-4 w-4" />
            </button>
          </>
        }
      />

      <KPIBar items={kpiItems} />

      <div className="mt-6 grid gap-4 lg:grid-cols-2">
        <ChartCard
          title="Behavior Timeline (Top Risk Keys)"
          titleHelpText="Hourly event trend for top-risk keys—watch for simultaneous block and velocity spikes."
        >
          <ResponsiveContainer width="100%" height={260}>
            <LineChart data={timeline?.timeline || []}>
              <CartesianGrid strokeDasharray="3 3" opacity={0.25} />
              <XAxis dataKey="timestamp" tickFormatter={(v) => v?.slice(11, 16)} fontSize={10} />
              <YAxis fontSize={11} />
              <Tooltip />
              <Line type="monotone" dataKey="total_events" stroke="#0ea5e9" strokeWidth={2} dot={false} />
              <Line type="monotone" dataKey="blocked" stroke="#ef4444" strokeWidth={2} dot={false} />
              <Line type="monotone" dataKey="redacted" stroke="#f59e0b" strokeWidth={2} dot={false} />
            </LineChart>
          </ResponsiveContainer>
        </ChartCard>

        <ChartCard
          title="Top Risky Keys"
          titleHelpText="Click a key prefix to load its drilldown profile and access distribution."
        >
          <DataTable
            columns={[
              {
                key: "prefix",
                label: "Key",
                render: (r) => (
                  <button
                    onClick={() => setSelectedKey(r.key_id)}
                    className={`font-mono text-xs ${selectedKey === r.key_id ? "text-teal-600" : "text-slate-700 dark:text-slate-300"}`}
                  >
                    {r.prefix}
                  </button>
                ),
              },
              { key: "project_id", label: "Project", helpText: "Owning app or project for this credential." },
              { key: "request_count", label: "Requests", helpText: "Enforcement events attributed to this key." },
              { key: "risk_band", label: "Risk", helpText: "Current UEBA band: low, medium, or high." },
              { key: "velocity_spike", label: "Velocity x", helpText: "Burst factor vs. baseline; values above 3x warrant review." },
            ]}
            rows={summary?.top_risky_keys || []}
            emptyMessage="No key activity in selected period"
          />
        </ChartCard>
      </div>

      <div className="mt-6 grid gap-4 lg:grid-cols-2">
        <ChartCard
          title="Selected Key Drilldown"
          titleHelpText="Per-key profile: block/redact rates, threat types, and which collections or MCP tools were touched."
        >
          {!behavior ? (
            <p className="py-8 text-center text-sm text-slate-400">Select a key from Top Risky Keys</p>
          ) : (
            <div className="space-y-3 text-sm">
              <div className="grid grid-cols-2 gap-3">
                <div className="rounded-lg bg-slate-50 px-3 py-2 dark:bg-slate-700/40">
                  <p className="text-xs text-slate-400">Key</p>
                  <p className="font-mono">{behavior.prefix}</p>
                </div>
                <div className="rounded-lg bg-slate-50 px-3 py-2 dark:bg-slate-700/40">
                  <p className="text-xs text-slate-400">Risk</p>
                  <p className="font-semibold">{behavior.risk_band} ({behavior.risk_score})</p>
                </div>
              </div>
              <div className="grid grid-cols-3 gap-3">
                <div className="rounded-lg bg-slate-50 px-3 py-2 dark:bg-slate-700/40"><p className="text-xs text-slate-400">Requests</p><p>{behavior.request_count}</p></div>
                <div className="rounded-lg bg-slate-50 px-3 py-2 dark:bg-slate-700/40"><p className="text-xs text-slate-400">Block %</p><p>{behavior.block_rate_pct}%</p></div>
                <div className="rounded-lg bg-slate-50 px-3 py-2 dark:bg-slate-700/40"><p className="text-xs text-slate-400">Redact %</p><p>{behavior.redact_rate_pct}%</p></div>
              </div>
              <p><strong>Anomaly Flags:</strong> {(behavior.anomaly_flags || []).join(", ") || "None"}</p>
              <p><strong>Top Threats:</strong> {(behavior.top_threat_types || []).map(([t, c]) => `${t} (${c})`).join(", ") || "—"}</p>

              {/* Access Distribution — vector collections & MCP tools */}
              <details className="mt-3 rounded-lg border border-slate-200 dark:border-slate-600">
                <summary className="cursor-pointer px-3 py-2 text-xs font-semibold text-slate-600 dark:text-slate-300 select-none">
                  Access Distribution Profile
                </summary>
                <div className="grid grid-cols-2 gap-3 p-3 pt-2">
                  <div>
                    <p className="mb-1 text-xs font-semibold text-slate-500 uppercase">Top Collections</p>
                    {(behavior.top_collections || []).length === 0 ? (
                      <p className="text-xs text-slate-400">No vector access</p>
                    ) : (
                      <ul className="space-y-1">
                        {behavior.top_collections.map(([coll, count], i) => (
                          <li key={i} className="flex justify-between text-xs">
                            <span className="font-mono truncate max-w-[100px]">{coll}</span>
                            <span className="text-slate-400">{count}</span>
                          </li>
                        ))}
                      </ul>
                    )}
                  </div>
                  <div>
                    <p className="mb-1 text-xs font-semibold text-slate-500 uppercase">Top MCP Tools</p>
                    {(behavior.top_mcp_tools || []).length === 0 ? (
                      <p className="text-xs text-slate-400">No MCP invocations</p>
                    ) : (
                      <ul className="space-y-1">
                        {behavior.top_mcp_tools.map(([tool, count], i) => (
                          <li key={i} className="flex justify-between text-xs">
                            <span className="font-mono truncate max-w-[100px]">{tool}</span>
                            <span className="text-slate-400">{count}</span>
                          </li>
                        ))}
                      </ul>
                    )}
                  </div>
                </div>
              </details>
            </div>
          )}
        </ChartCard>

        <ChartCard
          title="API Key Health Registry"
          titleHelpText="Fleet registry of key status, rate limits, and last-seen activity."
        >
          <DataTable
            columns={[
              { key: "prefix", label: "Prefix", helpText: "Truncated key ID—safe to display in logs and tickets." },
              { key: "name", label: "Name" },
              { key: "project_id", label: "App/Project" },
              { key: "is_active", label: "Status", render: (r) => (r.is_active ? "Active" : "Disabled") },
              { key: "rate_limit_tpm", label: "TPM" },
              { key: "last_used_at", label: "Last Used", render: (r) => (r.last_used_at ? new Date(r.last_used_at).toLocaleString() : "Never") },
            ]}
            rows={registry?.results || []}
          />
        </ChartCard>
      </div>
    </div>
  );
}
