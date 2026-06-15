/** Kill-switch and gateway key control APIs for UEBA response actions. */

import { notifyContainmentChanged } from "../utils/containmentEvents.js";

export function buildCredentialKillSwitchPayload({
  modelName,
  apiKeyPrefix,
  reason,
  action = "disable",
}) {
  return {
    model_name: String(modelName || "").trim(),
    api_key_prefix: String(apiKeyPrefix || "").trim(),
    action,
    reason: String(reason || "").trim() || "UEBA high-risk API key containment",
    fallback_model: "",
  };
}

export function buildUebaKillSwitchReason(behavior) {
  if (!behavior) {
    return "UEBA high-risk API key containment";
  }
  return (
    `UEBA ${behavior.risk_band} risk (score ${behavior.risk_score}): `
    + `block ${behavior.block_rate_pct ?? 0}%, velocity ${behavior.velocity_spike ?? 1}x`
  );
}

export function filterKillSwitchesForPrefix(killSwitches, prefix) {
  const normalized = String(prefix || "").trim();
  if (!normalized) return [];
  const rows = Array.isArray(killSwitches) ? killSwitches : killSwitches?.results || [];
  return rows.filter((ks) => String(ks.api_key_prefix || "").trim() === normalized);
}

export function createKillSwitchApi(fetchWithAuth) {
  async function parseJson(res) {
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      const err = new Error(data.detail || data.message || data.error || "Request failed");
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
      const createRes = await fetchWithAuth("/api/kill-switches/", {
        method: "POST",
        body: JSON.stringify(payload),
      });
      const created = await parseJson(createRes);
      const activateRes = await fetchWithAuth(`/api/kill-switches/${created.id}/activate/`, {
        method: "POST",
        body: JSON.stringify({ reason: payload.reason || "" }),
      });
      await parseJson(activateRes);
      notifyContainmentChanged("kill-switch-activate");
      return created;
    },

    async deactivateKillSwitch(id) {
      const res = await fetchWithAuth(`/api/kill-switches/${id}/deactivate/`, { method: "POST" });
      const data = await parseJson(res);
      notifyContainmentChanged("kill-switch-deactivate");
      return data;
    },

    async setGatewayKeyActive(keyId, isActive) {
      const res = await fetchWithAuth(`/api/gateways/keys/${keyId}/`, {
        method: "PATCH",
        body: JSON.stringify({ is_active: isActive }),
      });
      const data = await parseJson(res);
      notifyContainmentChanged(isActive ? "api-key-enable" : "api-key-disable");
      return data;
    },
  };
}
