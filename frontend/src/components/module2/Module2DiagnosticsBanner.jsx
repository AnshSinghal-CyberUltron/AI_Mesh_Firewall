import { useEffect, useMemo, useState } from "react";
import { AlertTriangle } from "lucide-react";
import { useAuth } from "../../context/AuthContext";
import { createModule2Api } from "../../api/module2";

const devRealtimeEnabled =
  !import.meta.env.DEV || import.meta.env.VITE_ENABLE_REALTIME_NOTIFICATIONS === "true";

/** Operator-visible stale-data / realtime configuration warnings for Module 2 pages. */
export function Module2DiagnosticsBanner() {
  const { fetchWithAuth } = useAuth();
  const api = useMemo(() => createModule2Api(fetchWithAuth), [fetchWithAuth]);
  const [telemetryEnabled, setTelemetryEnabled] = useState(true);
  const [checked, setChecked] = useState(false);

  useEffect(() => {
    let cancelled = false;
    api.getDashboard("1h", { useCache: false })
      .then((res) => {
        if (cancelled) return;
        setTelemetryEnabled(res?.data_health?.telemetry_enabled !== false);
        setChecked(true);
      })
      .catch(() => {
        if (!cancelled) setChecked(true);
      });
    return () => {
      cancelled = true;
    };
  }, [api]);

  const wsWarning = import.meta.env.DEV && !devRealtimeEnabled;
  if (!wsWarning && (!checked || telemetryEnabled)) return null;

  return (
    <div className="mb-4 space-y-2" data-testid="module2-diagnostics-banner">
      {wsWarning && (
        <div
          role="status"
          className="rounded-lg border border-amber-300 bg-amber-50 px-4 py-2 text-sm text-amber-900 dark:border-amber-700 dark:bg-amber-900/20 dark:text-amber-100"
        >
          <AlertTriangle className="mr-2 inline h-4 w-4 shrink-0" aria-hidden />
          Dev realtime WebSocket is off. Set{" "}
          <code className="rounded bg-amber-100 px-1 dark:bg-amber-900/40">VITE_ENABLE_REALTIME_NOTIFICATIONS=true</code>{" "}
          in <code className="rounded bg-amber-100 px-1 dark:bg-amber-900/40">.env</code> for live Module 2 updates.
        </div>
      )}
      {checked && !telemetryEnabled && (
        <div
          role="status"
          className="rounded-lg border border-orange-300 bg-orange-50 px-4 py-2 text-sm text-orange-900 dark:border-orange-700 dark:bg-orange-900/20 dark:text-orange-100"
        >
          <AlertTriangle className="mr-2 inline h-4 w-4 shrink-0" aria-hidden />
          Gateway audit logging / telemetry is disabled for this organization. Module 2 dashboards may be stale or empty
          until telemetry is re-enabled in Module 1 firewall settings.
        </div>
      )}
    </div>
  );
}
