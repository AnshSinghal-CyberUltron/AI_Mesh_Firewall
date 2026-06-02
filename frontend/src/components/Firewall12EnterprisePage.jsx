import { useMemo, useState, useEffect, useCallback } from "react";
import {
  Activity,
  AlertTriangle,
  Download,
  Plus,
  CheckCircle2,
  RefreshCw,
  CircleSlash2,
  Eye,
  Shield,
  Gauge,
} from "lucide-react";
import {
  LineChart,
  Line,
  Tooltip,
  XAxis,
  YAxis,
  CartesianGrid,
  ResponsiveContainer,
  BarChart,
  Bar,
} from "recharts";
import { useFirewallData } from "../hooks/useFirewallData";
import { useAuth } from "../context/AuthContext";
import { PolicyManagementPanel } from "./PolicyManagementPanel";
import { PolicyAnalyticsPanel } from "./PolicyAnalyticsPanel";
import { VectorPolicyPanel } from "./VectorPolicyPanel";
import { resolvePolicyCreateBehavior } from "../utils/policyCreateBehavior";

function numberOrDash(value) {
  if (value == null || Number.isNaN(Number(value))) return "--";
  return Number(value).toLocaleString();
}

function percentOrDash(value) {
  if (value == null || Number.isNaN(Number(value))) return "--";
  return `${Number(value).toFixed(1)}%`;
}

function sectionSkeleton() {
  return (
    <div className="rounded-2xl border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-900 p-5 animate-pulse">
      <div className="h-4 w-44 rounded bg-slate-200 dark:bg-slate-700 mb-4" />
      <div className="space-y-3">
        <div className="h-3 rounded bg-slate-200 dark:bg-slate-700" />
        <div className="h-3 rounded bg-slate-200 dark:bg-slate-700" />
        <div className="h-3 w-2/3 rounded bg-slate-200 dark:bg-slate-700" />
      </div>
    </div>
  );
}

function KpiCard({ icon: Icon, label, value, change, up = true }) {
  return (
    <div className="rounded-2xl border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-900 p-4 shadow-sm">
      <div className="flex items-center justify-between">
        <p className="text-sm text-slate-500 dark:text-slate-400">{label}</p>
        <Icon className="h-4 w-4 text-slate-400" />
      </div>
      <p className="mt-3 text-4xl font-semibold leading-none text-slate-900 dark:text-slate-100">{value}</p>
      {change ? (
        <p className={`mt-3 text-xs font-medium ${up ? "text-emerald-600 dark:text-emerald-400" : "text-rose-600 dark:text-rose-400"}`}>
          {up ? "↗" : "↘"} {change}
        </p>
      ) : null}
    </div>
  );
}

function ChartCard({ title, children }) {
  return (
    <div className="rounded-2xl border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-900 p-5 min-h-[270px]">
      <div className="mb-4 flex items-center justify-between">
        <h3 className="text-lg font-semibold text-slate-900 dark:text-slate-100">{title}</h3>
        <Gauge className="h-4 w-4 text-slate-400" />
      </div>
      {children}
    </div>
  );
}

export function Firewall12EnterprisePage({ onViewResults, onViewLogDetail, children }) {
  void onViewLogDetail;
  const [timeRange, setTimeRange] = useState("24h");
  const [activeSection, setActiveSection] = useState("global");
  const [externalCreateSignal, setExternalCreateSignal] = useState(0);
  const [vectorCreateSignal, setVectorCreateSignal] = useState(0);
  const [externalCreateScope, setExternalCreateScope] = useState("global");
  const [externalCreateScopeLocked, setExternalCreateScopeLocked] = useState(false);
  const { fetchWithAuth } = useAuth();
  const firewallData = useFirewallData("1.2", timeRange);
  const [policies, setPolicies] = useState([]);
  const [policyLoading, setPolicyLoading] = useState(true);

  const totalEvents = firewallData.socKpis?.total_threats || firewallData.threatFeed.length || 0;
  const blocked = firewallData.socKpis?.blocked || 0;
  const redacted = firewallData.socKpis?.redacted || 0;
  const critical = firewallData.socKpis?.critical_count || 0;
  const successRate = totalEvents > 0 ? ((Math.max(totalEvents - blocked - redacted, 0) / totalEvents) * 100) : null;

  const trendData = firewallData.timeSeriesData || [];
  const actionDistribution = firewallData.actionDistributionData || [];

  const categoryData = useMemo(() => {
    const counts = {};
    for (const ev of firewallData.threatFeed || []) {
      const key = ev.category || "unknown";
      counts[key] = (counts[key] || 0) + 1;
    }
    return Object.entries(counts)
      .map(([name, value]) => ({ name, value }))
      .sort((a, b) => b.value - a.value)
      .slice(0, 5);
  }, [firewallData.threatFeed]);

  const fetchPolicies = useCallback(async () => {
    setPolicyLoading(true);
    try {
      const domains = ["global", "pipeline", "rag", "mcp"];
      const all = [];
      for (const domain of domains) {
        const res = await fetchWithAuth(`/api/policies/?policy_domain=${domain}`);
        if (!res.ok) continue;
        const data = await res.json();
        all.push(...(Array.isArray(data) ? data : (data.results || [])));
      }
      setPolicies(all);
    } finally {
      setPolicyLoading(false);
    }
  }, [fetchWithAuth]);

  useEffect(() => {
    fetchPolicies();
  }, [fetchPolicies]);

  const sections = [
    { key: "global", label: "Global" },
    { key: "pipeline", label: "Pipeline" },
    { key: "rag", label: "RAG" },
    { key: "mcp", label: "MCP" },
    { key: "vector", label: "Vector" },
    { key: "analytics", label: "Analytics" },
  ];

  const exportCandidates = useMemo(() => {
    const section = activeSection === "analytics" || activeSection === "vector" ? "global" : activeSection;
    return policies.filter((p) => {
      const domain = String(p.policy_domain || "").toLowerCase();
      return section === "global" ? (domain === "global" || domain === "") : domain === section;
    });
  }, [policies, activeSection]);

  const exportPolicies = () => {
    const rows = exportCandidates.map((p) => ({
      name: p.name,
      code: p.code,
      domain: p.policy_domain,
      rules: p.rule_count || 0,
      updated_at: p.updated_at || "",
    }));
    const blob = new Blob([JSON.stringify(rows, null, 2)], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = "policy-summary.json";
    a.click();
    URL.revokeObjectURL(url);
  };

  const renderInspectionPanel = () => {
    if (activeSection === "analytics") return <PolicyAnalyticsPanel />;
    if (activeSection === "vector") {
      return (
        <VectorPolicyPanel
          title="Vector DB Policies"
          description="Collection-level policy controls for vector retrieval paths."
          externalCreateSignal={vectorCreateSignal}
        />
      );
    }
    return null;
  };

  const handleNewPolicy = () => {
    if (activeSection === "vector") {
      // Vector tab uses the dedicated vector policy CRUD model/API.
      setVectorCreateSignal((n) => n + 1);
      return;
    }
    const behavior = resolvePolicyCreateBehavior(activeSection, "header");
    if (behavior.shouldSwitchSection) {
      setActiveSection(behavior.targetSection);
    }
    setExternalCreateScope(behavior.scope);
    // Header-level create should allow choosing any policy domain.
    setExternalCreateScopeLocked(false);
    setExternalCreateSignal((n) => n + 1);
  };

  return (
    <div className="space-y-5 pb-8">
      <section className="rounded-2xl border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-900 px-5 py-4">
        <div className="flex flex-col gap-3 lg:flex-row lg:items-center lg:justify-between">
          <div className="flex items-start gap-3">
            <div className="mt-1 rounded-xl border border-slate-200 dark:border-slate-700 p-2">
              <Shield className="h-5 w-5 text-slate-700 dark:text-slate-200" />
            </div>
            <div>
              <h1 className="text-3xl font-semibold leading-tight text-slate-900 dark:text-slate-100">Policy Management</h1>
              <p className="text-sm text-slate-500 dark:text-slate-400">Firewall Module 1.2</p>
            </div>
            <span className="mt-1 inline-flex items-center gap-1 rounded-full bg-emerald-50 dark:bg-emerald-900/25 px-3 py-1 text-xs font-semibold text-emerald-700 dark:text-emerald-300">
              <CheckCircle2 className="h-3.5 w-3.5" />
              Operational
            </span>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <button
              onClick={() => {
                firewallData.refetch?.({ background: true });
                fetchPolicies();
              }}
              className="inline-flex items-center gap-2 rounded-xl border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-900 px-3 py-2 text-sm font-medium text-slate-700 dark:text-slate-200 hover:bg-slate-50 dark:hover:bg-slate-800"
            >
              <RefreshCw className={`h-4 w-4 ${firewallData.loading ? "animate-spin" : ""}`} />
              Refresh
            </button>
            <button
              onClick={exportPolicies}
              className="inline-flex items-center gap-2 rounded-xl border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-900 px-3 py-2 text-sm font-medium text-slate-700 dark:text-slate-200 hover:bg-slate-50 dark:hover:bg-slate-800"
            >
              <Download className="h-4 w-4" />
              Export
            </button>
            <button
              onClick={handleNewPolicy}
              className="inline-flex items-center gap-2 rounded-xl bg-slate-900 dark:bg-white px-3 py-2 text-sm font-semibold text-white dark:text-slate-900 hover:opacity-90"
            >
              <Plus className="h-4 w-4" />
              New Policy
            </button>
          </div>
        </div>
      </section>

      <section className="grid gap-3 sm:grid-cols-2 xl:grid-cols-5">
        <KpiCard icon={Activity} label="Total Events" value={numberOrDash(totalEvents)} />
        <KpiCard icon={CircleSlash2} label="Blocked" value={numberOrDash(blocked)} />
        <KpiCard icon={Eye} label="Redacted" value={numberOrDash(redacted)} />
        <KpiCard icon={AlertTriangle} label="Critical" value={numberOrDash(critical)} />
        <KpiCard icon={CheckCircle2} label="Success Rate" value={percentOrDash(successRate)} />
      </section>

      <section className="grid gap-4 xl:grid-cols-3">
        <ChartCard title="Event Trend">
          {firewallData.loading && trendData.length === 0 ? sectionSkeleton() : (
            <ResponsiveContainer width="100%" height={210}>
              <LineChart data={trendData}>
                <CartesianGrid strokeDasharray="3 3" stroke="#33415522" />
                <XAxis dataKey="time" tick={{ fontSize: 11 }} />
                <YAxis tick={{ fontSize: 11 }} />
                <Tooltip />
                <Line type="monotone" dataKey="primary" stroke="#6b7280" strokeWidth={2} dot={false} />
              </LineChart>
            </ResponsiveContainer>
          )}
        </ChartCard>

        <ChartCard title="Action Distribution">
          {firewallData.loading && actionDistribution.length === 0 ? sectionSkeleton() : (
            <div className="space-y-3 pt-2">
              {(actionDistribution.length ? actionDistribution : [
                { name: "Allowed", value: 0, color: "#22c55e" },
                { name: "Blocked", value: 0, color: "#ef4444" },
                { name: "Redacted", value: 0, color: "#facc15" },
                { name: "Flagged", value: 0, color: "#f97316" },
              ]).map((a) => {
                const total = actionDistribution.reduce((s, x) => s + (x.value || 0), 0) || 1;
                const pct = ((a.value || 0) / total) * 100;
                return (
                  <div key={a.name}>
                    <div className="mb-1 flex items-center justify-between text-sm">
                      <span className="text-slate-600 dark:text-slate-300">{a.name}</span>
                      <span className="font-medium text-slate-800 dark:text-slate-200">{pct.toFixed(1)}%</span>
                    </div>
                    <div className="h-2 rounded-full bg-slate-100 dark:bg-slate-800 overflow-hidden">
                      <div className="h-full rounded-full" style={{ width: `${Math.max(4, pct)}%`, backgroundColor: a.color || "#6b7280" }} />
                    </div>
                  </div>
                );
              })}
            </div>
          )}
        </ChartCard>

        <ChartCard title="Top Categories">
          {firewallData.loading && categoryData.length === 0 ? sectionSkeleton() : (
            <ResponsiveContainer width="100%" height={210}>
              <BarChart data={categoryData}>
                <CartesianGrid strokeDasharray="3 3" stroke="#33415522" />
                <XAxis dataKey="name" hide />
                <YAxis tick={{ fontSize: 11 }} width={100} dataKey="name" type="category" />
                <Tooltip />
                <Bar dataKey="value" fill="#6b7280" radius={[0, 6, 6, 0]} />
              </BarChart>
            </ResponsiveContainer>
          )}
        </ChartCard>
      </section>

      <section className="rounded-2xl border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-900 p-4">
        <div className="flex flex-wrap items-center gap-2">
          {sections.map((section) => (
            <button
              key={section.key}
              onClick={() => setActiveSection(section.key)}
              className={`rounded-lg px-3 py-2 text-sm font-semibold transition-colors ${
                activeSection === section.key
                  ? "bg-slate-100 dark:bg-slate-800 text-slate-900 dark:text-slate-100"
                  : "text-slate-600 dark:text-slate-400 hover:bg-slate-50 dark:hover:bg-slate-800"
              }`}
            >
              {section.label}
            </button>
          ))}
        </div>
      </section>

      {(activeSection !== "analytics" && activeSection !== "vector") ? (
        <section className="rounded-2xl border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-900 p-4">
          {policyLoading ? sectionSkeleton() : (
            <PolicyManagementPanel
              title="Advanced Policy Workspace"
              description="Full CRUD, compile and rule operations."
              scope={activeSection}
              showCompileButton={activeSection === "global"}
              showFilters={true}
              externalCreateSignal={externalCreateSignal}
              externalCreateScope={externalCreateScope}
              externalCreateScopeLocked={externalCreateScopeLocked}
            />
          )}
        </section>
      ) : (
        <section className="rounded-2xl border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-900 p-4">
          {renderInspectionPanel()}
        </section>
      )}

      {children ? <section>{children}</section> : null}
    </div>
  );
}

