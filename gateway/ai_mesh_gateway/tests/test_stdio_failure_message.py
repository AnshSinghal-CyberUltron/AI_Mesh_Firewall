"""Item 03 — the stdio-start failure message set on pending futures is CLEAN:
no exit codes, no internal env-var names, no stderr tail, no 'sandbox-agent
logs'/'gateway logs' pointer, no server key. It routes through the shared
classifier so the client sees only a branded, actionable summary."""
import types

import pytest

from ai_mesh_gateway import mcp_stdio_adapter as A
from ai_mesh_gateway import mcp_error_classifier as C


def _proc(key="zeroshield/ruflo-secret-server", oversized=False):
    return types.SimpleNamespace(key=key, oversized_line=oversized)


# a fake stderr tail carrying a secret + internal detail that must NEVER surface
_SECRET_STDERR = ("npm ERR! 401 Unauthorized token=sk-live-DEADBEEFsecret "
                  "at /home/agent/node_modules  JavaScript heap out of memory")

_FORBIDDEN = ["exit code", "sigabrt", "sigsegv", "sandbox-agent", "gateway logs",
              "mcp_sandbox", "mcp_stdio_max_line_bytes", "stderr", "node_modules",
              "sk-live-", "deadbeef", "ruflo-secret-server", "npm err"]


def _assert_clean(msg: str):
    low = msg.lower()
    for tok in _FORBIDDEN:
        assert tok not in low, f"leak: {tok!r} in client message {msg!r}"
    # no raw exit-code numbers
    for n in ("-9", "137", "-6", "134", "-11", "139", "127"):
        assert n not in msg, f"leak: exit number {n} in {msg!r}"


@pytest.mark.asyncio
async def test_oom_via_stderr_heap_with_sigabrt_rc():
    # V8 heap OOM aborts with 134 but is really OOM → memory-worded, not a crash
    msg = await A._stdio_failure_message(_proc(), 134, _SECRET_STDERR)
    assert "memory" in msg.lower()
    _assert_clean(msg)


@pytest.mark.asyncio
async def test_oom_via_signal_kill():
    msg = await A._stdio_failure_message(_proc(), -9, "killed")
    assert "memory" in msg.lower()
    _assert_clean(msg)


@pytest.mark.asyncio
@pytest.mark.parametrize("rc", [-6, 134, -11, 139])
async def test_crash_exit_codes(rc):
    msg = await A._stdio_failure_message(_proc(), rc, "Segmentation fault")
    assert "crash" in msg.lower() or "start" in msg.lower()
    _assert_clean(msg)


@pytest.mark.asyncio
async def test_disk_full():
    msg = await A._stdio_failure_message(_proc(), 1, "ENOSPC: no space left on device")
    assert "storage" in msg.lower()
    _assert_clean(msg)


@pytest.mark.asyncio
async def test_oversized_line():
    msg = await A._stdio_failure_message(_proc(oversized=True), None, "")
    assert "too large" in msg.lower()
    _assert_clean(msg)


@pytest.mark.asyncio
async def test_exit_zero_missing_dependency():
    msg = await A._stdio_failure_message(_proc(), 0, "")
    assert "start" in msg.lower()
    _assert_clean(msg)


@pytest.mark.asyncio
async def test_stdout_closed_rc_none():
    msg = await A._stdio_failure_message(_proc(), None, "")
    assert isinstance(msg, str) and msg
    _assert_clean(msg)


@pytest.mark.asyncio
async def test_generic_nonzero_exit():
    msg = await A._stdio_failure_message(_proc(), 127, "command not found somewhere")
    _assert_clean(msg)
