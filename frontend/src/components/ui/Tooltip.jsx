import { useState, useId } from "react";
import { cn } from "../../lib/utils";

export function Tooltip({ content, children, side = "top", className }) {
  const [open, setOpen] = useState(false);
  const id = useId();
  return (
    <span
      className="relative inline-flex"
      onMouseEnter={() => setOpen(true)}
      onMouseLeave={() => setOpen(false)}
      onFocus={() => setOpen(true)}
      onBlur={() => setOpen(false)}
    >
      <span aria-describedby={open ? id : undefined}>{children}</span>
      {open && content ? (
        <span
          role="tooltip"
          id={id}
          className={cn(
            "pointer-events-none absolute z-50 w-max max-w-[16rem] whitespace-normal rounded-lg bg-slate-900 dark:bg-slate-700 px-2.5 py-1.5 text-xs font-medium text-white shadow-lg",
            side === "top" && "bottom-full left-1/2 -translate-x-1/2 mb-1.5",
            side === "bottom" && "top-full left-1/2 -translate-x-1/2 mt-1.5",
            side === "right" && "left-full top-1/2 -translate-y-1/2 ml-1.5",
            className
          )}
        >
          {content}
        </span>
      ) : null}
    </span>
  );
}

export function InfoHint({ content, className }) {
  return (
    <Tooltip content={content}>
      <span
        tabIndex={0}
        aria-label="More information"
        className={cn(
          "inline-flex h-3.5 w-3.5 items-center justify-center rounded-full bg-slate-200 dark:bg-slate-700 text-[10px] font-bold text-slate-500 dark:text-slate-300 cursor-help focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-teal-500",
          className
        )}
      >
        ?
      </span>
    </Tooltip>
  );
}
