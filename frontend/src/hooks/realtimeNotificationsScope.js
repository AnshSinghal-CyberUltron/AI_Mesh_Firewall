import { resolveWebSocketBaseUrl } from '../utils/environmentUrls.js';

export function normalizeOrgId(value) {
  if (value == null || value === '') return null;
  const n = Number(value);
  return Number.isFinite(n) && n > 0 ? n : null;
}

/**
 * Build the notifications WebSocket URL.
 * Always attach organization_id when known so the control plane joins the
 * tenant channel (and can reject a mismatched claim for non-superusers).
 */
export function buildWebSocketUrl(token, orgId) {
  const base = resolveWebSocketBaseUrl();
  const normalized = base.endsWith('/') ? base.slice(0, -1) : base;
  const params = new URLSearchParams();
  if (token) params.set('token', token);
  const scopedOrg = normalizeOrgId(orgId);
  if (scopedOrg != null) params.set('organization_id', String(scopedOrg));
  const qs = params.toString();
  return `${normalized}/ws/notifications/${qs ? `?${qs}` : ''}`;
}

/**
 * Drop cross-tenant payloads that somehow arrive on this tab's socket.
 * Events without organization_id still pass (legacy envelopes); the server
 * group is the primary boundary.
 */
export function eventBelongsToOrg(payload, orgId) {
  const expected = normalizeOrgId(orgId);
  if (expected == null || !payload || typeof payload !== 'object') return true;
  const claimed =
    normalizeOrgId(payload.organization_id) ??
    normalizeOrgId(payload.metadata?.organization_id);
  if (claimed == null) return true;
  return claimed === expected;
}
