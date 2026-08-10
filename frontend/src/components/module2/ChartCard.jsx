import { InfoTooltip } from "./InfoTooltip";

export function ChartCard({ title, titleHelpText, children, className = "" }) {
  return (
    <div
      className={`rounded-xl border border-slate-200 bg-white p-4 shadow-sm dark:border-slate-700 dark:bg-slate-800/60 ${className}`}
    >
      {title && (
        <h3 className="mb-3 flex items-center gap-1.5 text-sm font-semibold text-slate-700 dark:text-slate-200">
          <span>{title}</span>
          <InfoTooltip text={titleHelpText} />
        </h3>
      )}
      {children}
    </div>
  );
}
