"""LGW30 org-credential-isolation property test (GW30, task 10.2).

# Feature: vector-store-adapter-gw30, Property 4: Org-credential isolation
# Validates: Requirements 8.2, 10.1, 10.2, 10.4, 10.6, 15.6

Property 4 (design §Correctness Properties → Property 4): *for all* request
``org_id`` values and *for all* resolved credentials,
:func:`resolve_scoped_credential` NEVER yields a usable credential whose
``org_id`` differs from the request's ``org_id``. The guard fails closed on every
unsafe shape, ordered so a failure is reached before a store call could be made:

* A missing/empty/whitespace-only request ``org_id`` -> ``ORG_IDENTITY_INVALID``
  with no credential (R10.3).
* A resolution with no credential, or one smuggling an **unverified** credential,
  -> ``CREDENTIAL_UNAVAILABLE`` with no credential (R8.3-R8.5, R10.1).
* A resolved credential whose ``org_id`` != the request's ``org_id`` ->
  ``CREDENTIAL_SCOPE_MISMATCH``, no usable credential proceeds (R10.2, R10.4).
* ONLY when the request carries a usable ``org_id`` AND the verified credential's
  ``org_id`` equals it is the original successful resolution returned unchanged,
  carrying that one credential (R10.5, R10.6).

The test also exercises :class:`StubCredentialSource` isolation: resolving org A
never returns org B's credential, and an ``org_id`` absent from the script fails
closed with ``CREDENTIAL_UNAVAILABLE`` -- never a silent process-wide default.

House idiom: one seeded ``random.Random`` drives ``_ITERATIONS`` trials; each
trial randomizes the request ``org_id`` (valid names / empty / whitespace /
``None``), the resolved credential's ``org_id`` (same vs different), the
``verified`` flag, and the presence/absence of a credential. No ``hypothesis``;
the async ``StubCredentialSource.resolve`` runs through ``asyncio.run``.

_Design: Testing Strategy (property tests); Correctness Properties → Property 4;
Components §credential-scope guard._
_Requirements: 10.1, 10.2, 10.3, 10.4, 10.5, 10.6._
"""

from __future__ import annotations

import asyncio
import random
from collections.abc import Awaitable

from gateway_v2.dispatch.vector_credentials import (
    CredentialResolution,
    OrgCredential,
    StubCredentialSource,
    resolve_scoped_credential,
)
from gateway_v2.domain import posture

_ITERATIONS = 10_000

# A pool of well-formed org names the generator draws request + credential
# org_ids from, so "same org" and "different org" are both frequently hit.
_ORG_NAMES = (
    "org-alpha",
    "org-beta",
    "org-gamma",
    "org-delta",
    "org-epsilon",
    "org-zeta",
)

# The unusable request-identity shapes: missing/empty/whitespace-only. Each must
# drive ORG_IDENTITY_INVALID before any credential is even inspected (R10.3).
_UNUSABLE_ORG_IDS: tuple[str | None, ...] = (
    None,
    "",
    " ",
    "   ",
    "\t",
    "\n",
    " \t\n ",
)


def _run[T](coro: Awaitable[T]) -> T:
    """Drive one coroutine to completion (house idiom; no async test plugin)."""
    return asyncio.run(coro)  # type: ignore[arg-type]


def _make_credential(rng: random.Random, org_id: str, *, verified: bool) -> OrgCredential:
    """A deterministic :class:`OrgCredential` scoped to ``org_id`` from ``rng``."""
    return OrgCredential(
        org_id=org_id,
        verified=verified,
        _secret=f"secret-{rng.randint(0, 1_000_000)}",
        ref_id=f"cred-ref-{rng.randint(0, 9999)}",
    )


def test_resolve_scoped_credential_never_crosses_org_boundary() -> None:
    """Property 4: the scope guard never yields a usable cross-org credential.

    Each trial randomizes the request ``org_id`` (valid / empty / whitespace /
    ``None``), whether a credential is present, the credential's ``org_id`` (same
    vs a different org), and the ``verified`` flag. The guard's output is checked
    against the fail-closed contract: a usable credential is returned ONLY on the
    verified-same-org path, and in every other shape no credential proceeds.

    _Validates: Requirements 10.1, 10.2, 10.3, 10.4, 10.5, 10.6._
    """
    rng = random.Random(0xC9ED150)

    for _ in range(_ITERATIONS):
        # --- request identity: usable vs unusable ----------------------------
        if rng.random() < 0.3:
            request_org_id: str | None = rng.choice(_UNUSABLE_ORG_IDS)
            request_usable = False
        else:
            request_org_id = rng.choice(_ORG_NAMES)
            request_usable = True

        # --- resolved credential: absent / unverified / wrong-org / matching --
        has_credential = rng.random() < 0.8
        if has_credential:
            verified = rng.random() < 0.7
            # Pick the credential's org: same as the (usable) request org, or a
            # deliberately different one, so the mismatch path is well covered.
            if request_usable and rng.random() < 0.5:
                cred_org = request_org_id  # same org
            else:
                cred_org = rng.choice(_ORG_NAMES)
            assert cred_org is not None
            credential = _make_credential(rng, cred_org, verified=verified)
            # The incoming resolution the source produced: a success carries the
            # credential; occasionally model a failure code that still smuggled a
            # credential, to prove the guard re-checks rather than trusting code.
            incoming_code = (
                posture.VECTOR_OK
                if rng.random() < 0.85
                else rng.choice(
                    (
                        posture.CREDENTIAL_UNAVAILABLE,
                        posture.PROVIDER_UNREACHABLE,
                    )
                )
            )
            resolution = CredentialResolution(code=incoming_code, credential=credential)
        else:
            credential = None  # type: ignore[assignment]
            resolution = CredentialResolution(
                code=rng.choice(
                    (
                        posture.CREDENTIAL_UNAVAILABLE,
                        posture.PROVIDER_UNREACHABLE,
                        posture.ORG_IDENTITY_INVALID,
                    )
                )
            )

        guarded = resolve_scoped_credential(request_org_id, resolution)

        # ---- the one invariant: no usable cross-org credential EVER escapes --
        if guarded.credential is not None:
            # A credential proceeded: it MUST be verified and scoped to the
            # usable request org, and the guard MUST have returned the original
            # success unchanged (R10.5, R10.6).
            assert request_usable
            assert guarded.credential.verified is True
            assert guarded.credential.org_id == request_org_id
            assert guarded is resolution
            assert guarded.ok is True
            continue

        # ---- otherwise it failed closed with the right, ordered code ---------
        assert guarded.ok is False
        assert guarded.credential is None

        if not request_usable:
            # Identity is checked first: an unusable request org_id is rejected
            # regardless of what credential was resolved (R10.3).
            assert guarded.code == posture.ORG_IDENTITY_INVALID
        elif credential is None or not credential.verified:
            # No verified credential was produced -> the single credential-absent
            # spelling (R8.3-R8.5, R10.1).
            assert guarded.code == posture.CREDENTIAL_UNAVAILABLE
        else:
            # A verified credential existed but was scoped to a different org ->
            # refused before any store call (R10.2, R10.4).
            assert credential.org_id != request_org_id
            assert guarded.code == posture.CREDENTIAL_SCOPE_MISMATCH


def test_resolve_scoped_credential_matching_org_returns_resolution_identity() -> None:
    """The verified-same-org path returns the ORIGINAL success object unchanged (R10.5, R10.6).

    A focused randomized check that the one success path is an identity return --
    the guard does not clone, re-wrap, or substitute the resolution it was handed.

    _Validates: Requirements 10.5, 10.6._
    """
    rng = random.Random(0x5A3E0)

    for _ in range(_ITERATIONS):
        org_id = rng.choice(_ORG_NAMES)
        credential = _make_credential(rng, org_id, verified=True)
        resolution = CredentialResolution(code=posture.VECTOR_OK, credential=credential)

        guarded = resolve_scoped_credential(org_id, resolution)

        assert guarded is resolution
        assert guarded.credential is credential
        assert guarded.credential.org_id == org_id


def test_stub_credential_source_never_returns_another_orgs_credential() -> None:
    """``StubCredentialSource`` replays only the exact-key resolution; fails closed otherwise.

    Resolving org A returns A's scripted resolution and never B's credential, and
    an ``org_id`` absent from the script resolves to ``CREDENTIAL_UNAVAILABLE``
    with no credential -- never a silent default, never a sibling org's credential
    (R8.2, R10.1, R10.6).

    _Validates: Requirements 10.1, 10.6._
    """
    rng = random.Random(0x5717B)

    for _ in range(_ITERATIONS):
        org_a, org_b = rng.sample(_ORG_NAMES, 2)
        cred_a = _make_credential(rng, org_a, verified=True)
        cred_b = _make_credential(rng, org_b, verified=True)
        source = StubCredentialSource(
            {
                org_a: CredentialResolution(code=posture.VECTOR_OK, credential=cred_a),
                org_b: CredentialResolution(code=posture.VECTOR_OK, credential=cred_b),
            }
        )

        res_a = _run(source.resolve(org_a))
        assert res_a.credential is cred_a
        assert res_a.credential is not cred_b
        assert res_a.credential.org_id == org_a

        res_b = _run(source.resolve(org_b))
        assert res_b.credential is cred_b
        assert res_b.credential.org_id == org_b

        # An org_id absent from the script fails closed -- no sibling credential.
        absent = f"org-unscripted-{rng.randint(0, 1_000_000)}"
        res_absent = _run(source.resolve(absent))
        assert res_absent.code == posture.CREDENTIAL_UNAVAILABLE
        assert res_absent.credential is None
        assert res_absent.ok is False

        # And the scope guard over the stub's output never crosses the boundary:
        # org B's resolution presented for an org A request is refused.
        cross = resolve_scoped_credential(org_a, res_b)
        assert cross.code == posture.CREDENTIAL_SCOPE_MISMATCH
        assert cross.credential is None
