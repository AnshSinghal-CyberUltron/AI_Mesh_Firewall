import { useCallback, useEffect, useState } from "react";
import { Bar, BarChart, CartesianGrid, PolarAngleAxis, PolarGrid, Radar, RadarChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { Loader2 } from "lucide-react";
import { useAuth } from "../../context/AuthContext";
import { createModule2Api } from "../../api/module2";
import { PageHeader } from "../../components/module2/PageHeader";
import { ChartCard } from "../../components/module2/ChartCard";
import { module2TooltipProps } from "../../components/module2/module2Chart";
import { PeriodSelector } from "../../components/module2/PeriodSelector";

export function ThreatAnalyticsPage() {
  const { fetchWithAuth } = useAuth();
  const api = createModule2Api(fetchWithAuth);
  const [period, setPeriod] = useState("24h");
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      setData(await api.getThreatAnalytics(period));
    } finally {
      setLoading(false);
    }
  }, [period]);

  useEffect(() => { load(); }, [load]);

  if (loading) return <div className="flex justify-center py-20"><Loader2 className="h-8 w-8 animate-spin text-teal-500" /></div>;

  const radarData = (data?.owasp_stats || []).map((s) => ({
    code: s.code,
    detected: s.detected,
    blocked: s.blocked,
  }));

  return (
    <div>
      <PageHeader
        title="Threat Analytics"
        subtitle="Deep-dive threat breakdown by type, agent, and model"
        actions={<PeriodSelector value={period} onChange={setPeriod} />}
      />

      <div className="grid gap-4 lg:grid-cols-2">
        <ChartCard title="Threat Breakdown by Type">
          <ResponsiveContainer width="100%" height={280}>
            <BarChart data={data?.by_threat_type?.slice(0, 12) || []}>
              <CartesianGrid strokeDasharray="3 3" opacity={0.3} />
              <XAxis dataKey="type" fontSize={10} angle={-20} textAnchor="end" height={60} />
              <YAxis fontSize={11} />
              <Tooltip {...module2TooltipProps} />
              <Bar dataKey="count" fill="#14b8a6" radius={[4, 4, 0, 0]} />
            </BarChart>
          </ResponsiveContainer>
        </ChartCard>

        <ChartCard title="OWASP Radar">
          <ResponsiveContainer width="100%" height={280}>
            <RadarChart data={radarData}>
              <PolarGrid />
              <PolarAngleAxis dataKey="code" fontSize={9} />
              <Radar name="Detected" dataKey="detected" stroke="#8b5cf6" fill="#8b5cf6" fillOpacity={0.4} />
              <Radar name="Blocked" dataKey="blocked" stroke="#ef4444" fill="#ef4444" fillOpacity={0.3} />
              <Tooltip {...module2TooltipProps} />
            </RadarChart>
          </ResponsiveContainer>
        </ChartCard>
      </div>

      <div className="mt-4 grid gap-4 lg:grid-cols-2">
        <ChartCard title="By Model">
          <ResponsiveContainer width="100%" height={220}>
            <BarChart data={data?.by_model?.slice(0, 8) || []} layout="vertical">
              <CartesianGrid strokeDasharray="3 3" opacity={0.3} />
              <XAxis type="number" fontSize={11} />
              <YAxis type="category" dataKey="model" fontSize={10} width={100} />
              <Tooltip {...module2TooltipProps} />
              <Bar dataKey="count" fill="#0ea5e9" />
            </BarChart>
          </ResponsiveContainer>
        </ChartCard>
        <ChartCard title="Attack Graph Summary">
          <div className="space-y-2 text-sm">
            <p className="text-slate-500">Total events: <strong>{data?.total ?? 0}</strong></p>
            {(data?.owasp_stats || []).slice(0, 8).map((s) => (
              <div key={s.code} className="flex justify-between rounded-lg bg-slate-50 px-3 py-2 dark:bg-slate-700/40">
                <span>{s.code}</span>
                <span>{s.detected} detected / {s.blocked} blocked ({s.coverage}%)</span>
              </div>
            ))}
          </div>
        </ChartCard>
      </div>
    </div>
  );
}
