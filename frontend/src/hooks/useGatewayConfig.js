import { useState, useEffect } from "react";
import {
  getDedicatedGatewayFallbackUrl,
  getGatewayStorageKey,
  isBrowserReachableUrl,
  isLocalBrowserHost,
  isProductionFirewallHost,
  preferSameOriginGateway,
  probeSameOriginGatewayProxy,
  resolveBrowserBackendBaseUrl,
  resolveBrowserGatewayBaseUrl,
} from "../utils/environmentUrls";

const GATEWAY_URL_KEY = getGatewayStorageKey();

export function getDefaultGatewayUrl() {
  return resolveBrowserGatewayBaseUrl();
}

/**
 * Fetches gateway and backend URLs from the backend API.
 * Seeds from localStorage for instant display, then updates from the API.
 * Returns { gatewayUrl, backendUrl, loading }.
 */
export function useGatewayConfig() {
  const [gatewayUrl, setGatewayUrl] = useState(() => getDefaultGatewayUrl());
  const [backendUrl, setBackendUrl] = useState(() => resolveBrowserBackendBaseUrl());
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    const stored = localStorage.getItem(GATEWAY_URL_KEY);
    if (stored && !isBrowserReachableUrl(stored)) {
      localStorage.removeItem(GATEWAY_URL_KEY);
      return;
    }
    const origin = typeof window !== "undefined" ? window.location.origin.replace(/\/+$/, "") : "";
    if (stored && origin) {
      try {
        const storedHost = new URL(stored).hostname.toLowerCase();
        const pageHost = new URL(origin).hostname.toLowerCase();
        // Drop stale cross-origin cache so prod UI uses same-origin /v1 proxy.
        if (pageHost === "aimeshfirewall.zeroshield.ai" && storedHost !== pageHost) {
          localStorage.removeItem(GATEWAY_URL_KEY);
        }
      } catch {
        /* ignore */
      }
    }
  }, []);

  useEffect(() => {
    let cancelled = false;

    const resolveGatewayUrl = async (data) => {
      const fallbackGw = resolveBrowserGatewayBaseUrl();
      const dedicatedGw = getDedicatedGatewayFallbackUrl();
      const apiGw =
        data?.gateway_url && isBrowserReachableUrl(data.gateway_url)
          ? data.gateway_url
          : "";
      const origin =
        typeof window !== "undefined"
          ? window.location.origin.replace(/\/+$/, "")
          : "";

      if (isLocalBrowserHost()) {
        return fallbackGw;
      }

      if (isProductionFirewallHost()) {
        if (origin && isBrowserReachableUrl(origin)) return origin;
        return dedicatedGw || fallbackGw || apiGw;
      }

      if (preferSameOriginGateway() && origin && isBrowserReachableUrl(origin)) {
        const proxyOk = await probeSameOriginGatewayProxy(origin);
        if (proxyOk) return origin;
        return dedicatedGw || fallbackGw;
      }

      // Cross-origin: prefer baked dedicated host over API gateway_url (API returns
      // firewall origin when FRONTEND_ORIGIN matches the request host).
      return dedicatedGw || fallbackGw || apiGw;
    };

    (async () => {
      try {
        const res = await fetch("/api/gateways/public-url/");
        const data = res.ok ? await res.json() : null;
        if (cancelled) return;

        const fallbackBe = resolveBrowserBackendBaseUrl();
        const gw = await resolveGatewayUrl(data);
        const be =
          data?.backend_url && isBrowserReachableUrl(data.backend_url)
            ? data.backend_url
            : fallbackBe;

        setGatewayUrl(gw);
        setBackendUrl(be);
        if (isBrowserReachableUrl(gw)) {
          localStorage.setItem(GATEWAY_URL_KEY, gw);
        }
      } catch {
        if (!cancelled) {
          setGatewayUrl(resolveBrowserGatewayBaseUrl());
          setBackendUrl(resolveBrowserBackendBaseUrl());
        }
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();

    return () => {
      cancelled = true;
    };
  }, []);

  return { gatewayUrl, backendUrl, loading };
}
