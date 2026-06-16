import { useState, useEffect, useCallback, useRef } from "react";
import { useGatewayConfig } from "./useGatewayConfig";
import { useAuth } from "../context/AuthContext";
import { isLocalDevBrowser } from "../utils/environmentUrls";
import {
  getGatewayApiKey,
  migrateGatewayStorage,
  setGatewayApiKey,
} from "../utils/gatewayStorage";
import {
  resolveGatewayKeyContext,
  writeStoredGatewayKeyContext,
} from "../api/gatewayContext";

const HEALTH_POLL_INTERVAL = 15000;

/**
 * Shared hook for all Module 1 live simulators.
 * Provides connection management, health polling, gateway/backend fetch helpers,
 * scenario execution, and result diffing.
 */
export function useSimulatorEngine() {
  const { gatewayUrl } = useGatewayConfig();
  const { fetchWithAuth } = useAuth();

  const [gatewayKey, setGatewayKey] = useState(() => {
    migrateGatewayStorage();
    return getGatewayApiKey();
  });
  const [connectionStatus, setConnectionStatus] = useState("disconnected"); // connected | degraded | disconnected
  const [authStatus, setAuthStatus] = useState("unknown"); // ok | invalid | missing | unknown
  const [backendHealth, setBackendHealth] = useState(null);
  const [gatewayHealth, setGatewayHealth] = useState(null);
  const [executing, setExecuting] = useState(false);
  const [lastResult, setLastResult] = useState(null);
  const [previousResult, setPreviousResult] = useState(null);

  const healthRef = useRef(null);
  const defaultKeyFetchedRef = useRef(false);

  // Save gateway key to localStorage (both legacy + primary keys)
  const updateGatewayKey = useCallback((key) => {
    setGatewayKey(key);
    setGatewayApiKey(key);
    const trimmed = String(key || "").trim();
    if (trimmed) {
      resolveGatewayKeyContext(fetchWithAuth, trimmed)
        .then((ctx) => {
          if (ctx) writeStoredGatewayKeyContext(ctx);
        })
        .catch(() => {});
    }
  }, [fetchWithAuth]);

  // Authenticated fetch to gateway
  const gatewayFetch = useCallback(async (path, opts = {}) => {
    const url = `${gatewayUrl}${path}`;
    const headers = {
      "Content-Type": "application/json",
      ...(gatewayKey ? { Authorization: `Bearer ${gatewayKey}` } : {}),
      ...(opts.headers || {}),
    };
    const res = await fetch(url, { ...opts, headers });
    const contentType = res.headers.get("content-type") || "";
    if (contentType.includes("text/event-stream")) {
      const sse = await import("../utils/liveGateway").then((m) => m.consumeSSEStream(res));
      return {
        ok: res.ok,
        status: res.status,
        headers: res.headers,
        sse,
        data: sse.data || sse.terminalError || null,
        isStream: true,
      };
    }
    const data = await res.json().catch(() => null);
    return { ok: res.ok, status: res.status, headers: res.headers, data, isStream: false };
  }, [gatewayUrl, gatewayKey]);

  /**
   * Explicit SSE path for simulators that exercise stream:true governance.
   * Reason: output-guard blocks arrive mid-stream; JSON fetch cannot observe them.
   */
  const gatewayFetchStream = useCallback(async (path, opts = {}) => {
    const url = `${gatewayUrl}${path}`;
    const headers = {
      "Content-Type": "application/json",
      Accept: "text/event-stream",
      ...(gatewayKey ? { Authorization: `Bearer ${gatewayKey}` } : {}),
      ...(opts.headers || {}),
    };
    const res = await fetch(url, { ...opts, headers });
    const { consumeSSEStream } = await import("../utils/liveGateway");
    const sse = await consumeSSEStream(res);
    return {
      ok: res.ok,
      status: res.status,
      headers: res.headers,
      sse,
      data: sse.data || null,
      isStream: true,
    };
  }, [gatewayUrl, gatewayKey]);

  // Authenticated fetch to backend (through proxy)
  const backendFetch = useCallback(async (path, opts = {}) => {
    try {
      const res = await fetchWithAuth(path, opts);
      return { ok: true, status: 200, data: res };
    } catch (err) {
      return { ok: false, status: err.status || 500, data: { error: err.message } };
    }
  }, [fetchWithAuth]);

  // Health check polling (includes API key validation — /health alone is not enough)
  const checkHealth = useCallback(async () => {
    let gwOk = false;
    let beOk = false;
    let keyOk = false;

    try {
      const res = await fetch(`${gatewayUrl}/health`, { signal: AbortSignal.timeout(5000) });
      const data = await res.json().catch(() => null);
      gwOk = res.ok;
      setGatewayHealth(data);
    } catch {
      setGatewayHealth(null);
    }

    try {
      const res = await fetch("/api/health/", { signal: AbortSignal.timeout(5000) });
      beOk = res.ok;
      setBackendHealth(beOk ? { status: "ok" } : null);
    } catch {
      setBackendHealth(null);
    }

    if (gatewayKey?.trim()) {
      try {
        const res = await fetch(`${gatewayUrl}/v1/models`, {
          headers: { Authorization: `Bearer ${gatewayKey.trim()}` },
          signal: AbortSignal.timeout(5000),
        });
        keyOk = res.status !== 401;
        setAuthStatus(res.status === 401 ? "invalid" : "ok");
      } catch {
        setAuthStatus("unknown");
      }
    } else {
      setAuthStatus("missing");
    }

    if (gwOk && beOk && gatewayKey?.trim() && keyOk) {
      setConnectionStatus("connected");
    } else if (gwOk && beOk) {
      setConnectionStatus(keyOk ? "connected" : "degraded");
    } else if (gwOk || beOk) {
      setConnectionStatus("degraded");
    } else {
      setConnectionStatus("disconnected");
    }
  }, [gatewayUrl, gatewayKey]);

  useEffect(() => {
    checkHealth();
    healthRef.current = setInterval(checkHealth, HEALTH_POLL_INTERVAL);
    return () => clearInterval(healthRef.current);
  }, [checkHealth]);

  useEffect(() => {
    if (gatewayKey || defaultKeyFetchedRef.current) return;
    if (!isLocalDevBrowser()) return;
    defaultKeyFetchedRef.current = true;

    (async () => {
      try {
        const res = await fetchWithAuth("/api/gateways/simulator-default/");
        if (!res.ok) return;
        const data = await res.json();
        if (data?.key) {
          updateGatewayKey(data.key);
          if (data.prefix && data.key_id) {
            writeStoredGatewayKeyContext({
              prefix: data.prefix,
              keyId: data.key_id,
              name: data.name || "simulator-default",
            });
          }
        }
      } catch {
        // Simulator bootstrap is best-effort; manual entry still works.
      }
    })();
  }, [fetchWithAuth, gatewayKey, updateGatewayKey]);

  // Execute a scenario against the gateway
  const executeScenario = useCallback(async (config) => {
    setExecuting(true);
    setPreviousResult(lastResult);
    try {
      const path = config.endpoint || "/v1/chat/completions";
      const res = await gatewayFetch(path, {
        method: config.method || "POST",
        body: JSON.stringify(config.payload || {}),
      });
      const result = {
        ...res.data,
        httpStatus: res.status,
        success: res.ok,
        timestamp: new Date().toISOString(),
      };
      setLastResult(result);
      return result;
    } catch (err) {
      const result = { error: err.message, success: false, timestamp: new Date().toISOString() };
      setLastResult(result);
      return result;
    } finally {
      setExecuting(false);
    }
  }, [gatewayFetch, lastResult]);

  // Diff two results for before/after comparison
  const diffResults = useCallback((a, b) => {
    if (!a || !b) return null;
    const changes = [];
    const allKeys = new Set([...Object.keys(a), ...Object.keys(b)]);
    for (const key of allKeys) {
      if (JSON.stringify(a[key]) !== JSON.stringify(b[key])) {
        changes.push({ key, before: a[key], after: b[key] });
      }
    }
    return changes;
  }, []);

  return {
    gatewayUrl,
    gatewayKey,
    setGatewayKey: updateGatewayKey,
    connectionStatus,
    authStatus,
    backendHealth,
    gatewayHealth,
    gatewayFetch,
    gatewayFetchStream,
    backendFetch,
    executeScenario,
    executing,
    lastResult,
    previousResult,
    diffResults,
    checkHealth,
  };
}
