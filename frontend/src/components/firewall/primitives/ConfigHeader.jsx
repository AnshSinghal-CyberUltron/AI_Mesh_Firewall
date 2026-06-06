import { motion, useReducedMotion } from "motion/react";
import { Settings, CheckCircle2, AlertCircle } from "lucide-react";
import { SaveBar } from "./SaveBar";

/**
 * Pinned page header for the Firewall Configuration surface.
 *
 * Renders as a TRUE sticky header bar (not a floating card): it breaks out of the
 * page container + the scroll-region (<main>) horizontal padding via negative
 * margins so it spans the full content width, pins flush to the top of the scroll
 * area, and uses an opaque, blurred surface with a hairline bottom border so the
 * content scrolls cleanly underneath. Inner content is re-centered to max-w-5xl to
 * stay aligned with the section cards below.
 *
 * The negative margins mirror <main>'s padding in DashboardLayout
 * (px-3 py-4 sm:px-4 sm:py-5 md:px-6 lg:px-8).
 */
export function ConfigHeader({
  isDirty,
  isSaving,
  saveSuccess,
  error,
  updatedAt,
  onReset,
  onSave,
}) {
  const reduceMotion = useReducedMotion();

  return (
    <header
      className="sticky -top-4 z-30 -mx-3 -mt-4 border-b border-border bg-card/95 px-3 shadow-sm backdrop-blur-md supports-[backdrop-filter]:bg-card/85 sm:-top-5 sm:-mx-4 sm:-mt-5 sm:px-4 md:-mx-6 md:px-6 lg:-mx-8 lg:px-8"
    >
      <div className="mx-auto max-w-5xl">
        <div className="flex flex-col gap-3 py-3 sm:flex-row sm:items-center sm:justify-between sm:gap-4">
          {/* Title block */}
          <div className="flex min-w-0 items-center gap-3">
            <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-lg bg-primary text-primary-foreground shadow-glow">
              <Settings className="h-5 w-5" strokeWidth={2} aria-hidden />
            </div>
            <div className="min-w-0">
              <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
                <h1 className="text-base font-semibold tracking-tight text-foreground sm:text-lg">
                  Firewall Configuration & Policy Settings
                </h1>
                <span className="inline-flex shrink-0 items-center gap-1.5 rounded-full border border-primary/30 bg-primary/10 px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-primary">
                  Configuration Mode
                </span>
              </div>
              <p className="mt-0.5 hidden truncate text-xs text-muted-foreground sm:block">
                Configure enforcement policies, thresholds, and security controls for AI Mesh Firewall
                {updatedAt && (
                  <span className="ml-2 text-muted-foreground/70">
                    · Last saved {new Date(updatedAt).toLocaleString()}
                  </span>
                )}
              </p>
            </div>
          </div>

          {/* Save controls */}
          <div className="shrink-0">
            <SaveBar isDirty={isDirty} isSaving={isSaving} onReset={onReset} onSave={onSave} />
          </div>
        </div>

        {/* Inline status banners (stay pinned with the header) */}
        {saveSuccess && (
          <motion.div
            initial={reduceMotion ? false : { opacity: 0, height: 0 }}
            animate={{ opacity: 1, height: "auto" }}
            className="overflow-hidden pb-3"
          >
            <div className="flex items-center gap-2 rounded-lg border border-success/30 bg-success/10 px-3 py-2 text-sm text-success">
              <CheckCircle2 className="h-4 w-4 shrink-0" aria-hidden />
              Configuration saved successfully — active via Redis hot-reload
            </div>
          </motion.div>
        )}

        {error && (
          <div className="pb-3">
            <div className="flex items-start gap-2 rounded-lg border border-destructive/30 bg-destructive/10 px-3 py-2 text-sm text-destructive">
              <AlertCircle className="mt-0.5 h-4 w-4 shrink-0" aria-hidden />
              <span>{error}</span>
            </div>
          </div>
        )}
      </div>
    </header>
  );
}
