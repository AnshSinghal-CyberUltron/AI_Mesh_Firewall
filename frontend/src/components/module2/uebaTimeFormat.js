/** Human-readable timestamps for API key recent-prompt views. */

export function formatUebaRequestTime(timestamp, { now = new Date() } = {}) {
  if (!timestamp) return "—";
  const date = new Date(timestamp);
  if (Number.isNaN(date.getTime())) {
    return String(timestamp).replace("T", " ").replace(/\.\d+Z?$/, "").slice(0, 19);
  }

  const localeStr = date.toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    year: "numeric",
    hour: "numeric",
    minute: "2-digit",
    hour12: true,
  });

  const diffMs = now.getTime() - date.getTime();
  if (diffMs >= 0) {
    const mins = Math.floor(diffMs / 60000);
    if (mins < 1) return `Just now · ${localeStr}`;
    if (mins < 60) return `${mins} min ago · ${localeStr}`;
    const hrs = Math.floor(mins / 60);
    if (hrs < 24) return `${hrs}h ago · ${localeStr}`;
    const days = Math.floor(hrs / 24);
    if (days < 7) return `${days}d ago · ${localeStr}`;
  }

  return localeStr;
}
