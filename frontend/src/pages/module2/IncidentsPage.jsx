import { useCallback, useEffect, useMemo, useState } from "react";
import { ChevronLeft, ChevronRight, RefreshCw, Search } from "lucide-react";
import { Link } from "react-router-dom";
import { useAuth } from "../../context/AuthContext";
import { createModule2Api } from "../../api/module2";
import { PageHeader } from "../../components/module2/PageHeader";
import { DataTable } from "../../components/module2/DataTable";
import { Module2ErrorState, Module2PageSkeleton } from "../../components/module2/PageStates";
import { ContextualAppBar } from "../../components/module2/ContextualAppBar";
import { sourceBadgeClass } from "./pageData";
import { ANALYST_BRIEF_TITLE, PAGE_BRIEFS } from "./pageCopy";

const SEVERITY_CLASS = {
  critical: "bg-red-100 text-red-700 dark:bg-red-900/40 dark:text-red-300",
  high: "bg-orange-100 text-orange-700 dark:bg-orange-900/40 dark:text-orange-300",
  medium: "bg-amber-100 text-amber-700 dark:bg-amber-900/40 dark:text-amber-300",
  low: "bg-slate-100 text-slate-600 dark:bg-slate-800 dark:text-slate-400",
};

export function IncidentsPage() {
  const { fetchWithAuth } = useAuth();
  const api = useMemo(() => createModule2Api(fetchWithAuth), [fetchWithAuth]);
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [statusFilter, setStatusFilter] = useState("");
  const [severityFilter, setSeverityFilter] = useState("");
  const [sourceFilter, setSourceFilter] = useState("");
  const [search, setSearch] = useState("");
  const [searchInput, setSearchInput] = useState("");
  const [page, setPage] = useState(1);
  const pageSize = 25;

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await api.listIncidents({
        status: statusFilter,
        severity: severityFilter,
        source: sourceFilter,
        search,
        page,
        page_size: pageSize,
      });
      setData(res);
    } catch (e) {
      setError(e.message || "Failed to load incidents.");
      setData(null);
    } finally {
      setLoading(false);
    }
  }, [api, statusFilter, severityFilter, sourceFilter, search, page]);

  useEffect(() => {
    load();
  }, [load]);

  useEffect(() => {
    setPage(1);
  }, [statusFilter, severityFilter, sourceFilter, search]);

  const handleSearchSubmit = (e) => {
    e.preventDefault();
    setSearch(searchInput.trim());
  };

  const incidents = data?.results || [];
  const totalPages = data?.total_pages || 1;

  return (
    <div>
      <ContextualAppBar title={ANALYST_BRIEF_TITLE} description={PAGE_BRIEFS.incidents} />
      <PageHeader
        title="Incident Queue"
        subtitle="Paginated security incidents with source attribution and enforcement context"
        actions={
          <button
            type="button"
            onClick={load}
            className="rounded-lg border border-slate-200 p-2 dark:border-slate-600"
            aria-label="Refresh"
          >
            <RefreshCw className="h-4 w-4" />
          </button>
        }
      />

      {error && !data && <Module2ErrorState message={error} onRetry={load} />}
      {loading && !data && !error && <Module2PageSkeleton rows={8} />}
      {(data || !loading) && (
      <>
      <div className="mb-4 flex flex-wrap items-center gap-3 rounded-xl border border-slate-200 bg-white p-4 shadow-sm dark:border-slate-700 dark:bg-slate-800/60">
        <form onSubmit={handleSearchSubmit} className="flex min-w-[220px] flex-1 items-center gap-2">
          <Search className="h-4 w-4 text-slate-400" />
          <input
            value={searchInput}
            onChange={(e) => setSearchInput(e.target.value)}
            placeholder="Search incidents..."
            className="w-full rounded-lg border px-3 py-1.5 text-sm dark:border-slate-600 dark:bg-slate-900"
          />
        </form>
        <select
          value={statusFilter}
          onChange={(e) => setStatusFilter(e.target.value)}
          className="rounded-lg border px-3 py-1.5 text-sm dark:border-slate-600 dark:bg-slate-800"
        >
          <option value="">All Statuses</option>
          <option value="open">Open</option>
          <option value="investigating">Investigating</option>
          <option value="escalated">Escalated</option>
          <option value="resolved">Resolved</option>
        </select>
        <select
          value={severityFilter}
          onChange={(e) => setSeverityFilter(e.target.value)}
          className="rounded-lg border px-3 py-1.5 text-sm dark:border-slate-600 dark:bg-slate-800"
        >
          <option value="">All Severities</option>
          <option value="critical">Critical</option>
          <option value="high">High</option>
          <option value="medium">Medium</option>
          <option value="low">Low</option>
        </select>
        <select
          value={sourceFilter}
          onChange={(e) => setSourceFilter(e.target.value)}
          className="rounded-lg border px-3 py-1.5 text-sm dark:border-slate-600 dark:bg-slate-800"
        >
          <option value="">All Sources</option>
          <option value="chat">Chat</option>
          <option value="rag">RAG</option>
          <option value="mcp">MCP</option>
          <option value="vector">Vector</option>
          <option value="ueba">UEBA</option>
          <option value="threat_intel">Threat Intel</option>
        </select>
      </div>

      <div className="rounded-xl border border-slate-200 bg-white p-4 shadow-sm dark:border-slate-700 dark:bg-slate-800/60">
        <DataTable
          columns={[
            {
              key: "id",
              label: "ID",
              render: (r) => (
                <Link to={`/incidents/${r.id}`} className="font-medium text-teal-600 hover:underline">
                  #{r.id}
                </Link>
              ),
            },
            { key: "title", label: "Title" },
            {
              key: "source",
              label: "Source",
              helpText: "Enforcement lane that raised the case: chat, rag, vector, mcp, threat_intel, or ueba.",
              render: (r) => (
                <span className={`rounded px-2 py-0.5 text-xs font-medium ${sourceBadgeClass(r.source)}`}>
                  {r.source || "generic"}
                </span>
              ),
            },
            {
              key: "severity",
              label: "Severity",
              helpText: "Business impact tier—critical and high should be worked before medium/low.",
              render: (r) => (
                <span className={`rounded px-2 py-0.5 text-xs font-medium ${SEVERITY_CLASS[r.severity] || SEVERITY_CLASS.low}`}>
                  {r.severity}
                </span>
              ),
            },
            { key: "status", label: "Status" },
            { key: "model", label: "Model", helpText: "LLM target involved in the triggering enforcement event.", render: (r) => r.model || "—" },
            { key: "key_prefix", label: "Key", helpText: "Truncated API key prefix for identity correlation.", render: (r) => r.key_prefix || "—" },
            { key: "created_at", label: "Created", render: (r) => new Date(r.created_at).toLocaleString() },
          ]}
          rows={incidents}
          emptyMessage="No incidents match the current filters."
        />

        <div className="mt-4 flex items-center justify-between border-t border-slate-100 pt-4 text-sm dark:border-slate-700">
          <p className="text-slate-500">
            Showing {incidents.length} of {data?.count ?? 0} incidents
          </p>
          <div className="flex items-center gap-2">
            <button
              type="button"
              disabled={page <= 1 || loading}
              onClick={() => setPage((p) => Math.max(1, p - 1))}
              className="rounded-lg border px-2 py-1 disabled:opacity-40 dark:border-slate-600"
            >
              <ChevronLeft className="h-4 w-4" />
            </button>
            <span>
              Page {page} / {totalPages}
            </span>
            <button
              type="button"
              disabled={page >= totalPages || loading}
              onClick={() => setPage((p) => p + 1)}
              className="rounded-lg border px-2 py-1 disabled:opacity-40 dark:border-slate-600"
            >
              <ChevronRight className="h-4 w-4" />
            </button>
          </div>
        </div>
      </div>
      </>
      )}
    </div>
  );
}
