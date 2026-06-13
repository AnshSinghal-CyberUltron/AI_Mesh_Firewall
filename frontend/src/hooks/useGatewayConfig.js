import { useState, useEffect } from "react";
import {
  getGatewayStorageKey,
  isBrowserReachableUrl,
  isLocalDevBrowser,
  resolveBrowserBackendBaseUrl,
  resolveBrowserGatewayBaseUrl,
} from "../utils/environmentUrls";
import { migrateGatewayStorage } from "../utils/gatewayStorage";

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
    migrateGatewayStorage();
    const stored = localStorage.getItem(GATEWAY_URL_KEY);
    if (stored && !isBrowserReachableUrl(stored)) {
      localStorage.removeItem(GATEWAY_URL_KEY);
    }
    setGatewayUrl(resolveBrowserGatewayBaseUrl());
  }, []);

  useEffect(() => {
    let cancelled = false;
    fetch("/api/gateways/public-url/")
      .then((res) => (res.ok ? res.json() : null))
      .then((data) => {
        if (cancelled) return;
        const fallbackGw = resolveBrowserGatewayBaseUrl();
        const fallbackBe = resolveBrowserBackendBaseUrl();
        let gw =
          data?.gateway_url && isBrowserReachableUrl(data.gateway_url)
            ? data.gateway_url
            : fallbackGw;
        const be =
          data?.backend_url && isBrowserReachableUrl(data.backend_url)
            ? data.backend_url
            : fallbackBe;
        if (isLocalDevBrowser()) {
          const localGw = resolveBrowserGatewayBaseUrl();
          if (gw && localGw && gw !== localGw) {
            gw = localGw;
          }
        }
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
