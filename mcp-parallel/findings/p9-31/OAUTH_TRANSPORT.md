# P9 item #31 — OAuth / transport correctness across the fleet under load

**Harness:** `scripts/mcp_oauth_transport_live.py`

## Invariants proven

1. **No stdio server ever attempts OAuth.** For every stdio server in every org:
   - `auth_type='none'`, `needs_reauth=false`;
   - the control authorize endpoint REJECTS an oauth-start with a clear transport
     error (400) and NEVER flips the row to `auth_type='oauth'`;
   - under concurrent load, no stdio tool response emits an OAuth signal.
2. **HTTP oauth servers authorize cleanly.** An authorized streamable-http+oauth
   server reports `oauth_authorized=true`, `tools_count>0`, `needs_reauth=false`
   (the item#16 Linear server: 47 tools). Unauthorized HTTP-oauth rows are valid
   PENDING state (tools_count=0), not failures.

## KEY FINDING — B1 (item#13) was committed but NOT deployed (fake-green, now fixed)

The harness's `stdio.oauth_start_rejected` check first FAILED: a stdio oauth-start
returned **`"Server has no URL; OAuth is only for HTTP transports."`** — the OLD
message. Investigation (grep inside the running `ai_mesh_firewall-control-1`
container) proved the deployed `mcp_connector/views.py` had **only the old url-check
at line 2526** — the item#13 transport guard (`oauth_http_transport_error` /
`_OAUTH_HTTP_TRANSPORTS`) was in committed source but **not in the running image**
(control is daphne on a baked image, no source mount). So B1's requirement — *"make
'Server has no URL' unreachable for stdio, replace with a clear transport error"* —
was passing unit tests but **false in the live system**.

**Fix:** hot-deployed the committed `views.py` (`docker cp` → `docker restart
ai_mesh_firewall-control-1`). Live probe after deploy:

```
POST /servers/{stdio_id}/oauth/authorize/  →  400
  "OAuth 2.1 (authorize via provider) requires an HTTP MCP transport
   (streamable-http or sse). stdio servers such as Linear via mcp-remote
   authorize upstream inside the gateway sandbox ... leave the auth type as 'none'."
```

"Server has no URL" is now unreachable for stdio; the row is never flipped to oauth.
Durability: the fix is committed in source, so a control image rebuild includes it;
the `docker cp` makes it live on the current running container.

## Result — 3× consecutive GREEN

| Run | Checks | Passed | Failures | Load probe |
|-----|--------|--------|----------|------------|
| 1 | 67 | 67 | 0 | oauth-signals=0, backpressure=0 |
| 2 | 67 | 67 | 0 | oauth-signals=0, backpressure=0 |
| 3 | 67 | 67 | 0 | oauth-signals=0, backpressure=0 |

**Harness:** `gateway/.venv/bin/python scripts/mcp_oauth_transport_live.py`

**Fleet coverage:** 3 orgs × (5 scale stdio Everything + extra stdio rows on zeroshield
including `linear-mcp`, `linear-remote`) + HTTP-oauth rows (`linear-manual-oauth`
authorized 47 tools, `stub-oauth` pending 0 tools). Every stdio row:
`auth_type=none`, oauth-start rejected with transport error (not "no URL"), never
flipped to oauth. Under 4× concurrent 15-wide echo load: **0 OAuth signals**.

**UI corroboration:** `scripts/playwright_mcp_b1_verify.mjs` 8/8 PASS (stdio = one
gateway Authorize, no "no URL"; HTTP-oauth = pending + one Authorize).

Evidence: `mcp-parallel/findings/p9-31/oauth_transport_run{1,2,3}.json`

## Reusable pattern

An "OAuth attempt" signal must be SPECIFIC (`authorization_url`, `www-authenticate`,
`-32001`, "authorization required") — a generic non-200 under load is backpressure
(item#29), NOT an oauth attempt; conflating them makes the check flaky. Also: never
put the substring "oauth" in a probe/echo payload — a benign echo will self-match.
"Committed" ≠ "deployed": for baked-image services (daphne/gunicorn, no source
mount) a live probe is the ONLY proof a backend fix is in effect — grep the running
container + exercise the real endpoint, don't trust the unit test + git log.
