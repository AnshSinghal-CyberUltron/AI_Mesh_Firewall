"""The single authority for the load harness release version (B7 / R2-20, partial).

This module is the ONE source of truth for the harness release identifier. R2-20 requires a
capacity measurement to be attributable to a *known, unmodified* harness release; the first step
is that the harness carry an explicit, machine-readable :class:`Version` discoverable at runtime
rather than a value stated only in a docstring (Req 1.1, 1.2, 1.3).

Everything that references the release version derives from :data:`HARNESS_VERSION` and never
restates the identifier as a second literal (Req 1.4):

- ``load_harness/__init__.py`` exposes ``__version__ = str(version.HARNESS_VERSION)`` and states in
  its docstring that the authoritative version lives here;
- ``README.md``'s version line is annotated as derived from this stamp;
- the release manifest's ``version`` field and the run report's ``harness_version`` provenance both
  carry the value of this one stamp.

Keeping a single stamp means a release-version change happens in exactly one place and nothing can
diverge — the single-authority invariant exercised by Property 6.

House posture (Req 6.2, 6.3). :class:`Version` is a ``@dataclass(frozen=True, slots=True)`` value
type; there is no module-level mutable (``HARNESS_VERSION`` is a frozen :class:`Version` instance,
not a list/dict/set). A version string is a release-identity value, not a host CPU/RAM/FD/RPS
budget, so it carries none of the capacity keywords and holds no capacity-position literal. The
module is stdlib-only (``dataclasses``), stays well under the 800-line / 120-line gate bounds, and
introduces no HTTP type — it is pure, offline, and server-free.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Version:
    """The harness release identifier as a frozen value (Req 1.3).

    ``identifier`` is the machine-readable released version (e.g. ``"v2.3"``). ``str(version)``
    returns that identifier so documentation / prose can derive from the stamp without restating a
    separate literal (Req 1.4).
    """

    identifier: str

    def __str__(self) -> str:
        return self.identifier


#: The ONE source of truth for the harness release version (Req 1.1, 1.2, 1.4). It is importable
#: and discoverable at runtime; ``__init__.py``/``README``/the release manifest/the run report
#: provenance all derive from this stamp and nothing restates ``"v2.3"`` a second time.
HARNESS_VERSION: Version = Version(identifier="v2.3")
