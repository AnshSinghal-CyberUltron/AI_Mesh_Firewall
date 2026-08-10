/** Cross-module signal when API key disable or kill-switch state changes. */

export const CONTAINMENT_CHANGED_EVENT = "zeroshield:containment-changed";
export const CONTAINMENT_STORAGE_KEY = "zeroshield_containment_ping";

/** When set, simulator key auto-heal must not mint a replacement after Disable. */
export const REPROVISION_SUPPRESS_KEY = "zeroshield_suppress_simulator_reprovision";

/** In-memory fallback when sessionStorage is unavailable (Node tests / private mode). */
let _memoryReprovisionSuppress = null;

function _sessionStore() {
  try {
    if (typeof sessionStorage !== "undefined" && sessionStorage) return sessionStorage;
    if (typeof window !== "undefined" && window.sessionStorage) return window.sessionStorage;
  } catch {
    /* private mode */
  }
  return null;
}

export function notifyContainmentChanged(source = "unknown", detail = {}) {
  if (typeof window === "undefined") return;
  window.dispatchEvent(
    new CustomEvent(CONTAINMENT_CHANGED_EVENT, {
      detail: { source, at: Date.now(), ...detail },
    }),
  );
  try {
    localStorage.setItem(CONTAINMENT_STORAGE_KEY, String(Date.now()));
  } catch {
    /* private mode / quota */
  }
}

/**
 * After Module 2 disables a gateway key, block shared simulator auto-heal
 * (POST /api/gateways/simulator-default/) from minting a NEW active key into the
 * same localStorage slot — that swap makes Attack Simulator Auth-ALLOW again and
 * mis-attributes the next 403 to Input Scan.
 */
export function suppressSimulatorKeyReprovision({ prefix = "", keyId = "" } = {}) {
  const payload = {
    prefix: String(prefix || "").trim(),
    keyId: String(keyId || "").trim(),
    at: Date.now(),
  };
  _memoryReprovisionSuppress = payload;
  const store = _sessionStore();
  if (store) {
    try {
      store.setItem(REPROVISION_SUPPRESS_KEY, JSON.stringify(payload));
    } catch {
      /* private mode — memory still holds */
    }
  }
  notifyContainmentChanged("api-key-disable-suppress-reprovision", payload);
}

export function clearSimulatorKeyReprovisionSuppress() {
  _memoryReprovisionSuppress = null;
  const store = _sessionStore();
  if (store) {
    try {
      store.removeItem(REPROVISION_SUPPRESS_KEY);
    } catch {
      /* private mode */
    }
  }
}

export function readSimulatorKeyReprovisionSuppress() {
  const store = _sessionStore();
  if (store) {
    try {
      const raw = store.getItem(REPROVISION_SUPPRESS_KEY);
      if (raw) {
        const parsed = JSON.parse(raw);
        if (parsed && typeof parsed === "object") {
          return {
            prefix: String(parsed.prefix || "").trim(),
            keyId: String(parsed.keyId || "").trim(),
            at: Number(parsed.at) || 0,
          };
        }
      }
    } catch {
      /* fall through to memory */
    }
  }
  if (!_memoryReprovisionSuppress) return null;
  return {
    prefix: String(_memoryReprovisionSuppress.prefix || "").trim(),
    keyId: String(_memoryReprovisionSuppress.keyId || "").trim(),
    at: Number(_memoryReprovisionSuppress.at) || 0,
  };
}

/** True when auto-heal would defeat an intentional Disable Key containment test. */
export function isSimulatorKeyReprovisionSuppressed(currentKey = "") {
  const suppress = readSimulatorKeyReprovisionSuppress();
  if (!suppress) return false;
  const key = String(currentKey || "");
  if (!key) return true;
  if (suppress.prefix && key.startsWith(suppress.prefix)) return true;
  // Prefix unknown but suppress active — still block heal so Disable stays testable.
  if (!suppress.prefix) return true;
  return false;
}
