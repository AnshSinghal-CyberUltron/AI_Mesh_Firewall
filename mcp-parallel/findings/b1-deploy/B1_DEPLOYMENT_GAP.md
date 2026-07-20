# B1 (item#13) — committed but NOT deployed (fake-green), now live

## Discovery
While verifying item#31 (OAuth/transport correctness), a stdio `oauth/authorize/`
POST returned the OLD message **"Server has no URL; OAuth is only for HTTP
transports."** — the exact string item#13/B1 was supposed to make UNREACHABLE for
stdio. Grep inside the running control container proved the deployed
`/app/control/ai_mesh_control/mcp_connector/views.py` had ONLY the old url-check
(line 2526); the item#13 transport guard (`oauth_http_transport_error` /
`_OAUTH_HTTP_TRANSPORTS`) existed in committed source but not in the running image.

Root: control runs `daphne main_app.asgi:application` on a BAKED image (no source
mount), and the image predates the item#13 commit. So B1 passed its unit tests +
git log, but was FALSE in the live system — a fake-green.

## Fix (runtime)
`docker cp control/ai_mesh_control/mcp_connector/views.py ai_mesh_firewall-control-1:/app/...`
then `docker restart ai_mesh_firewall-control-1`. Live probe after:

    POST /servers/{stdio_id}/oauth/authorize/  → 400
      "OAuth 2.1 (authorize via provider) requires an HTTP MCP transport
       (streamable-http or sse). stdio servers such as Linear via mcp-remote
       authorize upstream inside the gateway sandbox ... leave the auth type as 'none'."

"Server has no URL" is now unreachable for stdio; the row is never flipped to oauth.
Guard confirmed present in the live container (`grep -c 'requires an HTTP MCP
transport'` = 1).

## Durability
The fix is committed in source (item#13), so a control image rebuild includes it;
the docker cp makes it live on the CURRENT running container. If control is ever
recreated (compose down/up) from the stale image, re-apply the cp or rebuild.

## Lesson
"Committed" ≠ "deployed". For baked-image services (daphne/gunicorn, no source
mount) a LIVE probe is the only proof a backend fix is in effect — grep the running
container AND exercise the real endpoint; never trust unit test + git log alone.
