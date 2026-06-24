import { Link } from "react-router-dom";
import { Loader2 } from "lucide-react";
import { Module2EmptyState } from "./PageStates";

export function UebaLearningKeysTable({ rows, loading, onSelectKey }) {
  if (loading) {
    return (
      <div className="flex justify-center py-16">
        <Loader2 className="h-8 w-8 animate-spin text-teal-500" />
      </div>
    );
  }

  if (!rows?.length) {
    return (
      <Module2EmptyState
        title="No keys in learning mode"
        message="All active API keys have graduated to active UEBA scoring, or no keys are registered yet."
      />
    );
  }

  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b border-slate-200 bg-slate-50/80 text-left text-xs uppercase text-slate-500 dark:border-slate-700 dark:bg-slate-900/40">
            <th className="px-4 py-2.5">Key</th>
            <th className="px-4 py-2.5">Lifetime req</th>
            <th className="px-4 py-2.5">Days</th>
            <th className="px-4 py-2.5">Progress</th>
            <th className="px-4 py-2.5">Traditional score</th>
            <th className="px-4 py-2.5">Graduation</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => {
            const pct = row.graduation_progress?.pct_complete ?? 0;
            const progress = row.graduation_progress || {};
            const keyLink = `/ueba/api-keys?key_id=${encodeURIComponent(row.key_id)}`;

            return (
              <tr key={row.key_id} className="border-b border-slate-100 dark:border-slate-700/50">
                <td className="px-4 py-3">
                  {onSelectKey ? (
                    <button
                      type="button"
                      onClick={() => onSelectKey(row.key_id)}
                      className="font-mono text-xs text-teal-700 hover:underline dark:text-teal-300"
                    >
                      {row.prefix}
                    </button>
                  ) : (
                    <Link to={keyLink} className="font-mono text-xs text-teal-700 hover:underline dark:text-teal-300">
                      {row.prefix}
                    </Link>
                  )}
                </td>
                <td className="px-4 py-3">{row.lifetime_requests ?? 0}</td>
                <td className="px-4 py-3">{row.days_since_created ?? "—"}</td>
                <td className="px-4 py-3">
                  <div className="h-2.5 w-40 overflow-hidden rounded-full bg-slate-200 dark:bg-slate-700">
                    <div className="h-full rounded-full bg-teal-500" style={{ width: `${Math.min(pct, 100)}%` }} />
                  </div>
                  <span className="text-[11px] text-slate-500">{pct}%</span>
                </td>
                <td className="px-4 py-3">{row.traditional_score ?? "—"}</td>
                <td className="px-4 py-3 text-xs text-slate-500">
                  {progress.graduated ? (
                    <span className="font-medium text-emerald-600 dark:text-emerald-400">Ready to graduate</span>
                  ) : (
                    <>
                      {progress.requests ?? 0}/{progress.thresholds?.requests ?? "—"} req
                      {" · "}
                      {progress.days ?? "—"}/{progress.thresholds?.days ?? "—"} days
                    </>
                  )}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
