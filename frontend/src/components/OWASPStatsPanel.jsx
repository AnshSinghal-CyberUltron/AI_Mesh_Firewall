import { useState, useEffect, useCallback } from "react";
import {
  Loader2, RefreshCw, Shield, AlertTriangle, ChevronDown, ChevronRight,
} from "lucide-react";
import {
  BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer,
  RadarChart, Radar, PolarGrid, PolarAngleAxis, PolarRadiusAxis,
} from "recharts";
import { useAuth } from "../context/AuthContext";
import { SafeResponsiveChart } from "./SafeResponsiveChart";

const FAMILY_CONFIG = {
  llm: { label: "LLM (OWASP Top 10)", color: "#14b8a6", bg: "bg-teal-50 dark:bg-teal-900/20", border: "border-teal-200 dark:border-teal-800", text: "text-teal-700" },
  mcp: { label: "MCP / Tool Security", color: "#8b5cf6", bg: "bg-purple-50 dark:bg-purple-900/20", border: "border-purple-200 dark:border-purple-800", text: "text-purple-700" },
  agentic: { label: "Agentic AI", color: "#f59e0b", bg: "bg-amber-50 dark:bg-amber-900/20", border: "border-amber-200 dark:border-amber-800", text: "text-amber-700" },
};

const HOURS_OPTIONS = [
  { value: 24, label: "24h" },
  { value: 72, label: "3d" },
  { value: 168, label: "7d" },
  { value: 336, label: "14d" },
  { value: 720, label: "30d" },
];

function CoverageBadge({ coverage }) {
  if (coverage >= 95) return <span className="px-1.5 py-0.5 bg-emerald-100 dark:bg-emerald-800/30 text-emerald-700 text-[10px] font-semibold rounded-full">EXCELLENT</span>;
  if (coverage >= 80) return <span className="px-1.5 py-0.5 bg-blue-100 dark:bg-blue-800/30 text-blue-700 text-[10px] font-semibold rounded-full">GOOD</span>;
  if (coverage >= 50) return <span className="px-1.5 py-0.5 bg-amber-100 dark:bg-amber-800/30 text-amber-700 text-[10px] font-semibold rounded-full">MODERATE</span>;
  return <span className="px-1.5 py-0.5 bg-red-100 dark:bg-red-800/30 text-red-700 text-[10px] font-semibold rounded-full">LOW</span>;
}

function FamilySection({ familyKey, vectors, expanded, onToggle }) {
  const cfg = FAMILY_CONFIG[familyKey] || FAMILY_CONFIG.llm;
  const totalDetected = vectors.reduce((s, v) => s + v.detected, 0);
  const totalBlocked = vectors.reduce((s, v) => s + v.blocked, 0);
  const avgCoverage = totalDetected > 0 ? Math.round((totalBlocked / totalDetected) * 100) : 100;
  const activeVectors = vectors.filter((v) => v.detected > 0).length;

  const chartData = vectors
    .filter((v) => v.detected > 0)
    .map((v) => ({
      name: v.code,
      detected: v.detected,
      blocked: v.blocked,
      allowed: Math.max(0, v.detected - v.blocked),
    }));

  return (
    <div className={`border ${cfg.border} rounded-lg overflow-hidden`}>
      <button
        onClick={onToggle}
        className={`w-full flex items-center justify-between px-4 py-3 ${cfg.bg} hover:opacity-90 transition-colors`}
      >
        <div className="flex items-center gap-3">
          <Shield className={`w-4 h-4 ${cfg.text}`} />
          <span className={`text-sm font-semibold ${cfg.text}`}>{cfg.label}</span>
          <span className="text-xs text-slate-500 dark:text-slate-400">{activeVectors} active / {vectors.length} vectors</span>
        </div>
        <div className="flex items-center gap-4">
          <div className="flex items-center gap-2 text-xs">
            <span className="text-slate-600 dark:text-slate-400">Detected: <span className="font-semibold text-slate-800 dark:text-slate-200">{totalDetected}</span></span>
            <span className="text-slate-600 dark:text-slate-400">Blocked: <span className="font-semibold text-red-600">{totalBlocked}</span></span>
            <CoverageBadge coverage={avgCoverage} />
          </div>
          {expanded ? <ChevronDown className="w-4 h-4 text-slate-400" /> : <ChevronRight className="w-4 h-4 text-slate-400" />}
        </div>
      </button>

      {expanded && (
        <div className="p-4 space-y-4 bg-white dark:bg-slate-800">
          {chartData.length > 0 ? (
            <SafeResponsiveChart className="h-[200px]">
              <BarChart data={chartData} margin={{ top: 5, right: 10, bottom: 5, left: 0 }}>
                <CartesianGrid strokeDasharray="3 3" stroke="#f1f5f9" />
                <XAxis dataKey="name" stroke="#94a3b8" tick={{ fontSize: 10 }} />
                <YAxis stroke="#94a3b8" tick={{ fontSize: 10 }} />
                <Tooltip contentStyle={{ fontSize: 11, border: "1px solid #e2e8f0", borderRadius: "8px" }} />
                <Bar dataKey="blocked" stackId="a" fill="#ef4444" name="Blocked" />
                <Bar dataKey="allowed" stackId="a" fill="#94a3b8" name="Allowed" radius={[4, 4, 0, 0]} />
              </BarChart>
            </SafeResponsiveChart>
          ) : (
            <div className="text-center py-6 text-xs text-slate-400">No detections in this period</div>
          )}

          <div className="overflow-x-auto">
            <table className="w-full text-xs">
              <thead>
                <tr className="bg-slate-50 dark:bg-slate-800/50 border-b border-slate-200 dark:border-slate-700">
                  <th className="px-3 py-2 text-left font-semibold text-slate-600 dark:text-slate-300 uppercase">Vector</th>
                  <th className="px-3 py-2 text-right font-semibold text-slate-600 dark:text-slate-300 uppercase">Detected</th>
                  <th className="px-3 py-2 text-right font-semibold text-slate-600 dark:text-slate-300 uppercase">Blocked</th>
                  <th className="px-3 py-2 text-right font-semibold text-slate-600 dark:text-slate-300 uppercase">Coverage</th>
                  <th className="px-3 py-2 text-right font-semibold text-slate-600 dark:text-slate-300 uppercase">Status</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100 dark:divide-slate-700">
                {vectors.map((v) => (
                  <tr key={v.code} className="hover:bg-slate-50 dark:hover:bg-slate-700 transition-colors">
                    <td className="px-3 py-2 font-medium text-slate-800 dark:text-slate-200">{v.vector}</td>
                    <td className="px-3 py-2 text-right text-slate-700 dark:text-slate-300">{v.detected}</td>
                    <td className="px-3 py-2 text-right font-semibold text-red-600">{v.blocked}</td>
                    <td className="px-3 py-2 text-right text-slate-700 dark:text-slate-300">{v.coverage}%</td>
                    <td className="px-3 py-2 text-right"><CoverageBadge coverage={v.coverage} /></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  );
}

export function OWASPStatsPanel() {
  const { fetchWithAuth } = useAuth();
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [hours, setHours] = useState(168);
  const [expanded, setExpanded] = useState({ llm: true, mcp: false, agentic: false });

  const fetchData = useCallback(async () => {
    setLoading(true);
    try {
      const res = await fetchWithAuth(`/api/security/owasp-stats/?hours=${hours}`);
      if (res.ok) {
        setData(await res.json());
      }
    } finally {
      setLoading(false);
    }
  }, [fetchWithAuth, hours]);

  useEffect(() => {
    fetchData();
  }, [fetchData]);

  const allVectors = data
    ? [...(data.llm || []), ...(data.mcp || []), ...(data.agentic || [])]
    : [];
  const totalDetected = allVectors.reduce((s, v) => s + v.detected, 0);
  const totalBlocked = allVectors.reduce((s, v) => s + v.blocked, 0);
  const overallCoverage = totalDetected > 0 ? Math.round((totalBlocked / totalDetected) * 100) : 100;

  const radarData = allVectors
    .filter((v) => v.detected > 0)
    .slice(0, 10)
    .map((v) => ({ vector: v.code, detected: v.detected, blocked: v.blocked }));

  return (
    <div className="bg-white dark:bg-slate-800 border border-slate-200 dark:border-slate-700 rounded-xl shadow-sm p-6">
      <div className="flex items-center justify-between mb-4">
        <div>
          <h3 className="text-base font-semibold text-slate-900 dark:text-slate-100">OWASP Threat Coverage</h3>
          <p className="text-xs text-slate-500 dark:text-slate-400 mt-0.5">
            Detection and blocking rates across OWASP LLM, MCP, and Agentic AI threat vectors
          </p>
        </div>
        <div className="flex items-center gap-2">
          <select
            value={hours}
            onChange={(e) => setHours(Number(e.target.value))}
            className="text-slate-900 dark:text-slate-100 bg-white dark:bg-slate-800 px-2 py-1.5 border border-slate-200 dark:border-slate-700 rounded-lg text-xs focus:ring-2 focus:ring-teal-500"
          >
            {HOURS_OPTIONS.map((o) => (
              <option key={o.value} value={o.value}>{o.label}</option>
            ))}
          </select>
          <button
            onClick={fetchData}
            disabled={loading}
            className="p-2 hover:bg-slate-100 dark:hover:bg-slate-700 rounded-lg transition-colors disabled:opacity-50"
            title="Refresh"
          >
            {loading ? <Loader2 className="w-4 h-4 text-teal-500 animate-spin" /> : <RefreshCw className="w-4 h-4 text-slate-500 dark:text-slate-400" />}
          </button>
        </div>
      </div>

      {/* Summary Stats */}
      <div className="grid grid-cols-4 gap-3 mb-5">
        <div className="bg-slate-50 dark:bg-slate-800/50 rounded-lg p-3">
          <div className="text-lg font-bold text-slate-900 dark:text-slate-100">{data ? totalDetected.toLocaleString() : "--"}</div>
          <div className="text-[10px] text-slate-500 dark:text-slate-400 uppercase font-medium">Total Detected</div>
        </div>
        <div className="bg-slate-50 dark:bg-slate-800/50 rounded-lg p-3">
          <div className="text-lg font-bold text-red-600">{data ? totalBlocked.toLocaleString() : "--"}</div>
          <div className="text-[10px] text-slate-500 dark:text-slate-400 uppercase font-medium">Total Blocked</div>
        </div>
        <div className="bg-slate-50 dark:bg-slate-800/50 rounded-lg p-3">
          <div className="text-lg font-bold text-teal-600">{data ? `${overallCoverage}%` : "--"}</div>
          <div className="text-[10px] text-slate-500 dark:text-slate-400 uppercase font-medium">Coverage Rate</div>
        </div>
        <div className="bg-slate-50 dark:bg-slate-800/50 rounded-lg p-3">
          <div className="text-lg font-bold text-slate-900 dark:text-slate-100">{data ? (data.unique_events_count || 0).toLocaleString() : "--"}</div>
          <div className="text-[10px] text-slate-500 dark:text-slate-400 uppercase font-medium">Unique Events</div>
        </div>
      </div>

      {loading && !data ? (
        <div className="flex items-center justify-center py-8">
          <Loader2 className="w-5 h-5 text-teal-500 animate-spin" />
          <span className="ml-2 text-sm text-slate-500 dark:text-slate-400">Loading OWASP statistics...</span>
        </div>
      ) : (
        <div className="space-y-4">
          {/* Radar Chart for top active vectors */}
          {radarData.length > 2 && (
            <div className="bg-slate-50 dark:bg-slate-800/50 rounded-lg p-4 mb-4">
              <h4 className="text-xs font-semibold text-slate-700 dark:text-slate-300 mb-3 uppercase">Threat Radar (Top Active Vectors)</h4>
              <ResponsiveContainer width="100%" height={250}>
                <RadarChart data={radarData}>
                  <PolarGrid stroke="#64748b" strokeOpacity={0.25} />
                  <PolarAngleAxis dataKey="vector" tick={{ fontSize: 10, fill: "#64748b" }} />
                  <PolarRadiusAxis tick={{ fontSize: 9 }} />
                  <Radar name="Detected" dataKey="detected" stroke="#14b8a6" fill="#14b8a6" fillOpacity={0.2} />
                  <Radar name="Blocked" dataKey="blocked" stroke="#ef4444" fill="#ef4444" fillOpacity={0.15} />
                  <Tooltip contentStyle={{ fontSize: 11, border: "1px solid #e2e8f0", borderRadius: "8px" }} />
                </RadarChart>
              </ResponsiveContainer>
            </div>
          )}

          {/* Family Sections */}
          {data && (
            <>
              <FamilySection
                familyKey="llm"
                vectors={data.llm || []}
                expanded={expanded.llm}
                onToggle={() => setExpanded((prev) => ({ ...prev, llm: !prev.llm }))}
              />
              <FamilySection
                familyKey="mcp"
                vectors={data.mcp || []}
                expanded={expanded.mcp}
                onToggle={() => setExpanded((prev) => ({ ...prev, mcp: !prev.mcp }))}
              />
              <FamilySection
                familyKey="agentic"
                vectors={data.agentic || []}
                expanded={expanded.agentic}
                onToggle={() => setExpanded((prev) => ({ ...prev, agentic: !prev.agentic }))}
              />
            </>
          )}
        </div>
      )}
    </div>
  );
}
