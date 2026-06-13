import { useEffect, useState } from "react";
import { Loader2, ArrowLeft, Lock, Search, ArrowDownToLine, Cpu, ShieldOff, CheckCircle2 } from "lucide-react";
import { Link, useParams } from "react-router-dom";
import { useAuth } from "../../context/AuthContext";
import { createModule2Api } from "../../api/module2";
import { PageHeader } from "../../components/module2/PageHeader";
import { ChartCard } from "../../components/module2/ChartCard";
import { ContextualAppBar } from "../../components/module2/ContextualAppBar";
import { ANALYST_BRIEF_TITLE, PAGE_BRIEFS } from "./pageCopy";

const STAGE_ICONS = {
  ingress: { Icon: Lock, color: "text-sky-500", bg: "bg-sky-100 dark:bg-sky-900/30" },
  query: { Icon: Search, color: "text-violet-500", bg: "bg-violet-100 dark:bg-violet-900/30" },
  retriever: { Icon: ArrowDownToLine, color: "text-amber-500", bg: "bg-amber-100 dark:bg-amber-900/30" },
  ranker: { Icon: Cpu, color: "text-emerald-500", bg: "bg-emerald-100 dark:bg-emerald-900/30" },
  generator: { Icon: Cpu, color: "text-teal-500", bg: "bg-teal-100 dark:bg-teal-900/30" },
  enforcement: { Icon: ShieldOff, color: "text-red-500", bg: "bg-red-100 dark:bg-red-900/30" },
  completed: { Icon: CheckCircle2, color: "text-green-500", bg: "bg-green-100 dark:bg-green-900/30" },
};

function ChainOfCustody({ timeline }) {
  const stageEvents = (timeline || []).filter((ev) => ev.metadata?.pipeline_stage || ev.metadata?.event_type === "rag_pipeline");
  if (stageEvents.length === 0) return null;

  return (
    <ChartCard
      title="Chain-of-Custody — Pipeline Execution Trace"
      titleHelpText="Step-by-step RAG/MCP pipeline actions for this case—shows where ingress, retrieval, or generation policy fired."
    >
      <ol className="relative border-l border-slate-200 dark:border-slate-700 ml-3 mt-2 space-y-4">
        {stageEvents.map((ev, i) => {
          const stage = (ev.metadata?.pipeline_stage || ev.metadata?.event_type || "ingress").toLowerCase();
          const meta = STAGE_ICONS[stage] || STAGE_ICONS.ingress;
          const Icon = meta.Icon;
          const action = ev.action || "allow";
          const isBlock = action === "block";
          return (
            <li key={i} className="ml-4">
              <span className={`absolute -left-3 flex h-6 w-6 items-center justify-center rounded-full ${meta.bg}`}>
                <Icon className={`h-3.5 w-3.5 ${meta.color}`} />
              </span>
              <div className="rounded-lg border border-slate-100 bg-slate-50 px-3 py-2 dark:border-slate-700 dark:bg-slate-800/50">
                <div className="flex items-center justify-between">
                  <p className="text-xs font-semibold capitalize text-slate-700 dark:text-slate-200">
                    {stage === "ingress" ? "Ingress" : `Pipeline Step [${stage}]`}
                  </p>
                  <span className={`text-xs font-medium ${isBlock ? "text-red-500" : "text-emerald-600"}`}>
                    {isBlock ? "Blocked" : action}
                  </span>
                </div>
                {ev.metadata?.detail && (
                  <p className="mt-0.5 text-xs text-slate-500">{ev.metadata.detail}</p>
                )}
                {ev.metadata?.collection && (
                  <p className="mt-0.5 text-xs text-slate-400">Collection: <code>{ev.metadata.collection}</code></p>
                )}
                {ev.metadata?.model && (
                  <p className="mt-0.5 text-xs text-slate-400">Model: <code>{ev.metadata.model}</code></p>
                )}
                {ev.metadata?.key_prefix && (
                  <p className="mt-0.5 text-xs text-slate-400">Key: <code>{ev.metadata.key_prefix}</code></p>
                )}
              </div>
            </li>
          );
        })}
      </ol>
    </ChartCard>
  );
}

export function IncidentDetailPage() {
  const { id } = useParams();
  const { fetchWithAuth } = useAuth();
  const api = createModule2Api(fetchWithAuth);
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [selectedEvent, setSelectedEvent] = useState(null);

  useEffect(() => {
    (async () => {
      setLoading(true);
      try {
        setData(await api.getIncident(id));
      } finally {
        setLoading(false);
      }
    })();
  }, [id]);

  const escalate = async () => {
    await fetchWithAuth(`/api/security/incidents/${id}/escalate-incident/`, { method: "POST", body: JSON.stringify({}) });
    setData(await api.getIncident(id));
  };

  const resolve = async () => {
    await fetchWithAuth(`/api/security/incidents/${id}/resolve-incident/`, { method: "POST", body: JSON.stringify({}) });
    setData(await api.getIncident(id));
  };

  if (loading) {
    return (
      <div>
        <ContextualAppBar title={ANALYST_BRIEF_TITLE} description={PAGE_BRIEFS.incidentDetail} />
        <div className="flex justify-center py-20">
          <Loader2 className="h-8 w-8 animate-spin text-teal-500" />
        </div>
      </div>
    );
  }
  if (!data) {
    return (
      <div>
        <ContextualAppBar title={ANALYST_BRIEF_TITLE} description={PAGE_BRIEFS.incidentDetail} />
        <p className="text-sm text-slate-500">Incident not found</p>
      </div>
    );
  }

  const incident = data.incident;

  return (
    <div>
      <Link to="/incidents" className="mb-4 inline-flex items-center gap-1 text-sm text-teal-600 hover:underline">
        <ArrowLeft className="h-4 w-4" /> Back to Queue
      </Link>
      <ContextualAppBar title={ANALYST_BRIEF_TITLE} description={PAGE_BRIEFS.incidentDetail} />
      <PageHeader
        title={incident.title}
        subtitle={`Severity: ${incident.severity} · Status: ${incident.status} · Source: ${data.source || "generic"}`}
        actions={
          <>
            {incident.status !== "escalated" && (
              <button onClick={escalate} className="rounded-lg border border-amber-300 px-3 py-1.5 text-sm text-amber-700">Escalate</button>
            )}
            {incident.status !== "resolved" && (
              <button onClick={resolve} className="rounded-lg bg-teal-600 px-3 py-1.5 text-sm text-white">Resolve</button>
            )}
          </>
        }
      />

      <ChainOfCustody timeline={data.timeline} />

      <div className="mt-4 grid gap-4 lg:grid-cols-2">
        <ChartCard
          title="Incident Timeline"
          titleHelpText="Ordered enforcement events—select one to load evidence in the viewer panel."
        >
          <div className="max-h-96 space-y-2 overflow-y-auto">
            {(data.timeline || []).map((ev) => (
              <button
                key={ev.id}
                onClick={() => setSelectedEvent(ev)}
                className="w-full rounded-lg border border-slate-100 p-3 text-left text-sm hover:bg-slate-50 dark:border-slate-700 dark:hover:bg-slate-700/40"
              >
                <div className="flex justify-between">
                  <span className="font-medium">{ev.action}</span>
                  <span className="text-xs text-slate-400">{new Date(ev.created_at).toLocaleString()}</span>
                </div>
                <p className="mt-1 text-xs text-slate-400">
                  {ev.source} {ev.key_prefix ? `· key ${ev.key_prefix}` : ""} {ev.model ? `· ${ev.model}` : ""}
                </p>
              </button>
            ))}
          </div>
        </ChartCard>

        <ChartCard
          title="Evidence Viewer"
          titleHelpText="Policy, rule, key, model, and prompt/response snippets for the selected event."
        >
          {selectedEvent ? (
            <div className="space-y-2 text-sm">
              <p><strong>Source:</strong> {selectedEvent.source || data.source || "generic"}</p>
              <p><strong>Action:</strong> {selectedEvent.action}</p>
              <p><strong>Policy:</strong> {selectedEvent.policy_id || "—"}</p>
              <p><strong>Rule:</strong> {selectedEvent.rule_id || "—"}</p>
              <p><strong>API Key Prefix:</strong> {selectedEvent.key_prefix || data?.evidence?.key_prefix || "—"}</p>
              <p><strong>Project:</strong> {selectedEvent.metadata?.project_id || data?.evidence?.project_id || "—"}</p>
              <p><strong>Model:</strong> {selectedEvent.model || data?.evidence?.model || "—"}</p>
              <p><strong>Threat Type:</strong> {selectedEvent.metadata?.threat_type || data?.evidence?.threat_type || "—"}</p>
              {selectedEvent.metadata?.prompt_snippet && (
                <div className="rounded bg-slate-900 p-3 text-xs text-green-400">
                  {selectedEvent.metadata.prompt_snippet}
                </div>
              )}
              {selectedEvent.metadata?.response_snippet && (
                <div className="rounded bg-slate-900 p-3 text-xs text-blue-400">
                  {selectedEvent.metadata.response_snippet}
                </div>
              )}
            </div>
          ) : (
            <p className="py-8 text-center text-sm text-slate-400">Select a timeline event to view evidence</p>
          )}
        </ChartCard>
      </div>
    </div>
  );
}
