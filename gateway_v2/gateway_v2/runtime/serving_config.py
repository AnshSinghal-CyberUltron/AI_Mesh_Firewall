"""ServingConfig — the serving entrypoint's env config surface (B3 / GW serving).

Reads the serving host/port/drain bounds through the same env to dataclass+logs idiom as
``runtime/resources.py::load_contract`` and ``runtime/holdback_config.py::load_holdback_config``:
parse, validate, and return a frozen value together with a tuple of warning log lines. An unset
key uses the documented operational default with no warning; a present-but-unparseable or
out-of-range value falls back to that default and emits exactly one warning line naming the
rejected key and value (byte-for-byte the ``_parse_hold_cap`` warn-and-default behaviour).

The worker count is deliberately NOT a field here: it comes from
``ResourceContract.workers()`` (``runtime/resources.py``), so there is one worker-count authority
and no second source of truth. The default port/host/drain seconds are operational-configuration
literals, not capacity-position literals (they are not derived from CPU/RAM/FD), so they are
allowed outside ``resources.py``.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass

__all__ = (
    "ServingConfig",
    "load_serving_config",
)

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


@dataclass(frozen=True, slots=True)
class ServingConfig:
    """The validated serving configuration loaded once at boot.

    ``host``/``port`` are the TCP listen address; ``drain_accept_s`` is the stop-accepting hold
    (``RV_DRAIN_ACCEPT_S``) and ``drain_s`` is the in-flight drain budget (``RV_DRAIN_S``) that
    together drive the shipped GW19 drain state machine on ``SIGTERM``; ``conn_max_age_s`` is the
    connection recycling bound (``RV_CONN_MAX_AGE_S``). Worker count and max-stream-duration are
    NOT fields — they come from ``ResourceContract``.
    """

    host: str
    port: int
    drain_accept_s: float
    drain_s: float
    conn_max_age_s: float


def _env_map(env: Mapping[str, str] | None) -> Mapping[str, str]:
    if env is not None:
        return env
    return dict(os.environ)


def _parse_host(raw: str | None) -> tuple[str, tuple[str, ...]]:
    if raw is None or not raw.strip():
        return _DEFAULT_HOST, ()
    return raw.strip(), ()


def _parse_port(raw: str | None) -> tuple[int, tuple[str, ...]]:
    if raw is None or not raw.strip():
        return _DEFAULT_PORT, ()
    stripped = raw.strip()
    try:
        value = int(stripped)
    except ValueError:
        return _DEFAULT_PORT, (
            f"{_ENV_PORT}={stripped!r} is not an integer; "
            f"using default port {_DEFAULT_PORT}",
        )
    if value < _MIN_PORT or value > _MAX_PORT:
        return _DEFAULT_PORT, (
            f"{_ENV_PORT}={value} outside [{_MIN_PORT}, {_MAX_PORT}]; "
            f"using default port {_DEFAULT_PORT}",
        )
    return value, ()


def _parse_seconds(
    raw: str | None, *, key: str, default: float
) -> tuple[float, tuple[str, ...]]:
    if raw is None or not raw.strip():
        return default, ()
    stripped = raw.strip()
    try:
        value = float(stripped)
    except ValueError:
        return default, (
            f"{key}={stripped!r} is not a number; using default {default}",
        )
    if value < 0.0:
        return default, (
            f"{key}={value} is negative; using default {default}",
        )
    return value, ()


def load_serving_config(
    env: Mapping[str, str] | None = None,
) -> tuple[ServingConfig, tuple[str, ...]]:
    """Load the serving config from ``env`` (defaults to ``os.environ``).

    Returns ``(config, logs)`` exactly like ``load_contract`` / ``load_holdback_config``: ``logs``
    is empty when every key is unset or valid, and carries one warning line per rejected key naming
    the key and its value (R6.5). The function NEVER raises and never leaves a field unset — a
    missing or invalid value always falls back to the documented default (R6.1, R6.7).
    """
    src = _env_map(env)
    host, host_logs = _parse_host(src.get(_ENV_HOST))
    port, port_logs = _parse_port(src.get(_ENV_PORT))
    drain_accept_s, accept_logs = _parse_seconds(
        src.get(_ENV_DRAIN_ACCEPT_S),
        key=_ENV_DRAIN_ACCEPT_S,
        default=_DEFAULT_DRAIN_ACCEPT_S,
    )
    drain_s, drain_logs = _parse_seconds(
        src.get(_ENV_DRAIN_S),
        key=_ENV_DRAIN_S,
        default=_DEFAULT_DRAIN_S,
    )
    conn_max_age_s, conn_logs = _parse_seconds(
        src.get(_ENV_CONN_MAX_AGE_S),
        key=_ENV_CONN_MAX_AGE_S,
        default=_DEFAULT_CONN_MAX_AGE_S,
    )
    config = ServingConfig(
        host=host,
        port=port,
        drain_accept_s=drain_accept_s,
        drain_s=drain_s,
        conn_max_age_s=conn_max_age_s,
    )
    logs = (
        *host_logs,
        *port_logs,
        *accept_logs,
        *drain_logs,
        *conn_logs,
    )
    return config, logs
