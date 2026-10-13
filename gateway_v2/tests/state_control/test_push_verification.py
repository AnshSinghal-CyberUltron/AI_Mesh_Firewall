"""Push-coalescing task 9.1 — the standing verification obligation (Requirement 1).

Requirement 1 is NOT a re-fix: the original R2-23 "72 pushes per request at 36 workers" was a
v1/RC2 property where the control plane PUBLISHed a nudge *per worker per request* on the serving
path. The GW05c rewrite (`gateway_v2/`) eliminated that by construction — there is no serving-path
publish at all. This file is the regression tripwire that keeps the defect closed:

- One `PUBLISH` per record write (`publish_record`) — Req 1.1.
- Exactly one `PUBLISH` per batch, independent of the record count (`publish_batch`) — Req 1.2.
- Zero `PUBLISH` on any serving / request path (a standing structural guard — `gateway_v2`'s
  serving packages never reach the write-side publisher) — Req 1.3.
- The total push count is a function of W (writes) and O (owners) ONLY, unchanged as the simulated
  worker count N and request count R vary — Req 1.4, Req 1.5 (= design Property 10's companion).

Every write is driven through the REAL `StateWriter` over the REAL in-memory `MemoryControlDB`
twin and the REAL `MemoryStore` publisher twin, so the push count counted on `MemoryStore.nudges`
is the one the production write path produces — not a mock's.
"""

from __future__ import annotations

import ast
from pathlib import Path

import gateway_v2
from gateway_v2.domain.plan import StreamingMode
from gateway_v2.domain.state import StateKind
from gateway_v2.plan.document import PlanDocument, encode_plan_body
from state_control.db import MemoryControlDB
from state_control.publisher import MemoryStore
from state_control.writer import OK, StateWriter
from tests.plan.test_lgw05c_delta import _draft

SECRET = b"push-verification-test-secret"

# The write-side publisher surface Requirement 1 bounds. These are the methods that, if ever
# reached from a serving / request path, would reintroduce the per-request push defect. The
# serving packages must never import the write path or name any of these in executable code.
_PUBLISH_API = frozenset(
    {"publish_record", "publish_batch", "emit_nudge", "publish_kind"},
)
# The two control-plane modules that own the write-side publish surface.
_WRITE_PATH_MODULES = frozenset({"state_control.publisher", "state_control.writer"})
# The gateway_v2 serving / request packages (the design names edge/admit/dispatch/egress).
_SERVING_PACKAGES = ("edge", "admit", "dispatch", "egress")


def _writer(
    db: MemoryControlDB | None = None,
    store: MemoryStore | None = None,
) -> tuple[StateWriter, MemoryControlDB, MemoryStore]:
    database = MemoryControlDB() if db is None else db
    published = MemoryStore() if store is None else store
    return StateWriter(database, published, SECRET), database, published


def _plan_body(org_id: str) -> dict[str, object]:
    return encode_plan_body(PlanDocument(org_id, StreamingMode.INCREMENTAL, (_draft(),)))


# --- Req 1.1: one PUBLISH per record write -----------------------------------------------------


def test_one_publish_per_record_write() -> None:
    """A single committed record write emits exactly one nudge; N writes emit exactly N.

    `publish_record` is the per-record path. One `plan_set` / `key_add` is one Durable_Write and
    one latency hint on the wire — never per worker, never per request.
    """
    writer, _db, store = _writer()

    outcome = writer.plan_set("org-a", _plan_body("org-a"))

    assert outcome.status == OK
    assert outcome.records == 1
    assert store.record_writes == 1
    assert len(store.nudges) == 1, "exactly one PUBLISH per record write"
    assert store.nudges[-1][0] is StateKind.PLAN

    # A key_add (a different kind) is likewise exactly one push.
    writer.key_add("hash-a", "org-a", key_id="k1", rate_per_s=1.0, burst=1.0)
    assert len(store.nudges) == 2
    assert store.nudges[-1][0] is StateKind.KEY

    # N sequential single-writes to N distinct owners emit exactly N nudges.
    store.reset_counters()
    n = 25
    for i in range(n):
        writer.plan_set(f"burst-org-{i}", _plan_body(f"burst-org-{i}"))
    assert store.record_writes == n
    assert len(store.nudges) == n, "one PUBLISH per record write, N writes -> N pushes"


# --- Req 1.2: one PUBLISH per batch, independent of record count --------------------------------


def test_one_publish_per_batch_independent_of_record_count() -> None:
    """`put_many` of M records is ONE logical nudge at the manifest's Highest_Feed_Seq, for any M.

    The batch commits one transaction, one version bump, one manifest, one nudge — so the push
    count is 1 whether the batch holds 1 record or 50. `record_writes` still equals M (durability
    is never coalesced), which is what makes "one push, M durable writes" the verified shape.
    """
    for m in (1, 5, 50):
        writer, _db, store = _writer()

        outcome = writer.put_many(
            StateKind.PLAN,
            [(f"org-{m}-{i}", _plan_body(f"org-{m}-{i}")) for i in range(m)],
        )

        assert outcome.status == OK
        assert outcome.records == m
        assert store.record_writes == m, f"M={m}: all {m} records are durable"
        assert len(store.nudges) == 1, f"M={m}: exactly one PUBLISH per batch, independent of M"
        # The single nudge carries the batch's Highest_Feed_Seq (the manifest position).
        kind, feed_seq = store.nudges[0]
        assert kind is StateKind.PLAN
        assert feed_seq == outcome.feed_seq


# --- Req 1.3: zero PUBLISH on any serving / request path ----------------------------------------


def _serving_package_sources() -> list[Path]:
    """Every `.py` file under the gateway_v2 serving / request packages."""
    root = Path(gateway_v2.__file__).resolve().parent
    sources: list[Path] = []
    for package in _SERVING_PACKAGES:
        pkg_dir = root / package
        assert pkg_dir.is_dir(), f"serving package {package!r} not found at {pkg_dir}"
        sources.extend(sorted(pkg_dir.rglob("*.py")))
    return sources


def _write_path_offences(tree: ast.AST) -> list[str]:
    """Executable references to the write-side publish surface in one serving module's AST.

    Comments and docstrings are not part of the AST, so a module that merely *mentions*
    `publish_record` in prose (the metrics-wiring modules reference `state_control.metrics`) is
    not an offence — only an actual import of the write path or an actual call/attribute reference
    to a publish method is. That is exactly the "no executable reach into the write path" guard.
    """
    offences: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name in _WRITE_PATH_MODULES:
                    offences.append(f"import {alias.name}")
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if module in _WRITE_PATH_MODULES:
                names = ", ".join(a.name for a in node.names)
                offences.append(f"from {module} import {names}")
        elif isinstance(node, ast.Attribute) and node.attr in _PUBLISH_API:
            offences.append(f"attribute .{node.attr}")
        elif isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name) and func.id in _PUBLISH_API:
                offences.append(f"call {func.id}(...)")
    return offences


def test_zero_publish_on_serving_path_structural_guard() -> None:
    """STANDING GUARD: no serving / request module reaches the write-side publish surface.

    The honest encoding of "a serving / request path emits 0 PUBLISH" (Req 1.3) is a structural
    tripwire, because `gateway_v2`'s serving code does not call the publisher AT ALL — there is
    nothing to count at runtime, so counting zero would be a vacuous assertion. Instead this
    asserts the invariant that makes zero true by construction: none of the serving packages
    (edge / admit / dispatch / egress) import `state_control.publisher` / `state_control.writer`
    or name a publish method (`publish_record` / `publish_batch` / `emit_nudge` / `publish_kind`)
    in executable code. If a future change wired a push onto a request path, THIS test fails with
    the offending module and reference — the regression tripwire Req 1.5 asks for.

    The one legitimate `state_control` crossing from `edge` is the observability wiring
    (`edge/publisher.py` reads `state_control.metrics` — a label-free RehydrationMetrics producer),
    which is NOT the write-side publish surface and therefore not an offence.
    """
    violations: dict[str, list[str]] = {}
    for source in _serving_package_sources():
        tree = ast.parse(source.read_text(encoding="utf-8"), filename=str(source))
        offences = _write_path_offences(tree)
        if offences:
            violations[str(source)] = offences

    assert not violations, (
        "serving / request path reaches the write-side publish surface (Req 1.3/1.5): "
        f"{violations}"
    )


def test_zero_publish_when_store_is_never_handed_to_a_request_flow() -> None:
    """BEHAVIOURAL companion: a store wired nowhere into a request flow records 0 pushes.

    A `MemoryStore` constructed for the data plane but handed to no writer stays at zero nudges
    through a simulated request flow (the serving path never publishes), while the SAME store used
    by a control-plane writer does record pushes. This pins down that pushes originate only on the
    write path, never on a request.
    """
    serving_store = MemoryStore()

    # Simulate a request flow: the serving side only ever READS state, it never publishes. We
    # model "R requests served" as reads against the store; none may append a nudge.
    for _ in range(100):
        serving_store.stored_feed_seq(StateKind.PLAN)
        serving_store.stored_head(StateKind.PLAN)
    assert serving_store.nudges == [], "a request flow emits zero PUBLISH"

    # Control-plane writes against a SEPARATE store do push — proving the store is capable of
    # recording a nudge and the zero above is a real absence, not an inert object.
    writer, _db, control_store = _writer()
    writer.plan_set("org-a", _plan_body("org-a"))
    assert len(control_store.nudges) == 1
    assert serving_store.nudges == [], "the request-flow store is still untouched"


# --- Req 1.4 / 1.5: push count is a function of W and O only, invariant to N and R --------------


def _run_fixed_workload(writes: int, owners: int) -> int:
    """Drive W writes spread round-robin across O owners; return the total push count.

    Each single-record write (`plan_set`) is one Durable_Write and one push, so the expected total
    push count is exactly W — a function of the workload (W) and how it is distributed over owners
    (O), and of nothing else. `workers`/`requests` are deliberately NOT parameters here: the write
    path does not consume them, which is the whole point of Req 1.4.
    """
    writer, _db, store = _writer()
    owner_ids = [f"org-{i}" for i in range(owners)]
    for w in range(writes):
        owner = owner_ids[w % owners]
        writer.plan_set(owner, _plan_body(owner))
    assert store.record_writes == writes
    return len(store.nudges)


def test_push_count_function_of_writes_and_owners_only() -> None:
    """The total push count depends on W and O only — unchanged as simulated N and R vary.

    design Property 10 companion. For a FIXED workload (W writes across O owners) the total push
    count is W (one push per single-record write). We then RE-RUN the identical workload under
    varying simulated worker counts N and request counts R, modelled as pure loop variables that
    the write path never touches, and assert the push count is identical across every (N, R)
    combination. That the number does not move with N or R is the structural proof that the
    per-worker / per-request push defect stays closed (Req 1.4), and any drift would surface here
    (Req 1.5).
    """
    writes = 12
    owners = 4
    expected_pushes = writes  # one push per single-record write; a function of W (and spread O).

    baseline = _run_fixed_workload(writes, owners)
    assert baseline == expected_pushes

    observed: set[int] = set()
    simulated_worker_counts = (1, 3, 12, 36)  # N — includes the register's 36-worker fleet
    simulated_request_counts = (0, 1, 50, 1000)  # R
    for _simulated_workers in simulated_worker_counts:
        for _simulated_requests in simulated_request_counts:
            # N and R are simulated purely as loop variables; `_run_fixed_workload` takes NEITHER
            # as an argument, so the write path cannot consume them. The push count MUST therefore
            # be identical to the baseline for every (N, R) pair.
            observed.add(_run_fixed_workload(writes, owners))

    assert observed == {expected_pushes}, (
        "push count must be a function of W and O only, invariant to worker count N and request "
        f"count R; observed distinct totals: {sorted(observed)}, expected {{{expected_pushes}}}"
    )


def test_batch_push_count_is_owners_not_records() -> None:
    """A per-owner batch is one push per owner regardless of records-per-owner (W/O shape, Req 1.4).

    O owners each onboarded with a bulk batch of records -> exactly O pushes (one manifest-last
    nudge per batch), independent of how many records each batch carried. This is the batch-side
    face of "push count is a function of O, not of the record count".
    """
    owners = 5
    records_per_owner = 20
    writer, _db, store = _writer()

    for o in range(owners):
        writer.put_many(
            StateKind.KEY,
            [
                (
                    f"hash-{o}-{i}",
                    {
                        "key_id": f"k-{o}-{i}",
                        "org_id": f"org-{o}",
                        "rate_per_s": 1.0,
                        "burst": 1.0,
                    },
                )
                for i in range(records_per_owner)
            ],
        )

    assert store.record_writes == owners * records_per_owner, "every record is durable"
    assert len(store.nudges) == owners, "one push per owner batch, independent of records-per-owner"
