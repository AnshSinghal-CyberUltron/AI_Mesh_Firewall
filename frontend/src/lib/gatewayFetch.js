// Bounded self-healing fetch for direct gateway calls authenticated with the
// per-org "simulator" gateway key.
//
// Why this exists: this browser's cached simulator key can go stale (e.g. an old
// value left in localStorage), so gateway calls start returning 401/403 "API key
// is disabled" — with no way to recover short of a manual refresh. This helper
// detects exactly that case, RE-FETCHES the org's current key once (the
// non-rotating, idempotent reprovision), and retries the request once.
//
// Safety bounds (per the security review):
//   - ONE retry maximum per call — never loops.
//   - Only reprovisions on an EXPLICIT disabled/invalid-key signal in the body,
//     not on any 401/403 (a generic 403 must not trigger a reprovision).
//   - `reprovision` re-fetches the org's EXISTING active key — it never rotates.
//   - Scoped to the simulator-key gateway calls that pass `reprovision`; it is
//     never wired into the JWT/control fetch path.

import { isSimulatorKeyReprovisionSuppressed } from "../utils/containmentEvents.js";

const DISABLED_KEY_RE = /(api key is disabled|key is disabled|not valid for any token type|invalid api key|key_disabled|api_key_invalid)/i;

async function bodyIndicatesDisabledKey(res) {
  try {
    const text = await res.clone().text();
    return DISABLED_KEY_RE.test(text);
  } catch {
    return false;
  }
}

/**
 * fetch() against the gateway with the simulator key, self-healing on a disabled
 * key exactly once.
 *
 * @param {string} url               Absolute gateway URL (e.g. `${gatewayUrl}/v1/rag/query`).
 * @param {RequestInit} options      Standard fetch options (Authorization is set here).
 * @param {object} cred
 * @param {string} cred.key          Current simulator key.
 * @param {() => Promise<string|null>} [cred.reprovision]  Clears the cached key and returns a fresh one.
 */
export async function gatewayFetch(url, options = {}, { key, reprovision } = {}) {
  const withKey = (k) => ({
    ...options,
    headers: { ...(options.headers || {}), Authorization: `Bearer ${k}` },
  });

  let res = await fetch(url, withKey(key));

  if ((res.status === 401 || res.status === 403) && typeof reprovision === "function") {
    // After Module 2 "Disable key", do not mint/swap a fresh simulator key —
    // that hides Auth-stage rejection and makes the next chat look like Input Scan.
    if (isSimulatorKeyReprovisionSuppressed(key)) {
      return res;
    }
    if (await bodyIndicatesDisabledKey(res)) {
      const fresh = await reprovision();
      // Retry once, only if we actually got a different key.
      if (fresh && fresh !== key) {
        res = await fetch(url, withKey(fresh));
      }
    }
  }

  return res;
}

export default gatewayFetch;
