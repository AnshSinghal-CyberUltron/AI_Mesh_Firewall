/** Resolve gateway API keys to UEBA fleet rows (prefix + key_id). */

export const GATEWAY_PREFIX_STORAGE_KEY = "zeroshield_gateway_key_prefix";
export const GATEWAY_KEY_ID_STORAGE_KEY = "zeroshield_gateway_key_id";

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
  };
  writeStoredGatewayKeyContext(ctx);
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
