/**
 * Helpers for firewall global allowlist (comma-separated string in API / list in gateway Redis).
 */

export function parseAllowedModels(value) {
  if (Array.isArray(value)) {
    return value.map((m) => String(m).trim()).filter(Boolean);
  }
  if (typeof value === "string") {
    return value.split(",").map((m) => m.trim()).filter(Boolean);
  }
  return [];
}

export function formatAllowedModelsForApi(names) {
  return [...new Set(names.map((m) => String(m).trim()).filter(Boolean))].join(", ");
}

/**
 * Merge model names into org firewall allowed_models via PUT /api/firewall/config/.
 * Returns { updated, allowedModels, defaultModelUpdated }.
 */
export async function mergeModelsIntoFirewallAllowlist(fetchWithAuth, modelNames) {
  const incoming = [...new Set((modelNames || []).map((m) => String(m).trim()).filter(Boolean))];
  if (!incoming.length) {
    return { updated: false, allowedModels: [], defaultModelUpdated: false };
  }

  const configRes = await fetchWithAuth("/api/firewall/config/");
  if (!configRes.ok) {
    throw new Error("Could not load firewall configuration.");
  }
  const cfg = await configRes.json();
  const current = parseAllowedModels(cfg.allowed_models);
  const missing = incoming.filter((name) => !current.includes(name));
  const payload = {};

  if (missing.length > 0) {
    payload.allowed_models = formatAllowedModelsForApi([...current, ...missing]);
  }

  const defaultModel = String(cfg.default_model || "").trim();
  if (!defaultModel && incoming[0]) {
    payload.default_model = incoming[0];
  }

  if (!Object.keys(payload).length) {
    return { updated: false, allowedModels: current, defaultModelUpdated: false };
  }

  const putRes = await fetchWithAuth("/api/firewall/config/", {
    method: "PUT",
    body: JSON.stringify(payload),
  });
  if (!putRes.ok) {
    const err = await putRes.json().catch(() => ({}));
    throw new Error(err.detail || err.message || "Failed to update allowed models.");
  }

  const nextCfg = await putRes.json();
  return {
    updated: true,
    allowedModels: parseAllowedModels(nextCfg.allowed_models),
    defaultModelUpdated: Boolean(payload.default_model),
  };
}
