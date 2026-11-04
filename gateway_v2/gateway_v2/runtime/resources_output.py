"""GW33 output-parity capacity accessors, split out of `runtime/resources.py` (D-25, C14).

`ResourceContract` mixes `OutputBoundsMixin` in, exactly as it does `VectorBoundsMixin`. The split
keeps `runtime/resources.py` under its 800-line size gate. Unlike the vector split, the constants
live NEXT TO their accessors here: `lint/check_capacity_literals.py` exempts exactly two paths
(`ALLOWED_RELS`), this module and `runtime/resources.py`, and every detector module receives its
numbers through a constructor argument (`OutputBounds`), never a literal of its own.

Each accessor is a documented multiplier or fraction of an existing contract field
(`target_p99_ms`, `max_output_tokens()`) and fails closed: `CapacityUnset` when the supplying signal
is unset, `CapacityUnavailable` on a non-positive result.

D-30: `DEFAULT_OUTPUT_BOUNDS` holds the MOST SENSITIVE values the contract can derive (smallest
minimums, largest scan allowance). `OutputBoundsMixin.output_bounds()` is clamped so a contract-derived
detector never flags LESS than a detector built from the default. The compile-time template check
builds its detectors from the default, so a template it accepts cannot be flagged at run time.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Final

from gateway_v2.runtime.errors import CapacityUnavailable, CapacityUnset

# D-24: the one immutable template limit. `plan/rewrite_template.py` imports it directly because
# `compile_plan` has four call sites that hold no `ResourceContract`.
REWRITE_TEMPLATE_MAX_CHARS: Final[int] = 128

_MS_PER_S: Final[float] = 1000.0
# Sensitive floors (the DEFAULT bounds).
_DEFAULT_EXFIL_PAYLOAD: Final[int] = 32
_DEFAULT_EXFIL_ENCODED_RUN: Final[int] = 16
_DEFAULT_EXFIL_LABEL: Final[int] = 16
_DEFAULT_CANDIDATE_CAP: Final[int] = 64
_DEFAULT_DECODE_EXPANSION: Final[int] = 10
_DEFAULT_CANARY_TOKEN_CHARS: Final[int] = 31  # "cnry_" + 26 base32 characters (D-16)
_DNS_NAME_LIMIT: Final[int] = 253
# Multipliers on `target_p99_ms` (20 ms default) -- documented, not guessed.
_PAYLOAD_PER_P99_MS: Final[float] = 2.0
_ENCODED_RUN_PER_P99_MS: Final[float] = 1.0
_LABEL_PER_P99_MS: Final[float] = 1.0
_CANDIDATES_PER_P99_MS: Final[float] = 5.0
_SURVIVOR_PER_P99_MS: Final[float] = 0.6
_GROUNDING_WAIT_PER_P99_S: Final[float] = 100.0
# Multipliers on fixed sizes.
_PROPOSAL_PER_TEMPLATE: Final[int] = 8
_CANARY_ENTROPY_BYTES: Final[int] = 17  # 136 bits -> 26 base32 characters
_CANARY_TEXT_CHARS: Final[int] = 128
_GROUNDING_CHARS_PER_TOKEN: Final[int] = 4
_OUTPUT_CHARS_PER_TOKEN: Final[int] = 8  # deliberately generous: never fires on a compliant provider


@dataclass(frozen=True, slots=True)
class OutputBounds:
    """The single immutable parameter source for every GW33 detector constructor (D-30)."""

    exfil_min_payload_chars: int
    exfil_min_encoded_run: int
    exfil_min_label_chars: int
    exfil_max_host_chars: int
    output_scan_candidate_cap: int
    decode_expansion_bound: int
    canary_token_chars: int

    def __post_init__(self) -> None:
        for name in self.__slots__:
            if getattr(self, name) < 1:
                raise CapacityUnavailable(f"output bound {name} below minimum to serve")

    @property
    def canary_scan_overlap_chars(self) -> int:
        """Original characters a per-delta canary scan must re-read (D-23)."""
        return self.canary_token_chars * self.decode_expansion_bound


DEFAULT_OUTPUT_BOUNDS: Final[OutputBounds] = OutputBounds(
    exfil_min_payload_chars=_DEFAULT_EXFIL_PAYLOAD,
    exfil_min_encoded_run=_DEFAULT_EXFIL_ENCODED_RUN,
    exfil_min_label_chars=_DEFAULT_EXFIL_LABEL,
    exfil_max_host_chars=_DNS_NAME_LIMIT,
    output_scan_candidate_cap=_DEFAULT_CANDIDATE_CAP,
    decode_expansion_bound=_DEFAULT_DECODE_EXPANSION,
    canary_token_chars=_DEFAULT_CANARY_TOKEN_CHARS,
)


class OutputBoundsMixin:
    """GW33 detector, template, canary, grounding and output-length bounds for `ResourceContract`.

    A behaviour-only mixin (no field, no `__slots__`) so it composes with the frozen-slotted
    `ResourceContract`. `target_p99_ms` and `max_output_tokens` are provided by the host.
    """

    target_p99_ms: float

    @staticmethod
    def max_output_tokens() -> int:  # pragma: no cover - supplied by ResourceContract
        raise NotImplementedError

    def _p99_ms(self) -> float:
        value = self.target_p99_ms
        if math.isnan(value) or math.isinf(value):
            raise CapacityUnset("output bound signal unset")
        if value <= 0:
            raise CapacityUnavailable("output bound below minimum to serve")
        return value

    def _scaled(self, per_ms: float, floor: int) -> int:
        """`ceil(p99_ms x per_ms)`, never below the sensitive default floor (D-30)."""
        return max(floor, math.ceil(self._p99_ms() * per_ms))

    def exfil_min_payload_chars(self) -> int:
        """Query/path length from which an image URL is a payload channel (`p99_ms x 2`, 40)."""
        return self._scaled(_PAYLOAD_PER_P99_MS, _DEFAULT_EXFIL_PAYLOAD)

    def exfil_min_encoded_run(self) -> int:
        """Shortest base64/hex run counted as encoded content (`p99_ms x 1`, 20)."""
        return self._scaled(_ENCODED_RUN_PER_P99_MS, _DEFAULT_EXFIL_ENCODED_RUN)

    def exfil_min_label_chars(self) -> int:
        """Shortest leftmost DNS label tested as a smuggled blob (`p99_ms x 1`, 20)."""
        return self._scaled(_LABEL_PER_P99_MS, _DEFAULT_EXFIL_LABEL)

    def exfil_max_host_chars(self) -> int:
        """A hostname longer than the DNS limit is one finding, not a loop."""
        self._p99_ms()
        return _DNS_NAME_LIMIT

    def output_scan_candidate_cap(self) -> int:
        """URLs/addresses examined per scanned text before `UNAVAILABLE` (`p99_ms x 5`, 100)."""
        return self._scaled(_CANDIDATES_PER_P99_MS, _DEFAULT_CANDIDATE_CAP)

    def decode_expansion_bound(self) -> int:
        """Worst original-characters-per-decoded-character expansion (entity: up to 10x)."""
        self._p99_ms()
        return _DEFAULT_DECODE_EXPANSION

    def canary_token_chars(self) -> int:
        self._p99_ms()
        return _DEFAULT_CANARY_TOKEN_CHARS

    def rewrite_template_max_chars(self) -> int:
        self._p99_ms()
        return REWRITE_TEMPLATE_MAX_CHARS

    def rewrite_proposal_max_chars(self) -> int:
        """Model-proposed replacement bound: `template max x 8`."""
        return self.rewrite_template_max_chars() * _PROPOSAL_PER_TEMPLATE

    def rewrite_survivor_min_chars(self) -> int:
        """Originals shorter than this are exempt from the survivor check (`p99_ms x 0.6`, 12)."""
        return max(1, math.ceil(self._p99_ms() * _SURVIVOR_PER_P99_MS))

    def canary_entropy_bytes(self) -> int:
        self._p99_ms()
        return _CANARY_ENTROPY_BYTES

    def canary_text_max_chars(self) -> int:
        self._p99_ms()
        return _CANARY_TEXT_CHARS

    def canary_scan_overlap_chars(self) -> int:
        """`token chars x decode expansion` original characters (310 on the default contract)."""
        return self.canary_token_chars() * self.decode_expansion_bound()

    def output_ceiling_chars(self) -> int:
        """D-13: `max_output_tokens() x 8` characters; past it the stream is cut."""
        value = self.max_output_tokens() * _OUTPUT_CHARS_PER_TOKEN
        if value < 1:
            raise CapacityUnavailable("output ceiling below minimum to serve")
        return value

    def grounding_max_input_chars(self) -> int:
        """Context plus answer bound for grounding: `max_output_tokens() x 4` characters."""
        value = self.max_output_tokens() * _GROUNDING_CHARS_PER_TOKEN
        if value < 1:
            raise CapacityUnavailable("grounding input bound below minimum to serve")
        return value

    def grounding_wait_budget_s(self) -> float:
        """Wall-clock wait for the final grounding check (`p99_s x 100`, 2 s)."""
        return (self._p99_ms() / _MS_PER_S) * _GROUNDING_WAIT_PER_P99_S

    def exfil_allow_hosts_max(self) -> int:
        """D-29: most `allow_hosts` entries one rule may carry."""
        return self.output_scan_candidate_cap()

    def output_bounds(self) -> OutputBounds:
        """The contract-derived `OutputBounds`, never less sensitive than the default (D-30)."""
        return OutputBounds(
            exfil_min_payload_chars=self.exfil_min_payload_chars(),
            exfil_min_encoded_run=self.exfil_min_encoded_run(),
            exfil_min_label_chars=self.exfil_min_label_chars(),
            exfil_max_host_chars=self.exfil_max_host_chars(),
            output_scan_candidate_cap=self.output_scan_candidate_cap(),
            decode_expansion_bound=max(
                self.decode_expansion_bound(),
                DEFAULT_OUTPUT_BOUNDS.decode_expansion_bound,
            ),
            canary_token_chars=self.canary_token_chars(),
        )
