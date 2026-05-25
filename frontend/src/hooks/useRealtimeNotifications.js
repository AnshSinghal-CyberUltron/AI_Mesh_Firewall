import { useCallback, useEffect, useRef, useState } from 'react';
import { useAuth } from '../context/AuthContext';
import { resolveWebSocketBaseUrl } from '../utils/environmentUrls';

/**
 * WebSocket hook for real-time enforcement notifications.
 * Connects to /ws/notifications/ with JWT in query when user is authenticated.
 * @param {Object} options
 * @param {boolean} [options.enabled=true] - Whether to connect.
 * @param {function} [options.onEnforcementEvent] - Called when type === 'enforcement_event' with payload.
 * @param {function} [options.onEscalationEvent] - Called when type === 'escalation_event' with payload.
 * @param {function} [options.onResolutionEvent] - Called when type === 'resolution_event' with payload.
 * @param {function} [options.onMessage] - Called for every JSON message (e.g. pong).
 * @returns {{ lastEvent: object | null, connected: boolean }}
 */
export function useRealtimeNotifications({ enabled = true, onEnforcementEvent, onEscalationEvent, onResolutionEvent, onMessage } = {}) {
  const { isAuthenticated, getValidAccessToken } = useAuth();
  const realtimeEnabled = import.meta.env.DEV
    ? import.meta.env.VITE_ENABLE_REALTIME_NOTIFICATIONS === "true"
    : true;
  const [lastEvent, setLastEvent] = useState(null);
  const [connected, setConnected] = useState(false);
  const wsRef = useRef(null);
  const reconnectTimeoutRef = useRef(null);
  const reconnectAttemptRef = useRef(0);
  const onEnforcementEventRef = useRef(onEnforcementEvent);
  const onEscalationEventRef = useRef(onEscalationEvent);
  const onResolutionEventRef = useRef(onResolutionEvent);
  const onMessageRef = useRef(onMessage);
  onEnforcementEventRef.current = onEnforcementEvent;
  onEscalationEventRef.current = onEscalationEvent;
  onResolutionEventRef.current = onResolutionEvent;
  onMessageRef.current = onMessage;

  const buildWebSocketUrl = useCallback((token) => {
    const base = resolveWebSocketBaseUrl();
    const normalized = base.endsWith("/") ? base.slice(0, -1) : base;
    const baseUrl = `${normalized}/ws/notifications/`;
    return token ? `${baseUrl}?token=${encodeURIComponent(token)}` : baseUrl;
  }, []);

  const connect = useCallback(async () => {
    if (!realtimeEnabled || !enabled || !isAuthenticated) return;
    if (wsRef.current?.readyState === WebSocket.OPEN) return;
    if (reconnectTimeoutRef.current) {
      clearTimeout(reconnectTimeoutRef.current);
      reconnectTimeoutRef.current = null;
    }

    const token = await getValidAccessToken?.();
    const url = buildWebSocketUrl(token);
    const ws = new WebSocket(url);
    wsRef.current = ws;

    ws.onopen = () => {
      reconnectAttemptRef.current = 0;
      setConnected(true);
    };
    ws.onclose = (event) => {
      setConnected(false);
      wsRef.current = null;
      if (enabled && isAuthenticated) {
        if (event.code === 1008 || event.code === 4001 || event.code === 4003) return;
        reconnectAttemptRef.current += 1;
        const delayMs = Math.min(30000, 1500 * 2 ** (reconnectAttemptRef.current - 1));
        reconnectTimeoutRef.current = setTimeout(connect, delayMs);
      }
    };
    ws.onerror = () => {};
    ws.onmessage = (event) => {
      try {
        const data = JSON.parse(event.data);
        const payload = data.type === 'notification_message' ? data.message : data.payload ?? data;
        if (payload) {
          setLastEvent(payload);
          if (payload.type === 'enforcement_event' || data.type === 'notification_message') {
            if (onEnforcementEventRef.current) onEnforcementEventRef.current(payload);
          }
          if (payload.type === 'escalation_event') {
            if (onEscalationEventRef.current) onEscalationEventRef.current(payload);
          }
          if (payload.type === 'resolution_event') {
            if (onResolutionEventRef.current) onResolutionEventRef.current(payload);
          }
        }
        if (onMessageRef.current) onMessageRef.current(data);
      } catch {
        // ignore non-JSON
      }
    };
  }, [realtimeEnabled, enabled, isAuthenticated, getValidAccessToken, buildWebSocketUrl]);

  useEffect(() => {
    connect();
    return () => {
      if (reconnectTimeoutRef.current) clearTimeout(reconnectTimeoutRef.current);
      reconnectTimeoutRef.current = null;
      if (wsRef.current) {
        wsRef.current.close();
        wsRef.current = null;
      }
      setConnected(false);
    };
  }, [connect]);

  return { lastEvent, connected };
}
