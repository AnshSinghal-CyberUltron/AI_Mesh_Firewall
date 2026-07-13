import { inputScanHasTechnicalDetails } from "../utils/inputScanExplain";

/**
 * Collapsible raw input-scan metadata for auditors / power users.
 */
export function InputScanTechnicalDetails({ technical, className = "" }) {
  if (!technical || !inputScanHasTechnicalDetails(technical)) return null;

  const patterns = Array.isArray(technical.matched_patterns) ? technical.matched_patterns : [];
  const findings = Array.isArray(technical.guard_findings) ? technical.guard_findings : [];

  return (
    <details className={`group mt-3 rounded-lg border border-slate-200 bg-slate-50/80 dark:border-slate-700 dark:bg-slate-800/50 ${className}`}>
      <summary className="cursor-pointer list-none px-3 py-2 text-[11px] font-semibold uppercase tracking-wide text-slate-500 marker:content-none dark:text-slate-400 [&::-webkit-details-marker]:hidden">
        <span className="group-open:hidden">Show technical details</span>
        <span className="hidden group-open:inline">Hide technical details</span>
      </summary>
      <div className="space-y-2 border-t border-slate-200 px-3 py-2 text-xs text-slate-600 dark:border-slate-700 dark:text-slate-300">
        {technical.detail && (
          <div>
            <span className="font-medium text-slate-500 dark:text-slate-400">Detail: </span>
            <span className="whitespace-pre-wrap">{technical.detail}</span>
          </div>
        )}
        {technical.tier && (
          <div>
            <span className="font-medium text-slate-500 dark:text-slate-400">Tier: </span>
            <span>{technical.tier}</span>
          </div>
        )}
        {technical.threat_type && (
          <div>
            <span className="font-medium text-slate-500 dark:text-slate-400">Threat: </span>
            <span>{String(technical.threat_type).replace(/_/g, " ")}</span>
          </div>
        )}
        {technical.recommended_action && (
          <div>
            <span className="font-medium text-slate-500 dark:text-slate-400">Model recommendation: </span>
            <span className="uppercase">{technical.recommended_action}</span>
          </div>
        )}
        {patterns.length > 0 && (
          <div>
            <span className="font-medium text-slate-500 dark:text-slate-400">Matched patterns: </span>
            <span className="font-mono text-[11px]">{patterns.join(", ")}</span>
          </div>
        )}
        {findings.length > 0 && (
          <div>
            <span className="font-medium text-slate-500 dark:text-slate-400">Findings: </span>
            <span className="whitespace-pre-wrap">{findings.join("; ")}</span>
          </div>
        )}
        {technical.guard_reason && (
          <div>
            <span className="font-medium text-slate-500 dark:text-slate-400">Guard trace: </span>
            <pre className="mt-1 whitespace-pre-wrap font-mono text-[11px]">{technical.guard_reason}</pre>
          </div>
        )}
      </div>
    </details>
  );
}
