import { Workflow, Database, Wrench, Boxes } from "lucide-react";
import { POLICY_CREATE_DOMAINS } from "../utils/policyCreateBehavior";

// Icon per policy domain. Kept here (not in the metadata module) so the shared
// metadata stays framework-agnostic and the switcher owns its presentation.
const DOMAIN_ICON = {
  pipeline: Workflow,
  rag: Database,
  mcp: Wrench,
  vector: Boxes,
};

/**
 * In-panel domain switcher for the "New Policy" modal. Renders a segmented
 * control across Pipeline / RAG / MCP / Vector so the create panel can
 * morph to the chosen domain's settings without closing and switching tabs.
 *
 * Generic domains (pipeline/rag/mcp) morph the form in place; selecting
 * "vector" is a hand-off (the caller swaps to the dedicated Vector modal), since
 * Vector is a separate backend resource. flex-wrap keeps it from overflowing the
 * modal width on narrow viewports (same pattern as the module tab bar).
 */
export function PolicyDomainSwitcher({ value, onChange, disabled = false }) {
  const raw = String(value || "pipeline").toLowerCase();
  const active = raw === "global" ? "pipeline" : raw;
  return (
    <div>
      <label className="block text-xs font-medium text-slate-700 dark:text-slate-300 mb-1.5">
        Policy Domain
      </label>
      <div
        role="tablist"
        aria-label="Policy domain"
        className="flex flex-wrap gap-1.5 rounded-xl border border-slate-200 dark:border-slate-700 bg-slate-50 dark:bg-slate-900/60 p-1"
      >
        {POLICY_CREATE_DOMAINS.map((d) => {
          const Icon = DOMAIN_ICON[d.key] || Workflow;
          const isActive = active === d.key;
          return (
            <button
              key={d.key}
              type="button"
              role="tab"
              aria-selected={isActive}
              disabled={disabled}
              onClick={() => !isActive && onChange?.(d.key)}
              className={`inline-flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-xs font-semibold transition-colors disabled:opacity-50 disabled:cursor-not-allowed ${
                isActive
                  ? "bg-white dark:bg-slate-700 text-teal-700 dark:text-teal-200 shadow-sm ring-1 ring-teal-500/30"
                  : "text-slate-600 dark:text-slate-400 hover:bg-white/70 dark:hover:bg-slate-800"
              }`}
            >
              <Icon className="h-3.5 w-3.5" />
              {d.label}
            </button>
          );
        })}
      </div>
    </div>
  );
}

export default PolicyDomainSwitcher;
