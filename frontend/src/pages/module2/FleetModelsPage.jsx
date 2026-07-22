import { useEffect, useState } from "react";
import { Loader2 } from "lucide-react";
import { useAuth } from "../../context/AuthContext";
import { PageHeader } from "../../components/module2/PageHeader";
import { DataTable } from "../../components/module2/DataTable";

export function FleetModelsPage() {
  const { fetchWithAuth } = useAuth();
  const [models, setModels] = useState([]);
  const [circuitBreakers, setCircuitBreakers] = useState({});
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    (async () => {
      setLoading(true);
      try {
        const [modelsRes, cbRes] = await Promise.all([
          fetchWithAuth("/api/firewall/models/"),
          fetchWithAuth("/api/admin/gateway/circuit-breaker/state/"),
        ]);
        const modelsData = await modelsRes.json();
        setModels(modelsData.results || modelsData);
        if (cbRes.ok) {
          const cbData = await cbRes.json();
          const map = {};
          (cbData.states || cbData || []).forEach?.((s) => { map[s.model || s.name] = s; });
          if (typeof cbData === "object" && !Array.isArray(cbData)) {
            Object.assign(map, cbData);
          }
          setCircuitBreakers(map);
        }
      } finally {
        setLoading(false);
      }
    })();
  }, []);

  const resetCircuit = async (model) => {
    await fetchWithAuth("/api/admin/gateway/circuit-breaker/reset/", {
      method: "POST",
      body: JSON.stringify({ model }),
    });
    window.location.reload();
  };

  if (loading) return <div className="flex justify-center py-20"><Loader2 className="h-8 w-8 animate-spin text-teal-500" /></div>;

  return (
    <div>
      <PageHeader title="LLM Model Governance" subtitle="BYOK registry, circuit-breakers, and kill-switch status" />
      <DataTable
        columns={[
          { key: "model_name", label: "Model" },
          { key: "model_id", label: "LiteLLM ID" },
          { key: "provider", label: "Provider" },
          { key: "enabled", label: "Enabled", render: (r) => r.enabled ? "Yes" : "No" },
          { key: "circuit", label: "Circuit", render: (r) => {
            const cb = circuitBreakers[r.model_name];
            const state = cb?.state || cb?.status || "closed";
            return (
              <span className={`text-xs ${state === "open" ? "text-red-600" : "text-green-600"}`}>
                {state}
                {state === "open" && (
                  <button onClick={() => resetCircuit(r.model_name)} className="ml-2 text-teal-600 hover:underline">Reset</button>
                )}
              </span>
            );
          }},
        ]}
        rows={models}
      />
    </div>
  );
}
