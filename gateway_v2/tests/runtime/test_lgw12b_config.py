"""LGW12b config parse/validation (R2-06 / GW12b, Requirements 1.1, 1.2, 1.6)."""

from __future__ import annotations

import pytest

from gateway_v2.runtime.holdback_config import (
    _DEFAULT_HOLD_CAP,
    HoldbackConfig,
    load_holdback_config,
)


def test_unset_uses_default_no_warning() -> None:
    config, logs = load_holdback_config({})
    assert config.hold_cap_tokens == _DEFAULT_HOLD_CAP
    assert config.hold_cap_tokens == 3
    assert logs == ()


@pytest.mark.parametrize("raw", ["1", "3", "100"])
def test_valid_values_round_trip_no_warning(raw: str) -> None:
    config, logs = load_holdback_config({"RV_HOLDBACK_MAX_TOKENS": raw})
    assert config.hold_cap_tokens == int(raw)
    assert logs == ()


@pytest.mark.parametrize("raw", ["0", "101", "abc", "-5"])
def test_rejected_values_fall_back_with_warning(raw: str) -> None:
    config, logs = load_holdback_config({"RV_HOLDBACK_MAX_TOKENS": raw})
    assert config.hold_cap_tokens == _DEFAULT_HOLD_CAP
    assert config.hold_cap_tokens == 3
    assert len(logs) == 1
    # The warning must name the rejected value.
    assert raw in logs[0]


@pytest.mark.parametrize("raw", ["1", "3", "7", "50", "100"])
def test_relaxed_ceiling_is_double_cap(raw: str) -> None:
    config, _ = load_holdback_config({"RV_HOLDBACK_MAX_TOKENS": raw})
    assert config.relaxed_ceiling_tokens == 2 * config.hold_cap_tokens


def test_default_relaxed_ceiling() -> None:
    config, _ = load_holdback_config({})
    assert config.relaxed_ceiling_tokens == 2 * config.hold_cap_tokens
    assert config.relaxed_ceiling_tokens == 6


def test_config_is_frozen() -> None:
    config, _ = load_holdback_config({})
    with pytest.raises(Exception):
        config.hold_cap_tokens = 99  # type: ignore[misc]


def test_returns_holdback_config_instance() -> None:
    config, _ = load_holdback_config({"RV_HOLDBACK_MAX_TOKENS": "5"})
    assert isinstance(config, HoldbackConfig)
    assert config.hold_cap_tokens == 5
    assert config.relaxed_ceiling_tokens == 10
