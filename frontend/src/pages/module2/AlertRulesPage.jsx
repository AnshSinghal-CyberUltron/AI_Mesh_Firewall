import { useCallback, useEffect, useState } from "react";
import { Loader2, Plus } from "lucide-react";
import { useAuth } from "../../context/AuthContext";
import { createModule2Api } from "../../api/module2";
import { PageHeader } from "../../components/module2/PageHeader";
import { DataTable } from "../../components/module2/DataTable";

const METRICS = ["block_rate", "pii_rate", "tier2_score", "incident_count"];
const OPERATORS = ["gt", "lt", "gte", "lte"];
const SEVERITIES = ["low", "medium", "high", "critical"];

export function AlertRulesPage() {
  const { fetchWithAuth } = useAuth();
  const api = createModule2Api(fetchWithAuth);
  const [rules, setRules] = useState([]);
  const [loading, setLoading] = useState(true);
  const [showForm, setShowForm] = useState(false);
  const [form, setForm] = useState({ name: "", metric: "block_rate", operator: "gt", threshold: 10, severity: "medium", window_seconds: 300 });

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const data = await api.listAlertRules();
      setRules(data.results || data);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  const handleCreate = async () => {
    await api.createAlertRule(form);
    setShowForm(false);
    setForm({ name: "", metric: "block_rate", operator: "gt", threshold: 10, severity: "medium", window_seconds: 300 });
    load();
  };

  const toggleEnabled = async (rule) => {
    await api.updateAlertRule(rule.id, { enabled: !rule.enabled });
    load();
  };

  if (loading) return <div className="flex justify-center py-20"><Loader2 className="h-8 w-8 animate-spin text-teal-500" /></div>;

  return (
    <div>
      <PageHeader
        title="Alert Rules"
        subtitle="Threshold-based alert engine"
        actions={
          <button onClick={() => setShowForm(true)} className="flex items-center gap-1 rounded-lg bg-teal-600 px-4 py-2 text-sm text-white">
            <Plus className="h-4 w-4" /> New Rule
          </button>
        }
      />

      {showForm && (
        <div className="mb-4 rounded-xl border border-slate-200 bg-white p-4 dark:border-slate-700 dark:bg-slate-800/60">
          <div className="grid gap-3 sm:grid-cols-3">
            <input placeholder="Name" value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} className="rounded-lg border px-3 py-2 text-sm dark:border-slate-600 dark:bg-slate-800" />
            <select value={form.metric} onChange={(e) => setForm({ ...form, metric: e.target.value })} className="rounded-lg border px-3 py-2 text-sm dark:border-slate-600 dark:bg-slate-800">
              {METRICS.map((m) => <option key={m} value={m}>{m}</option>)}
            </select>
            <select value={form.operator} onChange={(e) => setForm({ ...form, operator: e.target.value })} className="rounded-lg border px-3 py-2 text-sm dark:border-slate-600 dark:bg-slate-800">
              {OPERATORS.map((o) => <option key={o} value={o}>{o}</option>)}
            </select>
            <input type="number" placeholder="Threshold" value={form.threshold} onChange={(e) => setForm({ ...form, threshold: Number(e.target.value) })} className="rounded-lg border px-3 py-2 text-sm dark:border-slate-600 dark:bg-slate-800" />
            <select value={form.severity} onChange={(e) => setForm({ ...form, severity: e.target.value })} className="rounded-lg border px-3 py-2 text-sm dark:border-slate-600 dark:bg-slate-800">
              {SEVERITIES.map((s) => <option key={s} value={s}>{s}</option>)}
            </select>
          </div>
          <div className="mt-3 flex gap-2">
            <button onClick={handleCreate} className="rounded-lg bg-teal-600 px-4 py-2 text-sm text-white">Save</button>
            <button onClick={() => setShowForm(false)} className="rounded-lg border px-4 py-2 text-sm">Cancel</button>
          </div>
        </div>
      )}

      <DataTable
        columns={[
          { key: "name", label: "Name" },
          { key: "metric", label: "Metric" },
          { key: "operator", label: "Op" },
          { key: "threshold", label: "Threshold" },
          { key: "severity", label: "Severity", render: (r) => (
            <span className={`rounded px-2 py-0.5 text-xs ${r.severity === "critical" ? "bg-red-100 text-red-700" : "bg-slate-100 text-slate-600"}`}>{r.severity}</span>
          )},
          { key: "enabled", label: "Status", render: (r) => (
            <button onClick={() => toggleEnabled(r)} className={`text-xs ${r.enabled ? "text-green-600" : "text-slate-400"}`}>
              {r.enabled ? "Enabled" : "Disabled"}
            </button>
          )},
        ]}
        rows={rules}
      />
    </div>
  );
}
