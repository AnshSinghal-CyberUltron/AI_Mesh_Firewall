import { useState, useEffect, useRef, useCallback } from "react";
import {
  Terminal, Pause, Play, Trash2, Wifi, WifiOff, Filter,
} from "lucide-react";
import {
  resolveGatewayBaseUrl,
} from "../utils/environmentUrls";

import { ZEROSHIELD_ML_LOG_SERVICE } from "../constants/zeroshieldBrand";

/** Display label → backend log service name (Bedrock kept internally for log routing). */
const SERVICE_FILTERS = [
  { label: "All", value: "All" },
  { label: "Gateway", value: "Gateway" },
  { label: "Scanner", value: "Scanner" },
  { label: ZEROSHIELD_ML_LOG_SERVICE, value: "Bedrock" },
  { label: "Middleware", value: "Middleware" },
  { label: "Backend", value: "Backend" },
  { label: "Celery", value: "Celery" },
];

const LOG_LEVELS = ["DEBUG", "INFO", "WARNING", "ERROR"];

const LEVEL_STYLES = {
  DEBUG: { bg: "bg-slate-600", text: "text-slate-300" },
  INFO: { bg: "bg-emerald-600", text: "text-emerald-300" },
  WARNING: { bg: "bg-amber-600", text: "text-amber-300" },
  ERROR: { bg: "bg-red-600", text: "text-red-300" },
};

export function LogViewerPanel() {
  const [gatewayUrl] = useState(() => resolveGatewayBaseUrl());
  const [logs, setLogs] = useState([]);
  const [connectionStatus, setConnectionStatus] = useState("disconnected");
  const [serviceFilter, setServiceFilter] = useState("All");
  const [levelFilter, setLevelFilter] = useState("DEBUG");
  const [paused, setPaused] = useState(false);

  const eventSourceRef = useRef(null);
  const logContainerRef = useRef(null);
  const reconnectTimerRef = useRef(null);
  const pausedRef = useRef(paused);
  const logsBufferRef = useRef([]);
  const logIdCounterRef = useRef(0);

  useEffect(() => {
    pausedRef.current = paused;
  }, [paused]);

  const scrollToBottom = useCallback(() => {
    if (logContainerRef.current && !pausedRef.current) {
      logContainerRef.current.scrollTop = logContainerRef.current.scrollHeight;
    }
  }, []);

  const connect = useCallback(() => {
    if (eventSourceRef.current) {
      eventSourceRef.current.close();
    }

    const sseUrl = `${gatewayUrl.replace(/\/+$/, "")}/v1/admin/logs?level=DEBUG`;

    setConnectionStatus("reconnecting");

    try {
      const eventSource = new EventSource(sseUrl);
      eventSourceRef.current = eventSource;

      eventSource.onopen = () => {
        setConnectionStatus("connected");
        if (reconnectTimerRef.current) {
          clearTimeout(reconnectTimerRef.current);
          reconnectTimerRef.current = null;
        }
      };

      eventSource.onmessage = (event) => {
        try {
          const logEntry = JSON.parse(event.data);
          const nextId = `${Date.now()}-${logIdCounterRef.current++}`;
          const entry = {
            id: nextId,
            timestamp: logEntry.timestamp || new Date().toISOString(),
            level: (logEntry.level || "INFO").toUpperCase(),
            service: logEntry.service || logEntry.source || "Gateway",
            message: logEntry.message || logEntry.msg || event.data,
          };

          if (!pausedRef.current) {
            setLogs((prev) => {
              const updated = [...prev, entry];
              if (updated.length > 1000) {
                return updated.slice(-500);
              }
              return updated;
            });
            requestAnimationFrame(scrollToBottom);
          } else {
            logsBufferRef.current.push(entry);
          }
        } catch {
          if (!pausedRef.current) {
            setLogs((prev) => [
              ...prev,
              {
                id: `${Date.now()}-${logIdCounterRef.current++}`,
                timestamp: new Date().toISOString(),
                level: "INFO",
                service: "Gateway",
                message: event.data,
              },
            ]);
          }
        }
      };

      eventSource.onerror = () => {
        setConnectionStatus("disconnected");
        eventSource.close();
        eventSourceRef.current = null;

        reconnectTimerRef.current = setTimeout(() => {
          connect();
        }, 3000);
      };
    } catch {
      setConnectionStatus("disconnected");
      reconnectTimerRef.current = setTimeout(() => {
        connect();
      }, 3000);
    }
  }, [gatewayUrl, scrollToBottom]);

  useEffect(() => {
    connect();

    return () => {
      if (eventSourceRef.current) {
        eventSourceRef.current.close();
        eventSourceRef.current = null;
      }
      if (reconnectTimerRef.current) {
        clearTimeout(reconnectTimerRef.current);
        reconnectTimerRef.current = null;
      }
    };
  }, [connect]);

  const handleTogglePause = () => {
    if (paused) {
      if (logsBufferRef.current.length > 0) {
        setLogs((prev) => [...prev, ...logsBufferRef.current]);
        logsBufferRef.current = [];
        requestAnimationFrame(scrollToBottom);
      }
    }
    setPaused(!paused);
  };

  const handleClear = () => {
    setLogs([]);
    logsBufferRef.current = [];
  };

  const levelPriority = LOG_LEVELS.indexOf(levelFilter);

  const filteredLogs = logs.filter((log) => {
    if (serviceFilter !== "All" && log.service.toLowerCase() !== serviceFilter.toLowerCase()) {
      return false;
    }
    const logPriority = LOG_LEVELS.indexOf(log.level);
    if (logPriority < levelPriority) {
      return false;
    }
    return true;
  });

  const getConnectionBadge = () => {
    switch (connectionStatus) {
      case "connected":
        return (
          <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[10px] font-medium bg-emerald-100 dark:bg-emerald-800/30 text-emerald-700">
            <Wifi className="w-3 h-3" /> Connected
          </span>
        );
      case "reconnecting":
        return (
          <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[10px] font-medium bg-amber-100 dark:bg-amber-800/30 text-amber-700">
            <Wifi className="w-3 h-3" /> Reconnecting
          </span>
        );
      default:
        return (
          <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[10px] font-medium bg-red-100 dark:bg-red-800/30 text-red-700">
            <WifiOff className="w-3 h-3" /> Disconnected
          </span>
        );
    }
  };

  const formatTimestamp = (iso) => {
    try {
      const date = new Date(iso);
      return date.toLocaleTimeString("en-US", {
        hour12: false,
        hour: "2-digit",
        minute: "2-digit",
        second: "2-digit",
      });
    } catch {
      return iso;
    }
  };

  const getLevelStyle = (level) => {
    return LEVEL_STYLES[level] || LEVEL_STYLES.INFO;
  };

  return (
    <div className="bg-white dark:bg-slate-800 border border-slate-200 dark:border-slate-700 rounded-xl shadow-sm p-6">
      <div className="flex items-center justify-between mb-4">
        <div className="flex items-center gap-3">
          <h3 className="text-base font-semibold text-slate-900 dark:text-slate-100 flex items-center gap-2">
            <Terminal className="w-4 h-4 text-teal-600" />
            Real-time Logs
          </h3>
          {getConnectionBadge()}
        </div>
      </div>

      <div className="flex items-center justify-between mb-3 gap-3">
        <div className="flex items-center gap-1">
          {SERVICE_FILTERS.map((filter) => (
            <button
              key={filter.value}
              onClick={() => setServiceFilter(filter.value)}
              className={`px-2.5 py-1 text-[10px] font-medium rounded-full transition-colors ${
                serviceFilter === filter.value
                  ? "bg-teal-600 text-white"
                  : "bg-slate-100 dark:bg-slate-700 text-slate-600 dark:text-slate-400 hover:bg-slate-200"
              }`}
            >
              {filter.label}
            </button>
          ))}
        </div>

        <div className="flex items-center gap-2">
          <div className="flex items-center gap-1.5">
            <Filter className="w-3.5 h-3.5 text-slate-400" />
            <select
              value={levelFilter}
              onChange={(e) => setLevelFilter(e.target.value)}
              className="bg-white dark:bg-slate-800 px-2 py-1 border border-slate-200 dark:border-slate-700 rounded-lg text-[10px] font-medium text-slate-700 dark:text-slate-300 focus:ring-2 focus:ring-teal-500 focus:border-transparent"
            >
              {LOG_LEVELS.map((level) => (
                <option key={level} value={level}>{level}</option>
              ))}
            </select>
          </div>

          <button
            onClick={handleTogglePause}
            className={`p-1.5 rounded transition-colors ${
              paused
                ? "bg-emerald-50 dark:bg-emerald-900/20 text-emerald-600 hover:bg-emerald-100 dark:bg-emerald-800/30"
                : "bg-slate-100 dark:bg-slate-700 text-slate-600 dark:text-slate-400 hover:bg-slate-200"
            }`}
            title={paused ? "Resume" : "Pause"}
          >
            {paused ? <Play className="w-3.5 h-3.5" /> : <Pause className="w-3.5 h-3.5" />}
          </button>

          <button
            onClick={handleClear}
            className="p-1.5 bg-slate-100 dark:bg-slate-700 text-slate-600 dark:text-slate-400 hover:bg-slate-200 rounded transition-colors"
            title="Clear logs"
          >
            <Trash2 className="w-3.5 h-3.5" />
          </button>
        </div>
      </div>

      <div
        ref={logContainerRef}
        className="bg-slate-900 rounded-lg p-3 font-mono text-[11px] max-h-[400px] overflow-y-auto overflow-x-auto"
      >
        {filteredLogs.length === 0 ? (
          <div className="text-slate-500 dark:text-slate-400 text-center py-8">
            {connectionStatus === "connected"
              ? "Waiting for log events..."
              : "Connecting to log stream..."}
          </div>
        ) : (
          <div className="space-y-0.5">
            {filteredLogs.map((log, index) => {
              const levelStyle = getLevelStyle(log.level);
              return (
                <div key={`${log.id}-${index}`} className="flex items-start gap-2 py-0.5 hover:bg-slate-800/50 px-1 rounded">
                  <span className="text-slate-400 whitespace-nowrap flex-shrink-0">
                    {formatTimestamp(log.timestamp)}
                  </span>
                  <span className={`${levelStyle.bg} text-white px-1.5 py-0 rounded text-[9px] font-bold flex-shrink-0 leading-4`}>
                    {log.level}
                  </span>
                  <span className="text-teal-400 flex-shrink-0">[{log.service}]</span>
                  <span className="text-slate-200 break-all">{log.message}</span>
                </div>
              );
            })}
          </div>
        )}
      </div>

      {paused && logsBufferRef.current.length > 0 && (
        <div className="mt-2 text-center text-[10px] text-amber-600 font-medium">
          {logsBufferRef.current.length} log entries buffered while paused
        </div>
      )}
    </div>
  );
}
