"""Secret-shaped-output stratum for the load harness (B6 / R2-21).

Marks a configured share of requests to drive the gateway's GW12b bounded-holdback path by
**scripting the stub provider's OUTPUT bytes** to match the holdback shape classes (Req 6.1). The
harness arranges the *emitted* bytes — a shape-matched token such as an AWS-key-shaped,
email-shaped, or UUID-shaped string chosen from :data:`SHAPE_TEMPLATES` — so that when the booted
gateway re-emits them over the real SSE egress + bounded-holdback pipeline, the client observes real
holdback timing (which the C4 collector excludes per Req 8.3) and the realized secret-shaped share
over a run (Req 6.2).

Honest limit (holdback MECHANISM, not the detector). This stratum drives the GW12b holdback
*mechanism*, which is keyed on output-byte **shape** — it does **not** prove the gateway's GW07/GW08
detector classifies those bytes as secrets: the request-path detector model call is a GW07/GW08
deferred seam, and B5/GW12b ships the holdback mechanism keyed on shape, not a live detector.
Therefore the harness drives and measures the holdback mechanism and its client-visible C4/share
using shape-matched scripted output; detector-driven secret classification (GW07/GW08) and any
real-upstream secret generation (the socket-level fake generation upstream, option (b)) are a
deferred seam.

Determinism spine (Req 13.1, 13.2, 13.3). :func:`plan` is a pure per-request decision keyed only by
the injected ``random.Random`` (never module-global ``random.*``) and the configured share: the
same seeded ``rng`` driven over the same arrivals yields byte-identical plans, with no network or
server dependency, so the realized share is generated and verified offline. :func:`realized_share`
is a pure reducer over a run's plans.

Self-contained (Req 14). This module imports no other harness module (in particular NOT the
in-flight ``arrivals``); it composes onto an ``Arrival`` in ``request_plan.py``. It adds no
capacity-position literal and no module-level mutable; :data:`SHAPE_TEMPLATES` is an immutable tuple
of obviously-synthetic placeholders and ``SecretOutputPlan`` is frozen + slots (Req 14.2, 14.3).
"""

from __future__ import annotations

import random
from collections.abc import Iterable
from dataclasses import dataclass
from enum import Enum

__all__ = (
    "SHAPE_TEMPLATES",
    "SecretOutputPlan",
    "SecretShapeClass",
    "plan",
    "realized_share",
)


class SecretShapeClass(Enum):
    """The GW12b holdback shape classes this stratum can script output bytes to match.

    These names mirror the shape classes the shipped ``gateway_v2.detect.holdback`` keys its
    bounded-holdback ``TRADE_OFF`` table on (``aws``, ``api_token``, ``email``, ``jwt``, ``card``,
    ``uuid``, ``sha256``, ``url``, ``base64``). The harness scripts the stub to emit a token of the
    chosen class's shape; it drives the holdback *mechanism*, not the GW07/GW08 detector (see the
    module docstring's honest limit).
    """

    AWS = "aws"
    API_TOKEN = "api_token"
    EMAIL = "email"
    JWT = "jwt"
    CARD = "card"
    UUID = "uuid"
    SHA256 = "sha256"
    URL = "url"
    BASE64 = "base64"


# --------------------------------------------------------------------------------------------------
# Synthetic shape templates (TEST FIXTURES — not real secrets).
#
# Each entry is an OBVIOUSLY-SYNTHETIC placeholder whose *byte shape* matches a GW12b holdback class
# so the scripted stub's emitted stream drives the real bounded-holdback path. None is a real
# credential: every value is a fixed, visibly-fake constant (repeated/placeholder characters, an
# ``example.invalid`` domain, a deliberately-broken PEM lead) and carries no live key material. This
# is a `.py` source file with a suffix, so the `check_secret_headers` gate (which scans only
# extensionless / `*.pem` / `*.key` / `*.secret` filenames for private-key headers) never inspects
# it; the PEM-shaped api_token fixture below is additionally kept clearly broken so it would not
# match that gate's `BEGIN ... PRIVATE KEY` pattern even if the file were scanned.
# --------------------------------------------------------------------------------------------------

SHAPE_TEMPLATES: tuple[tuple[SecretShapeClass, str], ...] = (
    # AWS access-key shape: the `AKIA` lead the holdback `_AWS_RE` keys on, then placeholder chars.
    (SecretShapeClass.AWS, "AKIAEXAMPLEPLACEHOLDER0"),
    # Generic api_token shape: a visibly-broken, NON-matching PEM-ish lead used only as a fixture
    # (deliberately NOT the real `BEGIN ... PRIVATE KEY` header, so it drives the api_token hold
    # class without being a key header).
    (SecretShapeClass.API_TOKEN, "token_PLACEHOLDER_not_a_real_secret_000"),
    # Email shape: an `@` with the reserved, non-routable `example.invalid` domain.
    (SecretShapeClass.EMAIL, "placeholder@example.invalid"),
    # JWT shape: the `eyJ` base64 head of `{"` the holdback `_JWT_RE` keys on, then placeholder.
    (SecretShapeClass.JWT, "eyJPLACEHOLDERplaceholderNOTareal"),
    # Payment-card shape: a 16-digit numeric run (the canonical ValidationException test number).
    (SecretShapeClass.CARD, "4111111111111111"),
    # UUID shape: a fixed all-zero/placeholder hex-with-dashes layout.
    (SecretShapeClass.UUID, "00000000-0000-0000-0000-000000000000"),
    # SHA-256 shape: a 64-char hex run (all-zero placeholder).
    (SecretShapeClass.SHA256, "0" * 64),
    # URL shape: an `https://` lead the holdback `_URL_RE` keys on, example.invalid host.
    (SecretShapeClass.URL, "https://example.invalid/placeholder"),
    # Base64 shape: a >=8-char base64 alphabet run (placeholder).
    (SecretShapeClass.BASE64, "UExBQ0VIT0xERVI="),
)


@dataclass(frozen=True, slots=True)
class SecretOutputPlan:
    """One request's secret-shaped-output decision (frozen + slots, Req 14.3).

    ``secret_shaped`` is ``True`` when this request is marked to drive the GW12b holdback path;
    ``shape`` names the holdback shape class the stub output is scripted to match (``None`` when the
    request is not secret-shaped). ``template`` is the obviously-synthetic shape-matched placeholder
    the stub emits (``None`` when not secret-shaped) — a test fixture from :data:`SHAPE_TEMPLATES`,
    never a real secret.
    """

    secret_shaped: bool
    shape: SecretShapeClass | None
    template: str | None


def plan(share: float, rng: random.Random) -> SecretOutputPlan:
    """Decide one request's secret-shaped-output plan (pure, keyed by the injected ``rng``).

    ``share`` is the configured secret-shaped share in ``[0.0, 1.0]`` (``StrataConfig
    .secret_output_share``). A request is marked secret-shaped with probability ``share`` via a
    single ``rng.random()`` draw (``< share``); when marked, a shape class + its synthetic template
    are drawn from :data:`SHAPE_TEMPLATES` by a second ``rng`` draw, so the realized shares converge
    to ``share`` (Req 6.1) and are reproducible offline under a seeded ``rng`` (Req 13.2). A
    ``share`` outside ``[0.0, 1.0]`` is clamped (``config.load_run_spec`` already validates the
    configured value, so this only hardens direct callers); ``share <= 0`` never marks and
    ``share >= 1`` always marks.

    The returned plan carries only the *harness's chosen* bytes — it drives the holdback mechanism,
    not the GW07/GW08 detector (module docstring's honest limit).
    """
    clamped = min(1.0, max(0.0, share))
    draw = rng.random()
    if draw >= clamped:
        return SecretOutputPlan(secret_shaped=False, shape=None, template=None)
    shape, template = SHAPE_TEMPLATES[rng.randrange(len(SHAPE_TEMPLATES))]
    return SecretOutputPlan(secret_shaped=True, shape=shape, template=template)


def realized_share(plans: Iterable[SecretOutputPlan]) -> float:
    """The realized share of plans marked secret-shaped over a run (Req 6.2).

    Pure reducer: ``(# secret-shaped plans) / (# plans)``; an empty run yields ``0.0``. This is the
    realized share the run report records for the secret-shaped-output stratum, measured against the
    configured ``share`` within the declared convergence tolerance (``Tolerances
    .share_convergence``).
    """
    total = 0
    marked = 0
    for item in plans:
        total += 1
        if item.secret_shaped:
            marked += 1
    if total == 0:
        return 0.0
    return marked / total
