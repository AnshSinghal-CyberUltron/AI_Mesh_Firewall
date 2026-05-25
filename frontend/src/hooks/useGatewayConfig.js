import { useState, useEffect } from "react";
import {
  getGatewayStorageKey,
  isBrowserReachableUrl,
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
    }
  }, []);

  useEffect(() => {
    let cancelled = false;
    fetch("/api/gateways/public-url/")
      .then((res) => (res.ok ? res.json() : null))
      .then((data) => {
        if (cancelled) return;
        const fallbackGw = resolveBrowserGatewayBaseUrl();
        const fallbackBe = resolveBrowserBackendBaseUrl();
        const gw =
          data?.gateway_url && isBrowserReachableUrl(data.gateway_url)
            ? data.gateway_url
            : fallbackGw;
        const be =
          data?.backend_url && isBrowserReachableUrl(data.backend_url)
            ? data.backend_url
            : fallbackBe;
        setGatewayUrl(gw);
        setBackendUrl(be);
        if (isBrowserReachableUrl(gw)) {
          localStorage.setItem(GATEWAY_URL_KEY, gw);
        }
        setLoading(false);
      })
      .catch(() => {
        if (!cancelled) {
          setGatewayUrl(resolveBrowserGatewayBaseUrl());
          setBackendUrl(resolveBrowserBackendBaseUrl());
          setLoading(false);
        }
      });
    return () => {
      cancelled = true;
    };
  }, []);

  return { gatewayUrl, backendUrl, loading };
}
