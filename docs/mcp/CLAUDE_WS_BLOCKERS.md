# Claude-owned WebSocket blockers — P4.13 / P6.18 (iter37)

**Status (2026-07-02 iter37):** Gateway ws routing **LANDED** (CHG-0026 + rebuild).  
**Remaining:** control `URLField` blocks `ws://` registration → 3/4 transports only.

## Blocker summary

| Seam | Owner | Git HEAD | Live container | Blocks |
|------|-------|----------|----------------|--------|
| Gateway ws routing | Claude (`mcp_proxy.py`) | `broker_send_rpc` L1974 ✓ | same (post-rebuild) | **resolved** |
| Control url field | Claude (`models.py`) | `URLField` L41 | same | `ws://` registration |

**Note:** An uncommitted working-tree patch on `mcp_proxy.py` + `test_mcp_http_via_sandbox.py`
already implements blocker #1 (CHG-0026). It is **not committed, not rebuilt, not live**. Landing =
commit + rebuild gateway + run 4-transport `ROUNDS=3`.

---

## Blocker 1 — Gateway: route websocket via `broker_send_rpc`

**Status: LANDED** — commit `d6ab1ae7` (CHG-0026); iter37 rebuilt live gateway.  
Live container confirms `if transport in ("streamable-http", "sse", "websocket")` → `broker_send_rpc`;
no `mcp_ws_adapter` hot path.

**Historical spec (for reference if reverting):**

**File:** `gateway/ai_mesh_gateway/mcp_proxy.py`  
**Function:** `_adapter_forward` (~L1953)

### Current (HEAD + live)

```python
if transport in ("streamable-http", "sse"):
    from mcp_sandbox_client import broker_send_rpc
    # ... build up_config ...
    result = await broker_send_rpc(...)
    return JSONResponse(content=result, status_code=200)
# ...
elif transport == "websocket":
    from mcp_ws_adapter import send_jsonrpc as ws_send
    result = await ws_send(org_slug=..., url=server_config["url"], ...)
```

`_is_sandbox_routed("websocket")` already returns `True` (L1942) — the hot path contradicts it.

### Target patch

1. Extend the remote-transport branch to include `"websocket"`:

```python
if transport in ("streamable-http", "sse", "websocket"):
    from mcp_sandbox_client import broker_send_rpc
    up_config = {
        "server_slug": server_slug,
        "transport": transport,  # "websocket" for ws
        "url": upstream_url,
        "allowed_hosts": server_config.get("allowed_hosts") or [],
        "headers": dict(server_config.get("upstream_headers") or {}),
    }
    result = await broker_send_rpc(
        org_slug, up_config, method, params if params else None,
        msg_id=msg_id, oauth_token=oauth_token,
    )
    return JSONResponse(content=result, status_code=200)
```

2. **Delete** the entire `elif transport == "websocket":` block (`mcp_ws_adapter.send_jsonrpc`).

3. Update the docstring to state ALL remote transports go broker → sandbox → upstream (CHG-0026).

### Test (add or land uncommitted)

**File:** `gateway/ai_mesh_gateway/tests/test_mcp_http_via_sandbox.py`

```python
@pytest.mark.asyncio
async def test_adapter_forward_websocket_uses_broker_send_rpc():
    # patch broker_send_rpc; patch mcp_ws_adapter.send_jsonrpc → must NOT be called
    resp = await mcp_proxy._adapter_forward("websocket", server_config, ...)
    broker_rpc.assert_awaited_once()
    assert up_config["transport"] == "websocket"
    ws_send.assert_not_awaited()
```

### Verify after rebuild

```bash
docker compose build gateway && docker compose up -d gateway
grep -n 'mcp_ws_adapter' gateway/ai_mesh_gateway/mcp_proxy.py  # expect comment-only
docker exec $(docker ps -qf name=gateway) grep -n mcp_ws_adapter /app/gateway/ai_mesh_gateway/mcp_proxy.py
```

Broker + agent already support ws (`upstream_manager.py` / `ws_manager.py`); broker-direct ws PASS
(iter33).

---

## Blocker 2 — Control: accept `ws://` / `wss://` on server registration

**File:** `control/ai_mesh_control/mcp_connector/models.py`  
**Field:** `MCPServerRegistration.url` (L41)

### Current

```python
url = models.URLField(
    help_text="MCP server endpoint URL (required for http/sse/websocket, blank for stdio)",
    blank=True,
    default="",
)
```

Django `URLField` rejects `ws://ws-everything.stub:3003/mcp` at the model layer even though
`serializers.py:154-158` already allows `ws`/`wss` in `is_safe_outbound_url`.

### Option A (preferred) — CharField + existing serializer guard

```python
from django.core.validators import MaxLengthValidator

url = models.CharField(
    max_length=2048,
    blank=True,
    default="",
    validators=[MaxLengthValidator(2048)],
    help_text="MCP server endpoint URL (http/https/ws/wss for remote; blank for stdio)",
)
```

Keep `MCPServerCreateSerializer.validate` SSRF guard (`serializers.py:149-161`) as the authority.
Add migration `AlterField` for `url`.

### Option B — Custom validator on URLField

Subclass or use `django.core.validators.URLValidator(schemes=["http","https","ws","wss"])`
on a `CharField` (Django's built-in `URLField` does not accept ws/wss).

### Verify after migrate + rebuild control

```bash
python scripts/mcp_register_transport_servers.py
# Expect: websocket: ws-everything-stub (created)
# TRANSPORT_MANIFEST.zeroshield.json gains "websocket": "ws-everything-stub"
```

Stub URL (in-cluster): `ws://ws-everything.stub:3003/mcp`  
Stubs script: `scripts/mcp_transport_stubs_up.sh` (Cursor-owned; auto-called by verify harness).

---

## Post-land checklist (Cursor runs)

1. Claude commits both seams; rebuild **gateway + control**.
2. `scripts/mcp_transport_stubs_up.sh` (or auto via harness).
3. `python scripts/mcp_register_transport_servers.py` → manifest includes websocket slug.
4. `TRANSPORT_MANIFEST=mcp-parallel/findings/p4-13/TRANSPORT_MANIFEST.zeroshield.json ROUNDS=3 python scripts/mcp_sandbox_transport_verify.py`
5. Expect **4/4** transports PASS; gateway `ss -tnp | grep :443` still empty.
6. Mark P4.13/P6.18 `[x]` on scratchpad when green.

## References

- `docs/mcp/gateway-integration-checklist.md` §3.2, §7 step 5
- `docs/mcp/SANDBOX_TRANSPORT_CONTRACT.md`
- `mcp-parallel/findings/p4-13/RECHECK_ITER37.md`
- Uncommitted reference diff: `git diff gateway/ai_mesh_gateway/mcp_proxy.py` (CHG-0026)
