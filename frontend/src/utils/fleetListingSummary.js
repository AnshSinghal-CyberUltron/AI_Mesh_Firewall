/**
 * Operator-facing count line for the UEBA API Key Fleet Inspector.
 * Separates "keys listed for this window (+ containment)" from "all registered keys".
 */
export function formatFleetListingSummary({
  shownCount = 0,
  listedCount = 0,
  registeredTotal = null,
  filterId = "all",
  periodLabel = "selected window",
} = {}) {
  const registered = Number.isFinite(Number(registeredTotal)) ? Number(registeredTotal) : null;
  if (registered == null || registered < 0) {
    return filterId === "all"
      ? `Showing ${shownCount} key${shownCount === 1 ? "" : "s"} with activity or containment in this window.`
      : `Showing ${shownCount} key${shownCount === 1 ? "" : "s"} matching this filter.`;
  }
  const idle = Math.max(0, registered - listedCount);
  if (filterId === "all") {
    if (idle > 0) {
      return `Showing ${shownCount} of ${registered} registered keys (${idle} idle in ${periodLabel} — no traffic, not disabled or kill-switched).`;
    }
    return `Showing ${shownCount} of ${registered} registered keys.`;
  }
  return `Showing ${shownCount} matching this filter · ${listedCount} listed for ${periodLabel} of ${registered} registered.`;
}
