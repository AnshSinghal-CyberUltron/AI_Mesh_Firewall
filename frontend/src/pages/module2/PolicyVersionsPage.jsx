import { useEffect, useState } from "react";
import { Loader2 } from "lucide-react";
import { useAuth } from "../../context/AuthContext";
import { createModule2Api } from "../../api/module2";
import { PageHeader } from "../../components/module2/PageHeader";
import { ChartCard } from "../../components/module2/ChartCard";
import { DataTable } from "../../components/module2/DataTable";

export function PolicyVersionsPage() {
  const { fetchWithAuth } = useAuth();
  const api = createModule2Api(fetchWithAuth);
  const [policies, setPolicies] = useState([]);
  const [selectedPolicy, setSelectedPolicy] = useState(null);
  const [versions, setVersions] = useState([]);
  const [diff, setDiff] = useState(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    (async () => {
      const res = await fetchWithAuth("/api/policies/");
      const data = await res.json();
      const list = data.results || data;
      setPolicies(list);
      if (list.length) setSelectedPolicy(list[0].id);
      setLoading(false);
    })();
  }, []);

  useEffect(() => {
    if (!selectedPolicy) return;
    (async () => {
      const res = await fetchWithAuth(`/api/policies/${selectedPolicy}/versions/`);
      const data = await res.json();
      setVersions(data.results || data);
      setDiff(null);
    })();
  }, [selectedPolicy]);

  const viewDiff = async (versionId) => {
    setDiff(await api.getPolicyDiff(versionId));
  };

  if (loading) return <div className="flex justify-center py-20"><Loader2 className="h-8 w-8 animate-spin text-teal-500" /></div>;

  return (
    <div>
      <PageHeader title="Policy Versions" subtitle="Version history and side-by-side diff" />
      <select
        value={selectedPolicy || ""}
        onChange={(e) => setSelectedPolicy(Number(e.target.value))}
        className="mb-4 rounded-lg border border-slate-200 px-3 py-2 text-sm dark:border-slate-600 dark:bg-slate-800"
      >
        {policies.map((p) => <option key={p.id} value={p.id}>{p.code}</option>)}
      </select>

      <DataTable
        columns={[
          { key: "version", label: "Version" },
          { key: "created_at", label: "Created", render: (r) => new Date(r.created_at).toLocaleString() },
          { key: "comment", label: "Comment" },
          { key: "actions", label: "", render: (r) => (
            <button onClick={() => viewDiff(r.id)} className="text-teal-600 text-xs hover:underline">View Diff</button>
          )},
        ]}
        rows={versions}
      />

      {diff && (
        <div className="mt-6 grid gap-4 lg:grid-cols-2">
          <ChartCard title={`Previous (v${diff.previous_version || "—"})`}>
            <pre className="max-h-96 overflow-auto text-xs">{JSON.stringify(diff.old, null, 2)}</pre>
          </ChartCard>
          <ChartCard title={`Current (v${diff.version})`}>
            <pre className="max-h-96 overflow-auto text-xs">{JSON.stringify(diff.new, null, 2)}</pre>
          </ChartCard>
        </div>
      )}
    </div>
  );
}
