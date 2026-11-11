# Validates: AC11, AC12, C2, D11 (the in-flight control source)
"""``KillSwitchControlSource`` / ``RefusingControlSource`` / ``DevNoControlSource`` semantics."""

from __future__ import annotations

from gateway_v2.admit.revocation import RevocationLedger
from gateway_v2.domain import posture
from gateway_v2.domain.incident import StreamBinding
from gateway_v2.domain.state import StateKind, StateOp, Version
from gateway_v2.edge import inflight_source as src
from gateway_v2.edge.cancel import InFlightControl, KillLatch
from gateway_v2.runtime.state_sig import make_record
from tests.admit._lgw29_support import SECRET, GateRig, ks
from tests.edge._lgw29_app import contract

ORG, MODEL = "o", "m"


def _binding(feed_seq: int = 0) -> StreamBinding:
    return StreamBinding(org_id=ORG, model=MODEL, key_hash="kh", key_feed_seq=feed_seq)


def _rig() -> tuple[GateRig, RevocationLedger, src.KillSwitchControlSource]:
    rig = GateRig()
    ledger = RevocationLedger(clock=rig.mono, slots=8, retention_s=600.0)
    return rig, ledger, src.KillSwitchControlSource(rig.snap, ledger, _binding())


def _control(source: object) -> tuple[InFlightControl, KillLatch]:
    latch = KillLatch()
    control = InFlightControl(
        source=source,
        contract=contract(),
        clock=lambda: 0.0,
        kill_latch=latch,  # type: ignore[arg-type]
    )
    return control, latch


def test_a_fresh_snapshot_reports_age_zero_so_the_20ms_bound_never_cuts_a_5s_old_snapshot() -> None:
    """C2: staleness is judged ONCE, by the snapshot; ``InFlightControl`` must not re-judge it."""
    rig, _ledger, source = _rig()
    rig.mono.now += 4.9  # inside FRESH_MS (5 s), far above max_snapshot_age_s (~20 ms)
    snap = source.snapshot()
    assert snap.available and snap.age_s == 0.0 and not snap.kill_switch_engaged
    control, _latch = _control(source)
    assert control.evaluate() is None


def test_a_stale_snapshot_is_unavailable_and_cuts_with_snapshot_stale() -> None:
    rig, _ledger, source = _rig()
    rig.mono.now += 6.0
    control, _latch = _control(source)
    assert control.evaluate() == posture.STREAM_SNAPSHOT_STALE


def test_every_kill_scope_is_visible_in_the_same_poll_that_the_snapshot_applied_it() -> None:
    for scope in ("global", f"org:{ORG}", f"om:{ORG}:{MODEL}", f"model:{MODEL}"):
        rig, ledger, source = _rig()
        assert not source.snapshot().kill_switch_engaged
        rig.engage(ks(scope, feed_seq=1))  # no clock advance: visibility is immediate (P8b)
        assert source.snapshot().kill_switch_engaged, scope
        control, _latch = _control(source)
        assert control.evaluate() == posture.STREAM_KILLED
        del ledger


def test_other_orgs_and_models_do_not_cut_this_stream() -> None:
    rig, _ledger, source = _rig()
    rig.engage(
        ks("org:x", feed_seq=1), ks("model:other", feed_seq=2), ks(f"om:x:{MODEL}", feed_seq=3)
    )
    assert not source.snapshot().kill_switch_engaged


def test_a_revocation_after_admission_cuts_and_one_before_it_does_not() -> None:
    rig, ledger, _ = _rig()
    source = src.KillSwitchControlSource(rig.snap, ledger, _binding(feed_seq=10))
    revoke = lambda seq: make_record(  # noqa: E731
        SECRET,
        StateKind.KEY,
        "kh",
        {"k": "kh"},
        Version(1, 9_000 - seq),
        seq,
        deleted=True,
        op=StateOp.REVOKE,
    )
    ledger.observe([revoke(5)])
    assert not source.snapshot().key_revoked
    ledger.observe([revoke(11)])
    assert source.snapshot().key_revoked
    control, _latch = _control(source)
    assert control.evaluate() == posture.STREAM_KEY_REVOKED


def test_a_source_that_raises_reports_unavailable() -> None:
    class Boom:
        def evaluate(self, *_a: object, **_k: object) -> object:
            raise RuntimeError("no")

    source = src.KillSwitchControlSource(Boom(), None, _binding())  # type: ignore[arg-type]
    assert not source.snapshot().available


def test_the_unwired_and_unbound_sources_fail_closed_but_dev_never_cuts() -> None:  # AC12
    refusing = src.RefusingControlSource().snapshot()
    assert not refusing.available
    rig, ledger, _ = _rig()
    assert not src.KillSwitchControlFactory(rig.snap, ledger).for_stream(None).snapshot().available
    bound = src.KillSwitchControlFactory(rig.snap, ledger).for_stream(_binding())
    assert isinstance(bound, src.KillSwitchControlSource) and bound.snapshot().available
    dev = src.DevNoControlSource().snapshot()
    assert dev.available and not dev.kill_switch_engaged and not dev.key_revoked


def test_plan_change_is_explicitly_not_wired() -> None:  # D11
    assert src.PLAN_CHANGED_WIRED is False
    rig, ledger, source = _rig()
    assert source.snapshot().plan_changed is False
    assert src.DevNoControlSource().snapshot().plan_changed is False
    assert src.RefusingControlSource().snapshot().plan_changed is False
    del rig, ledger


def test_build_app_defaults_to_the_refusing_source_not_the_dev_one() -> None:
    import importlib
    import inspect

    app = importlib.import_module("gateway_v2.edge.app")
    source = inspect.getsource(app._build_chat_route)
    assert "RefusingControlSource()" in source and "DevNoControlSource" not in source
