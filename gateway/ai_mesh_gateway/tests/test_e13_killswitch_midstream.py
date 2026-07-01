"""E13: mid-stream kill-switch / model-state enforcement.

Once a stream has STARTED there was previously NO periodic kill-switch / model-
state re-check, so an operator who tripped the kill-switch (or isolated the
model) DURING an active stream kept getting the remainder from the now-disabled
model. The fix threads a THROTTLED async ``state_check`` callback into
``LLMRouter.acompletion_stream`` (run inside the chunk loop via
``_stream_with_state_check``); a definitive kill/isolate verdict STOPS emitting
the remainder, aclose()'s the upstream generator, and ends the stream with a
terminal error SSE.

These tests prove the three contract requirements:

1. KILL: a stream whose ACTIVE model is kill-switched/isolated AFTER the first
   chunk stops emitting (post-kill chunks are NOT yielded) and ends with a
   terminal error SSE; the upstream generator is closed (no further pulls).
2. NO-OP: a stream with NO kill-switch runs to completion unchanged (no FP).
3. FAIL-OPEN: a Redis/exception error in the re-check does NOT terminate a
   legitimate stream — it runs to completion.

litellm is not installed in the test venv, so (as in test_bedrock_routing.py) we
stub ``litellm`` + ``litellm.exceptions`` BEFORE importing the router module.
"""
from __future__ import annotations

import asyncio
import json
import sys
import types

import pytest


# --- stub litellm before importing the router (matches test_bedrock_routing) ---
if "litellm" not in sys.modules:
    fake_litellm = types.ModuleType("litellm")

    class _FakeRouter:
        def __init__(self, *args, **kwargs):
            self.model_list = kwargs.get("model_list", [])

    async def _unused_async_completion(*args, **kwargs):
        raise RuntimeError("litellm completion should be stubbed in tests")

    fake_litellm.Router = _FakeRouter
    fake_litellm.acompletion = _unused_async_completion
    fake_litellm.aembedding = _unused_async_completion
    fake_litellm.drop_params = True
    fake_litellm.request_timeout = 120
    fake_litellm.num_retries = 2
    fake_litellm.ssl_verify = True

    fake_exceptions = types.ModuleType("litellm.exceptions")
    for exc_name in (
        "APIConnectionError",
        "APIError",
        "AuthenticationError",
        "BadRequestError",
        "BudgetExceededError",
        "ContentPolicyViolationError",
        "ContextWindowExceededError",
        "NotFoundError",
        "RateLimitError",
        "ServiceUnavailableError",
        "Timeout",
    ):
        setattr(fake_exceptions, exc_name, type(exc_name, (Exception,), {}))

    sys.modules["litellm"] = fake_litellm
    sys.modules["litellm.exceptions"] = fake_exceptions

from ai_mesh_gateway import llm_router as _lr
from ai_mesh_gateway.llm_router import LLMRouter


# --------------------------------------------------------------------------- #
# Test doubles
# --------------------------------------------------------------------------- #
class _FakeChunk:
    """Minimal stand-in for a litellm streaming chunk: exposes model_dump()."""

    def __init__(self, text: str, idx: int):
        self._text = text
        self._idx = idx

    def model_dump(self) -> dict:
        return {
            "id": f"chatcmpl-{self._idx}",
            "model": "raw/upstream-model-id",
            "choices": [{"index": 0, "delta": {"content": self._text}}],
        }


class _CountingUpstream:
    """Async generator of N fake chunks that RECORDS how many were pulled and
    whether aclose() was invoked — so a test can prove the loop stopped pulling
    upstream after a mid-stream kill (the remainder is never generated)."""

    def __init__(self, n: int):
        self._n = n
        self.pulled = 0
        self.closed = False

    def __aiter__(self):
        return self

    async def __anext__(self):
        if self.pulled >= self._n:
            raise StopAsyncIteration
        chunk = _FakeChunk(f"tok{self.pulled}", self.pulled)
        self.pulled += 1
        return chunk

    async def aclose(self):
        self.closed = True


def _make_router() -> LLMRouter:
    router = LLMRouter({"org_only_inference": True})
    # Bypass real model-config resolution: force a concrete model + identity body.
    router._build_kwargs = lambda body, stream=False, inference_allowlist=None: {  # type: ignore
        "model": "gpt-test",
        "messages": body.get("messages", []),
        "stream": True,
    }
    return router


async def _collect(router: LLMRouter, upstream: _CountingUpstream, state_check):
    """Drive acompletion_stream to exhaustion, returning the emitted SSE lines."""
    async def _exec(_kwargs):
        return upstream

    router._execute_completion = _exec  # type: ignore
    out: list[str] = []
    async for sse in router.acompletion_stream(
        {"model": "gpt-test", "messages": [{"role": "user", "content": "hi"}]},
        None,
        echo_model="gpt-test",
        state_check=state_check,
    ):
        out.append(sse)
    return out


def _content_lines(sse_lines: list[str]) -> list[str]:
    """Extract the delta-content tokens actually delivered to the client."""
    toks: list[str] = []
    for line in sse_lines:
        s = line.strip()
        if not s.startswith("data: "):
            continue
        payload = s[6:]
        if payload == "[DONE]":
            continue
        try:
            data = json.loads(payload)
        except (json.JSONDecodeError, TypeError):
            continue
        if data.get("error"):
            continue
        for ch in data.get("choices") or []:
            delta = ch.get("delta") or {}
            c = delta.get("content")
            if isinstance(c, str) and c:
                toks.append(c)
    return toks


def _has_terminal_error(sse_lines: list[str]) -> bool:
    for line in sse_lines:
        s = line.strip()
        if not s.startswith("data: "):
            continue
        payload = s[6:]
        if payload == "[DONE]":
            continue
        try:
            data = json.loads(payload)
        except (json.JSONDecodeError, TypeError):
            continue
        if data.get("error"):
            return True
    return False


# --------------------------------------------------------------------------- #
# (1) KILL: trip kill-switch after the first chunk -> stop emitting + terminal SSE
# --------------------------------------------------------------------------- #
def test_midstream_kill_switch_halts_stream(monkeypatch):
    # Throttle: check after EVERY chunk so the kill is observed promptly in the test.
    monkeypatch.setattr(_lr, "_MIDSTREAM_KS_CHECK_EVERY_CHUNKS", 1)

    router = _make_router()
    upstream = _CountingUpstream(n=10)

    calls = {"n": 0}

    async def _state_check() -> bool:
        # Active model is fine while the first chunk streams; on the SECOND
        # re-check (operator just tripped the kill-switch) it returns killed.
        calls["n"] += 1
        return calls["n"] >= 2

    sse = asyncio.run(_collect(router, upstream, _state_check))

    delivered = _content_lines(sse)
    # The PREFIX (chunks emitted before the kill verdict) stays; the REMAINDER
    # must NOT be delivered. We pulled far fewer than all 10 chunks.
    assert delivered, "expected at least the prefix to be delivered"
    assert len(delivered) < 10, (
        f"remainder leaked: delivered {len(delivered)}/10 chunks after kill"
    )
    # A terminal error SSE ends the stream.
    assert _has_terminal_error(sse), "expected a terminal error SSE on mid-stream kill"
    assert sse[-1].strip() == "data: [DONE]"
    # The upstream generator was closed -> upstream generation halted.
    assert upstream.closed, "upstream chunk generator was not aclose()'d on kill"
    # And we stopped PULLING upstream chunks (did not drain all 10).
    assert upstream.pulled < 10, f"kept pulling upstream: pulled {upstream.pulled}/10"


def test_midstream_model_state_isolation_halts_stream(monkeypatch):
    # Same contract, but the verdict source is model-state isolation (the
    # callback collapses both kill-switch and isolate into a single bool).
    monkeypatch.setattr(_lr, "_MIDSTREAM_KS_CHECK_EVERY_CHUNKS", 1)

    router = _make_router()
    upstream = _CountingUpstream(n=8)

    async def _state_check() -> bool:
        return True  # isolated immediately on the first re-check

    sse = asyncio.run(_collect(router, upstream, _state_check))

    assert _has_terminal_error(sse)
    assert upstream.closed
    assert len(_content_lines(sse)) < 8
    assert upstream.pulled < 8


# --------------------------------------------------------------------------- #
# (2) NO-OP: no kill-switch -> stream runs to completion, every chunk delivered
# --------------------------------------------------------------------------- #
def test_no_kill_switch_runs_to_completion_no_fp(monkeypatch):
    monkeypatch.setattr(_lr, "_MIDSTREAM_KS_CHECK_EVERY_CHUNKS", 1)

    router = _make_router()
    upstream = _CountingUpstream(n=6)

    checks = {"n": 0}

    async def _state_check() -> bool:
        checks["n"] += 1
        return False  # never killed

    sse = asyncio.run(_collect(router, upstream, _state_check))

    delivered = _content_lines(sse)
    assert delivered == [f"tok{i}" for i in range(6)], (
        f"clean stream was altered: {delivered}"
    )
    assert not _has_terminal_error(sse), "clean stream must NOT emit an error SSE"
    assert sse[-1].strip() == "data: [DONE]"
    assert upstream.pulled == 6, "clean stream must drain the full upstream"
    assert checks["n"] >= 1, "state_check should have been invoked at least once"


def test_no_state_check_is_transparent_passthrough():
    # state_check=None must be a transparent passthrough (back-compat).
    router = _make_router()
    upstream = _CountingUpstream(n=5)

    sse = asyncio.run(_collect(router, upstream, None))

    assert _content_lines(sse) == [f"tok{i}" for i in range(5)]
    assert not _has_terminal_error(sse)
    assert upstream.pulled == 5


# --------------------------------------------------------------------------- #
# (3) FAIL-OPEN: a Redis/exception error in the re-check must NOT kill the stream
# --------------------------------------------------------------------------- #
def test_redis_error_in_recheck_fails_open(monkeypatch):
    monkeypatch.setattr(_lr, "_MIDSTREAM_KS_CHECK_EVERY_CHUNKS", 1)

    router = _make_router()
    upstream = _CountingUpstream(n=6)

    async def _state_check() -> bool:
        # The re-check itself blows up (e.g. Redis down). The router must fail
        # OPEN: a legitimate live stream is never terminated on a check error.
        raise RuntimeError("redis connection reset")

    sse = asyncio.run(_collect(router, upstream, _state_check))

    delivered = _content_lines(sse)
    assert delivered == [f"tok{i}" for i in range(6)], (
        f"a Redis hiccup terminated a legit stream (fail-CLOSED bug): {delivered}"
    )
    assert not _has_terminal_error(sse), "fail-open: no error SSE on a check exception"
    assert sse[-1].strip() == "data: [DONE]"
    assert upstream.pulled == 6, "stream must complete despite the check error"


# --------------------------------------------------------------------------- #
# Throttle sanity: the re-check is THROTTLED, not run on every single chunk.
# --------------------------------------------------------------------------- #
def test_recheck_is_throttled_by_chunk_count(monkeypatch):
    # With the default throttle (every N chunks) a short clean stream should
    # trigger only a BOUNDED number of Redis reads, not one-per-chunk.
    monkeypatch.setattr(_lr, "_MIDSTREAM_KS_CHECK_EVERY_CHUNKS", 16)
    # Make the wall-clock branch never fire within the test by setting a long
    # interval, so we isolate the chunk-count throttle.
    monkeypatch.setattr(_lr, "_MIDSTREAM_KS_CHECK_INTERVAL_S", 1000.0)

    router = _make_router()
    upstream = _CountingUpstream(n=10)

    checks = {"n": 0}

    async def _state_check() -> bool:
        checks["n"] += 1
        return False

    sse = asyncio.run(_collect(router, upstream, _state_check))

    assert _content_lines(sse) == [f"tok{i}" for i in range(10)]
    # 10 chunks with a 16-chunk throttle and a huge time interval => 0 re-checks.
    assert checks["n"] == 0, (
        f"expected the check to be throttled (0 reads for 10 chunks), got {checks['n']}"
    )


def test_midstream_failopen_on_redis_artifact_terminate_on_real_kill():
    """E13 FP fix (caught by adversarial verify): the mid-stream state-check must
    FAIL-OPEN on the Redis-error/malformed fail-CLOSED ARTIFACTS — check_kill_switch
    returns is_killed=True scope='redis_unavailable'; check_model_state returns a
    'suspended' status with reason 'Redis unavailable'/'Malformed state data' — so a
    transient Redis blip never tears down an already-admitted live stream, while a
    REAL operator kill/isolate still ends it. Contract mirror of
    main._build_midstream_state_check._check."""
    try:
        from kill_switch import KillSwitchVerdict
        from model_state import ModelStateVerdict
    except ImportError:
        from ai_mesh_gateway.kill_switch import KillSwitchVerdict
        from ai_mesh_gateway.model_state import ModelStateVerdict

    def terminal(ks, ms):
        if getattr(ks, "is_killed", False) and str(getattr(ks, "scope", "")) != "redis_unavailable":
            return True
        r = str(getattr(ms, "reason", "") or "")
        if str(getattr(ms, "status", "active")) in ("isolated", "suspended") \
                and str(getattr(ms, "action", "")) != "alert" \
                and not r.startswith(("Redis unavailable", "Malformed state data")):
            return True
        return False

    A = ModelStateVerdict(status="active")
    # REAL operator kill / isolate -> terminate the stream
    assert terminal(KillSwitchVerdict(is_killed=True, scope="org_model"), A) is True
    assert terminal(KillSwitchVerdict(is_killed=False, scope="none"),
                    ModelStateVerdict(status="suspended", reason="manual isolate")) is True
    # Redis-error / malformed fail-closed ARTIFACTS -> FAIL-OPEN (do not terminate)
    assert terminal(KillSwitchVerdict(is_killed=True, scope="redis_unavailable"), A) is False
    assert terminal(KillSwitchVerdict(is_killed=False, scope="none"),
                    ModelStateVerdict(status="suspended", reason="Redis unavailable: boom")) is False
    # alert-only degraded + all-clear -> not terminal
    assert terminal(KillSwitchVerdict(is_killed=False, scope="none"),
                    ModelStateVerdict(status="degraded", action="alert")) is False
    assert terminal(KillSwitchVerdict(is_killed=False, scope="none"), A) is False
