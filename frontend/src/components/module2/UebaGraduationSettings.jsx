import { useCallback, useEffect, useState } from "react";
import { ChevronDown, ChevronRight, Loader2, Save } from "lucide-react";

const DEFAULTS = {
  graduation_min_requests: 50,
  graduation_min_days: 7,
  scanner_graduation_min_requests: 10,
  scanner_graduation_min_days: 1,
  llm_triage_enabled: true,
  llm_triage_min_traditional_score: 0.45,
  high_risk_threshold: 0.7,
  medium_risk_threshold: 0.35,
};

export function UebaGraduationSettings({ api, isAdmin, onSaved, expanded = false }) {
  const [open, setOpen] = useState(expanded);
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState(null);
  const [form, setForm] = useState(DEFAULTS);

  const load = useCallback(async () => {
    if (!api) return;
    setLoading(true);
    setError(null);
    try {
      const data = await api.getUebaSettings({ useCache: false });
      setForm({ ...DEFAULTS, ...data });
    } catch (err) {
      setError(err.message || "Failed to load UEBA settings.");
    } finally {
      setLoading(false);
    }
  }, [api]);

  useEffect(() => {
    if (open || expanded) load();
  }, [open, expanded, load]);

  const isPanelOpen = expanded || open;

  const handleChange = (field, value) => {
    setForm((prev) => ({ ...prev, [field]: value }));
  };

  const handleSave = async () => {
    setSaving(true);
    setError(null);
    try {
      await api.patchUebaSettings({
        graduation_min_requests: Number(form.graduation_min_requests),
        graduation_min_days: Number(form.graduation_min_days),
        scanner_graduation_min_requests: Number(form.scanner_graduation_min_requests),
        scanner_graduation_min_days: Number(form.scanner_graduation_min_days),
        llm_triage_enabled: Boolean(form.llm_triage_enabled),
        llm_triage_min_traditional_score: Number(form.llm_triage_min_traditional_score),
        high_risk_threshold: Number(form.high_risk_threshold),
        medium_risk_threshold: Number(form.medium_risk_threshold),
      });
      onSaved?.();
    } catch (err) {
      setError(err.message || "Failed to save settings.");
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="mb-6 rounded-xl border border-slate-200 bg-white shadow-sm dark:border-slate-700 dark:bg-slate-800/60">
      {expanded ? (
        <div className="border-b border-slate-200 px-4 py-3 dark:border-slate-700">
          <h3 className="text-sm font-semibold text-slate-800 dark:text-slate-100">Org UEBA Settings</h3>
          <p className="text-xs text-slate-500 dark:text-slate-400">
            Graduation thresholds, LLM triage, and risk band cutoffs
          </p>
        </div>
      ) : (
        <button
          type="button"
          onClick={() => setOpen((v) => !v)}
          className="flex w-full items-center justify-between gap-2 px-4 py-3 text-left"
        >
          <div>
            <h3 className="text-sm font-semibold text-slate-800 dark:text-slate-100">Org UEBA Settings</h3>
            <p className="text-xs text-slate-500 dark:text-slate-400">
              Graduation thresholds, LLM triage, and risk band cutoffs
            </p>
          </div>
          {isPanelOpen ? <ChevronDown className="h-4 w-4 text-slate-400" /> : <ChevronRight className="h-4 w-4 text-slate-400" />}
        </button>
      )}
      {isPanelOpen && (
        <div className={`${expanded ? "" : "border-t border-slate-200 dark:border-slate-700"} px-4 py-4`}>
          {loading ? (
            <div className="flex justify-center py-6">
              <Loader2 className="h-5 w-5 animate-spin text-teal-500" />
            </div>
          ) : (
            <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
              <label className="text-xs">
                <span className="mb-1 block font-medium text-slate-600 dark:text-slate-300">Graduation requests</span>
                <input
                  type="number"
                  min={1}
                  disabled={!isAdmin}
                  value={form.graduation_min_requests}
                  onChange={(e) => handleChange("graduation_min_requests", e.target.value)}
                  className="w-full rounded-lg border border-slate-200 px-2 py-1.5 text-sm dark:border-slate-600 dark:bg-slate-900"
                />
              </label>
              <label className="text-xs">
                <span className="mb-1 block font-medium text-slate-600 dark:text-slate-300">Graduation days</span>
                <input
                  type="number"
                  min={0.1}
                  step={0.1}
                  disabled={!isAdmin}
                  value={form.graduation_min_days}
                  onChange={(e) => handleChange("graduation_min_days", e.target.value)}
                  className="w-full rounded-lg border border-slate-200 px-2 py-1.5 text-sm dark:border-slate-600 dark:bg-slate-900"
                />
              </label>
              <label className="text-xs">
                <span className="mb-1 block font-medium text-slate-600 dark:text-slate-300">Scanner graduation requests</span>
                <input
                  type="number"
                  min={1}
                  disabled={!isAdmin}
                  value={form.scanner_graduation_min_requests}
                  onChange={(e) => handleChange("scanner_graduation_min_requests", e.target.value)}
                  className="w-full rounded-lg border border-slate-200 px-2 py-1.5 text-sm dark:border-slate-600 dark:bg-slate-900"
                />
              </label>
              <label className="text-xs">
                <span className="mb-1 block font-medium text-slate-600 dark:text-slate-300">Scanner graduation days</span>
                <input
                  type="number"
                  min={0.1}
                  step={0.1}
                  disabled={!isAdmin}
                  value={form.scanner_graduation_min_days}
                  onChange={(e) => handleChange("scanner_graduation_min_days", e.target.value)}
                  className="w-full rounded-lg border border-slate-200 px-2 py-1.5 text-sm dark:border-slate-600 dark:bg-slate-900"
                />
              </label>
              <label className="text-xs">
                <span className="mb-1 block font-medium text-slate-600 dark:text-slate-300">LLM min traditional score</span>
                <input
                  type="number"
                  min={0}
                  max={1}
                  step={0.05}
                  disabled={!isAdmin}
                  value={form.llm_triage_min_traditional_score}
                  onChange={(e) => handleChange("llm_triage_min_traditional_score", e.target.value)}
                  className="w-full rounded-lg border border-slate-200 px-2 py-1.5 text-sm dark:border-slate-600 dark:bg-slate-900"
                />
              </label>
              <label className="text-xs">
                <span className="mb-1 block font-medium text-slate-600 dark:text-slate-300">High risk threshold</span>
                <input
                  type="number"
                  min={0}
                  max={1}
                  step={0.05}
                  disabled={!isAdmin}
                  value={form.high_risk_threshold}
                  onChange={(e) => handleChange("high_risk_threshold", e.target.value)}
                  className="w-full rounded-lg border border-slate-200 px-2 py-1.5 text-sm dark:border-slate-600 dark:bg-slate-900"
                />
              </label>
              <label className="text-xs">
                <span className="mb-1 block font-medium text-slate-600 dark:text-slate-300">Medium risk threshold</span>
                <input
                  type="number"
                  min={0}
                  max={1}
                  step={0.05}
                  disabled={!isAdmin}
                  value={form.medium_risk_threshold}
                  onChange={(e) => handleChange("medium_risk_threshold", e.target.value)}
                  className="w-full rounded-lg border border-slate-200 px-2 py-1.5 text-sm dark:border-slate-600 dark:bg-slate-900"
                />
              </label>
              <label className="flex items-center gap-2 self-end text-xs font-medium text-slate-600 dark:text-slate-300">
                <input
                  type="checkbox"
                  disabled={!isAdmin}
                  checked={Boolean(form.llm_triage_enabled)}
                  onChange={(e) => handleChange("llm_triage_enabled", e.target.checked)}
                />
                LLM triage enabled
              </label>
            </div>
          )}
          {error && <p className="mt-3 text-xs text-red-600 dark:text-red-400">{error}</p>}
          {isAdmin && !loading && (
            <button
              type="button"
              disabled={saving}
              onClick={handleSave}
              className="mt-4 inline-flex items-center gap-1.5 rounded-lg bg-teal-600 px-3 py-2 text-xs font-semibold text-white hover:bg-teal-700 disabled:opacity-60"
            >
              {saving ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Save className="h-3.5 w-3.5" />}
              Save settings
            </button>
          )}
          {!isAdmin && (
            <p className="mt-3 text-xs text-slate-500">Platform admin required to edit org UEBA settings.</p>
          )}
        </div>
      )}
    </div>
  );
}
