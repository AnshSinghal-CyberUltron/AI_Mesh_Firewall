import { useState } from "react";
import { BookOpen, ChevronDown, ChevronRight } from "lucide-react";
import { UEBA_MODES_GUIDE } from "../../pages/module2/pageCopy";

const GUIDE_SECTIONS = [
  { id: "learning", ...UEBA_MODES_GUIDE.learningMode, tone: "amber" },
  { id: "active", ...UEBA_MODES_GUIDE.activeMode, tone: "teal" },
  { id: "baseline", ...UEBA_MODES_GUIDE.behaviorBaseline, tone: "sky" },
  { id: "deviation", ...UEBA_MODES_GUIDE.deviation, tone: "violet" },
];

const TONE_STYLES = {
  amber: "border-amber-200 bg-amber-50/60 dark:border-amber-900/50 dark:bg-amber-950/20",
  teal: "border-teal-200 bg-teal-50/60 dark:border-teal-900/50 dark:bg-teal-950/20",
  sky: "border-sky-200 bg-sky-50/60 dark:border-sky-900/50 dark:bg-sky-950/20",
  violet: "border-violet-200 bg-violet-50/60 dark:border-violet-900/50 dark:bg-violet-950/20",
};

export function UebaModesGuide({ defaultExpanded = true, className = "mb-6" }) {
  const [open, setOpen] = useState(defaultExpanded);

  return (
    <section className={`rounded-xl border border-slate-200 bg-white shadow-sm dark:border-slate-700 dark:bg-slate-800/60 ${className}`}>
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="flex w-full items-center justify-between gap-2 px-4 py-3 text-left"
        aria-expanded={open}
      >
        <div className="flex items-center gap-2">
          <span className="flex h-8 w-8 items-center justify-center rounded-lg bg-slate-100 dark:bg-slate-900/60">
            <BookOpen className="h-4 w-4 text-teal-600 dark:text-teal-400" />
          </span>
          <div>
            <h3 className="text-sm font-semibold text-slate-800 dark:text-slate-100">{UEBA_MODES_GUIDE.title}</h3>
            <p className="text-xs text-slate-500 dark:text-slate-400">Learning vs active mode, baselines, and deviation</p>
          </div>
        </div>
        {open ? <ChevronDown className="h-4 w-4 text-slate-400" /> : <ChevronRight className="h-4 w-4 text-slate-400" />}
      </button>

      {open && (
        <div className="border-t border-slate-200 px-4 py-4 dark:border-slate-700">
          <p className="mb-4 text-sm leading-relaxed text-slate-600 dark:text-slate-300">{UEBA_MODES_GUIDE.intro}</p>
          <div className="grid gap-3 lg:grid-cols-2">
            {GUIDE_SECTIONS.map((section) => (
              <article
                key={section.id}
                className={`rounded-lg border p-3 ${TONE_STYLES[section.tone] || TONE_STYLES.teal}`}
              >
                <h4 className="text-xs font-bold uppercase tracking-wide text-slate-700 dark:text-slate-200">
                  {section.title}
                </h4>
                <p className="mt-1.5 text-xs leading-relaxed text-slate-600 dark:text-slate-300">{section.body}</p>
              </article>
            ))}
          </div>
          <p className="mt-4 rounded-lg border border-slate-100 bg-slate-50 px-3 py-2 text-xs text-slate-500 dark:border-slate-700 dark:bg-slate-900/40 dark:text-slate-400">
            {UEBA_MODES_GUIDE.graduationNote}
          </p>
        </div>
      )}
    </section>
  );
}
