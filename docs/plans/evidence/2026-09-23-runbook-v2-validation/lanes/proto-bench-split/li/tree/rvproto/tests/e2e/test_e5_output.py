"""E5: provider-injected email and split-aws never reach the client raw (org-a REDACT);
the SSE stays valid for the official SDK; org-b (MONITOR) is the pass-through control."""

from __future__ import annotations

import httpx
import openai
import pytest

from tests.e2e.conftest import KEY_A, KEY_B, MODEL, OUT_AWS, OUT_EMAIL, rid

EMAIL = OUT_EMAIL
AWS = OUT_AWS
MSG = [{"role": "user", "content": "hello"}]


def _client(base: str, key: str) -> openai.OpenAI:
    return openai.OpenAI(base_url=f"{base}/v1", api_key=key, max_retries=0)


@pytest.mark.parametrize("inject", ["email", "split-aws", "aws"])
@pytest.mark.parametrize("stream", [True, False])
def test_output_redacted_for_enforce_tenant(base: str, inject: str, stream: bool) -> None:
    r = rid(f"out-{inject}")
    hdr = {"x-request-id": r, "x-synth-inject": inject, "x-synth-tokens": "12"}
    c = _client(base, KEY_A)
    if stream:
        chunks = list(c.chat.completions.create(model=MODEL, messages=MSG, stream=True, extra_headers=hdr))
        text = "".join(ch.choices[0].delta.content or "" for ch in chunks if ch.choices)
        assert [ch.choices[0].finish_reason for ch in chunks if ch.choices][-1] == "stop"
    else:
        text = c.chat.completions.create(model=MODEL, messages=MSG, extra_headers=hdr).choices[0].message.content
    assert EMAIL not in text and AWS not in text and "AKIA" not in text
    assert ("[REDACTED:pii.email]" if inject == "email" else "[REDACTED:secret.aws]") in text


@pytest.mark.parametrize("inject", ["email", "split-aws"])
def test_output_monitor_tenant_passes_through(base: str, inject: str) -> None:
    r = rid(f"outmon-{inject}")
    hdr = {"x-request-id": r, "x-synth-inject": inject, "x-synth-tokens": "12"}
    chunks = list(_client(base, KEY_B).chat.completions.create(model=MODEL, messages=MSG, stream=True,
                                                               extra_headers=hdr))
    text = "".join(ch.choices[0].delta.content or "" for ch in chunks if ch.choices)
    assert (EMAIL if inject == "email" else AWS) in text


def test_raw_sse_frames_are_wellformed(base: str) -> None:
    import json

    r = rid("raw-sse")
    with httpx.stream("POST", f"{base}/v1/chat/completions", timeout=60,
                      headers={"authorization": f"Bearer {KEY_A}", "x-request-id": r,
                               "x-synth-inject": "split-aws", "x-synth-tokens": "10"},
                      json={"model": MODEL, "messages": MSG, "stream": True,
                            "stream_options": {"include_usage": True}}) as resp:
        assert resp.headers["content-type"].startswith("text/event-stream")
        body = b"".join(resp.iter_bytes())
    events = [e for e in body.split(b"\n\n") if e]
    assert events[-1] == b"data: [DONE]"
    ids = set()
    for e in events[:-1]:
        assert e.startswith(b"data: ")
        d = json.loads(e[6:])
        ids.add(d["id"])
        assert d["object"] == "chat.completion.chunk"
    assert len(ids) == 1
    assert AWS[:8].encode() not in body and AWS[8:16].encode() not in body
