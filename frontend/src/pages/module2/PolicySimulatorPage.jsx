import { useEffect, useState } from "react";
import { Loader2, Play } from "lucide-react";
import { useAuth } from "../../context/AuthContext";
import { createModule2Api } from "../../api/module2";
import { PageHeader } from "../../components/module2/PageHeader";
import { DataTable } from "../../components/module2/DataTable";

export function PolicySimulatorPage() {
  const { fetchWithAuth } = useAuth();
  const api = createModule2Api(fetchWithAuth);
  const [policies, setPolicies] = useState([]);
  const [selectedPolicy, setSelectedPolicy] = useState(null);
  const [results, setResults] = useState([]);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    (async () => {
      const res = await fetchWithAuth("/api/policies/");
      const data = await res.json();
      const list = data.results || data;
      setPolicies(list);
      if (list.length) setSelectedPolicy(list[0].id);
    })();
  }, []);

  const runReplay = async () => {
    if (!selectedPolicy) return;
    setLoading(true);
    try {
      const data = await api.replayPolicy(selectedPolicy);
      setResults(data.results || []);
    } finally {
      setLoading(false);
    }
  };

  return (
    <div>
      <PageHeader
        title="Policy Simulator"
        subtitle="Replay historical enforcement events through a draft policy"
        actions={
          <button onClick={runReplay} disabled={loading} className="flex items-center gap-1 rounded-lg bg-teal-600 px-4 py-2 text-sm text-white disabled:opacity-50">
            {loading ? <Loader2 className="h-4 w-4 animate-spin" /> : <Play className="h-4 w-4" />}
            Replay Last 20 Events
          </button>
        }
      />
      <select
        value={selectedPolicy || ""}
        onChange={(e) => setSelectedPolicy(Number(e.target.value))}
        className="mb-4 rounded-lg border border-slate-200 px-3 py-2 text-sm dark:border-slate-600 dark:bg-slate-800"
      >
        {policies.map((p) => <option key={p.id} value={p.id}>{p.code} — {p.name}</option>)}
      </select>
      <DataTable
        columns={[
          { key: "event_id", label: "Event" },
          { key: "original_action", label: "Original" },
          { key: "simulated_action", label: "Simulated" },
          { key: "matched_rule", label: "Matched Rule" },
          { key: "prompt_snippet", label: "Prompt" },
        ]}
        rows={results}
        emptyMessage="Click Replay to simulate historical events"
      />
    </div>
  );
}
