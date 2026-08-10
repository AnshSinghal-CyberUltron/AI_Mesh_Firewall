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
// they share ONE provision request instead of each POSTing. The endpoint is
// idempotent (no ?ensure=1): it returns the org's EXISTING active key and only
// includes a plaintext `key` when one was newly created — it never rotates.
// Entries carry a TTL so a never-settling promise (hung fetch, dropped
// network) cannot block retries for that org forever.
const _inflightProvision = new Map(); // orgId -> { promise, startedAt }
const INFLIGHT_PROVISION_TTL_MS = 30_000;

function provisionOrgKey(orgId, fetchWithAuth) {
  const existing = _inflightProvision.get(orgId);
  if (existing && Date.now() - existing.startedAt < INFLIGHT_PROVISION_TTL_MS) {
    return existing.promise;
  }
  const promise = (async () => {
    const res = await fetchWithAuth("/api/gateways/simulator-default/", {
      method: "POST",
    });
    if (!res.ok) throw new Error(`provision failed (${res.status})`);
    const data = await res.json();
    if (!data?.key) {
      // No plaintext key in the response (regardless of has_gateway_key):
      // the backend only returns plaintext on creation / recoverable fetch,
      // so there is nothing to cache — callers surface the refresh hint.
      return null;
    }
    try {
      localStorage.setItem(data.storage_key || gatewayKeyStorageKey(orgId), data.key);
      localStorage.removeItem(LEGACY_KEY);
    } catch {
      // localStorage unavailable (private mode); key stays in memory only.
    }
    return data.key;
  })();
  const entry = { promise, startedAt: Date.now() };
  _inflightProvision.set(orgId, entry);
  // Drop the cache entry once settled so a future (post-cache-clear) provision
  // can run; concurrent callers during this window still share the one promise.
  // Only delete if OUR entry is still cached — a TTL-expired retry may have
  // replaced it, and the stale promise must not evict the fresh one.
  promise
    .finally(() => {
      if (_inflightProvision.get(orgId) === entry) {
        _inflightProvision.delete(orgId);
      }
    })
    .catch(() => {});
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
  // once on creation; we then cache it org-scoped. The endpoint is idempotent —
  // it returns the org's existing active key and never rotates.
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

  // Re-FETCH the org's current key after the cached one looked disabled/invalid
  // (e.g. a stale value in this browser). The endpoint is idempotent and never
  // rotates, so this just re-reads the org's active key and retries once. We do
  // NOT clear the cache or re-mint — that would churn keys and race other tabs.
  // Used by gatewayFetch's bounded self-heal (the caller retries at most once).
  const reprovision = useCallback(async () => {
    if (!orgId) return null;
    // Honor Module 2 Disable Key: do not POST simulator-default (would mint a
    // new active key and defeat Auth-stage containment in Attack Simulator).
    try {
      const { isSimulatorKeyReprovisionSuppressed } = await import("../utils/containmentEvents.js");
      if (isSimulatorKeyReprovisionSuppressed(readStoredGatewayKey(orgId))) {
        return null;
      }
    } catch {
      /* ignore */
    }
    try {
      const key = await provisionOrgKey(orgId, fetchWithAuth);
      if (key) {
        setGatewayKey(key);
        setError(null);
      }
      return key || readStoredGatewayKey(orgId) || null;
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
