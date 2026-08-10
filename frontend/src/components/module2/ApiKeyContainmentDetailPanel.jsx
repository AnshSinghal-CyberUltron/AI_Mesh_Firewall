import { X } from "lucide-react";
import { DataTable } from "./DataTable";

export function ApiKeyContainmentDetailPanel({ panel, containment, onClose, onSelectKey }) {
  if (!panel || !containment) return null;

  const isDisabled = panel === "disabled";
  const title = isDisabled ? "Disabled API Keys" : "Active Kill Switches";
  const rows = isDisabled
    ? containment.disabled_keys_detail || []
    : containment.active_kill_switches_detail || [];

  const columns = isDisabled
    ? [
        {
          key: "prefix",
          label: "Key",
          render: (r) => (
            onSelectKey ? (
              <button
                type="button"
                onClick={() => onSelectKey(r.key_id)}
                className="font-mono text-xs text-teal-600 hover:underline dark:text-teal-400"
              >
                {r.prefix}
              </button>
            ) : (
              <span className="font-mono text-xs">{r.prefix}</span>
            )
          ),
        },
        { key: "name", label: "Name" },
        { key: "project_id", label: "Project" },
        {
          key: "owner_email",
          label: "Owner",
          render: (r) => r.owner_email || "—",
        },
        {
          key: "last_used_at",
          label: "Last Used",
          render: (r) => (r.last_used_at ? new Date(r.last_used_at).toLocaleString() : "Never"),
        },
        {
          key: "updated_at",
          label: "Disabled At",
          render: (r) => (r.updated_at ? new Date(r.updated_at).toLocaleString() : "—"),
        },
      ]
    : [
        {
          key: "api_key_prefix",
          label: "Key Prefix",
          render: (r) => (
            <span className="font-mono text-xs">{r.api_key_prefix || "org-wide"}</span>
          ),
        },
        { key: "model_name", label: "Model" },
        { key: "action", label: "Action" },
        {
          key: "reason",
          label: "Reason",
          render: (r) => (
            <span className="line-clamp-2 max-w-xs text-xs">{r.reason || "—"}</span>
          ),
        },
        {
          key: "activated_at",
          label: "Activated",
          render: (r) => (r.activated_at ? new Date(r.activated_at).toLocaleString() : "—"),
        },
      ];

  return (
    <div className="fixed inset-0 z-[90] flex items-end justify-center bg-black/40 p-4 sm:items-center">
      <div className="flex max-h-[85vh] w-full max-w-3xl flex-col rounded-2xl border border-slate-200 bg-white shadow-xl dark:border-slate-700 dark:bg-slate-900">
        <div className="flex items-center justify-between border-b border-slate-200 px-5 py-4 dark:border-slate-700">
          <div>
            <h3 className="text-base font-semibold text-slate-900 dark:text-white">{title}</h3>
            <p className="text-xs text-slate-500 dark:text-slate-400">
              {rows.length} record{rows.length === 1 ? "" : "s"} · updates in real time
            </p>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="rounded-lg p-2 text-slate-500 hover:bg-slate-100 dark:hover:bg-slate-800"
            aria-label="Close"
          >
            <X className="h-4 w-4" />
          </button>
        </div>
        <div className="overflow-y-auto p-5">
          <DataTable
            columns={columns}
            rows={rows}
            emptyMessage={isDisabled ? "No disabled API keys" : "No active kill switches"}
          />
        </div>
      </div>
    </div>
  );
}
