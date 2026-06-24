import { Link } from "react-router-dom";
import { ChevronRight, GraduationCap } from "lucide-react";

export function UebaLearningNavCard({ learningCount }) {
  return (
    <Link
      to="/ueba/api-keys/learning"
      className="mt-6 flex items-center justify-between gap-3 rounded-xl border border-slate-200 bg-white px-4 py-3 shadow-sm transition hover:border-teal-300 hover:bg-teal-50/50 dark:border-slate-700 dark:bg-slate-800/60 dark:hover:border-teal-700 dark:hover:bg-teal-950/20"
    >
      <div className="flex items-start gap-3">
        <span className="mt-0.5 flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-teal-100 dark:bg-teal-900/40">
          <GraduationCap className="h-4 w-4 text-teal-700 dark:text-teal-300" />
        </span>
        <div>
          <p className="text-sm font-semibold text-slate-800 dark:text-slate-100">
            View learning mode keys &amp; settings
          </p>
          <p className="mt-0.5 text-xs text-slate-500 dark:text-slate-400">
            Org graduation thresholds, mode guide, and baseline progress for keys still in learning mode
            {learningCount > 0 && (
              <span className="ml-1 font-medium text-teal-700 dark:text-teal-300">
                · {learningCount} in learning
              </span>
            )}
          </p>
        </div>
      </div>
      <ChevronRight className="h-5 w-5 shrink-0 text-slate-400" />
    </Link>
  );
}
