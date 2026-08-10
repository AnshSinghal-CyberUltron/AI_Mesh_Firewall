/** Kill-switch and gateway key control APIs for API Key & Identity Risk analyst response actions.
 *
 * Uses Module 1 APIs already on main:
 * - GET/POST /api/kill-switches/ + activate/deactivate (per-model, optional api_key_prefix)
 * - PATCH /api/gateways/keys/{id}/ {"is_active": bool} for full-credential containment
 *
 * Credential-wide "__credential__" model scope is intentionally NOT used (not on main).
 * Gateway matches Redis key kill_switch:{org}:credential:{prefix}:model:{client_requested_model}
 * against the pre-routing body.model — not the post-routing served model.
 */

import {
  clearSimulatorKeyReprovisionSuppress,
  notifyContainmentChanged,
  suppressSimulatorKeyReprovision,
} from "../utils/containmentEvents.js";

/** Must match useSimulatorGatewayModels.SIMULATOR_MODEL_STORAGE_KEY. */
const SIMULATOR_MODEL_STORAGE_KEY = "zeroshield_simulator_model";

/** Legacy scope that never matches a real chat model in gateway Redis lookup. */
export const LEGACY_CREDENTIAL_KILL_MODEL = "__credential__";

/**
 * True when the gateway can enforce this kill-switch model_name against chat traffic.
 * `__credential__` writes Redis …:model:__credential__ and never matches client models.
 */
export function isGatewayEnforcedKillModel(modelName) {
  const name = String(modelName || "").trim();
  if (!name) return false;
  if (name === LEGACY_CREDENTIAL_KILL_MODEL) return false;
  return true;
}

export function filterEnforcedKillSwitches(killSwitches) {
  const rows = Array.isArray(killSwitches) ? killSwitches : killSwitches?.results || [];
  return rows.filter((ks) => ks?.is_active !== false && isGatewayEnforcedKillModel(ks?.model_name));
}

/**
 * Build a per-model kill-switch payload for main's KillSwitch API.
 * Full-key containment must use setGatewayKeyActive (disable key), not this helper.
 */
export function buildCredentialKillSwitchPayload({
  modelName,
  apiKeyPrefix,
  reason,
  action = "disable",
  fallbackModel = "",
}) {
  const prefix = String(apiKeyPrefix || "").trim();
  const resolvedModel = String(modelName || "").trim();
  const resolvedAction = action === "reroute" ? "reroute" : "disable";
  const resolvedFallbackModel = String(fallbackModel || "").trim();
  if (!resolvedModel) {
    throw new Error(
      "model_name is required for kill-switch; use Disable API key for all-model containment",
    );
  }
  if (resolvedModel === LEGACY_CREDENTIAL_KILL_MODEL) {
    throw new Error(
      "Legacy __credential__ kill scope is not enforced by the gateway; pick a real model or Disable API key",
    );
  }
  if (resolvedAction === "reroute" && !resolvedFallbackModel) {
    throw new Error("fallback_model is required when kill-switch action is reroute");
  }
  const payload = {
    model_name: resolvedModel,
    action: resolvedAction,
    reason: String(reason || "").trim() || "SOC API key containment (analyst)",
    fallback_model: resolvedAction === "reroute" ? resolvedFallbackModel : "",
  };
  if (prefix) payload.api_key_prefix = prefix;
  return payload;
}

/** Prefer first top_models entry, then models list, for per-model kill-switch. */
export function resolveTopModelName(behaviorOrRow) {
  if (!behaviorOrRow) return "";
  const top = behaviorOrRow.top_models;
  if (Array.isArray(top) && top.length) {
    const first = top[0];
    if (Array.isArray(first) && first[0]) return String(first[0]).trim();
    if (typeof first === "string") return first.trim();
  }
  const models = behaviorOrRow.models;
  if (Array.isArray(models) && models.length) {
    const sorted = [...models].map(String).filter(Boolean).sort();
    if (sorted[0]) return sorted[0].trim();
  }
  if (typeof behaviorOrRow.model === "string" && behaviorOrRow.model.trim()) {
    return behaviorOrRow.model.trim();
  }
  return "";
}

export function deriveAllowedModelsForKey(behaviorOrRow) {
  if (!behaviorOrRow) return [];
  const ordered = [];
  const seen = new Set();
  const pushModel = (value) => {
    const model = String(value || "").trim();
    if (!model || seen.has(model) || model === LEGACY_CREDENTIAL_KILL_MODEL) return;
    seen.add(model);
    ordered.push(model);
  };

  const top = behaviorOrRow.top_models;
  if (Array.isArray(top)) {
    top.forEach((entry) => {
      if (Array.isArray(entry)) pushModel(entry[0]);
      else pushModel(entry);
    });
  }

  const models = behaviorOrRow.models;
  if (Array.isArray(models)) {
    models.forEach((model) => pushModel(model));
  }

  const requests = behaviorOrRow.recent_requests;
  if (Array.isArray(requests)) {
    requests.forEach((req) => pushModel(req?.model));
  }

  pushModel(behaviorOrRow.model);
  return ordered;
}

/** Read Attack Simulator's currently selected model (client-requested body.model). */
export function readPreferredSimulatorModel() {
  try {
    return String(localStorage.getItem(SIMULATOR_MODEL_STORAGE_KEY) || "").trim();
  } catch {
    return "";
  }
}

/**
 * Merge telemetry models with org gateway catalog + preferred simulator model.
 * Preferred / catalog models are listed first so operators kill the client-requested
 * name (what gateway checks), not only the post-routing served model from UEBA.
 */
export function mergeKillSwitchModelCandidates({
  telemetryRow = null,
  gatewayModelNames = [],
  preferredModel = "",
} = {}) {
  const ordered = [];
  const seen = new Set();
  const pushModel = (value) => {
    const model = String(value || "").trim();
    if (!model || seen.has(model) || model === LEGACY_CREDENTIAL_KILL_MODEL) return;
    seen.add(model);
    ordered.push(model);
  };

  pushModel(preferredModel);
  (Array.isArray(gatewayModelNames) ? gatewayModelNames : []).forEach(pushModel);
  deriveAllowedModelsForKey(telemetryRow).forEach(pushModel);
  return ordered;
}

/** Load active org LLM model_name values from Module 1 firewall models API. */
export async function fetchGatewayModelNames(fetchWithAuth) {
  if (typeof fetchWithAuth !== "function") return [];
  try {
    const res = await fetchWithAuth("/api/firewall/models/");
    if (!res?.ok) return [];
    const data = await res.json().catch(() => []);
    const rows = Array.isArray(data) ? data : data?.results || [];
    const names = [];
    const seen = new Set();
    for (const row of rows) {
      if (row?.is_active === false) continue;
      const name = String(row?.model_name || "").trim();
      if (!name || seen.has(name)) continue;
      seen.add(name);
      names.push(name);
    }
    return names;
  } catch {
    return [];
  }
}

/**
 * Resolve the org's current ACTIVE simulator key via Module 1
 * GET /api/gateways/simulator-default/ (active-only; never returns inactive prefixes).
 */
export async function fetchActiveSimulatorContext(fetchWithAuth) {
  if (typeof fetchWithAuth !== "function") return null;
  try {
    const res = await fetchWithAuth("/api/gateways/simulator-default/");
    if (!res?.ok) return null;
    const data = await res.json().catch(() => ({}));
    if (!data?.has_gateway_key && !data?.prefix) return null;
    const prefix = String(data.prefix || "").trim();
    if (!prefix) return null;
    return {
      prefix,
      keyId: String(data.key_id || "").trim(),
      name: String(data.name || "simulator").trim() || "simulator",
      hasGatewayKey: data.has_gateway_key !== false,
    };
  } catch {
    return null;
  }
}

export function isSimulatorKeyRow(row, simulatorCtx = null) {
  if (!row) return false;
  const name = String(row.name || "").trim().toLowerCase();
  if (name === "simulator" || name === "simulator-default") return true;
  const prefix = String(row.prefix || "").trim();
  const livePrefix = String(simulatorCtx?.prefix || "").trim();
  if (prefix && livePrefix && prefix === livePrefix) return true;
  const keyId = String(row.key_id || row.id || "").trim();
  const liveId = String(simulatorCtx?.keyId || "").trim();
  return Boolean(keyId && liveId && keyId === liveId);
}

/**
 * Validate a kill-switch target against Module 1's active simulator contract.
 * Returns { ok:true } or { ok:false, error, activeSimulator }.
 *
 * When targeting a simulator-named / stored-simulator row that is inactive or
 * stale, refuse activation — Attack Simulator uses only the active prefix from
 * GET /api/gateways/simulator-default/, so a kill on a stale prefix never fires.
 */
export function validateKillSwitchTarget({
  row,
  activeSimulator = null,
  requireActiveKey = true,
} = {}) {
  if (!row) {
    return { ok: false, error: "No API key selected for kill switch.", activeSimulator };
  }
  const prefix = String(row.prefix || "").trim();
  if (!prefix) {
    return { ok: false, error: "API key prefix is required for credential-scoped kill switch.", activeSimulator };
  }
  if (requireActiveKey && row.is_active === false) {
    const live = activeSimulator?.prefix;
    if (isSimulatorKeyRow(row, activeSimulator) && live && live !== prefix) {
      return {
        ok: false,
        error: (
          `This simulator key (${prefix}) is disabled. Attack Simulator uses active prefix `
          + `${live}. Activate the kill switch on that live key, or re-enable this key first.`
        ),
        activeSimulator,
      };
    }
    return {
      ok: false,
      error: (
        `API key ${prefix} is disabled. Kill switch cannot be verified on disabled credentials `
        + "(Disable already stops traffic at Auth). Re-enable the key first, or pick the active key."
      ),
      activeSimulator,
    };
  }
  if (isSimulatorKeyRow(row, activeSimulator) && activeSimulator?.prefix) {
    const live = String(activeSimulator.prefix).trim();
    if (live && live !== prefix) {
      return {
        ok: false,
        error: (
          `Stale simulator key ${prefix}. Module 1 active simulator prefix is ${live}. `
          + "Activate kill switch on the live simulator key so Attack Simulator traffic matches."
        ),
        activeSimulator,
      };
    }
  }
  return { ok: true, activeSimulator };
}

/** Human-readable containment contract for Module 2 UI copy. */
export function describeContainmentSemantics() {
  return {
    killSwitch: (
      "Kill switch blocks only requests that send the exact client-requested model "
      + "with this API key prefix. Gateway stops them at the Kill Switch pipeline stage (HTTP 503)."
    ),
    disableKey: (
      "Disable API key rejects ALL traffic for this credential at Auth (HTTP 403). "
      + "Requests never reach Input Scan or Kill Switch."
    ),
  };
}

export function buildAnalystKillSwitchReason(behavior) {
  if (!behavior) {
    return "SOC API key containment (analyst)";
  }
  return (
    `Analyst containment — ${behavior.risk_band} risk (score ${behavior.risk_score}): `
    + `block ${behavior.block_rate_pct ?? 0}%, velocity ${behavior.velocity_spike ?? 1}x`
  );
}

export function filterKillSwitchesForPrefix(killSwitches, prefix) {
  const normalized = String(prefix || "").trim();
  if (!normalized) return [];
  const rows = Array.isArray(killSwitches) ? killSwitches : killSwitches?.results || [];
  return rows.filter((ks) => String(ks.api_key_prefix || "").trim() === normalized);
}

export function findKillSwitchForPayload(killSwitches, payload) {
  const modelName = String(payload?.model_name || "").trim();
  const prefix = String(payload?.api_key_prefix || "").trim();
  const rows = Array.isArray(killSwitches) ? killSwitches : killSwitches?.results || [];
  return rows.find(
    (ks) => String(ks.model_name || "").trim() === modelName
      && String(ks.api_key_prefix || "").trim() === prefix,
  ) || null;
}

export function createKillSwitchApi(fetchWithAuth) {
  function formatApiError(data, fallback = "Request failed") {
    if (!data) return fallback;
    if (typeof data.detail === "string") return data.detail;
    if (Array.isArray(data.detail)) return data.detail.join("; ");
    if (data.detail && typeof data.detail === "object") {
      return Object.entries(data.detail)
        .map(([field, messages]) => {
          const text = Array.isArray(messages) ? messages.join(" ") : String(messages);
          return `${field}: ${text}`;
        })
        .join(" ");
    }
    if (typeof data.message === "string") return data.message;
    if (typeof data.error === "string") return data.error;
    return fallback;
  }

  async function parseJson(res) {
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      const err = new Error(formatApiError(data));
      err.status = res.status;
      err.data = data;
      throw err;
    }
    return data;
  }

  return {
    async listKillSwitches() {
      const res = await fetchWithAuth("/api/kill-switches/");
      const data = await parseJson(res);
      return Array.isArray(data) ? data : data.results || [];
    },

    async createAndActivateKillSwitch(payload) {
      const existing = findKillSwitchForPayload(await this.listKillSwitches(), payload);
      if (existing?.id) {
        const activateRes = await fetchWithAuth(`/api/kill-switches/${existing.id}/activate/`, {
          method: "POST",
          body: JSON.stringify({ reason: payload.reason || "" }),
        });
        const activated = await parseJson(activateRes);
        notifyContainmentChanged("kill-switch-activate");
        return activated;
      }

      const createRes = await fetchWithAuth("/api/kill-switches/", {
        method: "POST",
        body: JSON.stringify(payload),
      });
      const created = await parseJson(createRes);
      if (!created?.id) {
        throw new Error("Kill switch creation response missing id");
      }
      try {
        const activateRes = await fetchWithAuth(`/api/kill-switches/${created.id}/activate/`, {
          method: "POST",
          body: JSON.stringify({ reason: payload.reason || "" }),
        });
        await parseJson(activateRes);
      } catch (error) {
        try {
          const rollbackRes = await fetchWithAuth(`/api/kill-switches/${created.id}/`, { method: "DELETE" });
          if (!rollbackRes.ok && rollbackRes.status !== 404) {
            throw new Error(`Rollback failed (${rollbackRes.status})`);
          }
        } catch (rollbackError) {
          error.rollbackError = rollbackError;
        }
        throw error;
      }
      notifyContainmentChanged("kill-switch-activate");
      return created;
    },

    async deactivateKillSwitch(id) {
      const res = await fetchWithAuth(`/api/kill-switches/${id}/deactivate/`, { method: "POST" });
      const data = await parseJson(res);
      notifyContainmentChanged("kill-switch-deactivate");
      return data;
    },

    async setGatewayKeyActive(keyId, isActive, meta = {}) {
      const res = await fetchWithAuth(`/api/gateways/keys/${keyId}/`, {
        method: "PATCH",
        body: JSON.stringify({ is_active: isActive }),
      });
      const data = await parseJson(res);
      const prefix = String(meta.prefix || data?.prefix || "").trim();
      if (isActive) {
        clearSimulatorKeyReprovisionSuppress();
        notifyContainmentChanged("api-key-enable", { keyId, prefix });
      } else {
        // Keep the disabled secret in Attack Simulator localStorage so the next
        // Run hits Auth — but block shared RAG/simulator auto-heal from minting
        // a replacement active key into the same storage slot.
        suppressSimulatorKeyReprovision({ prefix, keyId: String(keyId || "") });
        notifyContainmentChanged("api-key-disable", { keyId, prefix });
      }
      return data;
    },
  };
}
