import { useState } from "react";
import { Loader2, Save } from "lucide-react";

const PURPOSE_OPTIONS = [
  { value: "production", label: "Production" },
  { value: "scanner", label: "Scanner" },
  { value: "test", label: "Test" },
  { value: "simulator", label: "Simulator" },
];

export function UebaKeyOverridePanel({ behavior, api, onSaved }) {
  const [purpose, setPurpose] = useState(behavior?.key_purpose || "production");
  const [gradRequests, setGradRequests] = useState(
    behavior?.ueba_graduation_requests ?? "",
  );
  const [gradDays, setGradDays] = useState(behavior?.ueba_graduation_days ?? "");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState(null);
  const [success, setSuccess] = useState(null);

  if (!behavior?.key_id || !api) return null;

  const handleSave = async () => {
    setSaving(true);
    setError(null);
    setSuccess(null);
    try {
      const payload = { key_purpose: purpose };
      if (gradRequests !== "") {
        payload.ueba_graduation_requests = gradRequests === null ? null : Number(gradRequests);
      }
      if (gradDays !== "") {
        payload.ueba_graduation_days = gradDays === null ? null : Number(gradDays);
      }
      await api.patchUebaKeySettings(behavior.key_id, payload);
      setSuccess("Per-key UEBA settings saved.");
      onSaved?.();
    } catch (err) {
      setError(err.message || "Failed to save key settings.");
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="rounded-lg border border-slate-200 p-3 dark:border-slate-600">
      <p className="mb-2 text-xs font-semibold uppercase text-slate-500">Per-key UEBA overrides</p>
      <p className="mb-3 text-[11px] text-slate-500 dark:text-slate-400">
        Leave graduation fields blank to use org defaults. Overrides apply only to this API key.
      </p>
      <div className="grid gap-3 sm:grid-cols-3">
        <label className="block text-xs">
          <span className="mb-1 block text-slate-600 dark:text-slate-400">Key purpose</span>
          <select
            value={purpose}
            onChange={(e) => setPurpose(e.target.value)}
            className="w-full rounded-md border border-slate-200 bg-white px-2 py-1.5 dark:border-slate-600 dark:bg-slate-800"
          >
            {PURPOSE_OPTIONS.map((opt) => (
              <option key={opt.value} value={opt.value}>{opt.label}</option>
            ))}
          </select>
        </label>
        <label className="block text-xs">
          <span className="mb-1 block text-slate-600 dark:text-slate-400">Graduation requests</span>
          <input
            type="number"
            min="1"
            placeholder="Org default"
            value={gradRequests}
            onChange={(e) => setGradRequests(e.target.value)}
            className="w-full rounded-md border border-slate-200 bg-white px-2 py-1.5 dark:border-slate-600 dark:bg-slate-800"
          />
        </label>
        <label className="block text-xs">
          <span className="mb-1 block text-slate-600 dark:text-slate-400">Graduation days</span>
          <input
            type="number"
            min="0.1"
            step="0.1"
            placeholder="Org default"
            value={gradDays}
            onChange={(e) => setGradDays(e.target.value)}
            className="w-full rounded-md border border-slate-200 bg-white px-2 py-1.5 dark:border-slate-600 dark:bg-slate-800"
          />
        </label>
      </div>
      {error && (
        <p className="mt-2 text-xs text-red-600 dark:text-red-400">{error}</p>
      )}
      {success && (
        <p className="mt-2 text-xs text-emerald-700 dark:text-emerald-300">{success}</p>
      )}
      <button
        type="button"
        disabled={saving}
        onClick={handleSave}
        className="mt-3 inline-flex items-center gap-1.5 rounded-lg border border-slate-300 px-3 py-1.5 text-xs font-semibold text-slate-700 hover:bg-slate-50 disabled:opacity-60 dark:border-slate-600 dark:text-slate-200 dark:hover:bg-slate-800"
      >
        {saving ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Save className="h-3.5 w-3.5" />}
        Save overrides
      </button>
    </div>
  );
}
