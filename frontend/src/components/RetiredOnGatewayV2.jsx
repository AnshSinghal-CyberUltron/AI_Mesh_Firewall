import { Archive } from "lucide-react";

/**
 * Labelled panel for a feature gateway v2 does not implement (HTTP 410 retired_on_gateway_v2).
 * `model` comes from `retiredPanelModel`.
 */
export function RetiredOnGatewayV2({ model }) {
  if (!model) return null;
  return (
    <div
      role="status"
      data-testid="retired-on-gateway-v2"
      className="flex items-start gap-3 rounded-lg border border-slate-300 bg-slate-50 p-4 text-sm text-slate-700 dark:border-slate-600 dark:bg-slate-800/60 dark:text-slate-200"
    >
      <Archive className="mt-0.5 h-5 w-5 shrink-0 text-slate-500 dark:text-slate-400" aria-hidden="true" />
      <div>
        <div className="font-semibold">{model.title}</div>
        <p className="mt-1">{model.message}</p>
        {model.path ? (
          <p className="mt-1 font-mono text-xs text-slate-500 dark:text-slate-400">{model.path}</p>
        ) : null}
      </div>
    </div>
  );
}
