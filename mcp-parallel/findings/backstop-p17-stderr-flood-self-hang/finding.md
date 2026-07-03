# CHG-0152 — sandbox agent `_log_stderr` self-hung on an oversized stderr line (untrusted-server stderr flood)

**Change-id:** CHG-0152
**Date:** 2026-07-03
**Severity:** LOW-MEDIUM (resource-bomb containment / availability — a per-org self-hang; contained to that
org's sandbox, not cross-tenant). Closes the last deferred "code" residual (flagged since CHG-0144).
**Area:** HARDEN THE ARCHITECTURE — resource-bomb containment (item 17); the in-sandbox agent surviving an
untrusted server's stderr flood.
**Files:** `services/mcp-broker/sandbox-image/agent/stdio_manager.py` (`_log_stderr`);
`services/mcp-broker/sandbox-image/agent/tests/test_stdio_exit_reason.py` (+2 tests).
**Whose work it touches:** the in-sandbox stdio process manager (agent).

## Root cause

`_log_stderr` drains a spawned stdio MCP server's stderr line-by-line via
`await proc.process.stderr.readline()`, inside a single `while True` wrapped by a catch-all
`except Exception: pass` that sat OUTSIDE the loop. The subprocess StreamReader is created with
`limit=_MAX_LINE_BYTES` (8 MiB). An untrusted server flooding stderr with a huge UNTERMINATED line makes
`readline()` **raise** once the line exceeds the limit — and because the raise propagated to the outer
`except`, the loop **EXITED**: stderr was never drained again, the OS pipe buffer (~64 KiB) filled, and the
child then **BLOCKED on `write(2)` to stderr** — a self-hang of that org's MCP server (it can no longer make
progress on stdout either).

## Why this was deferred (and how it was finally verified)

The raise type + buffer disposition differ by Python version, and the sandbox image runs **Python 3.12**
while the test venv is **3.14** — so a fix verified only on 3.14 could be wrong on the prod runtime. This
time both were checked directly (docker `python:3.12-slim` is available):

| runtime | oversized `readline()` raises | buffer after raise |
|---------|-------------------------------|--------------------|
| 3.14.4  | `asyncio.LimitOverrunError`   | **consumed (0)**   |
| 3.12.13 | `ValueError`                  | **consumed (0)**   |

So BOTH runtimes CONSUME the oversized bytes on the raise. (My earlier CHG-0144 note that 3.12 "leaves the
data" was wrong — corrected in auto-memory. The earlier dead-end was caused by adding a *blocking* `read()`
to drain, which on both runtimes then blocked on the empty buffer and swallowed the next real line.)

## The fix (CHG-0152)

Catch the oversized-line raise INSIDE the loop and `continue` — no blocking read:

```python
try:
    line = await stderr.readline()
except (asyncio.LimitOverrunError, ValueError):   # 3.14 / 3.12 respectively; both consume the buffer
    LOG.warning("Stdio %s: skipped an oversized stderr line", proc.key)
    continue
```

A huge line drains in limit-sized chunks across successive raises (each `readline()` consumes up to the
limit, raises, we skip and retry); a normal line AFTER the flood is read cleanly; the reader task survives,
so the child never backs up on stderr. Bounded by the sandbox cpu/mem limits.

## Verification (cross-version)

```
# 3.14 (agent test venv)
cd services/mcp-broker/sandbox-image/agent
../../.venv/bin/python -m pytest tests/test_stdio_exit_reason.py -q          # 11 passed (2 new)

# 3.12 (the PROD sandbox runtime) — run the REAL _log_stderr in a container
docker run --rm -e PYTHONDONTWRITEBYTECODE=1 \
  -v <repo>:/repo:ro -v /tmp/.../real_log_stderr_probe.py:/probe.py:ro \
  python:3.12-slim python /probe.py
# -> py3.12.13: REAL _log_stderr survived flood, next line captured = True
```

New tests: `test_log_stderr_survives_oversized_line_flood` (feed two 4 KiB no-newline chunks then a normal
line under a 1 KiB StreamReader limit → the normal line is captured, the loop didn't hang);
`test_log_stderr_normal_lines_captured` (no regression on the normal path). Broader fast agent sweep
(`test_stdio_exit_reason` + `test_stdio_manager_packages` + `test_ssrf_ip_classifier`) → 88 passed.

## Scope / honesty note

Availability/containment fix for a per-org self-hang; not cross-tenant (the sandbox is per-org). The
stdout reader path is unchanged (it deliberately treats an oversized stdout line as a broken server —
`oversized_line=True` + break — which is correct there; stderr is diagnostic and must not hang the server).
Does not change the host-blocked live-stress status. Partial coverage is not completion.
