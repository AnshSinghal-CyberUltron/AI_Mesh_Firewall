import { useState } from "react";
import {
  ArrowLeft, Eye, Clock, Activity, Download, Share2, Copy,
  Shield, Server, CheckCircle, TrendingUp, ChevronDown,
} from "lucide-react";
import {
  AreaChart, Area, XAxis, YAxis, CartesianGrid, Tooltip,
  ResponsiveContainer,
} from "recharts";
import { SafeResponsiveChart } from "./SafeResponsiveChart";
import { copyToClipboard } from "../lib/clipboard";
import { getModuleLogCharts } from "./module-specific-log-charts";
import { useTheme } from "../context/ThemeContext";

export function LogDetailPage({ logData, onBack }) {
  const [expandedSections, setExpandedSections] = useState({
    request: true, response: true, security: false, metadata: false,
  });
  const [copiedField, setCopiedField] = useState(null);
  const { resolvedTheme } = (typeof useTheme === "function" ? useTheme() : {}) || {};
  const isDark = resolvedTheme === "dark";

  const moduleLogCharts = getModuleLogCharts(logData || {});
  const meta = logData?.metadata || logData?.raw?.metadata || logData?.event_metadata || {};

  const timestamp = logData?.timestamp || new Date().toISOString();
  const duration = meta?.latency_ms ? `${meta.latency_ms}ms` : (logData?.duration || formatDuration(meta?.latency_ms));
  const status = logData?.status || logData?.action || "allowed";
  const action = logData?.action || "ALLOWED";
  const scanId = logData?.id || "n/a";

  const handleCopy = async (text, field) => {
    await copyToClipboard(text);
    setCopiedField(field);
    setTimeout(() => setCopiedField(null), 2000);
  };

  const toggleSection = (section) => {
    setExpandedSections((prev) => ({ ...prev, [section]: !prev[section] }));
  };

  const timelineData = [{ time: "Event", latency: parseNumeric(duration) }];
  const securityScore = parseNumeric(logData?.severity || meta?.security_risk_score) || 0;
  const threatLevel = mapThreatLevel(logData?.severity || meta?.security_risk_score);
  const logChartTheme = isDark
    ? {
        grid: "#334155",
        axis: "#94a3b8",
        tooltipBg: "rgba(15, 23, 42, 0.96)",
        tooltipBorder: "#475569",
        tooltipText: "#e5e7eb",
        hover: "rgba(51, 65, 85, 0.35)",
      }
    : {
        grid: "#cbd5e1",
        axis: "#64748b",
        tooltipBg: "#f8fafc",
        tooltipBorder: "#cbd5e1",
        tooltipText: "#0f172a",
        hover: "rgba(226, 232, 240, 0.45)",
      };

  const handleExport = () => {
    const payload = JSON.stringify(logData || {}, null, 2);
    const blob = new Blob([payload], { type: "application/json;charset=utf-8;" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `module-log-${scanId}.json`;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
  };

  const handleShare = async () => {
    const url = window.location.href;
    const message = `ZeroShield Scan #${scanId} | Status: ${status} | Threat: ${meta?.threat_type || "none"} | Model: ${meta?.model || "n/a"} | ${url}`;
    if (navigator.share) {
      try {
        await navigator.share({ title: "ZeroShield Security Log", text: message, url });
        return;
      } catch {
        // Fall back to clipboard for unsupported/denied share flow.
      }
    }
    await copyToClipboard(message);
    setCopiedField("Shared");
    setTimeout(() => setCopiedField(null), 2000);
  };

  return (
    <div className="space-y-6">
      {/* Header with Breadcrumb */}
      <div className="bg-white dark:bg-slate-800 rounded-xl border-2 border-slate-200 dark:border-slate-700 shadow-sm p-6">
        <button
          onClick={onBack}
          className="flex items-center gap-2 text-teal-600 hover:text-teal-700 mb-4 transition-colors"
        >
          <ArrowLeft className="w-4 h-4" />
          <span className="text-sm font-medium">Back to Activity Preview</span>
        </button>
        <div className="flex items-start justify-between">
          <div className="flex-1">
            <div className="flex items-center gap-3 mb-2">
              <h1 className="text-2xl font-bold text-slate-900 dark:text-slate-100">Scan Detail Report</h1>
              <StatusBadge status={status} action={action} />
            </div>
            <div className="flex items-center gap-4 text-sm text-slate-600 dark:text-slate-400">
              <div className="flex items-center gap-2"><Eye className="w-4 h-4" /><span>Scan ID: {scanId}</span></div>
              <div className="w-1 h-1 bg-slate-400 rounded-full"></div>
              <div className="flex items-center gap-2"><Clock className="w-4 h-4" /><span>{new Date(timestamp).toLocaleString()}</span></div>
              <div className="w-1 h-1 bg-slate-400 rounded-full"></div>
              <div className="flex items-center gap-2"><Activity className="w-4 h-4" /><span>Duration: {duration}</span></div>
            </div>
          </div>
          <div className="flex items-center gap-2">
            <button
              onClick={() => handleCopy(JSON.stringify(logData, null, 2), "Request Payload")}
              className="flex items-center gap-2 px-4 py-2 bg-slate-100 dark:bg-slate-700 text-slate-700 dark:text-slate-300 rounded-lg hover:bg-slate-200 dark:hover:bg-slate-600 transition-colors text-sm font-medium"
            >
              <Copy className="w-4 h-4" />
              {copiedField === "Request Payload" ? "Copied!" : "Copy"}
            </button>
            <button onClick={handleShare} className="flex items-center gap-2 px-4 py-2 bg-slate-100 dark:bg-slate-700 text-slate-700 dark:text-slate-300 rounded-lg hover:bg-slate-200 dark:hover:bg-slate-600 transition-colors text-sm font-medium">
              <Share2 className="w-4 h-4" />
              <span>{copiedField === "Shared" ? "Shared!" : "Share"}</span>
            </button>
            <button onClick={handleExport} className="flex items-center gap-2 px-4 py-2 bg-teal-600 text-white rounded-lg hover:bg-teal-700 transition-colors text-sm font-medium">
              <Download className="w-4 h-4" />Export
            </button>
          </div>
        </div>
      </div>

      {/* Key Metrics */}
      <div className="grid grid-cols-1 md:grid-cols-4 gap-4">
        <MetricCard icon={CheckCircle} label="Overall Status" value={status.toUpperCase()} color="emerald" />
        <MetricCard icon={Clock} label="Total Duration" value={duration} color="blue" />
        <MetricCard icon={Shield} label="Security Score" value={`${securityScore}/100`} color="purple" />
        <MetricCard icon={Activity} label="Threat Level" value={threatLevel} color="teal" />
      </div>

      <div className="bg-white dark:bg-slate-800 rounded-xl border-2 border-slate-200 dark:border-slate-700 shadow-sm p-6">
        <h3 className="text-base font-semibold text-slate-900 dark:text-slate-100 mb-4">Request Latency</h3>
        <SafeResponsiveChart className="h-[220px] w-full">
          <AreaChart data={timelineData}>
            <defs>
              <linearGradient id="colorLatency" x1="0" y1="0" x2="0" y2="1">
                <stop offset="5%" stopColor="#3b82f6" stopOpacity={0.8} />
                <stop offset="95%" stopColor="#3b82f6" stopOpacity={0} />
              </linearGradient>
            </defs>
            <CartesianGrid strokeDasharray="3 3" stroke={logChartTheme.grid} strokeOpacity={0.25} />
            <XAxis dataKey="time" stroke={logChartTheme.axis} tick={{ fontSize: 11, fill: logChartTheme.axis }} />
            <YAxis stroke={logChartTheme.axis} tick={{ fontSize: 11, fill: logChartTheme.axis }} />
            <Tooltip
              cursor={{ fill: logChartTheme.hover }}
              contentStyle={{
                backgroundColor: logChartTheme.tooltipBg,
                border: `1px solid ${logChartTheme.tooltipBorder}`,
                borderRadius: "8px",
                color: logChartTheme.tooltipText,
              }}
            />
            <Area
              type="monotone"
              dataKey="latency"
              stroke="#3b82f6"
              strokeWidth={2}
              fillOpacity={1}
              fill="url(#colorLatency)"
              activeDot={{ r: 4, fill: "#3b82f6", stroke: isDark ? "#0f172a" : "#ffffff", strokeWidth: 2 }}
            />
          </AreaChart>
        </SafeResponsiveChart>
      </div>

      {/* Module-Specific Log Detail Charts */}
      {moduleLogCharts && moduleLogCharts.charts && moduleLogCharts.charts.length > 0 && (
        <>
          <div className="flex items-center justify-between">
            <h2 className="text-xl font-bold text-slate-900 dark:text-slate-100">Module-Specific Scan Analysis</h2>
            <span className="text-sm text-slate-500 dark:text-slate-400">Detailed insights for this specific scan</span>
          </div>
          <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
            {moduleLogCharts.charts.map((chart, index) => (
              <div key={index} className="bg-white dark:bg-slate-800 rounded-xl border-2 border-slate-200 dark:border-slate-700 shadow-sm p-6">
                <h3 className="text-base font-semibold text-slate-900 dark:text-slate-100 mb-4">{chart.title}</h3>
                {chart.component}
              </div>
            ))}
          </div>
        </>
      )}

      {/* Detailed Scan Data - Collapsible Sections */}
      <div className="space-y-4">
        <CollapsibleSection
          title="Request Details" icon={Server}
          isExpanded={expandedSections.request}
          onToggle={() => toggleSection("request")}
        >
          <table className="w-full text-sm">
            <tbody className="divide-y divide-slate-200 dark:divide-slate-700">
              <DataRow label="Request ID" value={logData?.id || "n/a"} />
              <DataRow label="Timestamp" value={new Date(timestamp).toLocaleString()} />
              <DataRow label="Method" value={logData?.method || meta?.method || "POST"} />
              <DataRow label="Endpoint" value={logData?.endpoint || meta?.endpoint || "/v1/chat/completions"} />
              <DataRow label="Source IP" value={meta?.source_ip || logData?.ip || logData?.source_ip || "--"} />
              <DataRow label="User Agent" value={meta?.user_agent || logData?.userAgent || logData?.user_agent || "--"} />
              <DataRow label="Model" value={logData?.model || meta?.model || "--"} />
              <DataRow label="Status Code" value={logData?.statusCode || meta?.status_code || "--"} />
            </tbody>
          </table>
        </CollapsibleSection>

        <CollapsibleSection
          title="Response Details" icon={Activity}
          isExpanded={expandedSections.response}
          onToggle={() => toggleSection("response")}
        >
          <table className="w-full text-sm">
            <tbody className="divide-y divide-slate-200 dark:divide-slate-700">
              <DataRow label="Status Code" value={logData?.statusCode || meta?.status_code || "--"} />
              <DataRow label="Response Time" value={duration} />
              <DataRow label="Action" value={action} />
              <DataRow label="Input Tokens" value={meta?.input_tokens ?? meta?.extra?.input_tokens ?? logData?.tokensPrompt ?? "--"} />
              <DataRow label="Output Tokens" value={meta?.output_tokens ?? meta?.extra?.output_tokens ?? logData?.tokensCompletion ?? "--"} />
              <DataRow label="Total Tokens" value={meta?.total_tokens ?? meta?.extra?.total_tokens ?? logData?.tokensUsed ?? "--"} />
              <DataRow label="Cost (USD)" value={
                meta?.extra?.cost != null ? `$${Number(meta.extra.cost).toFixed(6)}` : (logData?.cost != null ? `$${Number(logData.cost).toFixed(6)}` : "--")
              } />
              <DataRow label="Cache Hit" value={
                meta?.extra?.cache_hit != null ? (meta.extra.cache_hit ? "Yes" : "No") : (logData?.cacheHit != null ? (logData.cacheHit ? "Yes" : "No") : "--")
              } />
            </tbody>
          </table>
        </CollapsibleSection>

        <CollapsibleSection
          title="Security Analysis" icon={Shield}
          isExpanded={expandedSections.security}
          onToggle={() => toggleSection("security")}
        >
          <table className="w-full text-sm">
            <tbody className="divide-y divide-slate-200 dark:divide-slate-700">
              <DataRow label="Threat Level" value={threatLevel} />
              <DataRow label="Input Validation" value={meta?.input_validation || "--"} />
              <DataRow label="Content Safety" value={meta?.content_safety || "--"} />
              <DataRow label="PII Detection" value={String(meta?.pii_detected ?? "--")} />
              <DataRow label="Prompt Injection" value={String(meta?.prompt_injection_detected ?? "--")} />
              <DataRow label="Jailbreak Attempt" value={String(meta?.jailbreak_detected ?? "--")} />
              <DataRow label="Rate Limit Status" value={meta?.rate_limit_status || "--"} />
              <DataRow label="Auth Status" value={meta?.auth_status || "--"} />
              <DataRow label="Policy Violations" value={
                Array.isArray(meta?.policy_violations) && meta.policy_violations.length > 0
                  ? meta.policy_violations.join(", ")
                  : (meta?.policy_violations != null && meta.policy_violations !== "" ? String(meta.policy_violations) : "--")
              } />
              <DataRow label="Pipeline Stage" value={meta?.pipeline_stage || "--"} />
              <DataRow label="Intent" value={meta?.intent || "--"} />
              <DataRow label="OWASP Code" value={meta?.owasp_code || logData?.subcategory || "--"} />
              <DataRow label="Compliance Tags" value={
                Array.isArray(meta?.compliance_tags) && meta.compliance_tags.length > 0
                  ? meta.compliance_tags.join(", ")
                  : "--"
              } />
              <DataRow label="Matched Patterns" value={
                Array.isArray(meta?.extra?.matched_patterns) && meta.extra.matched_patterns.length > 0
                  ? meta.extra.matched_patterns.join(", ")
                  : "--"
              } />
            </tbody>
          </table>
        </CollapsibleSection>

        <CollapsibleSection
          title="Metadata & Context" icon={TrendingUp}
          isExpanded={expandedSections.metadata}
          onToggle={() => toggleSection("metadata")}
        >
          <table className="w-full text-sm">
            <tbody className="divide-y divide-slate-200 dark:divide-slate-700">
              <DataRow label="Project ID" value={meta?.project_id || "--"} />
              <DataRow label="Organization" value={logData?.organization_name || meta?.organization_name || logData?.endpoint_name || logData?.raw?.endpoint_name || meta?.organization_id || "--"} />
              <DataRow label="Endpoint" value={meta?.endpoint || logData?.endpoint_id || logData?.raw?.endpoint_id || "--"} />
              <DataRow label="Event Type" value={meta?.event_type || "--"} />
              <DataRow label="Pipeline Stage" value={meta?.pipeline_stage || "--"} />
              <DataRow label="Pipeline Request ID" value={meta?.pipeline_request_id || "--"} />
              <DataRow label="RAG Collection" value={meta?.extra?.collection || meta?.extra?.rag_collection || meta?.rag_collection || "--"} />
              <DataRow label="Escalation Level" value={
                meta?.extra?.escalation_level != null && meta.extra.escalation_level !== 0
                  ? `Level ${meta.extra.escalation_level}`
                  : "--"
              } />
              <DataRow label="Key Prefix" value={meta?.key_prefix || "--"} />
              <DataRow label="Prompt Hash" value={meta?.prompt_hash || "--"} />
              <DataRow label="Source" value={logData?.source || meta?.source || "--"} />
              <DataRow label="Intent" value={meta?.intent || "--"} />
            </tbody>
          </table>
        </CollapsibleSection>
      </div>
    </div>
  );
}

function parseNumeric(value) {
  if (typeof value === "number") return value;
  if (typeof value !== "string") return 0;
  const parsed = Number.parseFloat(value.replace(/[^0-9.]/g, ""));
  return Number.isFinite(parsed) ? parsed : 0;
}

function formatDuration(ms) {
  if (ms == null) return "--";
  const parsed = parseNumeric(String(ms));
  return Number.isFinite(parsed) ? `${parsed}ms` : "--";
}

function mapThreatLevel(value) {
  const score = parseNumeric(String(value ?? ""));
  if (score >= 80) return "HIGH";
  if (score >= 60) return "MEDIUM";
  if (score > 0) return "LOW";
  return "NONE";
}

function StatusBadge({ status, action }) {
  const statusConfig = {
    allowed: { bg: "bg-emerald-100 dark:bg-emerald-800/30", text: "text-emerald-700", dot: "bg-emerald-500" },
    blocked: { bg: "bg-red-100 dark:bg-red-800/30", text: "text-red-700", dot: "bg-red-500" },
    flagged: { bg: "bg-amber-100 dark:bg-amber-800/30", text: "text-amber-700", dot: "bg-amber-500" },
    redacted: { bg: "bg-blue-100 dark:bg-blue-800/30", text: "text-blue-700", dot: "bg-blue-500" },
  };
  const config = statusConfig[status?.toLowerCase()] || statusConfig.allowed;
  return (
    <span className={`inline-flex items-center gap-1.5 px-3 py-1 ${config.bg} ${config.text} rounded-full text-xs font-semibold`}>
      <div className={`w-1.5 h-1.5 rounded-full ${config.dot}`}></div>
      {action || status?.toUpperCase()}
    </span>
  );
}

function MetricCard({ icon: Icon, label, value, color }) {
  const colorMap = { blue: "#3b82f6", teal: "#14b8a6", purple: "#8b5cf6", emerald: "#10b981", amber: "#f59e0b" };
  const bgMap = { blue: "bg-blue-50 dark:bg-blue-900/20", teal: "bg-teal-50 dark:bg-teal-900/20", purple: "bg-purple-50 dark:bg-purple-900/20", emerald: "bg-emerald-50 dark:bg-emerald-900/20", amber: "bg-amber-50 dark:bg-amber-900/20" };
  return (
    <div className="bg-white dark:bg-slate-800 rounded-xl border-2 border-slate-200 dark:border-slate-700 shadow-sm p-4">
      <div className={`inline-flex items-center justify-center w-10 h-10 ${bgMap[color] || bgMap.teal} rounded-lg mb-3`}>
        <Icon className="w-5 h-5" style={{ color: colorMap[color] }} />
      </div>
      <div className="text-2xl font-bold text-slate-900 dark:text-slate-100 mb-1">{value}</div>
      <div className="text-xs font-medium text-slate-600 dark:text-slate-400">{label}</div>
    </div>
  );
}

function CollapsibleSection({ title, icon: Icon, isExpanded, onToggle, children }) {
  return (
    <div className="bg-white dark:bg-slate-800 rounded-xl border-2 border-slate-200 dark:border-slate-700 shadow-sm overflow-hidden">
      <button
        onClick={onToggle}
        className="w-full flex items-center justify-between p-6 hover:bg-slate-50 dark:hover:bg-slate-700 transition-colors"
      >
        <div className="flex items-center gap-3">
          <Icon className="w-5 h-5 text-teal-600" />
          <span className="text-base font-semibold text-slate-900 dark:text-slate-100">{title}</span>
        </div>
        <ChevronDown className={`w-5 h-5 text-slate-500 dark:text-slate-400 transition-transform ${isExpanded ? "rotate-180" : ""}`} />
      </button>
      {isExpanded && (
        <div className="px-6 pb-6 border-t border-slate-200 dark:border-slate-700 pt-4">
          <div className="overflow-x-auto">{children}</div>
        </div>
      )}
    </div>
  );
}

function DataRow({ label, value }) {
  return (
    <tr>
      <td className="py-2.5 pr-4 text-xs font-medium text-slate-600 dark:text-slate-400 w-1/3">{label}</td>
      <td className="py-2.5 text-sm text-slate-900 dark:text-slate-100 font-mono">{value}</td>
    </tr>
  );
}
