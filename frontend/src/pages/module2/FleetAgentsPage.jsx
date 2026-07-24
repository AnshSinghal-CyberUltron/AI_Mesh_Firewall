import { useEffect, useState } from "react";
import { Loader2 } from "lucide-react";
import { useAuth } from "../../context/AuthContext";
import { PageHeader } from "../../components/module2/PageHeader";
import { DataTable } from "../../components/module2/DataTable";

export function FleetAgentsPage() {
  const { fetchWithAuth } = useAuth();
  const [agents, setAgents] = useState([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    (async () => {
      setLoading(true);
      try {
        const res = await fetchWithAuth("/api/dashboard/agents/");
        const data = await res.json();
        setAgents(data.agents || data.results || []);
      } finally {
        setLoading(false);
      }
    })();
  }, []);

  if (loading) return <div className="flex justify-center py-20"><Loader2 className="h-8 w-8 animate-spin text-teal-500" /></div>;

  return (
    <div>
      <PageHeader title="Agent & Endpoint Health" subtitle="Fleet status across your organization" />
      <DataTable
        columns={[
          { key: "id", label: "ID" },
          { key: "name", label: "Name" },
          { key: "agent_type", label: "Type" },
          { key: "status", label: "Status" },
          { key: "risk_score", label: "Risk", render: (r) => r.risk_score?.toFixed?.(2) ?? r.risk_score ?? "—" },
        ]}
        rows={agents}
        emptyMessage="No agents registered"
      />
    </div>
  );
}
