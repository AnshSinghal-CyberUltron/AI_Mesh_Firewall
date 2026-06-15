import { useRef, useState } from "react";
import { Clock, Shield, AlertTriangle, XCircle, CheckCircle, Pin, X, ArrowRightLeft } from "lucide-react";
import {
  formatDecisionSource,
  formatDetectionTier,
  formatRoutingReason,
  formatZeroshieldScanSummary,
  ZEROSHIELD_GUARD_MODEL_LABEL,
} from "../../constants/zeroshieldBrand";

const ACTION_THEME = {
  allow: {
    card: "border-emerald-200 bg-emerald-50/80 text-emerald-900 dark:border-emerald-500/30 dark:bg-emerald-500/10 dark:text-emerald-50",
    badge: "bg-emerald-500/15 text-emerald-700 dark:text-emerald-300",
    dot: "bg-emerald-500",
    icon: "text-emerald-500",
    highlight: "ring-emerald-400/40 shadow-emerald-500/20",
  },
  block: {
    card: "border-rose-200 bg-rose-50/80 text-rose-900 dark:border-rose-500/30 dark:bg-rose-500/10 dark:text-rose-50",
    badge: "bg-rose-500/15 text-rose-700 dark:text-rose-300",
    dot: "bg-rose-500",
    icon: "text-rose-500",
    highlight: "ring-rose-400/40 shadow-rose-500/20",
  },
  flag: {
    card: "border-amber-200 bg-amber-50/80 text-amber-900 dark:border-amber-500/30 dark:bg-amber-500/10 dark:text-amber-50",
    badge: "bg-amber-500/15 text-amber-700 dark:text-amber-300",
    dot: "bg-amber-500",
    icon: "text-amber-500",
    highlight: "ring-amber-400/40 shadow-amber-500/20",
  },
  redact: {
    card: "border-sky-200 bg-sky-50/80 text-sky-900 dark:border-sky-500/30 dark:bg-sky-500/10 dark:text-sky-50",
    badge: "bg-sky-500/15 text-sky-700 dark:text-sky-300",
    dot: "bg-sky-500",
    icon: "text-sky-500",
    highlight: "ring-sky-400/40 shadow-sky-500/20",
  },
  skip: {
    card: "border-zinc-200 bg-zinc-50/90 text-zinc-900 dark:border-zinc-600/40 dark:bg-zinc-700/20 dark:text-zinc-50",
    badge: "bg-zinc-500/15 text-zinc-700 dark:text-zinc-300",
    dot: "bg-zinc-500",
    icon: "text-zinc-500",
    highlight: "ring-zinc-400/40 shadow-zinc-500/20",
  },
  needs_model: {
    card: "border-violet-200 bg-violet-50/80 text-violet-900 dark:border-violet-500/30 dark:bg-violet-500/10 dark:text-violet-50",
    badge: "bg-violet-500/15 text-violet-700 dark:text-violet-300",
    dot: "bg-violet-500",
    icon: "text-violet-500",
    highlight: "ring-violet-400/40 shadow-violet-500/20",
  },
  reroute: {
    card: "border-indigo-200 bg-indigo-50/80 text-indigo-900 dark:border-indigo-500/30 dark:bg-indigo-500/10 dark:text-indigo-50",
    badge: "bg-indigo-500/15 text-indigo-700 dark:text-indigo-300",
    dot: "bg-indigo-500",
    icon: "text-indigo-500",
    highlight: "ring-indigo-400/40 shadow-indigo-500/20",
  },
};

const ACTION_ICONS = {
  allow: CheckCircle,
  block: XCircle,
  flag: AlertTriangle,
  redact: Shield,
  skip: Clock,
  needs_model: AlertTriangle,
  reroute: ArrowRightLeft,
};

function formatStageLatency(stage) {
  const ms = Number(stage?.latency_ms);
  if (Number.isFinite(ms) && ms >= 0) {
    return `${ms < 1 ? "<1" : Math.round(ms * 10) / 10}ms latency`;
  }
  // Honest placeholder: never fabricate a latency number when the gateway did
  // not report one for this stage. Both render sites cope with the dash — the
  // stage card shows "—" and the detail card's `.replace(" latency", "")`
  // leaves it untouched.
  return "—";
}

/**
 * Reusable horizontal pipeline stage visualization.
 * Shows color-coded stages with hoverable detail cards, latency, and threat info.
 * Detail card appears on hover and stays visible when hovering over the card itself.
 */
export function StageTimeline({ stages = [], className = "" }) {
  const containerRef = useRef(null);
  const stageRefs = useRef({});
  const [hoveredStage, setHoveredStage] = useState(null);
  const [expandedStage, setExpandedStage] = useState(null);
  const [popoverPos, setPopoverPos] = useState({ left: 180, top: 160 });

  if (!stages.length) return null;

  const visibleStageIndex = hoveredStage !== null ? hoveredStage : expandedStage;

  const updatePopoverPosition = (index) => {
    const container = containerRef.current;
    const stageEl = stageRefs.current[index];
    if (!container || !stageEl) return;

    const containerRect = container.getBoundingClientRect();
    const stageRect = stageEl.getBoundingClientRect();
    const desiredLeft = stageRect.left - containerRect.left + stageRect.width / 2;
    const clampedLeft = Math.max(180, Math.min(desiredLeft, containerRect.width - 180));

    setPopoverPos({
      left: clampedLeft,
      top: stageRect.bottom - containerRect.top + 16,
    });
  };

  const handleStageEnter = (index) => {
    setHoveredStage(index);
    updatePopoverPosition(index);
  };

  const handleStageClick = (index) => {
    const next = expandedStage === index ? null : index;
    setExpandedStage(next);
    if (next !== null) {
      updatePopoverPosition(next);
    }
  };

  return (
    <div
      ref={containerRef}
      className={`relative rounded-2xl border border-slate-200/80 bg-gradient-to-b from-white to-slate-50 p-3 shadow-sm dark:border-slate-700/70 dark:from-slate-900 dark:to-slate-900/70 ${className}`}
      onMouseLeave={() => setHoveredStage(null)}
    >
      <div className="pointer-events-none absolute left-8 right-8 top-[56px] h-px bg-gradient-to-r from-transparent via-slate-300 to-transparent dark:via-slate-600" />

      <div className="flex items-stretch gap-3 overflow-x-auto pb-2 pt-1">
        {stages.map((stage, i) => {
          const theme = ACTION_THEME[stage.action] || ACTION_THEME.skip;
          const Icon = ACTION_ICONS[stage.action] || Clock;
          const isHovered = hoveredStage === i;
          const isExpanded = expandedStage === i;
          const isActive = isHovered || isExpanded;

          return (
            <div key={i} className="relative flex items-center">
              <button
                ref={(el) => {
                  stageRefs.current[i] = el;
                }}
                onMouseEnter={() => handleStageEnter(i)}
                onClick={() => handleStageClick(i)}
                className={`group relative min-w-[132px] cursor-pointer rounded-2xl border px-3 py-3 text-left transition-all duration-200 hover:-translate-y-0.5 hover:shadow-lg ${theme.card} ${
                  isActive ? `ring-2 ${theme.highlight}` : "ring-0"
                }`}
              >
                <div className="mb-2 flex items-center justify-between">
                  <div className={`inline-flex h-8 w-8 items-center justify-center rounded-xl ${theme.badge}`}>
                    <Icon className={`h-4 w-4 ${theme.icon}`} />
                  </div>
                  <span className={`rounded-full px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wide ${theme.badge}`}>
                    {stage.action}
                  </span>
                </div>

                <div className="text-xs font-semibold capitalize leading-tight text-slate-800 dark:text-slate-100">
                  {(stage.name || "").replace(/_/g, " ")}
                </div>
                <div className="mt-1 text-[11px] text-slate-500 dark:text-slate-400">
                  {formatStageLatency(stage)}
                </div>

                <div className="mt-2 flex items-center gap-1.5">
                  <span className={`h-1.5 w-1.5 rounded-full ${theme.dot} ${isActive ? "animate-pulse" : ""}`} />
                  <span className="text-[10px] text-slate-500 dark:text-slate-400">
                    {isExpanded ? "Pinned" : "Hover for details"}
                  </span>
                </div>
              </button>

              {i < stages.length - 1 && (
                <div className="mx-1 h-[2px] w-4 rounded-full bg-slate-300/70 dark:bg-slate-600/60" />
              )}
            </div>
          );
        })}
      </div>

      {visibleStageIndex !== null && stages[visibleStageIndex] && (
        <div
          className="absolute z-40 w-[360px] max-w-[calc(100%-1rem)] -translate-x-1/2"
          style={{ left: `${popoverPos.left}px`, top: `${popoverPos.top}px` }}
          onMouseEnter={() => setHoveredStage(visibleStageIndex)}
          onMouseLeave={() => setHoveredStage(null)}
        >
          <StageDetailCard
            stage={stages[visibleStageIndex]}
            onClose={() => {
              setExpandedStage(null);
              setHoveredStage(null);
            }}
            isPinned={expandedStage === visibleStageIndex}
          />
        </div>
      )}

      <div className="mt-2 text-[11px] text-slate-500 dark:text-slate-400">
        Tip: Hover a stage for instant detail, click to pin details while comparing stages.
      </div>
    </div>
  );
}

function BeforeAfterBlock({ beforeLabel, beforeText, afterLabel, afterText }) {
  return (
    <div className="col-span-2 mt-1 space-y-2">
      <div>
        <span className="mb-1 block text-slate-500 dark:text-slate-400">{beforeLabel}:</span>
        <pre className="max-h-40 overflow-y-auto rounded-lg border border-slate-200 bg-slate-100/80 p-2 font-mono text-[11px] whitespace-pre-wrap text-slate-700 dark:border-slate-700 dark:bg-slate-800/70 dark:text-slate-200">
          {beforeText}
        </pre>
      </div>
      <div>
        <span className="mb-1 block text-slate-500 dark:text-slate-400">{afterLabel}:</span>
        <pre className="max-h-40 overflow-y-auto rounded-lg border border-emerald-200 bg-emerald-50/70 p-2 font-mono text-[11px] whitespace-pre-wrap text-slate-700 dark:border-emerald-500/30 dark:bg-emerald-500/10 dark:text-slate-100">
          {afterText}
        </pre>
      </div>
    </div>
  );
}

const BEFORE_AFTER_LABELS = {
  policy: { before: "Before", after: "After policy redaction" },
  input_scan: { before: "Scanned input", after: "Forwarded to model" },
  model_input: { before: "Scanned input", after: "Forwarded to model" },
  output_guardrail: { before: "Model output", after: "After output guard" },
  default: { before: "Input", after: "Output" },
};

function StageDetailCard({ stage, onClose, isPinned }) {
  const theme = ACTION_THEME[stage.action] || ACTION_THEME.skip;
  const hasWeights = stage.weights && typeof stage.weights === "object" && Object.keys(stage.weights).length > 0;
  const hasDecisionFactors = Array.isArray(stage.decision_factors) && stage.decision_factors.length > 0;

  // Before/after (Input -> Output) detection: render only when both sides are
  // present AND actually differ. Otherwise fall back to the legacy single block.
  const promptIn = typeof stage.prompt_in === "string" ? stage.prompt_in : "";
  const promptOut = typeof stage.prompt_out === "string" ? stage.prompt_out : "";
  const hasBeforeAfter = promptIn.length > 0 && promptOut.length > 0 && promptIn !== promptOut;
  const baLabels = BEFORE_AFTER_LABELS[stage.name] || BEFORE_AFTER_LABELS.default;

  // Evidence de-duplication. The guard_reason violet block is the canonical
  // "why" — anything already contained in it must not be echoed again.
  const guardReason = typeof stage.guard_reason === "string" ? stage.guard_reason : "";
  const guardReasonLc = guardReason.toLowerCase();
  const inGuardReason = (value) => {
    const v = String(value ?? "").trim().toLowerCase();
    return v.length > 0 && guardReasonLc.includes(v);
  };

  // Suppress the standalone detail line when it is empty or already a substring
  // of guard_reason (the most common duplication the user complained about).
  const detailText = typeof stage.detail === "string" ? stage.detail.trim() : stage.detail;
  const showDetail = Boolean(detailText) && !(guardReason && inGuardReason(detailText));

  // Only surface patterns / findings whose text is NOT already in guard_reason.
  const dedupedPatterns = Array.isArray(stage.matched_patterns)
    ? stage.matched_patterns.filter((p) => !inGuardReason(p))
    : [];
  const dedupedFindings = Array.isArray(stage.guard_findings)
    ? stage.guard_findings.filter((f) => !inGuardReason(f))
    : [];

  // When we render the before/after block, suppress the legacy prompt_submitted
  // block for the same stage to avoid showing the same text twice.
  const showPromptSubmitted = Boolean(stage.prompt_submitted) && !hasBeforeAfter;

  return (
    <div className={`rounded-2xl border bg-white/95 p-4 shadow-2xl backdrop-blur-sm dark:bg-slate-900/95 ${theme.card}`}>
      <div className="mb-3 flex items-center justify-between gap-3">
        <h4 className="text-sm font-semibold capitalize text-slate-900 dark:text-slate-100">
          {(stage.name || "").replace(/_/g, " ")} - <span className={theme.icon}>{stage.action}</span>
        </h4>
        <button
          onClick={onClose}
          className="inline-flex items-center gap-1 rounded-full border border-slate-200 px-2 py-1 text-[10px] font-medium text-slate-500 hover:bg-slate-100 dark:border-slate-700 dark:text-slate-400 dark:hover:bg-slate-800"
        >
          {isPinned ? <X className="h-3 w-3" /> : <Pin className="h-3 w-3" />}
          {isPinned ? "Close" : "Pin"}
        </button>
      </div>

      <div className="grid grid-cols-2 gap-2 text-xs text-slate-600 dark:text-slate-300">
        {(stage.name === "input_scan" || stage.name === "output_guardrail") && stage.guard_reason && (
          <div className="col-span-2 rounded-xl border border-violet-200/80 bg-violet-50/90 p-3 dark:border-violet-500/30 dark:bg-violet-500/10">
            <div className="mb-1 text-[10px] font-semibold uppercase tracking-wide text-violet-700 dark:text-violet-300">
              {stage.guard_model || ZEROSHIELD_GUARD_MODEL_LABEL}
            </div>
            <pre className="whitespace-pre-wrap font-sans text-[11px] leading-relaxed text-slate-800 dark:text-slate-100">
              {stage.guard_reason}
            </pre>
            {stage.reason_code && (
              <div className="mt-2 text-[10px] text-violet-600 dark:text-violet-400">
                Reason code: <span className="font-mono">{stage.reason_code}</span>
                {stage.recommended_action ? (
                  <span className="ml-2">
                    · Model recommendation: <span className="font-semibold uppercase">{stage.recommended_action}</span>
                  </span>
                ) : null}
              </div>
            )}
          </div>
        )}
        {hasBeforeAfter && (
          <BeforeAfterBlock
            beforeLabel={baLabels.before}
            beforeText={promptIn}
            afterLabel={baLabels.after}
            afterText={promptOut}
          />
        )}
        {showDetail && (
          <div className="col-span-2">
            <span className="text-slate-500 dark:text-slate-400">
              {stage.action === "block" ? "Block reason:" : "Detail:"}
            </span>{" "}
            <span className="text-slate-700 dark:text-slate-200">{detailText}</span>
          </div>
        )}
        {stage.action === "allow" && stage.name === "input_scan" && stage.tier && (
          <div className="col-span-2 text-[11px] text-emerald-700 dark:text-emerald-300">
            Scan ran ({stage.tier}) — request was not blocked; later stages executed normally.
          </div>
        )}
        {stage.name === "input_scan" && (() => {
          const scan = formatZeroshieldScanSummary({
            detection_tier: stage.tier,
            threat_type: stage.threat_type,
            confidence: stage.confidence,
            risk_score: stage.risk_score,
            scan_outcome: stage.scan_outcome,
            action: stage.action,
          });
          return (
            <>
              <div>
                <span className="text-slate-500 dark:text-slate-400">Threat:</span>{" "}
                <span className={scan.clean ? "text-emerald-700 dark:text-emerald-300" : "text-rose-600 dark:text-rose-300"}>
                  {scan.threatLabel}
                </span>
              </div>
              <div>
                <span className="text-slate-500 dark:text-slate-400">{scan.scoreLabel}:</span>{" "}
                <span className="text-slate-700 dark:text-slate-200">{scan.scoreValue}</span>
              </div>
            </>
          );
        })()}
        {stage.name !== "input_scan" && stage.threat_type && !["none", "clean"].includes(String(stage.threat_type).toLowerCase()) && (
          <div>
            <span className="text-slate-500 dark:text-slate-400">Threat:</span>{" "}
            <span className="text-rose-600 dark:text-rose-300">{stage.threat_type}</span>
          </div>
        )}
        <div>
          <span className="text-slate-500 dark:text-slate-400">Latency:</span>{" "}
          <span className="text-slate-700 dark:text-slate-200">{formatStageLatency(stage).replace(" latency", "")}</span>
        </div>
        {showPromptSubmitted && (
          <div className="col-span-2 mt-1">
            <span className="mb-1 block text-slate-500 dark:text-slate-400">Prompt submitted:</span>
            <pre className="max-h-40 overflow-y-auto rounded-lg border border-slate-200 bg-slate-100/80 p-2 font-mono text-[11px] whitespace-pre-wrap text-slate-700 dark:border-slate-700 dark:bg-slate-800/70 dark:text-slate-200">
              {stage.prompt_submitted}
            </pre>
          </div>
        )}
        {stage.requested_model && (
          <div>
            <span className="text-slate-500 dark:text-slate-400">Requested model:</span>{" "}
            <span className="font-mono text-slate-700 dark:text-slate-200">{stage.requested_model}</span>
          </div>
        )}
        {stage.selected_model && (
          <div>
            <span className="text-slate-500 dark:text-slate-400">Selected model:</span>{" "}
            <span className="font-mono text-slate-700 dark:text-slate-200">{stage.selected_model}</span>
          </div>
        )}
        {stage.decision_source && (
          <div>
            <span className="text-slate-500 dark:text-slate-400">Decision source:</span>{" "}
            <span className="text-slate-700 dark:text-slate-200">
              {stage.decision_source_label || formatDecisionSource(stage.decision_source)}
            </span>
          </div>
        )}
        {stage.routing_reason && (
          <div className="col-span-2">
            <span className="text-slate-500 dark:text-slate-400">Routing reason:</span>{" "}
            <span className="text-slate-700 dark:text-slate-200">
              {formatRoutingReason(stage.routing_reason, { decisionSource: stage.decision_source })}
            </span>
          </div>
        )}
        {stage.policy_summary && (
          <div className="col-span-2">
            <span className="text-slate-500 dark:text-slate-400">Policy summary:</span>{" "}
            <span className="text-slate-700 dark:text-slate-200">{stage.policy_summary}</span>
          </div>
        )}
        {hasDecisionFactors && (
          <div className="col-span-2">
            <span className="text-slate-500 dark:text-slate-400">Decision factors:</span>{" "}
            <span className="text-slate-700 dark:text-slate-200">{stage.decision_factors.join(", ")}</span>
          </div>
        )}
        {hasWeights && (
          <div className="col-span-2">
            <span className="text-slate-500 dark:text-slate-400">Applied weights:</span>{" "}
            <span className="text-slate-700 dark:text-slate-200">
              {Object.entries(stage.weights)
                .map(([k, v]) => {
                  const raw = Number(v);
                  const pct = Number.isFinite(raw) ? `${Math.round(raw * 100)}%` : String(v);
                  return `${k}=${pct}`;
                })
                .join(", ")}
            </span>
          </div>
        )}
        {stage.tier && (
          <div>
            <span className="text-slate-500 dark:text-slate-400">Tier:</span>{" "}
            <span className="text-slate-700 dark:text-slate-200">{formatDetectionTier(stage.tier)}</span>
          </div>
        )}
        {dedupedPatterns.length > 0 && (
          <div className="col-span-2">
            <span className="text-slate-500 dark:text-slate-400">Patterns:</span>{" "}
            <span className="text-amber-600 dark:text-amber-300">{dedupedPatterns.join(", ")}</span>
          </div>
        )}
        {dedupedFindings.length > 0 && (
          <div className="col-span-2">
            <span className="text-slate-500 dark:text-slate-400">Evidence:</span>{" "}
            <span className="text-amber-600 dark:text-amber-300">{dedupedFindings.join(", ")}</span>
          </div>
        )}
        {stage.matched_policies?.length > 0 && (
          <div className="col-span-2">
            <span className="text-slate-500 dark:text-slate-400">Policies:</span>{" "}
            <span className="text-sky-600 dark:text-sky-300">{stage.matched_policies.join(", ")}</span>
          </div>
        )}
        {stage.matched_rules?.length > 0 && (
          <div className="col-span-2">
            <span className="text-slate-500 dark:text-slate-400">Rules applied:</span>{" "}
            <span className="text-sky-600 dark:text-sky-300">{stage.matched_rules.join(", ")}</span>
          </div>
        )}
        {stage.docs_in !== undefined && (
          <div className="col-span-2">
            <span className="text-slate-500 dark:text-slate-400">Docs:</span>{" "}
            <span className="text-slate-700 dark:text-slate-200">
              {stage.docs_in} in {"->"} {stage.docs_out} out ({stage.docs_dropped} dropped)
            </span>
          </div>
        )}
        {stage.content && !hasBeforeAfter && (
          <div className="col-span-2 mt-1">
            <span className="mb-1 block text-slate-500 dark:text-slate-400">Content:</span>
            <pre className="max-h-40 overflow-y-auto rounded-lg border border-slate-200 bg-slate-100/80 p-2 font-mono text-[11px] whitespace-pre-wrap text-slate-700 dark:border-slate-700 dark:bg-slate-800/70 dark:text-slate-200">
              {stage.content}
            </pre>
          </div>
        )}
      </div>
    </div>
  );
}
