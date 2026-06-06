import { useState, useEffect, useCallback } from "react";
import { useAuth } from "../context/AuthContext";
import { DEFAULT_VECTOR_PROVIDER } from "../constants/vectorProviders";

function parseProviderList(responseData) {
  if (Array.isArray(responseData)) return responseData;
  if (responseData && typeof responseData === "object") {
    if (Array.isArray(responseData.results)) return responseData.results;
    if (Array.isArray(responseData.data)) return responseData.data;
    return Object.values(responseData).filter(
      (item) => item && typeof item === "object" && item.provider_type,
    );
  }
  return [];
}

function isConfiguredProvider(cfg) {
  if (!cfg?.is_active) return false;
  return Boolean(cfg.api_key_set || (cfg.connection_url || "").trim());
}

/**
 * Org-level BYOK vector provider configs from the control plane.
 */
export function useVectorProviders() {
  const { fetchWithAuth } = useAuth();
  const [configs, setConfigs] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  const refresh = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await fetchWithAuth("/api/vector-providers/");
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const body = await res.json();
      setConfigs(parseProviderList(body));
    } catch (err) {
      setError(err.message);
      setConfigs([]);
    } finally {
      setLoading(false);
    }
  }, [fetchWithAuth]);

  useEffect(() => {
    refresh();
  }, [refresh]);

  const activeProviders = configs.filter(isConfiguredProvider);
  const hasConfiguredProvider = activeProviders.length > 0;
  const primaryProvider = activeProviders[0]?.provider_type || DEFAULT_VECTOR_PROVIDER;

  return {
    configs,
    activeProviders,
    hasConfiguredProvider,
    primaryProvider,
    loading,
    error,
    refresh,
  };
}
