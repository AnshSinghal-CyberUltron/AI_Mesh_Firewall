# Provenance: written by PR #121 Plan Reviewer A (review 5869433944) on the review host and copied
# unchanged below this header for the F1a-3 revision 2 evidence, with credit. Originally
# scripts/test_repro_develop.py (renamed so no pytest collector picks it up) or scripts/n1_nonascii_key.py.
"""Reviewer A (PR #121) reproductions of the plan's develop defect claims, run against develop
943a5f21 (PYTHONPATH points at a sparse worktree of that commit). Each test ASSERTS the defect
exists; a passing test means "reproduced". Nothing here is product code."""

from __future__ import annotations

import asyncio
import os
import pickle
import time

import pytest

from gateway_v2.dispatch.openai_upstream import (
    BODY_ENDPOINT_KEY,
    BODY_OPENAI_KEY,
    CHAT_ENDPOINT,
    OpenAIUpstreamProvider,
    httpx_client_factory,
)
from gateway_v2.dispatch.provider import UpstreamRequest
from gateway_v2.edge.wire.sse_decode import SSEDecoder
from gateway_v2.runtime.kinds import PoolKind
from gateway_v2.runtime.resources import load_contract


def _req() -> UpstreamRequest:
    body = {"model": "m", "messages": [{"role": "user", "content": "x"}], "stream": True}
    return UpstreamRequest("t", {BODY_OPENAI_KEY: body, BODY_ENDPOINT_KEY: CHAT_ENDPOINT})


# ------------------------------------------------------------------ tiny HTTP servers
SSE_BODY = (
    b'data: {"choices":[{"index":0,"delta":{"content":"hi"}}]}\n\n'
    b'data: {"choices":[{"index":0,"delta":{},"finish_reason":"stop"}]}\n\n'
    b"data: [DONE]\n\n"
)


async def _serve(handler):
    server = await asyncio.start_server(handler, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    return server, port


async def _read_request(reader: asyncio.StreamReader) -> bytes:
    head = await reader.readuntil(b"\r\n\r\n")
    length = 0
    for line in head.split(b"\r\n"):
        if line.lower().startswith(b"content-length:"):
            length = int(line.split(b":", 1)[1])
    body = await reader.readexactly(length) if length else b""
    return head + body


# ------------------------------------------------------------------ C3
def test_c3_trust_env_proxy_and_default_auth(monkeypatch):
    """The shipped factory honours HTTP(S)_PROXY and sets a client-wide Authorization header.
    Functional: with HTTP_PROXY set, a request to an http:// provider is sent to the proxy, bearer
    in clear (the ollama / private-http case)."""
    seen: list[bytes] = []

    async def proxy(reader, writer):
        seen.append(await _read_request(reader))
        writer.write(b"HTTP/1.1 503 x\r\ncontent-length: 0\r\nconnection: close\r\n\r\n")
        await writer.drain()
        writer.close()

    async def run():
        server, port = await _serve(proxy)
        monkeypatch.setenv("HTTP_PROXY", f"http://127.0.0.1:{port}")
        monkeypatch.setenv("HTTPS_PROXY", f"http://127.0.0.1:{port}")
        monkeypatch.delenv("NO_PROXY", raising=False)
        monkeypatch.delenv("no_proxy", raising=False)
        client = httpx_client_factory(api_key="sk-plant-c3", timeout_s=5, connections=1)()
        assert client.trust_env is True
        assert client.headers.get("authorization") == "Bearer sk-plant-c3"
        provider = OpenAIUpstreamProvider(
            base_url="http://10.255.255.1:11434/v1",
            decoder_factory=SSEDecoder,
            client_factory=lambda: client,
        )
        events = [e async for e in provider.open(_req())]
        await provider.aclose()
        server.close()
        return events

    events = asyncio.run(run())
    assert seen, "request did not go to the proxy"
    assert seen[0].startswith(b"POST http://10.255.255.1:11434/v1/chat/completions")
    assert b"authorization: Bearer sk-plant-c3" in seen[0].lower().replace(
        b"authorization: bearer", b"authorization: Bearer"
    )
    assert events[-1].gateway_error is not None


# ------------------------------------------------------------------ C4
def test_c4_one_connection_per_worker_serialises_streams():
    contract, _ = load_contract(env={k: v for k, v in os.environ.items() if k != "WEB_CONCURRENCY"})
    connections = contract.pool_size(PoolKind.PROVIDER)
    assert connections == contract.workers()

    async def upstream(reader, writer):
        try:
            while True:
                await _read_request(reader)
                await asyncio.sleep(1.0)
                writer.write(
                    b"HTTP/1.1 200 OK\r\ncontent-type: text/event-stream\r\n"
                    + f"content-length: {len(SSE_BODY)}\r\n\r\n".encode()
                    + SSE_BODY
                )
                await writer.drain()
        except (asyncio.IncompleteReadError, ConnectionError):
            writer.close()

    async def run():
        server, port = await _serve(upstream)
        provider = OpenAIUpstreamProvider(
            base_url=f"http://127.0.0.1:{port}/v1",
            decoder_factory=SSEDecoder,
            client_factory=httpx_client_factory(
                api_key="k", timeout_s=contract.max_stream_duration_s(), connections=connections
            ),
            connections=connections,
        )
        start = time.perf_counter()

        async def one():
            async for e in provider.open(_req()):
                assert e.gateway_error is None, e.gateway_error
            return time.perf_counter() - start

        done = sorted(await asyncio.gather(*(one() for _ in range(4))))
        await provider.aclose()
        server.close()
        return done

    done = asyncio.run(run())
    print(f"C4 workers={contract.workers()} pool_size(PROVIDER)={connections} done={done}")
    if connections == 1:
        assert done[-1] > 3.5, done  # serialised: ~1, 2, 3, 4 s


# ------------------------------------------------------------------ C5
def test_c5_cgnat_and_alibaba_metadata_pass_ssrf():
    from gateway_v2.dispatch.url_guard import Refusal, resolve_checked

    async def resolver(host, port):
        return {"evil.example": ("100.100.100.200",), "six.example": ("2002:a9fe:a9fe::1",),
                "teredo.example": ("2001:0:4136:e378:8000:63bf:5601:5601",)}[host]

    async def run():
        out = {}
        out["literal 100.64.0.1"] = await resolve_checked("https://100.64.0.1/v1", resolver=resolver)
        out["dns 100.100.100.200"] = await resolve_checked("https://evil.example/v1", resolver=resolver)
        out["6to4 default"] = await resolve_checked("https://six.example/", resolver=resolver)
        out["6to4 allowlisted"] = await resolve_checked(
            "https://six.example/", resolver=resolver, allow_hosts=frozenset({"six.example"}))
        out["teredo allowlisted"] = await resolve_checked(
            "https://teredo.example/", resolver=resolver, allow_hosts=frozenset({"teredo.example"}))
        return out

    out = asyncio.run(run())
    print("C5", out)
    assert out["literal 100.64.0.1"] == ("100.64.0.1",)
    assert out["dns 100.100.100.200"] == ("100.100.100.200",)  # Alibaba ECS metadata IP
    assert isinstance(out["6to4 default"], Refusal)
    assert out["6to4 allowlisted"] == ("2002:a9fe:a9fe::1",)  # embeds 169.254.169.254


def test_c5_pinned_transport_resolves_every_request():
    import httpx

    from gateway_v2.dispatch.pinned_transport import PinnedTransport

    calls: list[str] = []

    async def resolver(host, port):
        calls.append(host)
        return ("10.1.2.3",)

    class Inner(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request):
            return httpx.Response(200, content=b"ok")

    async def run():
        t = PinnedTransport(resolver, frozenset({"p.example"}), Inner())
        async with httpx.AsyncClient(transport=t) as c:
            for _ in range(5):
                await c.post("https://p.example/v1/chat/completions")

    asyncio.run(run())
    assert calls == ["p.example"] * 5


# ------------------------------------------------------------------ C6 (latent)
def test_c6_pinned_refused_escapes_provider_into_routing_internal_error():
    """Wires PinnedTransport under OpenAIUpstreamProvider (NOT done anywhere at develop)."""
    import httpx

    from gateway_v2.dispatch.pinned_transport import PinnedRefused, PinnedTransport
    from tests.dispatch import _gw28_fakes as f

    async def resolver(host, port):
        return ("127.0.0.1",)

    def factory():
        return httpx.AsyncClient(transport=PinnedTransport(resolver), trust_env=False)

    provider = OpenAIUpstreamProvider(
        base_url="https://p.example/v1", decoder_factory=SSEDecoder, client_factory=factory
    )

    async def direct():
        return [e async for e in provider.open(_req())]

    with pytest.raises(PinnedRefused):
        asyncio.run(direct())

    decisions = []
    rp = f.build_rp({"a": provider})  # type: ignore[dict-item]

    async def routed():
        return [e async for e in rp.open_routed(f.request(), f.ctx(sink=decisions.append))]

    events = asyncio.run(routed())
    assert events[-1].gateway_error == "routing_internal_error"


# ------------------------------------------------------------------ C7
def test_c7_refusal_decision_has_no_candidate():
    from tests.dispatch import _gw28_fakes as f

    tl = f.Timeline()
    decisions = []
    rp = f.build_rp({"a": f.FakeClient("a", [f.err("upstream_auth_error")], tl)})

    async def run():
        return [e async for e in rp.open_routed(f.request(), f.ctx(sink=decisions.append))]

    events = asyncio.run(run())
    assert events[-1].gateway_error == "upstream_auth_error"
    assert decisions[0].selected_candidate == "a"
    assert decisions[-1].refusal_code == "upstream_auth_error"
    assert decisions[-1].selected_candidate is None and decisions[-1].destination is None


# ------------------------------------------------------------------ C8 (+ lens 5 extras)
def test_c8_crlf_framing_yields_nothing_and_never_finishes():
    d = SSEDecoder()
    frames = d.feed(SSE_BODY.replace(b"\n", b"\r\n"))
    assert frames == () and not d.finished() and not d.malformed()
    lf = SSEDecoder()
    assert len(lf.feed(SSE_BODY)) == 2 and lf.finished()


def test_c8_comment_keepalive_sets_malformed():
    for comment in (b": ping\n\n", b": OPENROUTER PROCESSING\n\n", b":\n\n"):
        d = SSEDecoder()
        d.feed(comment)
        assert d.malformed(), comment


def test_lens5_bom_and_unknown_finish_reason_are_malformed():
    d = SSEDecoder()
    d.feed(b"\xef\xbb\xbf" + SSE_BODY)
    assert d.malformed()
    d2 = SSEDecoder()
    d2.feed(b'data: {"choices":[{"index":0,"delta":{},"finish_reason":"eos"}]}\n\n')
    assert d2.malformed()


def test_lens5_arbitrary_delta_keys_become_channels_unbounded():
    """Every string-valued delta key becomes a channel (and a per-channel scanner state
    downstream); empty values add 0 chars, so the output-length ceiling never counts them."""
    import json

    keys = {f"k{i}": "" for i in range(5000)}
    event = b"data: " + json.dumps({"choices": [{"index": 0, "delta": keys}]}).encode() + b"\n\n"
    d = SSEDecoder()
    frames = d.feed(event)
    assert not d.malformed()
    assert len(frames[0].text_deltas) == 5000
    assert sum(len(t) for _, t in frames[0].text_deltas) == 0


def test_lens5_reasoning_key_variants_pass_as_free_channels():
    d = SSEDecoder()
    frames = d.feed(
        b'data: {"choices":[{"index":0,"delta":{"reasoning":"r1","reasoning_content":"r2",'
        b'"refusal":"no"}}]}\n\n'
    )
    assert dict(frames[0].text_deltas) == {"reasoning": "r1", "reasoning_content": "r2", "refusal": "no"}


# ------------------------------------------------------------------ C13 (+ idle, first-event)
def test_c13_bounds_on_this_host():
    contract, _ = load_contract()
    vals = {
        "inter_chunk": contract.inter_chunk_timeout_s(),
        "gap": contract.upstream_gap_timeout_s(),
        "first_event": contract.upstream_first_event_timeout_s(),
        "idle": contract.idle_timeout_s(),
        "total": contract.upstream_total_timeout_s(),
        "edge_pre_content_budget": contract.routing_pre_content_budget_s(
            contract.routing_max_attempts()),
    }
    print("C13", vals)
    assert vals["inter_chunk"] == pytest.approx(1.0)
    assert vals["gap"] == pytest.approx(0.8)
    assert vals["idle"] == pytest.approx(3.0)
    assert vals["edge_pre_content_budget"] < 120.0  # < the plan's reasoning first-event class


def test_c13_edge_cuts_a_1_2s_pause_and_stall_guard_cuts_0_9s():
    from gateway_v2.dispatch.stall_guard import StallBounds, bounded_events
    from gateway_v2.edge.cancel import KillLatch
    from gateway_v2.edge.stream_timeouts import StreamTimeout, StreamTimeouts
    from tests.dispatch import _gw28_fakes as f

    contract, _ = load_contract()
    latch = KillLatch()
    timeouts = StreamTimeouts(contract=contract, kill_latch=latch, clock=time.monotonic)

    async def slow():
        await asyncio.sleep(1.2)
        return "late"

    async def edge():
        await timeouts.next_event(asyncio.sleep(0, "first"))
        await timeouts.next_event(slow())

    with pytest.raises(StreamTimeout) as exc:
        asyncio.run(edge())
    assert exc.value.code == "stream_inter_chunk_timeout"

    bounds = StallBounds(
        first_event_s=contract.upstream_first_event_timeout_s(),
        gap_s=contract.upstream_gap_timeout_s(),
        total_s=contract.upstream_total_timeout_s(),
        teardown_s=contract.cancellation_bound_s(),
    )
    tl = f.Timeline()
    client = f.FakeClient("a", [f.text("a"), 0.9, f.text("b", final=True)], tl)

    async def guarded():
        return [e async for e in bounded_events(client.open(f.request()), bounds)]

    events = asyncio.run(guarded())
    assert events[-1].gateway_error == "upstream_timeout"


def test_lens2_idle_bound_cuts_after_3s_without_a_write():
    """Not in the plan: after the inter-chunk cut is relaxed, the write-based idle bound (3.0 s)
    still cuts at the next write when the upstream paused > 3 s (default, non-liveness mode)."""
    from gateway_v2.edge.cancel import KillLatch
    from gateway_v2.edge.stream_timeouts import StreamTimeout, StreamTimeouts

    contract, _ = load_contract()
    now = [0.0]
    t = StreamTimeouts(contract=contract, kill_latch=KillLatch(), clock=lambda: now[0])

    async def run():
        async def ok():
            return None

        await t.send(ok)
        now[0] += 3.5  # upstream paused 3.5 s (a T-P1-style 5 s pause is longer)
        await t.send(ok)

    with pytest.raises(StreamTimeout) as exc:
        asyncio.run(run())
    assert exc.value.code == "stream_idle_timeout"


# ------------------------------------------------------------------ R17
def test_r17_org_credential_pickles_its_secret():
    import copy
    import dataclasses

    from gateway_v2.dispatch.vector_credentials import OrgCredential

    c = OrgCredential(org_id="o", verified=True, _secret="sk-plant-r17", ref_id="r")
    assert b"sk-plant-r17" in pickle.dumps(c)
    assert dataclasses.asdict(c)["_secret"] == "sk-plant-r17"
    # The bytes carry the secret even though a round trip fails (frozen + stripped setstate).
    with pytest.raises(dataclasses.FrozenInstanceError):
        pickle.loads(pickle.dumps(c))
    with pytest.raises(dataclasses.FrozenInstanceError):
        copy.copy(c)


# ------------------------------------------------------------------ C10 / C11
def test_c10_c11_forwarded_set():
    from gateway_v2.edge.wire.openai_params import forwarded_fields

    out = forwarded_fields(
        {
            "model": "m",
            "messages": [{"role": "developer", "content": "x"}],
            "stream": True,
            "stream_options": {"include_usage": True},
            "seed": 1,
            "logit_bias": {"1": 1},
            "reasoning_effort": "low",
            "n": 3,
        }
    )
    assert set(out) == {"seed", "logit_bias", "reasoning_effort"}


# ------------------------------------------------------------------ lens 5 extras (round 2)
def test_lens5_lone_surrogate_passes_decoder_then_breaks_utf8_encode():
    d = SSEDecoder()
    frames = d.feed(b'data: {"choices":[{"index":0,"delta":{"content":"\\ud83d"}}]}\n\n')
    assert not d.malformed()
    text = "".join(t for _, t in frames[0].text_deltas)
    with pytest.raises(UnicodeEncodeError):
        text.encode("utf-8")  # edge/asgi_sse.py:276 does exactly this for every event


def test_lens5_non_string_content_is_silently_dropped():
    """Mistral-style typed content arrays (or any non-string delta value) vanish without a
    malformed flag: the client would get an empty answer with finish_reason stop."""
    d = SSEDecoder()
    frames = d.feed(
        b'data: {"choices":[{"index":0,"delta":{"content":[{"type":"text","text":"hello"}]}}]}\n\n'
        b'data: {"choices":[{"index":0,"delta":{},"finish_reason":"stop"}]}\n\n'
        b"data: [DONE]\n\n"
    )
    assert not d.malformed() and d.finished()
    assert frames[0].text_deltas == ()
    assert frames[1].text_deltas == (("finish_reason", "stop"),)


def test_lens2_admission_concurrency_caps_streams_per_worker():
    """upstream_slots() = AdmissionBounds.concurrency = contract.queue_depth(q_safe) (plan 3.6).
    queue_depth is sized for ~p99-length requests and charges per_worker_rss per slot."""
    contract, _ = load_contract()
    depths = {q: contract.queue_depth(q) for q in (200, 1000, 5000, 50000)}
    print("admission concurrency by q_safe on this host:", depths)
    assert max(depths.values()) <= 64


def test_plan_standard_class_bounds_violate_stallbounds_invariant():
    """Plan 3.6: standard class first event 30 s and gap 30 s. StallBounds demands gap < first."""
    from gateway_v2.dispatch.routing_types import RoutingConfigError
    from gateway_v2.dispatch.stall_guard import StallBounds

    with pytest.raises(RoutingConfigError):
        StallBounds(first_event_s=30.0, gap_s=30.0, total_s=299.94, teardown_s=0.06)
    StallBounds(first_event_s=120.0, gap_s=60.0, total_s=299.94, teardown_s=0.06)  # reasoning ok
