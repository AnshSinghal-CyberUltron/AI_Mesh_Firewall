"""GW36 S3 end to end: signed state written by `state_control`, read by the gateway's runtime.

The KEY / PLAN records are written by the real `StateWriter` and stamped by the real
`Rehydrator` into fakeredis; the gateway side is the real `StateRuntime` over the same server,
and requests go through the app the boot table assembles. So "a console key resolves" here means
the same bytes S2 publishes were verified, applied and served.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import secrets

import fakeredis
import pytest

from gateway_v2.domain.plan import StreamingMode
from gateway_v2.domain.state import StateKind, StoreDataUnavailable
from gateway_v2.domain.state_knobs import StateKnobs
from gateway_v2.edge import serve
from gateway_v2.edge.identity_resolver import hashed_bearer_key
from gateway_v2.edge.state_runtime import StateRuntime, StateRuntimeConfig, StateServedApp
from gateway_v2.plan.document import PlanDocument, encode_plan_body
from gateway_v2.runtime.store_keys import StoreKeys
from state_control.db import MemoryControlDB
from state_control.rehydrate import Rehydrator
from state_control.valkey import ValkeyPublisher
from state_control.writer import StateWriter
from tests.edge.test_dev_profile_boot import _contract
from tests.plan.test_lgw05c_delta import _draft

SECRET = b"gw36-s3-secret"
NAMESPACE = "{rv2}"
CHAT = "/v1/chat/completions"
_BODY = {"model": "m", "messages": [{"role": "user", "content": "hello"}]}
_IDENTITY = {"invalid_api_key", "shared_state_unavailable"}


def _console_key() -> str:
    return secrets.token_urlsafe(48)[:48]


def _sha(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


class Lab:
    def __init__(self) -> None:
        self.server = fakeredis.FakeServer()
        sync = fakeredis.FakeRedis(server=self.server)
        self.publisher = ValkeyPublisher(sync, SECRET, StoreKeys(NAMESPACE))
        self.db = MemoryControlDB()
        self.writer = StateWriter(self.db, self.publisher, SECRET)
        self.rehydrator = Rehydrator(
            self.db, self.publisher, self.writer, SECRET, stale_grace_s=0.0,
        )
        self.runtime = StateRuntime(
            StateRuntimeConfig("redis://unused", SECRET, NAMESPACE, StateKnobs()),
            client=fakeredis.FakeAsyncRedis(server=self.server),
        )
        deps, _logs = serve.build_boot_dependencies(contract=_contract(), env={})
        deps.update(
            identity=self.runtime.identity_resolver(),
            snapshot_source=self.runtime.snapshot_source,
            readiness=self.runtime.ready,
        )
        self.app = serve._assemble_app(deps)

    def key(self, raw: str, org: str = "acme") -> None:
        self.writer.key_add(_sha(raw), org, key_id=f"id-{raw[:4]}", rate_per_s=0.0, burst=0.0)

    def plan(self, org: str = "acme") -> None:
        body = encode_plan_body(PlanDocument(org, StreamingMode.INCREMENTAL, (_draft(),)))
        self.writer.plan_set(org, body)

    async def settle(self) -> None:
        """One re-hydrator round (manifests + stamp), then one gateway state cycle."""
        self.rehydrator.round_once()
        await self.runtime.cycle()


async def _call(
    app: object,
    method: str,
    path: str,
    *,
    body: object = None,
    auth: str | None = None,
) -> tuple[int, dict[str, str], bytes]:
    raw = b"" if body is None else json.dumps(body).encode()
    headers = [(b"content-type", b"application/json")]
    if auth is not None:
        headers.append((b"authorization", auth.encode("latin-1")))
    scope = {
        "type": "http", "method": method, "path": path, "raw_path": path.encode(),
        "query_string": b"", "http_version": "1.1", "scheme": "http", "root_path": "",
        "server": ("test", 80), "client": ("test", 1), "headers": headers,
    }
    sent = False
    start: dict[str, object] = {}
    chunks: list[bytes] = []

    async def receive() -> dict[str, object]:
        nonlocal sent
        if sent:
            await asyncio.sleep(3600)
        sent = True
        return {"type": "http.request", "body": raw, "more_body": False}

    async def send(message: dict[str, object]) -> None:
        if message["type"] == "http.response.start":
            start.update(message)
        elif message["type"] == "http.response.body":
            chunks.append(bytes(message.get("body", b"")))  # type: ignore[arg-type]

    await app(scope, receive, send)  # type: ignore[operator]
    names = {
        bytes(k).decode(): bytes(v).decode()
        for k, v in start.get("headers", [])  # type: ignore[attr-defined]
    }
    return int(start["status"]), names, b"".join(chunks)  # type: ignore[call-overload]


def _error(body: bytes) -> dict[str, object]:
    error = json.loads(body)["error"]
    assert isinstance(error, dict)
    return error


async def _ready_lab() -> tuple[Lab, str]:
    lab = Lab()
    raw = _console_key()
    lab.plan()
    lab.key(raw)
    await lab.settle()
    assert lab.runtime.ready() == (True, None), lab.runtime.ready()
    return lab, raw


# --- E1: a console key resolves ------------------------------------------------------------


def test_a_console_key_passes_identity_and_plan() -> None:
    async def go() -> None:
        lab, raw = await _ready_lab()
        auth = f"Bearer {raw}"
        status, _headers, body = await _call(lab.app, "POST", CHAT, body=_BODY, auth=auth)
        code = _error(body)["code"]
        assert code not in _IDENTITY | {"plan_unavailable"}, (status, code)
        models, _h, _b = await _call(lab.app, "GET", "/v1/models", auth=f"bearer {raw}")
        assert models == 200, "Bearer is case-insensitive and the key resolves"

    asyncio.run(go())


# --- E2: anything that is not a live key is a 401, streaming or not ---------------------------


@pytest.mark.parametrize(
    "auth",
    [None, "Bearer", "Basic abc", "Bearer not-a-console-key", "Bearer " + "A" * 48],
)
@pytest.mark.parametrize("stream", [False, True])
def test_a_missing_or_unknown_key_is_401_plain_http(auth: str | None, stream: bool) -> None:
    async def go() -> None:
        lab, _raw = await _ready_lab()
        status, headers, body = await _call(
            lab.app, "POST", CHAT, body={**_BODY, "stream": stream}, auth=auth,
        )
        assert status == 401, (auth, stream, status, body)
        assert headers["content-type"] == "application/json", "never an SSE 200 for auth"
        assert headers["www-authenticate"] == "Bearer"
        error = _error(body)
        assert (error["code"], error["type"]) == ("invalid_api_key", "invalid_request_error")
        assert str(error["request_id"]).startswith("zs-")
        assert headers["x-request-id"] == error["request_id"]

    asyncio.run(go())


def test_every_surface_answers_401_for_a_bad_key() -> None:
    async def go() -> None:
        lab, _raw = await _ready_lab()
        bad = "Bearer " + _console_key()
        cases = [
            ("GET", "/v1/models", None),
            ("POST", "/v1/embeddings", {"model": "m", "input": "x"}),
            ("POST", "/v1/moderations", {"input": "x"}),
            ("GET", "/v1/responses/resp_x", None),
        ]
        for method, path, body in cases:
            status, headers, raw = await _call(lab.app, method, path, body=body, auth=bad)
            assert status == 401, (path, status, raw)
            assert _error(raw)["code"] == "invalid_api_key", path
            assert headers["x-request-id"].startswith("zs-"), path

    asyncio.run(go())


def test_a_junk_token_is_never_looked_up() -> None:
    """Only the console key shape is hashed and fetched (R12)."""

    def scope(value: str) -> dict[str, object]:
        return {"headers": [(b"authorization", value.encode("latin-1"))]}

    raw = _console_key()
    assert hashed_bearer_key(scope(f"Bearer {raw}")) == _sha(raw)
    assert hashed_bearer_key(scope(f"BEARER {raw}")) == _sha(raw)
    for junk in ("Bearer short", "Bearer " + "x" * 47, "Bearer " + "x" * 49, f"Bearer {raw[:47]}!"):
        assert hashed_bearer_key(scope(junk)) is None, junk


# --- E3: revocation ---------------------------------------------------------------------------


def test_a_revoked_key_is_401_after_its_delta_is_applied() -> None:
    async def go() -> None:
        lab, raw = await _ready_lab()
        assert (await _call(lab.app, "GET", "/v1/models", auth=f"Bearer {raw}"))[0] == 200
        lab.writer.key_revoke(_sha(raw))
        await lab.settle()
        status, _h, body = await _call(lab.app, "GET", "/v1/models", auth=f"Bearer {raw}")
        assert (status, _error(body)["code"]) == (401, "invalid_api_key")

    asyncio.run(go())


# --- E4 / E6: doubt is a 503, never a 401 ------------------------------------------------------


def test_unverified_state_is_503_not_401() -> None:
    async def go() -> None:
        lab = Lab()
        raw = _console_key()
        lab.plan()
        lab.key(raw)
        await lab.runtime.cycle()  # no re-hydrator round: no stamp
        for auth in (f"Bearer {raw}", "Bearer " + _console_key()):
            status, _h, body = await _call(lab.app, "GET", "/v1/models", auth=auth)
            assert (status, _error(body)["code"]) == (503, "shared_state_unavailable"), auth
        ready, reason = lab.runtime.ready()
        assert not ready and reason

    asyncio.run(go())


def test_a_store_outage_is_503_and_the_state_task_survives() -> None:
    async def go() -> None:
        lab, raw = await _ready_lab()
        fresh = "Bearer " + _console_key()  # never cached, so it must go to the store
        lab.server.connected = False
        status, _h, body = await _call(lab.app, "GET", "/v1/models", auth=fresh)
        assert (status, _error(body)["code"]) == (503, "shared_state_unavailable")
        reports = await lab.runtime.cycle()  # must report, not raise
        assert reports and not any(report.ok for report in reports)
        lab.server.connected = True
        await lab.settle()
        assert (await _call(lab.app, "GET", "/v1/models", auth=f"Bearer {raw}"))[0] == 200
        assert (await _call(lab.app, "GET", "/v1/models", auth=fresh))[0] == 401

    asyncio.run(go())


def test_a_store_that_regressed_below_the_applied_cursor_is_refused() -> None:
    """R7: a fill from a lagging replica must not resurrect a key this worker saw revoked."""

    async def go() -> None:
        lab, raw = await _ready_lab()
        applied = lab.runtime.sync.cursor(StateKind.KEY)
        lab.runtime.sync._cursors[StateKind.KEY] = type(applied)(  # noqa: SLF001
            version=applied.version, feed_seq=applied.feed_seq + 5,
        )
        with pytest.raises(StoreDataUnavailable):
            await lab.runtime.fetch_key(_sha(raw))

    asyncio.run(go())


# --- E7 and health ----------------------------------------------------------------------------


def test_livez_is_always_up_and_readyz_waits_for_state() -> None:
    async def go() -> None:
        lab = Lab()
        assert (await _call(lab.app, "GET", "/livez"))[0] == 200
        status, _h, body = await _call(lab.app, "GET", "/readyz")
        assert status == 503 and body
        lab.plan()
        await lab.settle()
        assert (await _call(lab.app, "GET", "/readyz"))[0] == 200

    asyncio.run(go())


def test_the_lifespan_starts_and_stops_the_state_task() -> None:
    async def go() -> None:
        lab = Lab()
        lab.plan()
        lab.rehydrator.round_once()
        served = StateServedApp(lab.app, lab.runtime)
        messages = iter([{"type": "lifespan.startup"}, {"type": "lifespan.shutdown"}])
        events: list[str] = []

        async def receive() -> dict[str, object]:
            return next(messages)

        async def send(message: dict[str, object]) -> None:
            events.append(str(message["type"]))
            if message["type"] == "lifespan.startup.complete":
                assert lab.runtime.ready() == (True, None), "startup waits for the first cycle"
                assert lab.runtime.cycles >= 1

        await served({"type": "lifespan"}, receive, send)  # type: ignore[arg-type]
        assert events == ["lifespan.startup.complete", "lifespan.shutdown.complete"]

    asyncio.run(go())
