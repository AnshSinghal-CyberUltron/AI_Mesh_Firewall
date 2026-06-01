# Decision final — org-scoped policy enable/disable (post-triage)

## Triage synthesis

| Agent | Verdict | Adopted |
|-------|---------|---------|
| MINIMAL | Partial — PATCH toggle enough for UX | Adopted toggle; **rejected** skipping compile fix |
| SECURITY | Conditional approve | Org on create, is_system guards, org-scoped compile |
| BACKEND | Approve per-org compile | `policies:pending_orgs` + task loop |
| UX-MAX | Approve | Inline toggles, status filter, hide system delete |
| SKEPTIC | Confirmed compile bug | Per-org Redis push is mandatory for gateway sync |

## Implemented

1. **Frontend** — `EnableToggle` on policy + rule rows; enabled filter; system badge; hide delete for `is_system`
2. **Backend** — Per-org compile via `PENDING_ORGS_KEY`; create requires org; system policy code delete blocked
3. **Compile API** — uses `get_request_organization`
4. **Tests** — `core.tests.test_policy_org_crud`

## Deferred

- Dedicated `POST .../toggle-enabled/` endpoint (PATCH sufficient)
- Per-org unique `Policy.code` migration
