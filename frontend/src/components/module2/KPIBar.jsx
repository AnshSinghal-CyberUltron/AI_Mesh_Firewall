export function KPIBar({ items }) {
  return (
    <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-5">
      {items.map((item) => {
        const interactive = item.clickable && typeof item.onClick === "function";
        const className = [
          "group relative rounded-xl border bg-white p-4 shadow-sm dark:bg-slate-800/60",
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
                className="pointer-events-none absolute left-1/2 top-full z-30 mt-2 w-64 -translate-x-1/2 rounded-lg bg-slate-900 px-3 py-2 text-xs font-normal leading-relaxed text-slate-100 opacity-0 shadow-lg transition-opacity duration-150 group-hover:opacity-100"
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
  );
}
