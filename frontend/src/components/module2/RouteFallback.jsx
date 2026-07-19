import { useEffect, useState } from "react";

/** Suspense fallback for lazy routes — shows progress so a hung chunk is obvious. */
export function RouteFallback({ label = "Loading module…" }) {
  const [slow, setSlow] = useState(false);

  useEffect(() => {
    const id = setTimeout(() => setSlow(true), 8000);
    return () => clearTimeout(id);
  }, []);

  return (
    <div className="flex min-h-[40vh] flex-col items-center justify-center gap-3 px-4 text-center">
      <div className="h-8 w-8 animate-spin rounded-full border-2 border-teal-500 border-t-transparent" />
      <p className="text-sm font-medium text-slate-600 dark:text-slate-300">{label}</p>
      {slow ? (
        <div className="max-w-sm space-y-2">
          <p className="text-xs text-slate-500 dark:text-slate-400">
            Still loading — the firewall module bundle is large. If this does not finish, refresh the page.
          </p>
          <button
            type="button"
            onClick={() => window.location.reload()}
            className="rounded-lg border border-slate-200 px-3 py-1.5 text-xs font-medium text-slate-700 hover:border-teal-300 hover:text-teal-700 dark:border-slate-600 dark:text-slate-200"
          >
            Refresh
          </button>
        </div>
      ) : null}
    </div>
  );
}
