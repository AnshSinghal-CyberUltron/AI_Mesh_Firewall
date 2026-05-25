import { useState, useRef, useCallback, useEffect } from 'react';
import { useAuth } from '../context/AuthContext';

const CHECK_INTERVAL_MS = 30 * 1000;
const THROTTLE_MS = 1000;

const EVENTS = ['mousemove', 'mousedown', 'keydown', 'scroll', 'touchstart'];

/**
 * Custom hook: inactivity auto-logout with "Stay logged in" warning.
 * @param {number} inactivityMs - Total inactivity before logout (default 15 min).
 * @param {number} warningBeforeMs - Show warning this long before logout (default 1 min).
 * @returns {{ showWarning: boolean, stayLoggedIn: function }}
 */
export function useInactivityLogout(
  inactivityMs = 15 * 60 * 1000,
  warningBeforeMs = 60 * 1000
) {
  const { logout, isAuthenticated } = useAuth();
  const warningAtMs = inactivityMs - warningBeforeMs;

  const lastActivityAt = useRef(Date.now());
  const logoutTimerRef = useRef(null);
  const throttleRef = useRef(0);
  const warningShownRef = useRef(false);

  const [showWarning, setShowWarning] = useState(false);

  const stayLoggedIn = useCallback(() => {
    lastActivityAt.current = Date.now();
    warningShownRef.current = false;
    if (logoutTimerRef.current) {
      clearTimeout(logoutTimerRef.current);
      logoutTimerRef.current = null;
    }
    setShowWarning(false);
  }, []);

  useEffect(() => {
    if (!isAuthenticated) return;

    function updateActivity() {
      const now = Date.now();
      if (now - throttleRef.current < THROTTLE_MS) return;
      throttleRef.current = now;
      lastActivityAt.current = now;
    }

    function checkIdle() {
      const idleMs = Date.now() - lastActivityAt.current;

      if (idleMs >= inactivityMs) {
        if (logoutTimerRef.current) {
          clearTimeout(logoutTimerRef.current);
          logoutTimerRef.current = null;
        }
        warningShownRef.current = false;
        setShowWarning(false);
        logout();
        return;
      }

      if (idleMs >= warningAtMs && !warningShownRef.current) {
        warningShownRef.current = true;
        setShowWarning(true);
        logoutTimerRef.current = setTimeout(() => {
          logoutTimerRef.current = null;
          setShowWarning(false);
          logout();
        }, warningBeforeMs);
      }
    }

    EVENTS.forEach((ev) => window.addEventListener(ev, updateActivity));
    const intervalId = setInterval(checkIdle, CHECK_INTERVAL_MS);
    checkIdle();

    return () => {
      EVENTS.forEach((ev) => window.removeEventListener(ev, updateActivity));
      clearInterval(intervalId);
      if (logoutTimerRef.current) {
        clearTimeout(logoutTimerRef.current);
        logoutTimerRef.current = null;
      }
    };
  }, [isAuthenticated, inactivityMs, warningAtMs, warningBeforeMs, logout]);

  return { showWarning, stayLoggedIn };
}
