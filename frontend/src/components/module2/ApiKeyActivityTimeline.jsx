function formatRequestTime(timestamp) {
  return (timestamp || "").replace("T", " ").slice(0, 19);
}

function promptPreview(req) {
  const direct = (req?.prompt_snippet || req?.intent || req?.detail || "").trim();
  if (direct) return direct;
  const lineage = req?.prompt_lineage;
  if (Array.isArray(lineage)) {
    for (const entry of lineage) {
      const text = (entry?.prompt || entry?.text || "").trim();
      if (text) return text;
    }
  }
  return "";
}

const ACTION_STYLES = {
  block: "border-red-300 bg-red-50 text-red-800 dark:border-red-900 dark:bg-red-950/40 dark:text-red-200",
  redact: "border-amber-300 bg-amber-50 text-amber-800 dark:border-amber-900 dark:bg-amber-950/40 dark:text-amber-200",
  allow: "border-emerald-300 bg-emerald-50 text-emerald-800 dark:border-emerald-900 dark:bg-emerald-950/40 dark:text-emerald-200",
};

function actionStyle(action) {
  const key = (action || "").toLowerCase();
  if (key.includes("block")) return ACTION_STYLES.block;
  if (key.includes("redact")) return ACTION_STYLES.redact;
  return ACTION_STYLES.allow;
}

export function ApiKeyActivityTimeline({ requests = [], requestCount = 0 }) {
  if (!requests?.length) {
    return (
      <div className="rounded-lg border border-slate-200 p-3 dark:border-slate-600">
        <p className="text-xs font-semibold uppercase text-slate-500">Activity timeline</p>
        <p className="mt-2 text-xs text-slate-400">
          {requestCount ? `${requestCount} event(s) — waiting for prompt detail…` : "No events in this period."}
        </p>
      </div>
    );
  }

  return (
    <div className="rounded-lg border border-slate-200 p-3 dark:border-slate-600">
      <p className="mb-3 text-xs font-semibold uppercase text-slate-500">
        Activity timeline
        {requestCount ? ` · last ${Math.min(requests?.length || 0, 10)} of ${requestCount}` : " · last 10 events"}
      </p>
      <ol className="relative space-y-0 border-l-2 border-slate-200 pl-4 dark:border-slate-700">
        {requests.map((req, index) => (
          <li key={`${req.event_id || req.timestamp}-${index}`} className="relative pb-4 last:pb-0">
            <span className="absolute -left-[1.35rem] top-1.5 h-2.5 w-2.5 rounded-full border-2 border-white bg-teal-500 dark:border-slate-900" />
            <p className="text-[10px] text-slate-400">{formatRequestTime(req.timestamp)} UTC</p>
            <div className={`mt-1 rounded-md border px-2.5 py-2 ${actionStyle(req.action)}`}>
              <p className="text-[11px] font-semibold uppercase">
                {(req.action || "—").replace(/_/g, " ")}
                {" · "}{req.model || "—"}
                {req.threat_type && req.threat_type !== "none" ? ` · ${req.threat_type}` : ""}
              </p>
              <p className="mt-1 line-clamp-3 text-xs leading-relaxed opacity-90">
                {promptPreview(req) || "No prompt captured."}
              </p>
            </div>
          </li>
        ))}
      </ol>
    </div>
  );
}
