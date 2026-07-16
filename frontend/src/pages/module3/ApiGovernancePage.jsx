import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { RefreshCw, Shield, Ban, Gauge } from "lucide-react";
import { useAuth } from "../../context/AuthContext";
import { clearModule3Cache, createModule3Api } from "../../api/module3";
import { PageHeader } from "../../components/module2/PageHeader";
import { KPIBar } from "../../components/module2/KPIBar";
import { DataTable } from "../../components/module2/DataTable";
import {
  Module2EmptyState,
  Module2ErrorState,
  Module2PageErrorBoundary,
  Module2PageSkeleton,
} from "../../components/module2/PageStates";
import { ContextualAppBar } from "../../components/module2/ContextualAppBar";
import { INFRA_BRIEF_TITLE, PAGE_BRIEFS } from "./pageCopy";

const REFRESH_DEBOUNCE_MS = 300;

function ApiGovernancePageInner() {
  const { fetchWithAuth } = useAuth();
  const api = useMemo(() => createModule3Api(fetchWithAuth), [fetchWithAuth]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [summary, setSummary] = useState(null);
  const [policies, setPolicies] = useState([]);
  const [events, setEvents] = useState([]);
  const refreshTimer = useRef(null);

  const load = useCallback(async () => {
    setError("");
    try {
      const [s, p, e] = await Promise.all([
        api.getApiGovernanceSummary({ useCache: false }),
        api.getApiGovernancePolicies(1, 50, { useCache: false }),
        api.getApiGovernanceEvents({ page: 1, page_size: 50 }, { useCache: false }),
      ]);
      setSummary(s);
      setPolicies(p.results || []);
      setEvents(e.results || []);
    } catch (err) {
      setError(err.message || "Failed to load API governance");
    } finally {
      setLoading(false);
    }
  }, [api]);

  useEffect(() => {
    clearModule3Cache();
    load();
    return () => {
      if (refreshTimer.current) clearTimeout(refreshTimer.current);
    };
  }, [load]);

  const onRefresh = () => {
    if (refreshTimer.current) clearTimeout(refreshTimer.current);
    refreshTimer.current = setTimeout(() => {
      setLoading(true);
      load();
    }, REFRESH_DEBOUNCE_MS);
  };

  const kpis = useMemo(
    () => [
      {
        key: "policies",
        label: "Quota policies",
        value: summary?.policy_count ?? "—",
        icon: Gauge,
      },
      {
        key: "enabled",
        label: "Enabled",
        value: summary?.enabled_policies ?? "—",
        icon: Shield,
      },
      {
        key: "denials",
        label: "Denials (24h)",
        value: summary?.denials_24h ?? "—",
        icon: Ban,
      },
      {
        key: "allows",
        label: "Allows (24h)",
        value: summary?.allows_24h ?? "—",
        icon: Shield,
      },
    ],
    [summary],
  );

  if (loading && !summary) {
    return <Module2PageSkeleton />;
  }

  if (error && !summary) {
    return <Module2ErrorState message={error} onRetry={load} />;
  }

  return (
    <div className="space-y-6">
      <ContextualAppBar title={INFRA_BRIEF_TITLE} description={PAGE_BRIEFS.apiGovernance} />
      <PageHeader
        title="API Governance"
        subtitle="OPA + Envoy ext_authz token quotas at the Kind network edge"
        actions={
          <button
            type="button"
            onClick={onRefresh}
            className="inline-flex items-center gap-1.5 rounded-lg border border-slate-200 bg-white px-3 py-1.5 text-xs font-medium text-slate-700 hover:bg-slate-50 dark:border-slate-600 dark:bg-slate-800 dark:text-slate-200"
          >
            <RefreshCw className="h-3.5 w-3.5" />
            Refresh
          </button>
        }
      />
      {error ? <p className="text-sm text-amber-700 dark:text-amber-300">{error}</p> : null}
      <KPIBar items={kpis} />

      <section className="space-y-3">
        <h2 className="text-sm font-semibold text-slate-800 dark:text-slate-100">Quota policies</h2>
        {!policies.length ? (
          <Module2EmptyState
            title="No quota policies"
            message="Seed demo tenants: python scripts/module3_phase3_seed_quotas.py — then ensure Kind state-sync is running (scripts/module3_kind_up.sh)."
            hint="Docs: docs/MODULE3_PHASE3_API_GOVERNANCE.md — also see /infrastructure/k8s-firewall."
          />
        ) : (
          <DataTable
            columns={[
              { key: "tenant_id", label: "Tenant" },
              { key: "environment", label: "Env" },
              { key: "tokens_per_minute", label: "TPM" },
              { key: "tokens_per_day", label: "TPD" },
              {
                key: "usage",
                label: "Used (min/day)",
                render: (row) => `${row.tokens_minute_used ?? 0} / ${row.tokens_day_used ?? 0}`,
              },
              {
                key: "enabled",
                label: "Status",
                render: (row) => (row.enabled ? "enabled" : "disabled"),
              },
            ]}
            rows={policies}
          />
        )}
      </section>

      <section className="space-y-3">
        <h2 className="text-sm font-semibold text-slate-800 dark:text-slate-100">
          Edge allow / deny events
        </h2>
        {!events.length ? (
          <Module2EmptyState
            title="No governance events yet"
            message="Probe llm-edge via Envoy: under-limit → 200, over-limit → 403 kill-switch (scripts/module3_phase3_e2e.py)."
          />
        ) : (
          <DataTable
            columns={[
              {
                key: "created_at",
                label: "When",
                render: (r) => new Date(r.created_at).toLocaleString(),
              },
              { key: "action", label: "Action" },
              { key: "tenant_id", label: "Tenant" },
              { key: "environment", label: "Env" },
              { key: "estimated_tokens", label: "Tokens" },
              { key: "path", label: "Path" },
              { key: "reason", label: "Reason" },
            ]}
            rows={events}
          />
        )}
      </section>
    </div>
  );
}

export function ApiGovernancePage() {
  return (
    <Module2PageErrorBoundary>
      <ApiGovernancePageInner />
    </Module2PageErrorBoundary>
  );
}
