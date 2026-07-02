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
from collections import deque
from dataclasses import dataclass, field

from ai_mesh_shared.mcp_stdio_common import (
    _args_have_oauth_header,
    _build_child_env,
    _looks_like_oauth_prompt,
)

LOG = logging.getLogger("gateway.mcp_stdio_adapter")

# How long an idle process lives before being reaped (seconds)
_IDLE_TIMEOUT = int(os.environ.get("MCP_STDIO_IDLE_TIMEOUT", "600"))
# Max concurrent stdio processes
_MAX_PROCESSES = int(os.environ.get("MCP_STDIO_MAX_PROCESSES", "20"))
# Max size (bytes) of a single line-delimited JSON-RPC message read from a
# child's stdout. asyncio's StreamReader defaults to 64 KiB, but a legitimate
# MCP `tools/list` response can be far larger — e.g. semgrep-mcp embeds the
# full Semgrep rule JSON schema in `semgrep_rule_schema`, pushing the single
# response line past 64 KiB. When the line exceeds the buffer, readline()
# raises LimitOverrunError and the reader dies, surfacing as the misleading
# "stdout stream closed unexpectedly". Raise the limit so large tool catalogs
# stream cleanly (8 MiB default; configurable for pathological servers).
_MAX_LINE_BYTES = int(os.environ.get("MCP_STDIO_MAX_LINE_BYTES", str(8 * 1024 * 1024)))

# --- On-demand fetch hardening -------------------------------------------
# Server packages are fetched lazily at connect time via npx/uvx (NOT baked
# into the image), so the FIRST connect for a server must download + resolve
# the package before it can answer the MCP `initialize` handshake. That needs a
# generous init timeout, while a process that dies immediately (wrong package
# name -> rc=0) is still detected fast because the stdout reader fails the
# pending future the moment the child exits.
_INIT_TIMEOUT = float(os.environ.get("MCP_STDIO_INIT_TIMEOUT", "120"))
# Timeout for normal (already-initialized) JSON-RPC method calls.
_METHOD_TIMEOUT = float(os.environ.get("MCP_STDIO_METHOD_TIMEOUT", "60"))
# Kill a process that spawned but never finished the MCP initialize handshake
# within this many seconds (a hung OAuth / stuck cold-fetch process would
# otherwise squat a slot in the shared pool indefinitely).
_HUNG_INIT_TIMEOUT = float(os.environ.get("MCP_STDIO_HUNG_INIT_TIMEOUT", "180"))
# Max concurrent processes a single org may hold (stops one tenant exhausting
# the shared _MAX_PROCESSES pool).
_MAX_PROCESSES_PER_ORG = int(os.environ.get("MCP_STDIO_MAX_PROCESSES_PER_ORG", "8"))
# Max simultaneous initialize handshakes across all orgs — throttles registry
# fetch stampedes and bounds event-loop pressure from cold starts.
_MAX_CONCURRENT_INITS = int(os.environ.get("MCP_STDIO_MAX_CONCURRENT_INITS", "4"))
# Optional comma-separated allowlist of package names permitted for on-demand
# fetch (e.g. "semgrep-mcp,mcp-remote,ruflo"). Empty = allow any (the backend
# already scopes registrations per-org). Set to lock down which packages the
# shared gateway may pull from npm/PyPI.
_PACKAGE_ALLOWLIST = {
    p.strip().lower()
    for p in os.environ.get("MCP_STDIO_PACKAGE_ALLOWLIST", "").split(",")
    if p.strip()
}
# When true, reject unpinned (`@latest` / bare) package specs so every fetch is
# reproducible. Default OFF because some servers legitimately track @latest.
_REQUIRE_PINNED_PACKAGES = os.environ.get(
    "MCP_STDIO_REQUIRE_PINNED_PACKAGES", "false"
).lower() in ("1", "true", "yes")
# Dev default true (in-gateway spawn); compose/prod sets MCP_STDIO_IN_PROCESS=false.
_STDIO_IN_PROCESS_DEFAULT = "true"


def _stdio_in_process() -> bool:
    """True when stdio MCP servers run in the gateway process (dev fallback)."""
    raw = os.environ.get("MCP_STDIO_IN_PROCESS", _STDIO_IN_PROCESS_DEFAULT)
    return raw.lower() in ("1", "true", "yes")


# Init concurrency limiter (lazily bound to the running loop).
_init_semaphore: "asyncio.Semaphore | None" = None


def _get_init_semaphore() -> asyncio.Semaphore:
    global _init_semaphore
    if _init_semaphore is None:
        _init_semaphore = asyncio.Semaphore(_MAX_CONCURRENT_INITS)
    return _init_semaphore

# Commands permitted to be spawned as stdio MCP servers. The backend stores an
# arbitrary `command` string per server with no allowlist, so we gate it here
# at the actual spawn point (defense-in-depth) to stop an org from turning the
# shared gateway into an RCE primitive (e.g. command="bash").
_ALLOWED_COMMANDS = {
    c.strip().lower()
    for c in os.environ.get(
        "MCP_STDIO_ALLOWED_COMMANDS", "npx,node,python,python3,uvx,uv"
    ).split(",")
    if c.strip()
}

def _command_basename(command: str) -> str:
    return os.path.basename(command).lower()


def _extract_package_spec(command: str, args: list[str]) -> str | None:
    """Best-effort extraction of the package spec an npx/uvx call will fetch.

    npx  ["-y", "mcp-remote", "https://.."]   -> "mcp-remote"
    npx  ["-y", "ruflo@latest", "mcp"]         -> "ruflo@latest"
    uvx  ["semgrep-mcp"]                        -> "semgrep-mcp"
    uvx  ["--from", "semgrep-mcp==1.0", ".."]  -> "semgrep-mcp==1.0"
    Returns None for runtimes we don't fetch packages with (node/python).
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
    # Set when the child emits output that looks like an interactive OAuth /
    # login prompt it cannot complete headless. Surfaced to the operator as
    # needs_reauth (BYOK) instead of a generic timeout.
    needs_reauth: bool = False
    # When the process was spawned — used by the reaper to evict processes that
    # never completed initialize (hung OAuth / stuck cold fetch).
    started_at: float = field(default_factory=time.time)
    # Set when the child emitted a single JSON-RPC line larger than the
    # StreamReader buffer (_MAX_LINE_BYTES). Lets the failure handler report an
    # actionable "oversized response" cause instead of "stdout closed".
    oversized_line: bool = False
    _msg_id_counter: int = 0
    _pending: dict[int | str, asyncio.Future] = field(default_factory=dict)
    _reader_task: asyncio.Task | None = None
    # Bounded tail of the child's stderr. Used ONLY for gateway-side WARNING
    # logs when a process dies — it is NEVER placed in a client/DB-facing
    # error message because stderr can contain BYOK tokens / npm credentials.
    stderr_tail: deque = field(default_factory=lambda: deque(maxlen=50))
    oauth_header_injected: bool = False

    def next_id(self) -> int:
        self._msg_id_counter += 1
        return self._msg_id_counter


# Global registry: key → StdioProcess
_processes: dict[str, StdioProcess] = {}
_registry_lock = asyncio.Lock()
_reaper_task: asyncio.Task | None = None


def _process_key(org_slug: str, server_slug: str) -> str:
    return f"{org_slug}/{server_slug}"


def _flag_needs_reauth(proc: StdioProcess, evidence: str) -> None:
    """Mark a process as requiring interactive auth and fail pending waiters
    fast (instead of letting them block for the full init timeout)."""
    if proc.needs_reauth:
        return
    proc.needs_reauth = True
    LOG.warning("Stdio %s appears to require interactive auth: %s",
                proc.key, evidence[:200])
    msg = (
        f"MCP server '{proc.key}' requires interactive authentication and "
        "cannot complete a headless login. Re-authenticate the server "
        "(BYOK / OAuth) and retry."
    )
    for fut in list(proc._pending.values()):
        if not fut.done():
            fut.set_exception(RuntimeError(msg))
    proc._pending.clear()
    # Free the slot — a process stuck on an auth prompt will never answer.
    asyncio.create_task(_kill_process(proc.key))


async def _start_reader(proc: StdioProcess):
    """Background task that reads stdout lines and resolves pending futures."""
    assert proc.process and proc.process.stdout
    try:
        while True:
            try:
                line = await proc.process.stdout.readline()
            except (asyncio.LimitOverrunError, ValueError) as exc:
                # A single JSON-RPC line exceeded the StreamReader buffer
                # (_MAX_LINE_BYTES). Surface an actionable message instead of
                # letting the reader die with the opaque "stdout closed".
                LOG.error(
                    "Stdio %s emitted a line larger than the %d-byte buffer "
                    "(%s). Raise MCP_STDIO_MAX_LINE_BYTES if this server has a "
                    "legitimately huge tool catalog.",
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
                # Notification or unsolicited message — log it
                LOG.debug("Stdio %s notification: %s", proc.key, str(msg)[:200])
    except asyncio.CancelledError:
        pass
    except Exception as exc:
        LOG.error("Stdio reader for %s crashed: %s", proc.key, exc)
    finally:
        # Determine *why* the process ended so the operator gets an actionable
        # signal instead of an opaque "disconnected". The raw stderr tail is
        # logged server-side ONLY (it may carry BYOK tokens / npm creds); the
        # client/DB-facing message contains just the exit code + a generic hint.
        rc = await _exit_code(proc)
        stderr_tail = "\n".join(str(line) for line in proc.stderr_tail).strip()
        if stderr_tail:
            LOG.warning("Stdio %s stderr tail (rc=%s):\n%s",
                        proc.key, rc, stderr_tail[-2000:])
        # A V8 heap OOM (NODE_OPTIONS --max-old-space-size cap, CP20) aborts with
        # SIGABRT (134) but prints "JavaScript heap out of memory"; a kernel
        # OOM-kill is -9/137. Detect the stderr signature FIRST so it is
        # categorized as OOM (→ MCP_OUT_OF_MEMORY) not a generic crash.
        _oom_in_stderr = any(
            s in stderr_tail.lower()
            for s in ("heap out of memory", "out of memory", "fatal error: reached heap limit")
        )
        if proc.oversized_line:
            reason = (
                "the MCP server sent a response larger than the gateway's "
                f"{_MAX_LINE_BYTES}-byte line buffer (raise "
                "MCP_STDIO_MAX_LINE_BYTES for servers with very large tool "
                "catalogs)"
            )
        elif _oom_in_stderr or rc in (-9, 137):
            reason = (f"the MCP server ran out of memory (exit code {rc}; exceeded the "
                      "per-org sandbox memory limit). Raise MCP_SANDBOX_MEMORY_MB for "
                      "heavy servers or reduce its footprint")
        elif rc is None:
            reason = "stdout stream closed unexpectedly"
        elif rc == 0:
            reason = ("the MCP server exited immediately without responding "
                      "(likely a missing host dependency or wrong package name)")
        elif rc in (-6, 134):
            reason = (f"the MCP server crashed on startup (exit code {rc} / SIGABRT). "
                      "Verify the command and package are compatible")
        else:
            reason = (f"the MCP server process exited with code {rc} "
                      "(check the command and its host dependencies)")
        safe_msg = (f"Stdio MCP server '{proc.key}' failed to start: {reason}. "
                    "See gateway logs for details.")
        # Mark all pending futures as failed with the secret-free message.
        for fut in proc._pending.values():
            if not fut.done():
                fut.set_exception(RuntimeError(safe_msg))
        proc._pending.clear()


async def _exit_code(proc: StdioProcess) -> int | None:
    """Best-effort exit code. Reaps the child with a short timeout so the
    returncode is reliably populated (it is ``None`` until ``wait()`` runs)."""
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


async def _ensure_process(key: str, command: str, args: list[str],
                          env: dict[str, str] | None = None) -> StdioProcess:
    """Get or start a stdio process for the given server."""
    org_slug = key.split("/")[0] if "/" in key else "default"
    requested_env = dict(env or {})

    # Reject path-qualified commands. Basename matching alone would let a
    # command like "/tmp/evil/python" satisfy the allowlist and execute an
    # attacker-planted binary; interpreters must be bare names resolved via
    # the sandboxed PATH.
    if "/" in command or "\\" in command:
        raise RuntimeError(
            f"Command '{command}' must be a bare interpreter name "
            "(e.g. 'npx', 'node', 'python'), not a path."
        )

    # Command allowlist (defense-in-depth — the backend stores arbitrary
    # command strings with no validation of its own).
    if _command_basename(command) not in _ALLOWED_COMMANDS:
        raise RuntimeError(
            f"Command '{command}' is not permitted for stdio MCP servers. "
            f"Allowed commands: {', '.join(sorted(_ALLOWED_COMMANDS))}."
        )

    # On-demand package gating: server packages are pulled from npm/PyPI at
    # connect time, so (optionally) restrict which names the shared gateway may
    # fetch and (optionally) require pinned versions for reproducible fetches.
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
            # Re-spawn if the per-org credentials/env changed so a token
            # rotation takes effect immediately instead of waiting out the
            # idle reaper (BYOK staleness fix).
            if alive and proc.env == requested_env:
                proc.last_used = time.time()
                return proc
            if alive:
                LOG.info("Stdio process %s env changed — restarting for fresh creds", key)
            else:
                LOG.warning("Stdio process %s died (rc=%s), restarting", key, proc.process.returncode if proc.process else "?")
            await _kill_process(key)

        if len(_processes) >= _MAX_PROCESSES:
            # Evict oldest idle process
            oldest_key = min(_processes, key=lambda k: _processes[k].last_used)
            await _kill_process(oldest_key)

        # Per-org concurrency cap — prevent one tenant exhausting the pool.
        org_count = sum(1 for k in _processes if k.split("/", 1)[0] == org_slug)
        if org_count >= _MAX_PROCESSES_PER_ORG:
            raise RuntimeError(
                f"Org '{org_slug}' reached its concurrent stdio MCP server "
                f"limit ({_MAX_PROCESSES_PER_ORG}). Close an existing server "
                "connection and retry."
            )

        proc_env = _build_child_env(requested_env, org_slug, log=LOG)

        LOG.info("Starting stdio MCP process: %s %s (key=%s)", command, args, key)
        try:
            process = await asyncio.create_subprocess_exec(
                command, *args,
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
            env=env or {},
            process=process,
            oauth_header_injected=_args_have_oauth_header(args),
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


async def _send_message(proc: StdioProcess, message: dict, timeout: float | None = None) -> dict:
    """Send a JSON-RPC message to the stdio process and wait for the response."""
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
        # Close the race where the child already died (reader's finally ran
        # and cleared _pending) so this freshly-registered future would never
        # resolve and we'd block for the full timeout. Fail fast instead.
        if proc._reader_task and proc._reader_task.done():
            proc._pending.pop(msg_id, None)
            raise RuntimeError(
                f"Stdio MCP server '{proc.key}' failed to start "
                "(process exited during startup). See gateway logs for details."
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
        # The initialize handshake may have to wait for an on-demand package
        # fetch (npx/uvx cold start), so it gets the longer _INIT_TIMEOUT. The
        # init semaphore bounds how many cold fetches run at once.
        async with _get_init_semaphore():
            try:
                resp = await _send_message(proc, init_msg, timeout=_INIT_TIMEOUT)
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

        # Send notifications/initialized (no response expected)
        await _send_message(proc, {
            "jsonrpc": "2.0",
            "method": "notifications/initialized",
        })
        proc.initialized = True


def _server_config_for_broker(
    org_slug: str,
    server_slug: str,
    command: str,
    args: list[str],
    env: dict[str, str] | None,
    server_config: dict | None,
) -> tuple[str, dict]:
    """Build broker server_config; org_slug may be overridden via server_config."""
    cfg = dict(server_config or {})
    cfg.setdefault("org_slug", org_slug)
    cfg.setdefault("server_slug", server_slug)
    cfg.setdefault("command", command)
    cfg.setdefault("args", list(args))
    if env is not None:
        cfg.setdefault("env_vars", env)
    effective_org = str(cfg.get("org_slug") or org_slug)
    return effective_org, cfg


# B3 item#19: orgs whose per-org sandbox we've already eagerly warmed this
# process, so the readiness-blocking warm runs once (on the first stdio op /
# first-sync) rather than on every RPC.
_WARMED_ORGS: set[str] = set()


async def _send_jsonrpc_broker(
    org_slug: str,
    server_slug: str,
    command: str,
    args: list[str],
    env: dict[str, str] | None,
    method: str,
    params: dict | None,
    msg_id: int | str | None,
    server_config: dict | None,
) -> dict:
    from ai_mesh_gateway.mcp_sandbox_client import broker_send_jsonrpc, ensure_sandbox

    effective_org, cfg = _server_config_for_broker(
        org_slug, server_slug, command, args, env, server_config,
    )
    # Eagerly provision + warm the per-org sandbox on the first broker
    # interaction (first-sync / first tool call) so it hits a READY agent
    # instead of racing the cold start (B3: "MCP sandbox is temporarily
    # unavailable"). Best-effort: on failure fall through to broker_send_jsonrpc,
    # whose stdio_rpc still ensures + retries lazily.
    if effective_org not in _WARMED_ORGS:
        try:
            await ensure_sandbox(effective_org)
        except Exception as exc:  # noqa: BLE001 — non-fatal warm, lazy path still applies
            LOG.warning("eager sandbox warm failed for org=%s: %s", effective_org, exc)
        else:
            _WARMED_ORGS.add(effective_org)
    return await broker_send_jsonrpc(
        effective_org,
        cfg,
        method,
        params,
        msg_id=msg_id,
    )


async def _send_jsonrpc_in_process(
    org_slug: str,
    server_slug: str,
    command: str,
    args: list[str],
    env: dict[str, str] | None,
    method: str,
    params: dict | None,
    msg_id: int | str | None,
) -> dict:
    """In-gateway stdio spawn + line-protocol JSON-RPC (dev / MCP_STDIO_IN_PROCESS=true)."""
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

    resp = await _send_message(proc, message, timeout=_METHOD_TIMEOUT)
    # Re-map the id to the original request id
    if msg_id is not None:
        resp["id"] = msg_id
    return resp


async def send_jsonrpc(org_slug: str, server_slug: str,
                       command: str, args: list[str],
                       env: dict[str, str] | None,
                       method: str, params: dict | None,
                       msg_id: int | str | None,
                       *,
                       server_config: dict | None = None) -> dict:
    """Send a JSON-RPC message to a stdio MCP server and return the response.

    When ``MCP_STDIO_IN_PROCESS=false``, delegates to the mcp-broker sandbox
    (per-org Docker container). Otherwise spawns the child in-process.
    """
    if not _stdio_in_process():
        return await _send_jsonrpc_broker(
            org_slug, server_slug, command, args, env,
            method, params, msg_id, server_config,
        )
    return await _send_jsonrpc_in_process(
        org_slug, server_slug, command, args, env,
        method, params, msg_id,
    )


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
    """Periodically kill idle stdio processes and processes that spawned but
    never finished the MCP initialize handshake (hung OAuth / stuck fetch)."""
    while True:
        await asyncio.sleep(60)
        now = time.time()
        to_kill = []
        for key, proc in _processes.items():
            idle = now - proc.last_used > _IDLE_TIMEOUT
            hung = (not proc.initialized
                    and now - proc.started_at > _HUNG_INIT_TIMEOUT)
            if idle or hung:
                if hung and not idle:
                    LOG.warning(
                        "Reaping stdio process that never initialized: %s", key)
                else:
                    LOG.info("Reaping idle stdio process: %s", key)
                to_kill.append(key)
        for key in to_kill:
            await _kill_process(key)


def start_reaper():
    """Start the background reaper task."""
    global _reaper_task
    if _reaper_task is None or _reaper_task.done():
        _reaper_task = asyncio.create_task(_reaper_loop())
