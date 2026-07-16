import {
  MessageSquare, Cpu, Shield, Brain, CheckCircle, XCircle, AlertTriangle,
  EyeOff, Eye, Clock, ChevronRight, Lock,
} from "lucide-react";
import { buildHonestTraceStages, extractRealStages, extractFinalAction } from "../utils/pipelineTrace";

const STAGE_ICONS = {
  input: MessageSquare,
  raw_output: Cpu,
  evaluation: Shield,
  reasoning: Brain,
  action: CheckCircle,
  final_output: Lock,
  // real pipeline_trace stage ids (TRACE_UI_CONTRACT.md)
  policy: Shield,
  policy_redact: EyeOff,
  input_scan: Shield,
  route: ChevronRight,
  model_routing: ChevronRight,
  llm: Cpu,
  model_input: MessageSquare,
  model_output: Cpu,
  output_guard: Lock,
  output_guardrail: Lock,
  auth: Lock,
  rate_limit: Clock,
  kill_switch: XCircle,
};

const ACTION_COLORS = {
  block: { border: "border-red-400 dark:border-red-600", bg: "bg-red-50 dark:bg-red-900/20", dot: "bg-red-500", text: "text-red-700 dark:text-red-300" },
  redact: { border: "border-amber-400 dark:border-amber-600", bg: "bg-amber-50 dark:bg-amber-900/20", dot: "bg-amber-500", text: "text-amber-700 dark:text-amber-300" },
  rewrite: { border: "border-violet-400 dark:border-violet-600", bg: "bg-violet-50 dark:bg-violet-900/20", dot: "bg-violet-500", text: "text-violet-700 dark:text-violet-300" },
  flag: { border: "border-orange-400 dark:border-orange-600", bg: "bg-orange-50 dark:bg-orange-900/20", dot: "bg-orange-500", text: "text-orange-700 dark:text-orange-300" },
  monitor: { border: "border-sky-400 dark:border-sky-600", bg: "bg-sky-50 dark:bg-sky-900/20", dot: "bg-sky-500", text: "text-sky-700 dark:text-sky-300" },
  reroute: { border: "border-teal-400 dark:border-teal-600", bg: "bg-teal-50 dark:bg-teal-900/20", dot: "bg-teal-500", text: "text-teal-700 dark:text-teal-300" },
  skip: { border: "border-slate-300 dark:border-slate-600", bg: "bg-slate-50 dark:bg-slate-800/40", dot: "bg-slate-400", text: "text-slate-500 dark:text-slate-400" },
  allow: { border: "border-emerald-400 dark:border-emerald-600", bg: "bg-emerald-50 dark:bg-emerald-900/20", dot: "bg-emerald-500", text: "text-emerald-700 dark:text-emerald-300" },
};


function StageNode({ stage, isLast, actionColor }) {
  const Icon = STAGE_ICONS[stage.id] || Shield;
  // impeccable-disable-next-line gray-on-color: dot(bg-teal-500) and text(slate) apply to SEPARATE elements — the teal dot holds a white icon, the slate label sits on the card bg (bg-white/slate-900). No gray text is rendered on teal; the static heuristic just pairs them from one object literal.
  const colors = stage.highlight ? (ACTION_COLORS[stage.highlightAction] || ACTION_COLORS.allow) : { border: "border-slate-200 dark:border-slate-700", bg: "bg-white dark:bg-slate-900/40", dot: "bg-teal-500", text: "text-slate-700 dark:text-slate-300" };

  // a11y: the stage's action state is otherwise conveyed by colour ALONE (dot/border).
  // Surface it as text for screen readers + a hover title, so a colour-blind or SR user
  // perceives the honest action (block/redact/flag/allow) — not just a coloured dot.
  const stageState = stage.badge || stage.highlightAction || (stage.highlight ? actionColor : "");
  const ariaLabel = stageState
    ? `${stage.label} — ${String(stageState).replace(/_/g, " ")}`
    : stage.label;

  return (
    <div className="flex items-stretch gap-3" role="listitem" aria-label={ariaLabel} title={ariaLabel}>
      {/* Timeline connector (decorative — state is announced via the aria-label above) */}
      <div className="flex flex-col items-center w-6 flex-shrink-0" aria-hidden="true">
        <div className={`w-5 h-5 rounded-full flex items-center justify-center ${colors.dot} ring-2 ring-white dark:ring-slate-900`}>
          <Icon className="w-2.5 h-2.5 text-white" />
        </div>
        {!isLast && <div className="w-0.5 flex-1 bg-slate-200 dark:bg-slate-700 my-0.5" />}
      </div>

      {/* Stage content */}
      <div className={`flex-1 mb-2.5 rounded-xl border ${colors.border} ${colors.bg} p-3 min-w-0`}>
        <div className="flex items-center justify-between mb-1">
          <span className={`text-[11px] font-semibold uppercase tracking-wider ${colors.text}`}>
            {stage.label}
          </span>
          {stage.badge && (
            <span className={`text-[10px] font-bold px-1.5 py-0.5 rounded-full ${
              stage.badge === "blocked" ? "bg-red-100 dark:bg-red-900/30 text-red-600 dark:text-red-400" :
              stage.badge === "redacted" ? "bg-amber-100 dark:bg-amber-900/30 text-amber-600 dark:text-amber-400" :
              stage.badge === "rewritten" ? "bg-violet-100 dark:bg-violet-900/30 text-violet-600 dark:text-violet-400" :
              stage.badge === "flagged" ? "bg-orange-100 dark:bg-orange-900/30 text-orange-600 dark:text-orange-400" :
              stage.badge === "monitored" ? "bg-sky-100 dark:bg-sky-900/30 text-sky-600 dark:text-sky-400" :
              stage.badge === "rerouted" ? "bg-teal-100 dark:bg-teal-900/30 text-teal-600 dark:text-teal-400" :
              stage.badge === "skipped" ? "bg-slate-100 dark:bg-slate-800 text-slate-500 dark:text-slate-400" :
              "bg-emerald-100 dark:bg-emerald-900/30 text-emerald-600 dark:text-emerald-400"
            }`}>
              {stage.badge.toUpperCase()}
            </span>
          )}
        </div>
        {stage.content && (
          <div className="text-xs text-slate-600 dark:text-slate-300 font-mono bg-white/60 dark:bg-slate-800/50 rounded-lg p-2 whitespace-pre-wrap break-words max-h-28 overflow-auto mt-1">
            {stage.content}
          </div>
        )}
        {stage.tags && stage.tags.length > 0 && (
          <div className="flex flex-wrap gap-1 mt-1.5">
            {stage.tags.map((tag, i) => (
              <span key={i} className="inline-flex items-center gap-0.5 px-1.5 py-0.5 rounded text-[10px] bg-slate-100 dark:bg-slate-800 text-slate-600 dark:text-slate-400 border border-slate-200 dark:border-slate-700">
                {tag}
              </span>
            ))}
          </div>
        )}
        {stage.metrics && (
          <div className="flex flex-wrap gap-3 mt-1.5 text-[10px] text-slate-500 dark:text-slate-400">
            {stage.metrics.map((m, i) => (
              <span key={i}>{m.label}: <strong className="text-slate-700 dark:text-slate-300">{m.value}</strong></span>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

export function OutputPipelineTimeline({ event }) {
  if (!event) return null;

  const meta = event.metadata || {};
  const extra = meta.extra || {};
  const action = event.action || "allow";

  // Extract pipeline data
  const promptSnippet = (meta.prompt_lineage && meta.prompt_lineage[0]?.prompt) || meta.prompt_snippet || "";
  const rawOutput = extra.raw_output || meta.raw_output || extra.response_snippet || meta.response_snippet || "";
  const sanitizedOutput = extra.sanitized_output || meta.sanitized_output || "";
  const guardrailReasoning = extra.guardrail_reasoning || meta.guardrail_reasoning || extra.detail || meta.detail || "";
  const threatType = meta.threat_category || meta.threat_type || extra.threat_type || "";
  const confidence = meta.risk_score || meta.security_risk_score || 0;
  const confidencePct = (typeof confidence === "number" && confidence <= 1) ? Math.round(confidence * 100) : Math.round(confidence);
  const matchedPatterns = extra.matched_patterns || meta.matched_patterns || [];
  const matchedValues = extra.matched_values || meta.matched_values || {};
  const outputSnippetTruncated = extra.output_snippet_truncated ?? meta.output_snippet_truncated;
  const complianceTags = meta.compliance_tags || extra.compliance_tags || [];
  const latency = meta.latency_ms || extra.latency_ms || 0;
  const model = meta.model || "";
  const matchedValueEntries = Object.entries(
    matchedValues && typeof matchedValues === "object" ? matchedValues : {},
  );

  // Build pipeline stages — prefer the gateway's REAL per-stage trace (honest); the
  // narrative view below is only a fallback for events that carry no pipeline_trace.
  let stages = [];
  const realStages = extractRealStages(event);
  if (realStages.length) {
    stages = buildHonestTraceStages(realStages, extractFinalAction(event, action), { promptSnippet, rawOutput, sanitizedOutput });
  }

  if (stages.length === 0) {
  // Stage 1: User Input
  stages.push({
    id: "input",
    label: "1. User Input",
    content: promptSnippet || "(prompt not captured)",
  });

  // Stage 2: Raw LLM Output (before guardrails)
  stages.push({
    id: "raw_output",
    label: "2. Raw Model Output",
    content: rawOutput || "(raw output not available)",
    metrics: [
      ...(model ? [{ label: "Model", value: model }] : []),
      ...(outputSnippetTruncated
        ? [{
            label: "Note",
            value: `Showing the first ${Number(rawOutput.length).toLocaleString()} characters for display only — detection ran on the FULL output and the complete response was delivered to the client (nothing was truncated in delivery).`,
          }]
        : []),
    ],
  });

  // Stage 3: Guardrail Evaluation
  const evalTags = [];
  if (threatType) evalTags.push(`Category: ${threatType.replace(/_/g, " ")}`);
  if (confidencePct > 0) evalTags.push(`Confidence: ${confidencePct}%`);
  matchedPatterns.forEach(p => evalTags.push(p));
  matchedValueEntries.forEach(([key, value]) => {
    evalTags.push(`${key}: ${String(value).slice(0, 40)}${String(value).length > 40 ? "…" : ""}`);
  });

  stages.push({
    id: "evaluation",
    label: "3. Guardrail Evaluation",
    content: threatType
      ? `Detected: ${threatType.replace(/_/g, " ")} (confidence: ${confidencePct}%)`
      : "No threats detected — output is clean.",
    highlight: action !== "allow",
    highlightAction: action,
    tags: evalTags,
  });

  // Stage 4: Guardrail Reasoning
  stages.push({
    id: "reasoning",
    label: "4. Guardrail Reasoning",
    content: guardrailReasoning || (action === "allow" ? "No threats detected. Output passes all security checks." : "Automated guardrail decision based on pattern matching and threat classification."),
    highlight: action !== "allow",
    highlightAction: action,
  });

  // Stage 5: Action Taken
  const redactUnchanged = action === "redact"
    && sanitizedOutput
    && rawOutput
    && sanitizedOutput.trim() === rawOutput.trim();
  const actionLabel = action === "block" ? "BLOCKED — Response not delivered"
    : action === "redact"
      ? (redactUnchanged
        ? "REDACTED — Action applied; the visible snippet is unchanged because the masked spans fall outside the displayed preview (see matched spans above)"
        : "REDACTED — Sensitive content removed")
    : action === "flag" ? "FLAGGED — Marked for review"
    : "ALLOWED — Clean response delivered";

  stages.push({
    id: "action",
    label: "5. Guardrail Action",
    content: actionLabel,
    badge: action === "block" ? "blocked" : action === "redact" ? "redacted" : action === "flag" ? "flagged" : "allowed",
    highlight: true,
    highlightAction: action,
    metrics: [
      ...(latency > 0 ? [{ label: "Processing", value: `${Math.round(latency)}ms` }] : []),
      ...(complianceTags.length > 0 ? [{ label: "Compliance", value: complianceTags.join(", ") }] : []),
    ],
  });

  // Stage 6: Final Output
  const finalContent = action === "block"
    ? "[Response blocked — not delivered to user]"
    : action === "redact"
      ? sanitizedOutput || "(redacted output)"
      : rawOutput || "(output delivered as-is)";

  stages.push({
    id: "final_output",
    label: "6. Final Output to User",
    content: finalContent,
    highlight: action !== "allow",
    highlightAction: action === "block" ? "block" : "allow",
  });
  } // end synthetic-narrative fallback

  return (
    <div className="pt-1" role="region" aria-label="Output guardrail pipeline trace">
      <div className="flex items-center gap-2 mb-3">
        <Shield className="w-3.5 h-3.5 text-teal-500" aria-hidden="true" />
        <span className="text-[11px] font-semibold text-teal-600 dark:text-teal-400 uppercase tracking-wider">
          Ingestion Pipeline — Step-by-Step
        </span>
      </div>
      <div className="pl-0.5" role="list" aria-label="Pipeline stages in order">
        {stages.map((stage, i) => (
          <StageNode
            key={stage.id}
            stage={stage}
            isLast={i === stages.length - 1}
            actionColor={action}
          />
        ))}
      </div>
    </div>
  );
}
