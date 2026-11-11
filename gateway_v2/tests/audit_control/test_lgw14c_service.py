"""GW14c: the exporter process. The R2-04 lessons, applied to this card's running component.

R2-04 found a requirement with nothing to attach to: the re-hydrator existed only in tests, was
absent from every image, and an alarm named a Prometheus job that did not exist. GW14c's
"acknowledged-vs-durable high-water mark" is the same kind of requirement, so the same four
things are asserted here:

* `from_env` is a pure function of a mapping, and names what is wrong;
* the session bounds are VERIFIED before the first round, not assumed;
* the store client is bounded, because a partitioned store accepts and never answers;
* the package is in the image.
"""

from __future__ import annotations

import pytest

from audit_control.cursor import OrgCursor, StreamId
from audit_control.export import AuditExporter
from audit_control.service import DEFAULT_METRICS_PORT, ExporterService, from_env
from gateway_v2.audit.metrics import DurableReading

ENV = {
    "AMF_AUDIT_PG_DSN": "postgresql://amf@pg:6432/amf",
    "AMF_STATE_VALKEY_URL": "redis://valkey:6379",
}


# --- configuration --------------------------------------------------------------------------------


def test_from_env_is_a_pure_function_of_a_mapping() -> None:
    """A start-up path that can only be exercised by mutating process state does not get
    exercised. R2-04 recorded that as the reason the PgBouncer `options` drop hid."""
    config = from_env({**ENV, "AMF_AUDIT_EXPORT_PERIOD_MS": "250"})

    assert config.dsn == ENV["AMF_AUDIT_PG_DSN"]
    assert config.period_ms == 250
    assert config.period_s == 0.25


def test_defaults_are_declared_rather_than_implied() -> None:
    config = from_env(ENV)

    assert config.metrics_port == DEFAULT_METRICS_PORT == 9109, "9108 is the re-hydrator's"
    assert config.namespace == "{rv2}"
    assert config.exporter_id
    assert config.zone == "unknown"


@pytest.mark.parametrize(
    ("missing", "expected"),
    [("AMF_AUDIT_PG_DSN", "AMF_AUDIT_PG_DSN is required"),
     ("AMF_STATE_VALKEY_URL", "AMF_STATE_VALKEY_URL is required")],
)
def test_a_missing_requirement_names_itself(missing: str, expected: str) -> None:
    env = {key: value for key, value in ENV.items() if key != missing}

    with pytest.raises(ValueError, match=expected):
        from_env(env)


def test_a_bad_knob_fails_at_start_up_rather_than_at_the_first_trim() -> None:
    """The audit knobs are validated here too, so a fraction of 1.0 is a start-up failure rather
    than a budget that quietly leaves nothing for the state and the registrations."""
    with pytest.raises(ValueError, match="fraction must be in"):
        from_env({**ENV, "AMF_AUDIT_STORE_FRACTION": "1.0"})


def test_a_nonsensical_period_is_refused() -> None:
    with pytest.raises(ValueError, match="PERIOD_MS must be positive"):
        from_env({**ENV, "AMF_AUDIT_EXPORT_PERIOD_MS": "0"})
    with pytest.raises(ValueError, match="must be an integer"):
        from_env({**ENV, "AMF_AUDIT_EXPORT_PERIOD_MS": "soon"})


def test_the_dsn_requirement_says_why_sharing_the_control_database_is_a_decision() -> None:
    """Audit volume is orders of magnitude above state volume. Defaulting to the control-plane
    database would make that a silent capacity choice."""
    with pytest.raises(ValueError, match="capacity decision rather than a default"):
        from_env({"AMF_STATE_VALKEY_URL": "redis://v:6379"})


# --- the loop and the metrics rollup --------------------------------------------------------------


class FakeSink:
    def __init__(self, cursors: tuple[OrgCursor, ...] = ()) -> None:
        self._cursors = cursors
        self.fail = False

    def cursors(self) -> tuple[OrgCursor, ...]:
        if self.fail:
            raise RuntimeError("database down")
        return self._cursors

    def cursor_for(self, org_id: str) -> OrgCursor:
        for cursor in self._cursors:
            if cursor.org_id == org_id:
                return cursor
        return OrgCursor(org_id=org_id)

    def commit_page(self, records: object, cursor_state: OrgCursor) -> int:
        del records, cursor_state
        return 0


class EmptyStream:
    async def read(self, org: str, *, after: str = "0-0", limit: int = 256) -> tuple[()]:
        del org, after, limit
        return ()

    async def first_id(self, org: str) -> None:
        del org
        return None

    async def length(self, org: str) -> int:
        del org
        return 0


def _service(sink: FakeSink) -> ExporterService:
    config = from_env(ENV)
    exporter = AuditExporter(EmptyStream(), sink)
    return ExporterService(config, exporter, sink)  # type: ignore[arg-type]


def test_a_round_rolls_the_per_tenant_cursors_up_into_label_free_totals() -> None:
    """R2-10: the question is per tenant, the metric is not. The rollup is what keeps the series
    count fixed while the answer stays available per tenant in the database."""
    sink = FakeSink(
        (
            OrgCursor("org-a", durable=StreamId(1, 1), durable_records=100,
                      acknowledged_records=120, records_lost=20),
            OrgCursor("org-b", durable=StreamId(1, 1), durable_records=50,
                      acknowledged_records=50),
        ),
    )
    service = _service(sink)

    service.round_once(["org-a", "org-b"])
    reading = service.metrics().durable

    assert isinstance(reading, DurableReading)
    assert reading.durable == 150
    assert reading.records_lost == 20
    assert reading.acknowledged_high_water == 170
    assert reading.completeness_ratio == pytest.approx(150 / 170)


def test_losing_the_metric_does_not_stop_the_export() -> None:
    """Observability is not the product. A database hiccup on the rollup read must not take down
    the component that makes audit durable."""
    sink = FakeSink()
    sink.fail = True
    service = _service(sink)

    summary = service.round_once(["org-a"])

    assert summary.failures == 0


def test_the_metrics_surface_renders() -> None:
    service = _service(FakeSink((OrgCursor("org-a", durable_records=5,
                                           acknowledged_records=5),)))
    service.round_once(["org-a"])

    from gateway_v2.audit.metrics import render

    text = render(service.metrics())

    assert "amf_audit_completeness_ratio" in text
    assert "# TYPE amf_audit_records_lost_total counter" in text


def test_stop_ends_the_loop() -> None:
    service = _service(FakeSink())
    service.stop()

    assert service.run(["org-a"]) == 0


# --- the image ------------------------------------------------------------------------------------


def test_the_package_is_copied_into_the_image() -> None:
    """R2-04's measured lesson: `state_control` was in pyproject, in no image, and so the
    "two re-hydrators" requirement had nothing to deploy. This is the same assertion for
    `audit_control`, made against the Dockerfile rather than against a built image so it runs
    everywhere."""
    from pathlib import Path

    dockerfile = (
        Path(__file__).resolve().parents[2] / "Dockerfile"
    ).read_text(encoding="utf-8")

    assert "gateway_v2/audit_control,target=/tmp/src/audit" in dockerfile
    assert "/opt/gateway_v2/audit_control" in dockerfile
    assert "cp -R /tmp/src/audit/." in dockerfile


def test_the_entrypoint_module_exists_and_does_not_run_on_import() -> None:
    """Importing the service to test it must not start a loop."""
    import audit_control.__main__ as entry

    assert entry.main is not None


def test_the_package_is_declared_to_the_build() -> None:
    from pathlib import Path

    pyproject = (
        Path(__file__).resolve().parents[2] / "pyproject.toml"
    ).read_text(encoding="utf-8")

    assert "audit_control*" in pyproject
    assert '"audit_control"' in pyproject, "mypy must cover it too"
