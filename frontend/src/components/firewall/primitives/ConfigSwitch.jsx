import { motion, useReducedMotion } from "motion/react";
import { cn } from "../../../lib/utils";

// Track is h-6 w-11 (24×44). Thumb is 16×16; on = 22px, off = 2px.
const THUMB_X_ON = 22;
const THUMB_X_OFF = 2;

export function ConfigSwitch({ checked, onCheckedChange, disabled, label, id }) {
  const reduceMotion = useReducedMotion();
  const on = !!checked;

  return (
    <button
      type="button"
      role="switch"
      id={id}
      aria-checked={on}
      aria-label={label}
      disabled={disabled}
      onClick={() => !disabled && onCheckedChange?.(!on)}
      className={cn(
        "relative inline-flex h-6 w-11 shrink-0 cursor-pointer items-center rounded-full border-2 border-transparent transition-colors",
        "focus:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background",
        "disabled:cursor-not-allowed disabled:opacity-50",
        on
          ? "bg-primary shadow-[0_0_12px_color-mix(in_oklch,var(--primary)_40%,transparent)]"
          : "bg-muted",
      )}
    >
      {/*
        Use animate.x — NOT layout + style.transform. Framer's layout prop
        owns transform for layout measurement and was pinning the thumb at
        the left while the track still showed the "on" color.
      */}
      <motion.span
        initial={false}
        animate={{ x: on ? THUMB_X_ON : THUMB_X_OFF }}
        transition={
          reduceMotion
            ? { duration: 0 }
            : { type: "spring", stiffness: 500, damping: 30 }
        }
        className="inline-block h-4 w-4 rounded-full bg-background shadow will-change-transform"
      />
    </button>
  );
}
