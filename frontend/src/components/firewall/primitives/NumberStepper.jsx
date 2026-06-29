import { ChevronDown, ChevronUp } from "lucide-react";
import { cn } from "../../../lib/utils";

export function NumberStepper({
  id,
  value,
  onChange,
  unit,
  min = 0,
  max,
  step = 1,
  width = "w-32",
}) {
  return (
    <div className={cn("flex items-center overflow-hidden rounded-md border border-input bg-background", width)}>
      <input
        id={id}
        type="number"
        value={value}
        min={min}
        max={max}
        step={step}
        onChange={(e) => onChange(Number(e.target.value))}
        className="w-full bg-transparent px-3 py-2 font-mono text-sm tabular-nums outline-none"
      />
      {unit && (
        <span className="border-l border-border bg-muted px-2 py-2 text-xs font-mono text-muted-foreground">
          {unit}
        </span>
      )}
      <div className="flex flex-col border-l border-border">
        <button
          type="button"
          onClick={() => onChange(max != null ? Math.min(max, value + step) : value + step)}
          className="px-1.5 py-0.5 text-muted-foreground transition-colors hover:bg-accent hover:text-accent-foreground"
        >
          <ChevronUp className="h-3 w-3" />
        </button>
        <button
          type="button"
          onClick={() => onChange(Math.max(min, value - step))}
          className="border-t border-border px-1.5 py-0.5 text-muted-foreground transition-colors hover:bg-accent hover:text-accent-foreground"
        >
          <ChevronDown className="h-3 w-3" />
        </button>
      </div>
    </div>
  );
}
