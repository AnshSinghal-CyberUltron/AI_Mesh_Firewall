"""Stdio MCP process manager for the in-container sandbox agent.

Adapted from gateway/ai_mesh_gateway/mcp_stdio_adapter.py for single-org
containers. Spawns npx/uvx children, speaks JSON-RPC over line-delimited
stdin/stdout, and reuses processes across RPC calls.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from collections import deque
from dataclasses import dataclass, field

from ai_mesh_shared.mcp_stdio_common import (
    _args_have_oauth_header,
    _build_child_env,
    _looks_like_oauth_prompt,
)

LOG = logging.getLogger("sandbox_agent.stdio")

ORG_SLUG = os.environ.get("ORG_SLUG", "default")

_IDLE_TIMEOUT = int(os.environ.get("MCP_STDIO_IDLE_TIMEOUT", "600"))
_MAX_PROCESSES = int(os.environ.get("MCP_STDIO_MAX_PROCESSES", "20"))
_MAX_LINE_BYTES = int(os.environ.get("MCP_STDIO_MAX_LINE_BYTES", str(8 * 1024 * 1024)))
_INIT_TIMEOUT = float(os.environ.get("MCP_STDIO_INIT_TIMEOUT", "120"))
_METHOD_TIMEOUT = float(os.environ.get("MCP_STDIO_METHOD_TIMEOUT", "60"))
_HUNG_INIT_TIMEOUT = float(os.environ.get("MCP_STDIO_HUNG_INIT_TIMEOUT", "180"))
_MAX_PROCESSES_PER_ORG = int(os.environ.get("MCP_STDIO_MAX_PROCESSES_PER_ORG", "16"))
_MAX_CONCURRENT_INITS = int(os.environ.get("MCP_STDIO_MAX_CONCURRENT_INITS", "4"))

_ALLOWED_COMMANDS = {
    c.strip().lower()
    for c in os.environ.get(
        "MCP_STDIO_ALLOWED_COMMANDS", "npx,node,python,python3,uvx,uv"
    ).split(",")
    if c.strip()
}

# Optional comma-separated allowlist of npm/PyPI package names permitted for
# on-demand fetch (e.g. "semgrep-mcp,mcp-remote"). Empty = allow any package
# (the sandbox already constrains which servers are registered per-org).
# Mirrors gateway mcp_stdio_adapter._PACKAGE_ALLOWLIST for parity.
_PACKAGE_ALLOWLIST = {
    p.strip().lower()
    for p in os.environ.get("MCP_STDIO_PACKAGE_ALLOWLIST", "").split(",")
    if p.strip()
}

# When true, reject unpinned (@latest / bare) package specs so every fetch is
# reproducible and the supply-chain attack window is eliminated.
# Mirrors gateway mcp_stdio_adapter._REQUIRE_PINNED_PACKAGES for parity.
_REQUIRE_PINNED_PACKAGES = os.environ.get(
    "MCP_STDIO_REQUIRE_PINNED_PACKAGES", "false"
).lower() in ("1", "true", "yes")

_init_semaphore: asyncio.Semaphore | None = None
_processes: dict[str, StdioProcess] = {}
_registry_lock = asyncio.Lock()
_reaper_task: asyncio.Task | None = None


# CHG-0053: stdio server args are logged for debuggability, but a configured
# credential passed as ``--token XYZ`` / ``--api-key=XYZ`` must NOT land in operator
# logs in plaintext. Mask the VALUE of any secret-looking flag before logging. (The
# normal secret location is ``env``, which the agent never logs — this covers the
# arg edge case; tool-call results/params are never logged either.)
_SECRET_ARG_HINTS = (
    "token", "key", "secret", "password", "passwd", "auth", "credential", "apikey",
)


def _safe_args_for_log(args: list[str]) -> list[str]:
    out: list[str] = []
    mask_next = False
    for a in args:
        s = str(a)
        if mask_next:
            out.append("***")
            mask_next = False
            continue
        low = s.lower()
        if s.startswith("-") and any(h in low for h in _SECRET_ARG_HINTS):
            if "=" in s:
                out.append(s.split("=", 1)[0] + "=***")
            else:
                out.append(s)       # keep the flag name itself
                mask_next = True     # ...but mask the following value
        else:
            out.append(s)
    return out


def _command_basename(command: str) -> str:
    return os.path.basename(command).lower()


def _extract_package_spec(command: str, args: list[str]) -> str | None:
    """Best-effort extraction of the package spec an npx/uvx call will fetch.

    npx  ["-y", "mcp-remote", "https://.."]   -> "mcp-remote"
    npx  ["-y", "ruflo@latest", "mcp"]         -> "ruflo@latest"
    uvx  ["semgrep-mcp"]                        -> "semgrep-mcp"
    uvx  ["--from", "semgrep-mcp==1.0", ".."]  -> "semgrep-mcp==1.0"
    Returns None for runtimes that don't fetch packages (node/python).
    """
    if _command_basename(command) not in ("npx", "uvx", "uv"):
        return None
    skip = {"-y", "--yes", "-q", "--quiet", "tool", "run"}
    it = iter(args)
    for tok in it:
        if tok in ("--from", "--with", "--package", "-p"):
            return next(it, None)
        if tok.startswith("-") or tok in skip:
            continue
        return tok
    return None


def _package_name(spec: str) -> str:
    """Strip version/url from a package spec to get the bare name (lowercase)."""
    if spec.startswith("@"):  # scoped npm pkg @scope/name@version
        at = spec.rfind("@")
        return (spec[:at] if at > 0 else spec).lower()
    for sep in ("==", ">=", "<=", "~=", "@", ">", "<"):
        if sep in spec:
            return spec.split(sep, 1)[0].lower()
    return spec.lower()


def _is_pinned(spec: str) -> bool:
    """True if the spec carries an explicit (non-@latest) version."""
    if spec.startswith("@"):
        at = spec.rfind("@")
        ver = spec[at + 1:] if at > 0 else ""
        return bool(ver) and ver != "latest"
    for sep in ("==", "@"):
        if sep in spec:
            ver = spec.split(sep, 1)[1]
            return bool(ver) and ver != "latest"
    return False


def _get_init_semaphore() -> asyncio.Semaphore:
    global _init_semaphore
    if _init_semaphore is None:
        _init_semaphore = asyncio.Semaphore(_MAX_CONCURRENT_INITS)
    return _init_semaphore


@dataclass
class StdioProcess:
    key: str
    command: str
    args: list[str]
    env: dict[str, str]
    process: asyncio.subprocess.Process | None = None
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    last_used: float = field(default_factory=time.time)
    initialized: bool = False
    needs_reauth: bool = False
    started_at: float = field(default_factory=time.time)
    oversized_line: bool = False
    _msg_id_counter: int = 0
    _pending: dict[int | str, asyncio.Future] = field(default_factory=dict)
    _reader_task: asyncio.Task | None = None
    stderr_tail: deque = field(default_factory=lambda: deque(maxlen=50))
    oauth_header_injected: bool = False

    def next_id(self) -> int:
        self._msg_id_counter += 1
        return self._msg_id_counter


def _process_key(server_slug: str) -> str:
    return f"{ORG_SLUG}/{server_slug}"


def _flag_needs_reauth(proc: StdioProcess, evidence: str) -> None:
    if proc.needs_reauth:
        return
    proc.needs_reauth = True
    LOG.warning("Stdio %s appears to require interactive auth: %s", proc.key, evidence[:200])
    msg = (
        f"MCP server '{proc.key}' requires interactive authentication and "
        "cannot complete a headless login. Re-authenticate the server "
        "(BYOK / OAuth) and retry."
    )
    for fut in list(proc._pending.values()):
        if not fut.done():
            fut.set_exception(RuntimeError(msg))
    proc._pending.clear()
    asyncio.create_task(_kill_process(proc.key))


def _classify_exit_reason(rc, stderr_tail: str, *, oversized_line: bool) -> str:
    """Human-readable reason a spawned stdio MCP server terminated.

    Pure + unit-testable. OOM (CP20) is detected FIRST from the stderr signature
    (a V8 heap-limit abort prints "JavaScript heap out of memory" but exits 134,
    which would otherwise read as a generic crash) OR from a kernel OOM-kill exit
    code (-9 / 137). The word "out of memory" / "exit code -9" is kept in the text
    so the control-plane classifier maps it to MCP_OUT_OF_MEMORY. (CP20)
    """
    oom_in_stderr = any(
        s in (stderr_tail or "").lower()
        for s in ("heap out of memory", "out of memory", "fatal error: reached heap limit")
    )
    if oversized_line:
        return (
            f"the MCP server sent a response larger than the {_MAX_LINE_BYTES}-byte "
            "line buffer (raise MCP_STDIO_MAX_LINE_BYTES for very large tool catalogs)"
        )
    if oom_in_stderr or rc in (-9, 137):
        return (
            f"the MCP server ran out of memory (exit code {rc}; exceeded the per-org "
            "sandbox memory limit). Raise MCP_SANDBOX_MEMORY_MB for heavy servers or "
            "reduce its footprint"
        )
    if rc is None:
        return "stdout stream closed unexpectedly"
    if rc == 0:
        return (
            "the MCP server exited immediately without responding "
            "(likely a missing host dependency or wrong package name)"
        )
    if rc in (-6, 134):
        return (
            f"the MCP server crashed on startup (exit code {rc} / SIGABRT). "
            "Verify the command and package are compatible"
        )
    return (
        f"the MCP server process exited with code {rc} "
        "(check the command and its host dependencies)"
    )


async def _exit_code(proc: StdioProcess) -> int | None:
    p = proc.process
    if not p:
        return None
    if p.returncode is not None:
        return p.returncode
    try:
        await asyncio.wait_for(p.wait(), timeout=2)
    except (asyncio.TimeoutError, ProcessLookupError):
        pass
    return p.returncode


async def _start_reader(proc: StdioProcess):
    assert proc.process and proc.process.stdout
    try:
        while True:
            try:
                line = await proc.process.stdout.readline()
            except (asyncio.LimitOverrunError, ValueError) as exc:
                LOG.error(
                    "Stdio %s line exceeded %d-byte buffer (%s)",
                    proc.key, _MAX_LINE_BYTES, exc,
                )
                proc.oversized_line = True
                break
            if not line:
                LOG.info("Stdio process %s stdout closed", proc.key)
                break
            line = line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                text = line.decode(errors="replace") if isinstance(line, bytes) else str(line)
                LOG.debug("Stdio %s non-JSON line: %s", proc.key, text[:200])
                if not proc.initialized and _looks_like_oauth_prompt(
                    text, oauth_header_injected=proc.oauth_header_injected
                ):
                    _flag_needs_reauth(proc, text)
                    break
                continue

            msg_id = msg.get("id")
            if msg_id is not None and msg_id in proc._pending:
                fut = proc._pending.pop(msg_id)
                if not fut.done():
                    fut.set_result(msg)
            else:
                LOG.debug("Stdio %s notification: %s", proc.key, str(msg)[:200])
    except asyncio.CancelledError:
        pass
    except Exception as exc:
        LOG.error("Stdio reader for %s crashed: %s", proc.key, exc)
    finally:
        rc = await _exit_code(proc)
        stderr_tail = "\n".join(str(line) for line in proc.stderr_tail).strip()
        if stderr_tail:
            LOG.warning("Stdio %s stderr tail (rc=%s):\n%s", proc.key, rc, stderr_tail[-2000:])
        reason = _classify_exit_reason(rc, stderr_tail, oversized_line=proc.oversized_line)
        safe_msg = (
            f"Stdio MCP server '{proc.key}' failed to start: {reason}. "
            "See sandbox-agent logs for details."
        )
        for fut in proc._pending.values():
            if not fut.done():
                fut.set_exception(RuntimeError(safe_msg))
        proc._pending.clear()


async def _ensure_process(
    key: str,
    command: str,
    args: list[str],
    env: dict[str, str] | None = None,
) -> StdioProcess:
    requested_env = dict(env or {})

    if "/" in command or "\\" in command:
        raise RuntimeError(
            f"Command '{command}' must be a bare interpreter name "
            "(e.g. 'npx', 'node', 'python'), not a path."
        )

    if _command_basename(command) not in _ALLOWED_COMMANDS:
        raise RuntimeError(
            f"Command '{command}' is not permitted for stdio MCP servers. "
            f"Allowed commands: {', '.join(sorted(_ALLOWED_COMMANDS))}."
        )

    # Package allowlist + pinned-version enforcement (N3 + N2).
    # Mirrors the same checks in gateway mcp_stdio_adapter so both paths have
    # identical supply-chain hardening regardless of MCP_STDIO_IN_PROCESS.
    spec = _extract_package_spec(command, args)
    if spec is not None:
        name = _package_name(spec)
        if _PACKAGE_ALLOWLIST and name not in _PACKAGE_ALLOWLIST:
            raise RuntimeError(
                f"Package '{name}' is not in the on-demand allowlist. "
                f"Allowed: {', '.join(sorted(_PACKAGE_ALLOWLIST))}."
            )
        if _REQUIRE_PINNED_PACKAGES and not _is_pinned(spec):
            raise RuntimeError(
                f"Package '{spec}' must be version-pinned (e.g. 'pkg@1.2.3'); "
                "unpinned/@latest specs are disabled by policy."
            )

    async with _registry_lock:
        if key in _processes:
            proc = _processes[key]
            alive = bool(proc.process and proc.process.returncode is None)
            if alive and proc.env == requested_env:
                proc.last_used = time.time()
                return proc
            if alive:
                LOG.info("Stdio process %s env changed — restarting", key)
            else:
                LOG.warning(
                    "Stdio process %s died (rc=%s), restarting",
                    key,
                    proc.process.returncode if proc.process else "?",
                )
            await _kill_process(key)

        if len(_processes) >= _MAX_PROCESSES:
            oldest_key = min(_processes, key=lambda k: _processes[k].last_used)
            await _kill_process(oldest_key)

        org_keys = [k for k in _processes if k.startswith(f"{ORG_SLUG}/")]
        while len(org_keys) >= _MAX_PROCESSES_PER_ORG:
            oldest = min(org_keys, key=lambda k: _processes[k].last_used)
            LOG.info(
                "Evicting LRU stdio process %s (org %s at limit %s)",
                oldest,
                ORG_SLUG,
                _MAX_PROCESSES_PER_ORG,
            )
            await _kill_process(oldest)
            org_keys = [k for k in _processes if k.startswith(f"{ORG_SLUG}/")]

        proc_env = _build_child_env(
            requested_env,
            ORG_SLUG,
            remote_config_dir=os.environ.get("MCP_REMOTE_CONFIG_DIR"),
            log=LOG,
        )

        LOG.info(
            "Starting stdio MCP process: %s %s (key=%s)",
            command, _safe_args_for_log(args), key,
        )
        try:
            process = await asyncio.create_subprocess_exec(
                command,
                *args,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=proc_env,
                limit=_MAX_LINE_BYTES,
            )
        except FileNotFoundError:
            raise RuntimeError(f"Command not found: {command}")
        except PermissionError:
            raise RuntimeError(f"Permission denied: {command}")

        proc = StdioProcess(
            key=key,
            command=command,
            args=args,
            env=requested_env,
            process=process,
            oauth_header_injected=_args_have_oauth_header(args),
        )
        proc._reader_task = asyncio.create_task(_start_reader(proc))
        _processes[key] = proc
        asyncio.create_task(_log_stderr(proc))
        return proc


async def _log_stderr(proc: StdioProcess):
    assert proc.process and proc.process.stderr
    try:
        while True:
            line = await proc.process.stderr.readline()
            if not line:
                break
            decoded = line.decode(errors="replace").strip()
            if decoded:
                proc.stderr_tail.append(decoded)
                if not proc.initialized and _looks_like_oauth_prompt(
                    decoded, oauth_header_injected=proc.oauth_header_injected
                ):
                    _flag_needs_reauth(proc, decoded)
            LOG.debug("Stdio %s stderr: %s", proc.key, decoded)
    except asyncio.CancelledError:
        pass
    except Exception:
        pass


async def _kill_process(key: str):
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


async def _send_message(
    proc: StdioProcess,
    message: dict,
    timeout: float | None = None,
) -> dict:
    if timeout is None:
        timeout = _METHOD_TIMEOUT
    if proc.needs_reauth:
        raise RuntimeError(
            f"MCP server '{proc.key}' requires re-authentication (BYOK). "
            "Provide valid credentials and retry."
        )
    if not proc.process or proc.process.returncode is not None:
        raise RuntimeError(f"Stdio process {proc.key} is not running")

    msg_id = message.get("id")
    is_notification = msg_id is None
    line = json.dumps(message) + "\n"

    if not is_notification:
        loop = asyncio.get_event_loop()
        fut: asyncio.Future = loop.create_future()
        proc._pending[msg_id] = fut
        if proc._reader_task and proc._reader_task.done():
            proc._pending.pop(msg_id, None)
            raise RuntimeError(
                f"Stdio MCP server '{proc.key}' failed to start "
                "(process exited during startup). See sandbox-agent logs for details."
            )

    proc.process.stdin.write(line.encode())
    await proc.process.stdin.drain()
    proc.last_used = time.time()

    if is_notification:
        return {}

    try:
        return await asyncio.wait_for(fut, timeout=timeout)
    except asyncio.TimeoutError:
        proc._pending.pop(msg_id, None)
        raise RuntimeError(
            f"Stdio process {proc.key} timed out waiting for response to message {msg_id}"
        )


async def _ensure_initialized(proc: StdioProcess, init_timeout: float):
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
                    "name": "MCP Sandbox Agent",
                    "version": "0.1.0",
                },
            },
        }
        async with _get_init_semaphore():
            try:
                resp = await _send_message(proc, init_msg, timeout=init_timeout)
            except RuntimeError as exc:
                if proc.needs_reauth:
                    raise RuntimeError(
                        f"MCP server '{proc.key}' requires interactive "
                        "authentication (BYOK / OAuth). Re-authenticate and retry."
                    ) from exc
                raise
        if "error" in resp:
            raise RuntimeError(f"Stdio initialize failed: {resp['error']}")

        LOG.info("Stdio %s initialized: %s", proc.key, json.dumps(resp.get("result", {}))[:200])
        await _send_message(proc, {
            "jsonrpc": "2.0",
            "method": "notifications/initialized",
        })
        proc.initialized = True


async def send_jsonrpc(
    server_slug: str,
    command: str,
    args: list[str],
    env: dict[str, str] | None,
    method: str,
    params: dict | list | None,
    msg_id: int | str | None,
    *,
    init_timeout: float | None = None,
    method_timeout: float | None = None,
) -> dict:
    """Send one JSON-RPC exchange to a stdio MCP child process."""
    key = _process_key(server_slug)
    proc = await _ensure_process(key, command, args, env)
    init_to = init_timeout if init_timeout is not None else _INIT_TIMEOUT
    method_to = method_timeout if method_timeout is not None else _METHOD_TIMEOUT
    await _ensure_initialized(proc, init_to)

    if method == "initialize":
        return {
            "jsonrpc": "2.0",
            "id": msg_id,
            "result": {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {
                    "name": f"MCP Sandbox Agent — {server_slug} (stdio)",
                    "version": "0.1.0",
                },
            },
        }

    if method == "notifications/initialized":
        return {}

    use_id = msg_id if msg_id is not None else proc.next_id()
    message: dict = {
        "jsonrpc": "2.0",
        "id": use_id,
        "method": method,
    }
    if params is not None:
        message["params"] = params

    # Stdio MCP is line-oriented on a single stdin/stdout pair — serialize per process.
    async with proc.lock:
        resp = await _send_message(proc, message, timeout=method_to)
    if msg_id is not None:
        resp["id"] = msg_id
    return resp


async def shutdown_all():
    keys = list(_processes.keys())
    for key in keys:
        await _kill_process(key)
    LOG.info("All stdio MCP processes terminated")


async def list_processes() -> list[dict]:
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
    while True:
        await asyncio.sleep(60)
        now = time.time()
        to_kill = []
        for key, proc in _processes.items():
            idle = now - proc.last_used > _IDLE_TIMEOUT
            hung = not proc.initialized and now - proc.started_at > _HUNG_INIT_TIMEOUT
            if idle or hung:
                if hung and not idle:
                    LOG.warning("Reaping stdio process that never initialized: %s", key)
                else:
                    LOG.info("Reaping idle stdio process: %s", key)
                to_kill.append(key)
        for key in to_kill:
            await _kill_process(key)


def start_reaper():
    global _reaper_task
    if _reaper_task is None or _reaper_task.done():
        _reaper_task = asyncio.create_task(_reaper_loop())
