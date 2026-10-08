"""The re-hydrator process. R2-04's third and fourth clauses have nothing to attach to without it.

The runbook requires *"≥ 2 re-hydrators in ≥ 2 zones (proven safe concurrently)"* and that nothing
*"lets an idle-stop policy touch them"*. Both are statements about running processes, and until
this module there were none: `Rehydrator` and `PostgresControlDB` were constructed only by tests,
`state_control` was not even copied into the image, and `StateRehydratorSingleInstance` alarmed on
a Prometheus job that did not exist.

Four deliberate choices:

* **`from_env` is a pure function of a mapping**, not a reader of `os.environ`. A start-up path
  that can only be exercised by mutating process state does not get exercised.
* **`verify_bounds()` runs before the first round and is fatal.** R2-04's first clause is that the
  four timeouts hold on every control-plane connection; a process that cannot prove that must not
  claim to be enforcing it. This is the check whose absence hid the PgBouncer `options` drop.
* **`require_bounded_client` runs on the publisher.** A partitioned store accepts the connection
  and never answers, so without a per-operation timeout a round never returns, the stamp is never
  withheld OR written, and the freshness mechanism silently does not exist. C36 measured the cost.
* **No leader election.** Two re-hydrators are redundant, not coordinated: publishes are
  WATCH-guarded, a stamp is never replaced by an older one, and losing the stamp race is the
  healthy outcome about half the time. A lease would convert redundancy into a new single point of
  failure with a failover window.

`ThreadingHTTPServer` from the standard library, not a framework: the surface is two endpoints and
adding a web dependency to the control plane to serve them would be the wrong trade. `/readyz` is
deliberately ABSENT — a re-hydrator has no readiness semantics. It is running or it is not, and
the fleet-level signal is the freshness stamp, which the gateways read directly.
"""

from __future__ import annotations

import logging
import os
import signal
import socket
import threading
import time
from collections.abc import Mapping
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from gateway_v2.domain.locks import FRESH_MS, PG_GRACE_MS
from gateway_v2.domain.state_knobs import (
    DEFAULT_DEEP_EVERY,
    DEFAULT_REFRESH_PERIOD_MS,
    DEFAULT_REHYDRATE_PERIOD_MS,
    StateKnobs,
)
from gateway_v2.runtime.store_keys import DEFAULT_NAMESPACE, StoreKeys
from gateway_v2.runtime.store_valkey import require_bounded_client
from state_control.metrics import RehydrationMetricsRecorder, render
from state_control.pg import PostgresControlDB
from state_control.rehydrate import (
    Rehydrator,
    RoundSummary,
    default_rehydrator_id,
)
from state_control.valkey import ValkeyPublisher
from state_control.writer import StateWriter

LOG = logging.getLogger("amf.state.service")

DEFAULT_METRICS_PORT = 9108
DEFAULT_LOCK_TIMEOUT_MS = 250
DEFAULT_STATEMENT_TIMEOUT_MS = 800
DEFAULT_SNAPSHOT_TIMEOUT_MS = 5_000
DEFAULT_IDLE_TX_TIMEOUT_MS = 5_000
DEFAULT_TCP_USER_TIMEOUT_MS = 2_000
DEFAULT_CONNECT_TIMEOUT_S = 2


def _int(env: Mapping[str, str], name: str, default: int) -> int:
    raw = (env.get(name) or "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer, got {raw!r}") from exc


def _required(env: Mapping[str, str], name: str, why: str) -> str:
    value = (env.get(name) or "").strip()
    if not value:
        raise ValueError(f"{name} is required: {why}")
    return value


@dataclass(frozen=True, slots=True)
class ServiceConfig:
    """Everything the process needs, resolved and validated. Built once, never re-read."""

    dsn: str
    valkey_url: str
    secret: bytes
    knobs: StateKnobs
    rehydrator_id: str
    zone: str
    namespace: str
    metrics_port: int
    lock_timeout_ms: int
    statement_timeout_ms: int
    snapshot_timeout_ms: int
    idle_tx_timeout_ms: int
    tcp_user_timeout_ms: int
    connect_timeout_s: int

    @property
    def period_s(self) -> float:
        return self.knobs.rehydrate_period_ms / 1000


def from_env(env: Mapping[str, str] | None = None) -> ServiceConfig:
    """Resolve the configuration, or raise with the name of what is wrong.

    `FRESH_MS` and `PG_GRACE_MS` are owner-locked in `domain/locks.py`. They are overridable here
    only because `StateKnobs` validates the relationships between them and refusing to read them
    would make the break-glass unreachable; `StateKnobs.warnings` is what says so out loud.
    """
    source = os.environ if env is None else env
    knobs = StateKnobs(
        fresh_ms=_int(source, "AMF_STATE_FRESH_MS", FRESH_MS),
        pg_grace_ms=_int(source, "AMF_STATE_PG_GRACE_MS", PG_GRACE_MS),
        rehydrate_period_ms=_int(
            source, "AMF_REHYDRATE_PERIOD_MS", DEFAULT_REHYDRATE_PERIOD_MS,
        ),
        refresh_period_ms=_int(source, "AMF_STATE_REFRESH_MS", DEFAULT_REFRESH_PERIOD_MS),
        deep_every=_int(source, "AMF_REHYDRATE_DEEP_EVERY", DEFAULT_DEEP_EVERY),
    )
    return ServiceConfig(
        dsn=_required(
            source,
            "AMF_STATE_PG_DSN",
            "the control-plane Postgres DSN. It MUST point at the primary, never a replica "
            "(see state_control/pg.py)",
        ),
        valkey_url=_required(
            source, "AMF_STATE_VALKEY_URL", "the published-state store URL",
        ),
        secret=_required(
            source,
            "AMF_STATE_HMAC_KEY",
            "the state signing secret. It must match the gateways' or every stamp fails its "
            "signature and the whole fleet fails closed",
        ).encode("utf-8"),
        knobs=knobs,
        rehydrator_id=(source.get("AMF_REHYDRATOR_ID") or "").strip()
        or default_rehydrator_id(),
        zone=(source.get("AMF_DEPLOY_ZONE") or "").strip() or "unknown",
        namespace=(source.get("AMF_STATE_NAMESPACE") or "").strip() or DEFAULT_NAMESPACE,
        metrics_port=_int(source, "AMF_REHYDRATOR_METRICS_PORT", DEFAULT_METRICS_PORT),
        lock_timeout_ms=_int(source, "AMF_PG_LOCK_TIMEOUT_MS", DEFAULT_LOCK_TIMEOUT_MS),
        statement_timeout_ms=_int(
            source, "AMF_PG_STATEMENT_TIMEOUT_MS", DEFAULT_STATEMENT_TIMEOUT_MS,
        ),
        snapshot_timeout_ms=_int(
            source, "AMF_PG_SNAPSHOT_TIMEOUT_MS", DEFAULT_SNAPSHOT_TIMEOUT_MS,
        ),
        idle_tx_timeout_ms=_int(source, "AMF_PG_IDLE_TX_TIMEOUT_MS", DEFAULT_IDLE_TX_TIMEOUT_MS),
        tcp_user_timeout_ms=_int(
            source, "AMF_PG_TCP_USER_TIMEOUT_MS", DEFAULT_TCP_USER_TIMEOUT_MS,
        ),
        connect_timeout_s=_int(source, "AMF_PG_CONNECT_TIMEOUT_S", DEFAULT_CONNECT_TIMEOUT_S),
    )


class _Handler(BaseHTTPRequestHandler):
    """`/healthz` and `/metrics`. Nothing else, and nothing that takes a parameter."""

    service: RehydratorService

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler's contract
        if self.path.startswith("/healthz"):
            self._text(200, "ok\n")
        elif self.path.startswith("/metrics"):
            self._text(200, render(self.service.metrics()))
        else:
            self._text(404, "not found\n")

    def _text(self, code: int, body: str) -> None:
        payload = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002 - stdlib's signature
        """Silence per-request stderr logging: a 1 Hz scrape is not an event worth a line."""


class RehydratorService:
    """One re-hydrator process: a bounded loop, a metrics surface, and a clean stop."""

    def __init__(self, config: ServiceConfig, rehydrator: Rehydrator) -> None:
        self._config = config
        self._rehydrator = rehydrator
        self._recorder = RehydrationMetricsRecorder()
        self._stop = threading.Event()
        self._round = 0

    # --- construction ---------------------------------------------------------------------------

    @classmethod
    def build(cls, config: ServiceConfig) -> RehydratorService:
        """Wire the real adapters and PROVE the session bounds before the first round."""
        import redis

        db = PostgresControlDB(
            config.dsn,
            lock_timeout_ms=config.lock_timeout_ms,
            statement_timeout_ms=config.statement_timeout_ms,
            idle_tx_timeout_ms=config.idle_tx_timeout_ms,
            tcp_user_timeout_ms=config.tcp_user_timeout_ms,
            connect_timeout_s=config.connect_timeout_s,
        )
        # R2-04 clause 1, verified rather than asserted. Raises ControlPlaneBoundsNotApplied.
        db.verify_bounds()
        client = redis.Redis.from_url(
            config.valkey_url, socket_timeout=config.knobs.store_timeout_ceiling_s / 2,
        )
        require_bounded_client(client, below_s=config.knobs.store_timeout_ceiling_s)
        publisher = ValkeyPublisher(client, config.secret, StoreKeys(config.namespace))
        writer = StateWriter(db, publisher, config.secret)
        rehydrator = Rehydrator(
            db,
            publisher,
            writer,
            config.secret,
            name=config.rehydrator_id,
            fresh_ms=config.knobs.fresh_ms,
            pg_grace_ms=config.knobs.pg_grace_ms,
        )
        return cls(config, rehydrator)

    # --- the loop -------------------------------------------------------------------------------

    def round_once(self, *, now: float | None = None) -> None:
        """One round, timed and recorded. Never raises: a round is allowed to fail."""
        self.round_once_summary(now=now)

    def round_once_summary(self, *, now: float | None = None) -> RoundSummary:
        """The same round, returning what it found. For start-up checks and tests."""
        self._round += 1
        deep = self._config.knobs.is_deep_round(self._round)
        started = time.monotonic()
        summary = self._rehydrator.round_once(deep=deep)
        self._recorder.observe_round(
            summary,
            duration_s=time.monotonic() - started,
            now=time.time() if now is None else now,
        )
        self._recorder.observe_publish_pending(summary.publish_pending_s)
        return summary

    def run(self) -> int:
        """Loop until stopped. Returns a process exit code."""
        config = self._config
        for warning in config.knobs.warnings:
            LOG.warning("%s", warning)
        LOG.info(
            "re-hydrator %s starting in zone=%s: period=%dms deep_every=%d fresh=%dms "
            "pg_grace=%dms namespace=%s",
            config.rehydrator_id,
            config.zone,
            config.knobs.rehydrate_period_ms,
            config.knobs.deep_every,
            config.knobs.fresh_ms,
            config.knobs.pg_grace_ms,
            config.namespace,
        )
        server = self._serve_metrics()
        try:
            while not self._stop.is_set():
                started = time.monotonic()
                self.round_once()
                # Sleep the REMAINDER of the period, so a slow round does not push the cadence
                # out: the freshness bound is measured from the round's start, not its end.
                elapsed = time.monotonic() - started
                self._stop.wait(max(config.period_s - elapsed, 0.0))
        finally:
            self._recorder.stopping()
            if server is not None:
                server.shutdown()
                server.server_close()
            LOG.info("re-hydrator %s stopped", config.rehydrator_id)
        return 0

    def stop(self) -> None:
        self._stop.set()

    def metrics(self) -> Any:
        return self._recorder.snapshot()

    # --- the metrics surface --------------------------------------------------------------------

    def _serve_metrics(self) -> ThreadingHTTPServer | None:
        """Bind `/metrics` and `/healthz`, or carry on without them.

        A failure to bind must not stop a re-hydrator: the fleet's enforcement depends on the
        loop, and losing observability is strictly better than losing the component.
        """
        handler = type("_BoundHandler", (_Handler,), {"service": self})
        try:
            server = ThreadingHTTPServer(("", self._config.metrics_port), handler)
        except OSError as exc:
            LOG.warning(
                "could not bind the metrics surface on port %d (%s); "
                "the loop continues without it, but StateRehydratorSingleInstance will not see "
                "this instance",
                self._config.metrics_port,
                exc,
            )
            return None
        server.daemon_threads = True
        threading.Thread(
            target=server.serve_forever, name="amf-rehydrator-metrics", daemon=True,
        ).start()
        LOG.info(
            "metrics on http://%s:%d/metrics",
            socket.gethostname(),
            self._config.metrics_port,
        )
        return server


def main(env: Mapping[str, str] | None = None) -> int:
    """Process entry point. `python -m state_control`."""
    logging.basicConfig(
        level=(os.environ.get("AMF_LOG_LEVEL") or "INFO").upper(),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    service = RehydratorService.build(from_env(env))
    for signalled in (signal.SIGTERM, signal.SIGINT):
        try:
            signal.signal(signalled, lambda *_: service.stop())
        except ValueError:  # pragma: no cover - not the main thread
            pass
    return service.run()
