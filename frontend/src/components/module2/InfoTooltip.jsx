import { CircleHelp } from "lucide-react";

export function InfoTooltip({ text, className = "" }) {
  if (!text) return null;

  return (
    <span className={`group relative inline-flex ${className}`}>
      <CircleHelp
        className="h-3.5 w-3.5 cursor-help text-slate-400 transition-colors group-hover:text-slate-600 dark:text-slate-500 dark:group-hover:text-slate-300"
        aria-hidden="true"
      />
      <span
        role="tooltip"
        className="pointer-events-none absolute left-1/2 top-full z-30 mt-2 w-64 -translate-x-1/2 rounded-lg bg-slate-900 px-3 py-2 text-xs font-normal leading-relaxed text-slate-100 opacity-0 shadow-lg transition-opacity duration-150 group-hover:opacity-100"
      >
        {text}
      </span>
    </span>
  );
}
