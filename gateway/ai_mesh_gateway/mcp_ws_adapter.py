"""MCP WebSocket Transport Adapter for the ZeroShield Gateway.

Manages WebSocket connections to MCP servers that expose a WebSocket
transport. Opens the connection on demand, sends JSON-RPC messages,
and parses responses.

Lifecycle:
  1. Gateway receives a JSON-RPC request for a websocket-transport server
  2. Adapter opens the WebSocket if not already connected (lazy start)
  3. Request is sent as a JSON-RPC text frame
  4. Response is read from incoming frames
  5. Connection is kept alive for reuse (with idle timeout cleanup)

Security:
  - Only URLs registered via the backend API can be connected to
  - TLS verification is enforced for wss:// URLs
"""

import asyncio
import json
import logging
import os
import time
from dataclasses import dataclass, field

import websockets
import websockets.client

LOG = logging.getLogger("gateway.mcp_ws_adapter")

# How long an idle connection lives before being closed (seconds)
_IDLE_TIMEOUT = int(os.environ.get("MCP_WS_IDLE_TIMEOUT", "600"))
# Max concurrent WebSocket connections
_MAX_CONNECTIONS = int(os.environ.get("MCP_WS_MAX_CONNECTIONS", "50"))


@dataclass
class WsConnection:
    """Tracks a live WebSocket connection to an MCP server."""
    key: str
    url: str
    ws: websockets.client.ClientConnection | None = None
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    last_used: float = field(default_factory=time.time)
    initialized: bool = False
    _msg_id_counter: int = 0
    _pending: dict[int | str, asyncio.Future] = field(default_factory=dict)
    _reader_task: asyncio.Task | None = None
    auth_headers: dict[str, str] = field(default_factory=dict)

    def next_id(self) -> int:
        self._msg_id_counter += 1
        return self._msg_id_counter


# Global registry: key → WsConnection
_connections: dict[str, WsConnection] = {}
_registry_lock = asyncio.Lock()
_reaper_task: asyncio.Task | None = None


def _connection_key(org_slug: str, server_slug: str) -> str:
    return f"{org_slug}/{server_slug}"


async def _start_reader(conn: WsConnection):
    """Background task that reads WebSocket messages and resolves pending futures."""
    assert conn.ws is not None
    try:
        async for raw_msg in conn.ws:
            if isinstance(raw_msg, bytes):
                raw_msg = raw_msg.decode(errors="replace")
            try:
                msg = json.loads(raw_msg)
            except json.JSONDecodeError:
                LOG.debug("WS %s non-JSON message: %s", conn.key, raw_msg[:200])
                continue

            msg_id = msg.get("id")
            if msg_id is not None and msg_id in conn._pending:
                fut = conn._pending.pop(msg_id)
                if not fut.done():
                    fut.set_result(msg)
            else:
                LOG.debug("WS %s notification: %s", conn.key, str(msg)[:200])
    except websockets.exceptions.ConnectionClosed as exc:
        LOG.info("WS %s connection closed: %s", conn.key, exc)
    except asyncio.CancelledError:
        pass
    except Exception as exc:
        LOG.error("WS reader for %s crashed: %s", conn.key, exc)
    finally:
        for fut in conn._pending.values():
            if not fut.done():
                fut.set_exception(RuntimeError(f"WebSocket connection {conn.key} closed"))
        conn._pending.clear()


async def _ensure_connection(key: str, url: str,
                             auth_headers: dict[str, str] | None = None) -> WsConnection:
    """Get or open a WebSocket connection for the given server."""
    async with _registry_lock:
        if key in _connections:
            conn = _connections[key]
            if conn.ws and conn.ws.state.name == "OPEN":
                conn.last_used = time.time()
                return conn
            # Connection closed — remove and re-create
            LOG.warning("WS connection %s closed, reconnecting", key)
            _connections.pop(key, None)

        if len(_connections) >= _MAX_CONNECTIONS:
            oldest_key = min(_connections, key=lambda k: _connections[k].last_used)
            await _close_connection(oldest_key)

        extra_headers = auth_headers or {}
        LOG.info("Opening WebSocket connection to %s (key=%s)", url, key)

        try:
            ws = await websockets.client.connect(
                url,
                additional_headers=extra_headers,
                open_timeout=15,
                close_timeout=5,
                max_size=10 * 1024 * 1024,  # 10MB max message
            )
        except Exception as exc:
            raise RuntimeError(f"Failed to connect to WebSocket MCP server at {url}: {exc}")

        conn = WsConnection(
            key=key,
            url=url,
            ws=ws,
            auth_headers=extra_headers,
        )
        conn._reader_task = asyncio.create_task(_start_reader(conn))
        _connections[key] = conn
        return conn


async def _close_connection(key: str):
    """Close and clean up a WebSocket connection."""
    conn = _connections.pop(key, None)
    if not conn:
        return
    if conn._reader_task and not conn._reader_task.done():
        conn._reader_task.cancel()
    if conn.ws:
        try:
            await conn.ws.close()
        except Exception:
            pass
    LOG.info("WS connection %s closed", key)


async def _send_message(conn: WsConnection, message: dict, timeout: float = 30.0) -> dict:
    """Send a JSON-RPC message and wait for the response."""
    if not conn.ws or conn.ws.state.name != "OPEN":
        raise RuntimeError(f"WebSocket connection {conn.key} is not open")

    msg_id = message.get("id")
    is_notification = msg_id is None

    if not is_notification:
        loop = asyncio.get_event_loop()
        fut: asyncio.Future = loop.create_future()
        conn._pending[msg_id] = fut

    await conn.ws.send(json.dumps(message))
    conn.last_used = time.time()

    if is_notification:
        return {}

    try:
        return await asyncio.wait_for(fut, timeout=timeout)
    except asyncio.TimeoutError:
        conn._pending.pop(msg_id, None)
        raise RuntimeError(f"WebSocket {conn.key} timed out waiting for response to message {msg_id}")


async def _ensure_initialized(conn: WsConnection):
    """Send MCP initialize + initialized if not already done."""
    if conn.initialized:
        return
    async with conn.lock:
        if conn.initialized:
            return

        init_id = conn.next_id()
        init_msg = {
            "jsonrpc": "2.0",
            "id": init_id,
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {
                    "name": "ZeroShield Gateway",
                    "version": "1.0.0",
                },
            },
        }
        resp = await _send_message(conn, init_msg, timeout=30)
        if "error" in resp:
            raise RuntimeError(f"WebSocket initialize failed: {resp['error']}")

        LOG.info("WS %s initialized: %s", conn.key, json.dumps(resp.get("result", {}))[:200])

        await _send_message(conn, {
            "jsonrpc": "2.0",
            "method": "notifications/initialized",
        })
        conn.initialized = True


async def send_jsonrpc(org_slug: str, server_slug: str,
                       url: str,
                       auth_headers: dict[str, str] | None,
                       method: str, params: dict | None,
                       msg_id: int | str | None) -> dict:
    """Send a JSON-RPC message to a WebSocket MCP server and return the response.

    This is the main entry point called by the gateway JSON-RPC handler.
    """
    key = _connection_key(org_slug, server_slug)
    conn = await _ensure_connection(key, url, auth_headers)
    await _ensure_initialized(conn)

    if method == "initialize":
        return {
            "jsonrpc": "2.0",
            "id": msg_id,
            "result": {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {
                    "name": f"ZeroShield Gateway — {server_slug} (websocket)",
                    "version": "1.0.0",
                },
            },
        }

    if method == "notifications/initialized":
        return {}

    use_id = msg_id if msg_id is not None else conn.next_id()
    message = {
        "jsonrpc": "2.0",
        "id": use_id,
        "method": method,
    }
    if params is not None:
        message["params"] = params

    resp = await _send_message(conn, message, timeout=60)
    if msg_id is not None:
        resp["id"] = msg_id
    return resp


async def shutdown_all():
    """Close all WebSocket connections. Called during gateway shutdown."""
    keys = list(_connections.keys())
    for key in keys:
        await _close_connection(key)
    LOG.info("All WebSocket MCP connections closed")


async def list_connections() -> list[dict]:
    """Return status of all managed WebSocket connections."""
    result = []
    for key, conn in _connections.items():
        result.append({
            "key": key,
            "url": conn.url,
            "connected": conn.ws is not None and conn.ws.state.name == "OPEN",
            "initialized": conn.initialized,
            "last_used": conn.last_used,
        })
    return result


async def _reaper_loop():
    """Periodically close idle WebSocket connections."""
    while True:
        await asyncio.sleep(60)
        now = time.time()
        to_close = [
            key for key, conn in _connections.items()
            if now - conn.last_used > _IDLE_TIMEOUT
        ]
        for key in to_close:
            LOG.info("Reaping idle WS connection: %s", key)
            await _close_connection(key)


def start_reaper():
    """Start the background reaper task."""
    global _reaper_task
    if _reaper_task is None or _reaper_task.done():
        _reaper_task = asyncio.create_task(_reaper_loop())
