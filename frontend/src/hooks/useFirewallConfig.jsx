/**
 * Shared firewall config for Module 1.5 — single GET /api/firewall/config/ with explicit invalidation.
 * Replaces refreshToken counters (T2 triage): panels subscribe and skip sync when locally dirty.
 */

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { useAuth } from "../context/AuthContext";

const FirewallConfigContext = createContext(null);

// Module-level stale-while-revalidate cache. The provider remounts every time
// the user navigates away from §1.5 and back; with a warm cache the page
// paints instantly from the last good config and only revalidates silently
// in the background instead of refetching with a full loading spinner.
let _configCache = null;

export function FirewallConfigProvider({ children }) {
  const { fetchWithAuth } = useAuth();
  const [config, setConfig] = useState(_configCache);
  const [loading, setLoading] = useState(_configCache == null);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState(null);

  // Unmount guard: load() resolves async, so without this a fetch that
  // completes after navigation would call setState on an unmounted provider.
  const mountedRef = useRef(true);
  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
    };
  }, []);

  const load = useCallback(
    async ({ silent = false } = {}) => {
      if (mountedRef.current) {
        if (silent) {
          setRefreshing(true);
        } else {
          setLoading(true);
        }
        setError(null);
      }
      try {
        const res = await fetchWithAuth("/api/firewall/config/");
        if (!res.ok) {
          if (mountedRef.current) setError("Failed to load firewall configuration.");
          return null;
        }
        const data = await res.json();
        _configCache = data;
        if (mountedRef.current) setConfig(data);
        return data;
      } catch {
        if (mountedRef.current) setError("Network error loading firewall configuration.");
        return null;
      } finally {
        if (mountedRef.current) {
          setLoading(false);
          setRefreshing(false);
        }
      }
    },
    [fetchWithAuth],
  );

  const invalidate = useCallback(() => load({ silent: true }), [load]);

  const mergeConfig = useCallback((data) => {
    if (data && typeof data === "object") {
      _configCache = data;
      if (mountedRef.current) setConfig(data);
    }
  }, []);

  useEffect(() => {
    // Stale-while-revalidate: with a warm cache, refresh silently (no spinner).
    load({ silent: _configCache != null });
  }, [load]);

  const value = useMemo(
    () => ({
      config,
      loading,
      refreshing,
      error,
      load,
      invalidate,
      mergeConfig,
      connectedModels: Array.isArray(config?.connected_models) ? config.connected_models : [],
      governanceStaleModels: Array.isArray(config?.governance_stale_models)
        ? config.governance_stale_models
        : [],
    }),
    [config, loading, refreshing, error, load, invalidate, mergeConfig],
  );

  return (
    <FirewallConfigContext.Provider value={value}>
      {children}
    </FirewallConfigContext.Provider>
  );
}

export function useFirewallConfig() {
  const ctx = useContext(FirewallConfigContext);
  if (!ctx) {
    throw new Error("useFirewallConfig must be used within FirewallConfigProvider");
  }
  return ctx;
}
