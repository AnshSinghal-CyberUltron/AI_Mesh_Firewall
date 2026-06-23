/** Dark-themed Recharts tooltip props for Module 2 charts (no default white box). */
export const module2TooltipProps = {
  contentStyle: {
    backgroundColor: "rgba(15, 23, 42, 0.95)",
    border: "1px solid #475569",
    borderRadius: "8px",
    color: "#f1f5f9",
  },
  itemStyle: { color: "#f1f5f9" },
  labelStyle: { color: "#e2e8f0" },
};

/** Shared class for custom Recharts tooltip panels in Module 2. */
export const module2TooltipPanelClass =
  "rounded-lg border border-slate-600 bg-slate-900/95 px-3 py-2 text-xs text-slate-100 shadow-lg";
