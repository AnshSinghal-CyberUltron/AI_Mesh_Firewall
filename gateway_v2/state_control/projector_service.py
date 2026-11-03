"""The projector process. `python -m state_control.projector_service`.

Same shape as the re-hydrator's service (R2-04): `from_env` is a pure function of a mapping, the
session bounds are verified before the first round and fatal, the store client must be bounded,
and the schema is created if missing (G13). Two replicas run; the lease (`projector.py`) makes
exactly one of them the writer, and the other takes over within one lease TTL.

`/healthz` answers "is the loop alive" -- a round started within three periods -- not "am I the
leader". A standby is healthy, and a container orchestrator restarting a healthy standby would
only remove the failover. Leadership and heartbeat age are metrics, and the alerts read them.
"""

from __future__ import annotations

import logging
import os
import signal
import socket
import threading
import time
from collections.abc import Mapping
from dataclasses import dataclass, replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from gateway_v2.domain.state_knobs import ProjectorKnobs, StateKnobs
from gateway_v2.runtime.store_keys import DEFAULT_NAMESPACE, StoreKeys
from gateway_v2.runtime.store_valkey import require_bounded_client
from state_control.pg import PostgresControlDB
from state_control.projector import ProjectionSummary, Projector
from state_control.service import (
    DEFAULT_CONNECT_TIMEOUT_S,
    DEFAULT_IDLE_TX_TIMEOUT_MS,
    DEFAULT_LOCK_TIMEOUT_MS,
    DEFAULT_STATEMENT_TIMEOUT_MS,
    DEFAULT_TCP_USER_TIMEOUT_MS,
    _int,
    _required,
    projector_knobs_from_env,
)
from state_control.source_pg import PostgresControlSource
from state_control.valkey import ValkeyPublisher
from state_control.writer import StateWriter

LOG = logging.getLogger("amf.state.projector.service")

DEFAULT_METRICS_PORT = 9109
_HEALTHY_PERIODS = 3
ABSENT = -1.0


@dataclass(frozen=True, slots=True)
class ProjectorConfig:
    dsn: str
    valkey_url: str
    secret: bytes
    knobs: ProjectorKnobs
    holder: str
    namespace: str
    metrics_port: int
    lock_timeout_ms: int
    statement_timeout_ms: int
    idle_tx_timeout_ms: int
    tcp_user_timeout_ms: int
    connect_timeout_s: int

    @property
    def period_s(self) -> float:
        return self.knobs.period_ms / 1000


def from_env(env: Mapping[str, str] | None = None) -> ProjectorConfig:
    source = os.environ if env is None else env
    return ProjectorConfig(
        dsn=_required(
            source, "AMF_STATE_PG_DSN", "control's Postgres DSN (the primary; see pg.py)",
        ),
        valkey_url=_required(source, "AMF_STATE_VALKEY_URL", "the published-state store URL"),
        secret=_required(
            source,
            "AMF_STATE_HMAC_KEY",
            "the state signing secret; it must match the re-hydrators' and the gateways'",
        ).encode("utf-8"),
        knobs=projector_knobs_from_env(source),
        holder=(source.get("AMF_PROJECTOR_ID") or "").strip()
        or f"{socket.gethostname()}:{os.getpid()}",
        namespace=(source.get("AMF_STATE_NAMESPACE") or "").strip() or DEFAULT_NAMESPACE,
        metrics_port=_int(source, "AMF_PROJECTOR_METRICS_PORT", DEFAULT_METRICS_PORT),
        lock_timeout_ms=_int(source, "AMF_PG_LOCK_TIMEOUT_MS", DEFAULT_LOCK_TIMEOUT_MS),
        statement_timeout_ms=_int(
            source, "AMF_PG_STATEMENT_TIMEOUT_MS", DEFAULT_STATEMENT_TIMEOUT_MS,
        ),
        idle_tx_timeout_ms=_int(source, "AMF_PG_IDLE_TX_TIMEOUT_MS", DEFAULT_IDLE_TX_TIMEOUT_MS),
        tcp_user_timeout_ms=_int(
            source, "AMF_PG_TCP_USER_TIMEOUT_MS", DEFAULT_TCP_USER_TIMEOUT_MS,
        ),
        connect_timeout_s=_int(source, "AMF_PG_CONNECT_TIMEOUT_S", DEFAULT_CONNECT_TIMEOUT_S),
    )


@dataclass(frozen=True, slots=True)
class ProjectorMetrics:
    """Label-free. Every series exists from the first scrape (ABSENT = never happened)."""

    rounds: int = 0
    failures: int = 0
    leader: int = 0
    heartbeats: int = 0
    lease_lost: int = 0
    writes: int = 0
    unchanged: int = 0
    revokes: int = 0
    adds: int = 0
    adds_deferred: int = 0
    invalid_plans: int = 0
    refused_conditions: int = 0
    orgs_without_plan: int = 0
    unsupported_switches: int = 0
    keys_without_org: int = 0
    invalid_names: int = 0
    duration_max_s: float = 0.0
    last_heartbeat_at: float = ABSENT
    active: int = 1

    def series(self) -> dict[str, float]:
        prefix = "amf_projector"
        return {
            f"{prefix}_rounds_total": self.rounds,
            f"{prefix}_round_failures_total": self.failures,
            f"{prefix}_leader": self.leader,
            f"{prefix}_heartbeats_total": self.heartbeats,
            f"{prefix}_lease_lost_total": self.lease_lost,
            f"{prefix}_writes_total": self.writes,
            f"{prefix}_unchanged_total": self.unchanged,
            f"{prefix}_revokes_total": self.revokes,
            f"{prefix}_adds_total": self.adds,
            f"{prefix}_adds_deferred": self.adds_deferred,
            f"{prefix}_invalid_plans": self.invalid_plans,
            f"{prefix}_refused_conditions": self.refused_conditions,
            f"{prefix}_orgs_without_plan": self.orgs_without_plan,
            f"{prefix}_unsupported_switches": self.unsupported_switches,
            f"{prefix}_keys_without_org": self.keys_without_org,
            f"{prefix}_invalid_names": self.invalid_names,
            f"{prefix}_round_duration_seconds_max": self.duration_max_s,
            f"{prefix}_last_heartbeat_timestamp": self.last_heartbeat_at,
            f"{prefix}_active": self.active,
        }


SERIES_COUNT = 19
"""Fixed by construction; the test asserts `len(series())` agrees (R2-10's rule)."""


def observe(
    metrics: ProjectorMetrics,
    summary: ProjectionSummary | None,
    *,
    duration_s: float,
    now: float,
) -> ProjectorMetrics:
    """Fold one round in. `summary=None` is a round that raised."""
    rounds = metrics.rounds + 1
    duration_max_s = max(metrics.duration_max_s, duration_s)
    if summary is None:
        return replace(
            metrics, rounds=rounds, duration_max_s=duration_max_s, failures=metrics.failures + 1,
        )
    if not summary.leader and not summary.lease_lost:
        return replace(metrics, rounds=rounds, duration_max_s=duration_max_s, leader=0)
    return replace(
        metrics,
        rounds=rounds,
        duration_max_s=duration_max_s,
        leader=0 if summary.lease_lost else 1,
        heartbeats=metrics.heartbeats + (1 if summary.heartbeat else 0),
        lease_lost=metrics.lease_lost + (1 if summary.lease_lost else 0),
        writes=metrics.writes + summary.writes,
        unchanged=metrics.unchanged + summary.unchanged,
        revokes=metrics.revokes + summary.revokes,
        adds=metrics.adds + summary.adds,
        adds_deferred=summary.adds_deferred,
        invalid_plans=summary.invalid_plans,
        refused_conditions=summary.refused_conditions,
        orgs_without_plan=summary.orgs_without_plan,
        unsupported_switches=summary.unsupported_switches,
        keys_without_org=summary.keys_without_org,
        invalid_names=summary.invalid_names,
        last_heartbeat_at=now if summary.heartbeat else metrics.last_heartbeat_at,
    )


def render(metrics: ProjectorMetrics) -> str:
    lines = [f"{name} {value}" for name, value in sorted(metrics.series().items())]
    return "\n".join(lines) + "\n"


class _Handler(BaseHTTPRequestHandler):
    service: ProjectorService

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler's contract
        if self.path.startswith("/healthz"):
            if self.service.alive():
                self._text(200, "ok\n")
            else:
                self._text(503, "projector loop is not running\n")
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
        """A 1 Hz scrape is not an event worth a line."""


class ProjectorService:
    def __init__(self, config: ProjectorConfig, projector: Projector) -> None:
        self._config = config
        self._projector = projector
        self._metrics = ProjectorMetrics()
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._last_round_at = time.monotonic()
        self._leader = False

    @classmethod
    def build(cls, config: ProjectorConfig) -> ProjectorService:
        import redis

        db = PostgresControlDB(
            config.dsn,
            lock_timeout_ms=config.lock_timeout_ms,
            statement_timeout_ms=config.statement_timeout_ms,
            idle_tx_timeout_ms=config.idle_tx_timeout_ms,
            tcp_user_timeout_ms=config.tcp_user_timeout_ms,
            connect_timeout_s=config.connect_timeout_s,
        )
        db.verify_bounds()
        db.init_schema()
        ceiling_s = StateKnobs().store_timeout_ceiling_s
        client = redis.Redis.from_url(config.valkey_url, socket_timeout=ceiling_s / 2)
        require_bounded_client(client, below_s=ceiling_s)
        publisher = ValkeyPublisher(client, config.secret, StoreKeys(config.namespace))
        writer = StateWriter(db, publisher, config.secret, actor=f"amf.projector:{config.holder}")
        projector = Projector(
            db,
            PostgresControlSource(db),
            writer,
            holder=config.holder,
            knobs=config.knobs,
        )
        return cls(config, projector)

    def round_once(self) -> ProjectionSummary | None:
        started = time.monotonic()
        self._last_round_at = started
        summary: ProjectionSummary | None
        try:
            summary = self._projector.round_once()
        except Exception as exc:  # noqa: BLE001 - a round may fail; the heartbeat then ages
            LOG.warning("projection round failed: %s: %s", type(exc).__name__, exc)
            summary = None
        self._log_transition(summary)
        with self._lock:
            self._metrics = observe(
                self._metrics, summary, duration_s=time.monotonic() - started, now=time.time(),
            )
        return summary

    def _log_transition(self, summary: ProjectionSummary | None) -> None:
        leading = summary is not None and summary.leader
        if leading != self._leader:
            LOG.info(
                "projector %s is %s",
                self._config.holder,
                "the writer" if leading else "standing by",
            )
        self._leader = leading

    def run(self) -> int:
        config = self._config
        for warning in config.knobs.warnings:
            LOG.warning("%s", warning)
        LOG.info(
            "projector %s starting: period=%dms lease=%dms lag_bound=%dms namespace=%s",
            config.holder,
            config.knobs.period_ms,
            config.knobs.lease_ms,
            config.knobs.lag_ms,
            config.namespace,
        )
        server = self._serve_metrics()
        try:
            while not self._stop.is_set():
                started = time.monotonic()
                self.round_once()
                elapsed = time.monotonic() - started
                self._stop.wait(max(config.period_s - elapsed, 0.0))
        finally:
            self._projector.release()
            with self._lock:
                self._metrics = replace(self._metrics, active=0, leader=0)
            if server is not None:
                server.shutdown()
                server.server_close()
            LOG.info("projector %s stopped", config.holder)
        return 0

    def stop(self) -> None:
        self._stop.set()

    def alive(self) -> bool:
        return (
            not self._stop.is_set()
            and time.monotonic() - self._last_round_at < _HEALTHY_PERIODS * self._config.period_s
        )

    def metrics(self) -> ProjectorMetrics:
        with self._lock:
            return self._metrics

    def _serve_metrics(self) -> ThreadingHTTPServer | None:
        handler = type("_BoundHandler", (_Handler,), {"service": self})
        try:
            server = ThreadingHTTPServer(("", self._config.metrics_port), handler)
        except OSError as exc:
            LOG.warning(
                "could not bind the projector metrics on port %d (%s); continuing without them",
                self._config.metrics_port,
                exc,
            )
            return None
        server.daemon_threads = True
        threading.Thread(
            target=server.serve_forever, name="amf-projector-metrics", daemon=True,
        ).start()
        return server


def main(env: Mapping[str, str] | None = None) -> int:
    logging.basicConfig(
        level=(os.environ.get("AMF_LOG_LEVEL") or "INFO").upper(),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    service = ProjectorService.build(from_env(env))
    for signalled in (signal.SIGTERM, signal.SIGINT):
        try:
            signal.signal(signalled, lambda *_: service.stop())
        except ValueError:  # pragma: no cover - not the main thread
            pass
    return service.run()


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
