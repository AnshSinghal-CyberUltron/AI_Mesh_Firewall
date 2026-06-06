import { useState, useEffect, useRef, useCallback } from "react";
import { useAuth } from "../context/AuthContext";
import { useGatewayConfig } from "./useGatewayConfig";

// Org-scoped storage prevents one org's simulator key leaking to another on a
// shared browser. Mirrors the keys written by useSimulatorEngine so the two
// hooks share the same provisioned key via localStorage.
const LEGACY_KEY = "zeroshield_gateway_key";

function gatewayKeyStorageKey(orgId) {
  return orgId ? `zeroshield_gateway_key:${orgId}` : LEGACY_KEY;
}

function readStoredGatewayKey(orgId) {
  try {
    const scoped = orgId ? localStorage.getItem(gatewayKeyStorageKey(orgId)) : "";
    if (scoped) return scoped;
    return localStorage.getItem(LEGACY_KEY) || "";
  } catch {
    return "";
  }
}

// Module-level de-dupe: if several panels mount at once with an empty cache,
// they share ONE provision request instead of each POSTing (which, with
// ?ensure=1 rotation, would churn keys and race localStorage).
const _inflightProvision = new Map(); // orgId -> Promise<string|null>

function provisionOrgKey(orgId, fetchWithAuth) {
  if (_inflightProvision.has(orgId)) return _inflightProvision.get(orgId);
  const promise = (async () => {
    const res = await fetchWithAuth("/api/gateways/simulator-default/?ensure=1", {
      method: "POST",
    });
    if (!res.ok) throw new Error(`provision failed (${res.status})`);
    const data = await res.json();
    if (!data?.key) return data?.has_gateway_key ? null : null;
    try {
      localStorage.setItem(data.storage_key || gatewayKeyStorageKey(orgId), data.key);
      localStorage.removeItem(LEGACY_KEY);
    } catch {
      // localStorage unavailable (private mode); key stays in memory only.
    }
    return data.key;
  })();
  _inflightProvision.set(orgId, promise);
  // Drop the cache entry once settled so a future (post-cache-clear) provision
  // can run; concurrent callers during this window still share the one promise.
  promise.finally(() => _inflightProvision.delete(orgId)).catch(() => {});
  return promise;
}

/**
 * Lightweight gateway credential for simulator / connection panels.
 *
 * Auto-provisions the per-organization "simulator" gateway key
 * (POST /api/gateways/simulator-default/) and resolves the gateway URL by
 * environment (prod = aimeshgateway.zeroshield.ai, local = local gateway port).
 * Panels consume this instead of asking the user to paste a key/URL.
 *
 * Unlike useSimulatorEngine, this does NOT run health polling or the scenario
 * engine — it is just the credential + URL, so cheap test panels can adopt it
 * without inheriting a 15s poll loop.
 *
 * Returns { gatewayUrl, gatewayKey, ready, provisioning, error }.
 */
export function useGatewayCredential() {
  const { gatewayUrl } = useGatewayConfig();
  const { fetchWithAuth, user, loading: authLoading } = useAuth();
  const orgId = user?.organization?.id;

  const [gatewayKey, setGatewayKey] = useState(() => readStoredGatewayKey(orgId));
  const [provisioning, setProvisioning] = useState(false);
  const [error, setError] = useState(null);
  const attemptedRef = useRef(null);

  // Adopt an existing org-scoped key once the org becomes known (e.g. provisioned
  // earlier by useSimulatorEngine or another panel in this browser).
  useEffect(() => {
    if (!orgId) return;
    const cached = readStoredGatewayKey(orgId);
    if (cached) setGatewayKey(cached);
  }, [orgId]);

  // Lazily provision the per-org simulator key. The backend returns plaintext
  // once on creation; we then cache it org-scoped. ?ensure=1 asks the backend to
  // (re)issue a usable key even if one already exists but isn't cached here.
  useEffect(() => {
    if (authLoading || !orgId || gatewayKey) return;
    const attemptKey = String(orgId);
    if (attemptedRef.current === attemptKey) return;
    attemptedRef.current = attemptKey;

    setProvisioning(true);
    setError(null);
    provisionOrgKey(orgId, fetchWithAuth)
      .then((key) => {
        if (key) setGatewayKey(key);
        else setError("Simulator key could not be retrieved automatically. Try refreshing.");
      })
      .catch(() => setError("Could not provision the simulator gateway key automatically."))
      .finally(() => setProvisioning(false));
  }, [authLoading, fetchWithAuth, gatewayKey, orgId]);

  // Force a fresh provision after the cached key was disabled server-side (e.g.
  // another session rotated it). Clears the org cache + the in-flight de-dupe so
  // a new ensure=1 POST runs, and returns the fresh key. Used by gatewayFetch's
  // bounded self-heal — NOT a retry loop (the caller retries at most once).
  const reprovision = useCallback(async () => {
    if (!orgId) return null;
    try {
      localStorage.removeItem(gatewayKeyStorageKey(orgId));
      localStorage.removeItem(LEGACY_KEY);
    } catch {
      // localStorage unavailable; proceed with the network re-provision.
    }
    attemptedRef.current = null;
    try {
      const key = await provisionOrgKey(orgId, fetchWithAuth);
      if (key) {
        setGatewayKey(key);
        setError(null);
      }
      return key || null;
    } catch {
      return null;
    }
  }, [orgId, fetchWithAuth]);

  return {
    gatewayUrl,
    gatewayKey,
    reprovision,
    ready: Boolean(gatewayUrl && gatewayKey),
    provisioning,
    error,
  };
}
