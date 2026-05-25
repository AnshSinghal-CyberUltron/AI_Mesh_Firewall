import { useState, useEffect, useCallback } from "react";

const GATEWAY_URL_KEY = "zeroshield_gateway_url";
const GATEWAY_KEY_KEY = "zeroshield_gateway_api_key";
const GATEWAY_KEY_STORAGE = "zeroshield_gateway_key";

function gwUrl() {
  const stored = localStorage.getItem(GATEWAY_URL_KEY);
  if (stored) return stored.replace(/\/+$/, "");
  const host = window.location.hostname || "127.0.0.1";
  return `http://${host}:8300`;
}

function gwKey() {
  return localStorage.getItem(GATEWAY_KEY_KEY) || localStorage.getItem(GATEWAY_KEY_STORAGE) || "";
}

/**
 * Shared hook for fetching vector DB collections from the gateway.
 * Returns a flat array of { provider, name } and a loading state.
 * Auto-fetches on mount; call refresh() to re-fetch.
 */
export function useCollections() {
  const [collections, setCollections] = useState([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);

  const fetchCollections = useCallback(async () => {
    const key = gwKey();
    if (!key) return;
    setLoading(true);
    setError(null);
    try {
      const res = await fetch(`${gwUrl()}/v1/rag/collections`, {
        headers: { Authorization: `Bearer ${key}` },
        signal: AbortSignal.timeout(8000),
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      const flat = Object.entries(data.collections || {}).flatMap(([provider, items]) =>
        (items || []).map((item) => ({
          provider,
          name: typeof item === "string" ? item : item.name || String(item),
        }))
      );
      setCollections(flat);
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchCollections();
  }, [fetchCollections]);

  return { collections, loading, error, refresh: fetchCollections };
}
