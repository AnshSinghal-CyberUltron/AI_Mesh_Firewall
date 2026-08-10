import { useCallback } from "react";
import { useRealtimeNotifications } from "../../hooks/useRealtimeNotifications";
import { MODULE2_ENFORCEMENT_NUDGE_EVENT } from "./useModule2Poll";

/**
 * Module-2-local listener on the shared /ws/notifications/ socket.
 * Does not modify useRealtimeNotifications — only subscribes and dispatches
 * MODULE2_ENFORCEMENT_NUDGE_EVENT so useModule2Poll can refresh hot pages.
 */
export function useModule2EnforcementNudge({ enabled = true } = {}) {
  const onEnforcementEvent = useCallback(() => {
    if (typeof window === "undefined") return;
    window.dispatchEvent(new CustomEvent(MODULE2_ENFORCEMENT_NUDGE_EVENT));
  }, []);

  useRealtimeNotifications({
    enabled,
    onEnforcementEvent,
  });
}
