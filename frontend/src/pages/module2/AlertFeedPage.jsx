import { useCallback, useEffect, useState } from "react";
import { Loader2 } from "lucide-react";
import { Link } from "react-router-dom";
import { useAuth } from "../../context/AuthContext";
import { createModule2Api } from "../../api/module2";
import { PageHeader } from "../../components/module2/PageHeader";
import { DataTable } from "../../components/module2/DataTable";

export function AlertFeedPage() {
  const { fetchWithAuth } = useAuth();
  const api = createModule2Api(fetchWithAuth);
  const [firings, setFirings] = useState([]);
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const data = await api.listAlertFirings(true);
      setFirings(data.results || []);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { load(); const t = setInterval(load, 30000); return () => clearInterval(t); }, [load]);

  if (loading) return <div className="flex justify-center py-20"><Loader2 className="h-8 w-8 animate-spin text-teal-500" /></div>;

  return (
    <div>
      <PageHeader title="Alert Feed" subtitle="Real-time fired alerts" />
      <DataTable
        columns={[
          { key: "rule_name", label: "Rule" },
          { key: "rule_severity", label: "Severity", render: (r) => (
            <span className={`rounded px-2 py-0.5 text-xs font-medium ${r.rule_severity === "critical" ? "bg-red-100 text-red-700" : "bg-amber-100 text-amber-700"}`}>
              {r.rule_severity}
            </span>
          )},
          { key: "current_value", label: "Value" },
          { key: "message", label: "Message" },
          { key: "fired_at", label: "Fired", render: (r) => new Date(r.fired_at).toLocaleString() },
          { key: "incident", label: "Incident", render: (r) => r.incident_id ? (
            <Link to={`/incidents/${r.incident_id}`} className="text-teal-600 text-xs hover:underline">#{r.incident_id}</Link>
          ) : "—" },
        ]}
        rows={firings}
        emptyMessage="No active alert firings"
      />
    </div>
  );
}
