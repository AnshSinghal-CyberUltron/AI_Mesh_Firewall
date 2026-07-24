/** Resolve gateway API keys to UEBA fleet rows (prefix + key_id). */

export const GATEWAY_PREFIX_STORAGE_KEY = "zeroshield_gateway_key_prefix";
export const GATEWAY_KEY_ID_STORAGE_KEY = "zeroshield_gateway_key_id";
export const SIMULATOR_KEY_CHANGED_EVENT = "ai-mesh:simulator-key-changed";
const ORG_KEY_PREFIX = "zeroshield_gateway_key";
const LEGACY_KEY = "zeroshield_gateway_key";

export function orgGatewayKeyStorageKey(orgId) {
  return orgId ? `${ORG_KEY_PREFIX}:${orgId}` : LEGACY_KEY;
}

/** Same lookup order as Attack Simulator (org-scoped, then legacy). */
export function readOrgScopedGatewayKey(orgId) {
  if (typeof window === "undefined") return "";
  try {
    const scoped = orgId ? localStorage.getItem(orgGatewayKeyStorageKey(orgId)) : "";
    if (scoped?.trim()) return scoped.trim();
    return localStorage.getItem(LEGACY_KEY)?.trim() || "";
  } catch {
    return "";
  }
}

export function writeOrgScopedGatewayKey(apiKey, orgId, storageKey) {
  if (typeof window === "undefined") return;
  const target = storageKey || orgGatewayKeyStorageKey(orgId);
  try {
    localStorage.setItem(target, apiKey);
    if (target !== LEGACY_KEY) {
      localStorage.removeItem(LEGACY_KEY);
    }
  } catch {
    /* private mode */
  }
}

export function readStoredGatewayKeyContext() {
  if (typeof window === "undefined") {
    return { prefix: "", keyId: "", name: "" };
  }
  return {
    prefix: localStorage.getItem(GATEWAY_PREFIX_STORAGE_KEY) || "",
    keyId: localStorage.getItem(GATEWAY_KEY_ID_STORAGE_KEY) || "",
    name: localStorage.getItem("zeroshield_gateway_key_name") || "",
  };
}

export function writeStoredGatewayKeyContext({ prefix, keyId, name }) {
  if (typeof window === "undefined") return;
  if (prefix) localStorage.setItem(GATEWAY_PREFIX_STORAGE_KEY, prefix);
  if (keyId) localStorage.setItem(GATEWAY_KEY_ID_STORAGE_KEY, keyId);
  if (name) localStorage.setItem("zeroshield_gateway_key_name", name);
}

export function notifySimulatorKeyChanged(detail = {}) {
  if (typeof window === "undefined") return;
  window.dispatchEvent(new CustomEvent(SIMULATOR_KEY_CHANGED_EVENT, { detail }));
}

export async function resolveGatewayKeyContext(fetchWithAuth, apiKey) {
  const trimmed = String(apiKey || "").trim();
  if (!trimmed) return null;

  const res = await fetchWithAuth("/api/gateways/keys/context/", {
    method: "POST",
    body: JSON.stringify({ api_key: trimmed }),
  });
  if (!res.ok) return null;
  const data = await res.json();
  const ctx = {
    prefix: data.prefix || "",
    keyId: data.key_id || "",
    name: data.name || "",
    isSimulatorDefault: !!data.is_simulator_default,
    storageKey: data.storage_key || "",
  };
  writeStoredGatewayKeyContext(ctx);
  return ctx;
}

export async function adoptGatewayKeyForSimulator(fetchWithAuth, apiKey, orgId) {
  const trimmed = String(apiKey || "").trim();
  if (!trimmed) return null;
  const ctx = await resolveGatewayKeyContext(fetchWithAuth, trimmed);
  if (!ctx) return null;
  writeOrgScopedGatewayKey(trimmed, orgId, ctx.storageKey);
  notifySimulatorKeyChanged(ctx);
  return ctx;
}

export async function fetchSimulatorDefaultContext(fetchWithAuth) {
  const res = await fetchWithAuth("/api/gateways/simulator-default/");
  if (!res.ok) return null;
  const data = await res.json();
  if (data.prefix && data.key_id) {
    const ctx = {
      prefix: data.prefix,
      keyId: data.key_id,
      name: data.name || "simulator-default",
      isSimulatorDefault: data.is_simulator_default !== false,
    };
    writeStoredGatewayKeyContext(ctx);
    return ctx;
  }
  if (data.key) {
    return resolveGatewayKeyContext(fetchWithAuth, data.key);
  }
  return null;
}
