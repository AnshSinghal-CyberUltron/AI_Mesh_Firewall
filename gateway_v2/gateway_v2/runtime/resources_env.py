"""Pure env-parsing helpers split out of `runtime/resources.py` (GW29 task 3, size gate).

`resources.py` was at its 800-line cap, so these three helpers moved here and are imported back
(`resources._env_map` etc. keep working). They hold no capacity literal: the owner-queue default
and env key stay in `runtime/resources.py` (the one module `check_capacity_literals` exempts) and
are read by a function-local import to break the otherwise-circular import.
"""

from __future__ import annotations

import math
import os
import warnings


def _env_map(env: dict[str, str] | None) -> dict[str, str]:
    if env is not None:
        return env
    return dict(os.environ)


def _parse_override(raw: str | None) -> int | None:
    if raw is None or not raw.strip():
        return None
    return int(raw.strip())


def _parse_owner_queue_ms(raw: str | None) -> float:
    """Parse `RV_OWNER_QUEUE_MS` in milliseconds, warn-and-default on a rejected value.

    An unset / blank key returns the measured default silently. A non-numeric or non-positive
    value falls back to the default and emits a `warnings.warn` naming the rejected value, the same
    stance `load_contract` / `holdback_config` take for an invalid env knob.
    """
    from gateway_v2.runtime.resources import _DEFAULT_OWNER_QUEUE_MS, _OWNER_QUEUE_ENV_KEY

    if raw is None or not raw.strip():
        return _DEFAULT_OWNER_QUEUE_MS
    stripped = raw.strip()
    try:
        value = float(stripped)
    except ValueError:
        warnings.warn(
            f"{_OWNER_QUEUE_ENV_KEY}={stripped!r} is not a number; "
            f"using default owner-queue bound {_DEFAULT_OWNER_QUEUE_MS} ms",
            stacklevel=2,
        )
        return _DEFAULT_OWNER_QUEUE_MS
    if value <= 0 or math.isnan(value) or math.isinf(value):
        warnings.warn(
            f"{_OWNER_QUEUE_ENV_KEY}={value} is not positive-finite; "
            f"using default owner-queue bound {_DEFAULT_OWNER_QUEUE_MS} ms",
            stacklevel=2,
        )
        return _DEFAULT_OWNER_QUEUE_MS
    return value
