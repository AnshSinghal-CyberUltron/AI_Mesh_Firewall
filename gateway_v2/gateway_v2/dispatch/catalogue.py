"""Snapshot provider catalogue + two distinct resolution errors (GW11, C5, R7).

The provider catalogue is the set of providers/models available to a tenant,
resolved as a SNAPSHOT in the same manner as the pinned plan (R7.1). It is not
read live on the hot path: ``resolve_catalogue`` takes an INJECTABLE
``CatalogueSource`` seam (mirroring the plan snapshot) so the request path stays
pure and testable and the real control-plane fetch is a deferred seam -- this
module implements no real store client.

Two resolution outcomes are failures, and the whole point of R7 / LGW11-5 is that
they are DISTINCT (R7.4):

* ``EmptyCatalogueError`` -- the tenant genuinely has no provider configured
  (R7.2). This is the only condition that MEANS "connect your provider"; even so
  it carries a CODE, not a tenant-facing string -- ``edge`` renders the message
  from the code, exactly as ``domain.posture`` codes are rendered (never an
  ``HTTPException``/``JSONResponse`` down here).
* ``ControlPlaneOutageError`` -- the catalogue could not be resolved because of a
  store/control-plane blip (R7.3). A store blip must NEVER surface to a tenant as
  "connect your provider" (R7.5), so it is a different class carrying a different
  code.

The two share a common base (``CatalogueResolutionError``) for a single
``except`` at the call site, but NEITHER is an ancestor of the other: a store
outage can never be caught as "empty", and genuine absence can never be caught as
"outage" (R7.4). The base is deliberately not itself raised.

Layer: ``dispatch`` sits above ``egress``/``runtime``/``contracts``/``domain`` and
imports only the standard library here, so this module adds no import-linter layer
edge (no ``detect``/``plan``/``admit``/``edge`` import). ``CatalogueEntry`` carries
``timeout_s`` (the plan-declared per-provider/model request timeout C27 uses, R8.4)
and ``context_band`` (the model's context-band width for over-band detection, R8.7);
both are per-ENTRY data sourced from the control plane, not module capacity literals.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

__all__ = (
    "CatalogueEntry",
    "CatalogueResolutionError",
    "CatalogueSource",
    "ControlPlaneOutageError",
    "EmptyCatalogueError",
    "ProviderCatalogue",
    "resolve_catalogue",
)

#: Code carried by ``EmptyCatalogueError`` (R7.2). The ONLY condition whose rendered
#: message may tell the tenant to "connect your provider" -- but the string lives in
#: ``edge``, not here; this module carries only the code (like ``domain.posture``).
EMPTY_CATALOGUE_CODE = "no_provider_configured"

#: Code carried by ``ControlPlaneOutageError`` (R7.3). A store/control-plane blip:
#: ``edge`` renders this as a transient outage and MUST NOT render "connect your
#: provider" for it (R7.5).
CONTROL_PLANE_OUTAGE_CODE = "catalogue_control_plane_unavailable"


@dataclass(frozen=True, slots=True)
class CatalogueEntry:
    """One provider/model available to a tenant (R7.1, R8.4, R8.7).

    ``timeout_s`` is the plan-declared per-provider/model request timeout C27 drives
    dispatch timeouts from (R8.4) -- a per-entry declared value from the control
    plane, not a capacity literal. ``context_band`` is the model's context-band
    width used for incremental over-band detection (R8.7), likewise per-entry data.
    """

    provider: str
    model: str
    timeout_s: float
    context_band: int


@dataclass(frozen=True, slots=True)
class ProviderCatalogue:
    """Snapshot of the providers/models available to a tenant (R7.1).

    Resolved as a snapshot, in the same manner as the pinned plan: a frozen value
    carried by the request path, never mutated. A valid catalogue always has at
    least one entry -- a genuinely empty resolution is ``EmptyCatalogueError``, so
    ``entries`` is never the empty tuple on a successfully resolved catalogue.
    """

    org_id: str
    entries: tuple[CatalogueEntry, ...]


class CatalogueResolutionError(Exception):
    """Common base for the two DISTINCT catalogue-resolution failures (R7.4).

    Exists only so a call site can ``except CatalogueResolutionError`` once; it is
    never raised directly. The two concrete errors below are SIBLINGS -- each
    extends this base, and NEITHER appears in the other's MRO -- so a store outage
    can never be caught as "empty" and genuine absence can never be caught as
    "outage".
    """


class EmptyCatalogueError(CatalogueResolutionError):
    """The tenant genuinely has no provider configured (R7.2).

    A ``no_provider_configured``-class condition caused by ACTUAL absence -- the
    only resolution failure that may surface as "connect your provider", and even
    then via the ``code`` below, which ``edge`` renders. Distinct from
    ``ControlPlaneOutageError`` (R7.4): a store blip is never this error.
    """

    code = EMPTY_CATALOGUE_CODE

    def __init__(self, org_id: str) -> None:
        """Carry the tenant id and the code; no tenant-facing message string here."""
        super().__init__(self.code)
        self.org_id = org_id


class ControlPlaneOutageError(CatalogueResolutionError):
    """The catalogue could not be resolved due to a store/control-plane blip (R7.3).

    DISTINCT from ``EmptyCatalogueError`` (R7.4) and NOT in its MRO: a transient
    control-plane fault is reported as its own code, and ``edge`` MUST NOT render
    "connect your provider" for it (R7.5). Carries only the outage code, never a
    provisioning message.
    """

    code = CONTROL_PLANE_OUTAGE_CODE

    def __init__(self, org_id: str) -> None:
        """Carry the tenant id and the code; never a 'connect your provider' string."""
        super().__init__(self.code)
        self.org_id = org_id


class CatalogueSource(Protocol):
    """Injectable snapshot source for a tenant's provider catalogue (R7.1).

    The real control-plane fetch is a DEFERRED seam (like the plan snapshot) -- this
    protocol is the injection point so ``edge`` wires the real source and tests
    supply a fake that returns entries, returns an empty tuple, or signals an
    outage.

    ``fetch`` returns the tenant's resolved entries as a tuple on success (an empty
    tuple MEANS genuine absence -> ``EmptyCatalogueError``). It signals a
    store/control-plane blip by RAISING ``ControlPlaneOutageError`` -- the one
    low-level outage signal ``resolve_catalogue`` propagates unchanged. The source
    does not itself raise ``EmptyCatalogueError``; emptiness is a value (the empty
    tuple), and ``resolve_catalogue`` is the single authority that turns it into the
    provisioning error.
    """

    def fetch(self, org_id: str) -> tuple[CatalogueEntry, ...]: ...


def resolve_catalogue(source: CatalogueSource, org_id: str) -> ProviderCatalogue:
    """Resolve the catalogue snapshot for ``org_id`` through the injected ``source``.

    Three outcomes, matching the plan snapshot's empty-vs-outage distinction (R7):

    * The source signals a store/control-plane blip -> ``ControlPlaneOutageError``
      propagates (R7.3). This path is NEVER rendered as "connect your provider"
      (R7.5).
    * The source resolves successfully but the tenant has NO entries ->
      ``EmptyCatalogueError`` (R7.2), the genuine provisioning absence.
    * Otherwise -> a ``ProviderCatalogue`` carrying the resolved entries (R7.1).

    The outage error is raised by the source and simply flows out; emptiness is
    adjudicated here so there is one authority turning an empty resolution into the
    provisioning error.
    """
    entries = source.fetch(org_id)
    if not entries:
        raise EmptyCatalogueError(org_id)
    return ProviderCatalogue(org_id=org_id, entries=tuple(entries))
