import { useState, useEffect, useCallback, useRef } from "react";
import {
  Server, Database, Cpu, Wifi, RefreshCw, Radio,
  CheckCircle2, XCircle, AlertCircle, HelpCircle, Loader2, Activity,
} from "lucide-react";
import { useGatewayConfig } from "../hooks/useGatewayConfig";
import { resolveGatewayHealthUrl } from "../utils/environmentUrls";
import { startVisibleInterval } from "../utils/visiblePoll.js";

const CHECK_INTERVAL = 30_000; // 30 seconds

const STATUS_CONFIG = {
  ok:      { label: "Healthy", className: "bg-emerald-500/15 text-emerald-400 border-emerald-500/30", icon: CheckCircle2 },
  error:   { label: "Error",   className: "bg-red-500/15 text-red-400 border-red-500/30",             icon: XCircle },
  warning: { label: "Warning", className: "bg-amber-500/15 text-amber-400 border-amber-500/30",        icon: AlertCircle },
  unknown: { label: "Unknown", className: "bg-slate-500/15 text-slate-400 border-slate-500/30",        icon: HelpCircle },
};

function ServiceCard({ name, icon: Icon, status, details = [], error = null, checking = false }) {
  const cfg = STATUS_CONFIG[status] ?? STATUS_CONFIG.unknown;
  const StatusIcon = cfg.icon;

  return (
    <div
      className={`rounded-[22px] border p-4 transition-all ${
        status === "error"
          ? "bg-red-50/90 dark:bg-red-950/20 border-red-300 dark:border-red-700/60"
          : "bg-white/80 dark:bg-slate-900/45 border-slate-200 dark:border-slate-700"
      }`}
    >
      {/* Header */}
      <div className="flex items-center justify-between mb-2">
        <div className="flex items-center gap-2">
          <div
            className={`w-8 h-8 rounded-lg flex items-center justify-center shrink-0 ${
              status === "ok"
                ? "bg-emerald-100 dark:bg-emerald-900/30"
                : status === "error"
                ? "bg-red-100 dark:bg-red-900/30"
                : "bg-slate-100 dark:bg-slate-700"
            }`}
          >
            <Icon
              className={`w-4 h-4 ${
                status === "ok"
                  ? "text-emerald-600 dark:text-emerald-400"
                  : status === "error"
                  ? "text-red-500 dark:text-red-400"
                  : "text-slate-500 dark:text-slate-400"
              }`}
            />
          </div>
          <span className="text-sm font-semibold text-slate-900 dark:text-slate-100 truncate">{name}</span>
        </div>
        <span
          className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[10px] font-bold uppercase border shrink-0 ml-1 ${cfg.className}`}
        >
          {checking ? (
            <Loader2 className="w-3 h-3 animate-spin" />
          ) : (
            <StatusIcon className="w-3 h-3" />
          )}
          {checking ? "Checking" : cfg.label}
        </span>
      </div>

      {/* Details */}
      {details.length > 0 && (
        <div className="mt-2 space-y-1">
          {details.map(({ label, value }) => (
            <div key={label} className="flex justify-between text-[11px]">
              <span className="text-slate-500 dark:text-slate-400">{label}</span>
              <span className="text-slate-700 dark:text-slate-300 font-mono font-medium truncate max-w-[160px]">{value}</span>
            </div>
          ))}
        </div>
      )}

      {/* Error block */}
      {error && (
        <div className="mt-2 bg-red-950/10 border border-red-500/20 rounded-md px-2.5 py-2">
          <p className="text-[10px] font-mono text-red-400 dark:text-red-400 break-all leading-relaxed">{error}</p>
        </div>
      )}
    </div>
  );
}

export function ServiceStatusPanel() {
  const { gatewayUrl } = useGatewayConfig();
  const [services, setServices] = useState(null); // null = initial load
  const [checking, setChecking] = useState(false);
  const [lastChecked, setLastChecked] = useState(null);
  const [ticker, setTicker] = useState(0); // forces "X sec ago" to update
  const intervalRef = useRef(null);
  const tickerRef = useRef(null);
  const mountedRef = useRef(true);

  const runCheck = useCallback(async () => {
    if (!mountedRef.current) return;
    setChecking(true);
    const t0 = Date.now();

    let backendStatus = "unknown", backendError = null, backendLatency = null;
    let svcData = null;
    let gatewayHealth = null, gatewayStatus = "unknown", gatewayError = null, gatewayLatency = null;

    await Promise.allSettled([
      // Backend liveness
      fetch("/api/health/", { signal: AbortSignal.timeout(5000) })
        .then((res) => {
          backendLatency = Date.now() - t0;
          if (res.ok) {
            backendStatus = "ok";
          } else {
            backendStatus = "error";
            backendError = `HTTP ${res.status} ${res.statusText}`;
          }
        })
        .catch((err) => {
          backendStatus = "error";
          backendError = err.message;
        }),

      // Internal service probes (DB / Redis / Celery / RabbitMQ)
      fetch("/api/health/services/", { signal: AbortSignal.timeout(8000) })
        .then(async (res) => {
          if (res.ok) {
            svcData = await res.json();
          } else {
            const msg = `HTTP ${res.status} ${res.statusText}`;
            svcData = {
              database: { status: "error", error: msg },
              redis:    { status: "error", error: msg },
              celery:   { status: "error", workers: [], error: msg },
              rabbitmq: { status: "error", error: msg },
            };
          }
        })
        .catch((err) => {
          const msg = err.message;
          svcData = {
            database: { status: "error", error: msg },
            redis:    { status: "error", error: msg },
            celery:   { status: "error", workers: [], error: msg },
            rabbitmq: { status: "error", error: msg },
          };
        }),

      fetch(resolveGatewayHealthUrl(gatewayUrl), { signal: AbortSignal.timeout(5000) })
        .then(async (res) => {
          gatewayLatency = Date.now() - t0;
          if (res.ok) {
            gatewayHealth = await res.json();
            gatewayStatus = "ok";
          } else {
            gatewayStatus = "error";
            gatewayError = `HTTP ${res.status} ${res.statusText}`;
          }
        })
        .catch((err) => {
          gatewayStatus = "error";
          gatewayError = err.message;
        }),
    ]);

    if (!mountedRef.current) return;

    setServices({
      backend:  { status: backendStatus,  error: backendError,  latency: backendLatency },
      gateway:  { status: gatewayStatus,  error: gatewayError,  latency: gatewayLatency, data: gatewayHealth },
      database: svcData?.database ?? { status: "unknown", error: null },
      redis:    svcData?.redis    ?? { status: "unknown", error: null },
      celery:   svcData?.celery   ?? { status: "unknown", workers: [], error: null },
      rabbitmq: svcData?.rabbitmq ?? { status: "unknown", error: null },
    });
    setLastChecked(Date.now());
    setChecking(false);
  }, [gatewayUrl]);

  // Poll every 30 s
  useEffect(() => {
    mountedRef.current = true;
    runCheck();
    intervalRef.current = startVisibleInterval(runCheck, CHECK_INTERVAL);
    return () => {
      mountedRef.current = false;
      if (typeof intervalRef.current === "function") intervalRef.current();
    };
  }, [runCheck]);

  // Tick "last checked X sec ago" every second
  useEffect(() => {
    tickerRef.current = setInterval(() => setTicker((n) => n + 1), 1000);
    return () => clearInterval(tickerRef.current);
  }, []);

  const secondsSince = lastChecked ? Math.round((Date.now() - lastChecked) / 1000) : null;

  // ─── Initial skeleton ─────────────────────────────────────────────────────
  if (!services) {
    return (
      <div className="ai-mesh-card rounded-[28px] p-5">
        <div className="flex items-center justify-between mb-4">
          <h2 className="flex items-center gap-2 text-sm font-bold text-slate-900 dark:text-slate-100">
            <Activity className="w-4 h-4 text-teal-500" />
            Infrastructure Health
          </h2>
          <Loader2 className="w-4 h-4 text-slate-400 animate-spin" />
        </div>
        <div className="grid grid-cols-2 md:grid-cols-3 gap-3">
          {Array.from({ length: 6 }).map((_, i) => (
            <div key={i} className="h-20 bg-slate-100 dark:bg-slate-700 rounded-xl animate-pulse" />
          ))}
        </div>
      </div>
    );
  }

  // ─── Build detail rows for each card ─────────────────────────────────────
  const backendDetails = services.backend.latency != null
    ? [{ label: "Latency", value: `${services.backend.latency} ms` }]
    : [];

  const gatewayDetails = (() => {
    const d = services.gateway.data;
    if (!d) return [];
    return [
      { label: "Mode",      value: d.enforcement_mode ?? "—" },
      { label: "Firewall",  value: d.firewall_enabled ? "Enabled" : "Disabled" },
      { label: "Policies",  value: String(d.policy_count ?? "—") },
      ...(d.agent_id ? [{ label: "Agent", value: d.agent_id.slice(0, 8) + "…" }] : []),
      ...(services.gateway.latency != null ? [{ label: "Latency", value: `${services.gateway.latency} ms` }] : []),
    ];
  })();

  const celeryWorkers = services.celery?.workers ?? [];
  const celeryDetails = celeryWorkers.length > 0
    ? [{ label: "Workers", value: celeryWorkers.map((w) => w.replace(/^celery@/, "")).join(", ") }]
    : [];

  // ─── Render ───────────────────────────────────────────────────────────────
  return (
    <div className="ai-mesh-card rounded-[28px] p-5">
      {/* Panel header */}
      <div className="flex items-center justify-between mb-4">
        <div>
          <h2 className="flex items-center gap-2 text-sm font-bold text-slate-900 dark:text-slate-100">
            <Activity className="w-4 h-4 text-teal-500" />
            Infrastructure Health
          </h2>
          <p className="mt-1 text-xs text-slate-500 dark:text-slate-400">Backend, gateway, and messaging dependencies at a glance.</p>
        </div>
        <div className="flex items-center gap-3">
          {secondsSince != null && (
            <span className="text-[11px] text-slate-400">
              Last checked {secondsSince}s ago
            </span>
          )}
          <button
            onClick={runCheck}
            disabled={checking}
            className="flex items-center gap-1 rounded-xl border border-slate-200 bg-white/80 px-3 py-1.5 text-[11px] font-medium text-slate-500 transition-colors hover:bg-white dark:border-slate-700 dark:bg-slate-900/70 dark:hover:bg-slate-900 disabled:opacity-40"
          >
            <RefreshCw className={`w-3 h-3 ${checking ? "animate-spin" : ""}`} />
            Check Now
          </button>
        </div>
      </div>

      {/* Service cards grid */}
      <div className="grid grid-cols-2 md:grid-cols-3 gap-3">
        <ServiceCard
          name="Backend (API)"
          icon={Server}
          status={services.backend.status}
          details={backendDetails}
          error={services.backend.error}
          checking={checking}
        />
        <ServiceCard
          name="Gateway"
          icon={Wifi}
          status={services.gateway.status}
          details={gatewayDetails}
          error={services.gateway.error}
          checking={checking}
        />
        <ServiceCard
          name="Database"
          icon={Database}
          status={services.database.status}
          error={services.database.error}
          checking={checking}
        />
        <ServiceCard
          name="Redis"
          icon={Cpu}
          status={services.redis.status}
          error={services.redis.error}
          checking={checking}
        />
        <ServiceCard
          name="Celery Workers"
          icon={Activity}
          status={services.celery.status}
          details={celeryDetails}
          error={services.celery.error}
          checking={checking}
        />
        <ServiceCard
          name="RabbitMQ"
          icon={Radio}
          status={services.rabbitmq.status}
          error={services.rabbitmq.error}
          checking={checking}
        />
      </div>
    </div>
  );
}
