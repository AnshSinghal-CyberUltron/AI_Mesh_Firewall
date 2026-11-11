"""B3 Serving_Config tests — Property 1 (parse totality) + example/unit coverage.

Property 1 is driven by the house idiom (a seeded ``random.Random``, no ``hypothesis``,
>=10,000 iterations). The example/unit tests pin the documented defaults, a valid override
read-through, the worker-count-not-a-field invariant, and the frozen-value contract.
"""

from __future__ import annotations

import dataclasses
import random

import pytest

from gateway_v2.runtime.serving_config import (
    ServingConfig,
    load_serving_config,
)

# ---------------------------------------------------------------------------
# Documented keys + defaults (mirrors runtime/serving_config.py; the test is the
# independent oracle, so these are spelled out here rather than imported from the
# private module constants).
# ---------------------------------------------------------------------------

_ENV_HOST = "RV_HOST"
_ENV_PORT = "RV_PORT"
_ENV_DRAIN_ACCEPT_S = "RV_DRAIN_ACCEPT_S"
_ENV_DRAIN_S = "RV_DRAIN_S"
_ENV_CONN_MAX_AGE_S = "RV_CONN_MAX_AGE_S"

_DEFAULT_HOST = "127.0.0.1"
_DEFAULT_PORT = 8300
_DEFAULT_DRAIN_ACCEPT_S = 15.0
_DEFAULT_DRAIN_S = 60.0
_DEFAULT_CONN_MAX_AGE_S = 30.0

_MIN_PORT = 1
_MAX_PORT = 65535


# ===========================================================================
# Property 1 (PBT, house idiom)
# ===========================================================================

# Feature: serving-entrypoint-gw-b3, Property 1: Serving_Config parse is total and never
#   un-defaults a bad value
# Validates: Requirements 6.1, 6.2, 6.3, 6.5, 6.7

_SEED = 0x0B3_0001
_ITERATIONS = 10_000

# Generation knobs: each key independently either (a) absent, (b) a blank string, (c) a
# non-numeric/garbage string, (d) an out-of-range value, or (e) a validly-parsed value. The
# oracle below computes, independently of the production parser, what the field and the
# warning-presence must be for the drawn raw value.
_HOST_VALID = ("127.0.0.1", "0.0.0.0", "localhost", "::1", "10.0.0.5")
# Genuinely non-blank, non-numeric strings. Deliberately excludes whitespace-only values
# (those strip to blank -> silent default, no warning) and non-ASCII digit strings (Python's
# int()/float() parse e.g. Arabic-Indic digits, so they are NOT garbage).
_GARBAGE = ("abc", "nan-ish", "12x", "1.2.3", "0x10", "1e", "--", "port")


def _ctx(i: int, **extra: object) -> str:
    body = " ".join(f"{k}={v!r}" for k, v in extra.items())
    return f"seed={_SEED:#x} iter={i} {body}"


def _draw_host(rng: random.Random) -> tuple[str | None, str, bool]:
    """Return ``(raw, expected_host, expects_warning)`` for RV_HOST.

    A blank or absent host is NOT a rejection — it silently defaults with no warning (the host
    parser accepts any non-blank string), so the host key can never produce a warning line.
    """
    kind = rng.choice(("absent", "blank", "valid", "padded"))
    if kind == "absent":
        return None, _DEFAULT_HOST, False
    if kind == "blank":
        return rng.choice(("", "   ", "\t")), _DEFAULT_HOST, False
    value = rng.choice(_HOST_VALID)
    if kind == "padded":
        return f"  {value}  ", value, False
    return value, value, False


def _draw_port(rng: random.Random) -> tuple[str | None, int, bool]:
    """Return ``(raw, expected_port, expects_warning)`` for RV_PORT."""
    kind = rng.choice(
        ("absent", "blank", "garbage", "below", "above", "valid", "padded")
    )
    if kind == "absent":
        return None, _DEFAULT_PORT, False
    if kind == "blank":
        return rng.choice(("", "   ")), _DEFAULT_PORT, False
    if kind == "garbage":
        return rng.choice(_GARBAGE), _DEFAULT_PORT, True
    if kind == "below":
        value = rng.randint(-5000, 0)  # <= 0, outside [1, 65535]
        return str(value), _DEFAULT_PORT, True
    if kind == "above":
        value = rng.randint(_MAX_PORT + 1, _MAX_PORT + 100000)
        return str(value), _DEFAULT_PORT, True
    value = rng.randint(_MIN_PORT, _MAX_PORT)
    if kind == "padded":
        return f"  {value}  ", value, False
    return str(value), value, False


def _draw_seconds(
    rng: random.Random, default: float
) -> tuple[str | None, float, bool]:
    """Return ``(raw, expected_value, expects_warning)`` for a seconds key.

    Negative seconds are rejected; a valid value is any float >= 0.0.
    """
    kind = rng.choice(
        ("absent", "blank", "garbage", "negative", "valid", "zero", "padded")
    )
    if kind == "absent":
        return None, default, False
    if kind == "blank":
        return rng.choice(("", "  ")), default, False
    if kind == "garbage":
        return rng.choice(_GARBAGE), default, True
    if kind == "negative":
        value = -round(rng.uniform(0.001, 500.0), 3)
        return repr(value), default, True
    if kind == "zero":
        return "0", 0.0, False
    value = round(rng.uniform(0.0, 500.0), 3)
    raw = repr(value)
    if kind == "padded":
        return f"  {raw}  ", value, False
    return raw, value, False


def test_property_1_parse_is_total_and_never_undefaults_a_bad_value() -> None:
    rng = random.Random(_SEED)
    for i in range(_ITERATIONS):
        env: dict[str, str] = {}

        host_raw, exp_host, host_warn = _draw_host(rng)
        port_raw, exp_port, port_warn = _draw_port(rng)
        accept_raw, exp_accept, accept_warn = _draw_seconds(
            rng, _DEFAULT_DRAIN_ACCEPT_S
        )
        drain_raw, exp_drain, drain_warn = _draw_seconds(rng, _DEFAULT_DRAIN_S)
        conn_raw, exp_conn, conn_warn = _draw_seconds(rng, _DEFAULT_CONN_MAX_AGE_S)

        if host_raw is not None:
            env[_ENV_HOST] = host_raw
        if port_raw is not None:
            env[_ENV_PORT] = port_raw
        if accept_raw is not None:
            env[_ENV_DRAIN_ACCEPT_S] = accept_raw
        if drain_raw is not None:
            env[_ENV_DRAIN_S] = drain_raw
        if conn_raw is not None:
            env[_ENV_CONN_MAX_AGE_S] = conn_raw

        # Totality: never raises.
        config, logs = load_serving_config(env)

        # Fully-populated frozen value.
        assert isinstance(config, ServingConfig), _ctx(i, env=env)
        assert config.host == exp_host, _ctx(i, env=env, got=config.host)
        assert config.port == exp_port, _ctx(i, env=env, got=config.port)
        assert config.drain_accept_s == exp_accept, _ctx(
            i, env=env, got=config.drain_accept_s
        )
        assert config.drain_s == exp_drain, _ctx(i, env=env, got=config.drain_s)
        assert config.conn_max_age_s == exp_conn, _ctx(
            i, env=env, got=config.conn_max_age_s
        )

        # Every field equals either the validly-parsed value OR its documented default:
        # port is always a valid in-range int, seconds are always >= 0.0.
        assert _MIN_PORT <= config.port <= _MAX_PORT, _ctx(i, env=env)
        assert config.host, _ctx(i, env=env)  # never blank
        assert config.drain_accept_s >= 0.0, _ctx(i, env=env)
        assert config.drain_s >= 0.0, _ctx(i, env=env)
        assert config.conn_max_age_s >= 0.0, _ctx(i, env=env)

        # Exactly one warning per rejected present value, and no warning otherwise. The
        # per-key expectation sums to the exact warning count, and each warning names its key.
        expected_warn_keys: list[str] = []
        if host_warn:
            expected_warn_keys.append(_ENV_HOST)
        if port_warn:
            expected_warn_keys.append(_ENV_PORT)
        if accept_warn:
            expected_warn_keys.append(_ENV_DRAIN_ACCEPT_S)
        if drain_warn:
            expected_warn_keys.append(_ENV_DRAIN_S)
        if conn_warn:
            expected_warn_keys.append(_ENV_CONN_MAX_AGE_S)

        assert len(logs) == len(expected_warn_keys), _ctx(
            i, env=env, logs=logs, expected=expected_warn_keys
        )
        # Each expected rejected key is named by exactly one warning line, and no warning
        # mentions a key that was valid/absent.
        for key in (
            _ENV_HOST,
            _ENV_PORT,
            _ENV_DRAIN_ACCEPT_S,
            _ENV_DRAIN_S,
            _ENV_CONN_MAX_AGE_S,
        ):
            naming = [line for line in logs if key in line]
            if key in expected_warn_keys:
                assert len(naming) == 1, _ctx(i, env=env, key=key, logs=logs)
            else:
                assert naming == [], _ctx(i, env=env, key=key, logs=logs)


# ===========================================================================
# Example / unit tests (plain asserts)
# ===========================================================================


def test_all_unset_env_yields_documented_defaults() -> None:
    """All-unset env -> the five documented defaults, no warnings.

    Requirements 6.2, 6.3, 3.6, 3.7, 3.10.
    """
    config, logs = load_serving_config({})
    assert config.port == 8300
    assert config.host == "127.0.0.1"
    assert config.drain_accept_s == 15.0
    assert config.drain_s == 60.0
    assert config.conn_max_age_s == 30.0
    assert logs == ()


def test_valid_override_for_each_key_reads_through_with_empty_logs() -> None:
    """A valid override for each key is read through with no warning lines (6.2, 6.3)."""
    env = {
        _ENV_HOST: "0.0.0.0",
        _ENV_PORT: "9100",
        _ENV_DRAIN_ACCEPT_S: "5.5",
        _ENV_DRAIN_S: "42.0",
        _ENV_CONN_MAX_AGE_S: "12.0",
    }
    config, logs = load_serving_config(env)
    assert config.host == "0.0.0.0"
    assert config.port == 9100
    assert config.drain_accept_s == 5.5
    assert config.drain_s == 42.0
    assert config.conn_max_age_s == 12.0
    assert logs == ()


def test_worker_count_is_not_an_attribute_of_serving_config() -> None:
    """Worker count comes from ``ResourceContract.workers()``, not ``ServingConfig`` (6.4, 3.6)."""
    config, _ = load_serving_config({})
    assert not hasattr(config, "workers")
    assert not hasattr(config, "worker_count")
    field_names = {f.name for f in dataclasses.fields(config)}
    assert field_names == {
        "host",
        "port",
        "drain_accept_s",
        "drain_s",
        "conn_max_age_s",
    }
    assert "workers" not in field_names
    assert "worker_count" not in field_names


def test_serving_config_is_frozen() -> None:
    """Assigning a field on the frozen value raises ``FrozenInstanceError`` (6.7, 3.7, 3.10)."""
    config, _ = load_serving_config({})
    with pytest.raises(dataclasses.FrozenInstanceError):
        config.port = 1234  # type: ignore[misc]
