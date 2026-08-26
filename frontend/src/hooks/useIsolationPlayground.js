import { useState, useEffect, useCallback, useRef } from "react";
import { useGatewayConfig } from "./useGatewayConfig";
import { useAuth } from "../context/AuthContext";
import { isDocumentHidden } from "../utils/requestLifecycle.js";
import { startVisibleInterval } from "../utils/visiblePoll.js";
import { resolveGatewayHealthUrl } from "../utils/environmentUrls";

const HEALTH_POLL_INTERVAL = 15000;
const PLAYGROUND_API = "/api/gateways/isolation-playground/";
const PLAYGROUND_ROTATE_API = "/api/gateways/isolation-playground/rotate/";

function playgroundKeyStorageKey(orgId) {
  return orgId ? `zeroshield_isolation_playground_key:${orgId}` : "";
}

function readStoredPlaygroundKey(orgId) {
  try {
    const key = playgroundKeyStorageKey(orgId);
    return key ? localStorage.getItem(key) || "" : "";
  } catch {
    return "";
  }
}

/**
 * Module 1.6 isolation playground credentials — separate from attack-simulator key.
 */
export function useIsolationPlayground() {
  const { gatewayUrl } = useGatewayConfig();
  const { fetchWithAuth, user, loading: authLoading } = useAuth();
  const orgId = user?.organization?.id;

  const [gatewayKey, setGatewayKey] = useState(() => readStoredPlaygroundKey(orgId));
  const [connectionStatus, setConnectionStatus] = useState("disconnected");
  const [gatewayHealth, setGatewayHealth] = useState(null);
  const [backendHealth, setBackendHealth] = useState(null);
  const [riskScore, setRiskScore] = useState(null);
  const [keyPrefix, setKeyPrefix] = useState("");
  const [executing, setExecuting] = useState(false);
  const [rotating, setRotating] = useState(false);

  const healthRef = useRef(null);
  const bootstrapAttemptedRef = useRef(null);

  const updateGatewayKey = useCallback((key, storageKey) => {
    setGatewayKey(key);
    const target = storageKey || playgroundKeyStorageKey(orgId);
    try {
      if (target && key) {
        localStorage.setItem(target, key);
      }
    } catch {
      // localStorage may be unavailable.
    }
  }, [orgId]);

  const refreshMetadata = useCallback(async () => {
    try {
      const res = await fetchWithAuth(PLAYGROUND_API);
      if (!res.ok) return;
      const data = await res.json();
      if (data?.has_gateway_key) {
        setRiskScore(typeof data.risk_score === "number" ? data.risk_score : null);
        setKeyPrefix(data.prefix || "");
      }
    } catch {
      // best-effort
    }
  }, [fetchWithAuth]);

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

  const checkHealth = useCallback(async () => {
    let gwOk = false;
    let beOk = false;

    try {
      const res = await fetch(resolveGatewayHealthUrl(gatewayUrl), { signal: AbortSignal.timeout(5000) });
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

    setConnectionStatus(gwOk && beOk ? "connected" : gwOk || beOk ? "degraded" : "disconnected");
  }, [gatewayUrl]);

  const provisionPlaygroundKey = useCallback(async () => {
    const res = await fetchWithAuth(PLAYGROUND_API, { method: "POST" });
    if (!res.ok) return null;
    const data = await res.json();
    if (data?.key) {
      updateGatewayKey(data.key, data.storage_key);
    }
    if (typeof data?.risk_score === "number") {
      setRiskScore(data.risk_score);
    }
    if (data?.prefix) {
      setKeyPrefix(data.prefix);
    }
    return data;
  }, [fetchWithAuth, updateGatewayKey]);

  const resetPlaygroundKey = useCallback(async () => {
    setRotating(true);
    try {
      const res = await fetchWithAuth(PLAYGROUND_ROTATE_API, { method: "POST" });
      if (!res.ok) return null;
      const data = await res.json();
      if (data?.key) {
        updateGatewayKey(data.key, data.storage_key);
      }
      setRiskScore(typeof data.risk_score === "number" ? data.risk_score : 0);
      setKeyPrefix(data.prefix || "");
      return data;
    } finally {
      setRotating(false);
    }
  }, [fetchWithAuth, updateGatewayKey]);

  useEffect(() => {
    if (!isDocumentHidden()) checkHealth();
    healthRef.current = startVisibleInterval(checkHealth, HEALTH_POLL_INTERVAL);
    return () => {
      if (typeof healthRef.current === "function") healthRef.current();
    };
  }, [checkHealth]);

  useEffect(() => {
    if (!orgId) return;
    const cached = readStoredPlaygroundKey(orgId);
    if (cached) {
      setGatewayKey(cached);
    }
    refreshMetadata();
  }, [orgId, refreshMetadata]);

  useEffect(() => {
    if (authLoading || !orgId) return;
    if (gatewayKey) return;
    const attemptKey = String(orgId);
    if (bootstrapAttemptedRef.current === attemptKey) return;
    bootstrapAttemptedRef.current = attemptKey;

    (async () => {
      try {
        await provisionPlaygroundKey();
      } catch {
        // manual key entry still works
      }
    })();
  }, [authLoading, gatewayKey, orgId, provisionPlaygroundKey]);

  return {
    gatewayUrl,
    gatewayKey,
    setGatewayKey: updateGatewayKey,
    connectionStatus,
    backendHealth,
    gatewayHealth,
    gatewayFetch,
    gatewayFetchStream,
    executing,
    setExecuting,
    riskScore,
    keyPrefix,
    refreshMetadata,
    resetPlaygroundKey,
    rotating,
    checkHealth,
  };
}
