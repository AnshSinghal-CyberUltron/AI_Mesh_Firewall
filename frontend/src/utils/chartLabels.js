/** Format ISO trend bucket timestamps for chart X-axis labels (HH:MM). */
export function formatTrendBucketLabel(isoTime, period = "24h") {
  if (!isoTime) return "--";
  const date = new Date(isoTime);
  if (Number.isNaN(date.getTime())) return "--";
  if (period === "7d" || period === "30d") {
    return `${date.getMonth() + 1}/${date.getDate()}`;
  }
  return `${String(date.getHours()).padStart(2, "0")}:${String(date.getMinutes()).padStart(2, "0")}`;
}
