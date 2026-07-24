import { useCallback, useEffect, useState } from "react";
import { Loader2, Plus, Play } from "lucide-react";
import { useAuth } from "../../context/AuthContext";
import { createModule2Api } from "../../api/module2";
import { PageHeader } from "../../components/module2/PageHeader";
import { DataTable } from "../../components/module2/DataTable";

const STEP_ACTIONS = ["notify_webhook", "create_incident", "kill_switch"];

export function PlaybooksPage() {
  const { fetchWithAuth } = useAuth();
  const api = createModule2Api(fetchWithAuth);
  const [playbooks, setPlaybooks] = useState([]);
  const [loading, setLoading] = useState(true);
  const [showForm, setShowForm] = useState(false);
  const [form, setForm] = useState({ name: "", description: "", steps: [{ action: "notify_webhook", params: { url: "" } }] });

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const data = await api.listPlaybooks();
      setPlaybooks(data.results || data);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  const addStep = () => {
    setForm({ ...form, steps: [...form.steps, { action: "notify_webhook", params: {} }] });
  };

  const handleCreate = async () => {
    await api.createPlaybook(form);
    setShowForm(false);
    load();
  };

  const runPlaybook = async (id) => {
    await api.runPlaybook(id);
    alert("Playbook execution queued");
  };

  if (loading) return <div className="flex justify-center py-20"><Loader2 className="h-8 w-8 animate-spin text-teal-500" /></div>;

  return (
    <div>
      <PageHeader
        title="Response Playbooks"
        subtitle="Automated response step sequences"
        actions={
          <button onClick={() => setShowForm(true)} className="flex items-center gap-1 rounded-lg bg-teal-600 px-4 py-2 text-sm text-white">
            <Plus className="h-4 w-4" /> New Playbook
          </button>
        }
      />

      {showForm && (
        <div className="mb-4 rounded-xl border p-4 dark:border-slate-700">
          <input placeholder="Name" value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} className="mb-3 w-full rounded-lg border px-3 py-2 text-sm dark:border-slate-600 dark:bg-slate-800" />
          <textarea placeholder="Description" value={form.description} onChange={(e) => setForm({ ...form, description: e.target.value })} className="mb-3 w-full rounded-lg border px-3 py-2 text-sm dark:border-slate-600 dark:bg-slate-800" rows={2} />
          <p className="mb-2 text-sm font-medium">Steps</p>
          {form.steps.map((step, i) => (
            <div key={i} className="mb-2 flex gap-2">
              <select
                value={step.action}
                onChange={(e) => {
                  const steps = [...form.steps];
                  steps[i] = { ...steps[i], action: e.target.value };
                  setForm({ ...form, steps });
                }}
                className="rounded-lg border px-2 py-1 text-sm dark:border-slate-600 dark:bg-slate-800"
              >
                {STEP_ACTIONS.map((a) => <option key={a} value={a}>{a}</option>)}
              </select>
              <input
                placeholder="Params JSON or URL"
                value={step.params?.url || JSON.stringify(step.params || {})}
                onChange={(e) => {
                  const steps = [...form.steps];
                  steps[i] = { action: step.action, params: { url: e.target.value } };
                  setForm({ ...form, steps });
                }}
                className="flex-1 rounded-lg border px-2 py-1 text-sm dark:border-slate-600 dark:bg-slate-800"
              />
            </div>
          ))}
          <button onClick={addStep} className="mb-3 text-xs text-teal-600 hover:underline">+ Add Step</button>
          <div className="flex gap-2">
            <button onClick={handleCreate} className="rounded-lg bg-teal-600 px-4 py-2 text-sm text-white">Save</button>
            <button onClick={() => setShowForm(false)} className="rounded-lg border px-4 py-2 text-sm">Cancel</button>
          </div>
        </div>
      )}

      <DataTable
        columns={[
          { key: "name", label: "Name" },
          { key: "description", label: "Description" },
          { key: "steps", label: "Steps", render: (r) => `${(r.steps || []).length} steps` },
          { key: "enabled", label: "Status", render: (r) => r.enabled ? "Enabled" : "Disabled" },
          { key: "run", label: "", render: (r) => (
            <button onClick={() => runPlaybook(r.id)} className="flex items-center gap-1 text-teal-600 text-xs hover:underline">
              <Play className="h-3 w-3" /> Run
            </button>
          )},
        ]}
        rows={playbooks}
      />
    </div>
  );
}
