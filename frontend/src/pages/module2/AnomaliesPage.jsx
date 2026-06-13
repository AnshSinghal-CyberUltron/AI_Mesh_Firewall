import { useCallback, useEffect, useState } from "react";
import { Line, LineChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis, ReferenceLine } from "recharts";
import { Loader2, Plus } from "lucide-react";
import { useAuth } from "../../context/AuthContext";
import { createModule2Api } from "../../api/module2";
import { PageHeader } from "../../components/module2/PageHeader";
import { ChartCard } from "../../components/module2/ChartCard";
import { DataTable } from "../../components/module2/DataTable";

export function AnomaliesPage() {
  const { fetchWithAuth } = useAuth();
  const api = createModule2Api(fetchWithAuth);
  const [rules, setRules] = useState([]);
  const [selectedRule, setSelectedRule] = useState(null);
  const [baseline, setBaseline] = useState(null);
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const data = await api.listAnomalyRules();
      const list = data.results || data;
      setRules(list);
      if (list.length && !selectedRule) {
        setSelectedRule(list[0].id);
      }
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  useEffect(() => {
    if (!selectedRule) return;
    (async () => {
      try {
        setBaseline(await api.getAnomalyBaseline(selectedRule));
      } catch (e) {
        setBaseline(null);
      }
    })();
  }, [selectedRule]);

  const toggleRule = async (rule) => {
    await api.updateAnomalyRule(rule.id, { enabled: !rule.enabled });
    load();
  };

  if (loading) return <div className="flex justify-center py-20"><Loader2 className="h-8 w-8 animate-spin text-teal-500" /></div>;

  return (
    <div>
      <PageHeader title="Anomaly Detection" subtitle="Statistical baselines and auto-response" />
      <DataTable
        columns={[
          { key: "name", label: "Name", render: (r) => r.name || `${r.scope}:${r.scope_id || "*"}` },
          { key: "scope", label: "Scope" },
          { key: "z_score_threshold", label: "Z Threshold" },
          { key: "enabled", label: "Auto-Response", render: (r) => (
            <button onClick={() => toggleRule(r)} className={`text-xs ${r.enabled ? "text-green-600" : "text-slate-400"}`}>
              {r.enabled ? "Armed" : "Disarmed"}
            </button>
          )},
          { key: "view", label: "", render: (r) => (
            <button onClick={() => setSelectedRule(r.id)} className="text-teal-600 text-xs hover:underline">Chart</button>
          )},
        ]}
        rows={rules}
      />

      {baseline && (
        <ChartCard title="Baseline Chart" className="mt-6">
          <div className="mb-3 flex gap-4 text-sm">
            <span>Mean: <strong>{baseline.mean_rate}</strong></span>
            <span>Current Z: <strong className={baseline.z_score >= baseline.threshold ? "text-red-600" : ""}>{baseline.z_score}</strong></span>
            <span>Threshold: <strong>{baseline.threshold}</strong></span>
          </div>
          <ResponsiveContainer width="100%" height={240}>
            <LineChart data={baseline.baseline || []}>
              <CartesianGrid strokeDasharray="3 3" opacity={0.3} />
              <XAxis dataKey="timestamp" tickFormatter={(v) => v?.slice(5, 10)} fontSize={10} />
              <YAxis fontSize={11} />
              <Tooltip />
              <ReferenceLine y={baseline.mean_rate} stroke="#94a3b8" strokeDasharray="3 3" />
              <Line type="monotone" dataKey="event_rate" stroke="#14b8a6" dot={false} />
            </LineChart>
          </ResponsiveContainer>
        </ChartCard>
      )}
    </div>
  );
}
