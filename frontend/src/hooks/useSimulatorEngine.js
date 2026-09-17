import { useState, useEffect, useCallback, useRef } from "react";
import { useGatewayConfig } from "./useGatewayConfig";
import { useAuth } from "../context/AuthContext";
import { isDocumentHidden } from "../utils/requestLifecycle.js";
import { startVisibleInterval } from "../utils/visiblePoll.js";
import { resolveGatewayHealthUrl } from "../utils/environmentUrls";
import { fetchGatewayPath } from "../utils/gatewayChatFetch";

const HEALTH_POLL_INTERVAL = 15000;
const GATEWAY_KEY_STORAGE_LEGACY = "zeroshield_gateway_key";

function gatewayKeyStorageKey(orgId) {
  return orgId ? `zeroshield_gateway_key:${orgId}` : GATEWAY_KEY_STORAGE_LEGACY;
}

function readStoredGatewayKey(orgId) {
  try {
    const scoped = orgId ? localStorage.getItem(gatewayKeyStorageKey(orgId)) : "";
    if (scoped) return scoped;
    return localStorage.getItem(GATEWAY_KEY_STORAGE_LEGACY) || "";
  } catch {
    return "";
  }
}

/**
 * Shared hook for all Module 1 live simulators.
 * Provides connection management, health polling, gateway/backend fetch helpers,
 * scenario execution, and result diffing.
 */
export function useSimulatorEngine() {
  const { gatewayUrl } = useGatewayConfig();
  const { fetchWithAuth, user, loading: authLoading } = useAuth();
  const orgId = user?.organization?.id;

  const [gatewayKey, setGatewayKey] = useState(() => readStoredGatewayKey(orgId));
  const [connectionStatus, setConnectionStatus] = useState("disconnected"); // connected | degraded | disconnected
  const [backendHealth, setBackendHealth] = useState(null);
  const [gatewayHealth, setGatewayHealth] = useState(null);
  const [executing, setExecuting] = useState(false);
  const [lastResult, setLastResult] = useState(null);
  const [previousResult, setPreviousResult] = useState(null);

  const healthRef = useRef(null);
  const bootstrapAttemptedRef = useRef(null);

  // Save gateway key to org-scoped localStorage (prevents cross-tenant leakage).
  const updateGatewayKey = useCallback((key, storageKey) => {
    setGatewayKey(key);
    const target = storageKey || gatewayKeyStorageKey(orgId);
    try {
      localStorage.setItem(target, key);
      if (target !== GATEWAY_KEY_STORAGE_LEGACY) {
        localStorage.removeItem(GATEWAY_KEY_STORAGE_LEGACY);
      }
    } catch {
      // localStorage may be unavailable in private mode.
    }
  }, [orgId]);

  // Authenticated fetch to gateway
  const gatewayFetch = useCallback(async (path, opts = {}) => {
    try {
      return await fetchGatewayPath({
        gatewayUrl,
        gatewayKey,
        path,
        opts,
        stream: false,
      });
    } catch (err) {
      const timedOut = err?.name === "TimeoutError" || /timeout/i.test(String(err?.message || ""));
      if (err?.name === "AbortError" || timedOut) {
        return {
          ok: false,
          status: 0,
          headers: null,
          data: {
            error: timedOut ? "timeout" : "aborted",
            code: timedOut ? "timeout" : "aborted",
            message: timedOut
              ? "Request timed out before the gateway responded."
              : "Request aborted (reset, timeout, or new burst).",
          },
          aborted: !timedOut,
          timedOut,
          isStream: false,
        };
      }
      throw err;
    }
  }, [gatewayUrl, gatewayKey]);

  /**
   * Explicit SSE path for simulators that exercise stream:true governance.
   * Reason: output-guard blocks arrive mid-stream; JSON fetch cannot observe them.
   */
  const gatewayFetchStream = useCallback(async (path, opts = {}) => {
    return fetchGatewayPath({
      gatewayUrl,
      gatewayKey,
      path,
      opts,
      stream: true,
    });
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

  // Health check polling
  const checkHealth = useCallback(async () => {
    let gwOk = false;
    let beOk = false;

    try {
      const res = await fetch(resolveGatewayHealthUrl(gatewayUrl), {
        cache: "no-store",
        signal: AbortSignal.timeout(5000),
      });
      const data = await res.json().catch(() => null);
      gwOk = res.ok;
      setGatewayHealth(data);
    } catch {
      setGatewayHealth(null);
    }

    try {
      const res = await fetch("/api/health/", {
        cache: "no-store",
        signal: AbortSignal.timeout(5000),
      });
      beOk = res.ok;
      setBackendHealth(beOk ? { status: "ok" } : null);
    } catch {
      setBackendHealth(null);
    }

    setConnectionStatus(gwOk && beOk ? "connected" : gwOk || beOk ? "degraded" : "disconnected");
  }, [gatewayUrl]);

  useEffect(() => {
    if (!isDocumentHidden()) checkHealth();
    healthRef.current = startVisibleInterval(checkHealth, HEALTH_POLL_INTERVAL);
    return () => {
      if (typeof healthRef.current === "function") healthRef.current();
    };
  }, [checkHealth]);

  // Reload cached key when org context becomes available or changes.
  useEffect(() => {
    if (!orgId) return;
    const cached = readStoredGatewayKey(orgId);
    if (cached) {
      setGatewayKey(cached);
    }
  }, [orgId]);

  // Lazy-provision per-org simulator key (POST returns plaintext once at creation).
  useEffect(() => {
    if (authLoading || !orgId) return;
    if (gatewayKey) return;
    const attemptKey = String(orgId);
    if (bootstrapAttemptedRef.current === attemptKey) return;
    bootstrapAttemptedRef.current = attemptKey;

    (async () => {
      try {
        const res = await fetchWithAuth("/api/gateways/simulator-default/", {
          method: "POST",
        });
        if (!res.ok) return;
        const data = await res.json();
        if (data?.key) {
          updateGatewayKey(data.key, data.storage_key);
        }
      } catch {
        // Simulator bootstrap is best-effort; manual entry still works.
      }
    })();
  }, [authLoading, fetchWithAuth, gatewayKey, orgId, updateGatewayKey]);

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
