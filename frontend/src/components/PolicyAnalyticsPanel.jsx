import { useState, useEffect, useCallback } from "react";
import {
  Loader2, RefreshCw, TrendingUp, AlertTriangle, Shield, Users, BookOpen,
  ChevronDown, ChevronRight,
} from "lucide-react";
import {
  AreaChart, Area, BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip,
  ResponsiveContainer, Legend,
} from "recharts";
import { useAuth } from "../context/AuthContext";

const DAYS_OPTIONS = [
  { value: 7, label: "7d" },
  { value: 14, label: "14d" },
  { value: 30, label: "30d" },
  { value: 60, label: "60d" },
  { value: 90, label: "90d" },
];

const VIOLATION_COLORS = {
  promptInjection: "#ef4444",
  jailbreak: "#f59e0b",
  piiDetection: "#8b5cf6",
  toolOverreach: "#3b82f6",
  sourceCode: "#6b7280",
};

const VIOLATION_LABELS = {
  promptInjection: "Prompt Injection",
  jailbreak: "Jailbreak",
  piiDetection: "PII Detection",
  toolOverreach: "Tool Overreach",
  sourceCode: "Source Code",
};

const GRANULARITY_OPTIONS = [
  { value: "hour", label: "Hourly" },
  { value: "day", label: "Daily" },
  { value: "week", label: "Weekly" },
];

function StatusBadge({ status }) {
  const cfg = {
    EXCELLENT: { bg: "bg-emerald-100 dark:bg-emerald-800/30", text: "text-emerald-700" },
    GOOD: { bg: "bg-blue-100 dark:bg-blue-800/30", text: "text-blue-700" },
    "NEEDS REVIEW": { bg: "bg-amber-100 dark:bg-amber-800/30", text: "text-amber-700" },
    LOW: { bg: "bg-red-100 dark:bg-red-800/30", text: "text-red-700" },
  };
  const c = cfg[status] || cfg.LOW;
  return (
    <span className={`px-1.5 py-0.5 rounded-full text-[10px] font-semibold uppercase ${c.bg} ${c.text}`}>
      {status}
    </span>
  );
}

function MetricCard({ icon: Icon, label, value, subtext, color }) {
  return (
    <div className="bg-slate-50 dark:bg-slate-800/50 rounded-lg p-4">
      <div className="flex items-center gap-2 mb-2">
        <Icon className={`w-4 h-4 ${color || "text-teal-600"}`} />
        <span className="text-[10px] text-slate-500 dark:text-slate-400 uppercase font-medium">{label}</span>
      </div>
      <div className="text-xl font-bold text-slate-900 dark:text-slate-100">{value}</div>
      {subtext && <div className="text-[10px] text-slate-400 mt-0.5">{subtext}</div>}
    </div>
  );
}

export function PolicyAnalyticsPanel() {
  const { fetchWithAuth } = useAuth();
  const [analytics, setAnalytics] = useState(null);
  const [topViolators, setTopViolators] = useState([]);
  const [topRules, setTopRules] = useState([]);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState(null);
  const [days, setDays] = useState(14);
  const [granularity, setGranularity] = useState("day");
  const [expandedSections, setExpandedSections] = useState({
    effectiveness: true,
    violations: true,
    categories: false,
    violators: false,
    rules: false,
  });

  const toggleSection = (key) => {
    setExpandedSections((prev) => ({ ...prev, [key]: !prev[key] }));
  };

  const fetchAll = useCallback(async () => {
    setLoading(true);
    setLoadError(null);
    try {
      const results = await Promise.allSettled([
        fetchWithAuth(`/api/policies/analytics/?days=${days}`),
        fetchWithAuth(`/api/policies/top-violators/?days=${days}&limit=10`),
        fetchWithAuth("/api/policy/top-rules/?limit=10"),
      ]);

      const errors = [];

      if (results[0].status === "fulfilled" && results[0].value.ok) {
        setAnalytics(await results[0].value.json());
      } else {
        setAnalytics(null);
        if (results[0].status === "fulfilled") {
          errors.push(`analytics (${results[0].value.status})`);
        } else {
          errors.push("analytics (network error)");
        }
      }

      if (results[1].status === "fulfilled" && results[1].value.ok) {
        const data = await results[1].value.json();
        setTopViolators(Array.isArray(data) ? data : []);
      } else {
        setTopViolators([]);
        if (results[1].status === "fulfilled") {
          errors.push(`top violators (${results[1].value.status})`);
        } else {
          errors.push("top violators (network error)");
        }
      }

      if (results[2].status === "fulfilled" && results[2].value.ok) {
        const data = await results[2].value.json();
        setTopRules(Array.isArray(data) ? data : []);
      } else {
        setTopRules([]);
        if (results[2].status === "fulfilled") {
          errors.push(`top rules (${results[2].value.status})`);
        } else {
          errors.push("top rules (network error)");
        }
      }

      if (errors.length > 0) {
        setLoadError(`Some analytics sources failed: ${errors.join(", ")}`);
      }
    } finally {
      setLoading(false);
    }
  }, [fetchWithAuth, days]);

  useEffect(() => {
    fetchAll();
  }, [fetchAll]);

  const violationData = analytics
    ? granularity === "hour"
      ? analytics.violations_by_hour || []
      : granularity === "week"
        ? analytics.violations_by_week || []
        : analytics.violations_by_day || []
    : [];

  const violationXKey = granularity === "hour" ? "hour" : granularity === "week" ? "week" : "date";

  return (
    <div className="bg-white dark:bg-slate-800 border border-slate-200 dark:border-slate-700 rounded-xl shadow-sm p-6">
      <div className="flex items-center justify-between mb-4">
        <div>
          <h3 className="text-base font-semibold text-slate-900 dark:text-slate-100">Policy Analytics</h3>
          <p className="text-xs text-slate-500 dark:text-slate-400 mt-0.5">
            Enforcement effectiveness, violation trends, top violators, and triggered rules
          </p>
        </div>
        <div className="flex items-center gap-2">
          <select
            value={days}
            onChange={(e) => setDays(Number(e.target.value))}
            className="text-slate-900 dark:text-slate-100 bg-white dark:bg-slate-800 px-2 py-1.5 border border-slate-200 dark:border-slate-700 rounded-lg text-xs focus:ring-2 focus:ring-teal-500"
          >
            {DAYS_OPTIONS.map((o) => (
              <option key={o.value} value={o.value}>{o.label}</option>
            ))}
          </select>
          <button
            onClick={fetchAll}
            disabled={loading}
            className="p-2 hover:bg-slate-100 dark:hover:bg-slate-700 rounded-lg transition-colors disabled:opacity-50"
            title="Refresh"
          >
            {loading ? <Loader2 className="w-4 h-4 text-teal-500 animate-spin" /> : <RefreshCw className="w-4 h-4 text-slate-500 dark:text-slate-400" />}
          </button>
        </div>
      </div>

      {loadError ? (
        <div className="mb-4 p-3 rounded-lg text-xs flex items-start gap-2 bg-red-50 dark:bg-red-900/20 border border-red-200 dark:border-red-800 text-red-700">
          <AlertTriangle className="w-4 h-4 flex-shrink-0 mt-0.5" />
          <span>{loadError}</span>
        </div>
      ) : null}

      {loading && !analytics ? (
        <div className="flex items-center justify-center py-8">
          <Loader2 className="w-5 h-5 text-teal-500 animate-spin" />
          <span className="ml-2 text-sm text-slate-500 dark:text-slate-400">Loading analytics...</span>
        </div>
      ) : (
        <div className="space-y-4">
          {/* Metric Cards */}
          <div className="grid grid-cols-4 gap-3">
            <MetricCard
              icon={AlertTriangle}
              label="Total Violations"
              value={analytics ? (analytics.total_violations || 0).toLocaleString() : "--"}
              subtext={`${days}-day window`}
              color="text-red-500"
            />
            <MetricCard
              icon={Shield}
              label="Blocked"
              value={analytics ? (analytics.total_blocked || 0).toLocaleString() : "--"}
              color="text-red-600"
            />
            <MetricCard
              icon={TrendingUp}
              label="Avg Effectiveness"
              value={analytics ? `${analytics.avg_effectiveness || 0}%` : "--"}
              color="text-teal-600"
            />
            <MetricCard
              icon={BookOpen}
              label="Redacted"
              value={analytics ? (analytics.total_redacted || 0).toLocaleString() : "--"}
              color="text-amber-500"
            />
          </div>

          {/* Effectiveness Trend */}
          <div className="border border-slate-200 dark:border-slate-700 rounded-lg overflow-hidden">
            <button
              onClick={() => toggleSection("effectiveness")}
              className="w-full flex items-center justify-between px-4 py-3 bg-slate-50 dark:bg-slate-800/50 hover:bg-slate-100 dark:hover:bg-slate-700 transition-colors"
            >
              <div className="flex items-center gap-2">
                <TrendingUp className="w-4 h-4 text-teal-600" />
                <span className="text-sm font-semibold text-slate-700 dark:text-slate-300">Effectiveness Trend</span>
              </div>
              {expandedSections.effectiveness ? <ChevronDown className="w-4 h-4 text-slate-400" /> : <ChevronRight className="w-4 h-4 text-slate-400" />}
            </button>
            {expandedSections.effectiveness && analytics?.effectiveness_trend && (
              <div className="p-4">
                <ResponsiveContainer width="100%" height={200}>
                  <AreaChart data={analytics.effectiveness_trend} margin={{ top: 5, right: 10, bottom: 5, left: 0 }}>
                    <defs>
                      <linearGradient id="gradEff" x1="0" y1="0" x2="0" y2="1">
                        <stop offset="5%" stopColor="#14b8a6" stopOpacity={0.3} />
                        <stop offset="95%" stopColor="#14b8a6" stopOpacity={0} />
                      </linearGradient>
                    </defs>
                    <CartesianGrid strokeDasharray="3 3" stroke="#f1f5f9" />
                    <XAxis dataKey="date" stroke="#94a3b8" tick={{ fontSize: 10 }} />
                    <YAxis domain={[0, 100]} stroke="#94a3b8" tick={{ fontSize: 10 }} unit="%" />
                    <Tooltip contentStyle={{ fontSize: 11, border: "1px solid #e2e8f0", borderRadius: "8px" }} formatter={(v) => `${v}%`} />
                    <Area type="monotone" dataKey="effectiveness" stroke="#14b8a6" strokeWidth={2} fill="url(#gradEff)" name="Effectiveness" />
                  </AreaChart>
                </ResponsiveContainer>
              </div>
            )}
          </div>

          {/* Violations Breakdown */}
          <div className="border border-slate-200 dark:border-slate-700 rounded-lg overflow-hidden">
            <button
              onClick={() => toggleSection("violations")}
              className="w-full flex items-center justify-between px-4 py-3 bg-slate-50 dark:bg-slate-800/50 hover:bg-slate-100 dark:hover:bg-slate-700 transition-colors"
            >
              <div className="flex items-center gap-2">
                <AlertTriangle className="w-4 h-4 text-red-500" />
                <span className="text-sm font-semibold text-slate-700 dark:text-slate-300">Violation Breakdown</span>
              </div>
              <div className="flex items-center gap-2">
                {expandedSections.violations && (
                  <select
                    value={granularity}
                    onChange={(e) => { e.stopPropagation(); setGranularity(e.target.value); }}
                    onClick={(e) => e.stopPropagation()}
                    className="text-slate-900 dark:text-slate-100 bg-white dark:bg-slate-800 px-2 py-1 border border-slate-200 dark:border-slate-700 rounded text-[10px] focus:ring-1 focus:ring-teal-500"
                  >
                    {GRANULARITY_OPTIONS.map((o) => (
                      <option key={o.value} value={o.value}>{o.label}</option>
                    ))}
                  </select>
                )}
                {expandedSections.violations ? <ChevronDown className="w-4 h-4 text-slate-400" /> : <ChevronRight className="w-4 h-4 text-slate-400" />}
              </div>
            </button>
            {expandedSections.violations && violationData.length > 0 && (
              <div className="p-4">
                <ResponsiveContainer width="100%" height={220}>
                  <BarChart data={violationData} margin={{ top: 5, right: 10, bottom: 5, left: 0 }}>
                    <CartesianGrid strokeDasharray="3 3" stroke="#f1f5f9" />
                    <XAxis dataKey={violationXKey} stroke="#94a3b8" tick={{ fontSize: 9 }} angle={granularity === "hour" ? 0 : -30} textAnchor={granularity === "hour" ? "middle" : "end"} height={granularity === "hour" ? 30 : 50} />
                    <YAxis stroke="#94a3b8" tick={{ fontSize: 10 }} />
                    <Tooltip contentStyle={{ fontSize: 11, border: "1px solid #e2e8f0", borderRadius: "8px" }} />
                    <Legend wrapperStyle={{ fontSize: 10 }} />
                    {Object.entries(VIOLATION_COLORS).map(([key, color]) => (
                      <Bar key={key} dataKey={key} stackId="a" fill={color} name={VIOLATION_LABELS[key] || key} />
                    ))}
                  </BarChart>
                </ResponsiveContainer>
              </div>
            )}
          </div>

          {/* Category Performance */}
          <div className="border border-slate-200 dark:border-slate-700 rounded-lg overflow-hidden">
            <button
              onClick={() => toggleSection("categories")}
              className="w-full flex items-center justify-between px-4 py-3 bg-slate-50 dark:bg-slate-800/50 hover:bg-slate-100 dark:hover:bg-slate-700 transition-colors"
            >
              <div className="flex items-center gap-2">
                <Shield className="w-4 h-4 text-blue-600" />
                <span className="text-sm font-semibold text-slate-700 dark:text-slate-300">Category Performance</span>
              </div>
              {expandedSections.categories ? <ChevronDown className="w-4 h-4 text-slate-400" /> : <ChevronRight className="w-4 h-4 text-slate-400" />}
            </button>
            {expandedSections.categories && analytics?.category_performance && (
              <div className="p-4 overflow-x-auto">
                <table className="w-full text-xs">
                  <thead>
                    <tr className="bg-slate-50 dark:bg-slate-800/50 border-b border-slate-200 dark:border-slate-700">
                      <th className="px-3 py-2 text-left font-semibold text-slate-600 dark:text-slate-300 uppercase">Category</th>
                      <th className="px-3 py-2 text-right font-semibold text-slate-600 dark:text-slate-300 uppercase">Policies</th>
                      <th className="px-3 py-2 text-right font-semibold text-slate-600 dark:text-slate-300 uppercase">Violations</th>
                      <th className="px-3 py-2 text-right font-semibold text-slate-600 dark:text-slate-300 uppercase">Effectiveness</th>
                      <th className="px-3 py-2 text-right font-semibold text-slate-600 dark:text-slate-300 uppercase">Status</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-slate-100 dark:divide-slate-700">
                    {analytics.category_performance.map((cat, idx) => (
                      <tr key={idx} className="hover:bg-slate-50 dark:hover:bg-slate-700 transition-colors">
                        <td className="px-3 py-2 font-medium text-slate-800 dark:text-slate-200 capitalize">{cat.category}</td>
                        <td className="px-3 py-2 text-right text-slate-600 dark:text-slate-400">{cat.policies}</td>
                        <td className="px-3 py-2 text-right text-slate-700 dark:text-slate-300">{cat.totalViolations}</td>
                        <td className="px-3 py-2 text-right">
                          <div className="flex items-center justify-end gap-2">
                            <div className="w-16 h-1.5 bg-slate-200 rounded-full overflow-hidden">
                              <div
                                className="h-full rounded-full bg-teal-500"
                                style={{ width: `${Math.min(cat.effectiveness, 100)}%` }}
                              />
                            </div>
                            <span className="text-slate-700 dark:text-slate-300 font-medium w-10 text-right">{cat.effectiveness}%</span>
                          </div>
                        </td>
                        <td className="px-3 py-2 text-right"><StatusBadge status={cat.status} /></td>
                      </tr>
                    ))}
                  </tbody>
                </table>
                {analytics.category_performance.length === 0 && (
                  <div className="text-center py-4 text-xs text-slate-400">No category data available</div>
                )}
              </div>
            )}
          </div>

          {/* Top Violators */}
          <div className="border border-slate-200 dark:border-slate-700 rounded-lg overflow-hidden">
            <button
              onClick={() => toggleSection("violators")}
              className="w-full flex items-center justify-between px-4 py-3 bg-slate-50 dark:bg-slate-800/50 hover:bg-slate-100 dark:hover:bg-slate-700 transition-colors"
            >
              <div className="flex items-center gap-2">
                <Users className="w-4 h-4 text-amber-600" />
                <span className="text-sm font-semibold text-slate-700 dark:text-slate-300">Top Violators</span>
                {topViolators.length > 0 && (
                  <span className="text-[10px] text-slate-400">{topViolators.length} entries</span>
                )}
              </div>
              {expandedSections.violators ? <ChevronDown className="w-4 h-4 text-slate-400" /> : <ChevronRight className="w-4 h-4 text-slate-400" />}
            </button>
            {expandedSections.violators && (
              <div className="p-4 overflow-x-auto">
                <table className="w-full text-xs">
                  <thead>
                    <tr className="bg-slate-50 dark:bg-slate-800/50 border-b border-slate-200 dark:border-slate-700">
                      <th className="px-3 py-2 text-left font-semibold text-slate-600 dark:text-slate-300 uppercase">Endpoint / User</th>
                      <th className="px-3 py-2 text-right font-semibold text-slate-600 dark:text-slate-300 uppercase">Violations</th>
                      <th className="px-3 py-2 text-right font-semibold text-slate-600 dark:text-slate-300 uppercase">Blocked</th>
                      <th className="px-3 py-2 text-left font-semibold text-slate-600 dark:text-slate-300 uppercase">Policy Codes</th>
                      <th className="px-3 py-2 text-right font-semibold text-slate-600 dark:text-slate-300 uppercase">Risk Score</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-slate-100 dark:divide-slate-700">
                    {topViolators.map((v, idx) => (
                      <tr key={idx} className="hover:bg-slate-50 dark:hover:bg-slate-700 transition-colors">
                        <td className="px-3 py-2">
                          <div className="font-medium text-slate-800 dark:text-slate-200">{v.endpoint_name}</div>
                          <div className="text-[10px] text-slate-400 font-mono">{v.endpoint_identifier}</div>
                        </td>
                        <td className="px-3 py-2 text-right font-semibold text-slate-700 dark:text-slate-300">{v.total_violations}</td>
                        <td className="px-3 py-2 text-right font-semibold text-red-600">{v.blocked}</td>
                        <td className="px-3 py-2">
                          <div className="flex flex-wrap gap-0.5">
                            {(v.violated_policy_codes || []).slice(0, 3).map((code, i) => (
                              <span key={i} className="px-1 py-0.5 bg-slate-100 dark:bg-slate-700 text-slate-600 dark:text-slate-400 text-[10px] rounded font-mono">{code}</span>
                            ))}
                            {(v.violated_policy_codes || []).length > 3 && (
                              <span className="text-[10px] text-slate-400">+{v.violated_policy_codes.length - 3}</span>
                            )}
                          </div>
                        </td>
                        <td className="px-3 py-2 text-right">
                          <span className={`font-semibold ${v.risk_score >= 70 ? "text-red-600" : v.risk_score >= 40 ? "text-amber-600" : "text-slate-600 dark:text-slate-400"}`}>
                            {v.risk_score}
                          </span>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
                {topViolators.length === 0 && (
                  <div className="text-center py-4 text-xs text-slate-400">No violator data available</div>
                )}
              </div>
            )}
          </div>

          {/* Top Triggered Rules */}
          <div className="border border-slate-200 dark:border-slate-700 rounded-lg overflow-hidden">
            <button
              onClick={() => toggleSection("rules")}
              className="w-full flex items-center justify-between px-4 py-3 bg-slate-50 dark:bg-slate-800/50 hover:bg-slate-100 dark:hover:bg-slate-700 transition-colors"
            >
              <div className="flex items-center gap-2">
                <BookOpen className="w-4 h-4 text-purple-600" />
                <span className="text-sm font-semibold text-slate-700 dark:text-slate-300">Top Triggered Rules</span>
                {topRules.length > 0 && (
                  <span className="text-[10px] text-slate-400">{topRules.length} rules</span>
                )}
              </div>
              {expandedSections.rules ? <ChevronDown className="w-4 h-4 text-slate-400" /> : <ChevronRight className="w-4 h-4 text-slate-400" />}
            </button>
            {expandedSections.rules && (
              <div className="p-4 overflow-x-auto">
                <table className="w-full text-xs">
                  <thead>
                    <tr className="bg-slate-50 dark:bg-slate-800/50 border-b border-slate-200 dark:border-slate-700">
                      <th className="px-3 py-2 text-left font-semibold text-slate-600 dark:text-slate-300 uppercase">Rule</th>
                      <th className="px-3 py-2 text-left font-semibold text-slate-600 dark:text-slate-300 uppercase">Policy</th>
                      <th className="px-3 py-2 text-right font-semibold text-slate-600 dark:text-slate-300 uppercase">Triggered</th>
                      <th className="px-3 py-2 text-right font-semibold text-slate-600 dark:text-slate-300 uppercase">Blocked</th>
                      <th className="px-3 py-2 text-right font-semibold text-slate-600 dark:text-slate-300 uppercase">Effectiveness</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-slate-100 dark:divide-slate-700">
                    {topRules.map((r, idx) => (
                      <tr key={idx} className="hover:bg-slate-50 dark:hover:bg-slate-700 transition-colors">
                        <td className="px-3 py-2">
                          <div className="font-medium text-slate-800 dark:text-slate-200">{r.ruleName}</div>
                          <div className="text-[10px] text-slate-400 font-mono">{r.ruleId}</div>
                        </td>
                        <td className="px-3 py-2">
                          <span className="px-1.5 py-0.5 bg-slate-100 dark:bg-slate-700 text-slate-600 dark:text-slate-400 text-[10px] rounded font-mono">{r.policyCode}</span>
                        </td>
                        <td className="px-3 py-2 text-right font-semibold text-slate-700 dark:text-slate-300">{r.triggered}</td>
                        <td className="px-3 py-2 text-right font-semibold text-red-600">{r.blocked}</td>
                        <td className="px-3 py-2 text-right">
                          <div className="flex items-center justify-end gap-2">
                            <div className="w-12 h-1.5 bg-slate-200 rounded-full overflow-hidden">
                              <div
                                className="h-full rounded-full bg-teal-500"
                                style={{ width: `${Math.min(r.effectiveness, 100)}%` }}
                              />
                            </div>
                            <span className="text-slate-700 dark:text-slate-300 font-medium">{r.effectiveness}%</span>
                          </div>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
                {topRules.length === 0 && (
                  <div className="text-center py-4 text-xs text-slate-400">No rule trigger data available</div>
                )}
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
