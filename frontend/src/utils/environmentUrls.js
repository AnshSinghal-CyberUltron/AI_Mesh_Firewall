const GATEWAY_URL_KEY = "zeroshield_gateway_url";

/** Docker service names — never reachable from the user's browser. */
const INTERNAL_DOCKER_HOSTS = new Set([
  "control",
  "gateway",
  "frontend",
  "postgres",
  "redis",
  "rabbitmq",
  "mongo",
  "guardrails",
  "mcp-broker",
  "vector-retrieval",
  "workers",
]);

function trimTrailingSlash(value) {
  return (value || "").replace(/\/+$/, "");
}

function hasValue(value) {
  return value !== undefined && value !== null && String(value).trim() !== "";
}

function isAbsoluteHttpUrl(value) {
  return /^https?:\/\//i.test(String(value || ""));
}

function toInt(value, fallback) {
  const parsed = Number.parseInt(String(value || ""), 10);
  return Number.isFinite(parsed) ? parsed : fallback;
}

function getBrowserOrigin() {
  if (typeof window === "undefined") return "";
  return window.location.origin || "";
}

function getBrowserHost() {
  if (typeof window === "undefined") return "";
  return window.location.hostname || "";
}

function getBrowserProtocol() {
  if (typeof window === "undefined") return "http:";
  return window.location.protocol || "http:";
}

function buildBaseUrl({ protocol, host, port }) {
  if (!host) return "";
  if (!hasValue(port)) return `${protocol}//${host}`;
  return `${protocol}//${host}:${port}`;
}

export function getStoredGatewayUrl() {
  if (typeof window === "undefined") return "";
  return trimTrailingSlash(window.localStorage.getItem(GATEWAY_URL_KEY) || "");
}

export function getGatewayStorageKey() {
  return GATEWAY_URL_KEY;
}

/** True when the URL is an absolute http(s) URL the browser can call (not a Docker internal host). */
export function isBrowserReachableUrl(value) {
  const raw = trimTrailingSlash(value);
  if (!raw || !isAbsoluteHttpUrl(raw)) return false;
  try {
    const host = new URL(raw).hostname.toLowerCase();
    if (INTERNAL_DOCKER_HOSTS.has(host)) return false;
    if (host.endsWith(".internal") || host.endsWith(".local")) return false;
    return true;
  } catch {
    return false;
  }
}

/**
 * Gateway base URL for browser-side fetch().
 * Prefer same-origin so Vite proxies /v1 → gateway (no CORS).
 */
export function resolveBrowserGatewayBaseUrl() {
  const explicit = trimTrailingSlash(import.meta.env?.VITE_GATEWAY_BASE_URL || "");
  if (explicit && isBrowserReachableUrl(explicit)) return explicit;

  const origin = trimTrailingSlash(getBrowserOrigin());
  if (origin && isBrowserReachableUrl(origin)) return origin;

  const stored = getStoredGatewayUrl();
  if (stored && isBrowserReachableUrl(stored)) return stored;

  return resolveGatewayBaseUrl();
}

export function resolveBrowserBackendBaseUrl() {
  const explicit = trimTrailingSlash(import.meta.env?.VITE_BACKEND_BASE_URL || "");
  if (explicit && isBrowserReachableUrl(explicit)) return explicit;

  const origin = trimTrailingSlash(getBrowserOrigin());
  if (origin && isBrowserReachableUrl(origin)) return origin;

  return resolveBackendBaseUrl();
}

export function resolveFrontendBaseUrl() {
  const explicit = trimTrailingSlash(import.meta.env?.VITE_FRONTEND_BASE_URL || "");
  if (explicit) return explicit;
  return trimTrailingSlash(getBrowserOrigin());
}

export function resolveGatewayBaseUrl() {
  const explicit = trimTrailingSlash(import.meta.env?.VITE_GATEWAY_BASE_URL || "");
  if (explicit) return explicit;

  const stored = getStoredGatewayUrl();
  if (stored) return stored;

  const protocol = import.meta.env?.VITE_GATEWAY_SCHEME || getBrowserProtocol();
  const host = import.meta.env?.VITE_GATEWAY_HOST || getBrowserHost();
  const envPort = import.meta.env?.VITE_GATEWAY_PORT;
  if (hasValue(envPort)) {
    const port = toInt(envPort, "");
    return trimTrailingSlash(buildBaseUrl({ protocol, host, port }));
  }

  // Final fallback for non-configured local runs.
  const port = toInt(import.meta.env?.VITE_GATEWAY_FALLBACK_PORT, 8300);
  return trimTrailingSlash(buildBaseUrl({ protocol, host, port }));
}

export function resolveBackendBaseUrl() {
  const explicit = trimTrailingSlash(import.meta.env?.VITE_BACKEND_BASE_URL || "");
  if (explicit) return explicit;

  const protocol = import.meta.env?.VITE_BACKEND_SCHEME || getBrowserProtocol();
  const host = import.meta.env?.VITE_BACKEND_HOST || getBrowserHost();
  const envPort = import.meta.env?.VITE_BACKEND_PORT;
  if (hasValue(envPort)) {
    const port = toInt(envPort, "");
    return trimTrailingSlash(buildBaseUrl({ protocol, host, port }));
  }

  // Final fallback for non-configured local runs.
  const port = toInt(import.meta.env?.VITE_BACKEND_FALLBACK_PORT, 8100);
  return trimTrailingSlash(buildBaseUrl({ protocol, host, port }));
}

export function resolveWebSocketBaseUrl() {
  const explicit = trimTrailingSlash(import.meta.env?.VITE_WS_BASE_URL || "");
  if (explicit) return explicit;

  const apiBase = trimTrailingSlash(import.meta.env?.VITE_API_BASE_URL || resolveBackendBaseUrl());
  if (apiBase.startsWith("https://")) return apiBase.replace("https://", "wss://");
  if (apiBase.startsWith("http://")) return apiBase.replace("http://", "ws://");

  if (typeof window === "undefined") return "";
  const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
  return `${protocol}//${window.location.host}`;
}

export function getOAuthRedirectUri() {
  if (typeof window === "undefined") return "";
  return `${window.location.origin}/oauth/callback`;
}

export function toAbsoluteGatewayUrl(pathOrUrl) {
  const value = String(pathOrUrl || "").trim();
  if (!value) return "";
  if (isAbsoluteHttpUrl(value)) return value;

  const base = resolveGatewayBaseUrl();
  if (!base) return value;
  const normalizedPath = value.startsWith("/") ? value : `/${value}`;
  return `${base}${normalizedPath}`;
}

export function buildMcpGatewayEndpoint(gatewaySlug, orgSlug = "zeroshield") {
  if (!gatewaySlug) return "";
  const base = resolveGatewayBaseUrl();
  return `${base}/gateway/${orgSlug}/mcp/${gatewaySlug}`;
}

export function buildVsCodeConnectionUrl(gatewaySlug, orgSlug = "zeroshield") {
  return buildMcpGatewayEndpoint(gatewaySlug, orgSlug);
}
