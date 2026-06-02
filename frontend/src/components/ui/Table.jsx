import { cn } from "../../lib/utils";

export function Table({ children, className }) {
  return (
    <div className="overflow-x-auto">
      <table className={cn("w-full border-collapse text-left text-sm", className)}>{children}</table>
    </div>
  );
}

export function THead({ children }) {
  return (
    <thead className="text-[11px] uppercase tracking-wide text-slate-500 dark:text-slate-400">
      {children}
    </thead>
  );
}

export function TBody({ children }) {
  return <tbody className="divide-y divide-slate-100 dark:divide-slate-800">{children}</tbody>;
}

export function TR({ children, className, ...props }) {
  return (
    <tr className={cn("transition-colors", className)} {...props}>
      {children}
    </tr>
  );
}

export function TH({ children, className }) {
  return <th className={cn("px-3 py-2 font-semibold", className)}>{children}</th>;
}

export function TD({ children, className, ...props }) {
  return (
    <td className={cn("px-3 py-2.5 align-middle text-slate-700 dark:text-slate-300", className)} {...props}>
      {children}
    </td>
  );
}
