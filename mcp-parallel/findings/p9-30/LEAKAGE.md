# P9 item #30 — cross-tenant LEAKAGE proof (canary in one org)

**Harness:** `scripts/mcp_leakage_live.py` (VICTIM=org-b)
**Invariant:** one sandbox per org; NO cross-tenant tool / data / credential /
result visibility on ANY channel; an org can never invoke another org's servers.

## Method

Plant a unique `CANARY-<uuid>` as **persisted tenant data** in the victim org
(register a server whose name+description carry the canary), confirm the OWNER can
see it (positive control), then prove NO other org can observe it — or invoke the
victim's servers — on every channel. Canary server is deleted at the end (fleet
restored to 3×5=15).

## Result — PASS 3× consecutively, 26/26 checks each

| # | channel | check | result |
|---|---------|-------|--------|
| plant | register canary server in victim | 201, id+slug returned | ✓ |
| pos | owner list + detail see canary | 200, canary present | ✓ (control) |
| A | attacker `GET /servers/` (×2 orgs) | 200, **canary absent** | ✓ |
| B | attacker `GET /servers/{victim_id}/` | **404** (org-scoped), no canary | ✓ |
| C | attacker `GET /servers/{victim_id}/tools/` | **404**, no canary | ✓ |
| D | attacker key → victim gateway path | **403** | ✓ |
| E | attacker `GET /events/` | 200, **canary absent** | ✓ |
| D2 | N×N invocation matrix (3 own, 6 foreign) | own→**200**, foreign→**403** | ✓ |
| F | victim echoes canary 100× while attackers probe concurrently | victim carried canary 100×, **attacker bleed = 0** | ✓ |
| oracle | canary absent in ALL attacker-visible bytes | 0 hits in ~240 KB | ✓ |
| cleanup | delete canary server | **204**, gone from owner list | ✓ |

3 runs × 26 checks = 78/78 green. `failures: []` every run.

## Why each channel matters

- **A/E enumeration+audit**: list/audit views are org-scoped (`_org_scoped_servers_queryset`)
  → an attacker's own list/audit can never surface another org's row.
- **B/C object-level authz**: fetching the victim's server by its *exact id* returns
  404 (not 403-with-body, not 200) — the row is invisible, not merely forbidden, so
  no metadata (incl. the canary) leaks. Object authz is enforced, not just list filtering.
- **D/D2 gateway path auth**: an org's gateway key is bound to its org; presenting it
  against another org's `/gateway/{other}/mcp/...` path → 403. The full N×N matrix
  (every key × every path) confirms own=200 / all-foreign=403 — no path-traversal.
- **F data-plane**: even with the canary *actively flowing* through the victim's live
  tool calls, concurrent attacker calls on their OWN servers never receive it — proving
  per-org sandbox result isolation under contention (complements items #27/#28 xtenant=0).

## Gotcha (harness robustness, not a product defect)

Running the harness back-to-back many times tripped the DRF token-endpoint throttle
(`/api/auth/token/` returned an empty body → empty `Bearer ` → httpx
`LocalProtocolError` / spurious 401s that looked like check failures). Fixed with a
bounded login retry+backoff; a login must never yield an empty token. The endpoint
recovers within seconds — it is rate-limiting, not an outage.
