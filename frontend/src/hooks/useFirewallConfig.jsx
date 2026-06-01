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
  useState,
} from "react";
import { useAuth } from "../context/AuthContext";

const FirewallConfigContext = createContext(null);

export function FirewallConfigProvider({ children }) {
  const { fetchWithAuth } = useAuth();
  const [config, setConfig] = useState(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState(null);

  const load = useCallback(
    async ({ silent = false } = {}) => {
      if (silent) {
        setRefreshing(true);
      } else {
        setLoading(true);
      }
      setError(null);
      try {
        const res = await fetchWithAuth("/api/firewall/config/");
        if (!res.ok) {
          setError("Failed to load firewall configuration.");
          return null;
        }
        const data = await res.json();
        setConfig(data);
        return data;
      } catch {
        setError("Network error loading firewall configuration.");
        return null;
      } finally {
        setLoading(false);
        setRefreshing(false);
      }
    },
    [fetchWithAuth],
  );

  const invalidate = useCallback(() => load({ silent: true }), [load]);

  const mergeConfig = useCallback((data) => {
    if (data && typeof data === "object") {
      setConfig(data);
    }
  }, []);

  useEffect(() => {
    load({ silent: false });
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
