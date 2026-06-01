import { useState, useEffect, useCallback, useMemo } from "react";
import { useAuth } from "../context/AuthContext";
import { filterUserManagedModels } from "../constants/zeroshieldBrand";
import { mergeModelsIntoFirewallAllowlist, parseAllowedModels } from "../utils/firewallAllowlist";

export const SIMULATOR_MODEL_STORAGE_KEY = "zeroshield_simulator_model";

function parseModelList(data) {
  if (Array.isArray(data)) return data;
  return data?.results ?? [];
}

/** Gateway-connected org models eligible for live simulators (active + API key set). */
export function filterSimulatorEligibleModels(models) {
  return filterUserManagedModels(models).filter(
    (m) => m.is_active !== false && m.api_key_set,
  );
}

/**
 * Loads org models from the control plane and picks a simulator default:
 * - 0 models: none (caller should block execute)
 * - 1 model: auto-use that model_name
 * - 2+: dropdown; persists choice in localStorage; falls back to firewall default_model
 */
export function useSimulatorGatewayModels() {
  const { fetchWithAuth } = useAuth();
  const [allModels, setAllModels] = useState([]);
  const [firewallDefault, setFirewallDefault] = useState("");
  const [modelIsolationEnabled, setModelIsolationEnabled] = useState(false);
  const [allowedModels, setAllowedModels] = useState([]);
  const [selectedModel, setSelectedModel] = useState("");
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState(null);
  const [allowlistSyncing, setAllowlistSyncing] = useState(false);
  const [allowlistSyncError, setAllowlistSyncError] = useState(null);

  const eligibleModels = useMemo(
    () => filterSimulatorEligibleModels(allModels),
    [allModels],
  );

  const eligibleNames = useMemo(
    () => eligibleModels.map((m) => m.model_name).filter(Boolean),
    [eligibleModels],
  );

  const refresh = useCallback(async () => {
    setLoading(true);
    setLoadError(null);
    try {
      const [modelsRes, configRes] = await Promise.all([
        fetchWithAuth("/api/firewall/models/"),
        fetchWithAuth("/api/firewall/config/"),
      ]);
      if (modelsRes.ok) {
        setAllModels(parseModelList(await modelsRes.json()));
      } else {
        setAllModels([]);
        setLoadError("Could not load connected models.");
      }
      if (configRes.ok) {
        const cfg = await configRes.json();
        setFirewallDefault(String(cfg.default_model || "").trim());
        setModelIsolationEnabled(Boolean(cfg.model_isolation_enabled));
        setAllowedModels(parseAllowedModels(cfg.allowed_models));
      }
    } catch {
      setAllModels([]);
      setLoadError("Network error loading models.");
    } finally {
      setLoading(false);
    }
  }, [fetchWithAuth]);

  useEffect(() => {
    refresh();
  }, [refresh]);

  useEffect(() => {
    if (loading) return;

    if (eligibleNames.length === 0) {
      setSelectedModel("");
      return;
    }

    if (eligibleNames.length === 1) {
      setSelectedModel(eligibleNames[0]);
      return;
    }

    const stored = (() => {
      try {
        return localStorage.getItem(SIMULATOR_MODEL_STORAGE_KEY) || "";
      } catch {
        return "";
      }
    })();

    if (stored && eligibleNames.includes(stored)) {
      setSelectedModel(stored);
      return;
    }
    if (firewallDefault && eligibleNames.includes(firewallDefault)) {
      setSelectedModel(firewallDefault);
      return;
    }
    setSelectedModel(eligibleNames[0]);
  }, [loading, eligibleNames, firewallDefault]);

  const setSimulatorModel = useCallback((modelName) => {
    setSelectedModel(modelName);
    if (eligibleNames.length > 1 && modelName) {
      try {
        localStorage.setItem(SIMULATOR_MODEL_STORAGE_KEY, modelName);
      } catch {
        /* ignore quota / private mode */
      }
    }
  }, [eligibleNames.length]);

  const effectiveModel = selectedModel || (eligibleNames.length === 1 ? eligibleNames[0] : "");

  const allowlistBlocksSimulator = Boolean(
    modelIsolationEnabled
    && allowedModels.length > 0
    && effectiveModel
    && !allowedModels.includes(effectiveModel),
  );

  const syncSelectedToAllowlist = useCallback(async () => {
    const names = effectiveModel ? [effectiveModel] : eligibleNames;
    if (!names.length) return { updated: false };
    setAllowlistSyncing(true);
    setAllowlistSyncError(null);
    try {
      const result = await mergeModelsIntoFirewallAllowlist(fetchWithAuth, names);
      await refresh();
      return result;
    } catch (err) {
      setAllowlistSyncError(err.message || "Allowlist update failed");
      throw err;
    } finally {
      setAllowlistSyncing(false);
    }
  }, [effectiveModel, eligibleNames, fetchWithAuth, refresh]);

  return {
    eligibleModels,
    selectedModel: effectiveModel,
    setSelectedModel: setSimulatorModel,
    firewallDefault,
    modelIsolationEnabled,
    allowedModels,
    allowlistBlocksSimulator,
    syncSelectedToAllowlist,
    allowlistSyncing,
    allowlistSyncError,
    loading,
    loadError,
    refresh,
    hasModels: eligibleNames.length > 0,
    isSingleModel: eligibleNames.length === 1,
    showModelPicker: eligibleNames.length > 1,
  };
}
