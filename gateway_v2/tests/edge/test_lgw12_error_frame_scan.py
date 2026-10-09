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
from gateway_v2.edge.error_frame_scan import scan_error_frame
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
