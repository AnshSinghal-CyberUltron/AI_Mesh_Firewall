import { Building2, KeyRound, Info } from "lucide-react";
import { useAuth } from "../context/AuthContext";

/**
 * Surfaces org slug + API contract reminders on Module 1.6.
 * Reason: live triage showed zero-shield vs zeroshield key mismatch and
 * model_name vs LiteLLM model_id confusion as the top false-positive bugs.
 */
export function OrgIsolationBanner() {
  const { user } = useAuth();
  const slug = user?.organization?.slug || "";
  const orgName = user?.organization?.name || "your organization";

  if (!slug) {
    return (
      <div
        className="rounded-2xl border border-amber-500/40 bg-amber-500/10 px-4 py-3 text-sm text-amber-900 dark:text-amber-200"
        role="alert"
      >
        <p className="font-medium">No organization linked to your account.</p>
        <p className="mt-1 text-xs opacity-90">
          Gateway keys and model isolation are org-scoped. Contact an admin to assign your profile.
        </p>
      </div>
    );
  }

  return (
    <div className="rounded-2xl border border-teal-500/30 bg-teal-500/5 px-4 py-3">
      <div className="flex flex-wrap items-start gap-3">
        <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl bg-teal-500/15 text-teal-700 dark:text-teal-300">
          <Building2 className="h-5 w-5" aria-hidden />
        </div>
        <div className="min-w-0 flex-1 space-y-2">
          <div className="flex flex-wrap items-center gap-2">
            <span className="text-sm font-semibold text-slate-900 dark:text-slate-100">
              Organization: {orgName}
            </span>
            <code className="rounded-md bg-slate-900/5 px-2 py-0.5 text-xs font-mono text-teal-800 dark:bg-slate-950/40 dark:text-teal-300">
              {slug}
            </code>
          </div>
          <ul className="space-y-1 text-xs leading-relaxed text-slate-600 dark:text-slate-400">
            <li className="flex items-start gap-2">
              <KeyRound className="mt-0.5 h-3.5 w-3.5 shrink-0 text-teal-600 dark:text-teal-400" aria-hidden />
              <span>
                Use a <strong className="font-medium text-slate-800 dark:text-slate-200">gateway API key</strong> issued
                for this org in the panel below. Keys from another org will not see your registered models.
              </span>
            </li>
            <li className="flex items-start gap-2">
              <Info className="mt-0.5 h-3.5 w-3.5 shrink-0 text-teal-600 dark:text-teal-400" aria-hidden />
              <span>
                In <code className="font-mono text-[11px]">/v1/chat/completions</code>, set{" "}
                <code className="font-mono text-[11px]">model</code> to the registered{" "}
                <strong className="font-medium text-slate-800 dark:text-slate-200">model_name</strong> (e.g.{" "}
                <code className="font-mono text-[11px]">live-triage-openai</code>), not the LiteLLM{" "}
                <code className="font-mono text-[11px]">model_id</code>.
              </span>
            </li>
          </ul>
        </div>
      </div>
    </div>
  );
}
