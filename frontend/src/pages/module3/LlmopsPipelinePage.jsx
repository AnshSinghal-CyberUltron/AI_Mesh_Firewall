import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { RefreshCw, ShieldCheck, FileKey, ArrowRight } from "lucide-react";
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
  PIPELINE_STAGES,
  admissionResultBadge,
  buildLlmopsKpis,
  signatureStatusBadge,
} from "./pageData";

const PERIOD_LABELS = { "1h": "1 hour", "24h": "24 hours", "7d": "7 days", "30d": "30 days" };
const REFRESH_DEBOUNCE_MS = 300;

function PipelineStageDiagram() {
  return (
    <div className="rounded-xl border border-slate-200 bg-white p-4 dark:border-slate-700 dark:bg-slate-900/40">
      <h3 className="text-sm font-semibold text-slate-800 dark:text-slate-100 mb-4">Secure Factory Pipeline</h3>
      <div className="flex flex-wrap items-center gap-2">
        {PIPELINE_STAGES.map((stage, i) => (
          <div key={stage.id} className="flex items-center gap-2">
            <div className="rounded-lg border border-teal-200 bg-teal-50 px-3 py-2 text-center dark:border-teal-800 dark:bg-teal-950/30 min-w-[100px]">
              <p className="text-xs font-semibold text-teal-800 dark:text-teal-200">{stage.label}</p>
              <p className="text-[10px] text-teal-600 dark:text-teal-400 mt-0.5 leading-tight">{stage.description}</p>
            </div>
            {i < PIPELINE_STAGES.length - 1 && (
              <ArrowRight className="h-4 w-4 text-slate-400 shrink-0 hidden sm:block" />
            )}
          </div>
        ))}
      </div>
    </div>
  );
}

function LlmopsSimulator({ api, onSuccess }) {
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState("");
  const [err, setErr] = useState("");

  const runVerify = async (valid) => {
    setBusy(true);
    setErr("");
    setMsg("");
    try {
      const digest = valid ? "sha256:sim-valid-signature" : "invalid:missing-cosign";
      const imageRef = `registry.zeroshield.ai/sim/test-model:${Date.now()}`;
      const res = await api.verifyArtifact({
        image_ref: imageRef,
        signature_digest: digest,
        data_sha256: "a".repeat(64),
        model_sha256: "b".repeat(64),
      });
      setMsg(
        res?.decision?.result === "allow"
          ? "Admission allowed — artifact verified."
          : `Admission denied: ${res?.decision?.reason || "signature check failed"}`,
      );
      onSuccess?.();
    } catch (e) {
      setErr(e.message || "Simulator failed");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="rounded-xl border border-dashed border-teal-300 bg-teal-50/50 p-4 dark:border-teal-800 dark:bg-teal-950/20">
      <h3 className="text-sm font-semibold text-slate-800 dark:text-slate-100 flex items-center gap-2">
        <ShieldCheck className="h-4 w-4 text-teal-600" />
        Admission Simulator
      </h3>
      <p className="text-xs text-slate-600 dark:text-slate-400 mt-1">
        Register a test artifact and run the real gateway admission check.
      </p>
      <div className="mt-3 flex flex-wrap gap-2">
        <button
          type="button"
          disabled={busy}
          onClick={() => runVerify(true)}
          className="rounded-lg bg-teal-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-teal-700 disabled:opacity-50"
        >
          Verify valid signature
        </button>
        <button
          type="button"
          disabled={busy}
          onClick={() => runVerify(false)}
          className="rounded-lg border border-red-300 bg-white px-3 py-1.5 text-xs font-medium text-red-700 hover:bg-red-50 disabled:opacity-50 dark:bg-slate-900"
        >
          Simulate denied deploy
        </button>
      </div>
      {msg && <p className="mt-2 text-xs text-emerald-700 dark:text-emerald-400">{msg}</p>}
      {err && <p className="mt-2 text-xs text-red-600">{err}</p>}
    </div>
  );
}

export function LlmopsPipelinePage() {
  const { fetchWithAuth } = useAuth();
  const api = useMemo(() => createModule3Api(fetchWithAuth), [fetchWithAuth]);
  const [period, setPeriod] = useState("24h");
  const [summary, setSummary] = useState(null);
  const [artifacts, setArtifacts] = useState([]);
  const [admissionLog, setAdmissionLog] = useState([]);
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
        const [sum, arts, log] = await Promise.all([
          api.getLlmopsSummary(period),
          api.getArtifacts(1, 50),
          api.getAdmissionLog(period, 1, 50),
        ]);
        if (seq !== loadSeqRef.current) return;
        setSummary(sum);
        setArtifacts(arts?.results || []);
        setAdmissionLog(log?.results || []);
      } catch (e) {
        if (seq !== loadSeqRef.current) return;
        setError(e.message || "Failed to load LLMOps data.");
      } finally {
        if (seq === loadSeqRef.current && !silent) setLoading(false);
      }
    },
    [api, period],
  );

  useEffect(() => {
    load();
  }, [load]);

  const scheduleRefresh = useCallback(() => {
    if (refreshTimerRef.current) clearTimeout(refreshTimerRef.current);
    refreshTimerRef.current = setTimeout(() => load({ silent: true }), REFRESH_DEBOUNCE_MS);
  }, [load]);

  const artifactColumns = [
    { key: "name", label: "Artifact", render: (r) => (
      <span className="font-medium">{r.name}:{r.version}</span>
    )},
    { key: "image_ref", label: "Image", render: (r) => (
      <span className="font-mono text-xs truncate max-w-[200px] block">{r.image_ref}</span>
    )},
    { key: "data_sha256", label: "Data SHA-256", render: (r) => (
      <span className="font-mono text-[10px]">{r.data_sha256?.slice(0, 12)}…</span>
    )},
    { key: "signature_status", label: "Signature", render: (r) => (
      <span className={`rounded px-2 py-0.5 text-xs font-medium ${signatureStatusBadge(r.signature_status)}`}>
        {r.signature_status}
      </span>
    )},
  ];

  const admissionColumns = [
    { key: "created_at", label: "Time", render: (r) => new Date(r.created_at).toLocaleString() },
    { key: "artifact_name", label: "Artifact", render: (r) => `${r.artifact_name}:${r.artifact_version}` },
    { key: "result", label: "Result", render: (r) => (
      <span className={`rounded px-2 py-0.5 text-xs font-medium uppercase ${admissionResultBadge(r.result)}`}>
        {r.result}
      </span>
    )},
    { key: "reason", label: "Reason", render: (r) => (
      <span className="text-xs text-slate-600 dark:text-slate-400">{r.reason}</span>
    )},
    { key: "latency_ms", label: "Latency", render: (r) => `${r.latency_ms} ms` },
  ];

  if (loading && !summary) {
    return <Module2PageSkeleton />;
  }

  if (error && !summary) {
    return <Module2ErrorState message={error} onRetry={() => load()} />;
  }

  return (
    <Module2PageErrorBoundary title="LLMOps pipeline view failed">
      <ContextualAppBar title={INFRA_BRIEF_TITLE} description={PAGE_BRIEFS.llmops} />

      <PageHeader
        title="M3.1 LLMOps Pipeline Security"
        subtitle={`Supply chain integrity and admission control — last ${PERIOD_LABELS[period] || period}`}
        actions={
          <>
            <PeriodSelector value={period} onChange={setPeriod} />
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

      <div className="mb-4 rounded-xl border border-teal-200 bg-teal-50/60 px-4 py-3 text-xs leading-relaxed text-teal-950 dark:border-teal-800/60 dark:bg-teal-950/20 dark:text-teal-100">
        <strong>Secure factory:</strong> DVC SHA-256 fingerprints → Docker build → Cosign sign → FastAPI admission gatekeeper.
        Denied deployments create incidents in{" "}
        <Link to="/incidents?source=module3" className="font-medium underline">Module 2 SOC</Link>.
      </div>

      <KPIBar items={buildLlmopsKpis(summary)} />

      <div className="mt-6">
        <PipelineStageDiagram />
      </div>

      <div className="mt-6 grid gap-6 lg:grid-cols-2">
        <div>
          <h3 className="text-sm font-semibold text-slate-800 dark:text-slate-100 mb-2 flex items-center gap-2">
            <FileKey className="h-4 w-4" />
            Artifact Registry
          </h3>
          {artifacts.length ? (
            <DataTable columns={artifactColumns} rows={artifacts} />
          ) : (
            <Module2EmptyState
              title="No artifacts registered"
              message="Run seed_module3 or register artifacts via CI/CD."
              hint="Use the simulator below to register a test artifact."
            />
          )}
        </div>
        <div>
          <h3 className="text-sm font-semibold text-slate-800 dark:text-slate-100 mb-2">Admission Log</h3>
          {admissionLog.length ? (
            <DataTable columns={admissionColumns} rows={admissionLog} />
          ) : (
            <Module2EmptyState title="No admission decisions yet" message="Run verify on an artifact to populate the log." />
          )}
        </div>
      </div>

      <div className="mt-6">
        <LlmopsSimulator api={api} onSuccess={scheduleRefresh} />
      </div>
    </Module2PageErrorBoundary>
  );
}
