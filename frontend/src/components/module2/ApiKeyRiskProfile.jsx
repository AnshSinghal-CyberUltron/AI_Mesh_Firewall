import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { AlertTriangle, Loader2, Power, PowerOff, ShieldAlert, ExternalLink } from "lucide-react";
import {
  buildCredentialKillSwitchPayload,
  buildAnalystKillSwitchReason,
  createKillSwitchApi,
  describeContainmentSemantics,
  fetchActiveSimulatorContext,
  fetchGatewayModelNames,
  filterEnforcedKillSwitches,
  filterKillSwitchesForPrefix,
  isGatewayEnforcedKillModel,
  isSimulatorKeyRow,
  mergeKillSwitchModelCandidates,
  readPreferredSimulatorModel,
  validateKillSwitchTarget,
} from "../../api/killSwitch";
import { LlMObservationBadge, LlMObservationPanel } from "./LlMObservationStatus";
import { RiskBandBadge } from "./RiskBandBadge";
import { RiskScoreCalculationGuide } from "./RiskScoreCalculationGuide";
import { ApiKeyActivityTimeline } from "./ApiKeyActivityTimeline";
import {
  actionLabelStyle,
  actionPromptStyle,
  eventPromptPreview,
  laneLabel,
} from "./uebaPromptDisplay";
import { formatUebaRequestTime } from "./uebaTimeFormat";
import { KillSwitchActionDialog } from "./KillSwitchActionDialog";

const RECOMMENDED_ACTION_LABELS = {
  monitor: "Monitor — routine watch",
  investigate: "Investigate — review prompts and owner",
  contain: "Contain — consider kill-switch or disable",
};

const BAND_STYLES = {
  low: {
    badge: "bg-emerald-100 text-emerald-800 dark:bg-emerald-900/40 dark:text-emerald-300",
    gauge: "bg-emerald-500",
    ring: "stroke-emerald-500",
  },
  medium: {
    badge: "bg-amber-100 text-amber-800 dark:bg-amber-900/40 dark:text-amber-300",
    gauge: "bg-amber-500",
    ring: "stroke-amber-500",
  },
  high: {
    badge: "bg-red-100 text-red-800 dark:bg-red-900/40 dark:text-red-300",
    gauge: "bg-red-500",
    ring: "stroke-red-500",
  },
};

const ANOMALY_LABELS = {
  high_block_rate: "High block rate",
  velocity_spike: "Velocity spike",
  model_spread: "Model spread",
};

function RiskGauge({ score, band }) {
  const displayScore = score ?? 0;
  const pct = Math.min(Math.max(Number(displayScore) || 0, 0), 1) * 100;
  const styles = BAND_STYLES[band] || BAND_STYLES.low;
  const radius = 36;
  const circumference = 2 * Math.PI * radius;
  const offset = circumference - (pct / 100) * circumference;

  return (
    <div className="relative flex h-24 w-24 items-center justify-center">
      <svg className="h-24 w-24 -rotate-90" viewBox="0 0 96 96">
        <circle cx="48" cy="48" r={radius} className="stroke-slate-200 dark:stroke-slate-700" strokeWidth="8" fill="none" />
        <circle
          cx="48"
          cy="48"
          r={radius}
          className={styles.ring}
          strokeWidth="8"
          fill="none"
          strokeLinecap="round"
          strokeDasharray={circumference}
          strokeDashoffset={offset}
        />
      </svg>
      <div className="absolute text-center">
        <p className="text-lg font-bold text-slate-900 dark:text-white">{pct.toFixed(0)}</p>
        <p className="text-[10px] uppercase text-slate-500">risk</p>
      </div>
    </div>
  );
}

function MetricBar({ label, value, colorClass }) {
  const pct = Math.min(Math.max(Number(value) || 0, 0), 100);
  return (
    <div>
      <div className="mb-1 flex justify-between text-xs">
        <span className="text-slate-600 dark:text-slate-400">{label}</span>
        <span className="font-medium text-slate-800 dark:text-slate-200">{pct.toFixed(1)}%</span>
      </div>
      <div className="h-2 overflow-hidden rounded-full bg-slate-200 dark:bg-slate-700">
        <div className={`h-full rounded-full ${colorClass}`} style={{ width: `${pct}%` }} />
      </div>
    </div>
  );
}

function formatRequestTime(timestamp) {
  return formatUebaRequestTime(timestamp);
}

function promptPreview(req) {
  return eventPromptPreview(req);
}

function compactRequestJson(req) {
  return {
    timestamp: req.timestamp,
    action: req.action,
    model: req.model,
    threat_type: req.threat_type,
    prompt: promptPreview(req) || null,
  };
}

function promptPositionLabel(index) {
  if (index === 0) return "Last prompt";
  if (index === 1) return "2nd last";
  if (index === 2) return "3rd last";
  if (index === 3) return "4th last";
  if (index === 4) return "5th last";
  return `${index + 1}th last`;
}

function requestIdentity(req) {
  if (!req) return "";
  return `${req.event_id ?? ""}:${req.timestamp ?? ""}:${promptPreview(req)}`;
}

function RecentRequestsSection({ requests, requestCount }) {
  const [selectedIndex, setSelectedIndex] = useState(0);
  const [showJson, setShowJson] = useState(false);
  const selectedIdentityRef = useRef("");

  useEffect(() => {
    if (!requests?.length) {
      setSelectedIndex(0);
      selectedIdentityRef.current = "";
      return;
    }
    const remembered = selectedIdentityRef.current;
    if (remembered) {
      const matchedIndex = requests.findIndex((req) => requestIdentity(req) === remembered);
      if (matchedIndex >= 0) {
        setSelectedIndex(matchedIndex);
        return;
      }
    }
    setSelectedIndex((prev) => {
      const next = Math.min(prev, requests.length - 1);
      selectedIdentityRef.current = requestIdentity(requests[next]);
      return next;
    });
  }, [requests]);

  const safeIndex = Math.min(selectedIndex, Math.max(0, (requests?.length || 1) - 1));
  const selected = requests?.[safeIndex];
  const canGoNewer = safeIndex > 0;
  const canGoOlder = safeIndex < (requests?.length || 0) - 1;

  return (
    <div className="rounded-lg border border-slate-200 p-3 dark:border-slate-600">
      <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
        <p className="text-xs font-semibold uppercase text-slate-500">
          Recent prompts
          {requestCount ? ` · last ${Math.min(requests?.length || 0, 10)} shown` : ""}
        </p>
      </div>

      {requests?.length > 0 ? (
        <div className="space-y-3">
          <div className="flex flex-wrap items-center gap-1.5">
            {requests.map((req, i) => (
              <button
                key={`${req.event_id || req.timestamp}-${i}`}
                type="button"
                onClick={() => {
                  selectedIdentityRef.current = requestIdentity(req);
                  setSelectedIndex(i);
                  setShowJson(false);
                }}
                className={`rounded-full px-2.5 py-1 text-[11px] font-medium transition-colors ${
                  i === safeIndex
                    ? "bg-teal-600 text-white shadow-sm"
                    : "border border-slate-200 bg-white text-slate-600 hover:border-teal-300 hover:text-teal-700 dark:border-slate-600 dark:bg-slate-800 dark:text-slate-300 dark:hover:border-teal-600 dark:hover:text-teal-300"
                }`}
                title={req.timestamp ? formatUebaRequestTime(req.timestamp) : promptPositionLabel(i)}
              >
                {promptPositionLabel(i)}
              </button>
            ))}
          </div>

          {requests.length > 1 && (
            <div className="flex items-center gap-2 text-[11px]">
              <button
                type="button"
                disabled={!canGoNewer}
                onClick={() => {
                  setSelectedIndex((i) => {
                    const next = Math.max(0, i - 1);
                    selectedIdentityRef.current = requestIdentity(requests[next]);
                    return next;
                  });
                  setShowJson(false);
                }}
                className="rounded-md border border-slate-200 px-2 py-1 font-medium text-slate-600 hover:bg-slate-50 disabled:cursor-not-allowed disabled:opacity-40 dark:border-slate-600 dark:text-slate-300 dark:hover:bg-slate-800"
              >
                Next (newer)
              </button>
              <span className="text-slate-400">
                Viewing {promptPositionLabel(safeIndex).toLowerCase()}
              </span>
              <button
                type="button"
                disabled={!canGoOlder}
                onClick={() => {
                  setSelectedIndex((i) => {
                    const next = Math.min(requests.length - 1, i + 1);
                    selectedIdentityRef.current = requestIdentity(requests[next]);
                    return next;
                  });
                  setShowJson(false);
                }}
                className="rounded-md border border-slate-200 px-2 py-1 font-medium text-slate-600 hover:bg-slate-50 disabled:cursor-not-allowed disabled:opacity-40 dark:border-slate-600 dark:text-slate-300 dark:hover:bg-slate-800"
              >
                Previous prompt
              </button>
            </div>
          )}

          {selected && (
            <div className={`rounded-md border px-3 py-2 text-xs ${actionPromptStyle(selected.action)}`}>
              <p className="text-[10px] font-semibold uppercase tracking-wide text-slate-500 dark:text-slate-400">
                {promptPositionLabel(safeIndex)}
              </p>
              <p className={`mt-1 font-semibold uppercase ${actionLabelStyle(selected.action)}`}>
                {laneLabel(selected.request_lane || selected.metadata?.source || "chat")}
                {" · "}{(selected.action || "—").toUpperCase()}
                {" · "}{selected.model || "—"}
                {" · "}{selected.threat_type || "—"}
              </p>
              <p className="mt-1 text-[10px] opacity-80">
                {formatRequestTime(selected.timestamp)}
              </p>
              <p className="mt-2 whitespace-pre-wrap break-words leading-relaxed">
                {promptPreview(selected) || "No prompt captured for this event."}
              </p>
              <button
                type="button"
                onClick={() => setShowJson((v) => !v)}
                className="mt-2 text-[11px] font-medium text-teal-600 hover:underline dark:text-teal-400"
              >
                {showJson ? "Hide JSON" : "Show JSON"}
              </button>
              {showJson && (
                <pre className="mt-2 max-h-40 overflow-auto rounded-md bg-slate-900 p-2 font-mono text-[10px] leading-relaxed text-emerald-300 dark:bg-black/50">
                  {JSON.stringify(compactRequestJson(selected), null, 2)}
                </pre>
              )}
            </div>
          )}
        </div>
      ) : (
        <p className="text-xs text-slate-400">
          {requestCount} event(s) in this period — waiting for prompt detail…
        </p>
      )}
    </div>
  );
}

function BehaviorProfileSection({ profile, llmReasoning, llmVerdict, llmRecommendedAction, traditionalScore, finalScore }) {
  if (!profile) return null;
  const collected = profile.prompt_samples_collected ?? 0;
  const target = profile.prompt_samples_target ?? 50;
  const pct = target ? Math.min(100, (collected / target) * 100) : 0;
  const ready = profile.status === "ready";
  const scoreDiff =
    traditionalScore != null
    && finalScore != null
    && Math.abs(Number(traditionalScore) - Number(finalScore)) >= 0.01;

  return (
    <div className="rounded-lg border border-slate-200 p-3 dark:border-slate-600">
      <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
        <p className="text-xs font-semibold uppercase text-slate-500">Behavior intelligence</p>
        <span className={`rounded-full px-2 py-0.5 text-[10px] font-semibold uppercase ${
          ready
            ? "bg-teal-100 text-teal-800 dark:bg-teal-900/40 dark:text-teal-300"
            : "bg-slate-200 text-slate-600 dark:bg-slate-700 dark:text-slate-300"
        }`}>
          {ready ? "Profile ready" : "Collecting prompts"}
        </span>
      </div>
      {!ready && (
        <div className="mb-3">
          <div className="mb-1 flex justify-between text-[11px] text-slate-500">
            <span>First {target} prompts for LLM baseline</span>
            <span>{collected}/{target}</span>
          </div>
          <div className="h-2 overflow-hidden rounded-full bg-slate-200 dark:bg-slate-700">
            <div
              className="h-full rounded-full bg-teal-500 transition-all"
              style={{ width: `${pct}%` }}
            />
          </div>
        </div>
      )}
      {ready && (
        <div className="space-y-2 text-xs text-slate-700 dark:text-slate-300">
          {profile.profile_locked && profile.prompt_samples_build_count != null && (
            <p className="text-[11px] text-slate-500">
              Profile locked from {profile.prompt_samples_build_count} prompts
              {profile.prompt_samples_target !== profile.prompt_samples_build_count
                ? ` (org target now ${profile.prompt_samples_target})`
                : ""}
            </p>
          )}
          {profile.expected_use_case && (
            <p><span className="font-semibold text-slate-500">Expected use:</span> {profile.expected_use_case}</p>
          )}
          {profile.behavior_class && profile.behavior_class !== "unknown" && (
            <p><span className="font-semibold text-slate-500">Class:</span> {profile.behavior_class.replace("_", " ")}</p>
          )}
          {profile.risk_prediction && (
            <p><span className="font-semibold text-slate-500">Prediction:</span> {profile.risk_prediction}</p>
          )}
        </div>
      )}
      {llmRecommendedAction && (
        <p className="mt-2 text-[11px] text-slate-600 dark:text-slate-300">
          <span className="font-semibold text-slate-500">Recommended action:</span>{" "}
          {RECOMMENDED_ACTION_LABELS[llmRecommendedAction] || llmRecommendedAction}
        </p>
      )}
      {scoreDiff && (
        <p className="mt-2 text-[11px] text-slate-500">
          Traditional {Number(traditionalScore).toFixed(2)} → LLM-adjusted {Number(finalScore).toFixed(2)}
        </p>
      )}
      {llmReasoning && llmVerdict && llmVerdict !== "skipped" && (
        <div className="mt-3 rounded-md border border-slate-100 bg-white/60 px-3 py-2 dark:border-slate-700 dark:bg-slate-800/50">
          <p className="text-[10px] font-semibold uppercase tracking-wide text-amber-600 dark:text-amber-400">
            Analyst · {llmVerdict}
          </p>
          <p className="mt-1 text-xs leading-relaxed text-slate-600 dark:text-slate-300">{llmReasoning}</p>
        </div>
      )}
    </div>
  );
}

export function ApiKeyRiskProfile({
  behavior,
  fetchWithAuth,
  onActionComplete,
  showActions = true,
  simulatorKeyPrefix = "",
  riskCalculation = null,
  variant = "inline",
  showScoreGuide,
  scoreUpdating = false,
}) {
  const isSidebar = variant === "sidebar";
  const isPreview = variant === "preview";
  const scoreGuideVisible = showScoreGuide ?? (!isSidebar && !isPreview);
  const api = useMemo(() => createKillSwitchApi(fetchWithAuth), [fetchWithAuth]);
  const [killSwitches, setKillSwitches] = useState([]);
  const [ksLoading, setKsLoading] = useState(true);
  const [actionLoading, setActionLoading] = useState(null);
  const [actionError, setActionError] = useState(null);
  const [actionSuccess, setActionSuccess] = useState(null);
  const [killSwitchDialogOpen, setKillSwitchDialogOpen] = useState(false);
  const [killDialogModels, setKillDialogModels] = useState([]);
  const [killPreferredModel, setKillPreferredModel] = useState("");
  const [activeSimulator, setActiveSimulator] = useState(null);

  const scopedKillSwitches = useMemo(
    () => filterKillSwitchesForPrefix(killSwitches, behavior?.prefix),
    [killSwitches, behavior?.prefix],
  );
  const enforcedKillSwitches = useMemo(
    () => filterEnforcedKillSwitches(scopedKillSwitches),
    [scopedKillSwitches],
  );
  const legacyKillSwitches = useMemo(
    () => scopedKillSwitches.filter((ks) => ks?.is_active !== false
      && !isGatewayEnforcedKillModel(ks?.model_name)),
    [scopedKillSwitches],
  );

  const allowRate = useMemo(() => {
    if (!behavior?.request_count) return 0;
    const blocked = behavior.blocked_count || 0;
    const redacted = behavior.redacted_count || 0;
    const allowed = Math.max(0, behavior.request_count - blocked - redacted);
    return (allowed / behavior.request_count) * 100;
  }, [behavior]);

  const isSimulatorTarget = Boolean(
    (simulatorKeyPrefix && behavior?.prefix && behavior.prefix === simulatorKeyPrefix)
    || isSimulatorKeyRow(behavior, activeSimulator),
  );

  useEffect(() => {
    if (!killSwitchDialogOpen) {
      setKillDialogModels([]);
      setKillPreferredModel("");
      setActiveSimulator(null);
      return undefined;
    }
    let cancelled = false;
    const preferred = readPreferredSimulatorModel();
    setKillPreferredModel(preferred);
    (async () => {
      const [gatewayNames, liveSim] = await Promise.all([
        fetchGatewayModelNames(fetchWithAuth),
        fetchActiveSimulatorContext(fetchWithAuth),
      ]);
      if (cancelled) return;
      setActiveSimulator(liveSim);
      const simTarget = Boolean(
        (simulatorKeyPrefix && behavior?.prefix && behavior.prefix === simulatorKeyPrefix)
        || isSimulatorKeyRow(behavior, liveSim),
      );
      setKillDialogModels(mergeKillSwitchModelCandidates({
        telemetryRow: behavior,
        gatewayModelNames: gatewayNames,
        preferredModel: simTarget ? preferred : "",
      }));
    })();
    return () => {
      cancelled = true;
    };
  }, [killSwitchDialogOpen, behavior, fetchWithAuth, simulatorKeyPrefix]);

  const loadKillSwitches = useCallback(async () => {
    setKsLoading(true);
    try {
      setKillSwitches(await api.listKillSwitches());
    } catch {
      setKillSwitches([]);
    } finally {
      setKsLoading(false);
    }
  }, [api]);

  useEffect(() => {
    loadKillSwitches();
  }, [loadKillSwitches]);

  if (!behavior) {
    return (
      <p className="py-8 text-center text-sm text-slate-400">
        Click <strong>Profile</strong> on any key in the fleet registry to open the behavior timeline and score breakdown.
      </p>
    );
  }

  const band = behavior.risk_band || "low";
  const displayScore = behavior.final_score ?? behavior.risk_score ?? 0;

  const handleApplyKillSwitch = async () => {
    setActionError(null);
    setActionSuccess(null);
    setKillSwitchDialogOpen(true);
  };

  const handleSubmitKillSwitch = async (form) => {
    setActionLoading("kill-switch");
    setActionError(null);
    setActionSuccess(null);
    try {
      const liveSim = activeSimulator || await fetchActiveSimulatorContext(fetchWithAuth);
      const check = validateKillSwitchTarget({
        row: behavior,
        activeSimulator: liveSim,
        requireActiveKey: true,
      });
      if (!check.ok) {
        setActionError(check.error);
        await onActionComplete?.();
        return;
      }
      const payload = buildCredentialKillSwitchPayload({
        modelName: form.modelName,
        apiKeyPrefix: form.apiKeyPrefix || behavior.prefix,
        action: form.action,
        fallbackModel: form.fallbackModel,
        reason: form.reason || buildAnalystKillSwitchReason(behavior),
      });
      await api.createAndActivateKillSwitch(payload);
      const semantics = describeContainmentSemantics();
      setActionSuccess(
        `Kill switch activated for ${behavior.prefix} / ${form.modelName}. ${semantics.killSwitch}`,
      );
      setKillSwitchDialogOpen(false);
      await loadKillSwitches();
      onActionComplete?.();
    } catch (err) {
      setActionError(err.message || "Failed to apply kill switch.");
    } finally {
      setActionLoading(null);
    }
  };

  const handleDisableKey = async () => {
    const semantics = describeContainmentSemantics();
    const confirmed = window.confirm(
      `Disable API key ${behavior.prefix}?\n\n${semantics.disableKey}`,
    );
    if (!confirmed) return;

    setActionLoading("disable-key");
    setActionError(null);
    setActionSuccess(null);
    try {
      await api.setGatewayKeyActive(behavior.key_id, false, { prefix: behavior.key_prefix || behavior.prefix });
      setActionSuccess(
        `API key ${behavior.prefix} disabled — Auth will reject this credential (HTTP 403). `
        + "Traffic never reaches Input Scan or Kill Switch.",
      );
      onActionComplete?.();
    } catch (err) {
      setActionError(err.message || "Failed to disable API key.");
    } finally {
      setActionLoading(null);
    }
  };

  const handleReenableKey = async () => {
    setActionLoading("enable-key");
    setActionError(null);
    try {
      await api.setGatewayKeyActive(behavior.key_id, true, { prefix: behavior.key_prefix || behavior.prefix });
      setActionSuccess(`API key ${behavior.prefix} re-enabled.`);
      onActionComplete?.();
    } catch (err) {
      setActionError(err.message || "Failed to re-enable API key.");
    } finally {
      setActionLoading(null);
    }
  };

  return (
    <div className="space-y-4">
      {simulatorKeyPrefix && behavior.prefix && behavior.prefix !== simulatorKeyPrefix && (
        <div className="rounded-lg border border-amber-300 bg-amber-50 px-3 py-2 text-xs text-amber-900 dark:border-amber-800 dark:bg-amber-950/40 dark:text-amber-100">
          Attack Simulator records traffic under key{" "}
          <span className="font-mono font-semibold">{simulatorKeyPrefix}</span>, not{" "}
          <span className="font-mono font-semibold">{behavior.prefix}</span>.
          {" "}Open the <strong>Simulator</strong> key profile to see new prompts and request counts.
        </div>
      )}
      <div className={`flex flex-wrap items-start gap-4 rounded-xl border border-slate-200 bg-slate-50/80 p-4 dark:border-slate-600 dark:bg-slate-800/40 ${isSidebar ? "py-3" : ""}`}>
        <RiskGauge score={displayScore} band={band} />
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <RiskBandBadge type="behavioral" band={band} score={displayScore} />
            {scoreUpdating && (
              <span className="inline-flex items-center gap-1 rounded-full bg-sky-100 px-2 py-0.5 text-[10px] font-semibold uppercase text-sky-800 dark:bg-sky-900/40 dark:text-sky-200">
                <Loader2 className="h-3 w-3 animate-spin" />
                Updating score
              </span>
            )}
            <LlMObservationBadge observation={behavior.llm_observation} compact />
            {!behavior.is_active && (
              <span className="rounded-full bg-slate-200 px-2 py-0.5 text-xs font-semibold text-slate-600 dark:bg-slate-700 dark:text-slate-300">
                Key disabled
              </span>
            )}
          </div>
          {!isSidebar && (
            <>
              <p className="mt-1 font-mono text-sm text-slate-800 dark:text-slate-100">{behavior.prefix}</p>
              <p className="text-xs text-slate-500 dark:text-slate-400">
                {behavior.name || "—"} · {behavior.project_id || "no project"}
                {behavior.owner_email ? ` · ${behavior.owner_email}` : ""}
              </p>
            </>
          )}
          <p className={`text-xs text-slate-600 dark:text-slate-300 ${isSidebar ? "mt-1" : "mt-2"}`}>
            Score {Number(displayScore).toFixed(2)} · Velocity {behavior.velocity_spike}x · {behavior.request_count} requests
          </p>
          {behavior.llm_recommended_action && (
            <p className="mt-1 text-[11px] text-slate-500 dark:text-slate-400">
              <span className="font-semibold">SOC action:</span>{" "}
              {RECOMMENDED_ACTION_LABELS[behavior.llm_recommended_action] || behavior.llm_recommended_action}
            </p>
          )}
        </div>
      </div>

      <div className="space-y-2">
        <p className="text-xs font-semibold uppercase text-slate-500">Safety rates</p>
        <MetricBar label="Blocked" value={behavior.block_rate_pct} colorClass="bg-red-500" />
        <MetricBar label="Redacted" value={behavior.redact_rate_pct} colorClass="bg-amber-500" />
        <MetricBar label="Allowed (safe bandwidth)" value={allowRate} colorClass="bg-emerald-500" />
      </div>

      <BehaviorProfileSection
        profile={behavior.behavior_profile}
        llmReasoning={behavior.llm_reasoning}
        llmVerdict={behavior.llm_verdict}
        llmRecommendedAction={behavior.llm_recommended_action}
        traditionalScore={behavior.traditional_score}
        finalScore={behavior.final_score ?? behavior.risk_score}
      />

      <LlMObservationPanel
        observation={behavior.llm_observation}
        traditionalScore={behavior.traditional_score}
      />

      {(behavior.anomaly_flags || []).length > 0 && (
        <div className="flex flex-wrap gap-1.5">
          {behavior.anomaly_flags.map((flag) => (
            <span
              key={flag}
              className="inline-flex items-center gap-1 rounded-md border border-amber-300/60 bg-amber-50 px-2 py-0.5 text-[11px] font-medium text-amber-800 dark:border-amber-700 dark:bg-amber-950/40 dark:text-amber-200"
            >
              <AlertTriangle className="h-3 w-3" />
              {ANOMALY_LABELS[flag] || flag}
            </span>
          ))}
        </div>
      )}

      {isSidebar ? (
        <ApiKeyActivityTimeline
          requests={behavior.recent_requests}
          requestCount={behavior.request_count}
        />
      ) : (
        <RecentRequestsSection
          requests={behavior.recent_requests}
          requestCount={behavior.request_count}
        />
      )}

      <div className="grid grid-cols-2 gap-3 text-xs">
        <div className="rounded-lg border border-slate-200 p-2 dark:border-slate-600">
          <p className="mb-1 font-semibold uppercase text-slate-500">Top threats</p>
          <p className="text-slate-700 dark:text-slate-300">
            {(behavior.top_threat_types || []).slice(0, 3).map(([t, c]) => `${t} (${c})`).join(", ") || "—"}
          </p>
        </div>
        <div className="rounded-lg border border-slate-200 p-2 dark:border-slate-600">
          <p className="mb-1 font-semibold uppercase text-slate-500">Models used</p>
          <p className="text-slate-700 dark:text-slate-300">
            {(behavior.top_models || []).slice(0, 3).map(([m, c]) => `${m} (${c})`).join(", ") || "—"}
          </p>
        </div>
      </div>

      {scoreGuideVisible && (
        <RiskScoreCalculationGuide
          behavior={behavior}
          riskCalculation={behavior.risk_calculation || riskCalculation}
        />
      )}

      {!isPreview && (
      <div className="rounded-lg border border-slate-200 p-3 dark:border-slate-600">
        <p className="mb-2 flex items-center gap-1 text-xs font-semibold uppercase text-slate-500">
          <ShieldAlert className="h-3.5 w-3.5" />
          Active credential kill switches
        </p>
        {ksLoading ? (
          <Loader2 className="h-4 w-4 animate-spin text-slate-400" />
        ) : scopedKillSwitches.length === 0 ? (
          <p className="text-xs text-slate-400">No kill switches scoped to this key prefix.</p>
        ) : (
          <ul className="space-y-1.5">
            {scopedKillSwitches.map((ks) => {
              const enforced = enforcedKillSwitches.some((row) => row.id === ks.id);
              return (
              <li key={ks.id} className="flex items-center justify-between text-xs">
                <span className="font-mono text-slate-700 dark:text-slate-200">
                  {ks.model_name}
                  {ks.is_active ? (
                    <span className={`ml-2 ${enforced ? "text-red-600 dark:text-red-400" : "text-amber-600 dark:text-amber-400"}`}>
                      {enforced ? "active · Kill Switch stage" : "active · legacy (not enforced)"}
                    </span>
                  ) : (
                    <span className="ml-2 text-slate-400">inactive</span>
                  )}
                </span>
                {ks.is_active && (
                  <button
                    type="button"
                    disabled={actionLoading === `deactivate-${ks.id}`}
                    onClick={async () => {
                      setActionLoading(`deactivate-${ks.id}`);
                      try {
                        await api.deactivateKillSwitch(ks.id);
                        await loadKillSwitches();
                        onActionComplete?.();
                      } finally {
                        setActionLoading(null);
                      }
                    }}
                    className="text-teal-600 hover:underline dark:text-teal-400"
                  >
                    Deactivate
                  </button>
                )}
              </li>
              );
            })}
          </ul>
        )}
        {legacyKillSwitches.length > 0 && (
          <p className="mt-2 text-xs text-amber-700 dark:text-amber-300">
            Legacy <span className="font-mono">__credential__</span> switches are not gateway-enforced.
            They do not count as containment — deactivate them and create a per-model kill switch,
            or use Disable API key (Auth rejection).
          </p>
        )}
        <p className="mt-2 text-[11px] text-slate-500 dark:text-slate-400">
          {describeContainmentSemantics().killSwitch}
          {" "}
          {describeContainmentSemantics().disableKey}
        </p>
      </div>
      )}

      {actionError && (
        <p className="rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-xs text-red-700 dark:border-red-900 dark:bg-red-950/40 dark:text-red-300">
          {actionError}
        </p>
      )}
      {actionSuccess && (
        <p className="rounded-lg border border-emerald-200 bg-emerald-50 px-3 py-2 text-xs text-emerald-800 dark:border-emerald-900 dark:bg-emerald-950/40 dark:text-emerald-200">
          {actionSuccess}
        </p>
      )}

      {showActions && (
        <div className="flex flex-wrap gap-2 border-t border-slate-200 pt-3 dark:border-slate-600">
          {behavior.is_active !== false && (
            <button
              type="button"
              disabled={!!actionLoading}
              onClick={handleApplyKillSwitch}
              className="inline-flex items-center gap-1.5 rounded-lg bg-red-600 px-3 py-2 text-xs font-semibold text-white hover:bg-red-700 disabled:opacity-60"
            >
              {actionLoading === "kill-switch" ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Power className="h-3.5 w-3.5" />}
              Activate kill switch
            </button>
          )}
          {behavior.is_active !== false ? (
            <button
              type="button"
              disabled={!!actionLoading}
              onClick={handleDisableKey}
              className="inline-flex items-center gap-1.5 rounded-lg border border-slate-300 px-3 py-2 text-xs font-semibold text-slate-700 hover:bg-slate-100 disabled:opacity-60 dark:border-slate-600 dark:text-slate-200 dark:hover:bg-slate-800"
            >
              {actionLoading === "disable-key" ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <PowerOff className="h-3.5 w-3.5" />}
              Disable API key
            </button>
          ) : (
            <button
              type="button"
              disabled={!!actionLoading}
              onClick={handleReenableKey}
              className="inline-flex items-center gap-1.5 rounded-lg border border-teal-300 px-3 py-2 text-xs font-semibold text-teal-700 hover:bg-teal-50 disabled:opacity-60 dark:border-teal-700 dark:text-teal-300"
            >
              Re-enable API key
            </button>
          )}
          <a
            href="/?tab=firewall-1-6"
            className="inline-flex items-center gap-1 rounded-lg px-2 py-2 text-xs text-slate-500 hover:text-teal-600 dark:hover:text-teal-400"
          >
            Open Kill Switch panel
            <ExternalLink className="h-3 w-3" />
          </a>
        </div>
      )}
      <KillSwitchActionDialog
        open={killSwitchDialogOpen}
        title="Activate kill switch"
        targetLabel={behavior?.prefix || ""}
        allowedModels={killDialogModels}
        preferredModel={isSimulatorTarget ? killPreferredModel : ""}
        initialApiKeyPrefix={behavior?.prefix || ""}
        defaultReason={buildAnalystKillSwitchReason(behavior)}
        keyIsActive={behavior?.is_active !== false}
        isSimulatorKey={isSimulatorTarget}
        activeSimulatorPrefix={activeSimulator?.prefix || simulatorKeyPrefix || ""}
        loading={actionLoading === "kill-switch"}
        onClose={() => setKillSwitchDialogOpen(false)}
        onSubmit={handleSubmitKillSwitch}
      />
    </div>
  );
}
