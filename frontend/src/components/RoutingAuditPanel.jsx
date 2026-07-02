import { useState, useEffect, useCallback, useMemo } from "react";
import {
  GitBranch, RefreshCw, Loader2, ArrowRight, Clock,
  ChevronDown, ChevronRight,
} from "lucide-react";
import { useAuth } from "../context/AuthContext";
import {
  filterRoutingEvents,
  getRequestedModel,
  getRoutedModel,
  getRoutingExtra,
  getEventMetadata,
} from "../utils/routingEventFields";

const SENSITIVITY_COLORS = {
  restricted: "bg-red-100 dark:bg-red-800/30 text-red-700 dark:text-red-300",
  confidential: "bg-amber-100 dark:bg-amber-800/30 text-amber-700 dark:text-amber-300",
  internal: "bg-blue-100 dark:bg-blue-800/30 text-blue-700 dark:text-blue-300",
  public: "bg-slate-100 dark:bg-slate-700/50 text-slate-600 dark:text-slate-400",
};

/**
 * @param {object} props
 * @param {Array} [props.events] - When provided, uses parent feed (no duplicate fetch).
 * @param {boolean} [props.loading]
 * @param {() => void} [props.onRefresh]
 */
export function RoutingAuditPanel({ events: eventsProp, loading: loadingProp, onRefresh }) {
  const { fetchWithAuth } = useAuth();
  const [localEvents, setLocalEvents] = useState([]);
  const [localLoading, setLocalLoading] = useState(!eventsProp);
  const [expandedId, setExpandedId] = useState(null);
  const [hours, setHours] = useState(48);
  const [loadError, setLoadError] = useState("");

  const usesParentFeed = eventsProp !== undefined;

  const fetchRoutingEvents = useCallback(async () => {
    if (usesParentFeed) {
      onRefresh?.();
      return;
    }
    setLocalLoading(true);
    try {
      const res = await fetchWithAuth(
        `/api/security/threat-feed/?hours=${hours}&limit=200&source=routing`
      );
      if (res.ok) {
        const data = await res.json();
        setLocalEvents(filterRoutingEvents(data.results || []));
        setLoadError("");
      } else {
        // A failed fetch must not masquerade as "no routing events yet".
        setLoadError(`Failed to load routing audit (HTTP ${res.status}).`);
      }
    } catch {
      setLoadError("Failed to load the routing audit trail.");
    } finally {
      setLocalLoading(false);
    }
  }, [fetchWithAuth, hours, onRefresh, usesParentFeed]);

  useEffect(() => {
    if (!usesParentFeed) {
      fetchRoutingEvents();
    }
  }, [fetchRoutingEvents, usesParentFeed]);

  const events = useMemo(() => {
    if (usesParentFeed) {
      return filterRoutingEvents(eventsProp || []);
    }
    return localEvents;
  }, [usesParentFeed, eventsProp, localEvents]);

  const loading = usesParentFeed ? Boolean(loadingProp) : localLoading;

  const toggleExpand = (id) => {
    setExpandedId(expandedId === id ? null : id);
  };

  return (
    <div className="bg-white dark:bg-slate-800 border border-slate-200 dark:border-slate-700 rounded-xl shadow-sm p-6">
      <div className="flex items-center justify-between mb-4">
        <div>
          <h3 className="text-base font-semibold text-slate-900 dark:text-slate-100 flex items-center gap-2">
            <GitBranch className="w-4 h-4 text-indigo-600" />
            Routing Audit Trail
          </h3>
          <p className="text-xs text-slate-500 dark:text-slate-400 mt-0.5">
            Real-time log of `/v1/chat/completions` routing decisions, including the final target model and why the route was kept or changed
          </p>
        </div>
        <div className="flex items-center gap-2">
          {!usesParentFeed ? (
            <select
              value={hours}
              onChange={(e) => setHours(parseInt(e.target.value, 10))}
              className="bg-white dark:bg-slate-800 px-2 py-1.5 border border-slate-200 dark:border-slate-700 rounded-lg text-xs text-slate-700 dark:text-slate-300 focus:ring-2 focus:ring-indigo-500 focus:border-transparent min-h-[44px]"
            >
              <option value={1}>Last 1h</option>
              <option value={6}>Last 6h</option>
              <option value={24}>Last 24h</option>
              <option value={48}>Last 48h</option>
              <option value={168}>Last 7d</option>
            </select>
          ) : null}
          <button
            type="button"
            onClick={fetchRoutingEvents}
            disabled={loading}
            className="p-2 min-h-[44px] min-w-[44px] flex items-center justify-center hover:bg-slate-100 dark:hover:bg-slate-700 rounded-lg transition-colors"
            title="Refresh"
            aria-label="Refresh routing audit"
          >
            <RefreshCw className={`w-3.5 h-3.5 text-slate-500 ${loading ? "animate-spin" : ""}`} />
          </button>
        </div>
      </div>

      {loading ? (
        <div className="flex items-center justify-center py-8">
          <Loader2 className="w-5 h-5 text-indigo-500 animate-spin" />
          <span className="ml-2 text-sm text-slate-500 dark:text-slate-400">Loading routing events...</span>
        </div>
      ) : loadError && events.length === 0 ? (
        <div className="flex flex-col items-center gap-3 py-8 text-center">
          <div className="flex items-start gap-2 rounded-lg border border-red-200 bg-red-50 p-3 text-xs text-red-700 dark:border-red-800 dark:bg-red-900/20 dark:text-red-300">
            <GitBranch className="mt-0.5 h-4 w-4 flex-shrink-0" />
            <span>{loadError}</span>
          </div>
          <button
            type="button"
            onClick={fetchRoutingEvents}
            className="rounded-lg border border-slate-200 px-3 py-1 text-xs font-medium text-slate-600 transition-colors hover:bg-slate-100 dark:border-slate-700 dark:text-slate-300 dark:hover:bg-slate-800"
          >
            Retry
          </button>
        </div>
      ) : events.length === 0 ? (
        <div className="text-center py-8">
          <GitBranch className="w-8 h-8 text-slate-300 dark:text-slate-600 mx-auto mb-2" />
          <p className="text-sm text-slate-500 dark:text-slate-400">No routing events recorded yet.</p>
          <p className="text-xs text-slate-400 dark:text-slate-400 mt-1 max-w-md mx-auto">
            Run a chat completion through the mesh gateway with routing enabled, or use the Routing exercises simulator above to generate the first audit entry.
          </p>
        </div>
      ) : (
        <div className="space-y-2 max-h-[500px] overflow-y-auto">
          {events.map((ev) => {
            const meta = getEventMetadata(ev);
            const extra = getRoutingExtra(ev);
            const originalModel = getRequestedModel(ev);
            const routedModel = getRoutedModel(ev) || "—";
            const score = extra.routing_score ?? meta.routing_score ?? null;
            const reason = extra.routing_reason || meta.routing_reason || "—";
            const fallbacks = extra.fallback_chain || [];
            const sensitivity = extra.data_sensitivity || "public";
            const weights = extra.weights || {};
            const decisionSource = extra.decision_source || meta.decision_source || "weighted";
            const policySummary = extra.policy_summary || meta.policy_summary || "";
            const decisionFactors = extra.decision_factors || meta.decision_factors || [];
            const rerouted = typeof extra.rerouted === "boolean"
              ? extra.rerouted
              : (originalModel !== "auto" && originalModel !== routedModel);
            const isExpanded = expandedId === ev.id;

            return (
              <div
                key={ev.id}
                className="border border-slate-200 dark:border-slate-700 rounded-lg overflow-hidden"
              >
                <button
                  type="button"
                  onClick={() => toggleExpand(ev.id)}
                  className="w-full flex items-center gap-3 px-4 py-3 text-left hover:bg-slate-50 dark:hover:bg-slate-700/50 transition-colors"
                >
                  {isExpanded ? (
                    <ChevronDown className="w-3.5 h-3.5 text-slate-400 flex-shrink-0" />
                  ) : (
                    <ChevronRight className="w-3.5 h-3.5 text-slate-400 flex-shrink-0" />
                  )}

                  <span className="text-[10px] text-slate-400 dark:text-slate-400 flex items-center gap-1 min-w-[110px]">
                    <Clock className="w-3 h-3" />
                    {ev.timestamp ? new Date(ev.timestamp).toLocaleString(undefined, {
                      month: "short", day: "numeric", hour: "2-digit", minute: "2-digit", second: "2-digit",
                    }) : "—"}
                  </span>

                  <div className="flex items-center gap-2 flex-1 min-w-0">
                    <span className="px-2 py-0.5 rounded bg-slate-100 dark:bg-slate-700 text-xs font-mono text-slate-600 dark:text-slate-400 truncate">
                      {originalModel}
                    </span>
                    <ArrowRight className="w-3.5 h-3.5 text-indigo-500 flex-shrink-0" />
                    <span className="px-2 py-0.5 rounded bg-indigo-100 dark:bg-indigo-800/30 text-xs font-mono font-semibold text-indigo-700 dark:text-indigo-300 truncate">
                      {routedModel}
                    </span>
                  </div>

                  {score !== null && (
                    <span className="text-[10px] font-mono font-semibold text-indigo-600 dark:text-indigo-400 min-w-[50px] text-right">
                      {(score * 100).toFixed(1)}%
                    </span>
                  )}

                  <span className="hidden lg:inline-flex px-1.5 py-0.5 rounded text-[10px] font-medium bg-violet-100 dark:bg-violet-800/30 text-violet-700 dark:text-violet-300">
                    {decisionSource === "policy_adjudicator" ? "Policy adjudicated" : "Weighted fallback"}
                  </span>

                  <span className={`hidden lg:inline-flex px-1.5 py-0.5 rounded text-[10px] font-medium ${rerouted ? "bg-amber-100 dark:bg-amber-800/30 text-amber-700 dark:text-amber-300" : "bg-emerald-100 dark:bg-emerald-800/30 text-emerald-700 dark:text-emerald-300"}`}>
                    {rerouted ? "Rerouted" : "Kept preferred"}
                  </span>

                  <span className={`inline-flex px-1.5 py-0.5 rounded text-[10px] font-medium ${SENSITIVITY_COLORS[sensitivity] || SENSITIVITY_COLORS.public}`}>
                    {sensitivity}
                  </span>
                </button>

                {isExpanded && (
                  <div className="px-4 py-3 border-t border-slate-100 dark:border-slate-700 bg-slate-50/50 dark:bg-slate-800/50 space-y-3">
                    <div>
                      <span className="text-[10px] font-semibold text-slate-500 dark:text-slate-400 uppercase">Routing Reason</span>
                      <p className="text-xs text-slate-700 dark:text-slate-300 mt-0.5">{reason}</p>
                      {policySummary && (
                        <p className="text-[11px] text-slate-500 dark:text-slate-400 mt-1">{policySummary}</p>
                      )}
                    </div>

                    {decisionSource && (
                      <div className="flex gap-4 text-[10px] text-slate-500 dark:text-slate-400">
                        <span>Decision source: {decisionSource}</span>
                      </div>
                    )}

                    {Array.isArray(decisionFactors) && decisionFactors.length > 0 && (
                      <div>
                        <span className="text-[10px] font-semibold text-slate-500 dark:text-slate-400 uppercase">Decision Factors</span>
                        <div className="flex items-center gap-1.5 mt-1 flex-wrap">
                          {decisionFactors.map((factor, i) => (
                            <span key={i} className="px-2 py-0.5 rounded bg-indigo-100 dark:bg-indigo-800/30 text-[10px] font-mono text-indigo-700 dark:text-indigo-300">
                              {String(factor)}
                            </span>
                          ))}
                        </div>
                      </div>
                    )}

                    {Object.keys(weights).length > 0 && (
                      <div>
                        <span className="text-[10px] font-semibold text-slate-500 dark:text-slate-400 uppercase">Applied Weights</span>
                        <div className="flex gap-3 mt-1">
                          {Object.entries(weights).map(([k, v]) => (
                            <div key={k} className="flex items-center gap-1">
                              <span className="text-[10px] text-slate-500 dark:text-slate-400 capitalize">{k}:</span>
                              <span className="text-[10px] font-mono font-semibold text-slate-700 dark:text-slate-300">
                                {(v * 100).toFixed(0)}%
                              </span>
                            </div>
                          ))}
                        </div>
                      </div>
                    )}

                    {fallbacks.length > 0 && (
                      <div>
                        <span className="text-[10px] font-semibold text-slate-500 dark:text-slate-400 uppercase">Fallback Chain</span>
                        <div className="flex items-center gap-1.5 mt-1 flex-wrap">
                          {fallbacks.map((fb, i) => (
                            <span key={i} className="px-2 py-0.5 rounded bg-slate-100 dark:bg-slate-700 text-[10px] font-mono text-slate-600 dark:text-slate-400">
                              {fb}
                            </span>
                          ))}
                        </div>
                      </div>
                    )}

                    <div className="flex gap-4 text-[10px] text-slate-500 dark:text-slate-400">
                      {ev.user_id && <span>User: {ev.user_display || ev.user_id}</span>}
                      {meta.latency_ms > 0 && <span>Latency: {meta.latency_ms.toFixed(0)}ms</span>}
                      {ev.endpoint_name && <span>Endpoint: {ev.endpoint_name}</span>}
                    </div>
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
