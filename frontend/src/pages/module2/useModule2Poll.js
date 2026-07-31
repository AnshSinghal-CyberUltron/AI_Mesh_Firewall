import { useEffect, useRef } from "react";
import {
  CONTAINMENT_CHANGED_EVENT,
  CONTAINMENT_STORAGE_KEY,
} from "../../utils/containmentEvents";

/** Default Module 2 REST poll interval (always on — no WebSocket dependency). */
export const MODULE2_POLL_MS = 30_000;

/** Faster poll for Dashboard / MCP Risk / Model+RAG traffic lanes. */
export const MODULE2_HOT_POLL_MS = 5_000;

/** Dispatched by useModule2EnforcementNudge when an enforcement_event arrives. */
export const MODULE2_ENFORCEMENT_NUDGE_EVENT = "module2:enforcement-nudge";

const NUDGE_DEBOUNCE_MS = 300;

/**
 * Module-2-local polling helper. Always refreshes on an interval (default 30s)
 * plus containment/custom-event, cross-tab storage pings, and enforcement WS nudges.
 * Does not use shared WebSocket hooks directly (nudges come via custom event).
 */
export function useModule2Poll(onRefresh, { enabled = true, intervalMs = MODULE2_POLL_MS } = {}) {
  const nudgeTimerRef = useRef(null);

  useEffect(() => {
    if (!enabled || !onRefresh) return undefined;

    const runRefresh = () => {
      onRefresh();
    };

    const onContainmentEvent = () => runRefresh();
    const onStorage = (event) => {
      if (event.key === CONTAINMENT_STORAGE_KEY) runRefresh();
    };
    const onEnforcementNudge = () => {
      clearTimeout(nudgeTimerRef.current);
      nudgeTimerRef.current = setTimeout(runRefresh, NUDGE_DEBOUNCE_MS);
    };

    window.addEventListener(CONTAINMENT_CHANGED_EVENT, onContainmentEvent);
    window.addEventListener("storage", onStorage);
    window.addEventListener(MODULE2_ENFORCEMENT_NUDGE_EVENT, onEnforcementNudge);

    const id = intervalMs > 0 ? setInterval(runRefresh, intervalMs) : null;
    return () => {
      if (id) clearInterval(id);
      clearTimeout(nudgeTimerRef.current);
      window.removeEventListener(CONTAINMENT_CHANGED_EVENT, onContainmentEvent);
      window.removeEventListener("storage", onStorage);
      window.removeEventListener(MODULE2_ENFORCEMENT_NUDGE_EVENT, onEnforcementNudge);
    };
  }, [enabled, onRefresh, intervalMs]);
}
