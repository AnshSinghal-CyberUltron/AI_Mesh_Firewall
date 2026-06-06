import { cn } from "../../../lib/utils";

export function FieldRow({
  label,
  hint,
  description,
  htmlFor,
  control,
  children,
  align = "between",
  className,
}) {
  const hintText = hint ?? description;
  const content = control ?? children;

  if (align === "stack") {
    return (
      <div className={cn("space-y-2", className)}>
        <div>
          <label htmlFor={htmlFor} className="text-sm font-medium text-foreground">
            {label}
          </label>
          {hintText && <p className="mt-0.5 text-xs text-muted-foreground">{hintText}</p>}
        </div>
        {content}
      </div>
    );
  }

  return (
    <div
      className={cn(
        "flex flex-wrap items-center justify-between gap-4 rounded-lg border border-border/60 bg-surface-2/50 px-4 py-3 transition-colors hover:bg-surface-2",
        className,
      )}
    >
      <div className="min-w-0 flex-1">
        <label htmlFor={htmlFor} className="text-sm font-medium text-foreground">
          {label}
        </label>
        {hintText && <p className="mt-0.5 text-xs text-muted-foreground">{hintText}</p>}
      </div>
      <div className="shrink-0">{content}</div>
    </div>
  );
}

/** @deprecated Use FieldRow with align="stack" */
export function FieldStack({ label, description, hint, children, htmlFor, className }) {
  return (
    <FieldRow
      align="stack"
      label={label}
      hint={hint ?? description}
      htmlFor={htmlFor}
      control={children}
      className={className}
    />
  );
}

export function InfoStrip({ children, className }) {
  return (
    <div
      className={cn(
        "flex items-start gap-2 rounded-lg border border-primary/20 bg-primary/5 px-3 py-2 text-xs text-muted-foreground",
        className,
      )}
    >
      <span className="mt-0.5 inline-block h-1.5 w-1.5 shrink-0 rounded-full bg-primary" />
      <span className="leading-relaxed">{children}</span>
    </div>
  );
}
