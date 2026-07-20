import { useState, useEffect } from "react";

/**
 * Polls the backend `/api/health/` probe so status chrome (the header
 * "Connected" badge, the sidebar "System Status" widget, module "Operational"
 * badges) reflects REAL backend reachability instead of a hardcoded
 * "Connected"/"operational" that stays green even when the backend is down.
 *
 * Returns one of:
 *   "checking"     — initial probe in flight (before the first result)
 *   "connected"    — HTTP 2xx
 *   "degraded"     — reachable but slow/unhealthy: a probe TIMED OUT (backend
 *                    under load) or returned non-2xx, OR a single transient
 *                    connect blip. A soft/amber state — NOT a hard "offline".
 *   "disconnected" — SUSTAINED connection-level failure (refused/DNS) across
 *                    ≥ FAIL_THRESHOLD consecutive probes: the backend is truly
 *                    unreachable.
 *
 * Why the degraded tier + debounce: the previous version flipped straight to
 * "disconnected" on the FIRST failure of ANY kind, so a single 5s-timeout while
 * the 16 gunicorn workers were saturated (or one fresh-TCP blip — the dev proxy
 * runs with keep-alive off) painted the WHOLE app "Backend unreachable" even
 * though `/api/health/` was up and answering in ~2ms. A slow response now
 * degrades gracefully instead of screaming offline; only a real, repeated
 * connection failure escalates to the hard red state.
 */
const FAIL_THRESHOLD = 2; // consecutive connection failures before "disconnected"

export function useBackendHealth(intervalMs = 30000) {
  const [status, setStatus] = useState("checking");

  useEffect(() => {
    let cancelled = false;
    let failures = 0;

    const check = async () => {
      let outcome; // "ok" | "slow" | "down"
      try {
        const res = await fetch("/api/health/", { signal: AbortSignal.timeout(5000) });
        // Reachable but non-2xx = degraded (it answered), not unreachable.
        outcome = res.ok ? "ok" : "slow";
      } catch (err) {
        // AbortSignal.timeout() rejects with a TimeoutError → the backend is SLOW
        // (TCP was fine, it just didn't answer in time). A TypeError ("Failed to
        // fetch" — connection refused / DNS) means the backend is truly DOWN.
        const name = err && err.name;
        outcome = name === "TimeoutError" || name === "AbortError" ? "slow" : "down";
      }
      if (cancelled) return;

      if (outcome === "ok") {
        failures = 0;
        setStatus("connected");
        return;
      }

      failures += 1;
      // A single miss (a slow probe or one transient connect blip) must NOT paint
      // the app "Backend unreachable" — show soft "degraded" first. Escalate to a
      // hard "disconnected" only on a SUSTAINED connection-level failure; a backend
      // that merely responds slowly stays "degraded" (never a false "offline").
      if (outcome === "slow" || failures < FAIL_THRESHOLD) {
        setStatus("degraded");
      } else {
        setStatus("disconnected");
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
