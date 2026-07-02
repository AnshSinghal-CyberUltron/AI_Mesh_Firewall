import { useState, useEffect } from "react";

/**
 * Polls the backend `/api/health/` probe so status chrome (the header
 * "Connected" badge, the sidebar "System Status" widget, module "Operational"
 * badges) reflects REAL backend reachability instead of a hardcoded
 * "Connected"/"operational" that stays green even when the backend is down.
 *
 * Returns one of: "checking" (initial probe in flight) | "connected" (HTTP 2xx)
 * | "disconnected" (non-2xx, network error, or timeout).
 */
export function useBackendHealth(intervalMs = 30000) {
  const [status, setStatus] = useState("checking");

  useEffect(() => {
    let cancelled = false;
    const check = async () => {
      try {
        const res = await fetch("/api/health/", { signal: AbortSignal.timeout(5000) });
        if (!cancelled) setStatus(res.ok ? "connected" : "disconnected");
      } catch {
        if (!cancelled) setStatus("disconnected");
      }
    };
    check();
    const timer = setInterval(check, intervalMs);
    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, [intervalMs]);

  return status;
}
