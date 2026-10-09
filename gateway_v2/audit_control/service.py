"""The audit exporter process. Without it the card's durable sink has nothing to attach to.

GW14c requires *"a durable audit sink with an acknowledged-vs-durable high-water mark and
`records_lost`"*. A high-water mark is a statement about a running component, and R2-04 recorded
what happens when a requirement like that has no process: `Rehydrator` and `PostgresControlDB`
were constructed only by tests, `state_control` was not in any image, and an alarm named a
Prometheus job that did not exist. This module exists so GW14c does not repeat it.

The four choices are the same four, for the same reasons:

* **`from_env` is a pure function of a mapping**, not a reader of `os.environ`. A start-up path
  that can only be exercised by mutating process state does not get exercised.
* **`verify_bounds()` runs before the first round and is fatal.** The sink writes through
  `state_control.pg`'s bounded sessions, so R2-04's four timeouts cover it — but only if they
  actually applied, and the PgBouncer `options` drop is exactly the failure that hid for a whole
  round of validation.
* **`require_bounded_client` runs on the store client.** A partitioned store accepts the
  connection and never answers, so without a per-operation timeout a round never returns, the
  cursor never advances, and the export silently stops while the trim keeps going. That
  combination is how counted loss becomes uncounted loss.
* **No leader election.** Two exporters are safe rather than coordinated: the page insert is
  idempotent on `(org_id, stream_id)` and the cursor only moves forward, so the loser of a race
  re-reads a page and inserts nothing. A lease would turn redundancy into a new single point of
  failure with a failover window.

`/healthz` and `/metrics` over `ThreadingHTTPServer`, as in `state_control`: the surface is two
endpoints and adding a web framework to the control plane to serve them would be the wrong trade.
`/readyz` is deliberately absent — an exporter has no readiness semantics, and the fleet-level
signal is `amf_audit_completeness_ratio`, which an operator reads directly.
"""

from __future__ import annotations

import asyncio
import logging
import os
import signal
import socket
import threading
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from audit_control.export import DEFAULT_PAGE, MAX_PAGES_PER_ROUND, AuditExporter, RoundSummary
from audit_control.sink_pg import PostgresAuditSink
from gateway_v2.audit.metrics import AuditMetricsRecorder, DurableReading, render
from gateway_v2.domain.audit_knobs import knobs_from_env
from gateway_v2.runtime.store_audit import ValkeyAuditStore
from gateway_v2.runtime.store_keys import DEFAULT_NAMESPACE, StoreKeys
from gateway_v2.runtime.store_valkey import require_bounded_client

LOG = logging.getLogger("amf.audit.service")

DEFAULT_METRICS_PORT = 9109
"""One above the re-hydrator's 9108, so the two can share a host without a collision."""

DEFAULT_PERIOD_MS = 1_000
DEFAULT_STORE_TIMEOUT_S = 5.0


def _int(env: Mapping[str, str], name: str, default: int) -> int:
    raw = (env.get(name) or "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer, got {raw!r}") from exc


def _float(env: Mapping[str, str], name: str, default: float) -> float:
    raw = (env.get(name) or "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be a number, got {raw!r}") from exc


def _required(env: Mapping[str, str], name: str, why: str) -> str:
    value = (env.get(name) or "").strip()
    if not value:
        raise ValueError(f"{name} is required: {why}")
    return value


@dataclass(frozen=True, slots=True)
class ExporterConfig:
    """Everything the process needs, resolved and validated. Built once, never re-read."""

    dsn: str
    valkey_url: str
    namespace: str
    exporter_id: str
    zone: str
    period_ms: int
    page: int
    max_pages: int
    metrics_port: int
    store_timeout_s: float

    @property
    def period_s(self) -> float:
        return self.period_ms / 1000


def from_env(env: Mapping[str, str] | None = None) -> ExporterConfig:
    """Resolve the configuration, or raise naming what is wrong."""
    source = os.environ if env is None else env
    config = ExporterConfig(
        dsn=_required(
            source,
            "AMF_AUDIT_PG_DSN",
            "the durable audit sink's Postgres DSN. It may be the control-plane database or a "
            "separate one; audit volume is far larger than state volume, so sharing it is a "
            "capacity decision rather than a default",
        ),
        valkey_url=_required(
            source, "AMF_STATE_VALKEY_URL", "the store holding the audit streams to drain",
        ),
        namespace=(source.get("AMF_STATE_NAMESPACE") or "").strip() or DEFAULT_NAMESPACE,
        exporter_id=(source.get("AMF_AUDIT_EXPORTER_ID") or "").strip() or _default_id(),
        zone=(source.get("AMF_DEPLOY_ZONE") or "").strip() or "unknown",
        period_ms=_int(source, "AMF_AUDIT_EXPORT_PERIOD_MS", DEFAULT_PERIOD_MS),
        page=_int(source, "AMF_AUDIT_EXPORT_PAGE", DEFAULT_PAGE),
        max_pages=_int(source, "AMF_AUDIT_EXPORT_MAX_PAGES", MAX_PAGES_PER_ROUND),
        metrics_port=_int(source, "AMF_AUDIT_METRICS_PORT", DEFAULT_METRICS_PORT),
        store_timeout_s=_float(source, "AMF_AUDIT_STORE_TIMEOUT_S", DEFAULT_STORE_TIMEOUT_S),
    )
    if config.period_ms <= 0:
        raise ValueError("AMF_AUDIT_EXPORT_PERIOD_MS must be positive")
    if config.store_timeout_s <= 0:
        raise ValueError("AMF_AUDIT_STORE_TIMEOUT_S must be positive")
    # Validated here so a bad knob fails at start-up rather than at the first trim.
    knobs_from_env(source)
    return config


def _default_id() -> str:
    return f"{socket.gethostname()}:{os.getpid()}"


class _Handler(BaseHTTPRequestHandler):
    """`/healthz` and `/metrics`. Nothing else, and nothing that takes a parameter."""

    service: ExporterService

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


class ExporterService:
    """One exporter process: a bounded loop, a metrics surface, and a clean stop."""

    def __init__(
        self,
        config: ExporterConfig,
        exporter: AuditExporter,
        sink: PostgresAuditSink,
    ) -> None:
        self._config = config
        self._exporter = exporter
        self._sink = sink
        self._recorder = AuditMetricsRecorder()
        self._stop = threading.Event()
        self._rounds = 0
        self._exported = 0
        self._failures = 0

    @classmethod
    def build(cls, config: ExporterConfig) -> ExporterService:
        """Wire the real adapters and PROVE the session bounds before the first round."""
        import redis.asyncio as aioredis

        from state_control.pg import PostgresControlDB

        database = PostgresControlDB(config.dsn)
        # R2-04 clause 1, verified rather than asserted. Raises ControlPlaneBoundsNotApplied.
        database.verify_bounds()
        sink = PostgresAuditSink(database)
        sink.init_schema()

        client = aioredis.Redis.from_url(
            config.valkey_url, socket_timeout=config.store_timeout_s,
        )
        require_bounded_client(client, below_s=config.store_timeout_s * 2)
        store = ValkeyAuditStore(client, StoreKeys(config.namespace))
        exporter = AuditExporter(
            store, sink, page=config.page, max_pages=config.max_pages,
        )
        return cls(config, exporter, sink)

    # --- the loop ------------------------------------------------------------------------------

    def round_once(self, orgs: Sequence[str]) -> RoundSummary:
        """One round over the named tenants, recorded. Never raises: a round may fail."""
        self._rounds += 1
        summary = asyncio.run(self._exporter.round_once(orgs))
        self._exported += summary.exported
        self._failures += summary.failures
        self._publish()
        return summary

    def _publish(self) -> None:
        """Roll the per-tenant cursors up into the label-free durability reading.

        O(tenants) and off every hot path. The rollup is what keeps the metric surface's
        cardinality fixed while the underlying question stays per tenant (R2-10).
        """
        try:
            cursors = self._sink.cursors()
        except Exception as exc:  # losing the metric must not stop the export
            LOG.warning(
                "audit_metrics_publish_failed error=%s",
                f"{type(exc).__name__}: {exc}"[:300],
            )
            return
        self._recorder.observe_durable(
            DurableReading(
                exported=self._exported,
                durable=sum(cursor.durable_records for cursor in cursors),
                records_lost=sum(cursor.records_lost for cursor in cursors),
                acknowledged_high_water=sum(
                    cursor.acknowledged_records for cursor in cursors
                ),
                durable_high_water=sum(cursor.durable_records for cursor in cursors),
                export_failures=self._failures,
            ),
        )

    def run(self, orgs: Sequence[str]) -> int:
        """Loop until stopped. Returns a process exit code."""
        config = self._config
        LOG.info(
            "audit exporter %s starting in zone=%s: period=%dms page=%d max_pages=%d "
            "namespace=%s tenants=%d",
            config.exporter_id,
            config.zone,
            config.period_ms,
            config.page,
            config.max_pages,
            config.namespace,
            len(orgs),
        )
        server = self._serve_metrics()
        try:
            while not self._stop.is_set():
                started = time.monotonic()
                try:
                    self.round_once(orgs)
                except Exception as exc:  # a round may fail; the loop may not
                    self._failures += 1
                    LOG.warning(
                        "audit_export_round_failed error=%s",
                        f"{type(exc).__name__}: {exc}"[:300],
                    )
                # Sleep the REMAINDER of the period, so a slow round does not push the cadence
                # out: the export has to keep pace with the trim, not with its own latency.
                elapsed = time.monotonic() - started
                self._stop.wait(max(config.period_s - elapsed, 0.0))
        finally:
            if server is not None:
                server.shutdown()
                server.server_close()
            LOG.info("audit exporter %s stopped", config.exporter_id)
        return 0

    def stop(self) -> None:
        self._stop.set()

    def metrics(self) -> Any:
        return self._recorder.snapshot()

    def _serve_metrics(self) -> ThreadingHTTPServer | None:
        """Bind `/metrics` and `/healthz`, or carry on without them.

        A failure to bind must not stop the exporter: losing observability is strictly better
        than losing the component that makes audit durable.
        """
        handler = type("_BoundHandler", (_Handler,), {"service": self})
        try:
            server = ThreadingHTTPServer(("", self._config.metrics_port), handler)
        except OSError as exc:
            LOG.warning(
                "could not bind the metrics surface on port %d (%s); the loop continues "
                "without it, but audit loss will be invisible to Prometheus",
                self._config.metrics_port,
                exc,
            )
            return None
        server.daemon_threads = True
        threading.Thread(
            target=server.serve_forever, name="amf-audit-metrics", daemon=True,
        ).start()
        LOG.info(
            "metrics on http://%s:%d/metrics", socket.gethostname(), self._config.metrics_port,
        )
        return server


def main(env: Mapping[str, str] | None = None) -> int:
    """Process entry point. `python -m audit_control`."""
    logging.basicConfig(
        level=(os.environ.get("AMF_LOG_LEVEL") or "INFO").upper(),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    source = os.environ if env is None else env
    config = from_env(source)
    service = ExporterService.build(config)
    for signalled in (signal.SIGTERM, signal.SIGINT):
        try:
            signal.signal(signalled, lambda *_: service.stop())
        except ValueError:  # pragma: no cover - not the main thread
            pass
    orgs = tuple(
        part.strip() for part in (source.get("AMF_AUDIT_ORGS") or "").split(",") if part.strip()
    )
    if not orgs:
        raise ValueError(
            "AMF_AUDIT_ORGS is required: the tenants whose audit streams to drain. Discovering "
            "them by scanning the store would be a KEYS/SCAN over the whole keyspace on every "
            "round, which is the shape R2-02 measured costing 405-459 ms at 25,000 tenants -- "
            "so the tenant list is configuration until the plan snapshot is wired in (GW05c)",
        )
    return service.run(orgs)
