"""MCP Stdio Transport Adapter for the ZeroShield Gateway.

Manages stdio-based MCP server processes. Spawns them on demand, communicates
via JSON-RPC over stdin/stdout, and provides an async interface that the
gateway JSON-RPC handler can call.

Lifecycle:
  1. Gateway receives a JSON-RPC request for a stdio-transport server
  2. Adapter spawns the process if not already running (lazy start)
  3. Request is written to stdin as a JSON-RPC line
  4. Response is read from stdout (line-delimited JSON-RPC)
  5. Process is kept alive for reuse (with idle timeout cleanup)

Security:
  - Only commands registered via the backend API can be spawned
  - No shell expansion (subprocess with shell=False)
  - Environment variables are sandboxed per-server
"""

import asyncio
import json
import logging
import os
import signal
import time
from dataclasses import dataclass, field

LOG = logging.getLogger("gateway.mcp_stdio_adapter")

# How long an idle process lives before being reaped (seconds)
_IDLE_TIMEOUT = int(os.environ.get("MCP_STDIO_IDLE_TIMEOUT", "600"))
# Max concurrent stdio processes
_MAX_PROCESSES = int(os.environ.get("MCP_STDIO_MAX_PROCESSES", "20"))


@dataclass
class StdioProcess:
    """Tracks a running stdio MCP server process."""
    key: str
    command: str
    args: list[str]
    env: dict[str, str]
    process: asyncio.subprocess.Process | None = None
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    last_used: float = field(default_factory=time.time)
    initialized: bool = False
    _msg_id_counter: int = 0
    _pending: dict[int | str, asyncio.Future] = field(default_factory=dict)
    _reader_task: asyncio.Task | None = None

    def next_id(self) -> int:
        self._msg_id_counter += 1
        return self._msg_id_counter


# Global registry: key → StdioProcess
_processes: dict[str, StdioProcess] = {}
_registry_lock = asyncio.Lock()
_reaper_task: asyncio.Task | None = None


def _process_key(org_slug: str, server_slug: str) -> str:
    return f"{org_slug}/{server_slug}"


async def _start_reader(proc: StdioProcess):
    """Background task that reads stdout lines and resolves pending futures."""
    assert proc.process and proc.process.stdout
    try:
        while True:
            line = await proc.process.stdout.readline()
            if not line:
                LOG.info("Stdio process %s stdout closed", proc.key)
                break
            line = line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                LOG.debug("Stdio %s non-JSON line: %s", proc.key, line[:200])
                continue

            msg_id = msg.get("id")
            if msg_id is not None and msg_id in proc._pending:
                fut = proc._pending.pop(msg_id)
                if not fut.done():
                    fut.set_result(msg)
            else:
                # Notification or unsolicited message — log it
                LOG.debug("Stdio %s notification: %s", proc.key, str(msg)[:200])
    except asyncio.CancelledError:
        pass
    except Exception as exc:
        LOG.error("Stdio reader for %s crashed: %s", proc.key, exc)
    finally:
        # Mark all pending futures as failed
        for fut in proc._pending.values():
            if not fut.done():
                fut.set_exception(RuntimeError(f"Stdio process {proc.key} disconnected"))
        proc._pending.clear()


async def _ensure_process(key: str, command: str, args: list[str],
                          env: dict[str, str] | None = None) -> StdioProcess:
    """Get or start a stdio process for the given server."""
    async with _registry_lock:
        if key in _processes:
            proc = _processes[key]
            if proc.process and proc.process.returncode is None:
                proc.last_used = time.time()
                return proc
            # Process died — remove and re-create
            LOG.warning("Stdio process %s died (rc=%s), restarting", key, proc.process.returncode if proc.process else "?")
            _processes.pop(key, None)

        if len(_processes) >= _MAX_PROCESSES:
            # Evict oldest idle process
            oldest_key = min(_processes, key=lambda k: _processes[k].last_used)
            await _kill_process(oldest_key)

        proc_env = {**os.environ, **(env or {})}
        # Remove potentially dangerous env vars
        for dangerous in ("LD_PRELOAD", "DYLD_INSERT_LIBRARIES"):
            proc_env.pop(dangerous, None)

        # Per-org token isolation for mcp-remote: each org gets its own
        # MCP_REMOTE_CONFIG_DIR so OAuth tokens cannot leak across orgs.
        org_slug = key.split("/")[0] if "/" in key else "default"
        proc_env["MCP_REMOTE_CONFIG_DIR"] = f"/tmp/mcp-orgs/{org_slug}/mcp-auth"

        LOG.info("Starting stdio MCP process: %s %s (key=%s)", command, args, key)
        try:
            process = await asyncio.create_subprocess_exec(
                command, *args,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=proc_env,
            )
        except FileNotFoundError:
            raise RuntimeError(f"Command not found: {command}")
        except PermissionError:
            raise RuntimeError(f"Permission denied: {command}")

        proc = StdioProcess(
            key=key,
            command=command,
            args=args,
            env=env or {},
            process=process,
        )
        proc._reader_task = asyncio.create_task(_start_reader(proc))
        _processes[key] = proc

        # Start background stderr logger
        asyncio.create_task(_log_stderr(proc))

        return proc


async def _log_stderr(proc: StdioProcess):
    """Log stderr output from the stdio process."""
    assert proc.process and proc.process.stderr
    try:
        while True:
            line = await proc.process.stderr.readline()
            if not line:
                break
            LOG.debug("Stdio %s stderr: %s", proc.key, line.decode(errors="replace").strip())
    except asyncio.CancelledError:
        pass
    except Exception:
        pass


async def _kill_process(key: str):
    """Terminate and clean up a stdio process."""
    proc = _processes.pop(key, None)
    if not proc or not proc.process:
        return
    if proc._reader_task and not proc._reader_task.done():
        proc._reader_task.cancel()
    try:
        proc.process.terminate()
        try:
            await asyncio.wait_for(proc.process.wait(), timeout=5)
        except asyncio.TimeoutError:
            proc.process.kill()
            await proc.process.wait()
    except ProcessLookupError:
        pass
    LOG.info("Stdio process %s terminated", key)


async def _send_message(proc: StdioProcess, message: dict, timeout: float = 30.0) -> dict:
    """Send a JSON-RPC message to the stdio process and wait for the response."""
    if not proc.process or proc.process.returncode is not None:
        raise RuntimeError(f"Stdio process {proc.key} is not running")

    msg_id = message.get("id")
    is_notification = msg_id is None

    line = json.dumps(message) + "\n"

    if not is_notification:
        loop = asyncio.get_event_loop()
        fut: asyncio.Future = loop.create_future()
        proc._pending[msg_id] = fut

    proc.process.stdin.write(line.encode())
    await proc.process.stdin.drain()
    proc.last_used = time.time()

    if is_notification:
        return {}

    try:
        return await asyncio.wait_for(fut, timeout=timeout)
    except asyncio.TimeoutError:
        proc._pending.pop(msg_id, None)
        raise RuntimeError(f"Stdio process {proc.key} timed out waiting for response to message {msg_id}")


async def _ensure_initialized(proc: StdioProcess):
    """Send MCP initialize + initialized if not already done."""
    if proc.initialized:
        return
    async with proc.lock:
        if proc.initialized:
            return

        init_id = proc.next_id()
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
        resp = await _send_message(proc, init_msg, timeout=30)
        if "error" in resp:
            raise RuntimeError(f"Stdio initialize failed: {resp['error']}")

        LOG.info("Stdio %s initialized: %s", proc.key, json.dumps(resp.get("result", {}))[:200])

        # Send notifications/initialized (no response expected)
        await _send_message(proc, {
            "jsonrpc": "2.0",
            "method": "notifications/initialized",
        })
        proc.initialized = True


async def send_jsonrpc(org_slug: str, server_slug: str,
                       command: str, args: list[str],
                       env: dict[str, str] | None,
                       method: str, params: dict | None,
                       msg_id: int | str | None) -> dict:
    """Send a JSON-RPC message to a stdio MCP server and return the response.

    This is the main entry point called by the gateway JSON-RPC handler.
    """
    key = _process_key(org_slug, server_slug)
    proc = await _ensure_process(key, command, args, env)
    await _ensure_initialized(proc)

    if method == "initialize":
        # Already initialized — return cached capabilities
        return {
            "jsonrpc": "2.0",
            "id": msg_id,
            "result": {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {
                    "name": f"ZeroShield Gateway — {server_slug} (stdio)",
                    "version": "1.0.0",
                },
            },
        }

    if method == "notifications/initialized":
        return {}

    # For all other methods, forward to the process
    use_id = msg_id if msg_id is not None else proc.next_id()
    message = {
        "jsonrpc": "2.0",
        "id": use_id,
        "method": method,
    }
    if params is not None:
        message["params"] = params

    resp = await _send_message(proc, message, timeout=60)
    # Re-map the id to the original request id
    if msg_id is not None:
        resp["id"] = msg_id
    return resp


async def shutdown_all():
    """Terminate all stdio processes. Called during gateway shutdown."""
    keys = list(_processes.keys())
    for key in keys:
        await _kill_process(key)
    LOG.info("All stdio MCP processes terminated")


async def list_processes() -> list[dict]:
    """Return status of all managed stdio processes."""
    result = []
    for key, proc in _processes.items():
        result.append({
            "key": key,
            "command": proc.command,
            "args": proc.args,
            "running": proc.process is not None and proc.process.returncode is None,
            "initialized": proc.initialized,
            "last_used": proc.last_used,
            "pid": proc.process.pid if proc.process else None,
        })
    return result


async def _reaper_loop():
    """Periodically kill idle stdio processes."""
    while True:
        await asyncio.sleep(60)
        now = time.time()
        to_kill = [
            key for key, proc in _processes.items()
            if now - proc.last_used > _IDLE_TIMEOUT
        ]
        for key in to_kill:
            LOG.info("Reaping idle stdio process: %s", key)
            await _kill_process(key)


def start_reaper():
    """Start the background reaper task."""
    global _reaper_task
    if _reaper_task is None or _reaper_task.done():
        _reaper_task = asyncio.create_task(_reaper_loop())
