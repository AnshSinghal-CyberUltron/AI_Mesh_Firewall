"""DEV PROFILE components: identity, plan, detectors, resolver, echo provider, catalog.

Everything in this module is wired ONLY when ``AMF_DEV_PROFILE=1`` (see
:mod:`gateway_v2.edge.dev_profile`). The default boot path never constructs any of it, so the
shipped Fail_Closed_Fallback table in :mod:`gateway_v2.edge.serve` is unchanged.

Each component is the thinnest honest adapter over a REAL shipped piece:

* :class:`DevStaticIdentity` — resolves exactly ONE static bearer key (``hmac.compare_digest``)
  to a :class:`~gateway_v2.edge.identity_resolver.TenantIdentity`; anything else is ``None``, so
  the shipped lifecycle refuses with its declared identity code (fail closed).
* :func:`load_dev_plan` — the plan file is decoded by the REAL ``plan.document.decode_plan_body``
  and compiled by the REAL ``plan.compiler.compile_plan``; a :class:`CompileError` is a refuse-to-
  start, never a silent fallback to an empty plan.
* :func:`warm_dev_snapshot_source` — a warmed :class:`~gateway_v2.plan.snapshot.ReplicaSnapshot`
  over a :class:`~gateway_v2.plan.store.PlanStore` holding the compiled plan.
* :class:`DeterministicFindings` — the GW09 deterministic detectors (PII / secret / injection
  signal) adapted to the ``(text) -> Sequence[Finding]`` shape the input scan, the non-streaming
  output scan, the error-frame scan and the streaming completed-match seam all take.
* :func:`phase_resolver_factory` — a :data:`~gateway_v2.edge.phase_resolvers.PhaseResolverFactory`
  that binds the REAL GW07 :class:`~gateway_v2.resolve.PlanResolver` to the request's pinned plan
  AND a specific :class:`~gateway_v2.domain.plan.Phase`. Wired onto both routes (Group E), it lets
  the input scan resolve at ``Phase.INPUT`` and the output guard at ``Phase.OUTPUT``, so an
  ``input``-scoped rule is enforced only on input and an ``output``-scoped rule only on output.
  :func:`require_phase_agnostic` therefore no longer refuses phase-specific rules; it only refuses
  a plan with no enforcing rule at all.
* :class:`EchoProviderClient` — a socket-free provider that streams back EXACTLY the text the
  gateway forwarded, so a user can see what reached the "model".
* :class:`StaticCatalogSource` — a fixed model list for ``/v1/models``.

``edge`` is the top layer, so importing ``detect`` / ``plan`` / ``resolve`` / ``dispatch`` here
adds only downward edges. No capacity literal, no module-level mutable.
"""

from __future__ import annotations

import hmac
import sys
from collections.abc import AsyncIterator, Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from gateway_v2.detect.base import DetectorContext
from gateway_v2.detect.deterministic.detectors import (
    InjectionSignalDetector,
    PiiDetector,
    SecretDetector,
)
from gateway_v2.detect.guard.backend import Budget
from gateway_v2.dispatch.provider import UpstreamEvent, UpstreamRequest
from gateway_v2.dispatch.transform import BODY_DELTAS_KEY
from gateway_v2.domain import Finding, FindingStatus
from gateway_v2.domain.identity import Principal
from gateway_v2.domain.plan import ExecutionPlan, Phase
from gateway_v2.edge.app import CatalogModel
from gateway_v2.edge.identity_resolver import ASGIScope, TenantIdentity, bearer_key
from gateway_v2.edge.phase_resolvers import PhaseResolverFactory
from gateway_v2.egress.output_guard import OutputResolver
from gateway_v2.plan.compiler import CompileError, compile_plan
from gateway_v2.plan.document import decode_plan_body
from gateway_v2.plan.snapshot import ReplicaSnapshot
from gateway_v2.plan.store import PlanStore
from gateway_v2.resolve import PlanResolver

__all__ = (
    "DEV_KEY_ID",
    "ECHO_PREFIX",
    "DeterministicFindings",
    "DevProfileError",
    "DevStaticIdentity",
    "EchoProviderClient",
    "StaticCatalogSource",
    "env_text",
    "load_dev_plan",
    "parse_model_ids",
    "phase_resolver_factory",
    "require_phase_agnostic",
    "summarize_findings",
    "warm_dev_snapshot_source",
)

#: The ``Principal.key_id`` the dev identity carries. A label, never the key itself — the raw
#: bearer value is compared once with ``hmac.compare_digest`` and never stored on the principal.
DEV_KEY_ID = "dev-profile-static-key"

#: Prefix the echo provider puts in front of the text it received (a wire-shape string).
ECHO_PREFIX = "echo: "

#: The output channel the echo reply is streamed on (the ``content`` delta key the OpenAI
#: renderer and the SSE codec both map to ``message.content``).
_ECHO_CHANNEL = "content"


class DevProfileError(RuntimeError):
    """A required DEV PROFILE input is missing or invalid — the server refuses to start.

    Raised at boot only (never on the request path). ``serve.main`` maps it to the shipped
    ``_refuse_start`` contract (stderr JSON + exit 2), so an opted-in dev profile never silently
    falls back to a partially-wired app.
    """


# --------------------------------------------------------------------------- #
# Identity — one static bearer key.
# --------------------------------------------------------------------------- #


class DevStaticIdentity:
    """An :class:`~gateway_v2.edge.identity_resolver.IdentityResolver` for ONE static key.

    The bearer token is named by the shipped :func:`bearer_key` and compared with
    :func:`hmac.compare_digest` (constant-time in the length of the inputs). A missing, malformed,
    or wrong key resolves to ``None``, so the lifecycle refuses with its declared identity code and
    makes zero provider calls — the same fail-closed contract as the real resolver.
    """

    __slots__ = ("_expected", "_identity")

    def __init__(self, *, api_key: str, org_id: str) -> None:
        if not api_key:
            raise DevProfileError("AMF_DEV_API_KEY must be a non-empty string")
        self._expected = api_key.encode("utf-8")
        self._identity = TenantIdentity(
            org_id=org_id,
            principal=Principal(
                key_id=DEV_KEY_ID,
                org_id=org_id,
                # Not consulted on the request path (GW06 reads its GCRA params from the quota
                # component, not the principal); carried as 0.0 rather than a fabricated rate.
                rate_per_s=0.0,
                burst=0.0,
                feed_seq=0,
            ),
        )

    async def resolve(self, scope: ASGIScope) -> TenantIdentity | None:
        """Resolve the one dev tenant iff the bearer key matches; else ``None`` (fail closed)."""
        presented = bearer_key(scope)
        if presented is None:
            return None
        if not hmac.compare_digest(presented.encode("utf-8"), self._expected):
            return None
        return self._identity


# --------------------------------------------------------------------------- #
# Plan — decoded + compiled by the REAL plan layer.
# --------------------------------------------------------------------------- #


def load_dev_plan(path: str, *, org_id: str, compiled_at: float) -> ExecutionPlan:
    """Read, decode and compile the dev plan file through the shipped plan layer.

    The file body is the SAME JSON shape the control plane signs (``plan.document``:
    ``org_id`` / ``streaming_mode`` / ``rules[]``), decoded by ``decode_plan_body`` and compiled by
    ``compile_plan`` — one validation authority. Any read / decode / compile failure, an
    ``org_id`` mismatch, or a plan the single shared resolver cannot honour raises
    :class:`DevProfileError` (refuse to start).
    """
    file_path = Path(path)
    try:
        raw = file_path.read_bytes()
    except OSError as exc:
        raise DevProfileError(f"AMF_DEV_PLAN_PATH={path!r} cannot be read: {exc}") from exc
    try:
        document = decode_plan_body(raw)
        plan = compile_plan(
            document.org_id,
            document.drafts,
            streaming_mode=document.streaming_mode,
            compiled_at=compiled_at,
        )
    except CompileError as exc:
        raise DevProfileError(f"AMF_DEV_PLAN_PATH={path!r} rejected by compiler: {exc}") from exc
    if plan.org_id != org_id:
        raise DevProfileError(
            f"plan org_id {plan.org_id!r} does not match AMF_DEV_ORG_ID {org_id!r}"
        )
    require_phase_agnostic(plan)
    return plan


def require_phase_agnostic(plan: ExecutionPlan) -> None:
    """Refuse a plan with no enforcing rule (nothing would be enforced).

    The name is historical. The dev profile now wires a phase-aware resolver factory
    (:func:`phase_resolver_factory`) onto both routes, so a phase-specific rule
    (``scope: input`` / ``scope: output``) IS enforced faithfully on exactly its phase (Group E):
    the input scan resolves at ``Phase.INPUT`` and the output guard at ``Phase.OUTPUT``. Phase-
    specific rules are therefore no longer refused. The one remaining refusal is a plan with no
    active rules at all: it would let every finding through and make the dev profile look like it
    enforces when it does not.
    """
    if not plan.rules:
        raise DevProfileError("plan has no active rules; nothing would be enforced")


def warm_dev_snapshot_source(
    plan: ExecutionPlan,
    *,
    now: float,
) -> Callable[[], ReplicaSnapshot]:
    """A ``snapshot_source`` whose served snapshot resolves ``plan.org_id`` to ``plan``.

    Built per worker (inside the module-level ``serving_app`` factory), so the store / snapshot are
    instance state, not module state. ``ReplicaSnapshot`` is constructed without a freshness stamp
    — there is no control-plane feed in the dev profile, the plan is the file it was booted with.
    """
    store = PlanStore()
    store.put(plan)
    snapshot = ReplicaSnapshot(store)
    snapshot.absorb(plan.org_id, now=now)

    def _source() -> ReplicaSnapshot:
        return snapshot

    return _source


def phase_resolver_factory() -> PhaseResolverFactory:
    """A phase-aware factory over the REAL GW07 resolver (Group E, Property 11).

    Returns a :data:`~gateway_v2.edge.phase_resolvers.PhaseResolverFactory`: given the request's
    pinned :class:`~gateway_v2.domain.plan.ExecutionPlan` and a
    :class:`~gateway_v2.domain.plan.Phase`, it builds a
    :class:`~gateway_v2.resolve.PlanResolver` bound to BOTH. Wired onto the chat and
    misc routes via ``phase_resolvers``, it drives
    :func:`~gateway_v2.edge.phase_resolvers.resolver_for` so the input scan resolves at
    ``Phase.INPUT`` and the output guard at ``Phase.OUTPUT``. An ``input``-scoped rule is then
    enforced only on input and an ``output``-scoped rule only on output (closing leak E).

    ``PlanResolver`` satisfies the structural ``OutputResolver`` protocol (it exposes
    ``decide(matches)``); the return annotation names that contract so the factory's product is
    typed where it is consumed.
    """

    def _factory(plan: ExecutionPlan, phase: Phase) -> OutputResolver:
        return PlanResolver(plan=plan, phase=phase)

    return _factory


# --------------------------------------------------------------------------- #
# Detectors — GW09 deterministic detectors in the server's callable shape.
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class DeterministicFindings:
    """GW09 PII + secret + injection-signal detectors as one ``(text) -> findings`` callable.

    The shape every scan seam in ``edge`` takes (``InputDetector`` / ``ErrorFrameScanner`` /
    the streaming ``Detector``). ``phase`` is carried into the ``DetectorContext`` for fidelity;
    the deterministic detectors do not branch on it. ``budget_s`` is the guard remaining-time
    budget (C30) — irrelevant to an in-process regex detector (decode cost is bounded by the
    detectors' own ``DEFAULT_BUDGET``), passed from the contract so no literal lives here.
    A detector that cannot execute emits an UNAVAILABLE finding, which the resolver routes through
    the rule's ``on_unavailable`` posture (never empty-as-clean).
    """

    phase: Phase
    budget_s: float

    def __call__(self, text: str) -> tuple[Finding, ...]:
        context = DetectorContext(phase=self.phase, rules=(), budget=Budget(self.budget_s))
        findings: list[Finding] = []
        findings.extend(PiiDetector().detect(text, context))
        findings.extend(SecretDetector().detect(text, context))
        findings.extend(InjectionSignalDetector().detect(text, context))
        return tuple(findings)


def summarize_findings(findings: Sequence[Finding]) -> str:
    """``category=count`` summary of EXECUTED findings (no content, no spans)."""
    counts: dict[str, int] = {}
    for finding in findings:
        if finding.status is FindingStatus.EXECUTED:
            key = finding.category.value
            counts[key] = counts.get(key, 0) + 1
    if not counts:
        return "none"
    return ",".join(f"{key}={counts[key]}" for key in sorted(counts))


# --------------------------------------------------------------------------- #
# Provider — the echo "model".
# --------------------------------------------------------------------------- #


def _received_texts(request: UpstreamRequest) -> tuple[str, ...]:
    """The text of every ``(channel, text)`` delta the gateway forwarded, in order."""
    raw = request.body.get(BODY_DELTAS_KEY, ())
    texts: list[str] = []
    if isinstance(raw, (tuple, list)):
        for item in raw:
            if isinstance(item, (tuple, list)) and len(item) == 2 and isinstance(item[1], str):
                texts.append(item[1])
    return tuple(texts)


def _word_chunks(text: str) -> tuple[str, ...]:
    """Split ``text`` into whitespace-preserving word chunks (a realistic token stream)."""
    chunks: list[str] = []
    current = ""
    for char in text:
        current += char
        if char.isspace():
            chunks.append(current)
            current = ""
    if current:
        chunks.append(current)
    return tuple(chunks)


class EchoProviderClient:
    """A socket-free :class:`~gateway_v2.dispatch.provider.ProviderClient` that echoes its input.

    ``open`` streams back ``"echo: " + <the text the gateway forwarded>`` (multiple input channels
    joined by a newline) as word-sized ``content`` deltas, the last one ``final=True``. It holds
    NO per-request state: the single instance on the frozen ``ChatRoute`` is shared by every
    request, so a shared abort latch (the ``StubProviderClient`` shape) would let one request's
    abort silence every later one. ``abort`` is therefore a no-op — there is no upstream read to
    cut and each echo stream completes in-process.

    ``probe`` (optional) re-runs the deterministic detectors over what the provider RECEIVED and
    writes one stderr line per request with category counts only (never content). It exists so the
    operator can SEE the provider-side finding counts for visibility: the shipped lifecycle now
    masks input REDACT findings before the provider (Group F), so a clean probe line is the
    expected case; any residual finding count is a diagnostic signal, not a false claim.
    """

    __slots__ = ("_probe",)

    def __init__(self, *, probe: Callable[[str], Sequence[Finding]] | None = None) -> None:
        self._probe = probe

    def open(self, request: UpstreamRequest) -> AsyncIterator[UpstreamEvent]:
        """Stream the echo of ``request``'s forwarded text."""
        return self._stream(request)

    async def _stream(self, request: UpstreamRequest) -> AsyncIterator[UpstreamEvent]:
        received = "\n".join(_received_texts(request))
        if self._probe is not None:
            summary = summarize_findings(self._probe(received))
            sys.stderr.write(
                f"dev-echo: provider received {len(received)} chars; "
                f"provider-side findings: {summary}\n"
            )
            sys.stderr.flush()
        chunks = _word_chunks(ECHO_PREFIX + received)
        last = len(chunks) - 1
        for index, chunk in enumerate(chunks):
            yield UpstreamEvent(text_deltas=((_ECHO_CHANNEL, chunk),), final=index == last)

    async def abort(self) -> None:
        """No-op: the echo has no upstream read to cut (see the class docstring)."""
        return None


# --------------------------------------------------------------------------- #
# Catalog — a static model list.
# --------------------------------------------------------------------------- #


class StaticCatalogSource:
    """A :class:`~gateway_v2.edge.app.CatalogSource` serving a fixed model list to its one org.

    The ``created`` stamp is the boot time (epoch seconds) supplied by the caller; ``owned_by`` is
    the dev org. An identity for any other org sees an empty catalog (never another tenant's).
    """

    __slots__ = ("_models", "_org_id")

    def __init__(self, *, model_ids: Sequence[str], org_id: str, created: int) -> None:
        self._org_id = org_id
        self._models = tuple(
            CatalogModel(id=model_id, created=created, owned_by=org_id) for model_id in model_ids
        )

    @property
    def models(self) -> tuple[CatalogModel, ...]:
        return self._models

    def catalog_for(self, identity: TenantIdentity) -> tuple[CatalogModel, ...]:
        if identity.org_id != self._org_id:
            return ()
        return self._models


def parse_model_ids(raw: str | None) -> tuple[str, ...]:
    """Parse ``AMF_DEV_MODELS`` (comma-separated); empty / missing ⇒ :class:`DevProfileError`.

    The compiled plan names no models (``ExecutionPlan`` carries no model field — routing is
    derived, not stored), so there is nothing to derive a default from and the list is required.
    """
    if raw is None:
        raise DevProfileError(
            "AMF_DEV_MODELS is required (comma-separated model ids); the plan names no models"
        )
    ids = tuple(part.strip() for part in raw.split(",") if part.strip())
    if not ids:
        raise DevProfileError("AMF_DEV_MODELS must name at least one model id")
    if len(set(ids)) != len(ids):
        raise DevProfileError(f"AMF_DEV_MODELS has duplicate ids: {ids!r}")
    return ids


def env_text(env: Mapping[str, str], name: str) -> str | None:
    """A stripped env value, ``None`` when unset or blank."""
    value = env.get(name)
    if value is None or not value.strip():
        return None
    return value.strip()
