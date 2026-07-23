import { useCallback, useEffect, useState } from "react";
import { Loader2, Play } from "lucide-react";
import { useAuth } from "../../context/AuthContext";
import { PageHeader } from "../../components/module2/PageHeader";
import { ChartCard } from "../../components/module2/ChartCard";

const CONDITION_TYPES = [
  { id: "keywords", label: "Keywords", template: { keywords: ["SSN", "password"], field: "prompt" } },
  { id: "regex", label: "Regex", template: { regex: "\\b\\d{3}-\\d{2}-\\d{4}\\b", field: "prompt" } },
  { id: "pii", label: "PII Type", template: { pii_type: "ssn", field: "prompt" } },
  { id: "owasp", label: "OWASP Category", template: { owasp_code: "LLM01", field: "prompt" } },
  { id: "model", label: "Model ID", template: { model_id: "gpt-4", field: "model" } },
  { id: "rate", label: "Rate Threshold", template: { rate_per_minute: 100, field: "agent" } },
];

const ACTIONS = ["block", "redact", "monitor"];

export function PolicyBuilderPage() {
  const { fetchWithAuth } = useAuth();
  const [policies, setPolicies] = useState([]);
  const [selectedPolicy, setSelectedPolicy] = useState(null);
  const [rules, setRules] = useState([]);
  const [testPrompt, setTestPrompt] = useState("");
  const [testResult, setTestResult] = useState(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);

  const loadPolicies = useCallback(async () => {
    setLoading(true);
    try {
      const res = await fetchWithAuth("/api/policies/?enabled=true");
      const data = await res.json();
      const list = data.results || data;
      setPolicies(list);
      if (list.length && !selectedPolicy) setSelectedPolicy(list[0].id);
    } finally {
      setLoading(false);
    }
  }, []);

  const loadRules = useCallback(async (policyId) => {
    if (!policyId) return;
    const res = await fetchWithAuth(`/api/policies/${policyId}/rules/`);
    const data = await res.json();
    setRules(data.results || data);
  }, []);

  useEffect(() => { loadPolicies(); }, [loadPolicies]);
  useEffect(() => { if (selectedPolicy) loadRules(selectedPolicy); }, [selectedPolicy, loadRules]);

  const addRuleFromPalette = async (condType, action) => {
    if (!selectedPolicy) return;
    setSaving(true);
    try {
      const cond = CONDITION_TYPES.find((c) => c.id === condType);
      await fetchWithAuth(`/api/policies/${selectedPolicy}/rules/`, {
        method: "POST",
        body: JSON.stringify({
          name: `${cond.label} Rule`,
          rule_type: condType === "regex" ? "regex" : "keywords",
          condition: cond.template,
          action,
          enabled: true,
          priority: rules.length + 1,
        }),
      });
      await loadRules(selectedPolicy);
    } finally {
      setSaving(false);
    }
  };

  const runTest = async () => {
    if (!selectedPolicy || !testPrompt) return;
    const res = await fetchWithAuth(`/api/policies/${selectedPolicy}/test/`, {
      method: "POST",
      body: JSON.stringify({ prompt: testPrompt }),
    });
    setTestResult(await res.json());
  };

  if (loading) return <div className="flex justify-center py-20"><Loader2 className="h-8 w-8 animate-spin text-teal-500" /></div>;

  return (
    <div>
      <PageHeader title="Policy Builder" subtitle="Visual rule authoring with live test sandbox" />

      <div className="mb-4">
        <select
          value={selectedPolicy || ""}
          onChange={(e) => setSelectedPolicy(Number(e.target.value))}
          className="rounded-lg border border-slate-200 px-3 py-2 text-sm dark:border-slate-600 dark:bg-slate-800"
        >
          {policies.map((p) => (
            <option key={p.id} value={p.id}>{p.code} — {p.name}</option>
          ))}
        </select>
      </div>

      <div className="grid gap-4 lg:grid-cols-3">
        <ChartCard title="Condition Palette">
          <div className="space-y-2">
            {CONDITION_TYPES.map((c) => (
              <div key={c.id} className="rounded-lg border border-slate-100 p-2 dark:border-slate-700">
                <p className="text-sm font-medium">{c.label}</p>
                <div className="mt-1 flex gap-1">
                  {ACTIONS.map((a) => (
                    <button
                      key={a}
                      disabled={saving}
                      onClick={() => addRuleFromPalette(c.id, a)}
                      className="rounded bg-teal-100 px-2 py-0.5 text-xs text-teal-700 hover:bg-teal-200 dark:bg-teal-900/40 dark:text-teal-300"
                    >
                      + {a}
                    </button>
                  ))}
                </div>
              </div>
            ))}
          </div>
        </ChartCard>

        <ChartCard title="Policy Canvas" className="lg:col-span-2">
          <div className="space-y-2">
            {rules.map((rule, idx) => (
              <div
                key={rule.id}
                draggable
                className="flex items-center gap-3 rounded-lg border border-slate-200 bg-slate-50 px-3 py-2 dark:border-slate-600 dark:bg-slate-700/40"
              >
                <span className="text-xs text-slate-400">#{idx + 1}</span>
                <span className="flex-1 text-sm font-medium">{rule.name}</span>
                <span className="rounded bg-slate-200 px-2 py-0.5 text-xs dark:bg-slate-600">{rule.action}</span>
                <span className="text-xs text-slate-400">{rule.rule_type}</span>
              </div>
            ))}
            {!rules.length && <p className="py-8 text-center text-sm text-slate-400">Drag conditions from the palette to build rules</p>}
          </div>
        </ChartCard>
      </div>

      <ChartCard title="Live Test Pane" className="mt-4">
        <div className="flex gap-2">
          <input
            value={testPrompt}
            onChange={(e) => setTestPrompt(e.target.value)}
            placeholder="Enter sample prompt to test..."
            className="flex-1 rounded-lg border border-slate-200 px-3 py-2 text-sm dark:border-slate-600 dark:bg-slate-800"
          />
          <button onClick={runTest} className="flex items-center gap-1 rounded-lg bg-teal-600 px-4 py-2 text-sm text-white">
            <Play className="h-4 w-4" /> Test
          </button>
        </div>
        {testResult && (
          <pre className="mt-3 rounded-lg bg-slate-900 p-3 text-xs text-green-400 overflow-x-auto">
            {JSON.stringify(testResult, null, 2)}
          </pre>
        )}
      </ChartCard>
    </div>
  );
}
