import { cn } from "../../lib/utils";

export function EmptyState({ icon: Icon, title, description, action, className }) {
  return (
    <div className={cn("flex flex-col items-center justify-center px-6 py-12 text-center", className)}>
      {Icon ? (
        <div className="mb-3 flex h-12 w-12 items-center justify-center rounded-2xl bg-slate-100 dark:bg-slate-800 text-slate-500 dark:text-slate-400">
          <Icon className="h-6 w-6" aria-hidden="true" />
        </div>
      ) : null}
      <h3 className="text-sm font-semibold text-slate-700 dark:text-slate-200">{title}</h3>
      {description ? (
        <p className="mt-1 max-w-sm text-sm text-slate-500 dark:text-slate-400">{description}</p>
      ) : null}
      {action ? <div className="mt-4">{action}</div> : null}
    </div>
  );
}
