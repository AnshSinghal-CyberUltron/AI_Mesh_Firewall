/** Cross-module signal when API key disable or kill-switch state changes. */

export const CONTAINMENT_CHANGED_EVENT = "zeroshield:containment-changed";
export const CONTAINMENT_STORAGE_KEY = "zeroshield_containment_ping";

export function notifyContainmentChanged(source = "unknown") {
  if (typeof window === "undefined") return;
  window.dispatchEvent(
    new CustomEvent(CONTAINMENT_CHANGED_EVENT, { detail: { source, at: Date.now() } }),
  );
  try {
    localStorage.setItem(CONTAINMENT_STORAGE_KEY, String(Date.now()));
  } catch {
    /* private mode / quota */
  }
}
