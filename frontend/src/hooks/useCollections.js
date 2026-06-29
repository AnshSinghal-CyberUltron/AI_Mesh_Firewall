import { useState, useEffect, useCallback } from "react";
import { useAuth } from "../context/AuthContext";

const COLLECTIONS_URL = "/api/admin/gateway/rag/collections/";
const COOLDOWN_MS = 60_000;

let cache = {
  data: [],
  nested: {},
  ragAvailable: null,
  reason: null,
  error: null,
  status: "idle",
  fetchedAt: 0,
  inflight: null,
  cooldownUntil: 0,
};

const listeners = new Set();

function notify() {
  listeners.forEach((listener) => listener());
}

function flattenCollections(nested) {
  return Object.entries(nested || {}).flatMap(([provider, items]) =>
    (items || []).map((item) => ({
      provider,
      name: typeof item === "string" ? item : item.name || String(item),
    })),
  );
}

async function fetchCollectionsShared(fetchWithAuth, { force = false } = {}) {
  const now = Date.now();
  if (!force && cache.status === "ready" && now - cache.fetchedAt < COOLDOWN_MS) {
    return cache;
  }
  if (!force && now < cache.cooldownUntil) {
    return cache;
  }
  if (cache.inflight) {
    return cache.inflight;
  }

  cache.status = "loading";
  notify();

  cache.inflight = (async () => {
    try {
      const res = await fetchWithAuth(COLLECTIONS_URL);
      let body = null;
      try {
        body = await res.json();
      } catch {
        body = null;
      }

      const data = body?.data || {};
      if (data.rag_available === false || data.reason === "no_provider_configured") {
        cache.data = [];
        cache.nested = {};
        cache.ragAvailable = false;
        cache.reason = data.reason || "no_provider_configured";
        cache.error = null;
        cache.status = "ready";
        cache.fetchedAt = Date.now();
        return cache;
      }

      if (!res.ok) {
        throw new Error(`HTTP ${res.status}`);
      }
      if (!body || body.status !== "ok") {
        throw new Error(body?.data?.message || body?.data?.error || "proxy error");
      }

      const nested = data.collections || {};
      cache.nested = nested;
      cache.data = flattenCollections(nested);
      cache.ragAvailable = data.rag_available !== false;
      cache.reason = null;
      cache.error = null;
      cache.status = "ready";
      cache.fetchedAt = Date.now();
      return cache;
    } catch (err) {
      if (String(err.message || "").includes("503")) {
        cache.cooldownUntil = Date.now() + COOLDOWN_MS;
      }
      cache.error = err.message;
      cache.status = "error";
      return cache;
    } finally {
      cache.inflight = null;
      notify();
    }
  })();

  return cache.inflight;
}

/**
 * Shared hook for fetching vector DB collections through the Django admin
 * proxy. Dedupes parallel mounts and treats unconfigured BYOK as empty.
 */
export function useCollections({ enabled = true } = {}) {
  const { fetchWithAuth } = useAuth();
  const [, setTick] = useState(0);

  useEffect(() => {
    const listener = () => setTick((value) => value + 1);
    listeners.add(listener);
    return () => listeners.delete(listener);
  }, []);

  const syncFromCache = useCallback(() => {
    if (!enabled) {
      return {
        collections: [],
        nested: {},
        loading: false,
        error: null,
        ragAvailable: false,
        reason: "disabled",
        skipped: true,
      };
    }
    return {
      collections: cache.data,
      nested: cache.nested,
      loading: cache.status === "loading",
      error: cache.error,
      ragAvailable: cache.ragAvailable,
      reason: cache.reason,
      skipped: false,
    };
  }, [enabled]);

  const fetchCollections = useCallback(
    async (options = {}) => {
      if (!enabled) return;
      await fetchCollectionsShared(fetchWithAuth, options);
    },
    [enabled, fetchWithAuth],
  );

  useEffect(() => {
    if (!enabled) return undefined;
    fetchCollections();
    return undefined;
  }, [enabled, fetchCollections]);

  const state = syncFromCache();

  return {
    ...state,
    refresh: () => fetchCollections({ force: true }),
  };
}
