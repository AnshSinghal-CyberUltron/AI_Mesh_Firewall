import { useCallback, useEffect, useRef, useState } from 'react';
import { useAuth } from '../context/AuthContext';
import { resolveWebSocketBaseUrl } from '../utils/environmentUrls';

/** @typedef {{ enabled?: boolean, onEnforcementEvent?: Function, onEscalationEvent?: Function, onResolutionEvent?: Function, onMessage?: Function }} RealtimeSubscriber */

const shared = {
  ws: null,
  /** @type {Set<RealtimeSubscriber>} */
  subscribers: new Set(),
  reconnectTimeout: null,
  reconnectAttempt: 0,
  connected: false,
  lastEvent: null,
  /** @type {Set<(connected: boolean) => void>} */
  connectionListeners: new Set(),
  /** @type {Set<(event: object | null) => void>} */
  eventListeners: new Set(),
  connectGeneration: 0,
  auth: {
    isAuthenticated: false,
    getValidAccessToken: null,
  },
};

function realtimeGloballyEnabled() {
  return import.meta.env.DEV
    ? import.meta.env.VITE_ENABLE_REALTIME_NOTIFICATIONS === 'true'
    : true;
}

function anySubscriberWantsConnection() {
  if (!realtimeGloballyEnabled() || !shared.auth.isAuthenticated) return false;
  for (const subRef of shared.subscribers) {
    if (subRef.current.enabled !== false) return true;
  }
  return false;
}

function setSharedConnected(connected) {
  if (shared.connected === connected) return;
  shared.connected = connected;
  for (const listener of shared.connectionListeners) listener(connected);
}

function setSharedLastEvent(event) {
  shared.lastEvent = event;
  for (const listener of shared.eventListeners) listener(event);
}

function buildWebSocketUrl(token) {
  const base = resolveWebSocketBaseUrl();
  const normalized = base.endsWith('/') ? base.slice(0, -1) : base;
  const baseUrl = `${normalized}/ws/notifications/`;
  return token ? `${baseUrl}?token=${encodeURIComponent(token)}` : baseUrl;
}

function dispatchPayload(data) {
  const payload = data.type === 'notification_message' ? data.message : data.payload ?? data;
  if (payload) {
    setSharedLastEvent(payload);
    for (const subRef of shared.subscribers) {
      const sub = subRef.current;
      if (sub.enabled === false) continue;
      if (payload.type === 'enforcement_event' || data.type === 'notification_message') {
        sub.onEnforcementEvent?.(payload);
      }
      if (payload.type === 'escalation_event') {
        sub.onEscalationEvent?.(payload);
      }
      if (payload.type === 'resolution_event') {
        sub.onResolutionEvent?.(payload);
      }
    }
  }
  for (const subRef of shared.subscribers) {
    const sub = subRef.current;
    if (sub.enabled !== false) sub.onMessage?.(data);
  }
}

function teardownSharedSocket() {
  if (shared.reconnectTimeout) {
    clearTimeout(shared.reconnectTimeout);
    shared.reconnectTimeout = null;
  }
  if (shared.ws) {
    shared.ws.onopen = null;
    shared.ws.onclose = null;
    shared.ws.onerror = null;
    shared.ws.onmessage = null;
    shared.ws.close();
    shared.ws = null;
  }
  setSharedConnected(false);
}

async function ensureSharedSocket() {
  if (!anySubscriberWantsConnection()) {
    teardownSharedSocket();
    return;
  }
  if (shared.ws?.readyState === WebSocket.OPEN || shared.ws?.readyState === WebSocket.CONNECTING) {
    return;
  }

  if (shared.reconnectTimeout) {
    clearTimeout(shared.reconnectTimeout);
    shared.reconnectTimeout = null;
  }

  const generation = ++shared.connectGeneration;
  const token = await shared.auth.getValidAccessToken?.();
  if (generation !== shared.connectGeneration || !anySubscriberWantsConnection()) return;

  const ws = new WebSocket(buildWebSocketUrl(token));
  shared.ws = ws;

  ws.onopen = () => {
    if (generation !== shared.connectGeneration) return;
    shared.reconnectAttempt = 0;
    setSharedConnected(true);
  };

  ws.onclose = (event) => {
    if (generation !== shared.connectGeneration) return;
    setSharedConnected(false);
    shared.ws = null;
    if (!anySubscriberWantsConnection()) return;
    if (event.code === 1008 || event.code === 4001 || event.code === 4003) return;
    shared.reconnectAttempt += 1;
    const delayMs = Math.min(30000, 1500 * 2 ** (shared.reconnectAttempt - 1));
    shared.reconnectTimeout = setTimeout(() => {
      shared.reconnectTimeout = null;
      ensureSharedSocket();
    }, delayMs);
  };

  ws.onerror = () => {};

  ws.onmessage = (event) => {
    try {
      dispatchPayload(JSON.parse(event.data));
    } catch {
      // ignore non-JSON
    }
  };
}

function subscribe(subscriberRef) {
  shared.subscribers.add(subscriberRef);
  ensureSharedSocket();
  return () => {
    shared.subscribers.delete(subscriberRef);
    if (!anySubscriberWantsConnection()) {
      shared.connectGeneration += 1;
      teardownSharedSocket();
    }
  };
}

/**
 * WebSocket hook for real-time enforcement notifications.
 * Uses one shared connection per browser tab (Header + dashboard hooks must not each open their own socket).
 */
export function useRealtimeNotifications({
  enabled = true,
  onEnforcementEvent,
  onEscalationEvent,
  onResolutionEvent,
  onMessage,
} = {}) {
  const { isAuthenticated, getValidAccessToken } = useAuth();
  const [lastEvent, setLastEvent] = useState(shared.lastEvent);
  const [connected, setConnected] = useState(shared.connected);

  const subscriberRef = useRef({
    enabled,
    onEnforcementEvent,
    onEscalationEvent,
    onResolutionEvent,
    onMessage,
  });
  subscriberRef.current.enabled = enabled;
  subscriberRef.current.onEnforcementEvent = onEnforcementEvent;
  subscriberRef.current.onEscalationEvent = onEscalationEvent;
  subscriberRef.current.onResolutionEvent = onResolutionEvent;
  subscriberRef.current.onMessage = onMessage;

  useEffect(() => {
    shared.auth = { isAuthenticated, getValidAccessToken };
    ensureSharedSocket();
  }, [isAuthenticated, getValidAccessToken]);

  useEffect(() => {
    const onConn = (value) => setConnected(value);
    const onEvt = (value) => setLastEvent(value);
    shared.connectionListeners.add(onConn);
    shared.eventListeners.add(onEvt);
    return () => {
      shared.connectionListeners.delete(onConn);
      shared.eventListeners.delete(onEvt);
    };
  }, []);

  useEffect(() => {
    const unsub = subscribe(subscriberRef);
    return unsub;
  }, [enabled]);

  const reconnect = useCallback(() => {
    shared.connectGeneration += 1;
    teardownSharedSocket();
    ensureSharedSocket();
  }, []);

  return { lastEvent, connected, reconnect };
}
