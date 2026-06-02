import { createContext, useContext } from "react";
import { cn } from "../../lib/utils";

const TabsCtx = createContext(null);

export function Tabs({ value, onValueChange, children, className }) {
  return (
    <TabsCtx.Provider value={{ value, onValueChange }}>
      <div className={cn("flex flex-col", className)}>{children}</div>
    </TabsCtx.Provider>
  );
}

export function TabsList({ children, className }) {
  return (
    <div
      role="tablist"
      className={cn(
        "flex items-center gap-1 overflow-x-auto border-b border-slate-200 dark:border-slate-700",
        className
      )}
    >
      {children}
    </div>
  );
}

export function TabsTrigger({ value, icon: Icon, count, children, className }) {
  const ctx = useContext(TabsCtx);
  const active = ctx?.value === value;
  return (
    <button
      type="button"
      role="tab"
      aria-selected={active}
      tabIndex={active ? 0 : -1}
      onClick={() => ctx?.onValueChange(value)}
      className={cn(
        "inline-flex items-center gap-2 whitespace-nowrap border-b-2 px-3.5 py-2.5 text-sm font-medium -mb-px transition-colors rounded-t-md focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-teal-500",
        active
          ? "border-teal-600 text-teal-700 dark:text-teal-400"
          : "border-transparent text-slate-500 dark:text-slate-400 hover:text-slate-800 dark:hover:text-slate-200 hover:border-slate-300 dark:hover:border-slate-600",
        className
      )}
    >
      {Icon ? <Icon className="w-4 h-4" aria-hidden="true" /> : null}
      {children}
      {count != null ? (
        <span
          className={cn(
            "ml-1 inline-flex items-center justify-center rounded-full px-1.5 min-w-[1.25rem] h-5 text-[11px] font-semibold",
            active
              ? "bg-teal-600 text-white"
              : "bg-slate-200 dark:bg-slate-700 text-slate-600 dark:text-slate-300"
          )}
        >
          {count}
        </span>
      ) : null}
    </button>
  );
}

export function TabsContent({ value, children, className }) {
  const ctx = useContext(TabsCtx);
  if (ctx?.value !== value) return null;
  return (
    <div role="tabpanel" tabIndex={0} className={cn("focus-visible:outline-none", className)}>
      {children}
    </div>
  );
}
