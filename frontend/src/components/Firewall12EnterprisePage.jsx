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
import { SafeResponsiveChart } from "./SafeResponsiveChart";
import { useFirewallData } from "../hooks/useFirewallData";
import { useBackendHealth } from "../hooks/useBackendHealth";
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

// ── ECharts option builders (replace recharts; zs-light/zs-dark theme drives
// axis/grid/tooltip colors so the charts read correctly on the dark card). The
// neutral slate series color (#6b7280) is preserved.
function eventTrendOption(rows) {
  return {
    grid: { top: 14, right: 14, bottom: 24, left: 40 },
    tooltip: { trigger: "axis" },
    xAxis: { type: "category", boundaryGap: false, data: rows.map((r) => r.time), axisLabel: { fontSize: 11 } },
    yAxis: { type: "value", axisLabel: { fontSize: 11 } },
    series: [{ name: "Events", type: "line", smooth: true, showSymbol: false, data: rows.map((r) => r.primary), lineStyle: { color: "#6b7280", width: 2 }, itemStyle: { color: "#6b7280" } }],
  };
}

function topCategoriesOption(rows) {
  return {
    grid: { top: 10, right: 18, bottom: 18, left: 104 },
    tooltip: { trigger: "axis", axisPointer: { type: "shadow" } },
    xAxis: { type: "value", minInterval: 1, axisLabel: { fontSize: 11 } },
    // inverse: true keeps the largest category (data is sorted desc) at the top,
    // matching the prior recharts horizontal-bar order.
    yAxis: { type: "category", inverse: true, data: rows.map((r) => r.name), axisLabel: { fontSize: 11, width: 96, overflow: "truncate" } },
    series: [{ name: "Events", type: "bar", itemStyle: { color: "#6b7280", borderRadius: [0, 6, 6, 0] }, data: rows.map((r) => r.value) }],
  };
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
  const [activeSection, setActiveSection] = useState("pipeline");
  const [externalCreateSignal, setExternalCreateSignal] = useState(0);
  const [vectorCreateSignal, setVectorCreateSignal] = useState(0);
  const [externalCreateScope, setExternalCreateScope] = useState("pipeline");
  const [externalCreateScopeLocked, setExternalCreateScopeLocked] = useState(false);
  const { fetchWithAuth } = useAuth();
  const firewallData = useFirewallData("1.2", timeRange);
  // Real backend reachability drives the module status pill (was a hardcoded
  // green "Operational").
  const backendHealth = useBackendHealth();
  const opBadge = backendHealth === "connected"
    ? { label: "Operational", cls: "bg-emerald-50 dark:bg-emerald-900/25 text-emerald-700 dark:text-emerald-300", Icon: CheckCircle2 }
    : backendHealth === "checking"
      ? { label: "Checking…", cls: "bg-amber-50 dark:bg-amber-900/25 text-amber-700 dark:text-amber-300", Icon: RefreshCw }
      : { label: "Backend offline", cls: "bg-red-50 dark:bg-red-900/25 text-red-700 dark:text-red-300", Icon: AlertTriangle };
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
      const domains = ["pipeline", "rag", "mcp"];
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
    { key: "pipeline", label: "Pipeline" },
    { key: "rag", label: "RAG" },
    { key: "mcp", label: "MCP" },
    { key: "vector", label: "Vector" },
    { key: "analytics", label: "Analytics" },
  ];

  const exportCandidates = useMemo(() => {
    const section = activeSection === "analytics" || activeSection === "vector" ? "pipeline" : activeSection;
    return policies.filter((p) => {
      const domain = String(p.policy_domain || "pipeline").toLowerCase();
      const normalized = domain === "global" || domain === "" ? "pipeline" : domain;
      return normalized === section;
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
          onRequestGenericCreate={handleRequestGenericCreate}
        />
      );
    }
    return null;
  };

  const handleNewPolicy = () => {
    const behavior = resolvePolicyCreateBehavior(activeSection, "header");
    if (behavior.usesVectorModal) {
      setVectorCreateSignal((n) => n + 1);
      return;
    }
    if (behavior.shouldSwitchSection) {
      setActiveSection(behavior.targetSection);
    }
    setExternalCreateScope(behavior.scope);
    // Open UNLOCKED so the in-panel domain switcher (Global / Pipeline / RAG /
    // MCP / Vector) is interactive. The active tab still preselects the domain,
    // but the user can now morph the panel to another domain without closing it.
    setExternalCreateScopeLocked(false);
    setExternalCreateSignal((n) => n + 1);
  };

  // In-panel domain switcher hand-offs. Vector is a separate resource, so when
  // the user switches to/from it we swap modals (and the active tab) to keep the
  // tab and the switcher in sync — one source of truth, not two competing ones.
  const handleRequestVectorCreate = () => {
    setActiveSection("vector");
    setVectorCreateSignal((n) => n + 1);
  };

  const handleRequestGenericCreate = (domain) => {
    const normalized = domain === "global" ? "pipeline" : domain;
    const safe = ["pipeline", "rag", "mcp"].includes(normalized) ? normalized : "pipeline";
    setActiveSection(safe);
    setExternalCreateScope(safe);
    setExternalCreateScopeLocked(false);
    setExternalCreateSignal((n) => n + 1);
  };

  return (
    <div className="space-y-5 pb-8">
      <section className="rounded-2xl border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-900 px-5 py-4">
        <div className="flex flex-col gap-3 lg:flex-row lg:items-center lg:justify-between">
          <div className="flex flex-wrap items-start gap-3">
            <div className="mt-1 rounded-xl border border-slate-200 dark:border-slate-700 p-2">
              <Shield className="h-5 w-5 text-slate-700 dark:text-slate-200" />
            </div>
            <div className="min-w-0">
              <h1 className="text-3xl font-semibold leading-tight text-slate-900 dark:text-slate-100">Policy Management</h1>
              <p className="text-sm text-slate-500 dark:text-slate-400">Firewall Module 1.2</p>
            </div>
            <span className={`mt-1 inline-flex items-center gap-1 rounded-full px-3 py-1 text-xs font-semibold ${opBadge.cls}`}>
              <opBadge.Icon className="h-3.5 w-3.5" />
              {opBadge.label}
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
            <SafeResponsiveChart className="h-[210px] w-full" option={eventTrendOption(trendData)} />
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
            <SafeResponsiveChart className="h-[210px] w-full" option={topCategoriesOption(categoryData)} />
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

      {activeSection !== "analytics" ? (
        <section className="rounded-2xl border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-900 p-4">
          {activeSection === "vector" ? (
            renderInspectionPanel()
          ) : policyLoading ? (
            sectionSkeleton()
          ) : (
            <PolicyManagementPanel
              title="Advanced Policy Workspace"
              description="Full CRUD, compile and rule operations."
              scope={activeSection}
              showCompileButton={true}
              showFilters={true}
              externalCreateSignal={externalCreateSignal}
              externalCreateScope={externalCreateScope}
              externalCreateScopeLocked={externalCreateScopeLocked}
              onRequestVectorCreate={handleRequestVectorCreate}
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

