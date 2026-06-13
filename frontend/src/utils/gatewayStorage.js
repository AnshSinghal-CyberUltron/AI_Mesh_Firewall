import {
  getGatewayStorageKey,
  getStoredGatewayUrl,
  isLocalDevBrowser,
  resolveBrowserGatewayBaseUrl,
} from "./environmentUrls";

/** Legacy key used by Attack Simulator / useSimulatorEngine. */
export const GATEWAY_KEY_STORAGE_PRIMARY = "zeroshield_gateway_key";
/** Legacy key used by RAG panels and model connection. */
export const GATEWAY_KEY_STORAGE_LEGACY = "zeroshield_gateway_api_key";

const GATEWAY_KEY_KEYS = [GATEWAY_KEY_STORAGE_PRIMARY, GATEWAY_KEY_STORAGE_LEGACY];

export function getGatewayUrl() {
  migrateGatewayStorage();
  return resolveBrowserGatewayBaseUrl();
}

export function setGatewayUrl(url) {
  const normalized = String(url || "").replace(/\/+$/, "");
  if (normalized) {
    localStorage.setItem(getGatewayStorageKey(), normalized);
  }
}

export function getGatewayApiKey() {
  for (const key of GATEWAY_KEY_KEYS) {
    const value = localStorage.getItem(key);
    if (value?.trim()) return value.trim();
  }
  return "";
}

export function setGatewayApiKey(apiKey) {
  const value = String(apiKey || "");
  for (const key of GATEWAY_KEY_KEYS) {
    localStorage.setItem(key, value);
  }
}

/**
 * On localhost, replace a cached production gateway URL with the same-origin
 * Vite proxy so locally issued API keys resolve in local Redis.
 */
export function migrateGatewayStorage() {
  if (typeof window === "undefined") return;

  const resolved = resolveBrowserGatewayBaseUrl();
  const stored = getStoredGatewayUrl();

  if (isLocalDevBrowser() && stored && stored !== resolved) {
    try {
      const storedHost = new URL(stored).hostname.toLowerCase();
      const resolvedHost = new URL(resolved).hostname.toLowerCase();
      if (storedHost !== resolvedHost) {
        localStorage.setItem(getGatewayStorageKey(), resolved);
      }
    } catch {
      localStorage.removeItem(getGatewayStorageKey());
    }
  }
}
