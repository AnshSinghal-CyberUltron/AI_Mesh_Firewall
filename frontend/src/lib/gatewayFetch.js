// Bounded self-healing fetch for direct gateway calls authenticated with the
// per-org "simulator" gateway key.
//
// Why this exists: the simulator key is rotated server-side (POST
// /api/gateways/simulator-default/?ensure=1 deactivates the old key and issues a
// fresh one). If another session/tab/device re-provisions, this browser's cached
// key gets disabled and its gateway calls start returning 401/403 "API key is
// disabled" — with no way to recover short of a manual refresh. This helper
// detects exactly that case, re-provisions once, and retries the request once.
//
// Safety bounds (per the security review):
//   - ONE retry maximum per call — never loops.
//   - Only re-provisions on an EXPLICIT disabled/invalid-key signal in the body,
//     not on any 401/403 (a generic 403 must not trigger key rotation).
//   - Scoped to the simulator-key gateway calls that pass `reprovision`; it is
//     never wired into the JWT/control fetch path.

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
