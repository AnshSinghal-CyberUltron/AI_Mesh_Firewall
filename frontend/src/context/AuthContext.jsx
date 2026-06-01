import { createContext, useContext, useState, useCallback, useEffect } from 'react';

const AuthContext = createContext(null);

const ACCESS_KEY = 'auth_access';
const REFRESH_KEY = 'auth_refresh';
const AUTH_REQUEST_TIMEOUT_MS = 8000;

// ── SEC-06 FIX: Mutex to prevent concurrent refresh token races ──
let refreshPromise = null;

function getStoredAccess() {
  try {
    return localStorage.getItem(ACCESS_KEY);
  } catch {
    return null;
  }
}

function getStoredRefresh() {
  try {
    return localStorage.getItem(REFRESH_KEY);
  } catch {
    return null;
  }
}

function setStoredTokens(access, refresh) {
  try {
    if (access) localStorage.setItem(ACCESS_KEY, access);
    if (refresh) localStorage.setItem(REFRESH_KEY, refresh);
  } catch {}
}

function clearStoredTokens() {
  try {
    localStorage.removeItem(ACCESS_KEY);
    localStorage.removeItem(REFRESH_KEY);
    // Clear legacy and tenant-sensitive auth artifacts that can leak across sessions.
    localStorage.removeItem('access_token');
    localStorage.removeItem('zeroshield_jwt_token');
    localStorage.removeItem('zeroshield_gateway_api_key');
    localStorage.removeItem('simulator_gateway_api_key');
    localStorage.removeItem('gateway_api_key');
    // Active storage key used by useSimulatorEngine; must be cleared on logout
    // to prevent cross-user credential leakage on shared browsers.
    localStorage.removeItem('zeroshield_gateway_key');
  } catch {}
}

/** Return true if token is expired or will expire within bufferSeconds. */
function isTokenExpired(token, bufferSeconds = 30) {
  if (!token) return true;
  try {
    const payload = JSON.parse(
      atob(token.split('.')[1].replace(/-/g, '+').replace(/_/g, '/'))
    );
    const exp = payload.exp;
    if (!exp) return true;
    return Date.now() / 1000 >= exp - bufferSeconds;
  } catch {
    return true;
  }
}

/** Get a valid access token, refreshing if expired. Returns null if unable to obtain one. */
async function getValidAccessToken() {
  let access = getStoredAccess();
  if (isTokenExpired(access)) {
    access = await refreshAccess();
  }
  return access;
}

async function refreshAccess() {
  // ── SEC-06 FIX: Use semaphore to avoid concurrent refresh requests ──
  if (refreshPromise) {
    return refreshPromise;
  }

  const refresh = getStoredRefresh();
  if (!refresh) return null;

  refreshPromise = (async () => {
    try {
      const res = await fetch('/api/auth/token/refresh/', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ refresh }),
        signal: AbortSignal.timeout(AUTH_REQUEST_TIMEOUT_MS),
      });
      if (!res.ok) return null;
      const data = await res.json();
      if (data.access) {
        setStoredTokens(data.access, data.refresh || refresh);
        return data.access;
      }
    } catch {
      return null;
    }
    return null;
  })();

  try {
    return await refreshPromise;
  } finally {
    refreshPromise = null;
  }
}

async function fetchMe(access) {
  try {
    const res = await fetch('/api/auth/me/', {
      headers: { Authorization: `Bearer ${access}` },
      signal: AbortSignal.timeout(AUTH_REQUEST_TIMEOUT_MS),
    });
    if (!res.ok) return null;
    return res.json();
  } catch {
    return null;
  }
}

export function AuthProvider({ children }) {
  const [user, setUser] = useState(null);
  const [loading, setLoading] = useState(true);

  const loadUser = useCallback(async () => {
    try {
      let access = getStoredAccess();
      if (!access) {
        access = await refreshAccess();
      }
      if (!access) {
        setUser(null);
        return;
      }
      const me = await fetchMe(access);
      if (me) {
        setUser(me);
        return;
      }
      access = await refreshAccess();
      if (access) {
        const retryMe = await fetchMe(access);
        if (retryMe) {
          setUser(retryMe);
          return;
        }
      }
      clearStoredTokens();
      setUser(null);
    } catch {
      clearStoredTokens();
      setUser(null);
    } finally {
      setLoading(false);
    }
  }, []);

  const refreshUser = useCallback(async () => {
    let access = getStoredAccess();
    if (!access) {
      access = await refreshAccess();
    }
    if (!access) {
      setUser(null);
      return null;
    }
    const me = await fetchMe(access);
    if (!me) return null;
    setUser(me);
    return me;
  }, []);

  useEffect(() => {
    loadUser();
  }, [loadUser]);

  const login = useCallback(async (email, password) => {
    const res = await fetch('/api/auth/token/', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ email, password }),
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail || err.email?.[0] || 'Invalid email or password.');
    }
    const data = await res.json();
    setStoredTokens(data.access, data.refresh);
    const me = await fetchMe(data.access);
    setUser(me ?? { email, first_name: '', last_name: '', roles: ['user'], is_active: true });
    return me;
  }, []);

  const logout = useCallback(async () => {
    const refresh = getStoredRefresh();
    const token = getStoredAccess();
    
    // Call backend logout AND clear tokens atomically
    if (refresh && token) {
      try {
        const response = await fetch('/api/auth/logout/', {
          method: 'POST',
          headers: { 
            'Content-Type': 'application/json', 
            Authorization: `Bearer ${token}` 
          },
          body: JSON.stringify({ refresh }),
          // Add timeout to prevent hanging requests
          signal: AbortSignal.timeout(10000),
        });
        
        if (!response.ok) {
          console.warn(`Logout API returned ${response.status}: ${response.statusText}`);
        }
      } catch (error) {
        // Log error but don't fail - we still need to clear tokens locally
        if (error.name === 'AbortError') {
          console.error('Logout request timed out after 10s');
        } else {
          console.error('Logout API call failed:', error.message);
        }
      }
    }
    
    // Always clear tokens locally, regardless of server response
    clearStoredTokens();
    setUser(null);
  }, []);

  const fetchWithAuth = useCallback(async (url, options = {}) => {
    const doFetch = (accessToken) => {
      const headers = { ...options.headers };
      // Only set Content-Type for requests that send a body (POST/PUT/PATCH)
      const method = (options.method || 'GET').toUpperCase();
      const body = options.body;
      const skipJsonContentType =
        body instanceof FormData || body instanceof URLSearchParams;
      if (!headers['Content-Type'] && ['POST', 'PUT', 'PATCH'].includes(method) && !skipJsonContentType) {
        headers['Content-Type'] = 'application/json';
      }
      if (accessToken) headers.Authorization = `Bearer ${accessToken}`;
      return fetch(url, { ...options, headers });
    };

    // Use valid token (refresh if expired) so the first request is less likely to 401
    let access = await getValidAccessToken();
    let res = await doFetch(access);

    if (res.status === 401) {
      const newAccess = await refreshAccess();
      if (newAccess) {
        res = await doFetch(newAccess);
        return res;
      }
      clearStoredTokens();
      setUser(null);
    }

    return res;
  }, []);

  const value = {
    user,
    loading,
    isAuthenticated: !!user,
    login,
    logout,
    getAccessToken: getStoredAccess,
    getValidAccessToken,
    fetchWithAuth,
    refreshUser,
    setUser,
  };

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth() {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error('useAuth must be used within AuthProvider');
  return ctx;
}
