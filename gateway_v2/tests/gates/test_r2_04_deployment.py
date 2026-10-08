"""R2-04 deployment gate: two re-hydrators, two zones, and an idle-stop exemption that holds.

The runbook's R2-04 row ends with two clauses that are not about code:

> runs >= 2 re-hydrators in >= 2 zones (proven safe concurrently); and never lets an idle-stop
> policy touch them

Those have been prose in three places -- the runbook row, the GW05b handoff §2, and an alert
annotation -- and prose does not fail a build. GW05c is the precedent: `require_bounded_client`
shipped with a docstring explaining exactly where it belonged, nobody wired it, and an entire card
went past. So the deployment facts are asserted here against the compose file itself.

What this gate does NOT claim: that one host provides zone redundancy. It cannot. `zone-a` and
`zone-b` on a single machine are labels, and the cloud spec is §13.3 of
`docs/plans/2026-10-08-r2-04-gw05-rehydrator-lock-and-pg-hardening.md`. What is asserted is that
the deployment DECLARES two instances with distinct identities and that both are exempt from the
idle-stop policy R2-24 makes mandatory -- which is the half that the 09:06Z ledger incident turned
on.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
COMPOSE = REPO / "docker-compose.yml"
DOCKERFILE = REPO / "gateway_v2" / "Dockerfile"
ALERTS = REPO / "deploy" / "observability" / "gw05b-state-freshness-alerts.yml"

SERVICES = ("state-rehydrator-a", "state-rehydrator-b")
EXEMPTION = 'ai-mesh.idle-stop: "exempt"'


@pytest.fixture(scope="module")
def compose() -> str:
    assert COMPOSE.is_file(), f"{COMPOSE} is missing"
    return COMPOSE.read_text(encoding="utf-8")


SECTION_MARKER = "# --- GW05b / R2-04: control-plane state re-hydrators"


def _service_block(compose: str, name: str) -> str:
    """The lines of one service, from its key to the next top-level service key."""
    start = compose.index(f"\n  {name}:")
    following = re.search(r"\n  [a-z0-9][a-z0-9-]*:|\nvolumes:", compose[start + 1 :])
    end = len(compose) if following is None else start + 1 + following.start()
    return compose[start:end]


def _section(compose: str) -> str:
    """The whole re-hydrator section, header comment included.

    The rationale comments that apply to BOTH instances sit above the first service key, so a
    per-service block would miss them -- and the rationale is the part that has to survive, since
    it is what stops the next reader from deleting one instance or writing an activity-based
    watchdog.
    """
    start = compose.index(SECTION_MARKER)
    return compose[start : compose.index("\nvolumes:", start)]


def test_two_rehydrators_are_declared(compose: str) -> None:
    """One is a single point of GLOBAL non-enforcement: when it stops, nothing stamps and every
    gateway fails closed at the freshness bound.
    """
    for name in SERVICES:
        assert f"\n  {name}:" in compose, f"{name} is not declared in docker-compose.yml"


def test_both_run_the_rehydrator_entrypoint(compose: str) -> None:
    """A service that exists but runs the gateway would satisfy a grep and nothing else."""
    for name in SERVICES:
        block = _service_block(compose, name)
        inherits = "<<: *state-rehydrator" in block
        assert inherits or "python" in block and "state_control" in block, (
            f"{name} does not run `python -m state_control`"
        )


def test_the_two_instances_have_distinct_identities(compose: str) -> None:
    """`AMF_REHYDRATOR_ID` travels in the stamp's `by` field. Two instances sharing one id make
    the field useless for telling an operator which re-hydrator last verified.
    """
    ids = re.findall(r"AMF_REHYDRATOR_ID:\s*(\S+)", compose)
    zones = re.findall(r"AMF_DEPLOY_ZONE:\s*(\S+)", compose)

    assert len(ids) >= 2, "each re-hydrator needs its own AMF_REHYDRATOR_ID"
    assert len(set(ids)) == len(ids), f"re-hydrator ids are not distinct: {ids}"
    assert len(set(zones)) >= 2, f"at least two zones must be declared, got {zones}"


@pytest.mark.parametrize("name", SERVICES)
def test_each_rehydrator_is_exempt_from_idle_stop(compose: str, name: str) -> None:
    """R2-24 makes an idle watchdog MANDATORY, so the exemption is the only way to satisfy
    R2-04's "never lets an idle-stop policy touch them".

    Round 2's ledger records the cost of getting this wrong: at 09:06Z an idle watchdog stopped
    `rv-r2state-cp-1`, which happened to host the only re-hydrator.
    """
    block = _service_block(compose, name)

    assert EXEMPTION in block, (
        f"{name} is missing {EXEMPTION}. R2-04 requires that no idle-stop policy can remove a "
        "re-hydrator, and the watchdog can only honour an exemption it can see."
    )
    assert 'ai-mesh.role: "control-plane-rehydrator"' in block


@pytest.mark.parametrize("name", SERVICES)
def test_each_rehydrator_restarts_and_is_health_checked(compose: str, name: str) -> None:
    """A component the fleet's availability depends on must come back by itself.

    BACKSTOP CHG-0027 set this precedent after the gateway was found with neither.
    """
    block = _service_block(compose, name)
    inherits = "<<: *state-rehydrator" in block

    assert inherits or "restart: unless-stopped" in block
    assert inherits or "healthcheck:" in block


def test_the_exemption_is_identity_based_not_activity_based(compose: str) -> None:
    """The subtle half, and the one that would quietly fail.

    A re-hydrator round is O(1) per kind by GW05c's design -- four indexed counter reads and four
    O(1) store head reads per second. Any CPU, request-rate or connection-count heuristic will
    therefore classify a perfectly healthy re-hydrator as idle, CORRECTLY, and stop it. The
    comment block must say so, because the next person to write the watchdog needs to know that
    "exclude anything that looks busy" is not a valid implementation.
    """
    block = _service_block(compose, "state-rehydrator-a")

    assert "never on activity" in block, (
        "the idle-stop exemption must document that it keys on identity, not activity"
    )


def test_the_single_host_zone_caveat_is_recorded(compose: str) -> None:
    """Honesty gate. `zone-a`/`zone-b` on one machine is not zone redundancy, and a reader who
    concludes otherwise would believe R2-04's HA clause is closed when it is not.
    """
    section = _section(compose)

    assert "NOT ISOLATION" in section
    assert "must not be described as doing so" in section


def test_state_control_is_in_the_image() -> None:
    """Until R2-04 the Dockerfile copied only `gateway_v2/gateway_v2`, so no image could run a
    re-hydrator even though pyproject already packaged `state_control` and declared psycopg.
    """
    dockerfile = DOCKERFILE.read_text(encoding="utf-8")

    assert "source=gateway_v2/state_control" in dockerfile
    assert "/opt/gateway_v2/state_control" in dockerfile


def test_the_single_instance_alarm_counts_the_job_the_services_expose() -> None:
    """`StateRehydratorSingleInstance` alarms on `up{job="amf-state-rehydrator"} < 2`.

    It shipped with the card and had no target to count, so it could never fire. This pins the
    threshold to the two services declared above: if someone drops one, the alarm is the backstop,
    and if someone changes the threshold the two statements must move together.
    """
    alerts = ALERTS.read_text(encoding="utf-8")

    assert 'up{job="amf-state-rehydrator"}' in alerts
    assert "< 2" in alerts
    assert len(SERVICES) == 2


# --- the prod overlay ----------------------------------------------------------------------------

PROD = REPO / "docker-compose.prod.yml"
BUILD_SCRIPT = REPO / "infra" / "scripts" / "build-push-images.sh"


@pytest.fixture(scope="module")
def prod() -> str:
    assert PROD.is_file(), f"{PROD} is missing"
    return PROD.read_text(encoding="utf-8")


@pytest.mark.parametrize("name", SERVICES)
def test_both_rehydrators_are_always_on_in_prod(prod: str, name: str) -> None:
    """The overlay's own header asserts it covers EVERY service in the base compose.

    Leaving them out would mean prod runs ZERO re-hydrators, so nothing stamps and the whole
    fleet fails closed at `AMF_STATE_FRESH_MS` once the v3 gateway serves traffic. They must also
    not be profile-gated: `profiles: !reset null` is what makes them always-on.
    """
    assert f"\n  {name}:" in prod, f"{name} is absent from the production overlay"
    block = _service_block(prod, name)
    inherits = "<<: *prod_rehydrator" in block
    assert inherits or "profiles: !reset null" in block, (
        f"{name} must be always-on in prod, not profile-gated"
    )


def test_the_prod_overlay_header_lists_them(prod: str) -> None:
    """The header is load-bearing documentation: it is how a reader knows the overlay is
    complete. A service added to the base and not to that list makes the claim false.
    """
    header = prod[: prod.index("name: ai_mesh_firewall")]

    assert "state-rehydrator-a" in header and "state-rehydrator-b" in header


def test_prod_uses_a_dedicated_state_control_image_not_the_gateway_one(prod: str) -> None:
    """The integration trap. `ai-mesh-gateway` is built from `gateway/Dockerfile` -- the v1
    gateway -- which contains no `state_control` package at all.

    Pointing the re-hydrators at it yields two containers that cannot start, and the symptom
    (no freshness stamp, fleet fails closed) looks nothing like the cause (wrong image).
    """
    assert "ai-mesh-state-control" in prod
    block = prod[prod.index("\n  state-rehydrator-a:") :]
    assert "ecr_gateway_image" not in block[: block.index("state-rehydrator-b")]


def test_the_state_control_image_is_actually_built_and_pushed() -> None:
    """An image referenced by the overlay and built by nothing is a deploy-time failure.

    It must be built from `gateway_v2/Dockerfile`, which is the only one that copies
    `state_control`.
    """
    script = BUILD_SCRIPT.read_text(encoding="utf-8")

    assert "ai-mesh-state-control" in script
    assert "-f gateway_v2/Dockerfile" in script
    # present in the ensure-repo loop AND the verify loop, not just one
    assert script.count("ai-mesh-state-control") >= 3
