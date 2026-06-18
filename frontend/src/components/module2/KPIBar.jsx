export function KPIBar({ items, loading = false }) {
  return (
    <div className="relative isolate overflow-visible">
      {loading && (
        <div
          className="pointer-events-none absolute inset-0 z-10 flex items-center justify-center rounded-xl bg-white/60 dark:bg-slate-900/50"
          aria-live="polite"
          aria-busy="true"
        >
          <span className="rounded-full bg-white px-3 py-1 text-xs font-medium text-slate-600 shadow-sm dark:bg-slate-800 dark:text-slate-300">
            Updating metrics…
          </span>
        </div>
      )}
      <div className={`grid grid-cols-2 gap-3 overflow-visible sm:grid-cols-3 lg:grid-cols-4 xl:grid-cols-5 ${loading ? "opacity-50" : ""}`}>
      {items.map((item) => {
        const interactive = item.clickable && typeof item.onClick === "function";
        const className = [
          "group relative z-0 rounded-xl border bg-white p-4 shadow-sm hover:z-20 focus-within:z-20 dark:bg-slate-800/60",
          item.active
            ? "border-teal-400 ring-2 ring-teal-400/30 dark:border-teal-500"
            : "border-slate-200 dark:border-slate-700",
          interactive
            ? "cursor-pointer transition hover:border-teal-300 hover:shadow-md dark:hover:border-teal-600"
            : item.helpText
              ? "cursor-help"
              : "",
        ].join(" ");

        const content = (
          <>
            <p className="text-xs font-medium uppercase tracking-wide text-slate-500 dark:text-slate-400">
              {item.label}
              {interactive && (
                <span className="ml-1 normal-case font-normal text-teal-600 dark:text-teal-400">
                  · view
                </span>
              )}
            </p>
            <p className={`mt-1 text-2xl font-bold ${item.color || "text-slate-900 dark:text-slate-100"}`}>
              {item.value}
            </p>
            {item.sub && (
              <p className="mt-0.5 text-xs text-slate-400 dark:text-slate-500">{item.sub}</p>
            )}
            {item.helpText && (
              <span
                role="tooltip"
                className="pointer-events-none absolute bottom-full left-1/2 z-[70] mb-2 w-64 -translate-x-1/2 rounded-lg bg-slate-900 px-3 py-2 text-xs font-normal leading-relaxed text-slate-100 opacity-0 shadow-lg transition-opacity duration-150 group-hover:opacity-100"
              >
                {item.helpText}
              </span>
            )}
          </>
        );

        if (interactive) {
          return (
            <button
              key={item.key || item.label}
              type="button"
              onClick={item.onClick}
              className={`text-left ${className}`}
            >
              {content}
            </button>
          );
        }

        return (
          <div key={item.key || item.label} className={className}>
            {content}
          </div>
        );
      })}
      </div>
    </div>
  );
}
