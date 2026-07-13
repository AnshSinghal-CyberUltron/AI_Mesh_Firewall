import { routingHasTechnicalDetails } from "../utils/routingExplain";

/**
 * Collapsible raw routing metadata for auditors / power users.
 */
export function RoutingTechnicalDetails({ technical, className = "" }) {
  if (!routingHasTechnicalDetails(technical)) return null;

  const factors = Array.isArray(technical.decision_factors) ? technical.decision_factors : [];
  const weights = technical.weights && typeof technical.weights === "object" ? technical.weights : {};
  const weightEntries = Object.entries(weights);
  const fallbacks = Array.isArray(technical.fallback_chain) ? technical.fallback_chain : [];

  return (
    <details className={`group mt-3 rounded-lg border border-slate-200 bg-slate-50/80 dark:border-slate-700 dark:bg-slate-800/50 ${className}`}>
      <summary className="cursor-pointer list-none px-3 py-2 text-[11px] font-semibold uppercase tracking-wide text-slate-500 marker:content-none dark:text-slate-400 [&::-webkit-details-marker]:hidden">
        <span className="group-open:hidden">Show technical details</span>
        <span className="hidden group-open:inline">Hide technical details</span>
      </summary>
      <div className="space-y-2 border-t border-slate-200 px-3 py-2 text-xs text-slate-600 dark:border-slate-700 dark:text-slate-300">
        {technical.routing_reason && (
          <div>
            <span className="font-medium text-slate-500 dark:text-slate-400">Routing reason: </span>
            <span className="whitespace-pre-wrap">{technical.routing_reason}</span>
          </div>
        )}
        {technical.policy_summary && (
          <div>
            <span className="font-medium text-slate-500 dark:text-slate-400">Policy: </span>
            <span>{technical.policy_summary}</span>
          </div>
        )}
        {factors.length > 0 && (
          <div>
            <span className="font-medium text-slate-500 dark:text-slate-400">Decision factors: </span>
            <span className="font-mono text-[11px]">{factors.join(", ")}</span>
          </div>
        )}
        {weightEntries.length > 0 && (
          <div>
            <span className="font-medium text-slate-500 dark:text-slate-400">Weights: </span>
            <span>
              {weightEntries.map(([k, v]) => {
                const raw = Number(v);
                const pct = Number.isFinite(raw) ? `${Math.round(raw * 100)}%` : String(v);
                return `${k}=${pct}`;
              }).join(", ")}
            </span>
          </div>
        )}
        {Number(technical.routing_score) > 0 && (
          <div>
            <span className="font-medium text-slate-500 dark:text-slate-400">Score: </span>
            <span>{Number(technical.routing_score).toFixed(3)}</span>
          </div>
        )}
        {Number(technical.candidate_count) > 0 && (
          <div>
            <span className="font-medium text-slate-500 dark:text-slate-400">Candidates: </span>
            <span>{technical.candidate_count}</span>
          </div>
        )}
        {fallbacks.length > 0 && (
          <div>
            <span className="font-medium text-slate-500 dark:text-slate-400">Fallback chain: </span>
            <span className="font-mono text-[11px]">{fallbacks.join(" → ")}</span>
          </div>
        )}
        {technical.guard_reason && technical.guard_reason !== technical.routing_reason && (
          <div>
            <span className="font-medium text-slate-500 dark:text-slate-400">Guard trace: </span>
            <pre className="mt-1 whitespace-pre-wrap font-mono text-[11px]">{technical.guard_reason}</pre>
          </div>
        )}
      </div>
    </details>
  );
}
