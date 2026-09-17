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
    const pageHost = getBrowserHost().toLowerCase();
    // Same-origin is always callable (GCP *.internal:8180 Vite UI included).
    if (pageHost && host === pageHost) return true;
    if (host.endsWith(".internal") || host.endsWith(".local")) return false;
    return true;
  } catch {
    return false;
  }
}

/**
 * Same-origin /v1 proxy is the default on the production firewall vhost
 * (nginx proxies /v1 → gateway). Set VITE_GATEWAY_SAME_ORIGIN=true to opt in
 * on other hosts. External API clients (curl/SDKs) use aimeshgateway.* directly.
 */
export function isViteDevUiPort() {
  if (typeof window === "undefined") return false;
  const port = String(window.location.port || "");
  return port === "8180" || port === "5173";
}

export function preferSameOriginGateway() {
  if (isProductionFirewallHost()) return true;
  if (isViteDevUiPort()) return true;
  return import.meta.env?.VITE_GATEWAY_SAME_ORIGIN === "true";
}

/** True when the UI is served from the production firewall vhost (not the gateway vhost). */
export function isProductionFirewallHost() {
  return getBrowserHost().toLowerCase() === "aimeshfirewall.zeroshield.ai";
}

/** True when a URL is the Django control plane — POST /v1 there is HTML 404. */
export function isControlPlaneGatewayUrl(value) {
  const raw = trimTrailingSlash(value);
  if (!raw) return false;
  try {
    const url = new URL(raw, getBrowserOrigin() || "http://localhost");
    const host = url.hostname.toLowerCase();
    const port = url.port;
    if (host === "aimeshbackend.zeroshield.ai") return true;
    if (host === "control") return true;
    if (port === "8100" || port === "8000") return true;
    if (url.pathname === "/api" || url.pathname.startsWith("/api/")) return true;
    return false;
  } catch {
    return false;
  }
}

/**
 * True when the UI is served from a LOCAL/dev host rather than a deployed
 * zeroshield.ai host. Used to force the local gateway port and IGNORE any prod
 * VITE_GATEWAY_BASE_URL baked into the shared .env (which points at the deployed
 * gateway). Covers localhost, loopback, *.local, and RFC-1918 private IPs — the
 * LAN IP (e.g. 192.168.x) is used to bypass Cursor's localhost port shadowing.
 */
export function isLocalBrowserHost() {
  const host = getBrowserHost().toLowerCase();
  if (!host) return false;
  if (host === "localhost" || host === "127.0.0.1" || host === "::1" || host === "[::1]") return true;
  if (host.endsWith(".local") || host.endsWith(".internal")) return true;
  if (/^10\./.test(host)) return true;
  if (/^192\.168\./.test(host)) return true;
  if (/^172\.(1[6-9]|2\d|3[01])\./.test(host)) return true;
  return false;
}

/**
 * Gateway base URL for browser-side fetch().
 * Prefer same-origin so nginx/Vite proxies /v1 → gateway (no CORS).
 */
export function resolveBrowserGatewayBaseUrl() {
  const origin = trimTrailingSlash(getBrowserOrigin());
  const dedicated = getDedicatedGatewayFallbackUrl();

  // Production firewall UI + Vite :8180/:5173: same-origin /v1 (nginx or Vite proxy).
  if (preferSameOriginGateway() && origin && isBrowserReachableUrl(origin)) {
    return origin;
  }

  // 2. LOCAL/dev — always the local gateway port. Ignore any prod
  //    VITE_GATEWAY_BASE_URL from the shared .env (it points at the deployed
  //    gateway, which is unreachable/incorrect from a dev browser).
  if (isLocalBrowserHost()) {
    const protocol = getBrowserProtocol();
    const host = getBrowserHost();
    const port = toInt(import.meta.env?.VITE_GATEWAY_PORT, 8300);
    const local = trimTrailingSlash(buildBaseUrl({ protocol, host, port }));
    if (isBrowserReachableUrl(local)) return local;
  }

  // 3. PRODUCTION — dedicated gateway host (VITE_GATEWAY_BASE_URL = aimeshgateway.zeroshield.ai).
  const explicit = trimTrailingSlash(import.meta.env?.VITE_GATEWAY_BASE_URL || "");
  if (explicit && isBrowserReachableUrl(explicit)) return explicit;

  if (origin && isBrowserReachableUrl(origin)) return origin;

  const stored = getStoredGatewayUrl();
  if (stored && isBrowserReachableUrl(stored)) return stored;

  return resolveGatewayBaseUrl();
}

/**
 * Base URL for building "/gateway/{org}/mcp/{slug}/..." endpoints — used for MCP
 * OAuth start, "Copy MCP Config", server-card URLs, and doc examples. These paths
 * are only ever served by the real gateway process itself (proxied 1:1, never
 * rewritten), so — unlike resolveBrowserGatewayBaseUrl() which prefers same-origin
 * purely to dodge CORS on plain /v1 fetch() calls — this always targets the actual
 * gateway host, which keeps the URL valid to paste into an external tool
 * (VS Code / Cursor mcp.json) or call directly:
 *   - LOCAL/dev browser → local gateway port (ignores any prod VITE_GATEWAY_BASE_URL
 *     baked into the shared .env — same override as resolveBrowserGatewayBaseUrl()).
 *   - Otherwise → the explicit dedicated gateway host (VITE_GATEWAY_BASE_URL),
 *     preserving production behavior exactly.
 */
export function resolveMcpGatewayBaseUrl() {
  if (isLocalBrowserHost()) {
    const protocol = getBrowserProtocol();
    const host = getBrowserHost();
    const port = toInt(import.meta.env?.VITE_GATEWAY_PORT, 8300);
    const local = trimTrailingSlash(buildBaseUrl({ protocol, host, port }));
    if (isBrowserReachableUrl(local)) return local;
  }

  const explicit = trimTrailingSlash(import.meta.env?.VITE_GATEWAY_BASE_URL || "");
  if (explicit) return explicit;

  return resolveGatewayBaseUrl();
}

/**
 * Dedicated gateway host baked at build time (cross-origin fallback when same-origin /v1 proxy is broken).
 */
export function getDedicatedGatewayFallbackUrl() {
  const explicit = trimTrailingSlash(import.meta.env?.VITE_GATEWAY_BASE_URL || "");
  if (explicit && isBrowserReachableUrl(explicit)) return explicit;
  const host = getBrowserHost().toLowerCase();
  if (host === "aimeshfirewall.zeroshield.ai") {
    return "https://aimeshgateway.zeroshield.ai";
  }
  return "";
}

/**
 * True when same-origin /gw-health is proxied to the gateway (not the SPA static handler).
 */
export async function probeSameOriginGatewayProxy(origin, fetchFn = fetch) {
  const base = trimTrailingSlash(origin);
  if (!base || !isBrowserReachableUrl(base)) return false;
  try {
    const res = await fetchFn(`${base}/gw-health`, {
      method: "GET",
      signal: AbortSignal.timeout(4000),
    });
    if (!res.ok) return false;
    const data = await res.json().catch(() => null);
    return data?.status === "ok";
  } catch {
    return false;
  }
}

/** Health probe path: /gw-health on UI host (proxied), /health on dedicated gateway host. */
export function resolveGatewayHealthUrl(gatewayBase) {
  const base = trimTrailingSlash(gatewayBase || resolveBrowserGatewayBaseUrl());
  const origin = trimTrailingSlash(getBrowserOrigin());
  if (base && origin && base === origin) {
    return `${origin}/gw-health`;
  }
  return `${base}/health`;
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

  const base = resolveMcpGatewayBaseUrl();
  if (!base) return value;
  const normalizedPath = value.startsWith("/") ? value : `/${value}`;
  return `${base}${normalizedPath}`;
}

export function buildMcpGatewayEndpoint(gatewaySlug, orgSlug = "zeroshield") {
  if (!gatewaySlug) return "";
  const base = resolveMcpGatewayBaseUrl();
  return `${base}/gateway/${orgSlug}/mcp/${gatewaySlug}`;
}

export function buildVsCodeConnectionUrl(gatewaySlug, orgSlug = "zeroshield") {
  return buildMcpGatewayEndpoint(gatewaySlug, orgSlug);
}
