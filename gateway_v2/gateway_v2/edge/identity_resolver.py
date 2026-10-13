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

**Fail closed (R4.2 / R4.3).** Three distinct conditions all reduce to the SAME
fail-closed answer — ``resolve`` returns ``None`` and the lifecycle refuses with the
declared ``shared_state_unavailable`` code (a 503, never a 401: the key may be valid, and
the part that is uncertain may be precisely the revocation):

* **No credential** on the request (missing / malformed ``Authorization`` header) — nothing
  to resolve.
* **Stale / unavailable freshness stamp** — :meth:`IdentityCache.resolve` raises
  :class:`~gateway_v2.domain.state.StoreDataUnavailable` while the GW05b/c stamp is not
  fresh, so the identity held in RAM is refused rather than trusted (SP1/SP2). Caught here
  and mapped to ``None``.
* **Unresolved principal** — a revoked / never-issued key resolves to ``None`` from the
  cache. Also ``None`` here.

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

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from gateway_v2.admit.identity import Fetch, IdentityCache
from gateway_v2.domain.identity import Principal
from gateway_v2.domain.state import StoreDataUnavailable

__all__ = (
    "CachingIdentityResolver",
    "IdentityResolver",
    "TenantIdentity",
    "fail_closed_resolver",
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


# --------------------------------------------------------------------------- #
# Credential extraction (pure; no I/O, no security decision beyond naming the key).
# --------------------------------------------------------------------------- #

#: The ASGI header name (lower-cased bytes, per the ASGI convention) carrying the API
#: credential, and the scheme prefix an OpenAI SDK sends. Plain byte/string constants, not
#: capacity-position literals.
_AUTHORIZATION_HEADER = b"authorization"
_BEARER_PREFIX = "Bearer "


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
        try:
            decoded = bytes(value).decode("latin-1")
        except UnicodeDecodeError:
            return None
        if decoded.startswith(_BEARER_PREFIX):
            token = decoded[len(_BEARER_PREFIX) :].strip()
            return token or None
        return None
    return None


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
        key_hash = self._key_namer(scope)
        if key_hash is None:
            return None
        try:
            principal = await self._cache.resolve(key_hash, self._fetch)
        except StoreDataUnavailable:
            # Stale / unavailable freshness stamp: refuse rather than trust RAM (R4.3).
            return None
        if principal is None:
            # Revoked / never-issued key: unresolved identity (R4.2).
            return None
        return TenantIdentity(org_id=principal.org_id, principal=principal)


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
