import { useState } from "react";
import { ChevronDown, Compass } from "lucide-react";

export function ContextualAppBar({ title = "Page Objective", description }) {
  const [expanded, setExpanded] = useState(false);

  return (
    <section
      className={`group relative mb-6 overflow-hidden rounded-xl border shadow-sm transition-all duration-300 ${
        expanded
          ? "border-teal-300/70 bg-gradient-to-br from-teal-50/80 via-white to-cyan-50/50 shadow-teal-100/50 dark:border-teal-800/60 dark:from-teal-950/30 dark:via-slate-900/80 dark:to-cyan-950/20 dark:shadow-teal-900/20"
          : "border-slate-200/80 bg-gradient-to-r from-slate-50 to-slate-100/50 hover:border-teal-200/60 hover:shadow-md dark:border-slate-700 dark:from-slate-800/50 dark:to-slate-900/40 dark:hover:border-teal-900/50"
      }`}
    >
      {/* Left accent bar */}
      <div
        className={`absolute left-0 top-0 h-full w-1 transition-all duration-300 ${
          expanded
            ? "bg-gradient-to-b from-teal-500 via-cyan-500 to-teal-600"
            : "bg-gradient-to-b from-slate-300 to-slate-400 group-hover:from-teal-400 group-hover:to-cyan-500 dark:from-slate-600 dark:to-slate-500 dark:group-hover:from-teal-500 dark:group-hover:to-cyan-500"
        }`}
      />

      <div className="relative pl-5 pr-4 py-3">
        <button
          type="button"
          onClick={() => setExpanded((open) => !open)}
          aria-expanded={expanded}
          className="flex w-full items-center gap-3 text-left transition-opacity hover:opacity-90"
        >
          <div
            className={`flex h-9 w-9 shrink-0 items-center justify-center rounded-lg transition-all duration-300 ${
              expanded
                ? "bg-teal-500 text-white shadow-md shadow-teal-500/25"
                : "bg-teal-500/10 text-teal-600 group-hover:bg-teal-500/15 dark:bg-teal-500/20 dark:text-teal-400"
            }`}
          >
            <Compass className="h-4 w-4" aria-hidden="true" />
          </div>

          <div className="min-w-0 flex-1">
            <div className="flex flex-wrap items-center gap-x-2 gap-y-0.5">
              <span
                className={`text-xs font-bold uppercase tracking-wider transition-colors duration-300 ${
                  expanded ? "text-teal-700 dark:text-teal-300" : "text-slate-600 dark:text-slate-300"
                }`}
              >
                {title}
              </span>
              {!expanded && (
                <span className="text-[11px] font-normal text-slate-400 dark:text-slate-500">
                  Click to view analyst brief
                </span>
              )}
            </div>
          </div>

          <div
            className={`flex h-7 w-7 shrink-0 items-center justify-center rounded-full transition-all duration-300 ${
              expanded
                ? "bg-teal-500/10 text-teal-600 dark:bg-teal-500/20 dark:text-teal-400"
                : "bg-slate-200/60 text-slate-500 group-hover:bg-teal-500/10 group-hover:text-teal-600 dark:bg-slate-700/60 dark:text-slate-400 dark:group-hover:bg-teal-500/20 dark:group-hover:text-teal-400"
            }`}
          >
            <ChevronDown
              className={`h-4 w-4 transition-transform duration-300 ${expanded ? "rotate-180" : ""}`}
              aria-hidden="true"
            />
          </div>
        </button>

        {/* Smooth expand/collapse */}
        <div
          className={`grid transition-[grid-template-rows] duration-300 ease-in-out ${
            expanded ? "grid-rows-[1fr]" : "grid-rows-[0fr]"
          }`}
        >
          <div className="overflow-hidden">
            <div className="mt-3 border-t border-teal-200/40 pt-3 dark:border-teal-800/40">
              <p className="text-sm leading-relaxed text-slate-700 dark:text-slate-300">{description}</p>
            </div>
          </div>
        </div>
      </div>
    </section>
  );
}
