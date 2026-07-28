import {
  actionPromptStyle,
  eventPromptPreview,
  laneLabel,
} from "./uebaPromptDisplay";
import { formatUebaRequestTime } from "./uebaTimeFormat";

function formatRequestTime(timestamp) {
  return formatUebaRequestTime(timestamp);
}

function promptPreview(req) {
  return eventPromptPreview(req);
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
          <li key={`${req.event_id || req.enforcement_event_id || req.request_id || req.timestamp}-${index}`} className="relative pb-4 last:pb-0">
            <span className="absolute -left-[1.35rem] top-1.5 h-2.5 w-2.5 rounded-full border-2 border-white bg-teal-500 dark:border-slate-900" />
            <p className="text-[10px] text-slate-400">{formatRequestTime(req.timestamp)}</p>
            <div className={`mt-1 rounded-md border px-2.5 py-2 ${actionPromptStyle(req.action)}`}>
              <p className="text-[11px] font-semibold uppercase">
                {laneLabel(req.request_lane || "chat")}
                {" · "}{(req.action || "—").replace(/_/g, " ")}
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
