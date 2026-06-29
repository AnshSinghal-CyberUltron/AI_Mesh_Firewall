import { useState, useEffect, useCallback, useRef } from "react";
import { useAuth } from "../context/AuthContext";
import { useRealtimeNotifications } from "./useRealtimeNotifications";

export const TIME_RANGE_TO_HOURS = {
  "1h": 1,
  "6h": 6,
  "24h": 24,
  "7d": 168,
  "30d": 720,
};

const MODULE_SOURCE_MAP = {
  "1.1": null,
  "1.2": "security_scan",
  "1.3": "security_scan",
  "1.4": "mcp_scan",
  "1.5": "routing",
  "1.6": "policy",
  "1.7": "security_scan",
};

export function useFirewallData(moduleId, timeRange = "24h", { enabled = true } = {}) {
  const { fetchWithAuth } = useAuth();
  const [socKpis, setSocKpis] = useState(null);
  const [threatFeed, setThreatFeed] = useState([]);
  const [threatFeedCount, setThreatFeedCount] = useState(null);
  const [attackTrends, setAttackTrends] = useState([]);
  const [gatewayStats, setGatewayStats] = useState(null);
  const [ragPipelineKpis, setRagPipelineKpis] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  const hasLoadedOnce = useRef(false);

  const fetchData = useCallback(async ({ background = false } = {}) => {
    if (!background) {
      setLoading(true);
      setError(null);
      if (!hasLoadedOnce.current) {
        setSocKpis(null);
        setThreatFeed([]);
        setThreatFeedCount(null);
        setAttackTrends([]);
        setGatewayStats(null);
        setRagPipelineKpis(null);
      }
    }

    const hours = TIME_RANGE_TO_HOURS[timeRange] || 24;
    const period = timeRange;
    const source = MODULE_SOURCE_MAP[moduleId] || null;

    try {
      const feedParams = new URLSearchParams({
        hours: String(hours),
        // Bump from 50 -> 500 so per-module summary KPIs (e.g. Module 1.4
        // "Context events") count all events in the lens instead of being
        // capped at the page size. The backend supports up to 500.
        limit: "500",
      });
      if (moduleId === "1.6") {
        feedParams.set("module_id", "1.6");
      } else if (source) {
        feedParams.set("source", source);
      }

      const requests = [
        fetchWithAuth(`/api/security/soc-kpis/?period=${period}`),
        fetchWithAuth(`/api/security/threat-feed/?${feedParams.toString()}`),
        fetchWithAuth(`/api/security/attack-vector-trends/?period=${period}`),
        fetchWithAuth("/api/gateways/stats/"),
      ];
      if (moduleId === "1.2" || moduleId === "1.3") {
        requests.push(fetchWithAuth(`/api/security/rag-pipeline-kpis/?period=${period}`));
      }

      const results = await Promise.allSettled(requests);

      if (results[0].status === "fulfilled" && results[0].value.ok) {
        const data = await results[0].value.json();
        setSocKpis(data);
      }

      if (results[1].status === "fulfilled" && results[1].value.ok) {
        const data = await results[1].value.json();
        if (Array.isArray(data)) {
          setThreatFeed(data);
          setThreatFeedCount(data.length);
        } else if (Array.isArray(data?.results)) {
          setThreatFeed(data.results);
          setThreatFeedCount(typeof data.count === "number" ? data.count : data.results.length);
        } else {
          setThreatFeed([]);
          setThreatFeedCount(0);
        }
      }

      if (results[2].status === "fulfilled" && results[2].value.ok) {
        const data = await results[2].value.json();
        setAttackTrends(Array.isArray(data) ? data : []);
      }

      if (results[3].status === "fulfilled" && results[3].value.ok) {
        const data = await results[3].value.json();
        setGatewayStats(data);
      }

      if (results[4] && results[4].status === "fulfilled" && results[4].value.ok) {
        const data = await results[4].value.json();
        setRagPipelineKpis(data);
      }
    } catch (err) {
      setSocKpis(null);
      setThreatFeed([]);
      setThreatFeedCount(null);
      setAttackTrends([]);
      setGatewayStats(null);
      setRagPipelineKpis(null);
      setError(err.message || "Failed to fetch firewall data");
    } finally {
      hasLoadedOnce.current = true;
      setLoading(false);
    }
  }, [fetchWithAuth, moduleId, timeRange]);

  // #5: `enabled` lets a parent that already runs this hook pass its result down
  // (instead of a child mounting a SECOND instance that duplicates the fetch +
  // the 15s polling). When disabled, skip the initial fetch, realtime refetch,
  // and polling — the parent drives the data.
  useEffect(() => {
    if (!enabled) return;
    hasLoadedOnce.current = false;
    fetchData({ background: false });
  }, [fetchData, enabled]);

  useRealtimeNotifications({
    enabled,
    onEnforcementEvent: () => fetchData({ background: true }),
  });

  // Polling fallback: refresh without clearing UI (avoids hero/table flicker).
  useEffect(() => {
    if (!enabled) return undefined;
    const id = setInterval(() => fetchData({ background: true }), 15000);
    return () => clearInterval(id);
  }, [fetchData, enabled]);

  const metrics = buildMetrics(moduleId, socKpis, gatewayStats, threatFeedCount);

  const statusMetrics = buildStatusMetrics(socKpis, gatewayStats);

  const timeSeriesData = attackTrends.map((bucket) => {
    const time = new Date(bucket.time);
    const label =
      timeRange === "1h"
        ? `${time.getHours()}:${String(time.getMinutes()).padStart(2, "0")}`
        : `${time.getHours()}:00`;
    return {
      time: label,
      primary:
        (bucket.promptInjection || 0) +
        (bucket.dataLeakage || 0) +
        (bucket.jailbreak || 0) +
        (bucket.goalHijacking || 0) +
        (bucket.toolOverreach || 0),
      secondary: bucket.promptInjection || 0,
    };
  });

  const actionDistributionData = socKpis
    ? [
        {
          name: "Allowed",
          value: Math.max(
            0,
            (socKpis.total_threats || 0) -
              (socKpis.blocked || 0) -
              (socKpis.redacted || 0)
          ),
          color: "#10b981",
        },
        { name: "Blocked", value: socKpis.blocked || 0, color: "#ef4444" },
        { name: "Redacted", value: socKpis.redacted || 0, color: "#f59e0b" },
      ]
    : null;

  const previewData = threatFeed.map((ev) => ({
    timestamp: ev.timestamp
      ? new Date(ev.timestamp).toISOString().substring(0, 19).replace("T", " ")
      : "",
    category: ev.category || "",
    subcategory: ev.subcategory || "",
    action: ev.action || "",
    severity: ev.severity || "",
    source: ev.source || "",
    // Hidden fields — not rendered in table (sliced out by previewColumns.length)
    // but available when passed to onViewLogDetail(row) for LogDetailPage
    id: ev.id,
    metadata: ev.metadata || {},
    endpoint_name: ev.endpoint_name || "",
    raw: ev,
  }));

  // Live-only: the backend SocKpisView always returns latency_distribution and
  // health_radar (shape {metric, value}) derived from real EnforcementEvent data.
  // No synthetic fallback — if the backend has nothing, render an empty chart
  // (handled by the consuming component's empty-state) rather than fabricating
  // constants like Uptime:95 / Policy Coverage:85.
  const latencyData = socKpis?.latency_distribution?.length > 0
    ? socKpis.latency_distribution
    : [];
  const healthRadarData = socKpis?.health_radar?.length > 0
    ? socKpis.health_radar
    : [];

  return {
    socKpis,
    metrics,
    statusMetrics,
    threatFeed,
    previewData,
    attackTrends,
    timeSeriesData,
    actionDistributionData,
    latencyData,
    healthRadarData,
    gatewayStats,
    ragPipelineKpis,
    loading,
    error,
    refetch: fetchData,
  };
}

function buildMetrics(moduleId, socKpis, gatewayStats, threatFeedCount) {
  if (!socKpis) return null;

  // For module pages whose dashboard is filtered by a single threat source
  // (see MODULE_SOURCE_MAP), prefer the source-scoped threat-feed count over
  // the org-wide soc-kpis total so per-module "Total Events" matches the
  // module's evidence list and "Context events" KPI.
  const sourceScoped = MODULE_SOURCE_MAP[moduleId] != null;
  const total = sourceScoped && typeof threatFeedCount === "number"
    ? threatFeedCount
    : (socKpis.total_threats || 0);
  const blocked = socKpis.blocked || 0;
  const redacted = socKpis.redacted || 0;
  const blockRate = socKpis.block_rate || 0;
  const critical = socKpis.critical_count || 0;
  const allowed = Math.max(0, total - blocked - redacted);

  switch (moduleId) {
    case "1.1":
      return [
        {
          label: "Total Events",
          value: total.toLocaleString(),
          change: `${blockRate}% block rate`,
          trend: "up",
        },
        {
          label: "Allowed",
          value: allowed.toLocaleString(),
          change: "",
          trend: "up",
        },
        {
          label: "Rate Limited",
          value: blocked.toLocaleString(),
          change: "",
          trend: blocked > 0 ? "up" : "down",
        },
        {
          label: "Critical",
          value: critical.toLocaleString(),
          change: "",
          trend: critical > 0 ? "up" : "down",
        },
      ];
    case "1.2":
      return [
        {
          label: "Pipeline Events",
          value: total.toLocaleString(),
          change: `${blockRate}% block rate`,
          trend: "up",
        },
        {
          label: "Query Blocks",
          value: blocked.toLocaleString(),
          change: "",
          trend: blocked > 0 ? "up" : "down",
        },
        {
          label: "Redactions",
          value: redacted.toLocaleString(),
          change: "",
          trend: redacted > 0 ? "up" : "down",
        },
        {
          label: "Critical",
          value: critical.toLocaleString(),
          change: "",
          trend: critical > 0 ? "up" : "down",
        },
      ];
    case "1.3":
      return [
        {
          label: "Vector Queries",
          value: total.toLocaleString(),
          change: `${blockRate}% block rate`,
          trend: "up",
        },
        {
          label: "Blocked",
          value: blocked.toLocaleString(),
          change: "",
          trend: blocked > 0 ? "up" : "down",
        },
        {
          label: "Redacted",
          value: redacted.toLocaleString(),
          change: "",
          trend: redacted > 0 ? "up" : "down",
        },
        {
          label: "Critical",
          value: critical.toLocaleString(),
          change: "",
          trend: critical > 0 ? "up" : "down",
        },
      ];
    case "1.4":
      return [
        {
          label: "MCP Events",
          value: total.toLocaleString(),
          change: `${blockRate}% block rate`,
          trend: "up",
        },
        {
          label: "Blocked",
          value: blocked.toLocaleString(),
          change: "",
          trend: blocked > 0 ? "up" : "down",
        },
        {
          label: "Redactions",
          value: redacted.toLocaleString(),
          change: "",
          trend: redacted > 0 ? "up" : "down",
        },
        {
          label: "Critical",
          value: critical.toLocaleString(),
          change: "",
          trend: critical > 0 ? "up" : "down",
        },
      ];
    case "1.5":
      return [
        {
          label: "Total Routings",
          value: total.toLocaleString(),
          change: `${blockRate}% block rate`,
          trend: "up",
        },
        {
          label: "Blocked",
          value: blocked.toLocaleString(),
          change: "",
          trend: blocked > 0 ? "up" : "down",
        },
        {
          label: "Redacted",
          value: redacted.toLocaleString(),
          change: "",
          trend: redacted > 0 ? "up" : "down",
        },
        {
          label: "Critical",
          value: critical.toLocaleString(),
          change: "",
          trend: critical > 0 ? "up" : "down",
        },
      ];
    case "1.6":
      return [
        {
          label: "Total Events",
          value: total.toLocaleString(),
          change: "",
          trend: "up",
        },
        {
          label: "Blocked",
          value: blocked.toLocaleString(),
          change: "",
          trend: blocked > 0 ? "up" : "down",
        },
        {
          label: "Redacted",
          value: redacted.toLocaleString(),
          change: "",
          trend: redacted > 0 ? "up" : "down",
        },
        {
          label: "Critical",
          value: critical.toLocaleString(),
          change: "",
          trend: critical > 0 ? "up" : "down",
        },
      ];
    case "1.7":
      return [
        {
          label: "Outputs Scanned",
          value: total.toLocaleString(),
          change: `${blockRate}% block rate`,
          trend: "up",
        },
        {
          label: "Blocked",
          value: blocked.toLocaleString(),
          change: "",
          trend: blocked > 0 ? "up" : "down",
        },
        {
          label: "Redacted",
          value: redacted.toLocaleString(),
          change: "",
          trend: redacted > 0 ? "up" : "down",
        },
        {
          label: "Critical",
          value: critical.toLocaleString(),
          change: "",
          trend: critical > 0 ? "up" : "down",
        },
      ];
    default:
      return [
        {
          label: "Total",
          value: total.toLocaleString(),
          change: "",
          trend: "up",
        },
        {
          label: "Blocked",
          value: blocked.toLocaleString(),
          change: "",
          trend: "up",
        },
        {
          label: "Redacted",
          value: redacted.toLocaleString(),
          change: "",
          trend: "up",
        },
        {
          label: "Critical",
          value: critical.toLocaleString(),
          change: "",
          trend: "up",
        },
      ];
  }
}

function buildStatusMetrics(socKpis, gatewayStats) {
  if (!socKpis) return null;
  const total = socKpis.total_threats || 0;
  const blocked = socKpis.blocked || 0;
  const redacted = socKpis.redacted || 0;
  const critical = socKpis.critical_count || 0;
  const allowed = Math.max(0, total - blocked - redacted);
  const successRate = total > 0 ? `${((allowed / total) * 100).toFixed(1)}%` : "--";
  let avgLatencyMs = socKpis?.avg_latency_ms;

  if (avgLatencyMs == null && Array.isArray(gatewayStats) && gatewayStats.length > 0) {
    const parsed = gatewayStats
      .map((item) => {
        if (item?.avg_latency_ms != null) return Number(item.avg_latency_ms);
        const fromLabel = String(item?.avgLatency || "").match(/(\d+(?:\.\d+)?)/);
        return fromLabel ? Number(fromLabel[1]) : null;
      })
      .filter((value) => Number.isFinite(value));

    if (parsed.length > 0) {
      avgLatencyMs = parsed.reduce((sum, value) => sum + value, 0) / parsed.length;
    }
  }

  const avgResponseTime = avgLatencyMs != null ? `${Math.round(avgLatencyMs)}ms` : "--";
  const activeAlerts = critical > 0 ? critical.toLocaleString() : "0";
  return { successRate, avgResponseTime, activeAlerts };
}
