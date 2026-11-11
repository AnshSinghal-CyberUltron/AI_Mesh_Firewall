"""LGW12 DispatchRouter determinism unit tests (GW12, task 4.4).

``DispatchRouter.select`` (``gateway_v2.dispatch.routing``) is a pure function of
an ``ExecutionPlan``'s IDENTITY — ``org_id`` plus ``epoch.sequence.content_hash``
(R8.3). These tests pin that contract from three directions, each over a seeded
``random.Random`` sweep of generated plans (house idiom; no ``hypothesis``):

1. Idempotence: repeated ``select`` calls on the SAME plan object, and on a
   distinct plan with the same identity fields, yield the identical destination.
2. Identity-irrelevant fields: plans differing ONLY in ``compiled_at``,
   ``feed_seq``, ``streaming_mode`` or ``integrity_locked`` route to the SAME
   destination (those fields are deliberately excluded from the selection key).
3. Identity fields: plans differing in ``org_id``, ``epoch``, ``sequence`` or
   ``content_hash`` route to DIFFERENT destinations.

Test files are NOT under the import-linter layer contract, so constructing an
``ExecutionPlan`` from ``gateway_v2.domain`` here is allowed.

_Design: Components §4; Correctness Properties → Property 8 (routing half)._
_Validates: Requirements 8.3._
"""

from __future__ import annotations

import random

from gateway_v2.dispatch.routing import DispatchRouter
from gateway_v2.domain.plan import ExecutionPlan, StreamingMode

_ITERATIONS = 10_000

_STREAMING_MODES: tuple[StreamingMode, ...] = tuple(StreamingMode)


def _hex_hash(rng: random.Random) -> str:
    """A 64-char lowercase hex string, the shape a real ``content_hash`` carries."""
    return f"{rng.getrandbits(256):064x}"


def _random_plan(
    rng: random.Random,
    *,
    org_id: str | None = None,
    epoch: int | None = None,
    sequence: int | None = None,
    content_hash: str | None = None,
    compiled_at: float | None = None,
    streaming_mode: StreamingMode | None = None,
    integrity_locked: bool | None = None,
    feed_seq: int | None = None,
) -> ExecutionPlan:
    """Build an ``ExecutionPlan`` with random (or pinned) fields.

    Any argument left ``None`` is randomised; pass a value to hold a field
    fixed, which is how the three test shapes vary exactly one dimension while
    sweeping the others.
    """
    return ExecutionPlan(
        org_id=org_id if org_id is not None else f"org-{rng.getrandbits(32):08x}",
        epoch=epoch if epoch is not None else rng.randint(0, 1_000_000),
        sequence=sequence if sequence is not None else rng.randint(0, 1_000_000),
        content_hash=content_hash if content_hash is not None else _hex_hash(rng),
        compiled_at=compiled_at if compiled_at is not None else rng.random() * 1e9,
        rules=(),
        required_detectors=frozenset(),
        streaming_mode=(
            streaming_mode if streaming_mode is not None else rng.choice(_STREAMING_MODES)
        ),
        integrity_locked=(
            integrity_locked if integrity_locked is not None else bool(rng.getrandbits(1))
        ),
        feed_seq=feed_seq if feed_seq is not None else rng.randint(0, 1_000_000),
    )


def test_select_is_idempotent_for_identical_plan_identity() -> None:
    """Identical plan identity → identical destination, every call (R8.3).

    Covers both idempotence shapes: repeated ``select`` on one object, and a
    freshly built plan carrying the same identity fields (but independently
    randomised identity-irrelevant fields) routing to the same string.
    """
    seed = 0x120041
    rng = random.Random(seed)
    router = DispatchRouter()
    for i in range(_ITERATIONS):
        plan = _random_plan(rng)

        first = router.select(plan)
        # Repeated calls on the SAME object are stable.
        assert router.select(plan) == first, f"i={i} seed={seed:#x} non-idempotent on same object"

        # A DISTINCT plan with the SAME identity but independently randomised
        # identity-irrelevant fields routes to the same destination.
        twin = _random_plan(
            rng,
            org_id=plan.org_id,
            epoch=plan.epoch,
            sequence=plan.sequence,
            content_hash=plan.content_hash,
        )
        assert router.select(twin) == first, (
            f"i={i} seed={seed:#x} identity twin diverged: {router.select(twin)!r} != {first!r}"
        )


def test_identity_irrelevant_fields_do_not_change_destination() -> None:
    """compiled_at / feed_seq / streaming_mode / integrity_locked are excluded (R8.3).

    A plan and a sibling that differ ONLY in those four fields share the same
    identity (org_id, epoch, sequence, content_hash) and therefore the same
    destination.
    """
    seed = 0x120042
    rng = random.Random(seed)
    router = DispatchRouter()
    for i in range(_ITERATIONS):
        base = _random_plan(rng)
        # Perturb each identity-irrelevant field to a guaranteed-different value.
        sibling = _random_plan(
            rng,
            org_id=base.org_id,
            epoch=base.epoch,
            sequence=base.sequence,
            content_hash=base.content_hash,
            compiled_at=base.compiled_at + 1.0 + rng.random(),
            feed_seq=base.feed_seq + 1 + rng.randint(0, 1_000),
            streaming_mode=(
                StreamingMode.STRICT_WITHHOLD
                if base.streaming_mode is StreamingMode.INCREMENTAL
                else StreamingMode.INCREMENTAL
            ),
            integrity_locked=not base.integrity_locked,
        )
        assert router.select(sibling) == router.select(base), (
            f"i={i} seed={seed:#x} identity-irrelevant field changed destination"
        )


def test_distinct_identity_yields_distinct_destination() -> None:
    """Different org_id / epoch / sequence / content_hash → different destination (R8.3).

    Each iteration flips exactly one identity dimension to a value distinct from
    the base and asserts the destination changes, so no identity field is
    silently dropped from the selection key.
    """
    seed = 0x120043
    rng = random.Random(seed)
    router = DispatchRouter()
    for i in range(_ITERATIONS):
        base = _random_plan(rng)
        base_dest = router.select(base)

        which = rng.randint(0, 3)
        if which == 0:
            other = _random_plan(
                rng,
                org_id=base.org_id + "-x",
                epoch=base.epoch,
                sequence=base.sequence,
                content_hash=base.content_hash,
            )
        elif which == 1:
            other = _random_plan(
                rng,
                org_id=base.org_id,
                epoch=base.epoch + 1 + rng.randint(0, 1_000),
                sequence=base.sequence,
                content_hash=base.content_hash,
            )
        elif which == 2:
            other = _random_plan(
                rng,
                org_id=base.org_id,
                epoch=base.epoch,
                sequence=base.sequence + 1 + rng.randint(0, 1_000),
                content_hash=base.content_hash,
            )
        else:
            # A fresh content hash, guaranteed to differ from the base's.
            new_hash = _hex_hash(rng)
            while new_hash == base.content_hash:
                new_hash = _hex_hash(rng)
            other = _random_plan(
                rng,
                org_id=base.org_id,
                epoch=base.epoch,
                sequence=base.sequence,
                content_hash=new_hash,
            )

        assert router.select(other) != base_dest, (
            f"i={i} seed={seed:#x} which={which} distinct identity collided on {base_dest!r}"
        )
