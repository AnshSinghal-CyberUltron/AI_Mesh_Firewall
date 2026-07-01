import { useEffect } from "react";
import {
  CONTAINMENT_CHANGED_EVENT,
  CONTAINMENT_STORAGE_KEY,
} from "../utils/containmentEvents";

const POLL_MS = 30_000;

/** Refreshes containment KPIs on interval, custom events, and cross-tab storage pings. */
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

    const id = setInterval(runRefresh, intervalMs);
    return () => {
      clearInterval(id);
      window.removeEventListener(CONTAINMENT_CHANGED_EVENT, onContainmentEvent);
      window.removeEventListener("storage", onStorage);
    };
  }, [enabled, onRefresh, intervalMs]);
}
