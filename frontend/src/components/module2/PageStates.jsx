export function Module2PageSkeleton({ rows = 4 }) {
  return (
    <div className="animate-pulse space-y-4">
      <div className="h-10 w-64 rounded-lg bg-slate-200 dark:bg-slate-700" />
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        {Array.from({ length: 4 }).map((_, i) => (
          <div key={i} className="h-24 rounded-xl bg-slate-200 dark:bg-slate-700" />
        ))}
      </div>
      <div className="grid gap-4 lg:grid-cols-2">
        <div className="h-64 rounded-xl bg-slate-200 dark:bg-slate-700" />
        <div className="h-64 rounded-xl bg-slate-200 dark:bg-slate-700" />
      </div>
      {Array.from({ length: rows }).map((_, i) => (
        <div key={`row-${i}`} className="h-8 rounded bg-slate-100 dark:bg-slate-800" />
      ))}
    </div>
  );
}

export function Module2EmptyState({ title, message, hint }) {
  return (
    <div className="rounded-xl border border-dashed border-slate-300 bg-slate-50 px-6 py-10 text-center dark:border-slate-600 dark:bg-slate-900/40">
      <p className="text-sm font-semibold text-slate-700 dark:text-slate-200">{title || "No data yet"}</p>
      <p className="mt-2 text-sm text-slate-500 dark:text-slate-400">{message}</p>
      {hint && <p className="mt-3 text-xs text-teal-700 dark:text-teal-400">{hint}</p>}
    </div>
  );
}

export function Module2ErrorState({ message, onRetry }) {
  return (
    <div className="rounded-xl border border-red-200 bg-red-50 px-6 py-10 text-center dark:border-red-900 dark:bg-red-950/40">
      <p className="text-sm font-medium text-red-700 dark:text-red-300">{message || "Failed to load data."}</p>
      {onRetry && (
        <button
          type="button"
          onClick={onRetry}
          className="mt-4 rounded-lg bg-red-600 px-4 py-2 text-sm text-white hover:bg-red-700"
        >
          Retry
        </button>
      )}
    </div>
  );
}
