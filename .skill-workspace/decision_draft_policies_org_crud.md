# Decision draft — org-scoped policy enable/disable/CRUD

## Analysis

Backend already has:
- `Policy.enabled` boolean
- Org-scoped `PolicyViewSet.get_queryset()` 
- PATCH `{"enabled": false}` documented
- Signal-driven recompile on save/delete
- `PolicyManagementPanel` with edit modal `enabled` checkbox but **no inline toggle**

Gaps:
1. **Frontend UX**: no one-click enable/disable on policy list; rules same
2. **Compile push wrong Redis key**: `compile_policies_task()` calls `compile_and_push()` without `organization` → bundle goes to `policies:compiled:default` with ALL orgs' policies mixed
3. **Create without org**: `perform_create` saves `organization=None` if user has no org profile
4. **System policies**: delete blocked but UI still shows trash icon

## D1 — Inline toggle on policy + rule rows (frontend)
PATCH `{ enabled, version }` — minimal payload, optimistic UI with rollback.

## D2 — Per-org Redis compile on signal (backend)
Track `policies:pending_orgs` in Redis; task compiles each affected org's bundle to `policies:compiled:{slug}`.

## D3 — Harden org boundaries (backend)
- Require org on create (403 if missing)
- Block delete/update destructive fields on `is_system` except `enabled`
- Fix compile view to use `get_request_organization`

## D4 — Enabled filter on list (frontend + existing API `?enabled=`)

## D5 — Tests for toggle + org compile + cross-org 404
