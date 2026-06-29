import { motion, useReducedMotion } from "motion/react";
import { cn } from "../../../lib/utils";

export function ConfigSwitch({ checked, onCheckedChange, disabled, label, id }) {
  const reduceMotion = useReducedMotion();

  return (
    <button
      type="button"
      role="switch"
      id={id}
      aria-checked={checked}
      aria-label={label}
      disabled={disabled}
      onClick={() => !disabled && onCheckedChange?.(!checked)}
      className={cn(
        "relative inline-flex h-6 w-11 shrink-0 cursor-pointer items-center rounded-full border-2 border-transparent transition-colors",
        "focus:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background",
        "disabled:cursor-not-allowed disabled:opacity-50",
        checked ? "bg-primary shadow-[0_0_12px_color-mix(in_oklch,var(--primary)_40%,transparent)]" : "bg-muted",
      )}
    >
      <motion.span
        layout={!reduceMotion}
        transition={{ type: "spring", stiffness: 500, damping: 30 }}
        className="inline-block h-4 w-4 rounded-full bg-background shadow"
        style={{ transform: checked ? "translateX(22px)" : "translateX(2px)" }}
      />
    </button>
  );
}
