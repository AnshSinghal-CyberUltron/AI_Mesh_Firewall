import { useEffect } from "react";
import {
  CONTAINMENT_CHANGED_EVENT,
  CONTAINMENT_STORAGE_KEY,
} from "../utils/containmentEvents";

const POLL_MS = 30_000;

/**
 * Refreshes containment KPIs on interval, custom events, and cross-tab storage pings.
 * Pass `intervalMs: 0` (or any non-positive value) to keep the event listeners but skip
 * the interval — used when a live WebSocket feed already pushes updates.
 */
export function useContainmentPolling(onRefresh, { enabled = true, intervalMs = POLL_MS } = {}) {
  useEffect(() => {
    if (!enabled || !onRefresh) return undefined;

    const runRefresh = () => {
      onRefresh();
    };

    const onContainmentEvent = () => runRefresh();
    const onStorage = (event) => {
      if (event.key === CONTAINMENT_STORAGE_KEY) runRefresh();
    };

    window.addEventListener(CONTAINMENT_CHANGED_EVENT, onContainmentEvent);
    window.addEventListener("storage", onStorage);

    const id = intervalMs > 0 ? setInterval(runRefresh, intervalMs) : null;
    return () => {
      if (id) clearInterval(id);
      window.removeEventListener(CONTAINMENT_CHANGED_EVENT, onContainmentEvent);
      window.removeEventListener("storage", onStorage);
    };
  }, [enabled, onRefresh, intervalMs]);
}
