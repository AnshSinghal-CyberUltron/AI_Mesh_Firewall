/**
 * Shared API helper for consistent error handling.
 * Use fetchWithAuth (from AuthContext) for authenticated endpoints, or pass fetch for public ones.
 * @param {string} url - Request URL
 * @param {{ method?: string, body?: string, headers?: Record<string, string> }} options - Fetch options
 * @param {(url: string, options?: RequestInit) => Promise<Response>} fetcher - fetch or fetchWithAuth
 * @returns {Promise<{ data: unknown, error: string | null, status: number }>}
 */
export async function apiRequest(url, options = {}, fetcher = fetch) {
  try {
    const res = await fetcher(url, {
      ...options,
      headers: { "Content-Type": "application/json", ...options.headers },
    });
    const status = res.status;
    const text = await res.text();
    let data = null;
    try {
      data = text ? JSON.parse(text) : null;
    } catch {
      data = text;
    }
    if (!res.ok) {
      const errorMessage = typeof data === "object" && data?.detail ? (Array.isArray(data.detail) ? data.detail[0] : data.detail) : text || `HTTP ${status}`;
      return { data: null, error: errorMessage, status };
    }
    return { data, error: null, status };
  } catch (e) {
    return { data: null, error: e.message || "Request failed", status: 0 };
  }
}
