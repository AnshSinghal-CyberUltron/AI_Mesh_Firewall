import { useState } from "react";
import { Shield, Eye, AlertTriangle, Lock } from "lucide-react";
import { useSimulatorEngine } from "../../hooks/useSimulatorEngine";
import { outputGuardChatBody, normalizeOutputGuardResult } from "../../utils/liveGateway";
import { SimulatorShell } from "./SimulatorShell";

const SCENARIOS = [
  {
    id: "pii-leak",
    label: "PII Leakage",
    badge: "attack",
    text: "The patient John Smith (SSN: 123-45-6789) was admitted on 2024-01-15. His credit card 4111-1111-1111-1111 is on file. Contact: john.smith@hospital.com, phone: (555) 123-4567.",
    context_chunks: ["Patient records contain sensitive PII that must be redacted before output."],
  },
  {
    id: "credential-exposure",
    label: "Credential Exposure",
    badge: "attack",
    text: "Here are the database credentials:\nHost: db.internal.company.com\nUsername: admin\nPassword: P@ssw0rd123!\nAPI Key: sk-proj-abc123def456ghi789\nAWS_SECRET_ACCESS_KEY=AKIAIOSFODNN7EXAMPLE",
    context_chunks: [],
  },
  {
    id: "hallucination",
    label: "Hallucinated Output",
    badge: "attack",
    text: "According to our latest financial report, the company's revenue was $4.2 billion in Q3 2024, representing a 340% year-over-year growth. Studies have conclusively proven that this product cures cancer. Research definitively shows these claims are absolutely certain.",
    context_chunks: ["Company revenue was $1.8 billion in Q3 2024, representing 12% year-over-year growth."],
  },
  {
    id: "ip-leakage",
    label: "IP Address Leakage",
    badge: "attack",
    text: "The internal server at 10.0.1.55 is running PostgreSQL on port 5432. The API gateway at 192.168.1.100:8300 handles all requests. External access is via load balancer at 203.0.113.50.",
    context_chunks: [],
  },
  {
    id: "clean-output",
    label: "Clean Output",
    badge: "safe",
    text: "The capital of France is Paris. It is known for the Eiffel Tower, built in 1889 for the World's Fair. Paris has a population of approximately 2.1 million people in the city proper.",
    context_chunks: ["Paris is the capital city of France with a population of about 2.1 million."],
  },
];

export function OutputGuardSimulator() {
  const engine = useSimulatorEngine();
  const [selected, setSelected] = useState(null);
  const [result, setResult] = useState(null);
  const [customText, setCustomText] = useState("");

  const hallucinationMetricStyle = {
    red: {
      text: "text-red-700 dark:text-red-300",
      bar: "bg-red-500",
    },
    amber: {
      text: "text-amber-700 dark:text-amber-300",
      bar: "bg-amber-500",
    },
    blue: {
      text: "text-blue-700 dark:text-blue-300",
      bar: "bg-blue-500",
    },
    purple: {
      text: "text-purple-700 dark:text-purple-300",
      bar: "bg-purple-500",
    },
  };

  const handleExecute = async () => {
    const text = selected?.text || customText;
    if (!text.trim()) return;

    const res = await engine.gatewayFetch("/v1/chat/completions", {
      method: "POST",
      body: JSON.stringify(
        outputGuardChatBody(text, selected?.context_chunks || []),
      ),
    });
    setResult(
      res.ok
        ? normalizeOutputGuardResult(res.data, res.status)
        : { error: res.data?.message || res.data?.error || "Request failed", success: false },
    );
  };

  return (
    <SimulatorShell
      title="Output Guard Simulator"
      description="Inspect LLM outputs for PII, credentials, IP leakage, and hallucination scoring"
      connectionStatus={engine.connectionStatus}
      gatewayUrl={engine.gatewayUrl}
      gatewayKey={engine.gatewayKey}
      onKeyChange={engine.setGatewayKey}
      scenarios={SCENARIOS}
      selectedScenario={selected}
      onSelectScenario={setSelected}
      onExecute={handleExecute}
      executing={engine.executing}
      result={result}
      customInput={
        !selected && (
          <textarea
            value={customText}
            onChange={(e) => setCustomText(e.target.value)}
            rows={3}
            placeholder="Paste LLM output text to inspect..."
            className="w-full px-2 py-1.5 rounded-md bg-slate-100 dark:bg-slate-900/60 border border-slate-300 dark:border-slate-700 text-xs text-slate-700 dark:text-slate-300 placeholder:text-slate-500 dark:placeholder:text-slate-500 resize-none"
          />
        )
      }
    >
      {result && !result.error && (
        <div className="px-4 py-3 space-y-4">
          {/* Verdict banner */}
          <div className={`p-3 rounded-lg border ${
            result.action === "block" ? "bg-red-500/10 border-red-500/30" :
            result.action === "flag" ? "bg-amber-500/10 border-amber-500/30" :
            "bg-emerald-500/10 border-emerald-500/30"
          }`}>
            <div className="flex items-center gap-2 mb-1">
              {result.action === "block" ? (
                <AlertTriangle className="w-4 h-4 text-red-700 dark:text-red-300" />
              ) : result.action === "flag" ? (
                <Eye className="w-4 h-4 text-amber-700 dark:text-amber-300" />
              ) : (
                <Shield className="w-4 h-4 text-emerald-700 dark:text-emerald-300" />
              )}
              <span className={`text-sm font-semibold ${
                result.action === "block" ? "text-red-700 dark:text-red-300" :
                result.action === "flag" ? "text-amber-700 dark:text-amber-300" : "text-emerald-700 dark:text-emerald-300"
              }`}>
                {result.action?.toUpperCase()}
              </span>
              {result.confidence != null && (
                <span className="text-[10px] text-slate-600 dark:text-slate-400">
                  Confidence: {(result.confidence * 100).toFixed(0)}%
                </span>
              )}
              {result.latency_ms != null && (
                <span className="text-[10px] text-slate-600 dark:text-slate-400 ml-auto">{result.latency_ms}ms</span>
              )}
            </div>
            {result.detail && <p className="text-xs text-slate-600 dark:text-slate-400">{result.detail}</p>}
            {result.threat_type && (
              <span className="inline-block mt-1 px-2 py-0.5 rounded text-[10px] font-medium bg-red-500/20 text-red-700 dark:text-red-300">
                {result.threat_type}
              </span>
            )}
          </div>

          {/* Hallucination meter */}
          {result.hallucination && (
            <div className="p-3 rounded-lg bg-slate-100 dark:bg-slate-900/60 border border-slate-200 dark:border-slate-700">
              <h4 className="text-xs font-medium text-slate-600 dark:text-slate-400 mb-3">Hallucination Analysis</h4>
              <div className="grid grid-cols-2 gap-3">
                {[
                  { label: "Overall Risk", value: result.hallucination.risk_score, color: "red" },
                  { label: "Pattern Score", value: result.hallucination.pattern_score, color: "amber" },
                  { label: "Grounding", value: result.hallucination.grounding_score, color: "blue" },
                  { label: "Contradiction", value: result.hallucination.contradiction_score, color: "purple" },
                ].map(({ label, value, color }) => (
                  <div key={label}>
                    <div className="flex items-center justify-between mb-1">
                      <span className="text-[10px] text-slate-600 dark:text-slate-400">{label}</span>
                      <span className={`text-[10px] font-bold ${hallucinationMetricStyle[color].text}`}>
                        {((value || 0) * 100).toFixed(0)}%
                      </span>
                    </div>
                    <div className="h-2 bg-slate-200 dark:bg-slate-900 rounded-full overflow-hidden">
                      <div
                        className={`h-full rounded-full ${hallucinationMetricStyle[color].bar} transition-all`}
                        style={{ width: `${(value || 0) * 100}%` }}
                      />
                    </div>
                  </div>
                ))}
              </div>
              {result.hallucination.matched_markers?.length > 0 && (
                <div className="mt-2 flex flex-wrap gap-1">
                  {result.hallucination.matched_markers.map((m, i) => (
                    <span key={i} className="px-1.5 py-0.5 rounded text-[10px] bg-amber-500/20 text-amber-400 border border-amber-500/30">
                      {m}
                    </span>
                  ))}
                </div>
              )}
            </div>
          )}

          {/* Raw vs Safe text comparison */}
          {result.safe_text && result.raw_text !== result.safe_text && (
            <div className="grid grid-cols-2 gap-3">
              <div>
                <h4 className="text-xs font-medium text-slate-600 dark:text-slate-400 mb-1">Raw Output</h4>
                <div className="p-2 rounded-lg bg-slate-100 dark:bg-slate-950 border border-slate-300 dark:border-slate-800 text-[11px] text-slate-700 dark:text-slate-300 max-h-48 overflow-auto whitespace-pre-wrap">
                  {result.raw_text}
                </div>
              </div>
              <div>
                <h4 className="text-xs font-medium text-emerald-700 dark:text-emerald-300 mb-1 flex items-center gap-1">
                  <Lock className="w-3 h-3" /> Sanitized Output
                </h4>
                <div className="p-2 rounded-lg bg-slate-100 dark:bg-slate-950 border border-emerald-500/30 text-[11px] text-slate-700 dark:text-slate-300 max-h-48 overflow-auto whitespace-pre-wrap">
                  {result.safe_text}
                </div>
              </div>
            </div>
          )}

          {/* Redacted tokens */}
          {result.redacted_tokens?.length > 0 && (
            <div>
              <h4 className="text-xs font-medium text-amber-700 dark:text-amber-300 mb-2">Redacted Tokens ({result.redacted_tokens.length})</h4>
              <div className="space-y-1">
                {result.redacted_tokens.map((t, i) => (
                  <div key={i} className="flex items-center gap-2 p-1.5 bg-amber-500/10 rounded text-[11px]">
                    <span className="text-slate-600 dark:text-slate-400 line-through font-mono">{t.original}</span>
                    <span className="text-slate-500 dark:text-slate-500">→</span>
                    <span className="text-amber-700 dark:text-amber-300 font-mono">{t.replacement}</span>
                    <span className="text-slate-500 dark:text-slate-500 text-[9px] ml-auto">pos {t.start}–{t.end}</span>
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* Matched patterns & compliance tags */}
          {result.matched_patterns?.length > 0 && (
            <div className="flex flex-wrap gap-1">
              {result.matched_patterns.map((p, i) => (
                <span key={i} className="px-1.5 py-0.5 rounded text-[10px] font-mono bg-red-500/20 text-red-700 dark:text-red-300 border border-red-500/30">
                  {p}
                </span>
              ))}
            </div>
          )}
          {result.compliance_tags?.length > 0 && (
            <div className="flex flex-wrap gap-1">
              {result.compliance_tags.map((t, i) => (
                <span key={i} className="px-1.5 py-0.5 rounded text-[10px] bg-blue-500/20 text-blue-700 dark:text-blue-300 border border-blue-500/30">
                  {t}
                </span>
              ))}
            </div>
          )}

          {/* Escalation flag */}
          {result.escalation_flag && (
            <div className="p-2 rounded-lg bg-red-500/10 border border-red-500/30 text-xs text-red-700 dark:text-red-300 flex items-center gap-2">
              <AlertTriangle className="w-4 h-4" />
              <span className="font-semibold">Escalation Required</span> — High-confidence threat detected, requires human review
            </div>
          )}
        </div>
      )}
    </SimulatorShell>
  );
}
