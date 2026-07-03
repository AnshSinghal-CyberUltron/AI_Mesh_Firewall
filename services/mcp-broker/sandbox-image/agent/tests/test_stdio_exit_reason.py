"""CP20: unit tests for _classify_exit_reason — the pure exit-reason classifier
used to build the sandbox-agent's stdio failure message. Proves OOM (kernel
SIGKILL -9/137 AND graceful V8 heap abort with the stderr signature) is
categorized as out-of-memory so the control classifier maps it to
MCP_OUT_OF_MEMORY, distinct from a generic crash/start failure.
"""
from __future__ import annotations

import asyncio
import importlib
import sys
from pathlib import Path
from types import SimpleNamespace

SANDBOX_IMAGE = Path(__file__).resolve().parents[2]


def _load():
    p = str(SANDBOX_IMAGE)
    if p not in sys.path:
        sys.path.insert(0, p)
    for mod in ("agent.stdio_manager", "agent"):
        sys.modules.pop(mod, None)
    return importlib.import_module("agent.stdio_manager")


def test_kernel_oom_kill_signal9():
    m = _load()
    r = m._classify_exit_reason(-9, "", oversized_line=False)
    assert "ran out of memory" in r and "-9" in r


def test_container_oom_kill_137():
    m = _load()
    r = m._classify_exit_reason(137, "some log", oversized_line=False)
    assert "ran out of memory" in r


def test_v8_heap_abort_detected_from_stderr_not_miscategorized_as_crash():
    """A V8 heap OOM exits 134 (SIGABRT) but prints the heap-limit message; it must
    read as OOM, not the generic 'crashed on startup' the bare 134 would give."""
    m = _load()
    stderr = "FATAL ERROR: Reached heap limit Allocation failed - JavaScript heap out of memory"
    r = m._classify_exit_reason(134, stderr, oversized_line=False)
    assert "ran out of memory" in r
    assert "crashed on startup" not in r


def test_enospc_disk_full_is_storage_not_start_failure():
    """CP21: a heavy server whose npm install overflows the RAM-backed npm-cache
    tmpfs fails with ENOSPC (exit 1) — must read as a STORAGE limit, not a bad
    command, so the control classifier maps it to MCP_INSUFFICIENT_STORAGE."""
    m = _load()
    stderr = "npm error code ENOSPC\nnpm error errno -28\nnpm error nospc ENOSPC: no space left on device, write"
    r = m._classify_exit_reason(1, stderr, oversized_line=False)
    assert "storage limit" in r
    assert "MCP_SANDBOX_NPM_CACHE_SIZE_MB" in r
    assert "check the command" not in r


def test_plain_sigabrt_without_oom_stderr_is_crash():
    m = _load()
    r = m._classify_exit_reason(134, "Segmentation fault", oversized_line=False)
    assert "crashed on startup" in r and "134" in r


def test_missing_dependency_exit0():
    m = _load()
    r = m._classify_exit_reason(0, "", oversized_line=False)
    assert "missing host dependency" in r


def test_generic_nonzero_exit():
    m = _load()
    r = m._classify_exit_reason(3, "", oversized_line=False)
    assert "exited with code 3" in r


def test_oversized_line_takes_priority():
    m = _load()
    r = m._classify_exit_reason(-9, "heap out of memory", oversized_line=True)
    assert "line buffer" in r


def test_stdout_closed_when_rc_none():
    m = _load()
    r = m._classify_exit_reason(None, "", oversized_line=False)
    assert "stdout stream closed" in r


def _fake_stderr_proc(reader):
    """Minimal StdioProcess-shaped object for _log_stderr (only the fields it reads)."""
    return SimpleNamespace(
        process=SimpleNamespace(stderr=reader),
        key="test-server",
        stderr_tail=[],
        initialized=True,          # skip the OAuth-prompt heuristic branch
        oauth_header_injected=False,
    )


def test_log_stderr_survives_oversized_line_flood():
    """CHG-0152: an untrusted server flooding stderr with a huge UNTERMINATED line must NOT
    hang the drain loop. The old catch-all EXITED on the LimitOverrunError/ValueError, leaving
    stderr undrained (child blocks on write). _log_stderr must skip the oversized line and KEEP
    reading, so a normal diagnostic line AFTER the flood is still captured. Verified cross-version
    (py3.14 here + py3.12 via docker): both consume the buffer on the oversized-line raise."""
    m = _load()

    async def _run():
        reader = asyncio.StreamReader(limit=1024)  # small limit -> readline raises fast
        proc = _fake_stderr_proc(reader)
        task = asyncio.create_task(m._log_stderr(proc))
        reader.feed_data(b"F" * 4096)           # oversized chunk (one huge line)
        await asyncio.sleep(0.05)
        reader.feed_data(b"G" * 4096)           # same line continues (multi-raise drain)
        await asyncio.sleep(0.05)
        reader.feed_data(b"still-draining-after-flood\n")   # a normal line after the flood
        await asyncio.sleep(0.05)
        reader.feed_eof()
        await asyncio.wait_for(task, timeout=2)
        return proc

    proc = asyncio.run(_run())
    assert any("still-draining-after-flood" in s for s in proc.stderr_tail), (
        "drain loop must survive the flood and keep reading subsequent stderr lines"
    )


def test_log_stderr_normal_lines_captured():
    """Sanity: the normal path still tails stderr lines (no regression from the flood guard)."""
    m = _load()

    async def _run():
        reader = asyncio.StreamReader(limit=8 * 1024 * 1024)
        proc = _fake_stderr_proc(reader)
        task = asyncio.create_task(m._log_stderr(proc))
        reader.feed_data(b"line-one\nline-two\n")
        reader.feed_eof()
        await asyncio.wait_for(task, timeout=2)
        return proc

    proc = asyncio.run(_run())
    assert "line-one" in proc.stderr_tail
    assert "line-two" in proc.stderr_tail
