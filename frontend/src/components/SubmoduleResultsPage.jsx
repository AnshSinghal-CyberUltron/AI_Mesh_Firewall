import { useMemo, useState } from "react";
import {
  ChevronRight, Search, Filter as FilterIcon, Download, RefreshCw,
  X, Home, Loader2,
} from "lucide-react";
import { SafeResponsiveChart } from "./SafeResponsiveChart";
import { useFirewallData } from "../hooks/useFirewallData";

// Bar colors keyed to the enforcement action (theme-agnostic accents; the registered
// zs-light/zs-dark ECharts theme drives axis/grid/tooltip colors).
const ACTION_BAR_COLORS = { block: "#ef4444", redact: "#f59e0b", monitor: "#3b82f6", allow: "#10b981", allowed: "#10b981", flag: "#f59e0b", flagged: "#f59e0b" };

export function SubModuleResultsPage({
  moduleId, onBack, onViewLogDetail,
}) {
  const [searchTerm, setSearchTerm] = useState("");
  const [currentPage, setCurrentPage] = useState(1);
  const [actionFilter, setActionFilter] = useState("all");
  const [stageFilter, setStageFilter] = useState("all");
  const [selectedRow, setSelectedRow] = useState(null);
  const itemsPerPage = 20;

  const {
    socKpis,
    threatFeed,
    timeSeriesData,
    actionDistributionData,
    loading,
    error,
    refetch,
  } = useFirewallData(moduleId, "24h");

  const moduleConfig = getModuleResultsConfig(moduleId, socKpis);
  const ModuleIcon = moduleConfig.icon;
  const tableData = useMemo(() => mapThreatFeedToTableData(moduleId, threatFeed), [moduleId, threatFeed]);
  const keys = moduleConfig.tableColumnKeys;
  const tableColumns = moduleConfig.tableColumns;

  // High-density traffic-vs-enforcement time series (ECharts line+area; theme-aware
  // via the registered zs-light/zs-dark theme). Data-identical to the prior recharts view.
  const timeSeriesOption = useMemo(() => ({
    grid: { top: 24, right: 16, bottom: 26, left: 46 },
    tooltip: { trigger: "axis" },
    legend: { top: 0, itemHeight: 8, itemWidth: 12, textStyle: { fontSize: 10 } },
    xAxis: {
      type: "category", boundaryGap: false,
      data: (timeSeriesData || []).map((d) => d.time),
      axisLabel: { fontSize: 10, interval: Math.max(0, Math.ceil((timeSeriesData || []).length / 6) - 1) },
    },
    yAxis: { type: "value", axisLabel: { fontSize: 10 } },
    series: [
      { name: "Total Traffic", type: "line", smooth: true, showSymbol: false, lineStyle: { width: 2 }, itemStyle: { color: "#14b8a6" }, areaStyle: { color: "#14b8a6", opacity: 0.18 }, data: (timeSeriesData || []).map((d) => d.primary) },
      { name: "Enforcements", type: "line", smooth: true, showSymbol: false, lineStyle: { width: 2 }, itemStyle: { color: "#8b5cf6" }, areaStyle: { color: "#8b5cf6", opacity: 0.14 }, data: (timeSeriesData || []).map((d) => d.secondary) },
    ],
  }), [timeSeriesData]);

  // Action distribution (ECharts horizontal bar; bars keyed to enforcement action).
  const actionDistOption = useMemo(() => ({
    grid: { top: 12, right: 18, bottom: 20, left: 88 },
    tooltip: { trigger: "axis", axisPointer: { type: "shadow" } },
    xAxis: { type: "value", axisLabel: { fontSize: 11 } },
    yAxis: { type: "category", data: (actionDistributionData || []).map((d) => d.name), axisLabel: { fontSize: 11 } },
    series: [{
      type: "bar", barWidth: "55%",
      data: (actionDistributionData || []).map((d) => ({ value: d.value, itemStyle: { color: ACTION_BAR_COLORS[String(d.name).toLowerCase()] || "#14b8a6", borderRadius: [0, 4, 4, 0] } })),
    }],
  }), [actionDistributionData]);

  const filteredData = tableData.filter((row) => {
    const matchesSearch =
      searchTerm === "" || JSON.stringify(row).toLowerCase().includes(searchTerm.toLowerCase());
    const matchesAction = actionFilter === "all" || (row.action || "").toLowerCase() === actionFilter;
    const matchesStage = stageFilter === "all" || (row.stage || "").toLowerCase() === stageFilter;
    return matchesSearch && matchesAction && matchesStage;
  });

  const totalPages = Math.ceil(filteredData.length / itemsPerPage);
  const startIndex = (currentPage - 1) * itemsPerPage;
  const paginatedData = filteredData.slice(startIndex, startIndex + itemsPerPage);

  const handleRowClick = (row) => {
    if (onViewLogDetail) {
      onViewLogDetail(row);
    } else {
      setSelectedRow(row);
    }
  };

  const colorClasses = {
    blue: "from-blue-500 to-indigo-600",
    teal: "from-teal-500 to-cyan-600",
    purple: "from-purple-500 to-pink-600",
    emerald: "from-emerald-500 to-green-600",
    amber: "from-amber-500 to-orange-600",
    orange: "from-orange-500 to-red-600",
    red: "from-red-500 to-rose-600",
    cyan: "from-cyan-500 to-teal-600",
    indigo: "from-indigo-500 to-purple-600",
  };

  const handleExport = () => {
    const rows = filteredData;
    const csvHeader = tableColumns.join(",");
    const csvRows = rows.map((row) =>
      keys
        .map((key) => {
          const raw = row[key] == null ? "" : String(row[key]);
          return `"${raw.replaceAll('"', '""')}"`;
        })
        .join(",")
    );
    const blob = new Blob([`${csvHeader}\n${csvRows.join("\n")}`], { type: "text/csv;charset=utf-8;" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `module-${moduleId.replace(".", "-")}-results.csv`;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
  };

  return (
    <div className="min-h-screen bg-gradient-to-br from-slate-50 dark:from-slate-900 via-white dark:via-slate-900 to-slate-50 dark:to-slate-900">
      <div className="p-4 sm:p-6 lg:p-8 space-y-6">
        {/* Breadcrumb */}
        <div className="bg-white dark:bg-slate-800 border border-slate-200 dark:border-slate-700 rounded-xl shadow-sm p-4">
          <div className="flex items-center gap-2 text-sm">
            <button onClick={onBack} className="flex items-center gap-1 text-slate-600 dark:text-slate-400 hover:text-teal-600 transition-colors">
              <Home className="w-4 h-4" />
              <span>Module 1</span>
            </button>
            <ChevronRight className="w-4 h-4 text-slate-400" />
            <button onClick={onBack} className="text-slate-600 dark:text-slate-400 hover:text-teal-600 transition-colors">
              {moduleId} {moduleConfig.title}
            </button>
            <ChevronRight className="w-4 h-4 text-slate-400" />
            <span className="text-teal-600 font-medium">View Results</span>
          </div>
        </div>

        {/* Page Header */}
        <div className="bg-white dark:bg-slate-800 border border-slate-200 dark:border-slate-700 rounded-xl shadow-sm p-6">
          <div className="flex items-start gap-4">
            <div className="w-14 h-14 bg-gradient-to-br from-teal-500 to-cyan-600 rounded-xl flex items-center justify-center shadow-sm">
              <ModuleIcon className="w-7 h-7 text-white" strokeWidth={2.5} />
            </div>
            <div className="flex-1">
              <div className="flex items-center gap-2 mb-1">
                <span className="text-xs font-bold text-teal-600">{moduleId}</span>
                <h1 className="text-2xl font-bold text-slate-900 dark:text-slate-100">{moduleConfig.title} - Detailed Results</h1>
              </div>
              <p className="text-sm text-slate-600 dark:text-slate-400">{moduleConfig.description}</p>
            </div>
          </div>
        </div>

        {/* AI Traffic Flow & Processing Pipeline */}
        <div className="bg-white dark:bg-slate-800 border border-slate-200 dark:border-slate-700 rounded-xl shadow-sm p-6">
          <div className="mb-6 flex flex-wrap items-center justify-between gap-2">
            <h2 className="text-lg font-semibold text-slate-900 dark:text-slate-100">AI Traffic Flow &amp; Processing Pipeline</h2>
            {socKpis?.avg_latency_ms != null && (
              <span className="text-xs font-mono text-slate-500 dark:text-slate-400">avg {Math.round(socKpis.avg_latency_ms)} ms end-to-end</span>
            )}
          </div>
          <div className="bg-slate-50 dark:bg-slate-800/50 rounded-lg p-4 sm:p-6 lg:p-8 border border-slate-200 dark:border-slate-700 overflow-x-auto">
            <div className="flex items-center justify-between">
              {moduleConfig.flowNodes.map((node, index) => (
                <div key={index} className="flex items-center">
                  <div className={`bg-gradient-to-br ${colorClasses[node.color] || colorClasses.teal} px-6 py-4 rounded-xl shadow-lg min-w-[160px]`}>
                    <div className="text-white text-sm font-semibold mb-1">{node.label}</div>
                    <div className="text-white text-2xl font-bold mb-2">{node.value}</div>
                    {node.metrics && node.metrics.length > 0 && (
                      <div className="space-y-0.5">
                        {node.metrics.map((metric, idx) => (
                          <div key={idx} className="text-white text-[10px] opacity-75">{metric}</div>
                        ))}
                      </div>
                    )}
                  </div>
                  {index < moduleConfig.flowNodes.length - 1 && (
                    <div className="flex flex-col items-center mx-4">
                      <div className="w-16 h-0.5 bg-slate-300 relative">
                        <div className="absolute right-0 top-1/2 -translate-y-1/2 w-0 h-0 border-t-4 border-t-transparent border-l-8 border-l-slate-300 border-b-4 border-b-transparent" />
                      </div>
                      <span className="text-[10px] text-slate-500 dark:text-slate-400 mt-1 font-mono">
                        {moduleConfig.arrowTimings[index] != null ? `${moduleConfig.arrowTimings[index]}ms` : "--"}
                      </span>
                    </div>
                  )}
                </div>
              ))}
            </div>
          </div>
        </div>

        {/* Charts */}
        <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
          <div className="lg:col-span-2 bg-white dark:bg-slate-800 border border-slate-200 dark:border-slate-700 rounded-xl shadow-sm p-6">
            <h3 className="text-base font-semibold text-slate-900 dark:text-slate-100 mb-4">High-Density Time-Series Analysis (7 Days)</h3>
            {timeSeriesData.length > 0 ? (
              <SafeResponsiveChart className="h-[350px] w-full" option={timeSeriesOption} />
            ) : (
              <div className="h-[350px] flex items-center justify-center text-sm text-slate-500 dark:text-slate-400">
                {loading ? "Loading timeline..." : "No timeline data available"}
              </div>
            )}
          </div>
          <div className="bg-white dark:bg-slate-800 border border-slate-200 dark:border-slate-700 rounded-xl shadow-sm p-6">
            <h3 className="text-base font-semibold text-slate-900 dark:text-slate-100 mb-4">Action Distribution</h3>
            {actionDistributionData && actionDistributionData.length > 0 ? (
              <SafeResponsiveChart className="h-[350px] w-full" option={actionDistOption} />
            ) : (
              <div className="h-[350px] flex items-center justify-center text-sm text-slate-500 dark:text-slate-400">
                {loading ? "Loading distribution..." : "No action data available"}
              </div>
            )}
          </div>
        </div>

        {/* Detailed Records */}
        <div className="bg-white dark:bg-slate-800 border border-slate-200 dark:border-slate-700 rounded-xl shadow-sm">
          <div className="p-6 border-b border-slate-200 dark:border-slate-700">
            <div className="flex flex-wrap items-center justify-between gap-3 mb-4">
              <h3 className="text-lg font-semibold text-slate-900 dark:text-slate-100">Detailed Records ({filteredData.length})</h3>
              <div className="flex flex-wrap items-center gap-2">
                <button
                  onClick={refetch}
                  disabled={loading}
                  className="flex items-center gap-2 px-3 py-2 bg-slate-100 dark:bg-slate-700 text-slate-700 dark:text-slate-300 rounded-lg hover:bg-slate-200 dark:hover:bg-slate-600 transition-colors text-sm disabled:opacity-60"
                >
                  {loading ? <Loader2 className="w-4 h-4 animate-spin" /> : <RefreshCw className="w-4 h-4" />}
                  Refresh
                </button>
                <button
                  onClick={handleExport}
                  disabled={filteredData.length === 0}
                  className="flex items-center gap-2 px-3 py-2 bg-teal-600 hover:bg-teal-700 text-white rounded-lg transition-colors text-sm disabled:opacity-60"
                >
                  <Download className="w-4 h-4" />
                  Export CSV
                </button>
                <div className="relative">
                  <select
                    value={actionFilter}
                    onChange={(e) => setActionFilter(e.target.value)}
                    className="appearance-none flex items-center gap-2 px-4 py-2 pr-9 bg-slate-100 dark:bg-slate-700 text-slate-700 dark:text-slate-300 rounded-lg hover:bg-slate-200 dark:hover:bg-slate-600 transition-colors text-sm"
                  >
                    <option value="all">All actions</option>
                    <option value="block">Blocked</option>
                    <option value="redact">Redacted</option>
                    <option value="monitor">Monitored</option>
                    <option value="allow">Allowed</option>
                  </select>
                  <FilterIcon className="w-4 h-4 absolute right-3 top-1/2 -translate-y-1/2 pointer-events-none text-slate-500 dark:text-slate-300" />
                </div>
                {moduleId === "1.2" && (
                  <div className="relative">
                    <select
                      value={stageFilter}
                      onChange={(e) => setStageFilter(e.target.value)}
                      className="appearance-none flex items-center gap-2 px-4 py-2 pr-9 bg-slate-100 dark:bg-slate-700 text-slate-700 dark:text-slate-300 rounded-lg hover:bg-slate-200 dark:hover:bg-slate-600 transition-colors text-sm"
                    >
                      <option value="all">All stages</option>
                      <option value="query">Query</option>
                      <option value="retriever">Retriever</option>
                      <option value="ranker">Ranker</option>
                      <option value="generator">Generator</option>
                    </select>
                    <FilterIcon className="w-4 h-4 absolute right-3 top-1/2 -translate-y-1/2 pointer-events-none text-slate-500 dark:text-slate-300" />
                  </div>
                )}
              </div>
            </div>
            {error && (
              <div className="text-xs text-red-600 dark:text-red-400 mb-3">
                Failed to refresh results: {error}
              </div>
            )}
            <div className="flex items-center gap-4">
              <div className="flex-1 relative">
                <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-slate-400" />
                <input
                  type="text"
                  placeholder="Search all fields..."
                  value={searchTerm}
                  onChange={(e) => setSearchTerm(e.target.value)}
                  className="w-full pl-10 pr-4 py-2 rounded-lg text-sm focus:ring-2 focus:ring-teal-500 bg-white dark:bg-slate-800/60 border border-slate-300 dark:border-slate-700 text-slate-900 dark:text-slate-100 placeholder:text-slate-500 dark:placeholder:text-slate-400"
                />
              </div>
            </div>
          </div>
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="bg-slate-50 dark:bg-slate-800/50 border-b border-slate-200 dark:border-slate-700">
                  {tableColumns.map((col, idx) => (
                    <th key={idx} className="px-4 py-3 text-left text-xs font-semibold text-slate-600 dark:text-slate-300 uppercase tracking-wider">
                      {col}
                    </th>
                  ))}
                  <th className="px-4 py-3 w-10" />
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-200 dark:divide-slate-700">
                {paginatedData.map((row, rowIdx) => (
                  <tr
                    key={row.id || rowIdx}
                    onClick={() => handleRowClick(row)}
                    className="hover:bg-teal-50 dark:hover:bg-teal-900/20 transition-colors cursor-pointer"
                  >
                    {keys.map((key, cellIdx) => (
                      <td key={cellIdx} className="px-4 py-3 text-slate-700 dark:text-slate-300 font-mono text-xs">
                        {typeof row[key] === "string" ? row[key] : JSON.stringify(row[key])}
                      </td>
                    ))}
                  </tr>
                ))}
                {!loading && paginatedData.length === 0 && (
                  <tr>
                    <td colSpan={tableColumns.length} className="px-4 py-8 text-center text-sm text-slate-500 dark:text-slate-400">
                      No records found for current filter
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
          <div className="p-4 border-t border-slate-200 dark:border-slate-700 flex flex-wrap items-center justify-between gap-3">
            <div className="text-sm text-slate-600 dark:text-slate-400">
              Showing {startIndex + 1} to {Math.min(startIndex + itemsPerPage, filteredData.length)} of {filteredData.length}
            </div>
            <div className="flex items-center gap-2">
              <button
                onClick={() => setCurrentPage(Math.max(1, currentPage - 1))}
                disabled={currentPage === 1}
                className="px-3 py-1 bg-slate-100 dark:bg-slate-700 text-slate-700 dark:text-slate-300 rounded hover:bg-slate-200 dark:hover:bg-slate-600 disabled:opacity-50 text-sm"
              >
                Previous
              </button>
              <span className="text-sm text-slate-600 dark:text-slate-400">Page {currentPage} of {totalPages}</span>
              <button
                onClick={() => setCurrentPage(Math.min(totalPages, currentPage + 1))}
                disabled={currentPage === totalPages || totalPages === 0}
                className="px-3 py-1 bg-slate-100 dark:bg-slate-700 text-slate-700 dark:text-slate-300 rounded hover:bg-slate-200 dark:hover:bg-slate-600 disabled:opacity-50 text-sm"
              >
                Next
              </button>
            </div>
          </div>
        </div>

        {selectedRow && (
          <div className="fixed inset-0 z-50 flex items-center justify-center p-4">
            <div className="absolute inset-0 bg-black/60" onClick={() => setSelectedRow(null)} />
            <div className="relative w-full max-w-5xl max-h-[90vh] overflow-auto rounded-xl border border-slate-200 bg-white shadow-2xl dark:border-slate-700 dark:bg-slate-800">
              <div className="sticky top-0 z-10 flex items-center justify-between border-b border-slate-200 bg-white px-5 py-3 dark:border-slate-700 dark:bg-slate-800">
                <h3 className="text-base font-semibold text-slate-900 dark:text-slate-100">Detailed Record - Zoom View</h3>
                <button
                  type="button"
                  onClick={() => setSelectedRow(null)}
                  className="rounded-md p-1.5 text-slate-600 dark:text-slate-300 hover:bg-slate-100 dark:hover:bg-slate-700"
                >
                  <X className="w-4 h-4" />
                </button>
              </div>

              <div className="grid grid-cols-1 lg:grid-cols-2 gap-5 p-5">
                <div className="rounded-lg border border-slate-200 bg-slate-50 p-4 dark:border-slate-700 dark:bg-slate-900/50">
                  <h4 className="text-sm font-semibold text-slate-800 dark:text-slate-100 mb-3">Detailed Metadata</h4>
                  <dl className="grid grid-cols-2 gap-x-4 gap-y-3">
                    {keys.map((key) => (
                      <div key={key} className="min-w-0">
                        <dt className="text-[10px] uppercase tracking-wide text-slate-500 dark:text-slate-400 mb-1">{key}</dt>
                        <dd className="text-xs text-slate-800 dark:text-slate-200 font-mono break-words">{formatCellValue(selectedRow[key])}</dd>
                      </div>
                    ))}
                  </dl>
                </div>

                <div className="rounded-lg border border-slate-700 bg-slate-900 p-4">
                  <h4 className="text-sm font-semibold text-slate-100 mb-3">Raw Request Payload</h4>
                  <pre className="text-xs leading-5 text-slate-200 whitespace-pre-wrap break-words max-h-[70vh] overflow-auto">
{JSON.stringify(selectedRow, null, 2)}
                  </pre>
                </div>
              </div>
            </div>
          </div>
        )}

      </div>
    </div>
  );
}

function getModuleResultsConfig(moduleId, socKpis) {
  const total = socKpis?.total_threats ?? 0;
  const blocked = socKpis?.blocked ?? 0;
  const redacted = socKpis?.redacted ?? 0;
  const allowed = Math.max(0, total - blocked - redacted);
  // Module 1.1 (ingress) reflects REQUESTS, not all enforcement events. With
  // routing active each request also emits a model_routed event, so total_threats
  // double-counts ingress. Use the request-scoped count for the 1.1 flow only.
  const requestsInspected = socKpis?.requests_inspected ?? total;
  const allowedReq = Math.max(0, requestsInspected - blocked - redacted);
  // The backend emits only an end-to-end avg_latency_ms — no per-hop breakdown. A
  // synthetic 0.3/0.4/0.3 split would fabricate per-arrow numbers, so arrow labels stay
  // "--" and the real end-to-end average is surfaced once in the flow heading instead.
  const timings3 = [null, null, null];
  const timings2 = [null, null];
  const base = {
    "1.1": {
      title: "AI Gateway & Traffic Ingress",
      description: "Real-time ingress traffic decisions and authentication/rate-limit outcomes.",
      icon: Home,
      flowNodes: [
        { label: "Client", value: requestsInspected.toLocaleString(), color: "blue" },
        { label: "Auth", value: requestsInspected.toLocaleString(), color: "teal" },
        { label: "Rate Limit", value: blocked.toLocaleString(), color: "purple" },
        { label: "Gateway", value: allowedReq.toLocaleString(), color: "emerald" },
      ],
      arrowTimings: timings3,
      tableColumns: ["Timestamp", "Category", "Subcategory", "Action", "Severity", "Source", "Model"],
      tableColumnKeys: ["timestamp", "category", "subcategory", "action", "severity", "source", "model"],
    },
    "1.2": {
      title: "Policy Management",
      description: "Live enforcement evidence behind the unified RAG and vector policy workspace.",
      icon: FilterIcon,
      flowNodes: [
        { label: "Policies", value: total.toLocaleString(), color: "blue" },
        { label: "Rules", value: total.toLocaleString(), color: "teal" },
        { label: "Blocks", value: blocked.toLocaleString(), color: "orange" },
        { label: "Audit", value: redacted.toLocaleString(), color: "emerald" },
      ],
      arrowTimings: timings3,
      tableColumns: ["Timestamp", "Stage", "Threat", "Action", "OWASP", "Risk", "Model"],
      tableColumnKeys: ["timestamp", "stage", "category", "action", "owaspCode", "severity", "model"],
    },
    "1.3": {
      title: "RAG & Vector DB Firewall",
      description: "Live retrieval-security evidence for the combined RAG and Vector DB operations workspace.",
      icon: FilterIcon,
      flowNodes: [
        { label: "Query", value: total.toLocaleString(), color: "teal" },
        { label: "Retriever", value: total.toLocaleString(), color: "purple" },
        { label: "Vector DB", value: blocked.toLocaleString(), color: "indigo" },
        { label: "Results", value: allowed.toLocaleString(), color: "cyan" },
      ],
      arrowTimings: timings3,
      tableColumns: ["Timestamp", "Collection", "Namespace", "Action", "Threat", "Risk", "Model"],
      tableColumnKeys: ["timestamp", "collection", "namespace", "action", "category", "severity", "model"],
    },
    "1.4": {
      title: "Context Assembly & MCP Guardrails",
      description: "Live MCP/context assembly enforcement and redaction outcomes.",
      icon: FilterIcon,
      flowNodes: [
        { label: "Context", value: total.toLocaleString(), color: "blue" },
        { label: "Redaction", value: redacted.toLocaleString(), color: "amber" },
        { label: "Guardrails", value: blocked.toLocaleString(), color: "purple" },
        { label: "Final", value: allowed.toLocaleString(), color: "emerald" },
      ],
      arrowTimings: timings3,
      tableColumns: ["Timestamp", "Threat", "Tools Invoked", "Data Accessed", "Action", "Risk", "Source"],
      tableColumnKeys: ["timestamp", "category", "toolsInvoked", "dataAccessed", "action", "severity", "source"],
    },
    "1.5": {
      title: "Multi-Model Governance & AI Mesh Routing",
      description: "Live model routing and governance decisions from runtime events.",
      icon: FilterIcon,
      flowNodes: [
        { label: "Request", value: total.toLocaleString(), color: "blue" },
        { label: "Router", value: total.toLocaleString(), color: "purple" },
        { label: "Policies", value: blocked.toLocaleString(), color: "cyan" },
        { label: "Execute", value: allowed.toLocaleString(), color: "emerald" },
      ],
      arrowTimings: timings3,
      tableColumns: ["Timestamp", "Requested Model", "Routed Model", "Action", "Risk", "Source"],
      tableColumnKeys: ["timestamp", "requestedModel", "routedModel", "action", "severity", "source"],
    },
    "1.6": {
      title: "Inline Model Isolation & Kill-Switch",
      description: "Live model isolation, high-risk controls, and kill-switch actions.",
      icon: FilterIcon,
      flowNodes: [
        { label: "Model", value: total.toLocaleString(), color: "emerald" },
        { label: "Risk", value: (socKpis?.critical_count ?? 0).toLocaleString(), color: "amber" },
        { label: "Block", value: blocked.toLocaleString(), color: "orange" },
        { label: "Status", value: allowed.toLocaleString(), color: "emerald" },
      ],
      arrowTimings: timings3,
      tableColumns: ["Timestamp", "Model", "Action", "Risk", "Incident Status", "Assignee"],
      tableColumnKeys: ["timestamp", "model", "action", "severity", "incidentStatus", "assignee"],
    },
    "1.7": {
      title: "Generator-Level Output Guardrails",
      description: "Live output-stage guardrail actions for block/redact/monitor.",
      icon: FilterIcon,
      flowNodes: [
        { label: "Output", value: total.toLocaleString(), color: "blue" },
        { label: "Flagged", value: blocked.toLocaleString(), color: "amber" },
        { label: "Redacted", value: redacted.toLocaleString(), color: "orange" },
        { label: "Final", value: allowed.toLocaleString(), color: "emerald" },
      ],
      arrowTimings: timings3,
      tableColumns: ["Timestamp", "Threat", "Action", "Risk", "OWASP", "Model", "Source"],
      tableColumnKeys: ["timestamp", "category", "action", "severity", "owaspCode", "model", "source"],
    },
  };

  return base[moduleId] || base["1.1"];
}

function mapThreatFeedToTableData(moduleId, threatFeed) {
  return (threatFeed || []).map((ev) => {
    const metadata = ev.metadata || {};
    return {
      id: ev.id,
      timestamp: ev.timestamp
        ? new Date(ev.timestamp).toISOString().replace("T", " ").slice(0, 19)
        : "",
      category: ev.category || metadata.threat_category || "",
      subcategory: ev.subcategory || metadata.threat_subcategory || "",
      stage: metadata.pipeline_stage || metadata.stage || "",
      action: ev.action || "",
      severity: String(ev.severity ?? metadata.security_risk_score ?? ""),
      source: ev.source || metadata.source || "",
      model: metadata.model || "",
      owaspCode: metadata.owasp_code || "",
      collection: metadata.collection || metadata.vector_collection || "",
      namespace: metadata.namespace || metadata.vector_namespace || "",
      toolsInvoked: (ev.tools_invoked || metadata.tools_invoked || []).join(", "),
      dataAccessed: (ev.data_accessed || metadata.data_accessed || []).join(", "),
      requestedModel: metadata.requested_model || metadata.model || "",
      routedModel: metadata.routed_model || metadata.model || "",
      incidentStatus: ev.incident_status || "",
      assignee: ev.assignee || "",
      raw: ev,
      moduleId,
    };
  });
}

function formatCellValue(value) {
  if (value == null || value === "") return "--";
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
}
