"""GW36 S2 — the projector process surface and the knobs it shares with the re-hydrators."""

from __future__ import annotations

import pytest

from gateway_v2.domain.locks import PG_GRACE_MS
from gateway_v2.domain.state_knobs import ProjectorKnobs
from state_control import projector_service, service
from state_control.metrics import SERIES_COUNT as REHYDRATION_SERIES
from state_control.metrics import RehydrationMetricsRecorder
from state_control.projector import ProjectionSummary
from state_control.rehydrate import RoundSummary

BASE = {
    "AMF_STATE_PG_DSN": "postgresql://x@y/z",
    "AMF_STATE_VALKEY_URL": "redis://r:6379/0",
    "AMF_STATE_HMAC_KEY": "k",
}


@pytest.mark.parametrize(
    ("kwargs", "match"),
    [
        ({"period_ms": 0}, "positive"),
        ({"lease_ms": 1_500}, "at least 2 x"),
        ({"lag_ms": 5_999}, "lease_ms \\+ period_ms"),
        ({"lag_ms": -1}, "negative"),
        ({"add_chunks_per_round": 0}, ">= 1"),
    ],
)
def test_projector_knobs_refuse_incoherent_values(kwargs: dict[str, int], match: str) -> None:
    with pytest.raises(ValueError, match=match):
        ProjectorKnobs(**kwargs)


def test_lag_zero_is_a_loud_break_glass() -> None:
    assert ProjectorKnobs(lag_ms=0).warnings
    assert not ProjectorKnobs().warnings


def test_the_rehydrator_enforces_the_heartbeat_by_default_from_env() -> None:
    config = service.from_env(BASE)
    assert config.projector.lag_ms == PG_GRACE_MS
    off = service.from_env({**BASE, "AMF_PROJECTOR_LAG_MS": "0"})
    assert off.projector.lag_ms == 0


def test_a_directly_built_config_does_not_enforce_it() -> None:
    """Configs built in tests and benches predate the projector and must not change behaviour."""
    assert service.ServiceConfig.__dataclass_fields__["projector"].default == ProjectorKnobs(
        lag_ms=0,
    )


def test_the_projector_reads_the_same_knobs_and_names_itself() -> None:
    config = projector_service.from_env({**BASE, "AMF_PROJECTOR_ID": "projector-a"})
    assert config.holder == "projector-a"
    assert config.knobs == service.projector_knobs_from_env(BASE)
    with pytest.raises(ValueError, match="AMF_STATE_HMAC_KEY"):
        projector_service.from_env({k: v for k, v in BASE.items() if k != "AMF_STATE_HMAC_KEY"})


def test_projector_series_are_a_fixed_set() -> None:
    metrics = projector_service.ProjectorMetrics()
    assert len(metrics.series()) == projector_service.SERIES_COUNT
    lead = ProjectionSummary(leader=True, heartbeat=True, complete=True, writes=3, revokes=1)
    folded = projector_service.observe(metrics, lead, duration_s=0.2, now=50.0)
    assert len(folded.series()) == projector_service.SERIES_COUNT
    assert folded.leader == 1 and folded.writes == 3 and folded.last_heartbeat_at == 50.0
    standby = projector_service.observe(
        folded, ProjectionSummary(leader=False), duration_s=0.01, now=51.0,
    )
    assert standby.leader == 0 and standby.writes == 3
    failed = projector_service.observe(standby, None, duration_s=0.01, now=52.0)
    assert failed.failures == 1


def test_rehydration_series_count_includes_the_lag_pair() -> None:
    recorder = RehydrationMetricsRecorder()
    recorder.observe_round(
        RoundSummary(repairs=(), healthy=(), errors=(), source_lag_s=20.0, source_lagging=True),
        duration_s=0.1,
        now=1.0,
    )
    series = recorder.snapshot().series()
    assert len(series) == REHYDRATION_SERIES
    assert series["amf_state_stamp_withheld_source_lag_total"] == 1
    assert series["amf_state_source_lag_seconds"] == 20.0


def test_healthz_means_the_loop_is_alive_not_that_it_leads() -> None:
    config = projector_service.from_env(BASE)

    class _Standby:
        def round_once(self) -> ProjectionSummary:
            return ProjectionSummary(leader=False)

    svc = projector_service.ProjectorService(config, _Standby())  # type: ignore[arg-type]
    svc.round_once()
    assert svc.alive(), "a healthy standby must not be restarted by its orchestrator"
    svc.stop()
    assert not svc.alive()
