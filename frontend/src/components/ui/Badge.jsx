import { cn } from "../../lib/utils";

const badgeVariants = {
  default: "bg-teal-600 text-white border-transparent",
  secondary: "bg-slate-100 dark:bg-slate-700 text-slate-900 dark:text-slate-100 border-transparent",
  destructive: "bg-red-500 text-white border-transparent",
  outline: "text-slate-700 dark:text-slate-300 border-slate-200 dark:border-slate-700 dark:border-slate-600",
  success: "bg-teal-100 dark:bg-teal-800/30 dark:bg-teal-900/50 text-teal-700 dark:text-teal-400 border-teal-200 dark:border-teal-800",
  warning: "bg-amber-100 dark:bg-amber-800/30 dark:bg-amber-900/50 text-amber-700 dark:text-amber-400 border-amber-200 dark:border-amber-800",
  danger: "bg-red-100 dark:bg-red-800/30 dark:bg-red-900/50 text-red-700 dark:text-red-400 border-red-200 dark:border-red-800",
  info: "bg-blue-100 dark:bg-blue-800/30 dark:bg-blue-900/50 text-blue-700 dark:text-blue-400 border-blue-200 dark:border-blue-800",
};

export function Badge({ className, variant = "default", children, ...props }) {
  return (
    <span
      className={cn(
        "inline-flex items-center justify-center rounded-md border px-2 py-0.5 text-xs font-medium whitespace-nowrap transition-colors",
        badgeVariants[variant],
        className
      )}
      {...props}
    >
      {children}
    </span>
  );
}

