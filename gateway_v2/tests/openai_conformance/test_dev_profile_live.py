"""DEV PROFILE end-to-end over a REAL socket with the stock OpenAI SDK.

Boots ``python -m gateway_v2.edge.serve`` (the item-1 entry fix) as a child process with
``AMF_DEV_PROFILE=1`` + ``gateway_v2/dev/plan.example.json`` and drives the real pipeline:
identity → plan → admission → budget → GW09 input scan → GW07 resolver → echo provider → output
guard. Asserts allow / redact / block / auth refusal / streaming / models.

A second fixture boots the ``AMF_DEV_BUDGET=lease`` mode against an in-process fakeredis TCP
server (real RESP over a real socket) and asserts the GW06 lease refuses once its pool is spent.

Skips cleanly without the ``serve`` + ``conformance`` extras.
"""

from __future__ import annotations

import contextlib
import os
import signal
import socket
import subprocess
import sys
import threading
import time
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import pytest

uvicorn = pytest.importorskip("uvicorn")
openai = pytest.importorskip("openai")
httpx = pytest.importorskip("httpx")

GW_ROOT = Path(__file__).resolve().parents[2]
EXAMPLE_PLAN = GW_ROOT / "dev" / "plan.example.json"
API_KEY = "sk-dev-e2e-key"
MODEL = "echo-1"
SSN = "123-45-6789"
AWS_KEY = "AKIAIOSFODNN7EXAMPLE"
_READY_TIMEOUT_S = 30.0
_SHUTDOWN_S = 20.0


def _free_port() -> int:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])
    finally:
        sock.close()


@dataclass(frozen=True, slots=True)
class _Live:
    base_url: str
    process: subprocess.Popen[str]


def _env(port: int, **extra: str) -> dict[str, str]:
    env = {key: value for key, value in os.environ.items() if not key.startswith("AMF_DEV_")}
    env.update(
        {
            "RV_HOST": "127.0.0.1",
            "RV_PORT": str(port),
            "WEB_CONCURRENCY": "1",
            "AMF_TARGET_P99_MS": "250",
            "AMF_UTILIZATION_CAP": "0.8",
            "AMF_PER_WORKER_RSS_MB": "512",
            "PYTHONUNBUFFERED": "1",
            "AMF_DEV_PROFILE": "1",
            "AMF_DEV_API_KEY": API_KEY,
            "AMF_DEV_ORG_ID": "dev-org",
            "AMF_DEV_PLAN_PATH": str(EXAMPLE_PLAN),
            "AMF_DEV_MODELS": f"{MODEL},gpt-4o-mini",
            "AMF_DEV_Q_SAFE": "200",
            "AMF_DEV_AUDIT_DRAIN_RATE_PER_S": "1000",
            "AMF_DEV_AUDIT_BYTES_PER_RECORD": "2765",
            "AMF_DEV_BUDGET": "unlimited",
        }
    )
    env.update(extra)
    return env


def _terminate(proc: subprocess.Popen[str]) -> str:
    if proc.poll() is None:
        proc.send_signal(signal.SIGTERM)
        try:
            proc.wait(timeout=_SHUTDOWN_S)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5.0)
    out = ""
    if proc.stdout is not None:
        with contextlib.suppress(Exception):
            out = proc.stdout.read()
    return out


def _boot(env: dict[str, str], port: int) -> _Live:
    proc = subprocess.Popen(
        [sys.executable, "-m", "gateway_v2.edge.serve"],
        env=env,
        cwd=str(GW_ROOT),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    base_url = f"http://127.0.0.1:{port}"
    deadline = time.monotonic() + _READY_TIMEOUT_S
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            break
        try:
            if httpx.get(f"{base_url}/readyz", timeout=1.0).status_code == 200:
                return _Live(base_url=base_url, process=proc)
        except httpx.HTTPError:
            pass
        time.sleep(0.2)
    output = _terminate(proc)
    pytest.fail(f"dev-profile server did not become ready (exit={proc.returncode}):\n{output}")


@pytest.fixture(scope="module")
def live() -> Iterator[_Live]:
    port = _free_port()
    server = _boot(_env(port), port)
    try:
        yield server
    finally:
        _terminate(server.process)


def _client(base_url: str, api_key: str = API_KEY) -> openai.OpenAI:
    return openai.OpenAI(base_url=f"{base_url}/v1", api_key=api_key, max_retries=0)


def _ask(base_url: str, content: str) -> object:
    return _client(base_url).chat.completions.create(
        model=MODEL, messages=[{"role": "user", "content": content}]
    )


def _ask_raw(base_url: str, content: str, *, stream: bool = False) -> httpx.Response:
    return httpx.post(
        f"{base_url}/v1/chat/completions",
        headers={"Authorization": f"Bearer {API_KEY}"},
        json={"model": MODEL, "stream": stream, "messages": [{"role": "user", "content": content}]},
        timeout=10.0,
    )


# (a) clean prompt → 200 echo
def test_clean_prompt_is_echoed(live: _Live) -> None:
    completion = _ask(live.base_url, "hello from the stock SDK")
    assert completion.choices[0].message.content == "echo: hello from the stock SDK"


# (b) SSN → PII REDACT → 200, masked, raw bytes absent
def test_ssn_is_redacted_and_raw_ssn_absent_from_response_bytes(live: _Live) -> None:
    completion = _ask(live.base_url, f"My SSN is {SSN} thanks")
    assert completion.choices[0].message.content == "echo: My SSN is [REDACTED] thanks"
    raw = _ask_raw(live.base_url, f"My SSN is {SSN} thanks")
    assert raw.status_code == 200 and SSN.encode() not in raw.content


# (c) AWS key → SECRET BLOCK → 400 input_blocked
def test_aws_key_is_blocked_with_input_blocked(live: _Live) -> None:
    with pytest.raises(openai.APIStatusError) as caught:
        _ask(live.base_url, f"use key {AWS_KEY} please")
    assert caught.value.status_code == 400
    assert isinstance(caught.value.body, dict) and caught.value.body.get("code") == "input_blocked"
    assert AWS_KEY not in caught.value.response.text


# (d) wrong key → declared identity refusal
@pytest.mark.parametrize("api_key", ["sk-wrong", API_KEY + "x"])
def test_wrong_api_key_is_refused(live: _Live, api_key: str) -> None:
    with pytest.raises(openai.APIStatusError) as caught:
        _client(live.base_url, api_key).chat.completions.create(
            model=MODEL, messages=[{"role": "user", "content": "hi"}]
        )
    assert caught.value.status_code == 503
    assert isinstance(caught.value.body, dict)
    assert caught.value.body.get("code") == "shared_state_unavailable"


# (e) streaming variants of (a) and (b)
def _stream_text(base_url: str, content: str) -> str:
    stream = _client(base_url).chat.completions.create(
        model=MODEL, messages=[{"role": "user", "content": content}], stream=True
    )
    return "".join(chunk.choices[0].delta.content or "" for chunk in stream if chunk.choices)


def test_streaming_clean_prompt_is_echoed(live: _Live) -> None:
    assert _stream_text(live.base_url, "stream me please") == "echo: stream me please"


def test_streaming_ssn_is_redacted(live: _Live) -> None:
    text = _stream_text(live.base_url, f"My SSN is {SSN} thanks")
    assert text == "echo: My SSN is [REDACTED] thanks"
    raw = _ask_raw(live.base_url, f"My SSN is {SSN} thanks", stream=True)
    assert raw.status_code == 200 and SSN.encode() not in raw.content


# (f) /v1/models lists the catalog
def test_models_lists_the_catalog(live: _Live) -> None:
    client = _client(live.base_url)
    assert sorted(model.id for model in client.models.list()) == sorted([MODEL, "gpt-4o-mini"])
    assert client.models.retrieve(MODEL).id == MODEL


# --- AMF_DEV_BUDGET=lease over a real RESP socket -------------------------------------------- #


@pytest.fixture(scope="module")
def lease_live() -> Iterator[_Live]:
    fakeredis = pytest.importorskip("fakeredis")
    redis_port = _free_port()
    store = fakeredis.TcpFakeServer(("127.0.0.1", redis_port))
    thread = threading.Thread(target=store.serve_forever, daemon=True)
    thread.start()
    port = _free_port()
    env = _env(
        port,
        AMF_DEV_BUDGET="lease",
        AMF_DEV_REDIS_URL=f"redis://127.0.0.1:{redis_port}/0",
        AMF_DEV_BUDGET_POOL_REQUESTS="3",
        AMF_DEV_RATE_PER_S="1000",
        AMF_DEV_BURST="100",
        AMF_DEV_LEASE_TTL_S="60",
    )
    server = _boot(env, port)
    try:
        yield server
    finally:
        _terminate(server.process)
        store.shutdown()
        store.server_close()


def test_lease_budget_refuses_once_the_pool_is_spent(lease_live: _Live) -> None:
    for index in range(3):
        assert _ask(lease_live.base_url, f"request {index}").choices[0].message.content
    with pytest.raises(openai.APIStatusError) as caught:
        _ask(lease_live.base_url, "one too many")
    assert caught.value.status_code == 503
    assert isinstance(caught.value.body, dict)
    assert caught.value.body.get("code") == "budget_unavailable"
