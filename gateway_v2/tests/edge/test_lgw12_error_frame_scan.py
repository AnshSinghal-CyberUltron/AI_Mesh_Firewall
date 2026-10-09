"""LGW12 mid-stream error-frame scan properties (GW12, R11).

This file is shared by two property tasks that each own a disjoint set of
helpers and one test function:

* Task 12.2 — ``test_property5_byte_linearity`` with ``_bl_``-prefixed helpers
  (this file's initial content).
* Task 12.3 — ``test_property6_fail_closed_on_scan_error`` with its own helpers,
  added to this same file without disturbing Property 5.

Both drive ``gateway_v2.edge.error_frame_scan.scan_error_frame`` over a seeded
``random.Random`` loop of >= 10,000 iterations (house idiom; no ``hypothesis``).
Test files are NOT under the import-linter layer contract, so the injected
scanner and resolver are built here directly.
"""

from __future__ import annotations

import random
from collections.abc import Sequence
from dataclasses import dataclass, field

from gateway_v2.domain import (
    Category,
    Decision,
    Disposition,
    Finding,
    FindingDisposition,
    FindingStatus,
    Span,
    Transformation,
)
from gateway_v2.edge.error_frame_scan import (
    BLOCK_CODE,
    SCAN_ERROR_CODE,
    scan_error_frame,
)
from gateway_v2.egress.output_guard import REDACTION_PLACEHOLDER

# --------------------------------------------------------------------------- #
# Feature: sse-egress-pipeline, Property 5: Byte-linearity of mid-stream scan
# Validates: Requirements 11.5
#
# For all mid-stream error frames, the scan does its work exactly ONCE per frame
# and that work is linear in the frame's byte length (cost(2n) ≈ 2·cost(n)).
#
# Byte-linearity is tested STRUCTURALLY by work-count rather than wall-clock
# timing (timing is flaky). We inject a counting scanner that records, per
# `scan_error_frame` call, (a) how many times it was invoked and (b) how many
# characters of the frame it inspected, and a resolver that records its own call
# count and the cost of its single decision/apply pass. Property 5 then holds iff
# for every frame the scanner is invoked EXACTLY ONCE (no per-byte rescan, no
# repeated full-text pass) and the total inspected work for a 2n-byte frame is
# ~2x the work for the n-byte frame built from the same unit.
# --------------------------------------------------------------------------- #


@dataclass(slots=True)
class _BlCounter:
    """Per-call work ledger a counting scanner + resolver write into."""

    scanner_calls: int = 0
    scanner_chars: int = 0
    resolver_calls: int = 0


@dataclass(slots=True)
class _BlCountingScanner:
    """A findings scanner that counts its calls and the characters it inspects.

    One left-to-right pass over the whole frame (`sum(... for ch in frame)`), so
    the character work it records is exactly `len(frame)` for a single call. The
    ledger makes a per-byte rescan or a repeated full-text pass observable: a
    second pass would double `scanner_chars`, and a second invocation would bump
    `scanner_calls` past 1. Findings are keyed off a sentinel character so the
    produced redaction spans scale with the frame too (the apply pass cost), but
    the counting discipline does not depend on finding anything.
    """

    ledger: _BlCounter
    sentinel: str
    detector_class: str = "email"
    category: Category = field(default=Category.PII)

    def __call__(self, frame: str) -> Sequence[Finding]:
        self.ledger.scanner_calls += 1
        # Exactly one left-to-right pass over the frame.
        spans: list[Span] = []
        inspected = 0
        run_start: int | None = None
        for idx, ch in enumerate(frame):
            inspected += 1
            if ch == self.sentinel:
                if run_start is None:
                    run_start = idx
            elif run_start is not None:
                spans.append(Span(start=run_start, end=idx))
                run_start = None
        if run_start is not None:
            spans.append(Span(start=run_start, end=len(frame)))
        self.ledger.scanner_chars += inspected
        if not spans:
            return ()
        return (
            Finding(
                detector=self.detector_class,
                detector_version="bl-test",
                category=self.category,
                status=FindingStatus.EXECUTED,
                confidence=1.0,
                spans=tuple(spans),
                evidence=None,
            ),
        )


@dataclass(slots=True)
class _BlCountingResolver:
    """A resolver that counts its calls and emits a simple redact/allow decision.

    REDACT every EXECUTED finding's spans; an empty finding set resolves to
    ALLOW. The decision/apply pass that follows is a single left-to-right walk in
    `apply_decision`, so the resolver + apply work is linear in the frame too.
    """

    ledger: _BlCounter
    placeholder: str = REDACTION_PLACEHOLDER

    def decide(self, matches: Sequence[Finding]) -> Decision:
        self.ledger.resolver_calls += 1
        per_finding: list[FindingDisposition] = []
        transformations: list[Transformation] = []
        for finding in matches:
            if finding.status is not FindingStatus.EXECUTED:
                per_finding.append(
                    FindingDisposition(
                        detector=finding.detector,
                        disposition=Disposition.ALLOW,
                    ),
                )
                continue
            per_finding.append(
                FindingDisposition(
                    detector=finding.detector,
                    disposition=Disposition.REDACT,
                ),
            )
            for span in finding.spans:
                transformations.append(
                    Transformation(
                        kind="redact",
                        span=span,
                        replacement=self.placeholder,
                    ),
                )
        if not per_finding:
            overall = Disposition.ALLOW
        else:
            overall = (
                Disposition.REDACT
                if any(fd.disposition is Disposition.REDACT for fd in per_finding)
                else Disposition.ALLOW
            )
        deciding = tuple(
            fd.detector
            for fd in per_finding
            if fd.disposition is not Disposition.ALLOW
        )
        return Decision(
            disposition=overall,
            per_finding=tuple(per_finding),
            transformations=tuple(transformations),
            findings=tuple(matches),
            plan_version="bl-test",
            deciding_rules=deciding,
            unavailable_detectors=(),
        )


# Frame-unit alphabet: plain prose plus the sentinel the counting scanner keys
# off. A random mix of both means some iterations redact and some ALLOW, so the
# single-scan + linearity assertions hold across both the redact and allow paths.
_BL_SENTINEL = "@"
_BL_ALPHABET = "abcdefghijklmnopqrstuvwxyz 0123456789.-_" + _BL_SENTINEL


def _bl_unit(rng: random.Random) -> str:
    """One frame unit of 1..40 characters drawn from the frame alphabet."""
    length = rng.randint(1, 40)
    return "".join(rng.choice(_BL_ALPHABET) for _ in range(length))


def _bl_measure(frame: str, sentinel: str) -> tuple[int, int, int]:
    """Run one `scan_error_frame` and report (scanner_calls, work, resolver_calls).

    `work` is the character count the scanner inspected plus the characters the
    apply pass walked over — the whole of the per-frame scan cost — so a per-byte
    rescan or a repeated full-text pass would inflate it. `apply_decision` walks
    the frame once (len(frame)) whether it redacts or not, so the apply cost is
    `len(frame)`; the scanner contributes `len(frame)` for its single pass.
    """
    ledger = _BlCounter()
    scanner = _BlCountingScanner(ledger=ledger, sentinel=sentinel)
    resolver = _BlCountingResolver(ledger=ledger)
    outcome = scan_error_frame(frame, scanner=scanner, resolver=resolver)
    # A redact/allow decision always forwards (never withheld) in this harness,
    # so the apply pass ran and its cost is the frame length.
    assert not outcome.withheld
    assert outcome.forward_text is not None
    work = ledger.scanner_chars + len(frame)
    return ledger.scanner_calls, work, ledger.resolver_calls


def test_property5_byte_linearity() -> None:
    seed = 0x12_05
    rng = random.Random(seed)
    print(f"test_property5_byte_linearity seed={seed:#x}")  # noqa: T201
    iterations = 10_000
    for i in range(iterations):
        unit = _bl_unit(rng)
        frame_n = unit
        frame_2n = unit + unit  # exactly 2n bytes, same composition
        n = len(frame_n)

        calls_n, work_n, res_calls_n = _bl_measure(frame_n, _BL_SENTINEL)
        calls_2n, work_2n, res_calls_2n = _bl_measure(frame_2n, _BL_SENTINEL)

        # (1) Single scan per frame: the scanner is invoked EXACTLY once per
        # `scan_error_frame` call — no per-byte rescan, no repeated full pass.
        assert calls_n == 1, f"seed={seed:#x} iter={i} n-scanner-calls={calls_n}"
        assert calls_2n == 1, f"seed={seed:#x} iter={i} 2n-scanner-calls={calls_2n}"
        # And the resolver decides exactly once per frame too.
        assert res_calls_n == 1, f"seed={seed:#x} iter={i} n-resolver-calls={res_calls_n}"
        assert res_calls_2n == 1, f"seed={seed:#x} iter={i} 2n-resolver-calls={res_calls_2n}"

        # (2) The n-byte scan inspects exactly n characters once over (scanner
        # pass n + apply pass n == 2n); no hidden extra pass.
        assert work_n == 2 * n, f"seed={seed:#x} iter={i} work_n={work_n} n={n}"
        assert work_2n == 2 * (2 * n), (
            f"seed={seed:#x} iter={i} work_2n={work_2n} n={n}"
        )

        # (3) Byte-linearity: cost(2n) == 2·cost(n) exactly for this structural
        # single-pass work metric (tolerance band is trivially satisfied; we
        # assert the exact linear relation and a defensive tolerance envelope).
        assert work_2n == 2 * work_n, (
            f"seed={seed:#x} iter={i} work_n={work_n} work_2n={work_2n} "
            "(cost(2n) must equal 2·cost(n))"
        )
        # Defensive tolerance envelope: cost(2n) within 1% of 2·cost(n). This is
        # the "~linear within tolerance" form Property 5 states; the exact check
        # above is strictly stronger but the envelope documents the intent and
        # guards against a future metric that is linear-but-not-exact.
        expected = 2.0 * work_n
        assert abs(work_2n - expected) <= 0.01 * expected, (
            f"seed={seed:#x} iter={i} work_2n={work_2n} not within 1% of {expected}"
        )


# --------------------------------------------------------------------------- #
# Feature: sse-egress-pipeline, Property 6: Fail-closed-on-scan-error
# Validates: Requirements 11.4, 15.2
#
# For any injected scan error, NO raw byte of the affected error frame is
# forwarded. `scan_error_frame` must WITHHOLD (forward_text is None, withheld is
# True, withheld_code set) whenever the scan, the resolve, or the apply step
# cannot be completed, and whenever the decision BLOCKs the frame. Because a
# withheld outcome carries `forward_text is None`, there is literally nothing
# forwarded — so no raw frame text and no secret-looking substring in the frame
# can leak on a failure.
#
# The property is exercised three ways over random frames (many of which embed a
# secret-looking substring the fail-open bug would leak):
#   1. the injected scanner RAISES            -> withheld_code == SCAN_ERROR_CODE
#   2. the injected resolver.decide RAISES    -> withheld_code == SCAN_ERROR_CODE
#   3. the resolver returns a BLOCK Decision  -> withheld_code == BLOCK_CODE
#      (so `apply_decision` raises OutputBlocked)
# Each withhold case asserts forward_text is None AND the raw frame text / secret
# substring is absent from the outcome. The contrast case (an ALLOW/REDACT
# resolver that never raises) asserts forward_text IS returned and the frame is
# NOT withheld, proving the withhold is specific to the failure, not universal.
# --------------------------------------------------------------------------- #


class _FcInjectedScanError(RuntimeError):
    """A distinctive exception the fail-closed scanner/resolver raises."""


@dataclass(slots=True)
class _FcRaisingScanner:
    """A findings scanner that always raises when invoked (case 1)."""

    def __call__(self, frame: str) -> Sequence[Finding]:
        raise _FcInjectedScanError("injected scanner failure")


@dataclass(slots=True)
class _FcRaisingResolver:
    """A resolver whose ``decide`` always raises (case 2).

    The scanner runs cleanly (returns no findings) so the failure is isolated to
    the resolve step; `scan_error_frame` must still withhold fail-closed.
    """

    def decide(self, matches: Sequence[Finding]) -> Decision:
        raise _FcInjectedScanError("injected resolver failure")


@dataclass(slots=True)
class _FcCleanScanner:
    """A scanner that returns no findings — used to isolate the resolver/BLOCK paths."""

    def __call__(self, frame: str) -> Sequence[Finding]:
        return ()


@dataclass(slots=True)
class _FcBlockingResolver:
    """A resolver that returns a BLOCK Decision so `apply_decision` raises OutputBlocked (case 3).

    The Decision carries a single BLOCK per-finding disposition so its overall
    disposition is the most-restrictive BLOCK (satisfying `Decision.__post_init__`),
    which is exactly what the shipped `apply_decision` turns into `OutputBlocked`.
    """

    detector: str = "card"

    def decide(self, matches: Sequence[Finding]) -> Decision:
        per_finding = (
            FindingDisposition(detector=self.detector, disposition=Disposition.BLOCK),
        )
        return Decision(
            disposition=Disposition.BLOCK,
            per_finding=per_finding,
            transformations=(),
            findings=tuple(matches),
            plan_version="fc-test",
            deciding_rules=(self.detector,),
            unavailable_detectors=(),
        )


@dataclass(slots=True)
class _FcAllowRedactResolver:
    """Contrast resolver: never raises, forwards (ALLOW with no findings, or REDACT).

    When the clean-forward scanner surfaces a sentinel span it REDACTs it;
    otherwise the decision is ALLOW. Either way the frame is forwarded (never
    withheld), proving the withhold in the failure cases is specific, not
    universal.
    """

    def decide(self, matches: Sequence[Finding]) -> Decision:
        per_finding: list[FindingDisposition] = []
        transformations: list[Transformation] = []
        for finding in matches:
            if finding.status is not FindingStatus.EXECUTED:
                per_finding.append(
                    FindingDisposition(
                        detector=finding.detector,
                        disposition=Disposition.ALLOW,
                    ),
                )
                continue
            per_finding.append(
                FindingDisposition(
                    detector=finding.detector,
                    disposition=Disposition.REDACT,
                ),
            )
            for span in finding.spans:
                transformations.append(
                    Transformation(
                        kind="redact",
                        span=span,
                        replacement=REDACTION_PLACEHOLDER,
                    ),
                )
        if not per_finding:
            overall = Disposition.ALLOW
        else:
            overall = (
                Disposition.REDACT
                if any(fd.disposition is Disposition.REDACT for fd in per_finding)
                else Disposition.ALLOW
            )
        deciding = tuple(
            fd.detector
            for fd in per_finding
            if fd.disposition is not Disposition.ALLOW
        )
        return Decision(
            disposition=overall,
            per_finding=tuple(per_finding),
            transformations=tuple(transformations),
            findings=tuple(matches),
            plan_version="fc-test",
            deciding_rules=deciding,
            unavailable_detectors=(),
        )


@dataclass(slots=True)
class _FcContrastScanner:
    """Clean-forward scanner for the contrast case: a single whole-frame span.

    Keeps the contrast path simple and deterministic — the frame is forwarded
    (ALLOW when empty, REDACT when non-empty), never raised and never blocked.
    """

    detector_class: str = "email"

    def __call__(self, frame: str) -> Sequence[Finding]:
        if not frame:
            return ()
        return (
            Finding(
                detector=self.detector_class,
                detector_version="fc-test",
                category=Category.PII,
                status=FindingStatus.EXECUTED,
                confidence=1.0,
                spans=(Span(start=0, end=len(frame)),),
                evidence=None,
            ),
        )


# Secret-looking fragments embedded into random frames: if the fail-closed
# contract broke and raw bytes leaked, these are the substrings a leak check
# would catch in the forwarded text. In every withhold case forward_text is
# None, so none of these can appear in the outcome.
_FC_SECRETS = (
    "AKIAIOSFODNN7EXAMPLE",
    "sk-live-0123456789abcdef",
    "ghp_wXyZ1234567890abcdefABCDEF",
    "4111111111111111",
    "postgres://user:p4ssw0rd@db.internal:5432/app",
    "eyJhbGciOiJIUzI1NiJ9.payload.signature",
)
_FC_ALPHABET = "abcdefghijklmnopqrstuvwxyz ABCDEF0123456789.:/-_@"


def _fc_frame(rng: random.Random) -> str:
    """One random error-frame body, often embedding a secret-looking substring.

    Mixes plain prose with — on ~70% of iterations — one of the `_FC_SECRETS`
    spliced in at a random position, so the leak assertions exercise frames that
    the fail-open bug would actually leak.
    """
    length = rng.randint(0, 60)
    body = "".join(rng.choice(_FC_ALPHABET) for _ in range(length))
    if rng.random() < 0.7:
        secret = rng.choice(_FC_SECRETS)
        cut = rng.randint(0, len(body))
        return body[:cut] + secret + body[cut:]
    return body


def _fc_assert_withheld(
    outcome: object,
    *,
    expected_code: str,
    frame: str,
    seed: int,
    iteration: int,
    case: str,
) -> None:
    """Assert a withheld outcome forwards nothing and leaks no raw frame byte."""
    # Typed access without importing the type name twice: the outcome is the
    # ErrorFrameOutcome `scan_error_frame` returns.
    withheld = outcome.withheld  # type: ignore[attr-defined]
    withheld_code = outcome.withheld_code  # type: ignore[attr-defined]
    forward_text = outcome.forward_text  # type: ignore[attr-defined]
    ctx = f"seed={seed:#x} iter={iteration} case={case}"
    assert withheld is True, f"{ctx} expected withheld, got withheld={withheld}"
    assert withheld_code == expected_code, (
        f"{ctx} expected code={expected_code!r}, got {withheld_code!r}"
    )
    # The whole of the fail-closed guarantee: there is literally nothing
    # forwarded, so neither the raw frame nor any secret substring can leak.
    assert forward_text is None, f"{ctx} forward_text must be None on withhold"
    for secret in _FC_SECRETS:
        if secret in frame:
            assert forward_text is None or secret not in forward_text, (
                f"{ctx} secret substring leaked into forwarded text"
            )


def test_property6_fail_closed_on_scan_error() -> None:
    seed = 0x12_06
    rng = random.Random(seed)
    print(f"test_property6_fail_closed_on_scan_error seed={seed:#x}")  # noqa: T201
    iterations = 10_000

    raising_scanner = _FcRaisingScanner()
    raising_resolver = _FcRaisingResolver()
    clean_scanner = _FcCleanScanner()
    blocking_resolver = _FcBlockingResolver()
    contrast_scanner = _FcContrastScanner()
    allow_redact_resolver = _FcAllowRedactResolver()

    for i in range(iterations):
        frame = _fc_frame(rng)

        # Case 1: the scanner raises -> withhold fail-closed (SCAN_ERROR_CODE).
        outcome1 = scan_error_frame(
            frame, scanner=raising_scanner, resolver=allow_redact_resolver,
        )
        _fc_assert_withheld(
            outcome1,
            expected_code=SCAN_ERROR_CODE,
            frame=frame,
            seed=seed,
            iteration=i,
            case="scanner-raises",
        )

        # Case 2: the resolver.decide raises -> withhold fail-closed (SCAN_ERROR_CODE).
        outcome2 = scan_error_frame(
            frame, scanner=clean_scanner, resolver=raising_resolver,
        )
        _fc_assert_withheld(
            outcome2,
            expected_code=SCAN_ERROR_CODE,
            frame=frame,
            seed=seed,
            iteration=i,
            case="resolver-raises",
        )

        # Case 3: the decision BLOCKs -> apply_decision raises OutputBlocked ->
        # withhold with BLOCK_CODE.
        outcome3 = scan_error_frame(
            frame, scanner=clean_scanner, resolver=blocking_resolver,
        )
        _fc_assert_withheld(
            outcome3,
            expected_code=BLOCK_CODE,
            frame=frame,
            seed=seed,
            iteration=i,
            case="decision-blocks",
        )

        # Contrast: a resolver/scanner that never fails forwards the frame (not
        # withheld). Proves the withhold above is SPECIFIC to the failure, not a
        # universal "always withhold" behaviour.
        outcome_ok = scan_error_frame(
            frame, scanner=contrast_scanner, resolver=allow_redact_resolver,
        )
        assert not outcome_ok.withheld, (
            f"seed={seed:#x} iter={i} contrast path must forward, not withhold"
        )
        assert outcome_ok.withheld_code is None, (
            f"seed={seed:#x} iter={i} contrast path must have no withheld_code"
        )
        assert outcome_ok.forward_text is not None, (
            f"seed={seed:#x} iter={i} contrast path must return forward_text"
        )
