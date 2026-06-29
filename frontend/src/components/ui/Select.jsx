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
          "w-full appearance-none rounded-md border border-input bg-background pl-3 pr-9 py-2 text-sm text-foreground transition-colors",
          "focus:outline-none focus:ring-2 focus:ring-ring focus:ring-offset-2 focus:ring-offset-background disabled:opacity-50 disabled:cursor-not-allowed",
          className
        )}
        {...props}
      >
        {children}
      </select>
      <ChevronDown
        className="pointer-events-none absolute right-2.5 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground"
        aria-hidden="true"
      />
    </div>
  );
}
