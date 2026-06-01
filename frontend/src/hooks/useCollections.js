import { useState, useEffect, useCallback } from "react";
import { useAuth } from "../context/AuthContext";

/**
 * Shared hook for fetching vector DB collections through the Django admin
 * proxy. The proxy stamps ``project_id`` from the caller's organization
 * and forwards to the gateway with the internal server-to-server key, so
 * the browser never holds a per-org gateway Bearer.
 * Returns a flat array of { provider, name } and a loading state.
 */
export function useCollections() {
  const { fetchWithAuth } = useAuth();
  const [collections, setCollections] = useState([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);

  const fetchCollections = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await fetchWithAuth(
        "/api/admin/gateway/rag/collections/",
      );
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      let body = null;
      try {
        body = await res.json();
      } catch {
        body = null;
      }
      if (!body || body.status !== "ok") {
        throw new Error(body?.data?.message || body?.data?.error || "proxy error");
      }
      const data = body.data || {};
      const flat = Object.entries(data.collections || {}).flatMap(([provider, items]) =>
        (items || []).map((item) => ({
          provider,
          name: typeof item === "string" ? item : item.name || String(item),
        })),
      );
      setCollections(flat);
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  }, [fetchWithAuth]);

  useEffect(() => {
    fetchCollections();
  }, [fetchCollections]);

  return { collections, loading, error, refresh: fetchCollections };
}
