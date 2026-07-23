/** Cross-module signal when new gateway telemetry is expected (simulator, live MCP, etc.). */

export const TELEMETRY_ACTIVITY_EVENT = "zeroshield:telemetry-activity";
export const TELEMETRY_STORAGE_KEY = "zeroshield_telemetry_ping";

export function notifyTelemetryActivity(source = "unknown", detail = {}) {
  if (typeof window === "undefined") return;
  window.dispatchEvent(
    new CustomEvent(TELEMETRY_ACTIVITY_EVENT, {
      detail: { source, at: Date.now(), ...detail },
    }),
  );
  try {
    localStorage.setItem(TELEMETRY_STORAGE_KEY, String(Date.now()));
  } catch {
    /* private mode / quota */
  }
}
