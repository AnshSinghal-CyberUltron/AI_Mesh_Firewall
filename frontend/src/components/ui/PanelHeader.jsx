import { cn } from "../../lib/utils";

export function PanelHeader({ icon: Icon, title, description, actions, className }) {
  return (
    <div className={cn("flex flex-wrap items-start justify-between gap-4", className)}>
      <div className="flex items-start gap-3">
        {Icon ? (
          <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl bg-teal-50 dark:bg-teal-900/30 text-teal-600 dark:text-teal-400 ring-1 ring-teal-100 dark:ring-teal-800/50">
            <Icon className="h-5 w-5" aria-hidden="true" />
          </div>
        ) : null}
        <div>
          <h2 className="text-lg font-semibold text-slate-900 dark:text-slate-100">{title}</h2>
          {description ? (
            <p className="mt-0.5 max-w-2xl text-sm text-slate-500 dark:text-slate-400">{description}</p>
          ) : null}
        </div>
      </div>
      {actions ? <div className="flex items-center gap-2">{actions}</div> : null}
    </div>
  );
}
