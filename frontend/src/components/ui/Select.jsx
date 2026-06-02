import { ChevronDown } from "lucide-react";
import { cn } from "../../lib/utils";

/** Styled wrapper around a native <select> — keeps full keyboard/a11y behaviour. */
export function Select({ value, onChange, children, className, "aria-label": ariaLabel, ...props }) {
  return (
    <div className="relative inline-flex w-full">
      <select
        value={value}
        onChange={onChange}
        aria-label={ariaLabel}
        className={cn(
          "w-full appearance-none rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-800 text-slate-800 dark:text-slate-100 text-sm pl-3 pr-9 py-2 transition-colors focus:outline-none focus:ring-2 focus:ring-teal-500 disabled:opacity-50 disabled:cursor-not-allowed",
          className
        )}
        {...props}
      >
        {children}
      </select>
      <ChevronDown
        className="pointer-events-none absolute right-2.5 top-1/2 -translate-y-1/2 w-4 h-4 text-slate-400"
        aria-hidden="true"
      />
    </div>
  );
}
