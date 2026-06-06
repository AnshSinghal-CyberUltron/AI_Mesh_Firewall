import { motion } from "motion/react";
import { cn } from "../../../lib/utils";

export function ChipMultiSelect({ options, value = [], onChange }) {
  return (
    <div className="flex flex-wrap gap-2">
      {options.map((opt) => {
        const active = value.includes(opt);
        return (
          <motion.button
            key={opt}
            type="button"
            whileTap={{ scale: 0.94 }}
            whileHover={{ y: -1 }}
            onClick={() => onChange(active ? value.filter((v) => v !== opt) : [...value, opt])}
            aria-pressed={active}
            className={cn(
              "rounded-md border px-3 py-1.5 font-mono text-xs font-medium transition-all",
              active
                ? "border-primary/40 bg-primary/10 text-primary shadow-[0_0_0_1px_var(--color-primary)/10]"
                : "border-border bg-surface-2 text-muted-foreground hover:border-border hover:bg-accent hover:text-accent-foreground",
            )}
          >
            <span
              className={cn(
                "mr-1.5 inline-block h-1.5 w-1.5 rounded-full transition-colors",
                active ? "bg-primary" : "bg-muted-foreground/40",
              )}
            />
            {opt}
          </motion.button>
        );
      })}
    </div>
  );
}
