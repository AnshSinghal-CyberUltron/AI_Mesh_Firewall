import { cn } from "../../lib/utils";

/**
 * Native range slider with shadcn-style track/fill — no Radix dependency (Docker-safe).
 * API matches Radix: value={[n]}, onValueChange={(v) => ...}, min, max, step.
 */
export function Slider({
  className,
  value,
  onValueChange,
  min = 0,
  max = 100,
  step = 1,
  disabled,
  ...props
}) {
  const current = Array.isArray(value) ? value[0] : value;
  const pct = max === min ? 0 : ((current - min) / (max - min)) * 100;

  return (
    <div className={cn("relative flex w-full touch-none select-none items-center", className)}>
      <div className="relative h-1.5 w-full grow overflow-hidden rounded-full bg-muted">
        <div
          className="absolute h-full rounded-full bg-primary transition-all"
          style={{ width: `${pct}%` }}
        />
        <input
          type="range"
          min={min}
          max={max}
          step={step}
          value={current}
          disabled={disabled}
          onChange={(e) => onValueChange?.([Number(e.target.value)])}
          className="absolute inset-0 h-full w-full cursor-pointer opacity-0 disabled:cursor-not-allowed"
          aria-valuemin={min}
          aria-valuemax={max}
          aria-valuenow={current}
          {...props}
        />
      </div>
      <span
        className="pointer-events-none absolute block h-4 w-4 rounded-full border-2 border-primary bg-background shadow transition-[left]"
        style={{ left: `calc(${pct}% - 8px)` }}
        aria-hidden
      />
    </div>
  );
}
