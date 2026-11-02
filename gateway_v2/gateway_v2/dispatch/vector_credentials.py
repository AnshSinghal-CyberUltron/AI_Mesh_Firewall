"""GW30 credential value types + ``Credential_Source`` seam + scope guard (R8, R9, R10).

This module owns the credential value types the vector-store adapter layer
reads, the injected ``Credential_Source`` seam, and the credential-scope guard
(tasks 5.1, 10.1). The pre-emission leak guard is a later task (11) and is NOT
implemented here.

:class:`OrgCredential` is the per-org credential resolved from the control
plane. It carries the owning ``org_id``, a ``verified`` flag, the secret
material (``_secret``), and an opaque diagnostic ``ref_id`` (safe to log). Its
``__repr__``/``__str__`` are overridden to **redact** the secret, and it
deliberately carries **no serialization method** (``to_dict``/``to_json``/
``__getstate__``): an audit stream is append-only and exported, so a serialized
credential would put the secret at rest -- mirrors the GW14c
``Finding.evidence``-is-never-serialized lesson in AGENTS.md. Diagnostics
reference a credential by its ``ref_id`` only (R9.4, R9.5).

:class:`CredentialResolution` is the value returned by a later
``Credential_Source.resolve(org_id)``: either a **success** carrying exactly one
``OrgCredential`` scoped to the requested ``org_id`` or a **failure** carrying a
posture code and never a credential. The success-vs-failure split is decidable
by ``ok`` alone -- ``credential`` is present only on success and ``None`` on
every failure.

The ``Credential_Source`` protocol is the injected seam a connector resolves a
per-org credential through before any backing-store byte; it is injected from
``edge``/``dispatch`` and NEVER imported downward (R8.6), mirroring the
``dispatch/provider.py`` ``ProviderClient`` idiom. :class:`StubCredentialSource`
is the socket-free in-process source offline tests inject in place of the real
control-plane client (a DEFERRED live gate).

:func:`resolve_scoped_credential` is the credential-scope guard. Given a request
``org_id`` and a resolved :class:`CredentialResolution`, it enforces -- BEFORE
any backing-store call -- that the request carries a usable ``org_id`` and that
the resolved credential's ``org_id`` equals it. It fails closed on every
failure: a missing/empty/unresolvable request ``org_id`` yields
``ORG_IDENTITY_INVALID``; an unresolvable/unverified credential or an unreachable
control plane yields ``CREDENTIAL_UNAVAILABLE``; a resolved-vs-request ``org_id``
mismatch yields ``CREDENTIAL_SCOPE_MISMATCH``. None of these ever proceeds to a
store call (R10.1-R10.5, R8.1-R8.3).

Layering (import-linter ``layers`` contract): ``dispatch`` sits above ``egress``
and ``detect`` and may import ``domain`` and below. This module imports only the
standard library and ``gateway_v2.domain.posture`` (a ``domain`` module, an
allowed downward edge), so it adds no forbidden layer edge. The value types are
frozen slotted dataclasses; there is NO module-level mutable table and NO
capacity literal.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from gateway_v2.domain import posture

__all__ = (
    "CredentialResolution",
    "Credential_Source",
    "OrgCredential",
    "StubCredentialSource",
    "resolve_scoped_credential",
)


@dataclass(frozen=True, slots=True)
class OrgCredential:
    """A per-org credential: redacted repr, never put at rest (R9.4, R9.5).

    ``_secret`` is the secret material -- never emitted, never serialized.
    ``ref_id`` is an opaque diagnostic reference id that IS safe to log.
    """

    org_id: str
    verified: bool
    _secret: str  # secret material -- never emitted, never serialized
    ref_id: str  # opaque diagnostic reference id (safe to log)

    def __repr__(self) -> str:  # redacted -- the secret never reaches a repr
        return (
            f"OrgCredential(org_id={self.org_id!r}, verified={self.verified!r}, "
            f"ref_id={self.ref_id!r}, secret=<redacted>)"
        )

    __str__ = __repr__

    # NO to_dict / to_json / __getstate__ -- a credential is NEVER put at rest
    # (R9.4, R9.5). An audit stream is append-only and exported; a serialized
    # credential would put the secret at rest in three places.


# ``@dataclass(slots=True)`` on 3.11+ synthesizes ``__getstate__`` /
# ``__setstate__`` on the class so a slotted instance can be pickled. That is
# exactly the at-rest serialization R9.4/R9.5 forbid, so we strip the
# dataclass-synthesized pickle-state hooks: ``OrgCredential`` then carries NO
# serialization method of its own (``object``'s inherited default is not a
# per-type credential serializer).
for _pickle_hook in ("__getstate__", "__setstate__"):
    if _pickle_hook in OrgCredential.__dict__:
        delattr(OrgCredential, _pickle_hook)
del _pickle_hook


@dataclass(frozen=True, slots=True)
class CredentialResolution:
    """The outcome of a credential ``resolve(org_id)`` (R10).

    ``code`` is ``VECTOR_OK`` on success, else a failure ``Vector_Posture_Code``
    (``credential_unavailable``, ``credential_scope_mismatch``, or
    ``org_identity_invalid``). ``credential`` is present ONLY on success and
    ``None`` on every failure; the split is decidable by ``ok`` alone.
    """

    code: str
    credential: OrgCredential | None = None

    @property
    def ok(self) -> bool:
        return self.credential is not None


# --------------------------------------------------------------------------- #
# Credential_Source protocol (task 10.1, R8.6) -- the injected seam.
# --------------------------------------------------------------------------- #


@runtime_checkable
class Credential_Source(Protocol):
    """The injected per-org credential seam (R8.6), mirroring ``ProviderClient``.

    ``resolve(org_id)`` reads the per-org plan key ``{rv2}:plan:{org}`` from the
    control plane within the 5000 ms resolution bound and returns exactly one
    :class:`CredentialResolution`: a success carrying one :class:`OrgCredential`
    scoped to the requested ``org_id``, or a failure carrying a posture code and
    no credential. It fails closed -- a missing/empty ``org_id``, a control plane
    unreachable within the bound, or a resolved-but-unverified credential each
    yields ``credential_unavailable`` with no credential (R8.3-R8.5).

    The protocol is ``@runtime_checkable`` so a test can assert a stub satisfies
    it with ``isinstance``. It is injected from ``edge``/``dispatch`` and NEVER
    imported downward (the import-linter ``layers`` contract). The real
    control-plane client is a DEFERRED live gate; offline tests inject
    :class:`StubCredentialSource`.
    """

    async def resolve(self, org_id: str) -> CredentialResolution: ...


class StubCredentialSource:
    """A socket-free, in-process :class:`Credential_Source` for offline tests.

    Driven by an injected ``org_id -> CredentialResolution`` mapping snapshotted
    at construction into an immutable tuple of pairs (so a caller mutating the
    passed-in mapping after construction cannot change what ``resolve`` returns,
    and there is no shared mutable state). A request for an ``org_id`` absent from
    the script resolves to ``credential_unavailable`` -- fail closed, never a
    silent default credential. Opens no socket, file handle, or other external
    I/O; the real control-plane client is a DEFERRED live gate.

    It is a plain slotted class (not a frozen dataclass) only so ``__slots__``
    keeps it allocation-light; it holds no mutable state after construction.
    ``isinstance(stub, Credential_Source)`` is true.
    """

    __slots__ = ("_script",)

    def __init__(
        self,
        resolutions: dict[str, CredentialResolution],
    ) -> None:
        # Snapshot into an immutable tuple of pairs: no retained reference to the
        # caller's dict, no module-level or shared mutable table.
        self._script: tuple[tuple[str, CredentialResolution], ...] = tuple(
            resolutions.items()
        )

    async def resolve(self, org_id: str) -> CredentialResolution:
        """Return the scripted resolution for ``org_id`` or fail closed (R8.3).

        An ``org_id`` absent from the script -- including a missing/empty one --
        resolves to ``credential_unavailable`` with no credential, never a
        process-wide default. The stub never returns another ``org_id``'s
        credential: it only replays the resolution scripted under the exact key.
        """
        for scripted_org_id, resolution in self._script:
            if scripted_org_id == org_id:
                return resolution
        return CredentialResolution(code=posture.CREDENTIAL_UNAVAILABLE)


# --------------------------------------------------------------------------- #
# Credential-scope guard (task 10.1, R8.1-R8.3, R10.1-R10.5) -- fail closed.
# --------------------------------------------------------------------------- #


def resolve_scoped_credential(
    request_org_id: str | None,
    resolution: CredentialResolution,
) -> CredentialResolution:
    """Enforce credential scope BEFORE any backing-store call (R10.1-R10.5).

    Given the request's ``org_id`` and the :class:`CredentialResolution` returned
    by a :class:`Credential_Source`, return a resolution safe to proceed with --
    the SAME successful resolution only when the request carries a usable
    ``org_id`` AND the resolved credential's ``org_id`` equals it -- or a
    fail-closed failure. On any failure the caller MUST NOT contact the backing
    store; the guard orders its checks so a failure is reached before a store
    call could be made:

    1. A missing/empty/whitespace-only request ``org_id`` -> ``org_identity_invalid``:
       the request has no usable identity to scope a credential to, so no
       credential is honoured and no store call is made (R10.3).
    2. A non-``ok`` resolution (unresolvable/unverified credential or an
       unreachable control plane, carried as any ``credential is None`` failure)
       -> ``credential_unavailable``: a verified credential was never produced
       (R8.3-R8.5). Its own failure code is normalized to
       ``credential_unavailable`` so the guard emits exactly one credential-
       absent spelling.
    3. A resolved credential whose ``org_id`` != the request's ``org_id`` ->
       ``credential_scope_mismatch``, refused BEFORE any store call (R10.2,
       R10.4, R10.5).

    Only when all three checks pass is the original successful ``resolution``
    returned unchanged, carrying the one :class:`OrgCredential` scoped to the
    request's ``org_id``. The guard never returns another org's credential and
    never fabricates a default (R10.6).
    """
    # 1. The request must carry a usable identity before any credential is honoured.
    if request_org_id is None or not request_org_id.strip():
        return CredentialResolution(code=posture.ORG_IDENTITY_INVALID)

    # 2. A verified credential must have been produced; otherwise fail closed with
    #    the one credential-absent spelling (R8.3-R8.5). A resolution with no
    #    credential -- and, defensively, one that smuggled an unverified
    #    credential past the source -- is treated as unavailable.
    credential = resolution.credential
    if credential is None or not credential.verified:
        return CredentialResolution(code=posture.CREDENTIAL_UNAVAILABLE)

    # 3. The resolved credential must be scoped to the request's org -- refused
    #    before any store call on a mismatch (R10.2, R10.4, R10.5).
    if credential.org_id != request_org_id:
        return CredentialResolution(code=posture.CREDENTIAL_SCOPE_MISMATCH)

    return resolution
