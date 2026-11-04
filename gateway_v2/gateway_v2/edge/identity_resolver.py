"""The thin ``IdentityResolver`` edge-adapter (GW15, task 2.3; design Components §6).

The §10.4 lifecycle's identity phase (``edge/openai_chat.py::ChatSurface._identity``)
resolves the owner of a request to a :class:`TenantIdentity` (``org_id`` + the signed
:class:`~gateway_v2.domain.identity.Principal`) BEFORE the plan round trip, admission,
budget, and the input scan. The design calls the adapter that does this a *thin
``IdentityResolver`` edge-adapter over ``admit.principal_of`` + the GW05b/c freshness
stamp*: it names the request's credential, resolves it through the shipped
:class:`~gateway_v2.admit.identity.IdentityCache` (which is RAM-served and already gates
every answer on the freshness stamp), and returns either the resolved identity or a
fail-closed ``None``.

**Home + layer.** ``edge/identity_resolver.py`` is ``edge`` (the top of the import-linter
layer order ``edge > admit > plan > detect > resolve > dispatch > egress > audit >
runtime > contracts > domain``), so it may import ``admit`` (``IdentityCache`` /
``principal_of`` / ``Principal``) and ``domain`` (``StoreDataUnavailable``). It is a
sibling of ``edge/openai_chat.py`` that sits BELOW it in the import graph: ``openai_chat``
imports :class:`IdentityResolver` + :class:`TenantIdentity` from here, and this module
imports nothing from ``openai_chat`` — so no circular import is formed and
:class:`TenantIdentity` has one definition (re-exported from ``openai_chat`` for callers
that reach it there, matching the design's Data-Models placement).

**Fail closed (R4.2 / R4.3), with the reason kept (GW36 S3).** ``resolve_outcome`` returns
an :class:`IdentityOutcome`, and every surface runs the phase through :func:`identity_of`:

* **No usable credential** (missing, another scheme, not a console key's shape) and
  **verified absence** (a revoked / never-issued key the cache resolved to ``None``) refuse
  with ``invalid_api_key`` -- HTTP 401, the code OpenAI's SDK raises ``AuthenticationError``
  on.
* **Unverified state** -- :meth:`IdentityCache.resolve` raises
  :class:`~gateway_v2.domain.state.StoreDataUnavailable` while freshness is not met or the
  store does not answer -- refuses with ``shared_state_unavailable``: a 503, never a 401,
  because the key may be valid and the uncertain part may be precisely the revocation.

``resolve`` (``None`` on any failure) is kept for resolvers and callers that predate the
outcome; :func:`identity_of` reads a bare ``None`` as the 503.

**ONE round trip (R3.3).** The identity resolution does NOT perform a request-path shared-
state read: :class:`IdentityCache` answers from its per-process RAM cache (or a single
coalesced fill the synchroniser warms off the request path) and reads only the freshness
STAMP, which is itself RAM-served per GW05b/c. The one shared-state round trip per request
is the plan ``lookup_plan`` in ``_plan``; the identity phase adds none, which is what lets
Property 6 (exactly one round trip) hold.

**Injectable (so tests pass a fake).** :class:`IdentityResolver` is a
:class:`typing.Protocol`, so the lifecycle depends on the ``resolve`` surface, not a
concrete type: a test injects a fake resolver that returns a canned
:class:`TenantIdentity` or ``None`` with zero wiring. :class:`CachingIdentityResolver` is
the concrete production adapter over the shipped :class:`IdentityCache`; the credential
extractor and the store ``fetch`` are injected so the key-naming and the record read stay
boot-wiring concerns (tasks 5/6), not baked in here.
"""

from __future__ import annotations

import hashlib
import re
import secrets
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from gateway_v2.admit.identity import Fetch, IdentityCache, ProjectInvalid
from gateway_v2.domain import posture
from gateway_v2.domain.identity import Principal
from gateway_v2.domain.state import StoreDataUnavailable

__all__ = (
    "CachingIdentityResolver",
    "IdentityOutcome",
    "IdentityResolver",
    "TenantIdentity",
    "fail_closed_resolver",
    "hashed_bearer_key",
    "identity_of",
    "mint_request_id",
    "refusal_request_id",
)

#: An ASGI connection scope — the read-only ``{"type": ..., "method": ..., "headers": ...}``
#: mapping the server hands the application. Mirrors ``edge/routes.py::ASGIScope`` but is
#: re-stated here to avoid an ``edge/openai_chat.py`` ↔ ``edge/routes.py`` import cycle; both
#: spell the same read-only ``Mapping[str, object]`` so the resolver and the route table agree.
ASGIScope = Mapping[str, object]


# --------------------------------------------------------------------------- #
# Value type (Data Models) — the resolved owner of a request.
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class TenantIdentity:
    """The resolved owner of a request: the org plus its signed principal (R4).

    Produced by the identity phase from ``admit.principal_of`` + the GW05b/c freshness
    stamp. ``org_id`` is the tenant the plan round trip, the admission owner key, and the
    budget lease are all scoped to; ``principal`` is the shipped
    :class:`~gateway_v2.domain.identity.Principal` resolved from the signed key record
    (carrying ``org_id`` / rate / burst / ``feed_seq``). Frozen + slotted: an immutable
    value built once per request.
    """

    org_id: str
    principal: Principal


@dataclass(frozen=True, slots=True)
class IdentityOutcome:
    """What the identity phase concluded: an owner, or the code to refuse with (GW36 S3).

    The distinction the bare ``None`` could not carry. ``invalid_api_key`` (401) means the
    request carried no usable credential, or VERIFIED state holds no live key for it;
    ``shared_state_unavailable`` (503) means the state could not be verified, so the key may
    well be valid and a 401 would be a lie.
    """

    identity: TenantIdentity | None
    refusal: str = ""

    @staticmethod
    def resolved(identity: TenantIdentity) -> IdentityOutcome:
        return IdentityOutcome(identity)

    @staticmethod
    def invalid() -> IdentityOutcome:
        return IdentityOutcome(None, posture.INVALID_API_KEY)

    @staticmethod
    def unverified() -> IdentityOutcome:
        return IdentityOutcome(None, posture.SHARED_STATE_UNAVAILABLE)


# --------------------------------------------------------------------------- #
# The resolver surface (a Protocol so a test can inject a fake).
# --------------------------------------------------------------------------- #


@runtime_checkable
class IdentityResolver(Protocol):
    """Resolve a request's :class:`TenantIdentity`, or ``None`` to fail closed (R4).

    The thin edge-adapter the §10.4 lifecycle's identity phase depends on. ``resolve``
    names the request's credential from ``scope``, resolves it over ``admit.principal_of``
    + the GW05b/c freshness stamp, and returns the resolved :class:`TenantIdentity` — or
    ``None`` when the credential is absent, the principal is unresolved, OR the freshness
    stamp is stale / unavailable. A ``None`` means the lifecycle refuses with the declared
    authentication value-code and makes zero provider calls (fail closed, R4.2 / R4.3).

    A :class:`Protocol` (not a concrete base) so the lifecycle is coupled only to the
    ``resolve`` surface: production injects :class:`CachingIdentityResolver`; a test injects
    a fake returning a canned identity or ``None``.
    """

    async def resolve(self, scope: ASGIScope) -> TenantIdentity | None:
        """Resolve the request owner, or ``None`` to fail closed (R4.2 / R4.3)."""
        ...


async def identity_of(resolver: IdentityResolver, scope: ASGIScope) -> IdentityOutcome:
    """The ONE way a surface runs the identity phase (GW36 S3).

    Prefers ``resolve_outcome`` (401 vs 503); a resolver that predates it is still served,
    with its ``None`` read as "could not verify" -- the old meaning, so nothing that relied on
    a 503 starts answering 401.
    """
    outcome = getattr(resolver, "resolve_outcome", None)
    if outcome is not None:
        result: IdentityOutcome = await outcome(scope)
        return result
    identity = await resolver.resolve(scope)
    return IdentityOutcome.unverified() if identity is None else IdentityOutcome(identity)


_IDENTITY_REFUSALS = frozenset(
    {posture.INVALID_API_KEY, posture.SHARED_STATE_UNAVAILABLE, posture.PROJECT_INVALID}
)


def mint_request_id() -> str:
    """A random, content-free gateway request id (``zs-`` + 24 hex)."""
    return f"zs-{secrets.token_hex(12)}"


def refusal_request_id(code: str) -> str | None:
    """A request id for an IDENTITY refusal, which precedes the content-derived one (N11).

    Random, so a client can quote it and nothing about the request is derivable from it. Every
    other code returns None: those refusals either carry the minted id or predate any.
    """
    return mint_request_id() if code in _IDENTITY_REFUSALS else None


# --------------------------------------------------------------------------- #
# Credential extraction (pure; no I/O, no security decision beyond naming the key).
# --------------------------------------------------------------------------- #

#: The ASGI header name (lower-cased bytes, per the ASGI convention) carrying the API
#: credential, and the scheme an OpenAI SDK sends. Plain byte/string constants, not
#: capacity-position literals.
_AUTHORIZATION_HEADER = b"authorization"
_BEARER_SCHEME = "bearer"

_CONSOLE_KEY = re.compile(r"[A-Za-z0-9_-]{48}")
"""The shape every console key has: ``secrets.token_urlsafe(48)[:48]``
(``GatewayAPIKey.generate_key``, ``KEY_LENGTH = 48``). Anything else cannot match a stored key,
so it is refused as absent without a store read: an unbounded stream of junk tokens must not
become an unbounded stream of Valkey reads."""


def bearer_key(scope: ASGIScope) -> str | None:
    """Name the request's credential from the ASGI ``Authorization`` header, or ``None``.

    Reads the ``scope["headers"]`` list (the ASGI ``list[tuple[bytes, bytes]]`` shape) for
    the ``authorization`` header and returns the bearer token following the ``Bearer ``
    scheme as the cache key_hash. PURE: it names the credential and makes no security
    decision — a missing / malformed header returns ``None`` (the caller fails closed). It
    does NOT hash or trust the value; it is the cache lookup key the injected ``fetch``
    resolves against the signed key record.
    """
    raw_headers = scope.get("headers")
    if not isinstance(raw_headers, (list, tuple)):
        return None
    for header in raw_headers:
        if not isinstance(header, (list, tuple)) or len(header) != 2:
            continue
        name, value = header[0], header[1]
        if name != _AUTHORIZATION_HEADER or not isinstance(value, (bytes, bytearray)):
            continue
        # latin-1 decodes every byte, so this cannot raise. The scheme is case-insensitive
        # (RFC 7235 section 2.1); the token is not.
        scheme, _, token = bytes(value).decode("latin-1").partition(" ")
        if scheme.lower() != _BEARER_SCHEME:
            return None
        return token.strip() or None
    return None


def hashed_bearer_key(scope: ASGIScope) -> str | None:
    """The credential as control stores it: ``sha256(key).hexdigest()``, or ``None``.

    The raw key never leaves this function: the cache, the store read and every log see only
    the hash. A token that cannot be a console key (`_CONSOLE_KEY`) is ``None`` -- absent,
    a 401 with no store read.
    """
    token = bearer_key(scope)
    if token is None or not _CONSOLE_KEY.fullmatch(token):
        return None
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


# --------------------------------------------------------------------------- #
# The concrete production adapter over the shipped IdentityCache.
# --------------------------------------------------------------------------- #


class CachingIdentityResolver:
    """``IdentityResolver`` over the shipped :class:`IdentityCache` + freshness stamp (R4).

    Thin by design: it names the request's credential (via the injected ``key_namer``,
    defaulting to :func:`bearer_key`), resolves it through the shipped
    :class:`~gateway_v2.admit.identity.IdentityCache` (RAM-served, freshness-gated,
    single-flighted), and builds a :class:`TenantIdentity` from the resolved
    :class:`~gateway_v2.domain.identity.Principal`. The store read that turns a cold key
    into a principal is the injected ``fetch`` — a boot-wiring concern (the record read +
    ``principal_of``), kept out of the request path except on a cold-cache fill the cache
    coalesces to ONE.

    Fail closed (R4.2 / R4.3): no credential ⇒ ``None``; an unresolved principal ⇒ ``None``;
    and a :class:`~gateway_v2.domain.state.StoreDataUnavailable` raised while the GW05b/c
    freshness stamp is stale / unavailable is caught and mapped to ``None`` — the identity
    held in RAM is refused rather than trusted. The caller renders the declared
    ``shared_state_unavailable`` code and makes zero provider calls.

    Stateless beyond its injected collaborators; held once on the ``ChatRoute`` and shared
    across requests (the cache carries the per-process state). ``__slots__`` so it allocates
    no ``__dict__``.
    """

    __slots__ = ("_cache", "_fetch", "_key_namer")

    def __init__(
        self,
        *,
        cache: IdentityCache,
        fetch: Fetch,
        key_namer: Callable[[ASGIScope], str | None] = bearer_key,
    ) -> None:
        """Construct over the shipped cache, the store ``fetch``, and a credential namer.

        ``cache`` is the shipped per-process :class:`IdentityCache` (its ``stamp`` gates
        freshness); ``fetch`` reads one signed key record into a
        :class:`~gateway_v2.domain.identity.Principal` (``principal_of`` over the store
        read) and is only called on a cold-cache fill the cache coalesces; ``key_namer``
        names the request's credential from the scope (defaults to :func:`bearer_key`).
        """
        self._cache = cache
        self._fetch = fetch
        self._key_namer = key_namer

    async def resolve(self, scope: ASGIScope) -> TenantIdentity | None:
        """Resolve the request owner, or ``None`` to fail closed (R4.2 / R4.3).

        Names the credential, resolves it through the freshness-gated cache, and returns a
        :class:`TenantIdentity`. A missing credential, an unresolved principal, or a stale /
        unavailable freshness stamp (surfaced as
        :class:`~gateway_v2.domain.state.StoreDataUnavailable`) all return ``None`` so the
        lifecycle fails closed with zero provider calls. The ``org_id`` is taken from the
        resolved principal so the plan round trip, admission, and budget are all scoped to
        the credential's own tenant (never another tenant's).
        """
        return (await self.resolve_outcome(scope)).identity

    async def resolve_outcome(self, scope: ASGIScope) -> IdentityOutcome:
        """The same resolution, keeping WHY it failed: 401 for a bad key, 503 for doubt."""
        key_hash = self._key_namer(scope)
        if key_hash is None:
            return IdentityOutcome.invalid()
        return await self.resolve_hash(key_hash)

    async def resolve_hash(self, key_hash: str) -> IdentityOutcome:
        """Resolve an already-named credential hash (GW32). The one cache path: chat reaches it
        through `resolve_outcome`, MCP also reaches it for an `MCP_Access_Token`'s stored hash,
        so revocation and plan edits bind both the same way."""
        try:
            principal = await self._cache.resolve(key_hash, self._fetch)
        except ProjectInvalid:
            # GW31: the key record is signed but malformed; nothing about it is trusted.
            return IdentityOutcome(None, posture.PROJECT_INVALID)
        except StoreDataUnavailable:
            # Stale / unavailable freshness, or the store did not answer: the key may be
            # valid, so this is a 503, never a 401 (R4.3).
            return IdentityOutcome.unverified()
        if principal is None:
            # Verified state holds no live key: revoked, expired or never issued (R4.2).
            return IdentityOutcome.invalid()
        return IdentityOutcome.resolved(
            TenantIdentity(org_id=principal.org_id, principal=principal),
        )


# --------------------------------------------------------------------------- #
# A fail-closed default resolver (so an unwired ChatRoute still refuses safely).
# --------------------------------------------------------------------------- #


class _FailClosedResolver:
    """An :class:`IdentityResolver` that resolves nothing — the fail-closed default.

    The default ``ChatRoute.identity`` for a deployment / test that has not yet wired the
    real :class:`CachingIdentityResolver` (task 5/6). It resolves EVERY request to ``None``,
    so the lifecycle refuses with the declared ``shared_state_unavailable`` code and makes
    zero provider calls — never a fail-OPEN hole. ``__slots__`` so there is no instance
    state; a single shared instance (:data:`fail_closed_resolver`) is safe to reuse.
    """

    __slots__ = ()

    async def resolve(self, scope: ASGIScope) -> TenantIdentity | None:
        """Resolve nothing: always ``None`` (fail closed)."""
        del scope
        return None


#: The shared fail-closed default resolver. An immutable, stateless instance (not a module-
#: level mutable), used as the ``ChatRoute.identity`` default until the real resolver is
#: wired at boot (task 5/6). Fail closed: every request refuses, zero provider calls.
fail_closed_resolver: IdentityResolver = _FailClosedResolver()
