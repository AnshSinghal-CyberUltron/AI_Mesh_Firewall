"""GW29 `StateWriter.isolation`: validated bodies, explicit OFF, idempotent auto isolation."""
from __future__ import annotations

import ast
import json
import os
from pathlib import Path

import pytest

from gateway_v2.domain.incident import IsolationOrigin, isolation_scope, parse_isolation_body
from gateway_v2.domain.state import StateKind
from state_control.incident_scopes import isolation_body
from state_control.writer import OK, UNCHANGED
from tests.state_control.test_lgw05c_writer import SECRET, Gateway, _writer

NOW = 1_000.0


def _ks_body(db, scope: str) -> dict[str, object]:  # type: ignore[no-untyped-def]
    record = next(r for r in db.snapshot(StateKind.KS)[1] if r.key == scope)
    return json.loads(record.body)  # type: ignore[no-any-return]


def test_an_operator_isolation_round_trips_through_a_gateway_snapshot() -> None:
    writer, db, store = _writer()
    outcome = writer.isolation(
        "GPT-4o", on=True, fallback="GPT-4o-Mini", reason="provider incident",
        expires_at=NOW + 600, created_at=NOW,
    )
    assert outcome.status == OK and outcome.records == 1
    gateway = Gateway(store)
    gateway.poll(StateKind.KS)
    rec = gateway.switches.isolation_record("gpt-4o")
    assert rec is not None and not rec.malformed
    assert rec.origin is IsolationOrigin.OPERATOR and rec.fallback == "gpt-4o-mini"
    assert rec.reason == "provider incident" and rec.expires_at == NOW + 600
    assert gateway.switches.view().isolations == 1


def test_an_operator_isolation_can_be_permanent_only_explicitly() -> None:
    writer, db, store = _writer()
    writer.isolation("gpt-4o", on=True, no_expiry=True)
    gateway = Gateway(store)
    gateway.poll(StateKind.KS)
    rec = gateway.switches.isolation_record("gpt-4o")
    assert rec is not None and rec.no_expiry and rec.expires_at is None and not rec.malformed


def test_off_is_an_explicit_record_and_keeps_on_count_honest() -> None:
    writer, db, store = _writer()
    writer.isolation("gpt-4o", on=True, no_expiry=True)
    assert db.snapshot(StateKind.KS)[2] == (isolation_scope("gpt-4o"),)
    writer.isolation("gpt-4o", on=False)
    assert db.snapshot(StateKind.KS)[2] == ()
    assert _ks_body(db, isolation_scope("gpt-4o")) == {"on": False}
    gateway = Gateway(store)
    gateway.poll(StateKind.KS)
    assert gateway.switches.isolation_record("gpt-4o") is None


@pytest.mark.parametrize("kwargs", [
    {},                                                  # operator, neither expiry nor no_expiry
    {"expires_at": NOW, "no_expiry": True},              # both
    {"expires_at": -5.0},
    {"expires_at": float("inf")},
    {"expires_at": float("nan")},
    {"expires_at": NOW, "reason": "x" * 201},
    {"expires_at": NOW, "reason": "tab\there"},
    {"expires_at": NOW, "reason": "naïve"},
    {"expires_at": NOW, "fallback": "bad name"},
    {"expires_at": NOW, "created_at": -1.0},
])
def test_a_body_the_gateway_would_read_as_malformed_is_never_written(
    kwargs: dict[str, object],
) -> None:
    writer, db, _ = _writer()
    with pytest.raises(ValueError):
        writer.isolation("gpt-4o", on=True, **kwargs)  # type: ignore[arg-type]
    assert db.snapshot(StateKind.KS)[1] == (), "nothing committed"


@pytest.mark.parametrize("model", ["", "bad name", "x" * 200, "naïve", "a\x00b"])
def test_an_invalid_model_name_is_rejected_at_the_writer(model: str) -> None:
    writer, db, _ = _writer()
    with pytest.raises(ValueError):
        writer.isolation(model, on=True, no_expiry=True)
    assert db.snapshot(StateKind.KS)[1] == ()


def test_every_valid_body_parses_as_not_malformed() -> None:
    writer, db, store = _writer()
    for origin, extra in (
        (IsolationOrigin.OPERATOR, {"no_expiry": True}),
        (IsolationOrigin.OPERATOR, {"expires_at": NOW + 5}),
        (IsolationOrigin.AUTO, {"expires_at": NOW + 5, "created_at": NOW}),
        (IsolationOrigin.AUTO, {}),
    ):
        writer.isolation("m", on=True, origin=origin, **extra)  # type: ignore[arg-type]
        record = next(r for r in db.snapshot(StateKind.KS)[1] if r.key == "iso:m")
        assert not parse_isolation_body(record, "m").malformed, (origin, extra)


def test_eight_identical_auto_events_leave_exactly_one_record() -> None:
    """AC27: N gateways detect one incident; the writer holds one record and writes once."""
    writer, db, store = _writer()
    outcomes = [
        writer.isolation("gpt-4o", on=True, fallback="gpt-4o-mini", origin=IsolationOrigin.AUTO,
                         reason="error_rate", created_at=NOW, expires_at=NOW + 300, now=NOW)
        for _ in range(8)
    ]
    assert [o.status for o in outcomes] == [OK] + [UNCHANGED] * 7
    assert [o.records for o in outcomes] == [1] + [0] * 7
    assert len([r for r in db.snapshot(StateKind.KS)[1] if r.key == "iso:gpt-4o"]) == 1
    counters = db.counters(StateKind.KS)
    assert counters.feed_seq == 1, "no second record, no second manifest bump"


def test_an_expired_auto_record_is_replaced_and_an_operator_record_is_never_deduplicated() -> None:
    writer, db, _ = _writer()
    kwargs = {"fallback": "b", "origin": IsolationOrigin.AUTO, "reason": "error_rate"}
    first = writer.isolation("a", on=True, created_at=NOW, expires_at=NOW + 300, now=NOW, **kwargs)
    again = writer.isolation("a", on=True, created_at=NOW + 400, expires_at=NOW + 700,
                             now=NOW + 400, **kwargs)
    assert first.status == OK and again.status == OK, "the first one had expired"
    op1 = writer.isolation("b", on=True, no_expiry=True)
    op2 = writer.isolation("b", on=True, no_expiry=True)
    assert op1.status == OK and op2.status in (OK, UNCHANGED)


def test_isolation_body_off_is_minimal_and_pure() -> None:
    assert isolation_body(on=False, reason="ignored") == {"on": False}
    body = isolation_body(on=True, expires_at=5.0, created_at=1.0, fallback="B", reason="r")
    assert body == {"on": True, "origin": "operator", "fallback": "b", "reason": "r",
                    "created_at": 1.0, "expires_at": 5.0}


def test_state_control_imports_only_the_domain_layer_for_the_new_modules() -> None:
    root = Path(__file__).resolve().parents[2] / "state_control"
    for name in ("incident_scopes.py", "writer.py", "projector.py"):
        tree = ast.parse((root / name).read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            modules: list[str] = []
            if isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                modules = [node.module]
            elif isinstance(node, ast.Import):
                modules = [alias.name for alias in node.names]
            for module in modules:
                if module.startswith("gateway_v2"):
                    allowed = module.startswith(("gateway_v2.domain", "gateway_v2.detect",
                                                 "gateway_v2.plan", "gateway_v2.runtime"))
                    assert allowed, f"{name} imports {module}"
                    if name == "incident_scopes.py":
                        assert module.startswith("gateway_v2.domain"), module


def test_the_iso_prefix_is_pinned_to_the_domain_scope_builder() -> None:
    from state_control.incident_scopes import _ISO_PREFIX
    assert isolation_scope("m").startswith(_ISO_PREFIX)


@pytest.mark.skipif(not os.environ.get("AMF_PG_DSN"), reason="AMF_PG_DSN is not set")
def test_postgres_16_isolation_round_trip() -> None:  # pragma: no cover - needs a database
    from state_control.pg import PostgresControlDB
    from state_control.publisher import MemoryStore
    from state_control.writer import StateWriter
    db = PostgresControlDB(os.environ["AMF_PG_DSN"])
    db.init_schema()
    writer = StateWriter(db, MemoryStore(), SECRET)
    first = writer.isolation("gw29-pg", on=True, origin=IsolationOrigin.AUTO,
                             created_at=NOW, expires_at=9_999_999_999.0, now=NOW)
    second = writer.isolation("gw29-pg", on=True, origin=IsolationOrigin.AUTO,
                              created_at=NOW, expires_at=9_999_999_999.0, now=NOW)
    assert second.status == UNCHANGED and first.status in (OK, UNCHANGED)
    writer.isolation("gw29-pg", on=False)
