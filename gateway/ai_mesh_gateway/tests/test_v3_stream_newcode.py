"""V3 — verification of the two NEW streaming-enforcement commits, from the client surface.

Under test
----------
  a0834d3b  fix(streaming): honor per-detector actions on the stream path
  7ad09344  fix(streaming): apply the non-stream fail-closed floor to the stream path

Everything here is driven through the UNMODIFIED stock ``openai`` SDK / raw httpx
over a REAL uvicorn loopback socket, so SSE framing (chunked transfer, frame
boundaries, ``data: [DONE]``) is what the guard and the client actually see —
not ASGITransport's in-memory concatenation.

HARNESS-ASYMMETRY CONTROL (mandatory, read before adding a test)
---------------------------------------------------------------
``OutputGuard.inspect`` resolves per-detector actions from ``org_config or
self._config`` (output_guard.py:775).  The non-stream call site passes
``org_config=None`` when ``org_slug`` is empty (main.py:9101), so it reads the
CONSTRUCTOR config; the stream call site always passes the org_config dict
(secure_streaming.py:371).  A fixture that populates only one of the two hands
the two transports DIFFERENT POLICIES, and any "stream weakens X" conclusion
drawn from it is an artifact, not a finding.

``_configure()`` therefore installs ONE dict object as gm.CONFIG, as the
OutputGuard constructor config AND as CONFIG_SYNC.get_config()'s return value,
and ``_RecordingGuard`` fingerprints the dict the guard actually resolved on
every inspect() call.  Every matrix cell asserts the two legs' fingerprints are
identical.  ``test_antivacuity_comparator_detects_a_planted_asymmetry`` proves
the control has teeth by planting the asymmetry on purpose.

Run:
  cd gateway && .venv/bin/python -m pytest \
      ai_mesh_gateway/tests/test_v3_stream_newcode.py -q -p no:cacheprovider
"""
from __future__ import annotations

import hashlib
import json
import socket
import threading
import time
from unittest.mock import AsyncMock, MagicMock

import fakeredis
import fakeredis.aioredis
import httpx
import openai
import pytest
import uvicorn

import ai_mesh_gateway.main as gm
from ai_mesh_gateway import middleware as gw_middleware
from ai_mesh_gateway.output_guard import OutputGuard, OutputVerdict
from ai_mesh_gateway.scanner import InputScanner
from ai_mesh_gateway.tests import test_openai_sdk_compat as T

# ── canaries, one per detector class the output guard actually reaches on the
# stock SDK chat path (empirically confirmed: pii / credential / ip_leakage). ──
SSN = "412-55-9083"
EMAIL = "john.doe@acme.com"
AKIA = "AKIAIOSFODNN7EXAMPLE"
INTERNAL_IP = "10.42.7.19"
# Free text with a real person name: a tier-2 semantic PII target the
# deterministic regex redactor has NO pattern for, so masking is a strict no-op.
UNMASKABLE = "The patient is Margarethe Villanueva-Okonkwo, seen at the annex."

MIXED = f"Contact {EMAIL} with key {AKIA} on host {INTERNAL_IP} today."

ACTION_KEYS = (
    "output_pii_action",
    "output_credential_action",
    "output_ip_leakage_action",
    "output_policy_action",
    "output_exfil_action",
    "output_hallucination_action",
    "output_pii_enabled",
    "output_credential_enabled",
    "output_ip_leakage_enabled",
    "output_scan_enabled",
    "output_guard_enabled",
    "enforcement_mode",
)

# leg → list of config fingerprints the guard resolved while serving that leg
_FP: dict[str, list[str]] = {"stream": [], "nonstream": []}
_LEG = {"current": "?"}
_LIVE: dict = {"stream": None}


def _fingerprint(cfg: dict) -> str:
    return hashlib.sha256(
        json.dumps({k: cfg.get(k) for k in ACTION_KEYS}, sort_keys=True).encode()
    ).hexdigest()[:16]


class _RecordingGuard(OutputGuard):
    """Real guard + a record of the config dict it ACTUALLY resolved per call."""

    async def inspect(self, text, **kw):  # noqa: ANN001, ANN003
        resolved = kw.get("org_config") or self._config
        _FP[_LEG["current"]].append(_fingerprint(resolved))
        return await super().inspect(text, **kw)


class _StubGuard:
    """Duck-typed guard returning one fixed verdict — models a tier-2 outage /
    an un-maskable semantic verdict / a guard crash at the guard BOUNDARY, which
    is exactly where both transports read it from."""

    def __init__(self, verdict=None, *, raises: Exception | None = None) -> None:
        self._verdict = verdict
        self._raises = raises

    async def inspect(self, text, **kw):  # noqa: ANN001, ANN003, ARG002
        _FP[_LEG["current"]].append("stub-guard")
        if self._raises is not None:
            raise self._raises
        return self._verdict


# ── upstream stubs ──────────────────────────────────────────────────────────
def _completion(content: str) -> dict:
    return {
        "id": "chatcmpl-v3-001", "object": "chat.completion", "created": 1700000000,
        "model": "gpt-4o-mini",
        "choices": [{"index": 0,
                     "message": {"role": "assistant", "content": content},
                     "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 8, "completion_tokens": 6, "total_tokens": 14},
    }


def _sse(token: str, finish=None) -> str:
    return "data: " + json.dumps({
        "id": "chatcmpl-v3-stream", "object": "chat.completion.chunk",
        "created": 1700000000, "model": "gpt-4o-mini",
        "choices": [{"index": 0, "delta": {"content": token}, "finish_reason": finish}],
    }) + "\n\n"


def _split_stream(text: str, chunk_size: int):
    parts = [text[i:i + chunk_size] for i in range(0, len(text), chunk_size)] or [""]

    async def gen(body, redacted_prompt=None, metrics=None, **_kw):  # noqa: ANN001
        for i, part in enumerate(parts):
            yield _sse(part, "stop" if i == len(parts) - 1 else None)
        if metrics is not None:
            metrics.completed = True
        yield "data: [DONE]\n\n"
    return gen


# ── live uvicorn harness ────────────────────────────────────────────────────
_FAKE_SERVER = fakeredis.FakeServer()
_CFG_SYNC = MagicMock()


def _apply_stubs() -> None:
    sync_r = fakeredis.FakeStrictRedis(server=_FAKE_SERVER, decode_responses=True)
    sync_r.set(f"auth:apikey:{hashlib.sha256(T.API_KEY.encode()).hexdigest()}",
               json.dumps(T._auth_payload()))

    async def _get_redis(self):  # noqa: ANN001
        return fakeredis.aioredis.FakeRedis(server=_FAKE_SERVER, decode_responses=True)
    gw_middleware.AuthMiddleware._get_redis = _get_redis

    async def _dispatch_stream(body, redacted_prompt=None, metrics=None, **kw):  # noqa: ANN001
        async for frame in _LIVE["stream"](body, redacted_prompt, metrics=metrics, **kw):
            yield frame

    router = MagicMock()
    async def _completion_stub(body, redacted_prompt=None, **_kw):  # noqa: ANN001
        return 200, _completion(_LIVE["text"])

    router.acompletion = AsyncMock(side_effect=_completion_stub)
    router.acompletion_stream = _dispatch_stream
    router.aembedding = AsyncMock(side_effect=T._fake_embedding)
    router.get_model_list = MagicMock(return_value=[{"id": "gpt-4o-mini"}])
    router.estimate_prompt_tokens = MagicMock(return_value=500)

    _CFG_SYNC.get_model_routing = MagicMock(
        return_value=[dict(T.TEST_MODEL), dict(T.EMBED_MODEL)])
    _CFG_SYNC.reload_models_now = AsyncMock()
    _CFG_SYNC.get_fallback_chains = MagicMock(return_value={"chains": {}, "per_primary": {}})

    gm.CONFIG_SYNC = _CFG_SYNC
    gm.LLM_ROUTER = router
    gm.INPUT_SCANNER = InputScanner(thread_pool_size=2)
    gm.AGENT_ID = None
    gm.POLICY_SYNC = None
    # policy-driven-detection cutover (task 7.1 / task 9): the STREAMING output guard now runs
    # ONLY when the org has an enabled Tier-1 pipeline output policy OR opt-in Tier-2 is on
    # (``_streaming_output_detection_active``); a zero-policy org is Passthrough and the stream
    # leg never reaches the guard. This suite verifies the RETAINED output-guard MACHINERY
    # (per-detector actions, stream/non-stream parity, fail-closed floor), so stub the activation
    # to True to represent an org with an enabled output policy — Tier-2 stays OFF (no Bedrock),
    # detection is driven by the config-resolved per-detector actions exactly as before.
    gm._streaming_output_detection_active = lambda org_slug, org_config: True
    gm.RATE_LIMITER = None
    gm.CIRCUIT_BREAKER = None
    gm.REDIS_CLIENT = None
    gm.TELEMETRY = None
    gm._emit_telemetry = lambda **_k: None
    gm._audit_fire_and_forget = lambda **_k: None
    _configure()
    try:
        gm.app.router.on_startup.clear()
        gm.app.router.on_shutdown.clear()
    except Exception:  # noqa: BLE001
        pass


def _configure(guard=None, **actions) -> dict:
    """Install ONE config dict everywhere the two transports can read it from."""
    cfg = dict(T.TEST_CONFIG)
    cfg.update({
        "output_guard_enabled": True, "output_scan_enabled": True,
        "output_pii_action": "allow", "output_credential_action": "allow",
        "output_ip_leakage_action": "allow", "output_policy_action": "allow",
        "output_exfil_action": "allow", "output_hallucination_action": "allow",
        "enforcement_mode": "block",
    })
    cfg.update(actions)
    gm.CONFIG = cfg
    _CFG_SYNC.get_config = MagicMock(return_value=cfg)   # SAME object, not a copy
    gm.OUTPUT_GUARD = guard if guard is not None else _RecordingGuard(gm.INPUT_SCANNER, cfg)
    _FP["stream"].clear()
    _FP["nonstream"].clear()
    return cfg


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


@pytest.fixture(scope="module")
def live_url():
    _apply_stubs()
    port = _free_port()
    server = uvicorn.Server(uvicorn.Config(
        gm.app, host="127.0.0.1", port=port, log_level="error", lifespan="off"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(200):
        if getattr(server, "started", False):
            break
        time.sleep(0.05)
    assert getattr(server, "started", False), "uvicorn live server did not start"
    yield f"http://127.0.0.1:{port}"
    server.should_exit = True
    thread.join(timeout=5)


_AUTH = {"Authorization": f"Bearer {T.API_KEY}"}


class Leg:
    """Normalized enforcement outcome, comparable across the two transports."""

    def __init__(self, *, blocked: bool, delivered: str, status: int,
                 wire: str, frames: int, done: bool, seconds: float) -> None:
        self.blocked = blocked
        self.delivered = delivered
        self.status = status
        self.wire = wire
        self.frames = frames       # content-bearing data: frames
        self.done = done
        self.seconds = seconds

    @property
    def outcome(self):
        """(blocked, delivered) — the comparator. Deliberately excludes HTTP
        status, which cannot match once stream headers have committed."""
        return (self.blocked, self.delivered)

    def __repr__(self) -> str:  # pragma: no cover - failure messages only
        return (f"Leg(blocked={self.blocked}, status={self.status}, "
                f"frames={self.frames}, delivered={self.delivered!r})")


async def _nonstream(live_url: str, text: str) -> Leg:
    _LEG["current"] = "nonstream"
    _LIVE["text"] = text
    t0 = time.perf_counter()
    async with httpx.AsyncClient(base_url=live_url, headers=_AUTH, timeout=30) as c:
        r = await c.post("/v1/chat/completions", json={
            "model": "gpt-4o-mini", "messages": [{"role": "user", "content": "go"}]})
    elapsed = time.perf_counter() - t0
    if r.status_code != 200:
        return Leg(blocked=True, delivered="", status=r.status_code,
                   wire=r.text, frames=0, done=True, seconds=elapsed)
    delivered = (r.json()["choices"][0]["message"].get("content") or "")
    return Leg(blocked=False, delivered=delivered, status=200,
               wire=r.text, frames=1, done=True, seconds=elapsed)


async def _stream(live_url: str, text: str, chunk_size: int = 8) -> Leg:
    _LEG["current"] = "stream"
    _LIVE["stream"] = _split_stream(text, chunk_size)
    t0 = time.perf_counter()
    async with httpx.AsyncClient(base_url=live_url, headers=_AUTH, timeout=30) as c:
        r = await c.post("/v1/chat/completions", json={
            "model": "gpt-4o-mini", "messages": [{"role": "user", "content": "go"}],
            "stream": True})
    elapsed = time.perf_counter() - t0
    if r.status_code != 200:
        return Leg(blocked=True, delivered="", status=r.status_code,
                   wire=r.text, frames=0, done=False, seconds=elapsed)
    parts, frames, blocked, done = [], 0, False, False
    for line in r.text.splitlines():
        if not line.startswith("data: "):
            continue
        payload = line[6:].strip()
        if payload == "[DONE]":
            done = True
            continue
        try:
            frame = json.loads(payload)
        except json.JSONDecodeError:
            continue
        if isinstance(frame.get("error"), dict):
            blocked = True
            continue
        for choice in frame.get("choices") or []:
            piece = (choice.get("delta") or {}).get("content") or ""
            if piece:
                parts.append(piece)
                frames += 1
    return Leg(blocked=blocked, delivered="".join(parts), status=200,
               wire=r.text, frames=frames, done=done, seconds=elapsed)


def _assert_same_policy(label: str) -> None:
    """Anti-vacuity: both legs must have resolved the SAME config fingerprint."""
    assert _FP["stream"], f"{label}: stream leg never reached the output guard"
    assert _FP["nonstream"], f"{label}: non-stream leg never reached the output guard"
    assert set(_FP["stream"]) == set(_FP["nonstream"]), (
        f"{label}: FIXTURE ASYMMETRY — the two transports were handed different "
        f"policies (stream={set(_FP['stream'])}, nonstream={set(_FP['nonstream'])}). "
        "Any comparison here would be meaningless.")


async def _both(live_url: str, text: str, chunk_size: int = 8) -> tuple[Leg, Leg]:
    ns = await _nonstream(live_url, text)
    st = await _stream(live_url, text, chunk_size)
    return ns, st


# ═══════════════════════════════════════════════════════════════════════════
# §0  ANTI-VACUITY CONTROLS
# ═══════════════════════════════════════════════════════════════════════════
@pytest.mark.asyncio
async def test_antivacuity_stream_leg_really_streams(live_url):
    """A pass-through stream must arrive as MANY frames, not one blob — otherwise
    every 'chunk boundary' assertion below would be testing nothing."""
    _configure()
    st = await _stream(live_url, "The quick brown fox jumps over the lazy dog twice.", 4)
    assert st.status == 200
    assert st.frames > 1, f"stream leg delivered a single frame: {st.wire!r}"
    assert st.done, "stream did not terminate with data: [DONE]"


@pytest.mark.asyncio
async def test_antivacuity_comparator_detects_a_planted_asymmetry(live_url):
    """Proves the fingerprint guard can FAIL.

    Reproduces the harness artifact that produced the REFUTED "stream downgrades
    block to redact" finding: give the constructor config (read by the non-stream
    leg) ``block`` while org_config (read by the stream leg) says ``redact``.  The
    legs then disagree — and ``_assert_same_policy`` must catch it BEFORE the
    disagreement can be mistaken for a defect.
    """
    ctor_cfg = _configure(output_pii_action="block")
    org_cfg = dict(ctor_cfg)
    org_cfg["output_pii_action"] = "redact"
    _CFG_SYNC.get_config = MagicMock(return_value=org_cfg)  # PLANTED asymmetry

    ns, st = await _both(live_url, f"Customer SSN {SSN} confirmed.")
    assert ns.blocked and ns.status in (400, 403) and not st.blocked, (
        "planted asymmetry did not even produce divergent outcomes; "
        f"ns={ns} ns.wire={ns.wire[:400]!r} st={st}")
    with pytest.raises(AssertionError, match="FIXTURE ASYMMETRY"):
        _assert_same_policy("planted")


# ═══════════════════════════════════════════════════════════════════════════
# §1  a0834d3b — PER-DETECTOR ACTION PARITY
# ═══════════════════════════════════════════════════════════════════════════
_ISOLATION = [
    ("pii", "output_pii_action", f"Customer SSN {SSN} confirmed.", SSN),
    ("credential", "output_credential_action", f"The key is {AKIA} ok.", AKIA),
    ("ip_leakage", "output_ip_leakage_action", f"Host {INTERNAL_IP}:5432 is up.", INTERNAL_IP),
]


@pytest.mark.asyncio
@pytest.mark.parametrize("klass,key,text,canary", _ISOLATION,
                         ids=[c[0] for c in _ISOLATION])
@pytest.mark.parametrize("action", ["block", "redact", "rewrite", "flag", "allow"])
async def test_per_detector_action_parity(live_url, klass, key, text, canary, action):
    """15-cell isolation matrix: one detector configured, the other two ``allow``.

    Both transports must reach the SAME enforcement outcome on IDENTICAL input
    and IDENTICAL config.  ``block`` is the documented BY-DESIGN difference (a
    stream cannot become a 400 after headers commit) — there the assertion is
    the security-equivalent one: NO content escapes on either leg.
    """
    _configure(**{key: action})
    ns, st = await _both(live_url, text)
    _assert_same_policy(f"{klass}/{action}")

    if action == "block":
        assert ns.blocked and ns.status in (400, 403), f"non-stream: {ns}"
        assert st.blocked, f"stream did not block: {st}"
        assert st.frames == 0, f"stream leaked {st.frames} content frames: {st.wire!r}"
        assert canary not in ns.wire and canary not in st.wire, "canary on the wire"
        return

    assert not ns.blocked and not st.blocked, f"ns={ns} st={st}"
    if action == "rewrite":
        # BY-DESIGN-DIFFERENCE (V3-03): the two transports produce DIFFERENT bytes
        # for the same rewrite. Non-stream re-infers through the LLM router
        # (main.py:_rewrite_output_response_text_via_router) and then applies the
        # deterministic redactor as a safety net; the stream path calls
        # rewrite_output_response_text() with no bedrock_client
        # (secure_streaming.py:437), which falls back to the STATIC canned string.
        # Neither leaks, so the invariant asserted here is containment, and the
        # divergence itself is pinned by
        # test_rewrite_bytes_diverge_between_transports below.
        assert canary not in ns.delivered, f"non-stream rewrite leaked: {ns}"
        assert canary not in st.delivered, f"stream rewrite leaked: {st}"
        assert canary not in st.wire, "canary on the SSE wire under rewrite"
        return

    assert st.outcome == ns.outcome, (
        f"{klass}/{action} PARITY BREAK\n  nonstream={ns}\n  stream   ={st}")
    if action in ("flag", "allow"):
        assert canary in st.delivered, f"{action} must deliver verbatim: {st}"
    else:
        assert canary not in st.delivered, f"{action} leaked the canary: {st}"
        assert canary not in st.wire, "canary present raw in the SSE wire body"


@pytest.mark.asyncio
@pytest.mark.parametrize("pii", ["redact", "flag", "allow"])
@pytest.mark.parametrize("cred", ["redact", "flag", "allow"])
@pytest.mark.parametrize("ip", ["redact", "flag", "allow"])
async def test_strict_mixed_matrix_27_cells(live_url, pii, cred, ip):
    """THE regression this commit exists for: 27 delivered pii x cred x ip cells.

    Pre-a0834d3b the stream branch called ``scanner.redact_pii`` — a BLANKET
    redact_all — so ANY redact verdict masked EVERY class.  Assert (a) strict
    operator selection: a class set to flag/allow keeps its canary verbatim, a
    class set to redact loses it; and (b) byte-exact stream/non-stream parity.

    GRANULARITY (deliberate): the whole answer is delivered as ONE upstream delta
    so the guard inspects the SAME text on both legs.  ``MIXED`` contains an
    email and a dotted IPv4, and '.' is a SENTENCE_BOUNDARY flush trigger
    (secure_streaming.py:297), so any smaller chunking makes the stream guard see
    fragments the non-stream guard never sees — a difference in what was
    *inspected*, not in how it was *enforced*.  Multi-segment flushing is covered
    separately by test_multisegment_ip_straddling_a_redact_flush_boundary and by
    §3 below; that the stream leg genuinely emits many frames is proven by
    test_antivacuity_stream_leg_really_streams.
    """
    _configure(output_pii_action=pii, output_credential_action=cred,
               output_ip_leakage_action=ip)
    ns, st = await _both(live_url, MIXED, chunk_size=len(MIXED))
    cell = f"pii={pii} cred={cred} ip={ip}"
    _assert_same_policy(cell)
    assert not ns.blocked and not st.blocked, f"{cell}: ns={ns} st={st}"
    assert st.delivered == ns.delivered, (
        f"{cell} PARITY BREAK\n  nonstream={ns.delivered!r}\n  stream   ={st.delivered!r}")
    for canary, act, name in ((EMAIL, pii, "pii"), (AKIA, cred, "credential"),
                              (INTERNAL_IP, ip, "ip_leakage")):
        if act == "redact":
            assert canary not in st.delivered, (
                f"{cell}: {name}=redact did not mask {canary!r}: {st.delivered!r}")
        else:
            assert canary in st.delivered, (
                f"{cell}: STRICT VIOLATION — {name}={act} was masked anyway: "
                f"{st.delivered!r}")


@pytest.mark.asyncio
async def test_rewrite_bytes_diverge_between_transports(live_url):
    """V3-03 (LOW, BY-DESIGN-DIFFERENCE) — pinned so a future change is noticed.

    Non-stream rewrite re-infers through the LLM router and then runs the
    deterministic redactor over the result; the stream path has no router handle
    at flush time and emits the STATIC canned replacement.  Both withhold the
    canary; only the bytes differ.
    """
    _configure(output_pii_action="rewrite")
    ns, st = await _both(live_url, f"Customer SSN {SSN} confirmed.")
    _assert_same_policy("rewrite-divergence")
    assert SSN not in ns.delivered and SSN not in st.delivered
    assert st.delivered != ns.delivered, (
        "rewrite is now byte-identical across transports — V3-03 has been fixed; "
        "delete this test and restore the parity assertion in "
        "test_per_detector_action_parity")
    assert "removed from the generated response" in st.delivered, st.delivered


@pytest.mark.xfail(strict=True, reason=(
    "FINDING V3-02 (HIGH, PRE-EXISTING — not introduced by either commit under "
    "test): a detectable token straddling a REDACT flush boundary escapes the "
    "guard entirely. A clean flush retains a lookahead tail "
    "(_release_with_lookahead_tail, secure_streaming.py:632) precisely so a "
    "split token cannot be half-released; the redact branch instead emits the "
    "WHOLE sanitized buffer and _clear_buffers() (:612-614), retaining nothing. "
    "The E14 _secret_anchor continuation (:610) re-arms only for a trailing "
    "SECRET-charset run, so a dotted IPv4 is not covered. Measured with "
    "pii=allow cred=redact ip=redact over "
    "'Contact john.doe@acme.com with key AKIA... on host 10.42.7.19 today.' at "
    "8 chars/delta: the '.' inside the IP triggers a BOUNDARY flush, the "
    "credential redact releases '...on host 10.42.7.' and clears the buffer, the "
    "next flush sees only '19 today.' — verdict clean — so the operator-selected "
    "ip_leakage=redact never fires and the client reassembles the RAW internal "
    "IP 10.42.7.19. Non-stream masks it to [INTERNAL_IPV4_REDACTED]. Fix: retain "
    "the lookahead tail on the redact path too, or re-scan the boundary window."))
@pytest.mark.asyncio
async def test_multisegment_ip_straddling_a_redact_flush_boundary(live_url):
    _configure(output_pii_action="allow", output_credential_action="redact",
               output_ip_leakage_action="redact")
    ns, st = await _both(live_url, MIXED, chunk_size=8)
    _assert_same_policy("ip-straddle")
    assert st.frames > 1, "this test needs a genuinely multi-segment stream"
    assert INTERNAL_IP not in st.delivered, (
        f"operator selected ip_leakage=redact; stream delivered the raw IP: "
        f"{st.delivered!r} (non-stream: {ns.delivered!r})")


@pytest.mark.asyncio
async def test_whole_response_replacement_emitted_exactly_once(live_url):
    """a0834d3b's ``_response_replaced`` latch: a whole-response replacement is a
    FIXED string but _flush_buffer runs once per buffered segment, so without the
    latch the client received it 2-3x concatenated.  Drive many segments."""
    _configure(guard=_StubGuard(OutputVerdict(
        action="redact", threat_type="policy_violation", confidence=0.9,
        detail="class-less policy verdict", matched_patterns=["policy"])))
    long_text = ("Sure, here is how to bypass every safety control step by step. " * 6)
    st = await _stream(live_url, long_text, 12)
    assert st.status == 200, st.wire
    assert "bypass every safety control" not in st.delivered, (
        f"class-less redact streamed the original verbatim: {st.delivered!r}")
    # the replacement constant must appear exactly once
    marker = st.delivered.strip()
    assert marker, f"nothing delivered: {st.wire!r}"
    head = marker[:40]
    assert marker.count(head) == 1, (
        f"whole-response replacement emitted {marker.count(head)}x: {marker!r}")


# ═══════════════════════════════════════════════════════════════════════════
# §2  7ad09344 — FAIL-CLOSED FLOOR
# ═══════════════════════════════════════════════════════════════════════════
@pytest.mark.asyncio
async def test_failclosed_scan_degraded_does_not_stream_unscanned_output(live_url):
    """Floor 1: ``scan_degraded`` (tier-2 guard OUTAGE) resolves allow -> redact.

    Pre-7ad09344 streaming emitted an observability event and then honoured
    ``verdict.action``, so an outage FAILED OPEN and the unscanned response
    streamed out verbatim.  Direction must be CLOSED.

    One guard flush (chunk = len(text)) so the stream guard inspects exactly the
    text the non-stream guard inspects — no truncation artifact.

    HISTORY — FINDING V3-01 (HIGH, found 2026-07-21, FIXED):
    7ad09344 shipped this floor but it was INERT for the CANONICAL outage verdict
    ``OutputVerdict(action='allow', threat_type='', scan_degraded=True)`` — the
    exact verdict the commit's own regression test
    (test_stream_failclosed_parity.py:92) uses.  ``enforce_output`` DID resolve
    allow->redact and secure_streaming.py:488 DID take the redact branch, but
    :517 handed the sanitizer the UNRESOLVED verdict, so
    ``output_guard._sanitize_output_core`` short-circuited on
    ``if action != "redact": return response_text`` (verdict.action was still
    "allow").  The scrub was a strict no-op; ``_whole_response`` is True (empty
    threat_type is not in _REDACTABLE_OUTPUT_CATEGORIES) so the :564
    un-maskable->block floor was skipped by ``not _whole_response``; the branch
    emitted action="flag" and DELIVERED THE UNSCANNED TEXT VERBATIM:
        nonstream -> 'Contact j***@a***.com about SSN ***-**-9083'
        stream    -> 'Contact john.doe@acme.com about SSN 412-55-9083'
    The commit's own regression test passed only because chunk=24 truncated the
    email mid-token at a '.' flush boundary while the _response_replaced latch
    dropped every later segment — a substring artifact, not a redaction.
    Fixed by threading the RESOLVED action into the sanitizer at :517 via a
    synthesised verdict, so both transports feed the sanitizer identically.
    """
    text = f"Contact {EMAIL} about SSN {SSN}"
    verdict = OutputVerdict(action="allow", threat_type="", scan_degraded=True)
    _configure(guard=_StubGuard(verdict))
    ns = await _nonstream(live_url, text)
    _configure(guard=_StubGuard(verdict))
    st = await _stream(live_url, text, chunk_size=len(text))
    assert EMAIL not in ns.delivered, f"non-stream leg also failed open: {ns}"
    assert EMAIL not in st.delivered, (
        f"guard outage FAILED OPEN on the stream path: {st.delivered!r}")
    assert SSN not in st.delivered, f"guard outage leaked the SSN: {st.delivered!r}"
    assert EMAIL not in st.wire, "raw PII on the SSE wire under scan_degraded"


@pytest.mark.asyncio
async def test_scan_degraded_with_a_typed_threat_reaches_parity(live_url):
    """Typed-threat outage (threat_type='pii'): both legs MASK and deliver.

    HISTORY — FINDING V3-04 (LOW, RESOLVED by the V3-01 fix, not separately):
    while :517 passed the unresolved verdict, this cell diverged — the stream
    BLOCKED (0 content frames) where non-stream redacted and delivered.  Safe
    direction, but still a parity break.  Threading the resolved action through
    made the sanitizer actually mask, so the redact-no-op fail-closed escalation
    at :564 no longer fires and both transports now deliver the same masked
    bytes.  Asserted as PARITY here rather than retired, so a regression in
    either direction (silent pass-through OR a return to over-blocking) fails.
    """
    text = f"Contact {EMAIL} about SSN {SSN}"
    verdict = OutputVerdict(action="allow", threat_type="pii", scan_degraded=True)
    _configure(guard=_StubGuard(verdict))
    ns = await _nonstream(live_url, text)
    _configure(guard=_StubGuard(verdict))
    st = await _stream(live_url, text, chunk_size=len(text))
    assert not st.blocked and st.frames > 0, f"stream withheld the answer: {st}"
    assert not ns.blocked, f"non-stream leg changed shape: {ns}"
    assert st.delivered == ns.delivered, (
        f"typed-threat outage PARITY BREAK\n  nonstream={ns.delivered!r}\n"
        f"  stream   ={st.delivered!r}")
    # a real masking, not a pass-through dressed up as one
    assert EMAIL not in st.delivered and SSN not in st.delivered, st.delivered
    assert EMAIL not in st.wire and SSN not in st.wire, f"canary on the wire: {st.wire!r}"
    assert st.delivered != text, "delivered bytes are identical to the raw model output"


@pytest.mark.asyncio
async def test_failclosed_control_healthy_guard_allow_still_allows(live_url):
    """Negative control for floor 1: the floor must fire ONLY on degradation."""
    _configure(guard=_StubGuard(
        OutputVerdict(action="allow", threat_type="", scan_degraded=False)))
    text = f"Contact {EMAIL} about SSN {SSN}."
    ns, st = await _both(live_url, text)
    assert st.delivered == text, f"healthy allow was mutated: {st.delivered!r}"
    assert ns.delivered == text, f"non-stream healthy allow was mutated: {ns!r}"


@pytest.mark.asyncio
async def test_failclosed_unmaskable_redact_blocks_instead_of_shipping_raw(live_url):
    """Floor 2: redact selected but NOTHING maskable -> block.

    Pre-7ad09344 streaming downgraded to "flag" and released the response,
    shipping the exact bytes the operator asked to remove.
    """
    _configure(guard=_StubGuard(OutputVerdict(
        action="redact", threat_type="pii", confidence=0.9,
        detail="tier-2 semantic pii, no deterministic span",
        matched_patterns=["semantic_pii"])))
    ns, st = await _both(live_url, UNMASKABLE, chunk_size=len(UNMASKABLE))
    assert st.blocked, f"un-maskable redact was RELEASED on the stream: {st}"
    assert "Margarethe Villanueva-Okonkwo" not in st.delivered, st.delivered
    assert "Margarethe Villanueva-Okonkwo" not in st.wire, "raw name on the SSE wire"
    assert st.frames == 0, f"content escaped before the block: {st.frames} frames"
    assert ns.blocked, f"non-stream leg did not block either: {ns}"


@pytest.mark.asyncio
async def test_failclosed_control_maskable_redact_still_delivers(live_url):
    """Negative control for floor 2: an ordinary maskable redact must NOT become
    a block — the floor is a floor, not an escalation of every redact."""
    _configure(output_pii_action="redact")
    text = f"Customer SSN {SSN} confirmed."
    ns, st = await _both(live_url, text, chunk_size=len(text))
    assert not st.blocked and not ns.blocked, f"ns={ns} st={st}"
    assert SSN not in st.delivered and "Customer" in st.delivered, st.delivered
    assert st.outcome == ns.outcome, f"ns={ns} st={st}"


_LATCH_TEXT = ("Contact john.doe@acme.com now. The SSN is 412-55-9083. "
               "The host is 10.42.7.19 here. Ordinary trailing sentence one. "
               "Ordinary trailing sentence two.")


@pytest.mark.asyncio
@pytest.mark.parametrize("chunk", [len(_LATCH_TEXT), 24, 8, 1])
async def test_whole_response_replacement_not_truncated_at_any_granularity(
        live_url, chunk):
    """``_response_replaced`` latch re-check at every flush granularity.

    Pre-V3-01-fix this path never ran for a degraded verdict (the sanitizer
    no-opped), so the latch's behaviour under a real replacement was unverified —
    and test_stream_failclosed_parity.py passed on a truncated prefix rather than
    on a replacement.  Now that the sanitizer honours the resolved action, assert
    the replacement is delivered WHOLE and EXACTLY ONCE from one flush down to
    one character per frame: no truncation, no 2-3x concatenation.
    """
    _configure(guard=_StubGuard(
        OutputVerdict(action="allow", threat_type="", scan_degraded=True)))
    st = await _stream(live_url, _LATCH_TEXT, chunk)
    assert not st.blocked, st.wire
    expected = "The original model output was rewritten to comply with response safety policy."
    assert st.delivered == expected, (
        f"chunk={chunk}: replacement truncated or repeated: {st.delivered!r}")
    assert st.done


class _TransientOutageGuard:
    """Tier-2 breaker OPEN on the first flush, recovered (clean) afterwards."""

    def __init__(self) -> None:
        self.calls = 0

    async def inspect(self, text, **kw):  # noqa: ANN001, ANN003, ARG002
        _FP[_LEG["current"]].append("transient-guard")
        self.calls += 1
        if self.calls == 1:
            return OutputVerdict(action="allow", threat_type="", scan_degraded=True)
        return OutputVerdict(action="allow", threat_type="")


@pytest.mark.asyncio
@pytest.mark.parametrize("chunk", [24, 8])
async def test_whole_response_replacement_suppresses_later_clean_segments(
        live_url, chunk):
    guard = _TransientOutageGuard()
    _configure(guard=guard)
    st = await _stream(live_url, _LATCH_TEXT, chunk)
    assert guard.calls > 1, "this test needs more than one guard flush"
    assert SSN not in st.delivered, (
        f"chunk={chunk}: raw SSN delivered after a whole-response replacement: "
        f"{st.delivered!r}")
    assert INTERNAL_IP not in st.delivered, (
        f"chunk={chunk}: raw internal IP delivered after a whole-response "
        f"replacement: {st.delivered!r}")


@pytest.mark.asyncio
async def test_failclosed_guard_exception_delivers_no_content(live_url):
    """A guard CRASH must not release unscanned bytes on the stream path."""
    _configure(guard=_StubGuard(raises=RuntimeError("output guard exploded")))
    st = await _stream(live_url, f"Contact {EMAIL} about SSN {SSN}.", 8)
    assert EMAIL not in st.wire, f"guard crash FAILED OPEN: {st.wire!r}"
    assert SSN not in st.wire, f"guard crash leaked the SSN: {st.wire!r}"
    assert st.frames == 0, f"guard crash released {st.frames} content frames"


# ═══════════════════════════════════════════════════════════════════════════
# §3  CHUNK-BOUNDARY INTEGRITY
# ═══════════════════════════════════════════════════════════════════════════
@pytest.mark.asyncio
@pytest.mark.parametrize("secret,chunk_size", [
    (AKIA, 1),      # one character per SSE frame
    (AKIA, 3),
    (AKIA, 7),
    (SSN, 2),
    (INTERNAL_IP, 1),
])
async def test_secret_split_across_frame_boundaries_is_still_caught(
        live_url, secret, chunk_size):
    _configure(output_pii_action="redact", output_credential_action="redact",
               output_ip_leakage_action="redact")
    text = f"Please store {secret} safely and do not share it with anyone."
    st = await _stream(live_url, text, chunk_size)
    assert st.status == 200, st.wire
    assert secret not in st.delivered, (
        f"chunk_size={chunk_size}: client reassembled the raw secret: {st.delivered!r}")
    assert secret not in st.wire, "raw secret present in the SSE wire body"
    assert "Please store" in st.delivered, f"answer destroyed, not masked: {st.delivered!r}"


@pytest.mark.asyncio
@pytest.mark.parametrize("chunk_size", [1, 5])
async def test_secret_abutting_the_stream_end_is_still_caught(live_url, chunk_size):
    """No trailing text to force a sentence-boundary flush: the secret must be
    masked by the terminal DONE flush."""
    _configure(output_credential_action="redact")
    st = await _stream(live_url, f"The access key is {AKIA}", chunk_size)
    assert st.status == 200
    assert AKIA not in st.wire, f"secret at stream end escaped: {st.wire!r}"
    assert st.done, "stream did not terminate with [DONE]"


@pytest.mark.asyncio
async def test_no_prefix_of_the_secret_leaks_before_enforcement(live_url):
    """A mid-stream enforcement must not have already delivered pre-enforcement
    bytes of the sensitive span.  Assert NO prefix of the secret (>= 6 chars)
    appears anywhere on the wire, at 1 char per frame — the worst case for
    premature release."""
    _configure(output_credential_action="redact")
    st = await _stream(live_url, f"key {AKIA} then a long tail of ordinary words "
                                 "that forces several intermediate buffer flushes "
                                 "before the stream finally reaches its end.", 1)
    leaked = [AKIA[:n] for n in range(6, len(AKIA) + 1) if AKIA[:n] in st.wire]
    assert not leaked, f"pre-enforcement prefixes on the wire: {leaked}"


# ═══════════════════════════════════════════════════════════════════════════
# §4  TERMINATION
# ═══════════════════════════════════════════════════════════════════════════
@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["allow", "flag", "redact", "rewrite", "block"])
async def test_every_stream_terminates_bounded_with_done(live_url, action):
    _configure(output_pii_action=action)
    st = await _stream(live_url, f"Customer SSN {SSN} confirmed.", 6)
    assert st.seconds < 20, f"{action}: stream took {st.seconds:.1f}s"
    assert st.done, f"{action}: stream never emitted data: [DONE]: {st.wire!r}"


@pytest.mark.asyncio
async def test_blocked_stream_delivers_zero_content_frames(live_url):
    """BY-DESIGN-DIFFERENCE: headers are already committed, so a stream block is
    an in-band OpenAI-shaped error frame + [DONE], not a 400.  What must hold is
    the security-equivalent property: ZERO content-bearing data frames."""
    _configure(output_pii_action="block")
    st = await _stream(live_url, f"Customer SSN {SSN} confirmed.", 6)
    assert st.status == 200, "stream block should not change the committed status"
    assert st.blocked, f"no error frame emitted: {st.wire!r}"
    assert st.frames == 0, f"content escaped a blocked stream: {st.wire!r}"
    assert st.delivered == "", f"content escaped a blocked stream: {st.delivered!r}"
    assert SSN not in st.wire
    assert st.done


@pytest.mark.asyncio
async def test_blocked_stream_is_consumable_by_the_stock_sdk(live_url):
    """The stock SDK must be able to iterate a blocked stream without a parse
    crash — a firewall that corrupts the transport is not deployable."""
    _configure(output_pii_action="block")
    _LEG["current"] = "stream"
    _LIVE["stream"] = _split_stream(f"Customer SSN {SSN} confirmed.", 6)
    client = openai.AsyncOpenAI(base_url=f"{live_url}/v1", api_key=T.API_KEY,
                                max_retries=0)
    try:
        stream = await client.chat.completions.create(
            model="gpt-4o-mini", messages=[{"role": "user", "content": "go"}],
            stream=True)
        content = ""
        try:
            async for chunk in stream:
                if chunk.choices and chunk.choices[0].delta:
                    content += chunk.choices[0].delta.content or ""
        except openai.APIError:
            pass  # an in-band error event surfacing as an SDK error is acceptable
        assert SSN not in content, content
        assert content == "", f"blocked stream delivered content: {content!r}"
    finally:
        await client.close()
