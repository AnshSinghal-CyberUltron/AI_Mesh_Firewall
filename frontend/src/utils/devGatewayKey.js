/** Bearer the local gateway-v2 dev profile accepts. Empty outside that compose env. */
export function devGatewayKey() {
  const value = import.meta.env?.VITE_GATEWAY_DEV_KEY;
  return typeof value === "string" ? value.trim() : "";
}
