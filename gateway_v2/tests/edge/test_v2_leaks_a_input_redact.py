"""v2-enforcement-leaks Group A (chat): input REDACT must be applied before the provider.

Bug-condition exploration test (spec ``v2-enforcement-leaks``, task 2; design Property 1 (A) and
Property 3). Under the dev plan (``dev/plan.example.json``: pii → redact, secret/credential →
block, every rule ``scope: both``) an SSN in any forwarded input channel resolves to an input
REDACT decision with no BLOCK, so the request is admitted and dispatched. The PROVIDER must then
receive only the verified, transformed bytes:

* the raw value is absent from every recorded provider delta, and from the concatenation of
  the deltas (a value split across two ``content_part_text`` parts lives in no single delta);
* ``[REDACTED]`` is present;
* each delta holds exactly its own sanitized bytes, in order, with its channel name unchanged
  (a value straddling a part boundary places its replacement in the delta where it starts and
  the later delta loses the covered prefix -- design, Group A ``_resplit``).

Streaming and non-streaming share ``ChatSurface._proceed``, so every placement is driven in both
modes. The recording provider keeps the exact ``(channel, text)`` tuple the gateway forwarded
(``Transform.to_provider`` packs it under ``BODY_DELTAS_KEY``), rather than a joined string.

On the unfixed tree ``ChatSurface._input_scan`` refuses only on BLOCK and ``_proceed`` packs the
raw ``req.text_deltas``, so every placement leaks; the leaking cases carry a strict xfail naming
the observed counterexample and flip when Group A lands.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Mapping
from pathlib import Path

import pytest

from gateway_v2.dispatch.provider import UpstreamEvent, UpstreamRequest
from gateway_v2.dispatch.transform import BODY_DELTAS_KEY
from gateway_v2.edge import serve
from gateway_v2.runtime.kinds import HardwareSignals
from gateway_v2.runtime.resources import ResourceContract, from_signals

# Same dev plan, key, value and contract as ``test_dev_profile_pipeline.py`` (kept local so this
# file type-checks on its own under ``mypy --strict``).
EXAMPLE_PLAN = Path(__file__).resolve().parents[2] / "dev" / "plan.example.json"
SSN = "123-45-6789"
KEY = "sk-dev-pipeline"
REDACTED = "[REDACTED]"
#: Every contiguous fragment of the planted value long enough to identify it. Checked against
#: the joined deltas, so a value split across parts cannot survive as two raw halves.
RAW_FRAGMENTS = (SSN, SSN[:6], SSN[6:], SSN[-4:])

Delta = tuple[str, str]


def _contract() -> ResourceContract:
    signals = HardwareSignals(
        cpu_quota=4.0,
        memory_limit=400 * 1024 * 1024 * 8,
        fd_limit=4096,
        cpu_source="test",
        mem_source="test",
        fd_source="test",
    )
    return from_signals(
        signals, target_p99_ms=20.0, utilization_cap=0.75, per_worker_rss=400 * 1024 * 1024
    )


class _RecordingProvider:
    """Records the exact ``(channel, text)`` deltas of every forwarded body, then echoes."""

    def __init__(self) -> None:
        self.received: list[tuple[Delta, ...]] = []

    def open(self, request: UpstreamRequest) -> AsyncIterator[UpstreamEvent]:
        return self._stream(request)

    async def _stream(self, request: UpstreamRequest) -> AsyncIterator[UpstreamEvent]:
        raw = request.body.get(BODY_DELTAS_KEY, ())
        assert isinstance(raw, tuple)
        deltas = tuple((str(item[0]), str(item[1])) for item in raw)
        self.received.append(deltas)
        text = "\n".join(t for _c, t in deltas)
        yield UpstreamEvent(text_deltas=(("content", "echo: " + text),), final=True)

    async def abort(self) -> None:
        return None


def _app(provider: _RecordingProvider) -> object:
    env = {
        "AMF_DEV_PROFILE": "1",
        "AMF_DEV_API_KEY": KEY,
        "AMF_DEV_PLAN_PATH": str(EXAMPLE_PLAN),
        "AMF_DEV_MODELS": "echo-1",
        "AMF_DEV_Q_SAFE": "500",
        "AMF_DEV_AUDIT_DRAIN_RATE_PER_S": "1000",
        "AMF_DEV_AUDIT_BYTES_PER_RECORD": "2765",
        "AMF_DEV_BUDGET": "unlimited",
    }
    deps, _logs = serve.build_boot_dependencies(contract=_contract(), env=env)
    deps["provider"] = provider
    return serve._assemble_app(deps)


async def _post(app: object, messages: list[dict[str, object]], *, stream: bool) -> int:
    body = json.dumps({"model": "echo-1", "stream": stream, "messages": messages}).encode()
    scope = {
        "type": "http",
        "method": "POST",
        "path": "/v1/chat/completions",
        "headers": [(b"authorization", f"Bearer {KEY}".encode()), (b"content-type", b"json")],
    }
    sent: list[Mapping[str, object]] = []
    delivered = False

    async def _receive() -> Mapping[str, object]:
        nonlocal delivered
        if delivered:
            await asyncio.sleep(3600)
        delivered = True
        return {"type": "http.request", "body": body, "more_body": False}

    async def _send(message: Mapping[str, object]) -> None:
        sent.append(message)

    assert callable(app)
    await app(scope, _receive, _send)
    return next(int(str(m["status"])) for m in sent if m["type"] == "http.response.start")


# Each placement: the request ``messages`` and the EXACT provider deltas Property 3 requires
# (``apply_and_verify(decision, request).request.text_deltas``): masked where the value sits,
# byte-identical everywhere else, one delta per folded input channel, in order.
_PLACEMENTS: dict[str, tuple[list[dict[str, object]], tuple[Delta, ...]]] = {
    "user_message": (
        [{"role": "user", "content": f"My SSN is {SSN} thanks"}],
        (("message_content", f"My SSN is {REDACTED} thanks"),),
    ),
    "system_message": (
        [
            {"role": "system", "content": f"Customer SSN {SSN} is on file"},
            {"role": "user", "content": "summarise the account"},
        ],
        (
            ("message_content", f"Customer SSN {REDACTED} is on file"),
            ("message_content", "summarise the account"),
        ),
    ),
    "later_message": (
        [
            {"role": "user", "content": "hello there"},
            {"role": "assistant", "content": "hi, how can I help"},
            {"role": "user", "content": f"my SSN is {SSN}"},
        ],
        (
            ("message_content", "hello there"),
            ("message_content", "hi, how can I help"),
            ("message_content", f"my SSN is {REDACTED}"),
        ),
    ),
    "split_across_parts": (
        [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": f"My SSN is {SSN[:6]}"},
                    {"type": "text", "text": f"{SSN[6:]} thanks"},
                ],
            }
        ],
        (
            ("content_part_text", f"My SSN is {REDACTED}"),
            ("content_part_text", " thanks"),
        ),
    ),
}

# Every placement -- the single-channel cases and ``split_across_parts`` -- is now masked
# before the provider and passes plainly. ``split_across_parts`` closed when the join-run scan
# separator was made EMPTY (``input_enforcement._JOIN_SEPARATOR``): ``scan_input`` derives the
# message-part join run (``input_message_index == [0, 0]`` -> ``join_runs == ((0, 1),)``) and now
# scans the SEPARATOR-FREE concatenation "My SSN is 123-45-6789 thanks", byte-identical to the
# request-content coordinate space (``dispatch/verify._request_content`` / ``_base_offsets``), so
# the straddling SSN is matched as the contiguous bytes it is on the wire, its span aligns with
# ``apply_and_verify``'s masking, and the replacement lands in part0 where the SSN starts while
# part1 loses the covered "-6789" prefix and keeps " thanks" (the ``_resplit`` behaviour). No
# strict xfail remains.


def _cases() -> list[object]:
    cases: list[object] = []
    for name in _PLACEMENTS:
        for stream in (False, True):
            mode = "stream" if stream else "json"
            cases.append(pytest.param(name, stream, id=f"{name}-{mode}"))
    return cases


@pytest.mark.parametrize(("placement", "stream"), _cases())
def test_input_redact_masks_every_channel_before_the_provider(placement: str, stream: bool) -> None:
    """Property 3: the provider receives only the verified, transformed deltas.

    **Validates: Requirements 2.1, 2.3**
    """
    messages, expected = _PLACEMENTS[placement]
    provider = _RecordingProvider()
    status = asyncio.run(_post(_app(provider), messages, stream=stream))
    assert status == 200, f"{placement}: an input REDACT (no BLOCK) must proceed, got {status}"
    assert len(provider.received) == 1, f"{placement}: exactly one provider call"
    deltas = provider.received[0]
    for channel, text in deltas:
        assert SSN not in text, f"{placement}: raw SSN in provider delta {(channel, text)!r}"
    joined = "".join(text for _channel, text in deltas)
    for fragment in RAW_FRAGMENTS:
        assert fragment not in joined, (
            f"{placement}: raw fragment {fragment!r} reached the provider: {deltas!r}"
        )
    assert REDACTED in joined, f"{placement}: no {REDACTED} in provider deltas {deltas!r}"
    assert deltas == expected, (
        f"{placement}: each delta must hold exactly its own sanitized bytes: "
        f"got {deltas!r}, expected {expected!r}"
    )
