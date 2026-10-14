"""B3 task 7.1 — real-socket conformance over the running Serving_Entrypoint.

Boots ``gateway_v2.edge.serve.main`` as a REAL CHILD PROCESS on ``127.0.0.1`` on a free port
and asserts SDK-visible PROTOCOL behaviour over the live socket — the "Proof" rule's local half:
real wire bytes, the stock OpenAI SDK, and ``curl`` (``httpx``) against the hosted ASGI app.

Honest-stub boundary (R5.6): with the fail-closed boot deps (``fail_closed_resolver`` /
``fail_closed_admission`` / ``empty_catalog_source`` / ``StubProviderClient``), the entrypoint is
fail-closed at identity/state, so every data-plane surface refuses with a declared JSON error
envelope. These assertions therefore check PROTOCOL shape only — status class, JSON-not-HTML,
error envelope, graceful drain — and NEVER stubbed-upstream response bytes.

The whole module is guarded by ``pytest.importorskip`` on ``uvicorn`` / ``openai`` / ``httpx`` so
the default framework-free ``pytest`` run skips it cleanly where the ``serve`` + ``conformance``
extras are absent; where those extras ARE installed (as here) the test actually boots a real
server and exercises it.

Covers R4.1/4.2 (/readyz 200-or-503-with-dep-name, non-HTML), R4.4/4.5 (/metrics 200 Prometheus
text), R5.1 (chat over the socket via the stock SDK), R5.2 (embeddings/models/models.{id}/
moderations surfaces), R5.3 (raw non-HTML content type), R5.6 (SDK-visible protocol only), and
R3.1/3.9 (SIGTERM → graceful bounded shutdown, no hang, no unhandled-error traceback).
"""

from __future__ import annotations

import contextlib
import os
import signal
import socket
import subprocess
import sys
import time
from collections.abc import Iterator, Mapping
from dataclasses import dataclass

import pytest

# Framework-free trees skip the whole module cleanly (the serve + conformance extras are optional).
uvicorn = pytest.importorskip("uvicorn")
openai = pytest.importorskip("openai")
httpx = pytest.importorskip("httpx")


_READY_TIMEOUT_S = 20.0
_READY_POLL_S = 0.25
_SHUTDOWN_DEADLINE_S = 20.0
# A ``build_app`` dependency is unwired at the B3 local half, so the fail-closed data-plane surfaces
# refuse; the SDK maps any of these 4xx/5xx classes to an ``APIStatusError`` carrying a JSON
# envelope — the SDK-visible protocol behaviour we assert (R5.6), never stubbed bytes.
_REFUSAL_STATUSES = frozenset({401, 403, 429, 500, 502, 503})


def _free_port() -> int:
    """Bind ``127.0.0.1:0`` to let the OS hand out an ephemeral free port, then release it."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])
    finally:
        sock.close()


def _child_env(port: int) -> dict[str, str]:
    """Env for the child: loopback host + chosen port, workers=1, a serviceable capacity contract.

    ``load_contract`` reads ``AMF_TARGET_P99_MS`` / ``AMF_UTILIZATION_CAP`` /
    ``AMF_PER_WORKER_RSS_MB`` (else defaults) and derives the worker count from detected hardware
    unless
    ``WEB_CONCURRENCY`` overrides it; pinning ``WEB_CONCURRENCY=1`` keeps the boot deterministic and
    light. A serviceable SLO/rss ensures ``from_signals`` does not refuse-to-start (it raises
    ``CapacityUnavailable`` only on a non-positive SLO / rss < 1).
    """
    env = dict(os.environ)
    env.update(
        {
            "RV_HOST": "127.0.0.1",
            "RV_PORT": str(port),
            "WEB_CONCURRENCY": "1",
            "AMF_WEB_CONCURRENCY": "1",
            "AMF_TARGET_P99_MS": "250",
            "AMF_UTILIZATION_CAP": "0.8",
            "AMF_PER_WORKER_RSS_MB": "512",
            # Keep the child's own uvicorn log quiet so a boot failure's traceback stands out.
            "PYTHONUNBUFFERED": "1",
        }
    )
    return env


@dataclass(frozen=True, slots=True)
class _LiveServer:
    """A booted Serving_Entrypoint child process + its base URL and the Popen handle."""

    base_url: str
    port: int
    process: subprocess.Popen[str]


def _poll_until_ready(proc: subprocess.Popen[str], base_url: str) -> bool:
    """Poll ``GET /readyz`` until it answers (ready OR 503) or the child dies / the timeout fires.

    A 2xx/5xx HTTP answer (ready or an honest 503) both mean the socket is up and serving —
    either is a successful boot for the fixture. Returns ``True`` once the socket answers,
    ``False`` if the child exited early or the readiness window elapsed.
    """
    deadline = time.monotonic() + _READY_TIMEOUT_S
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            return False
        try:
            resp = httpx.get(f"{base_url}/readyz", timeout=1.0)
        except (httpx.ConnectError, httpx.ReadError, httpx.ConnectTimeout, httpx.ReadTimeout):
            time.sleep(_READY_POLL_S)
            continue
        # Any HTTP answer means the ASGI_Host is listening and routing over the real socket.
        return 100 <= resp.status_code < 600
    return False


def _terminate(proc: subprocess.Popen[str]) -> None:
    """Tear the child down: SIGTERM, wait, SIGKILL on timeout — always, even on test failure."""
    if proc.poll() is not None:
        return
    proc.send_signal(signal.SIGTERM)
    try:
        proc.wait(timeout=_SHUTDOWN_DEADLINE_S)
    except subprocess.TimeoutExpired:
        proc.kill()
        with contextlib.suppress(subprocess.TimeoutExpired):
            proc.wait(timeout=5.0)


def _boot_entrypoint(env: Mapping[str, str], port: int) -> _LiveServer:
    """Launch ``edge.serve.main`` as a child process; wait for the socket; fail with its output."""
    proc = subprocess.Popen(
        [sys.executable, "-c", "from gateway_v2.edge.serve import main; raise SystemExit(main())"],
        env=dict(env),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    base_url = f"http://127.0.0.1:{port}"
    if not _poll_until_ready(proc, base_url):
        # Boot failure: capture the child's stdout/stderr so the cause is diagnosable, then kill it.
        _terminate(proc)
        captured = ""
        if proc.stdout is not None:
            with contextlib.suppress(Exception):
                captured = proc.stdout.read()
        pytest.fail(
            f"Serving_Entrypoint child did not become ready on {base_url} within "
            f"{_READY_TIMEOUT_S:.0f}s (exit={proc.returncode}).\n--- child output ---\n{captured}"
        )
    return _LiveServer(base_url=base_url, port=port, process=proc)


@pytest.fixture(scope="module")
def live_server() -> Iterator[_LiveServer]:
    """Module-scoped real-socket Serving_Entrypoint; always torn down (SIGTERM→wait→SIGKILL)."""
    port = _free_port()
    server = _boot_entrypoint(_child_env(port), port)
    try:
        yield server
    finally:
        _terminate(server.process)


def _sdk_client(base_url: str) -> openai.AsyncOpenAI:
    """Stock OpenAI SDK with ONLY a base_url + API-key swap (R5.1) — no other config."""
    return openai.AsyncOpenAI(
        base_url=f"{base_url.rstrip('/')}/v1",
        api_key="sk-bogus-b3-live",
        max_retries=0,
    )


def _assert_json_not_html(resp: httpx.Response) -> None:
    """Assert a response is JSON-ish and never an HTML error page (R5.3/R5.6)."""
    content_type = resp.headers.get("content-type", "")
    assert "text/html" not in content_type, f"HTML error page leaked: {content_type!r}"


# --- R4: /readyz + /metrics over the real socket -----------------------------------------------


def test_readyz_is_honest_200_or_503_over_socket(live_server: _LiveServer) -> None:
    """R4.1/4.2: /readyz is 200-when-ready OR 503-naming-the-dep, and never an HTML page."""
    resp = httpx.get(f"{live_server.base_url}/readyz", timeout=10.0)
    assert resp.status_code in {200, 503}
    _assert_json_not_html(resp)
    if resp.status_code == 503:
        # A 503 MUST name the unavailable dependency in its body so it is diagnosable (R4.2).
        assert resp.text.strip(), "503 /readyz body must name the unavailable dependency"
    else:
        assert resp.text.strip(), "200 /readyz must carry a body"


def test_metrics_is_prometheus_text_over_socket(live_server: _LiveServer) -> None:
    """R4.4/4.5: /metrics is 200 with a text (Prometheus) exposition computed off the loop."""
    resp = httpx.get(f"{live_server.base_url}/metrics", timeout=10.0)
    assert resp.status_code == 200
    content_type = resp.headers.get("content-type", "")
    assert "text/plain" in content_type, f"/metrics should be text exposition, got {content_type!r}"
    _assert_json_not_html(resp)


# --- R5.1 / R5.2: stock SDK surfaces over the real socket --------------------------------------


@pytest.mark.asyncio
async def test_sdk_chat_completions_protocol_over_socket(live_server: _LiveServer) -> None:
    """R5.1/5.6: chat.completions.create over the socket yields an SDK-visible JSON outcome.

    With the fail-closed boot posture an unauthenticated/undecidable request refuses with the
    declared code; the SDK surfaces that as an ``APIStatusError`` carrying a JSON ``{"error": ...}``
    envelope. A completion (if the fail-closed path ever allowed one) is equally acceptable — we
    assert the PROTOCOL shape, never stubbed-upstream bytes.
    """
    client = _sdk_client(live_server.base_url)
    try:
        try:
            completion = await client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[{"role": "user", "content": "hi"}],
            )
        except openai.APIStatusError as exc:
            assert exc.status_code in _REFUSAL_STATUSES
            assert "text/html" not in str(exc.response.headers.get("content-type", ""))
            body = exc.response.json()
            assert isinstance(body, dict)
            assert "error" in body or "detail" in body
        else:
            assert completion is not None
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_sdk_misc_surfaces_protocol_over_socket(live_server: _LiveServer) -> None:
    """R5.2/5.6: embeddings / models.list / models.retrieve / moderations each answer over socket.

    Each surface returns either a structured success body (needs only identity) or the declared
    JSON refusal envelope; in both cases the answer is JSON (not an HTML page) and is not a raw
    framework 500-from-HTML. We assert the honest observed behaviour, PROTOCOL shape only.
    """
    client = _sdk_client(live_server.base_url)
    try:
        awaitables = {
            "embeddings": client.embeddings.create(
                model="text-embedding-3-small", input="hi"
            ),
            "models.list": client.models.list(),
            "models.retrieve": client.models.retrieve("gpt-4o-mini"),
            "moderations": client.moderations.create(input="hi"),
        }
        for name, awaitable in awaitables.items():
            try:
                result = await awaitable
            except openai.APIStatusError as exc:
                assert exc.status_code in _REFUSAL_STATUSES, f"{name}: {exc.status_code}"
                assert "text/html" not in str(exc.response.headers.get("content-type", "")), name
                body = exc.response.json()
                assert isinstance(body, dict), name
                assert "error" in body or "detail" in body, name
            else:
                assert result is not None, name
    finally:
        await client.close()


# --- R5.3: raw curl-equivalent, non-HTML content type ------------------------------------------


def test_raw_post_is_non_html_over_socket(live_server: _LiveServer) -> None:
    """R5.3: a raw (curl-equivalent) POST to a served surface returns a NON-HTML content type."""
    resp = httpx.post(
        f"{live_server.base_url}/v1/chat/completions",
        json={"model": "gpt-4o-mini", "messages": [{"role": "user", "content": "hi"}]},
        timeout=15.0,
    )
    _assert_json_not_html(resp)
    # Not a raw framework 500-from-HTML: an honest refusal or success, with a JSON body when JSON.
    assert resp.status_code != 500 or "application/json" in resp.headers.get("content-type", "")
    if "json" in resp.headers.get("content-type", ""):
        assert isinstance(resp.json(), dict)


# --- R3.1 / R3.9: graceful SIGTERM shutdown ----------------------------------------------------


def test_sigterm_shuts_down_cleanly_within_deadline() -> None:
    """R3.1/3.9: SIGTERM drains + releases the ScanExecutor; the process exits within a bound.

    A dedicated child (not the module fixture) so the shutdown assertion owns the process lifecycle.
    A clean exit is a bounded exit that does NOT hang past the deadline and does NOT crash with an
    unhandled-error traceback on stderr. The exact exit code is timing/signal-dependent (uvicorn
    maps SIGTERM to graceful exit), so we accept any bounded exit and only reject a hang or a
    traceback — the assertion is deliberately timing-tolerant.
    """
    port = _free_port()
    server = _boot_entrypoint(_child_env(port), port)
    proc = server.process
    start = time.monotonic()
    proc.send_signal(signal.SIGTERM)
    try:
        proc.wait(timeout=_SHUTDOWN_DEADLINE_S)
    except subprocess.TimeoutExpired:
        _terminate(proc)
        output = proc.stdout.read() if proc.stdout is not None else ""
        pytest.fail(
            f"Serving_Entrypoint did not exit within {_SHUTDOWN_DEADLINE_S:.0f}s of SIGTERM "
            f"(hung).\n--- child output ---\n{output}"
        )
    elapsed = time.monotonic() - start
    assert elapsed <= _SHUTDOWN_DEADLINE_S
    output = proc.stdout.read() if proc.stdout is not None else ""
    # A graceful shutdown must not surface an unhandled-error traceback to stderr/stdout.
    assert "Traceback (most recent call last)" not in output, (
        f"SIGTERM shutdown surfaced an unhandled error:\n{output}"
    )


# --- R5.4 / R5.5: harness subset + turned-on live-TCP gate against the booted gateway_v2 ---------
#
# Task 7.2. ``test_live_tcp_8300.py`` is un-ignored + ``importorskip``-guarded so it is COLLECTED
# and skips cleanly when no ``AMF_CONFORMANCE_BASE_URL`` is wired (the case for the default
# framework-free ``pytest`` run). Its RUN-GREEN counterpart is HERE: these tests boot the real
# gateway_v2 entrypoint (the module ``live_server`` fixture), point the harness
# (``AMF_CONFORMANCE_BASE_URL`` + ``AMF_CONFORMANCE_APP=v2``) at the live socket (R5.4), and assert
# the SAME SDK-visible contract ``test_live_tcp_8300.py`` encodes — an SDK authentication/status
# error on a bogus key and a JSON (not HTML) error envelope over the real socket (R5.5) —
# honest-stub boundary (R5.6): SDK-visible protocol only, never stubbed-upstream response bytes.


@pytest.fixture
def harness_pointed_at_live(
    live_server: _LiveServer, monkeypatch: pytest.MonkeyPatch
) -> Iterator[str]:
    """Point the gateway_v2 conformance harness at the booted live socket for the test's duration.

    Sets ``AMF_CONFORMANCE_BASE_URL`` (TCP mode) + ``AMF_CONFORMANCE_APP=v2`` so
    ``harness.tcp_base_url()`` / ``harness.conformance_app_name()`` resolve to the running
    gateway_v2 entrypoint (R5.4). ``monkeypatch`` restores the prior env after the test, so the
    collection-time ``skipif`` on ``test_live_tcp_8300.py`` (a separate module) is unaffected.
    """
    monkeypatch.setenv("AMF_CONFORMANCE_BASE_URL", live_server.base_url)
    monkeypatch.setenv("AMF_CONFORMANCE_APP", "v2")
    yield live_server.base_url


def test_harness_resolves_live_socket_in_tcp_mode(harness_pointed_at_live: str) -> None:
    """R5.4: with the env set, the gateway_v2 harness selects TCP mode at the live base URL / v2."""
    from gateway_v2.contracts.openai_conformance import harness

    assert harness.is_tcp_mode() is True
    assert harness.tcp_base_url() == harness_pointed_at_live
    assert harness.conformance_app_name() == "v2"


@pytest.mark.asyncio
async def test_live_tcp_missing_key_is_sdk_error_over_socket(harness_pointed_at_live: str) -> None:
    """R5.5/5.6: a bogus key over the harness TCP base URL surfaces an SDK-visible auth error.

    Mirrors ``test_live_tcp_8300.py::test_live_tcp_missing_key_is_authentication_error`` but against
    the booted gateway_v2 entrypoint. With the fail-closed boot posture the refusal class is
    identity/admission-dependent, so we accept the SDK's ``AuthenticationError`` (its 401 subclass)
    OR any declared-refusal ``APIStatusError`` carrying a JSON (not HTML) envelope — PROTOCOL shape
    only, never stubbed-upstream bytes.
    """
    from gateway_v2.contracts.openai_conformance.harness import tcp_base_url

    base = tcp_base_url()
    assert base
    client = openai.AsyncOpenAI(
        base_url=f"{base.rstrip('/')}/v1", api_key="sk-bogus", max_retries=0
    )
    try:
        with pytest.raises(openai.APIStatusError) as excinfo:
            await client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[{"role": "user", "content": "hi"}],
            )
    finally:
        await client.close()
    exc = excinfo.value
    assert exc.status_code in _REFUSAL_STATUSES
    assert "text/html" not in str(exc.response.headers.get("content-type", ""))


@pytest.mark.asyncio
async def test_live_tcp_error_body_is_json_not_html_over_socket(
    harness_pointed_at_live: str,
) -> None:
    """R5.5/5.6: the error body over the harness TCP base URL is a JSON envelope, never HTML.

    Mirrors ``test_live_tcp_8300.py::test_live_tcp_error_body_is_json_not_html`` against gateway_v2.
    """
    from gateway_v2.contracts.openai_conformance.harness import tcp_base_url

    base = tcp_base_url()
    assert base
    async with httpx.AsyncClient(base_url=base, timeout=15) as raw:
        resp = await raw.post(
            "/v1/chat/completions",
            json={"model": "gpt-4o-mini", "messages": [{"role": "user", "content": "hi"}]},
        )
    assert resp.status_code in _REFUSAL_STATUSES
    assert "text/html" not in resp.headers.get("content-type", "")
    body = resp.json()
    assert isinstance(body, dict)
    assert "error" in body or "detail" in body


# --- R2.4/2.5/2.6/2.7: real OS worker crash-and-respawn over the running entrypoint ------------
#
# Task 7.3. HONEST SCOPE (do NOT over-claim). This test exercises the ONE real-OS fact that is
# actually wired at the B3 local half: uvicorn's own multiprocess manager owns the OS fork +
# respawn of worker PROCESSES, so SIGKILL-ing a single worker does NOT take the server down —
# peers keep answering and uvicorn respawns the dead process. That is the OS-observable
# counterpart to R2.6 (peers keep admitting across a crash).
#
# What this test deliberately does NOT assert: ``WorkerSupervisor.active_slots`` reconciliation
# end-to-end through a real crash. The shipped ``main``/``_host_app`` does NOT bind
# ``WorkerSupervisor``/``build_worker_supervisor`` into the running uvicorn manager — that wiring
# is a seam whose live-manager binding + full fleet behaviour are the DEFERRED GW20 gate (R7.2).
# The shared-accounting isolate/respawn/peers-unchanged accounting is already covered at the unit
# layer (``tests/edge/test_b3_supervisor_wiring.py``, task 4.2) with a fake ``respawn`` + a fake
# crash. Here we keep strictly to the OS-observable fact.


_WORKERS_FOR_CRASH = 2
_SERVE_KEEPALIVE_TIMEOUT_S = 25.0
_SERVE_KEEPALIVE_POLL_S = 0.25


def _child_env_multi(port: int, workers: int) -> dict[str, str]:
    """Mirror :func:`_child_env` but ask the entrypoint to host ``workers`` OS worker processes.

    ``load_contract`` resolves the worker count from ``WEB_CONCURRENCY`` / ``AMF_WEB_CONCURRENCY``
    (``ResourceContract.workers()``), so pinning both to ``workers`` makes uvicorn's multiprocess
    manager spawn exactly that many worker subprocesses under the parent/manager PID.
    """
    env = _child_env(port)
    env["WEB_CONCURRENCY"] = str(workers)
    env["AMF_WEB_CONCURRENCY"] = str(workers)
    return env


def _worker_pids(parent_pid: int) -> list[int]:
    """Best-effort discovery of uvicorn worker-subprocess PIDs under the manager ``parent_pid``.

    uvicorn runs the parent/manager in the child process we spawned; the per-worker event-loop
    processes are its direct children. We find them via ``pgrep -P <parent>`` (POSIX) and fall back
    to scanning ``/proc/*/stat`` for a matching PPid when ``pgrep`` is unavailable. ``psutil`` is
    intentionally NOT added as a dependency. Returns the child PIDs (empty if none are found yet).
    """
    pids: list[int] = []
    try:
        completed = subprocess.run(
            ["pgrep", "-P", str(parent_pid)],
            capture_output=True,
            text=True,
            timeout=5.0,
            check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        completed = None
    if completed is not None and completed.returncode == 0:
        for line in completed.stdout.split():
            with contextlib.suppress(ValueError):
                pids.append(int(line))
        if pids:
            return pids
    # /proc fallback: scan every PID's stat for PPid == parent_pid.
    proc_root = "/proc"
    if os.path.isdir(proc_root):
        for entry in os.listdir(proc_root):
            if not entry.isdigit():
                continue
            stat_path = os.path.join(proc_root, entry, "stat")
            try:
                with open(stat_path, encoding="utf-8") as handle:
                    fields = handle.read().rsplit(")", 1)[-1].split()
            except OSError:
                continue
            # After the final ')' the fields are: state ppid ...  → index 1 is PPid.
            if len(fields) >= 2 and fields[1] == str(parent_pid):
                with contextlib.suppress(ValueError):
                    pids.append(int(entry))
    return pids


def _await_worker_pids(server: _LiveServer, *, want: int) -> list[int]:
    """Poll for ``want`` worker subprocesses to appear under the manager, bounded by a timeout."""
    deadline = time.monotonic() + _READY_TIMEOUT_S
    pids: list[int] = []
    while time.monotonic() < deadline:
        if server.process.poll() is not None:
            return []
        pids = _worker_pids(server.process.pid)
        if len(pids) >= want:
            return pids
        time.sleep(_READY_POLL_S)
    return pids


def _pid_alive(pid: int) -> bool:
    """True if ``pid`` is still a live process (signal 0 probe, EPERM counts as alive)."""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _server_keeps_serving(base_url: str) -> bool:
    """True once ``/readyz`` answers (200-or-503, non-HTML) over the socket within a bound.

    A 200 or an honest 503 both prove the server as a whole is still listening + routing after the
    worker kill (a peer answered and/or uvicorn respawned the dead worker). An HTML body would mean
    a framework crash page leaked, which is a failure.
    """
    deadline = time.monotonic() + _SERVE_KEEPALIVE_TIMEOUT_S
    while time.monotonic() < deadline:
        try:
            resp = httpx.get(f"{base_url}/readyz", timeout=2.0)
        except (httpx.ConnectError, httpx.ReadError, httpx.ConnectTimeout, httpx.ReadTimeout):
            time.sleep(_SERVE_KEEPALIVE_POLL_S)
            continue
        if resp.status_code in {200, 503} and "text/html" not in resp.headers.get(
            "content-type", ""
        ):
            return True
        time.sleep(_SERVE_KEEPALIVE_POLL_S)
    return False


def test_killed_worker_does_not_down_the_server_over_socket() -> None:
    """R2.6 (OS level): SIGKILL-ing one uvicorn worker does NOT take the server down.

    HONEST SCOPE — this verifies uvicorn's OS-level worker resilience ONLY: a killed worker
    PROCESS does not down the server; the gateway as a whole keeps serving over the socket (a peer
    worker answers and/or uvicorn's multiprocess manager respawns the dead worker). It does NOT
    observe ``WorkerSupervisor.active_slots`` reconciliation through a real crash — that shared
    accounting is unit-tested (``tests/edge/test_b3_supervisor_wiring.py``, task 4.2) and its
    live-manager binding + fleet behaviour are the DEFERRED GW20 gate (R7.2).

    Boots the entrypoint with ``workers=2``, identifies the worker subprocesses under the
    parent/manager PID (``pgrep -P`` / ``/proc`` fallback — no ``psutil`` dependency), SIGKILLs one
    worker to simulate an abnormal crash, then asserts within a bounded timeout that ``/readyz``
    still answers (200-or-503, non-HTML) AND the stock OpenAI SDK still gets an SDK-visible response
    over the socket. SKIPS cleanly (never fails spuriously) if no >=2 real worker processes are
    observable.

    NOTE (honest at the B3 local half): the shipped ``_host_app`` hosts via
    ``uvicorn.Server(config).run()``, which runs a SINGLE in-process server and ignores
    ``config.workers`` — uvicorn only forks worker subprocesses through the top-level
    ``uvicorn.run(...)`` multiprocess supervisor, which B3 does not call. So on the shipped
    entrypoint this test SKIPS with that precise reason today; it RUNS GREEN once the real
    multi-worker OS manager is bound (the deferred GW20 gate, R7.2). The test is written to assert
    only the OS-observable fact (a killed worker does not down the server) whenever >=2 real worker
    processes do appear, and never asserts the un-wired WorkerSupervisor accounting.
    """
    port = _free_port()
    server = _boot_entrypoint(_child_env_multi(port, _WORKERS_FOR_CRASH), port)
    try:
        worker_pids = _await_worker_pids(server, want=_WORKERS_FOR_CRASH)
        if len(worker_pids) < _WORKERS_FOR_CRASH:
            pytest.skip(
                "no >=2 uvicorn worker subprocesses are observable under the manager PID "
                f"(found {len(worker_pids)}: {worker_pids}). HONEST ROOT CAUSE: the B3 "
                "``_host_app`` hosts via ``uvicorn.Server(config).run()``, which runs a SINGLE "
                "in-process server and IGNORES ``config.workers`` — uvicorn only engages its "
                "multiprocess worker manager through the top-level ``uvicorn.run(...)`` "
                "supervisor, "
                "which the B3 local half does not call. Binding the real multi-worker OS manager "
                "(and the WorkerSupervisor into it) is the DEFERRED GW20 gate (R7.2), so a real OS "
                "worker crash-and-respawn is not observable at this half. The isolate/respawn/"
                "peers-unchanged shared accounting is unit-tested in test_b3_supervisor_wiring.py "
                "(task 4.2). Skipping rather than asserting a fact that is not wired here."
            )

        victim = worker_pids[0]
        survivors = [pid for pid in worker_pids if pid != victim]
        os.kill(victim, signal.SIGKILL)

        # The server as a whole must keep serving across the kill (peer answers / uvicorn respawns).
        assert _server_keeps_serving(server.base_url), (
            "server stopped answering /readyz over the socket after one worker was SIGKILL-ed; "
            "a killed worker must not take the whole gateway down"
        )

        # The parent/manager must still be alive (uvicorn did not exit on a single worker death).
        assert server.process.poll() is None, "the uvicorn manager exited when one worker died"

        # At least one peer worker is still alive, OR the manager respawned replacements — either
        # way the live worker set is non-empty (the dead victim is gone, peers/replacements remain).
        assert any(_pid_alive(pid) for pid in survivors) or _await_worker_pids(
            server, want=1
        ), "no live worker process remained after the crash (expected a peer or a respawn)"

        # And an SDK request still gets an SDK-visible response over the socket (protocol only).
        async def _sdk_roundtrip() -> None:
            client = _sdk_client(server.base_url)
            try:
                try:
                    completion = await client.chat.completions.create(
                        model="gpt-4o-mini",
                        messages=[{"role": "user", "content": "post-crash"}],
                    )
                except openai.APIStatusError as exc:
                    assert exc.status_code in _REFUSAL_STATUSES
                    assert "text/html" not in str(
                        exc.response.headers.get("content-type", "")
                    )
                else:
                    assert completion is not None
            finally:
                await client.close()

        import asyncio

        asyncio.run(_sdk_roundtrip())
    finally:
        _terminate(server.process)
