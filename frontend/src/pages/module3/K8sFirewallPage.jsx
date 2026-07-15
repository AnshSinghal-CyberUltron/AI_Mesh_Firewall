import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { RefreshCw, Network, Server, AlertTriangle, Shield } from "lucide-react";
import { useAuth } from "../../context/AuthContext";
import { clearModule3Cache, createModule3Api } from "../../api/module3";
import { PageHeader } from "../../components/module2/PageHeader";
import { KPIBar } from "../../components/module2/KPIBar";
import { PeriodSelector } from "../../components/module2/PeriodSelector";
import { DataTable } from "../../components/module2/DataTable";
import {
  Module2EmptyState,
  Module2ErrorState,
  Module2PageErrorBoundary,
  Module2PageSkeleton,
} from "../../components/module2/PageStates";
import { ContextualAppBar } from "../../components/module2/ContextualAppBar";
import { INFRA_BRIEF_TITLE, PAGE_BRIEFS } from "./pageCopy";
import {
  buildK8sFirewallKpis,
  embeddingStatusBadge,
  mtlsBadge,
} from "./pageData";

const PERIOD_LABELS = { "1h": "1 hour", "24h": "24 hours", "7d": "7 days", "30d": "30 days" };
const REFRESH_DEBOUNCE_MS = 300;

function TopologyGrid({ topology }) {
  const clusters = topology?.clusters || [];
  if (!clusters.length) {
    return (
      <Module2EmptyState
        title="No clusters registered"
        message="Bootstrap Kind Phase 2: bash scripts/module3_kind_up.sh (needs AGENT_API_KEY). Or run scripts/module3_ingest_agent_demo.sh."
        hint="Docs: docs/MODULE3_PHASE2_K8S.md — demo fallback: seed_module3 / Zero-Trust Simulator."
      />
    );
  }

  return (
    <div className="space-y-4">
      {clusters.map((cluster) => (
        <div key={cluster.id} className="rounded-xl border border-slate-200 bg-white p-4 dark:border-slate-700 dark:bg-slate-900/40">
          <div className="flex items-center justify-between mb-3">
            <div className="flex items-center gap-2">
              <Server className="h-4 w-4 text-violet-500" />
              <span className="font-semibold text-slate-800 dark:text-slate-100">{cluster.name}</span>
              <span className="text-xs text-slate-500">K8s {cluster.k8s_version}</span>
              {cluster.cilium_enabled && (
                <span className="rounded bg-blue-100 px-1.5 py-0.5 text-[10px] font-medium text-blue-800">Cilium</span>
              )}
            </div>
            <span className={`text-xs font-medium ${cluster.status === "healthy" ? "text-emerald-600" : "text-amber-600"}`}>
              {cluster.status}
            </span>
          </div>
          {cluster.namespaces?.map((ns) => (
            <div key={ns.name} className="mb-3 last:mb-0">
              <p className="text-xs font-medium text-slate-500 mb-2">namespace: {ns.name}</p>
              <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
                {ns.pods?.map((pod) => (
                  <div
                    key={pod.id}
                    className="rounded-lg border border-slate-100 bg-slate-50 px-3 py-2 dark:border-slate-700 dark:bg-slate-800/50"
                  >
                    <p className="text-xs font-medium text-slate-800 dark:text-slate-100 truncate">{pod.pod_name}</p>
                    <p className="text-[10px] text-slate-500">{pod.workload_type}</p>
                    <div className="mt-1 flex flex-wrap gap-1">
                      <span className={`text-[10px] rounded px-1 ${pod.sidecar_attached ? "bg-emerald-100 text-emerald-800" : "bg-red-100 text-red-800"}`}>
                        {pod.sidecar_attached ? "sidecar" : "no sidecar"}
                      </span>
                      <span className={`text-[10px] rounded px-1 ${mtlsBadge(pod.mtls_status)}`}>
                        mTLS {pod.mtls_status}
                      </span>
                    </div>
                  </div>
                ))}
              </div>
            </div>
          ))}
        </div>
      ))}
    </div>
  );
}

function K8sSimulator({ api, onSuccess }) {
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState("");
  const [err, setErr] = useState("");

  const inject = async (eventType) => {
    setBusy(true);
    setErr("");
    setMsg("");
    try {
      if (eventType === "network_drop") {
        await api.simulatorIngest({
          event_type: "network_drop",
          layer: "ebpf",
          source_ref: "compromised/sim-worker",
          dest_ref: "vector-db/chroma-0",
          reason: "Cilium: unauthorized sender label",
        });
        setMsg("Network drop event injected.");
      } else {
        await api.simulatorIngest({
          event_type: "embedding_poison",
          collection: "corp-docs",
          anomaly_score: 0.98,
          quarantine_reason: "Simulator: embedding poisoning detected",
        });
        setMsg("Poisoned embedding quarantined — check Module 2 incidents.");
      }
      onSuccess?.();
    } catch (e) {
      setErr(e.message || "Simulator failed");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="rounded-xl border border-dashed border-violet-300 bg-violet-50/50 p-4 dark:border-violet-800 dark:bg-violet-950/20">
      <h3 className="text-sm font-semibold text-slate-800 dark:text-slate-100 flex items-center gap-2">
        <AlertTriangle className="h-4 w-4 text-violet-600" />
        Zero-Trust Simulator (demo fallback)
      </h3>
      <p className="text-xs text-slate-600 dark:text-slate-400 mt-1">
        JWT demo injector when no Kind agent is running. Prefer{" "}
        <code className="text-[10px]">scripts/module3_kind_up.sh</code> or{" "}
        <code className="text-[10px]">scripts/module3_ingest_agent_demo.sh</code>. Drops and
        quarantines open Module 2 incidents.
      </p>
      <div className="mt-3 flex flex-wrap gap-2">
        <button
          type="button"
          disabled={busy}
          onClick={() => inject("network_drop")}
          className="rounded-lg bg-violet-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-violet-700 disabled:opacity-50"
        >
          Inject network drop
        </button>
        <button
          type="button"
          disabled={busy}
          onClick={() => inject("embedding_poison")}
          className="rounded-lg border border-red-300 bg-white px-3 py-1.5 text-xs font-medium text-red-700 hover:bg-red-50 disabled:opacity-50 dark:bg-slate-900"
        >
          Quarantine poisoned embedding
        </button>
      </div>
      {msg && <p className="mt-2 text-xs text-emerald-700 dark:text-emerald-400">{msg}</p>}
      {err && <p className="mt-2 text-xs text-red-600">{err}</p>}
    </div>
  );
}

export function K8sFirewallPage() {
  const { fetchWithAuth } = useAuth();
  const api = useMemo(() => createModule3Api(fetchWithAuth), [fetchWithAuth]);
  const [period, setPeriod] = useState("24h");
  const [layerFilter, setLayerFilter] = useState("");
  const [summary, setSummary] = useState(null);
  const [topology, setTopology] = useState(null);
  const [networkEvents, setNetworkEvents] = useState([]);
  const [embeddingQueue, setEmbeddingQueue] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const loadSeqRef = useRef(0);
  const refreshTimerRef = useRef(null);

  const load = useCallback(
    async ({ silent = false } = {}) => {
      const seq = ++loadSeqRef.current;
      if (!silent) setLoading(true);
      if (!silent) setError(null);
      try {
        clearModule3Cache();
        const netFilters = { page: 1, page_size: 50 };
        if (layerFilter) netFilters.layer = layerFilter;
        const [sum, topo, net, emb] = await Promise.all([
          api.getK8sFirewallSummary(period),
          api.getTopology(),
          api.getNetworkEvents(period, netFilters),
          api.getEmbeddingQueue(period, { page: 1, page_size: 50 }),
        ]);
        if (seq !== loadSeqRef.current) return;
        setSummary(sum);
        setTopology(topo);
        setNetworkEvents(net?.results || []);
        setEmbeddingQueue(emb?.results || []);
      } catch (e) {
        if (seq !== loadSeqRef.current) return;
        setError(e.message || "Failed to load K8s firewall data.");
      } finally {
        if (seq === loadSeqRef.current && !silent) setLoading(false);
      }
    },
    [api, period, layerFilter],
  );

  useEffect(() => {
    load();
  }, [load]);

  const scheduleRefresh = useCallback(() => {
    if (refreshTimerRef.current) clearTimeout(refreshTimerRef.current);
    refreshTimerRef.current = setTimeout(() => load({ silent: true }), REFRESH_DEBOUNCE_MS);
  }, [load]);

  const networkColumns = [
    { key: "created_at", label: "Time", render: (r) => new Date(r.created_at).toLocaleString() },
    { key: "layer", label: "Layer", render: (r) => (
      <span className="uppercase text-xs font-medium">{r.layer}</span>
    )},
    { key: "action", label: "Action", render: (r) => (
      <span className={r.action === "drop" ? "text-red-600 font-medium" : "text-emerald-600"}>{r.action}</span>
    )},
    { key: "source_ref", label: "Source", render: (r) => (
      <span className="font-mono text-xs">{r.source_ref}</span>
    )},
    { key: "dest_ref", label: "Destination", render: (r) => (
      <span className="font-mono text-xs">{r.dest_ref}</span>
    )},
    { key: "reason", label: "Reason", render: (r) => (
      <span className="text-xs text-slate-600">{r.reason}</span>
    )},
  ];

  const embeddingColumns = [
    { key: "collection", label: "Collection", render: (r) => r.collection || "—" },
    { key: "status", label: "Status", render: (r) => (
      <span className={`rounded px-2 py-0.5 text-xs font-medium ${embeddingStatusBadge(r.status)}`}>{r.status}</span>
    )},
    { key: "anomaly_score", label: "Anomaly", render: (r) => r.anomaly_score?.toFixed(2) },
    { key: "quarantine_reason", label: "Reason", render: (r) => (
      <span className="text-xs text-slate-600">{r.quarantine_reason || "—"}</span>
    )},
  ];

  if (loading && !summary) {
    return <Module2PageSkeleton />;
  }

  if (error && !summary) {
    return <Module2ErrorState message={error} onRetry={() => load()} />;
  }

  return (
    <Module2PageErrorBoundary title="K8s firewall view failed">
      <ContextualAppBar title={INFRA_BRIEF_TITLE} description={PAGE_BRIEFS.k8sFirewall} />

      <PageHeader
        title="M3.2 Kubernetes-Native AI Firewall"
        subtitle={`Zero-trust pod isolation and embedding inspection — last ${PERIOD_LABELS[period] || period}`}
        actions={
          <>
            <PeriodSelector value={period} onChange={setPeriod} />
            <select
              value={layerFilter}
              onChange={(e) => setLayerFilter(e.target.value)}
              className="rounded-lg border border-slate-200 px-2 py-1.5 text-xs dark:border-slate-600 dark:bg-slate-900"
            >
              <option value="">All layers</option>
              <option value="ebpf">eBPF</option>
              <option value="envoy">Envoy</option>
            </select>
            <button
              type="button"
              onClick={() => load()}
              className="inline-flex items-center gap-1 rounded-lg border border-slate-200 px-3 py-1.5 text-xs font-medium hover:bg-slate-50 dark:border-slate-600 dark:hover:bg-slate-800"
            >
              <RefreshCw className="h-3.5 w-3.5" />
              Refresh
            </button>
          </>
        }
      />

      <div className="mb-4 rounded-xl border border-violet-200 bg-violet-50/60 px-4 py-3 text-xs leading-relaxed text-violet-950 dark:border-violet-800/60 dark:bg-violet-950/20 dark:text-violet-100">
        Traffic approved by{" "}
        <Link to="/?tab=firewall-1-1" className="font-medium underline">Module 1 gateway</Link>
        {" "}is validated here via mTLS. Drops and quarantines feed{" "}
        <Link to="/incidents" className="font-medium underline">Module 2 SOC</Link>.
      </div>

      <KPIBar items={buildK8sFirewallKpis(summary)} />

      <div className="mt-6">
        <h3 className="text-sm font-semibold text-slate-800 dark:text-slate-100 mb-3 flex items-center gap-2">
          <Shield className="h-4 w-4 text-violet-500" />
          Cluster Topology
        </h3>
        <TopologyGrid topology={topology} />
      </div>

      <div className="mt-6 grid gap-6 lg:grid-cols-2">
        <div>
          <h3 className="text-sm font-semibold text-slate-800 dark:text-slate-100 mb-2">Network Events</h3>
          {networkEvents.length ? (
            <DataTable columns={networkColumns} rows={networkEvents} />
          ) : (
            <Module2EmptyState
              title="No network events"
              message="Agent ingest posts drops to /api/module3/ingest/network-event/ (or use the simulator)."
            />
          )}
        </div>
        <div>
          <h3 className="text-sm font-semibold text-slate-800 dark:text-slate-100 mb-2">Embedding Inspection Queue</h3>
          {embeddingQueue.length ? (
            <DataTable columns={embeddingColumns} rows={embeddingQueue} />
          ) : (
            <Module2EmptyState
              title="Queue empty"
              message="Post via /api/module3/ingest/embedding-inspection/ (status=quarantined exercises Module 2 SOC)."
            />
          )}
        </div>
      </div>

      <div className="mt-6">
        <K8sSimulator api={api} onSuccess={scheduleRefresh} />
      </div>
    </Module2PageErrorBoundary>
  );
}
