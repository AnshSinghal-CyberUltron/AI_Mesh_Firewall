import {
  MessageSquare, Cpu, Shield, Brain, CheckCircle, XCircle, AlertTriangle,
  EyeOff, Eye, Clock, ChevronRight, Lock,
} from "lucide-react";

const STAGE_ICONS = {
  input: MessageSquare,
  raw_output: Cpu,
  evaluation: Shield,
  reasoning: Brain,
  action: CheckCircle,
  final_output: Lock,
};

const ACTION_COLORS = {
  block: { border: "border-red-400 dark:border-red-600", bg: "bg-red-50 dark:bg-red-900/20", dot: "bg-red-500", text: "text-red-700 dark:text-red-300" },
  redact: { border: "border-amber-400 dark:border-amber-600", bg: "bg-amber-50 dark:bg-amber-900/20", dot: "bg-amber-500", text: "text-amber-700 dark:text-amber-300" },
  flag: { border: "border-orange-400 dark:border-orange-600", bg: "bg-orange-50 dark:bg-orange-900/20", dot: "bg-orange-500", text: "text-orange-700 dark:text-orange-300" },
  allow: { border: "border-emerald-400 dark:border-emerald-600", bg: "bg-emerald-50 dark:bg-emerald-900/20", dot: "bg-emerald-500", text: "text-emerald-700 dark:text-emerald-300" },
};

function StageNode({ stage, isLast, actionColor }) {
  const Icon = STAGE_ICONS[stage.id] || Shield;
  const colors = stage.highlight ? (ACTION_COLORS[stage.highlightAction] || ACTION_COLORS.allow) : { border: "border-slate-200 dark:border-slate-700", bg: "bg-white dark:bg-slate-900/40", dot: "bg-teal-500", text: "text-slate-700 dark:text-slate-300" };

  return (
    <div className="flex items-stretch gap-3">
      {/* Timeline connector */}
      <div className="flex flex-col items-center w-6 flex-shrink-0">
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
            <span className={`text-[9px] font-bold px-1.5 py-0.5 rounded-full ${
              stage.badge === "blocked" ? "bg-red-100 dark:bg-red-900/30 text-red-600 dark:text-red-400" :
              stage.badge === "redacted" ? "bg-amber-100 dark:bg-amber-900/30 text-amber-600 dark:text-amber-400" :
              stage.badge === "flagged" ? "bg-orange-100 dark:bg-orange-900/30 text-orange-600 dark:text-orange-400" :
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
              <span key={i} className="inline-flex items-center gap-0.5 px-1.5 py-0.5 rounded text-[9px] bg-slate-100 dark:bg-slate-800 text-slate-600 dark:text-slate-400 border border-slate-200 dark:border-slate-700">
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
  const complianceTags = meta.compliance_tags || extra.compliance_tags || [];
  const latency = meta.latency_ms || extra.latency_ms || 0;
  const model = meta.model || "";

  // Build pipeline stages
  const stages = [];

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
    metrics: model ? [{ label: "Model", value: model }] : [],
  });

  // Stage 3: Guardrail Evaluation
  const evalTags = [];
  if (threatType) evalTags.push(`Category: ${threatType.replace(/_/g, " ")}`);
  if (confidencePct > 0) evalTags.push(`Confidence: ${confidencePct}%`);
  matchedPatterns.forEach(p => evalTags.push(p));

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
  const actionLabel = action === "block" ? "BLOCKED — Response not delivered"
    : action === "redact" ? "REDACTED — Sensitive content removed"
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

  return (
    <div className="pt-1">
      <div className="flex items-center gap-2 mb-3">
        <Shield className="w-3.5 h-3.5 text-teal-500" />
        <span className="text-[11px] font-semibold text-teal-600 dark:text-teal-400 uppercase tracking-wider">
          Ingestion Pipeline — Step-by-Step
        </span>
      </div>
      <div className="pl-0.5">
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
