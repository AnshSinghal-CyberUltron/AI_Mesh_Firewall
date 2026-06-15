import { filterUserManagedModels, modelHasUsableKey } from "../constants/zeroshieldBrand";

/** Org-wide kill-switch scope — matches control KillSwitch.SCOPE_GLOBAL */
export const KILL_SWITCH_GLOBAL_SCOPE = "__global__";

export function normalizeApiKeyPrefix(prefix) {
  return String(prefix ?? "").trim();
}

/** Redis uses a single `kill_switch:{org}:global` key — prefix on DB row is not meaningful. */
export function isOrgGlobalKillSwitchReserved(killSwitches, editingId) {
  return killSwitches.some((ks) => {
    if (editingId != null && ks.id === editingId) {
      return false;
    }
    return ks.model_name === KILL_SWITCH_GLOBAL_SCOPE;
  });
}

/**
 * True when an existing kill-switch already targets this model at the same credential scope.
 * Matches DB unique_together (organization, model_name, api_key_prefix).
 */
export function isTargetScopeReserved(killSwitch, modelName, apiKeyPrefix, editingId) {
  if (!killSwitch || !modelName) {
    return false;
  }
  if (editingId != null && killSwitch.id === editingId) {
    return false;
  }
  if (modelName === KILL_SWITCH_GLOBAL_SCOPE) {
    return killSwitch.model_name === KILL_SWITCH_GLOBAL_SCOPE;
  }
  if (killSwitch.model_name !== modelName) {
    return false;
  }
  return normalizeApiKeyPrefix(killSwitch.api_key_prefix) === normalizeApiKeyPrefix(apiKeyPrefix);
}

/**
 * Gateway would block traffic to `candidateModel` for requests using `formApiKeyPrefix`.
 * Mirrors check_kill_switch precedence (credential + org_model + org_global), active rows only.
 */
export function wouldModelBeBlockedAsFallback(killSwitches, candidateModel, formApiKeyPrefix, editingId) {
  if (!candidateModel) {
    return false;
  }
  const formPrefix = normalizeApiKeyPrefix(formApiKeyPrefix);

  for (const ks of killSwitches) {
    if (editingId != null && ks.id === editingId) {
      continue;
    }
    if (!ks.is_active) {
      continue;
    }

    if (ks.model_name === KILL_SWITCH_GLOBAL_SCOPE) {
      return true;
    }

    if (ks.model_name !== candidateModel) {
      continue;
    }

    const ksPrefix = normalizeApiKeyPrefix(ks.api_key_prefix);
    if (ksPrefix === "" || ksPrefix === formPrefix) {
      return true;
    }
  }

  return false;
}

export function filterConnectedModels(models) {
  const list = Array.isArray(models) ? models : models?.results ?? [];
  return filterUserManagedModels(list).filter(
    (m) => m && m.model_name && m.is_active !== false && modelHasUsableKey(m),
  );
}

function optionLabel(m) {
  const provider = m.provider_display || m.provider || "";
  return provider ? `${m.model_name} (${provider})` : m.model_name;
}

/**
 * Models eligible as kill-switch target (create: exclude reserved scopes; edit: keep current).
 */
export function buildKillSwitchTargetOptions({
  connectedModels,
  killSwitches,
  apiKeyPrefix,
  editingId,
  currentModelName,
  isCreate,
}) {
  const connected = filterConnectedModels(connectedModels);
  const options = [];
  const globalReserved = isCreate && isOrgGlobalKillSwitchReserved(killSwitches, editingId);

  if (isCreate) {
    options.push({
      value: KILL_SWITCH_GLOBAL_SCOPE,
      label: "All models — org-wide emergency",
      modelId: "",
      disabled: globalReserved,
      disabledReason: globalReserved ? "Org-wide emergency switch already exists" : "",
    });
  }

  for (const m of connected) {
    const name = m.model_name;
    const reserved =
      isCreate
      && killSwitches.some((ks) => isTargetScopeReserved(ks, name, apiKeyPrefix, editingId));
    const isCurrentEdit = !isCreate && name === currentModelName;
    if (reserved && !isCurrentEdit) {
      continue;
    }
    options.push({
      value: name,
      label: optionLabel(m),
      modelId: m.model_id || "",
      disabled: false,
      disabledReason: "",
    });
  }

  if (
    currentModelName
    && currentModelName !== KILL_SWITCH_GLOBAL_SCOPE
    && !options.some((o) => o.value === currentModelName)
  ) {
    options.unshift({
      value: currentModelName,
      label: `${currentModelName} (current — not in connected list)`,
      modelId: "",
      disabled: false,
      disabledReason: "",
    });
  }

  if (
    isCreate
    && currentModelName === KILL_SWITCH_GLOBAL_SCOPE
    && !options.some((o) => o.value === KILL_SWITCH_GLOBAL_SCOPE)
  ) {
    options.unshift({
      value: KILL_SWITCH_GLOBAL_SCOPE,
      label: "All models — org-wide emergency",
      modelId: "",
      disabled: globalReserved,
      disabledReason: globalReserved ? "Org-wide emergency switch already exists" : "",
    });
  }

  return options;
}

/**
 * Models eligible as reroute fallback: connected, not target, not gateway-blocked as fallback.
 */
export function buildKillSwitchFallbackOptions({
  connectedModels,
  killSwitches,
  apiKeyPrefix,
  targetModelName,
  editingId,
  currentFallbackModel,
  isCreate,
}) {
  if (!targetModelName || targetModelName === KILL_SWITCH_GLOBAL_SCOPE) {
    return [];
  }

  const connected = filterConnectedModels(connectedModels);
  const options = [];

  for (const m of connected) {
    const name = m.model_name;
    if (name === targetModelName) {
      continue;
    }
    const blocked = wouldModelBeBlockedAsFallback(
      killSwitches,
      name,
      apiKeyPrefix,
      editingId,
    );
    if (isCreate && blocked && name !== currentFallbackModel) {
      continue;
    }
    if (
      isCreate
      && killSwitches.some((ks) => isTargetScopeReserved(ks, name, apiKeyPrefix, editingId))
      && name !== currentFallbackModel
    ) {
      continue;
    }
    options.push({
      value: name,
      label: optionLabel(m),
      modelId: m.model_id || "",
      disabled: false,
      disabledReason: "",
    });
  }

  if (
    currentFallbackModel
    && currentFallbackModel !== targetModelName
    && !options.some((o) => o.value === currentFallbackModel)
  ) {
    options.unshift({
      value: currentFallbackModel,
      label: `${currentFallbackModel} (current — not in connected list)`,
      modelId: "",
      disabled: false,
      disabledReason: "",
    });
  }

  return options;
}
