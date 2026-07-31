/** Resolve gateway API keys to UEBA fleet rows (prefix + key_id).
 *
 * Uses only Module 1 APIs available on main:
 * - GET/POST /api/gateways/simulator-default/
 * - GET /api/gateways/keys/ (list + match by prefix)
 *
 * Does NOT call /api/gateways/keys/context/ or .../adopt-simulator/ (not on main).
 */

export const GATEWAY_PREFIX_STORAGE_KEY = "zeroshield_gateway_key_prefix";
export const GATEWAY_KEY_ID_STORAGE_KEY = "zeroshield_gateway_key_id";
export const SIMULATOR_KEY_CHANGED_EVENT = "ai-mesh:simulator-key-changed";
const ORG_KEY_PREFIX = "zeroshield_gateway_key";
const LEGACY_KEY = "zeroshield_gateway_key";
const KEY_PREFIX_LENGTH = 8;

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

async function listGatewayKeys(fetchWithAuth) {
  const res = await fetchWithAuth("/api/gateways/keys/");
  if (!res.ok) return [];
  const data = await res.json().catch(() => ([]));
  return Array.isArray(data) ? data : data.results || [];
}

function matchKeyByPlaintext(keys, apiKey) {
  const trimmed = String(apiKey || "").trim();
  if (!trimmed) return null;
  const guessedPrefix = trimmed.slice(0, KEY_PREFIX_LENGTH);
  return (
    keys.find((k) => {
      const prefix = String(k.prefix || "").trim();
      return prefix && (trimmed.startsWith(prefix) || prefix === guessedPrefix);
    }) || null
  );
}

/**
 * Resolve plaintext gateway key → UEBA identity via key list prefix match
 * (main has no POST /api/gateways/keys/context/).
 */
export async function resolveGatewayKeyContext(fetchWithAuth, apiKey) {
  const trimmed = String(apiKey || "").trim();
  if (!trimmed) return null;

  const keys = await listGatewayKeys(fetchWithAuth);
  const match = matchKeyByPlaintext(keys, trimmed);
  if (!match) return null;

  const ctx = {
    prefix: match.prefix || trimmed.slice(0, KEY_PREFIX_LENGTH),
    keyId: String(match.id || match.key_id || ""),
    name: match.name || "",
    isSimulatorDefault: !!(match.is_simulator_default || match.name === "simulator"),
    storageKey: "",
  };
  if (!ctx.keyId) return null;
  writeStoredGatewayKeyContext(ctx);
  return ctx;
}

export async function adoptGatewayKeyForSimulator(fetchWithAuth, apiKey, orgId) {
  const trimmed = String(apiKey || "").trim();
  if (!trimmed) return null;
  const ctx = await resolveGatewayKeyContext(fetchWithAuth, trimmed);
  if (!ctx) return null;
  writeOrgScopedGatewayKey(trimmed, orgId, ctx.storageKey || orgGatewayKeyStorageKey(orgId));
  notifySimulatorKeyChanged(ctx);
  return ctx;
}

/**
 * Focus an existing fleet key in UEBA (client-side context only).
 * Main has no adopt-simulator endpoint — does not rotate or re-issue plaintext.
 */
export async function adoptSimulatorKeyById(fetchWithAuth, keyId, orgId, { plaintext = "" } = {}) {
  const id = String(keyId || "").trim();
  if (!id) {
    throw new Error("key id required");
  }

  const keys = await listGatewayKeys(fetchWithAuth);
  const match = keys.find((k) => String(k.id || k.key_id) === id);
  if (!match) {
    throw new Error(`Gateway key ${id} not found`);
  }

  const apiKey = plaintext || "";
  if (apiKey) {
    writeOrgScopedGatewayKey(apiKey, orgId, orgGatewayKeyStorageKey(orgId));
  }

  const ctx = {
    prefix: match.prefix || "",
    keyId: id,
    name: match.name || "",
    isSimulatorDefault: !!(match.is_simulator_default || match.name === "simulator"),
    storageKey: orgGatewayKeyStorageKey(orgId),
  };
  writeStoredGatewayKeyContext(ctx);
  notifySimulatorKeyChanged(ctx);
  return ctx;
}

export async function fetchSimulatorDefaultContext(fetchWithAuth) {
  const res = await fetchWithAuth("/api/gateways/simulator-default/");
  if (!res.ok) return null;
  const data = await res.json().catch(() => ({}));

  if (data.prefix) {
    const keys = await listGatewayKeys(fetchWithAuth);
    const match = keys.find((k) => String(k.prefix || "") === String(data.prefix));
    const ctx = {
      prefix: data.prefix,
      keyId: String(data.key_id || match?.id || match?.key_id || ""),
      name: data.name || match?.name || "simulator-default",
      isSimulatorDefault: data.is_simulator_default !== false,
    };
    if (ctx.keyId) {
      writeStoredGatewayKeyContext(ctx);
      return ctx;
    }
    writeStoredGatewayKeyContext(ctx);
    return ctx.keyId ? ctx : { ...ctx, keyId: "" };
  }

  if (data.key) {
    return resolveGatewayKeyContext(fetchWithAuth, data.key);
  }
  return null;
}
