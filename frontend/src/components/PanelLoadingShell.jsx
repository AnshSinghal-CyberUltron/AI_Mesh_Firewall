/**
 * Dimension-stable loading shell (ui-ux-pro-max progressive-loading / CLS avoidance).
 */

export function PanelLoadingShell({ variant = "governance", rows = 4 }) {
  if (variant === "routing") {
    return (
      <div
        className="bg-slate-100 dark:bg-slate-800/50 border border-slate-200 dark:border-slate-700 rounded-lg p-6 animate-pulse"
        aria-busy="true"
        aria-label="Loading routing governance"
      >
        <div className="h-6 bg-slate-300 dark:bg-slate-700 rounded w-48 mb-4" />
        <div className="space-y-3">
          {[1, 2, 3, 4].map((i) => (
            <div key={i} className="h-10 bg-slate-300/70 dark:bg-slate-700/50 rounded" />
          ))}
        </div>
      </div>
    );
  }

  return (
    <div
      className="bg-slate-100 dark:bg-slate-800/50 border border-slate-200 dark:border-slate-700 rounded-lg overflow-hidden animate-pulse"
      aria-busy="true"
      aria-label="Loading model governance"
    >
      <div className="px-5 py-4 border-b border-slate-200 dark:border-slate-700 flex gap-3">
        <div className="h-10 w-10 rounded-lg bg-slate-300 dark:bg-slate-700" />
        <div className="flex-1 space-y-2">
          <div className="h-4 bg-slate-300 dark:bg-slate-700 rounded w-40" />
          <div className="h-3 bg-slate-300/80 dark:bg-slate-700/60 rounded w-64" />
        </div>
      </div>
      <div className="p-5 space-y-3">
        {Array.from({ length: rows }, (_, i) => (
          <div key={i} className="h-11 bg-slate-300/70 dark:bg-slate-700/50 rounded-lg" />
        ))}
      </div>
    </div>
  );
}
