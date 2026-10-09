"""HoldbackConfig — the owner-signed streaming holdback cap (R2-06 / GW12b).

Reads ``RV_HOLDBACK_MAX_TOKENS`` through the same env to dataclass+logs idiom as
``runtime/resources.py::load_contract``: parse, validate, and return a frozen config
together with a tuple of warning log lines. An unset key uses the signed default of 3
tokens with no warning; a non-integer, out-of-range, or otherwise rejected value falls
back to the default and emits a warning naming the rejected value.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass

__all__ = (
    "HoldbackConfig",
    "load_holdback_config",
)

_ENV_KEY = "RV_HOLDBACK_MAX_TOKENS"
_DEFAULT_HOLD_CAP = 3
_MIN_HOLD_CAP = 1
_MAX_HOLD_CAP = 100


@dataclass(frozen=True, slots=True)
class HoldbackConfig:
    """The validated streaming holdback cap and its derived relaxed-class ceiling.

    ``hold_cap_tokens`` is the hard maximum number of upstream tokens a word-class
    pattern may hold (R1). ``relaxed_ceiling_tokens`` is twice that cap: the ceiling a
    relaxed-class pattern (``RELAXED_HOLDBACK_CLASSES``) may reach before the pipeline
    signals overflow (R1.5).
    """

    hold_cap_tokens: int
    relaxed_ceiling_tokens: int


def _env_map(env: Mapping[str, str] | None) -> Mapping[str, str]:
    if env is not None:
        return env
    return dict(os.environ)


def _parse_hold_cap(raw: str | None) -> tuple[int, tuple[str, ...]]:
    if raw is None or not raw.strip():
        return _DEFAULT_HOLD_CAP, ()
    stripped = raw.strip()
    try:
        value = int(stripped)
    except ValueError:
        return _DEFAULT_HOLD_CAP, (
            f"{_ENV_KEY}={stripped!r} is not an integer; "
            f"using default hold cap {_DEFAULT_HOLD_CAP}",
        )
    if value < _MIN_HOLD_CAP or value > _MAX_HOLD_CAP:
        return _DEFAULT_HOLD_CAP, (
            f"{_ENV_KEY}={value} outside [{_MIN_HOLD_CAP}, {_MAX_HOLD_CAP}]; "
            f"using default hold cap {_DEFAULT_HOLD_CAP}",
        )
    return value, ()


def load_holdback_config(
    env: Mapping[str, str] | None = None,
) -> tuple[HoldbackConfig, tuple[str, ...]]:
    """Load the holdback config from ``env`` (defaults to ``os.environ``).

    Returns ``(config, logs)`` exactly like ``load_contract``: ``logs`` is empty when
    the key is unset or valid, and carries a single warning line naming the rejected
    value when ``RV_HOLDBACK_MAX_TOKENS`` is non-integer, ``< 1``, or ``> 100`` (R1.6).
    """
    src = _env_map(env)
    hold_cap, logs = _parse_hold_cap(src.get(_ENV_KEY))
    config = HoldbackConfig(
        hold_cap_tokens=hold_cap,
        relaxed_ceiling_tokens=2 * hold_cap,
    )
    return config, logs
