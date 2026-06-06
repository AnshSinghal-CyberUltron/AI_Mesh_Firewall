import { motion, useReducedMotion } from "motion/react";
import { cn } from "../../../lib/utils";

export function SectionCard({
  id,
  title,
  description,
  icon: Icon,
  children,
  index = 0,
  action,
  flushBody = false,
  className,
}) {
  const reduceMotion = useReducedMotion();

  return (
    <motion.section
      id={id}
      initial={reduceMotion ? false : { opacity: 0, y: 16 }}
      whileInView={{ opacity: 1, y: 0 }}
      viewport={{ once: true, margin: "-80px" }}
      transition={{ duration: 0.4, delay: Math.min(index * 0.04, 0.2), ease: [0.22, 1, 0.36, 1] }}
      className="scroll-mt-28"
    >
      <motion.div
        whileHover={reduceMotion ? undefined : { y: -2 }}
        transition={{ type: "spring", stiffness: 400, damping: 30 }}
        className={cn(
          "group relative overflow-hidden rounded-xl border border-border bg-card bg-card-glow shadow-elegant transition-shadow hover:shadow-glow",
          className,
        )}
      >
        <div className="flex flex-wrap items-start justify-between gap-4 border-b border-border/60 p-5 sm:p-6">
          <div className="flex items-start gap-3">
            {Icon && (
              <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-accent text-accent-foreground ring-1 ring-border/60 transition-transform group-hover:scale-105">
                <Icon className="h-4 w-4" strokeWidth={2} aria-hidden />
              </div>
            )}
            <div>
              <h2 className="text-base font-semibold tracking-tight text-foreground">{title}</h2>
              {description && <p className="mt-1 text-sm text-muted-foreground">{description}</p>}
            </div>
          </div>
          {action}
        </div>
        <div className={cn(!flushBody && "p-5 sm:p-6")}>{children}</div>
      </motion.div>
    </motion.section>
  );
}
