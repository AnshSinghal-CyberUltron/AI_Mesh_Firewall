"""IP and infrastructure leakage detector (GW33 C3, requirement 1).

Canonicalises the output (GW09: zero-width, fullwidth, percent/entity/base64 within the Decode
Budget), extracts IPv4, IPv6 and hostname candidates with bounded regexes, confirms each with
`ipaddress`, and maps findings back to ORIGINAL byte offsets. `evidence` is always `None`.
Documentation ranges, public addresses and dotted version strings are never findings.

Work is bounded: input length by the Decode Budget, candidate count by
`OutputBounds.output_scan_candidate_cap`; the remainder becomes one UNAVAILABLE finding.
Any exception becomes one UNAVAILABLE finding (D-18). The platform `rule_id` is stamped on
`Finding.detector` (D-32) so the resolver joins by identity.
"""
from __future__ import annotations

import ipaddress
import re
from dataclasses import dataclass, field
from typing import Final

from gateway_v2.detect.base import DetectorContext
from gateway_v2.detect.deterministic.canonicalize import (
    DEFAULT_BUDGET,
    BudgetExceeded,
    canonicalize,
)
from gateway_v2.detect.output.common import executed, original_span, unavailable
from gateway_v2.detect.output.ruleset import IP_RULESET, ruleset_version_of
from gateway_v2.domain import Category, Finding, Span
from gateway_v2.runtime.resources_output import DEFAULT_OUTPUT_BOUNDS, OutputBounds

__all__ = ("IpLeakDetector",)

_IPV4: Final = re.compile(r"(?<![\w.-])(?:\d{1,3}\.){3}\d{1,3}(?![\w-])(?!\.\d)")
# Two or more colons, hex groups, optional dotted-quad tail (IPv4-mapped); validated afterwards.
_IPV6: Final = re.compile(r"(?<![\w:.])(?:[0-9A-Fa-f]{0,4}:){2,}[0-9A-Fa-f:.]*(?![\w])")
_HOST: Final = re.compile(
    r"(?<![\w@.-])[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?(?:\.[A-Za-z0-9-]{1,63}){0,8}"
    r"(?![\w-])"
)
_NETS: Final = tuple(ipaddress.ip_network(n) for n in IP_RULESET["internal_networks"])
_DOC_NETS: Final = tuple(ipaddress.ip_network(n) for n in IP_RULESET["documentation_networks"])
_SUFFIXES: Final = IP_RULESET["internal_host_suffixes"]
_PORT: Final = re.compile(r":(\d{2,5})(?![\w])")
_VERSION: Final = ruleset_version_of(IP_RULESET)


def _is_internal(address: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped is not None:
        address = address.ipv4_mapped
    if any(address in net for net in _DOC_NETS if net.version == address.version):
        return False
    return any(address in net for net in _NETS if net.version == address.version)


def _parse_v4(token: str) -> ipaddress.IPv4Address | None:
    parts = token.split(".")
    if len(parts) != 4 or not all(p.isdigit() for p in parts):
        return None
    values = [int(p) for p in parts]  # leading zeros are folded, not trusted (fail-safe)
    if any(v > 255 for v in values):
        return None
    return ipaddress.IPv4Address(bytes(values))


def _parse_v6(token: str) -> ipaddress.IPv6Address | None:
    token = token.rstrip(":.")
    if token.count(":") < 2:
        return None
    try:
        return ipaddress.IPv6Address(token)
    except ValueError:
        return None


@dataclass(frozen=True, slots=True)
class IpLeakDetector:
    """Frozen detector (D-30: constructed from one `OutputBounds`)."""

    bounds: OutputBounds = DEFAULT_OUTPUT_BOUNDS
    rule_id: str = "output.ip_leak"
    required_capabilities: frozenset[str] = field(default_factory=frozenset)

    @property
    def detector_version(self) -> str:
        return _VERSION

    def detect(self, text: str, context: DetectorContext | None = None) -> tuple[Finding, ...]:
        try:
            return self._detect(text)
        except Exception:  # noqa: BLE001 - D-18: any failure is data, never a clean result
            return (unavailable(self.rule_id, _VERSION, Category.IP_LEAK),)

    def _detect(self, text: str) -> tuple[Finding, ...]:
        try:
            canonical, span_map = canonicalize(text, DEFAULT_BUDGET)
        except BudgetExceeded:
            return (unavailable(self.rule_id, _VERSION, Category.IP_LEAK),)
        cap = self.bounds.output_scan_candidate_cap
        # One past the cap is enough to know the remainder exists; discovery stops there, so the
        # work is bounded by the cap (plus linear regex scanning), never by the candidate count.
        found = self._candidates(canonical, cap + 1)
        over = len(found) > cap
        hits = found[:cap]
        findings = [
            executed(self.rule_id, _VERSION, Category.IP_LEAK, (original_span(span_map, s, e),))
            for s, e in hits
        ]
        if over:
            findings.append(unavailable(self.rule_id, _VERSION, Category.IP_LEAK))
        return tuple(findings)

    def _candidates(self, canonical: str, limit: int) -> list[tuple[int, int]]:
        """Up to ``limit`` confirmed internal addresses/hostnames, as canonical ``(start, end)``."""
        found: list[tuple[int, int]] = []
        taken: list[tuple[int, int]] = []

        def claim(start: int, end: int) -> bool:
            if any(start < b and a < end for a, b in taken):
                return False
            taken.append((start, end))
            return True

        for match in _IPV6.finditer(canonical):
            token = match.group().rstrip(":.")
            address6 = _parse_v6(token)
            end = match.start() + len(token)
            if address6 is not None and _is_internal(address6) and claim(match.start(), end):
                found.append((match.start(), end))
                if len(found) >= limit:
                    return sorted(found)
        for match in _IPV4.finditer(canonical):
            address = _parse_v4(match.group())
            if address is not None and _is_internal(address) and claim(*match.span()):
                found.append(match.span())
                if len(found) >= limit:
                    return sorted(found)
        for match in _HOST.finditer(canonical):
            if self._is_internal_host(canonical, match) and claim(*match.span()):
                found.append(match.span())
                if len(found) >= limit:
                    return sorted(found)
        found.sort()
        return found

    @staticmethod
    def _is_internal_host(canonical: str, match: re.Match[str]) -> bool:
        name = match.group().lower().rstrip(".")
        if "." in name:
            if any(name.endswith(suffix) for suffix in _SUFFIXES):
                return True
            return False
        if len(name) < 2 or name.isdigit():
            return False
        before = canonical[max(0, match.start() - 3) : match.start()]
        if before.endswith("://"):
            return True
        return _PORT.match(canonical, match.end()) is not None and name.isalnum() is True and any(
            c.isalpha() for c in name
        )


def span_of(finding: Finding) -> Span:
    return finding.spans[0]
