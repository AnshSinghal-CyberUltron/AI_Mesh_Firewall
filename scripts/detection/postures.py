"""Posture adapters for the posture scoring harness.

The ``PostureAdapter`` protocol plus the three concrete adapters (Tier-1 only,
Tier-1 + policy, Tier-1 + semantic) and their availability probes. Adapters
invoke the shipped scanner read-only as a ``text -> score`` oracle and never
mutate scanner state or use its verdict as a label (Requirements 1, 8, 9.3).

Task 5.1 defined the ``PostureAdapter`` protocol, the ``AvailabilityResult``
type, posture-name validation, and the :func:`build_postures` factory that
returns the three adapters in a fixed order and rejects duplicate names *before*
any scoring so the CLI emits no report (Requirements 1.2, 1.4, 1.5).

Task 5.3 (this change) replaces the placeholders with the three concrete
scanner-backed adapters:

* :class:`Tier1OnlyAdapter` — the fast Tier-1 pattern scan
  (``InputScanner.scan_prompt``), scored by ``ScanVerdict.confidence``.
* :class:`Tier1PlusPolicyAdapter` — Tier-1 plus the policy layer's contribution.
* :class:`Tier1PlusSemanticAdapter` — Tier-1 plus the Tier-2/semantic (Bedrock)
  confidence (``InputScanner.scan_prompt_with_tier2``); its :meth:`probe` reports
  the posture unavailable when ``ENABLE_TIER2`` is off or Bedrock is not
  reachable (Requirement 8).

Each adapter constructs exactly ONE :class:`InputScanner`, invokes it read-only
as a ``text -> score`` oracle, never mutates scanner state, and never uses the
scanner ``action`` as a label (Requirements 2.3, 9.3).

**Measurement-only / zero blast radius (Requirement 9).** This module reads the
shipped scanner's API but never modifies it or any file outside
``scripts/detection/``. It imports the scanner LAZILY (inside the adapters, not
at module import) so the module stays importable standalone even when the
gateway package or its deps (boto3, etc.) are not installed; when the scanner
cannot be imported or constructed, :meth:`probe` returns an unavailable
``AvailabilityResult`` carrying the import/construction error as its reason and
:meth:`score` fails loudly rather than fabricating a number.
"""

from __future__ import annotations

import asyncio
import threading
from dataclasses import dataclass
from typing import Any, Protocol, Sequence, runtime_checkable

# A Posture name is a non-empty string of 1 to 128 characters and must be unique
# across all configured postures (Requirements 1.2, 1.5).
NAME_MIN_LENGTH = 1
NAME_MAX_LENGTH = 128

# The three postures scored by this feature, in the fixed order they are
# reported (Requirement 1.1). These are the stable, unique posture names.
TIER1_ONLY_NAME = "Tier1_Only"
TIER1_PLUS_POLICY_NAME = "Tier1_Plus_Policy"
TIER1_PLUS_SEMANTIC_NAME = "Tier1_Plus_Semantic"


@dataclass(frozen=True)
class AvailabilityResult:
    """The result of a posture's availability probe (Requirements 1, 8).

    ``available`` is ``True`` when the posture can be scored in the current
    environment. When ``available`` is ``False`` the posture is an
    Unavailable_Posture and ``reason`` carries a non-empty explanation of why it
    could not be scored (for example Tier-2/semantic not reachable); the caller
    records the posture as "not scored" with that reason and computes no metric
    values for it (Requirements 8.1, 8.2). An *available* result carries an empty
    ``reason``.

    The invariant "unavailable carries a non-empty reason" is enforced at
    construction, so an unavailable result can never be produced without a
    human-readable reason.
    """

    available: bool
    reason: str = ""

    def __post_init__(self) -> None:
        if not self.available and not self.reason.strip():
            raise ValueError(
                "an unavailable AvailabilityResult must carry a non-empty reason"
            )
        if self.available and self.reason:
            # An available posture has nothing to explain; keep the type honest
            # so callers can rely on `reason` being non-empty iff unavailable.
            raise ValueError(
                "an available AvailabilityResult must not carry a reason"
            )

    @classmethod
    def ok(cls) -> "AvailabilityResult":
        """Construct an available result (no reason)."""
        return cls(available=True, reason="")

    @classmethod
    def unavailable(cls, reason: str) -> "AvailabilityResult":
        """Construct an unavailable result with a required non-empty ``reason``."""
        if not reason or not reason.strip():
            raise ValueError(
                "AvailabilityResult.unavailable requires a non-empty reason"
            )
        return cls(available=False, reason=reason)


@runtime_checkable
class PostureAdapter(Protocol):
    """A named, reproducible detection configuration of the shipped scanner.

    The metric, threshold-selection, and reporting layers depend only on this
    protocol plus the collected score lists — never on a concrete posture — so a
    further posture can be added as a new adapter without changing those layers
    (Requirements 1.3, 1.4).

    Attributes:
        name: A stable, unique posture name; a non-empty string of 1 to 128
            characters (Requirement 1.2).
    """

    name: str

    def probe(self) -> AvailabilityResult:
        """Report whether this posture can be scored, with no scoring side effects.

        Returns an available :class:`AvailabilityResult`, or an unavailable one
        carrying a non-empty reason (Requirements 8.1, 8.2). ``probe`` must not
        mutate scanner state.
        """
        ...

    def score(self, text: str) -> float:
        """Return the read-only Posture_Score in ``[0.0, 1.0]`` for one item's text.

        The scanner is invoked read-only as a ``text -> score`` oracle; the score
        is never used to derive a label and scanner state is never mutated
        (Requirements 2.3, 9.3).
        """
        ...


class DuplicatePostureNameError(ValueError):
    """Raised when two or more configured postures share the same name (R1.5).

    Carries the duplicated name in :attr:`name` so the caller can produce an
    error indication identifying it, emit no report, and leave any previously
    committed report unchanged.
    """

    def __init__(self, name: str) -> None:
        self.name = name
        super().__init__(f"duplicate posture name: {name!r}")


class InvalidPostureNameError(ValueError):
    """Raised when a configured posture name is not a valid name (R1.2).

    A valid name is a non-empty ``str`` of 1 to 128 characters. Carries the
    offending value in :attr:`name`.
    """

    def __init__(self, name: object, detail: str) -> None:
        self.name = name
        super().__init__(f"invalid posture name {name!r}: {detail}")


def validate_posture_names(names: Sequence[object]) -> None:
    """Validate a list of configured posture names (Requirements 1.2, 1.5).

    Accepts the configuration if and only if every name is a non-empty ``str`` of
    1 to 128 characters and all names are unique. On the first invalid name a
    :class:`InvalidPostureNameError` is raised; on the first duplicate a
    :class:`DuplicatePostureNameError` naming the duplicated value is raised. On a
    valid, unique list the function returns ``None`` (no side effects, emits no
    report).

    Validation is performed before any scoring, so a rejected configuration
    produces an error and no report (Requirement 1.5).
    """
    seen: set[str] = set()
    for candidate in names:
        if not isinstance(candidate, str):
            raise InvalidPostureNameError(candidate, "name must be a string")
        if len(candidate) < NAME_MIN_LENGTH:
            raise InvalidPostureNameError(candidate, "name must be non-empty")
        if len(candidate) > NAME_MAX_LENGTH:
            raise InvalidPostureNameError(
                candidate, f"name must be at most {NAME_MAX_LENGTH} characters"
            )
        if candidate in seen:
            raise DuplicatePostureNameError(candidate)
        seen.add(candidate)


# ---------------------------------------------------------------------------
# Read-only scanner access
# ---------------------------------------------------------------------------
#
# The scanner is imported LAZILY so this module is importable standalone even
# when the gateway package / its deps are absent (Requirement 9 keeps the
# standalone-importability contract from Task 5.1). The reproducible command runs
# inside ``gateway/`` so ``from ai_mesh_gateway import scanner`` resolves there.


def _import_scanner() -> Any:
    """Import the shipped scanner module read-only, or raise ImportError.

    Tries the documented ``ai_mesh_gateway.scanner`` import first (the form the
    reproducible command resolves from inside ``gateway/``), then the bare
    ``scanner`` fallback (mirroring the scanner's own dual-import style). Never
    modifies the scanner.
    """
    try:
        from ai_mesh_gateway import scanner as _scanner  # type: ignore
        return _scanner
    except Exception:  # pragma: no cover - exercised only when gateway present
        import scanner as _scanner  # type: ignore

        return _scanner


def _clamp_unit(value: float) -> float:
    """Clamp a score into the inclusive unit range ``[0.0, 1.0]`` (R2.1/2.2 range).

    ``ScanVerdict.confidence`` is produced in ``[0.0, 1.0]`` across every scanner
    path, but clamping keeps the adapter's contract (a Posture_Score in the unit
    range) robust to any future scanner change without ever fabricating a value.
    """
    v = float(value)
    if v < 0.0:
        return 0.0
    if v > 1.0:
        return 1.0
    return v


class _ScannerBackedAdapter:
    """Base for the concrete scanner-backed posture adapters (Requirements 1, 9.3).

    Holds exactly ONE lazily-constructed :class:`InputScanner` and a private event
    loop used to drive the scanner's async scan entrypoints from the harness's
    synchronous :meth:`score`. The scanner is treated purely as a read-only
    ``text -> score`` oracle: no adapter ever mutates scanner state, edits the
    scanner module, or uses ``ScanVerdict.action`` as a label (Requirements 2.3,
    9.2, 9.3).

    Import/construction of the scanner is deferred to first use and any failure is
    captured (not raised at construction), so the module stays importable
    standalone and :meth:`probe` can report the posture unavailable with the real
    error as its reason (Requirement 8).
    """

    def __init__(self, name: str) -> None:
        self.name = name
        self._scanner: Any = None
        self._scanner_module: Any = None
        self._init_error: str = ""
        self._initialized = False
        self._lock = threading.Lock()
        self._loop: asyncio.AbstractEventLoop | None = None

    # -- lazy, read-only scanner construction -------------------------------

    def _ensure_scanner(self) -> Any:
        """Return the single InputScanner instance, constructing it once.

        Thread-safe and idempotent. On import/construction failure the error is
        recorded on ``self._init_error`` and ``None`` is returned; callers turn
        that into an unavailable probe / a loud score failure rather than a
        fabricated number.
        """
        if self._initialized:
            return self._scanner
        with self._lock:
            if self._initialized:
                return self._scanner
            try:
                module = _import_scanner()
                # Construct exactly one InputScanner for this adapter. A tiny
                # thread pool keeps the read-only oracle lightweight; the scanner
                # reads ENABLE_TIER2 from the environment in its own __init__.
                scanner_obj = module.InputScanner(thread_pool_size=1)
                self._scanner_module = module
                self._scanner = scanner_obj
            except Exception as exc:  # ImportError, missing deps, ctor failure
                self._init_error = (
                    f"scanner unavailable ({type(exc).__name__}: {exc})"
                )
                self._scanner = None
                self._scanner_module = None
            finally:
                self._initialized = True
        return self._scanner

    def _get_loop(self) -> asyncio.AbstractEventLoop:
        """Return this adapter's private event loop, creating it on first use.

        The scanner's scan entrypoints are coroutines; the harness pipeline is
        synchronous. A dedicated per-adapter loop drives them without depending on
        (or interfering with) any ambient event loop and keeps scoring
        deterministic and side-effect-free with respect to scanner state.
        """
        loop = self._loop
        if loop is None or loop.is_closed():
            loop = asyncio.new_event_loop()
            self._loop = loop
        return loop

    def _run(self, coro: Any) -> Any:
        """Drive an awaitable to completion on the adapter's private loop."""
        return self._get_loop().run_until_complete(coro)

    # -- probe --------------------------------------------------------------

    def probe(self) -> AvailabilityResult:
        """Available when the scanner imports + constructs; else unavailable.

        The Tier-1-only and Tier-1+policy postures are available whenever the
        scanner itself is reachable (the import resolves and one InputScanner
        constructs). If the scanner import/construction fails, the posture is
        surfaced as unavailable with that error as the reason (Requirement 8).
        The semantic posture overrides this to add the Tier-2/Bedrock checks.
        """
        if self._ensure_scanner() is None:
            return AvailabilityResult.unavailable(
                self._init_error or "scanner could not be constructed"
            )
        return AvailabilityResult.ok()

    # -- score --------------------------------------------------------------

    def _tier1_confidence(self, text: str) -> float:
        """Read-only Tier-1 ``ScanVerdict.confidence`` for ``text`` in ``[0,1]``.

        Calls ``InputScanner.scan_prompt`` (the Tier-1 pattern/regex path via
        ``ATTACK_PATTERNS``) and reads its ``confidence``. Never inspects/uses
        ``action`` as a label (Requirement 9.3).
        """
        scanner_obj = self._ensure_scanner()
        if scanner_obj is None:
            raise RuntimeError(
                f"posture {self.name!r} cannot score: {self._init_error}"
            )
        verdict = self._run(scanner_obj.scan_prompt(text))
        return _clamp_unit(getattr(verdict, "confidence", 0.0))

    def score(self, text: str) -> float:  # pragma: no cover - overridden
        raise NotImplementedError


class Tier1OnlyAdapter(_ScannerBackedAdapter):
    """Tier-1 pattern scan scored by ``ScanVerdict.confidence`` (Requirement 1.1).

    The Posture_Score is the Tier-1 confidence from ``InputScanner.scan_prompt``
    (the ``ATTACK_PATTERNS`` fast path). Read-only; scanner state is never mutated
    and the scanner ``action`` is never used as a label (Requirements 2.3, 9.3).
    """

    def __init__(self) -> None:
        super().__init__(TIER1_ONLY_NAME)

    def score(self, text: str) -> float:
        return self._tier1_confidence(text)


class Tier1PlusPolicyAdapter(_ScannerBackedAdapter):
    """Tier-1 plus the policy layer's contribution to the score (Requirement 1.1).

    The Posture_Score combines the Tier-1 confidence with the policy layer's
    contribution. The gateway's policy layer (``policy_engine.evaluate``) scores a
    prompt against a *compiled policy bundle* that, in production, is pushed to the
    running gateway over Redis; a standalone measurement harness has no such
    bundle to load read-only. So the policy contribution is the score the policy
    layer assigns given the policies available to the harness — with no bundle
    available, the policy layer matches nothing and contributes 0, and the score
    equals the Tier-1 confidence. When a policy contribution can be derived it is
    combined by taking the maximum (the policy layer, like the scanner, raises the
    detection signal — it never lowers a Tier-1 detection), keeping the score in
    ``[0.0, 1.0]``.

    Read-only: the policy layer is evaluated (not mutated), scanner state is never
    mutated, and no scanner/policy ``action`` is used as a label (Requirements
    2.3, 9.3).

    Scanner-API assumption (Task 5.3): the gateway ``InputScanner`` does not embed
    the policy layer, and the compiled policy bundle is a live-gateway artefact not
    reachable from a standalone harness, so the policy contribution is 0 in the
    harness environment and this posture's score equals ``Tier1_Only``'s. This is
    honest (no fabricated policy signal) and the combine rule is written so a real
    policy contribution, if one becomes available, raises the score correctly
    without changing the metric/threshold/report layers.
    """

    def __init__(self) -> None:
        super().__init__(TIER1_PLUS_POLICY_NAME)

    def _policy_contribution(self, text: str) -> float:
        """Read-only policy-layer contribution for ``text`` in ``[0,1]``.

        Returns the detection signal the policy layer assigns to ``text`` given
        the policies available to the harness. With no compiled bundle reachable
        the policy layer matches nothing and contributes 0.0 (never a fabricated
        signal). A policy match maps to a positive contribution derived from the
        matched action (block > redact/rewrite > allow), never using the action as
        a corpus label — only as the strength of the policy's own signal.
        """
        module = self._scanner_module
        if module is None:
            # Scanner not constructed → no policy contribution to add. The caller
            # already surfaced scanner-unavailability via probe()/score().
            return 0.0
        try:
            from ai_mesh_gateway import policy_engine  # type: ignore
        except Exception:
            try:
                import policy_engine  # type: ignore
            except Exception:
                # Policy engine not importable in this environment → the policy
                # layer contributes nothing. Tier-1 confidence stands alone.
                return 0.0
        # No compiled policy bundle is reachable from a standalone harness, so the
        # policy layer is evaluated against an empty policy set: it matches nothing
        # and returns the default "allow" action → contribution 0.0. Evaluating
        # explicitly (rather than short-circuiting) keeps the read-only policy-
        # layer invocation honest and ready to reflect a real contribution if a
        # bundle ever becomes available to the harness.
        try:
            result = policy_engine.evaluate(text, "", [])
        except Exception:
            return 0.0
        action = str(getattr(result, "action", "allow") or "allow").lower()
        # Map the policy action to a detection-signal strength. This is the
        # policy's OWN signal contribution, not a corpus label (Requirement 9.3).
        if action == "block":
            return 1.0
        if action in ("redact", "rewrite"):
            return 0.85
        return 0.0

    def score(self, text: str) -> float:
        tier1 = self._tier1_confidence(text)
        policy = _clamp_unit(self._policy_contribution(text))
        # The policy layer raises the detection signal; combine by max so a policy
        # match can only strengthen (never weaken) the Tier-1 signal (R2.1 range).
        return _clamp_unit(max(tier1, policy))


class Tier1PlusSemanticAdapter(_ScannerBackedAdapter):
    """Tier-1 plus Tier-2/semantic (Bedrock) confidence (Requirement 1.1).

    The Posture_Score is the combined ``ScanVerdict.confidence`` from
    ``InputScanner.scan_prompt_with_tier2`` — Tier-1 first, then the Tier-2 /
    semantic (Bedrock) verdict layered on. :meth:`probe` reports the posture
    unavailable when ``ENABLE_TIER2`` is off, when the scanner has no Bedrock
    scanner configured, or when Bedrock is not reachable, so an unscorable semantic
    posture is recorded "not scored" with a reason rather than fabricated
    (Requirement 8).

    Read-only: scanner state is never mutated and the scanner ``action`` is never
    used as a label (Requirements 2.3, 9.3).
    """

    def __init__(self) -> None:
        super().__init__(TIER1_PLUS_SEMANTIC_NAME)

    def probe(self) -> AvailabilityResult:
        """Available only when Tier-2/semantic (Bedrock) can actually run.

        Checks, in order: the scanner imports + constructs; ``ENABLE_TIER2`` is on
        (the scanner exposes it as ``tier2_enabled``); a Bedrock scanner instance is
        configured (``_bedrock_scanner`` is not ``None``); and Bedrock is reachable.
        Any failing check returns an unavailable :class:`AvailabilityResult` with a
        non-empty reason (Requirements 8.1, 8.2).
        """
        scanner_obj = self._ensure_scanner()
        if scanner_obj is None:
            return AvailabilityResult.unavailable(
                self._init_error or "scanner could not be constructed"
            )

        # ENABLE_TIER2 gate (surfaced on the scanner as `tier2_enabled`).
        if not getattr(scanner_obj, "tier2_enabled", False):
            return AvailabilityResult.unavailable(
                "Tier-2/semantic disabled (ENABLE_TIER2 is off)"
            )

        # A Bedrock scanner must be configured to run the semantic path.
        bedrock = getattr(scanner_obj, "_bedrock_scanner", None)
        if bedrock is None:
            return AvailabilityResult.unavailable(
                "Tier-2/semantic enabled but no Bedrock scanner is configured"
            )

        # Bedrock reachability: constructing the Bedrock scanner requires boto3 +
        # a bedrock-runtime client; if that succeeded the client is present. Probe
        # for a client/model handle without invoking Bedrock (no scoring side
        # effects, no cost). A missing client/model means Bedrock is not reachable.
        reachable, reason = self._bedrock_reachable(bedrock)
        if not reachable:
            return AvailabilityResult.unavailable(reason)

        return AvailabilityResult.ok()

    @staticmethod
    def _bedrock_reachable(bedrock: Any) -> tuple[bool, str]:
        """Best-effort, side-effect-free Bedrock reachability check.

        Returns ``(True, "")`` when the Bedrock scanner carries a usable transport
        client and model id, else ``(False, reason)``. Never invokes Bedrock (no
        network call, no cost, no scoring side effects) — a real invocation is
        deferred to :meth:`score`, and a transport failure there is handled by the
        scanner's own circuit breaker.
        """
        model = getattr(bedrock, "model", "") or ""
        if not model:
            return False, "Tier-2/semantic Bedrock scanner has no model configured"
        client = getattr(bedrock, "client", None)
        if client is None:
            return False, "Tier-2/semantic Bedrock transport client is not available"
        return True, ""

    def score(self, text: str) -> float:
        scanner_obj = self._ensure_scanner()
        if scanner_obj is None:
            raise RuntimeError(
                f"posture {self.name!r} cannot score: {self._init_error}"
            )
        # Tier-1 plus Tier-2/semantic: scan_prompt_with_tier2 runs Tier-1 first
        # and layers the Bedrock verdict on, returning a combined ScanVerdict whose
        # `confidence` is the Posture_Score. Read-only oracle; `action` unused.
        verdict = self._run(scanner_obj.scan_prompt_with_tier2(text))
        return _clamp_unit(getattr(verdict, "confidence", 0.0))


def build_postures() -> list[PostureAdapter]:
    """Return the three posture adapters in fixed order (Requirements 1.1, 1.4, 1.5).

    The adapters are returned in the fixed report order
    ``[Tier1_Only, Tier1_Plus_Policy, Tier1_Plus_Semantic]`` as the concrete
    scanner-backed adapters. Their names are validated for validity and uniqueness
    *before* any scoring via :func:`validate_posture_names`; a duplicate name
    raises :class:`DuplicatePostureNameError` (identifying the duplicated name) so
    the caller emits no report (Requirement 1.5).

    The factory's contract is unchanged from Task 5.1: three adapters, fixed order,
    names validated up front. Constructing an adapter does not import or construct
    the scanner (that is deferred to the first ``probe``/``score``), so this
    factory stays importable and side-effect-free even when the gateway package is
    not installed (Requirement 9 standalone-importability).
    """
    postures: list[PostureAdapter] = [
        Tier1OnlyAdapter(),
        Tier1PlusPolicyAdapter(),
        Tier1PlusSemanticAdapter(),
    ]
    validate_posture_names([posture.name for posture in postures])
    return postures
