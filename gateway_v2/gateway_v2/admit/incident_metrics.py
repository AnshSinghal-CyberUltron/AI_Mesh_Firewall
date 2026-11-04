"""GW29 incident metrics: a FIXED, label-free producer (design 5.10).

Same shape as `QuotaMetrics`: a class-level `SERIES_COUNT`, O(1) `observe_*` / `set_*` recorders,
`series()` (flat `name -> value`) and a frozen `snapshot()`. Every series exists from construction
as a real zero, so "no refusals" and "not instrumented" cannot look the same to an alarm. No method
accepts an org, tenant, key or request value, and model names never become labels: per-model risk
is the separate top-K family accounted for by `risk_family_series_count` (the cardinality
directory in `edge` owns the actual top-K selection).

`SERIES_COUNT = 9 refusals + 3 cuts + 11 misc + 4 gauges/ticker = 27`, pinned by a test against
`len(series())`.
"""

from __future__ import annotations

import threading
from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType

__all__ = (
    "CUT_SERIES",
    "PREFIX",
    "REFUSAL_SERIES",
    "RISK_FAMILY",
    "IncidentMetrics",
    "IncidentReading",
    "risk_family_series_count",
)

PREFIX = "amf_incident"
RISK_FAMILY = f"{PREFIX}_risk_score"
"""Name of the per-model top-K gauge family (padded to exactly K + other + overflow in `edge`)."""

# cause -> series. Unmapped causes land on `gate_error`: a refusal is never silently uncounted.
REFUSAL_SERIES: Mapping[str, str] = MappingProxyType({
    "stale": "refused_stale_total",
    "global": "refused_global_total",
    "org": "refused_org_total",
    "org_model": "refused_org_model_total",
    "model": "refused_model_total",
    "unresolvable_model": "refused_model_total",
    "unknown_model": "refused_model_total",
    "isolated_no_fallback": "refused_isolated_no_fallback_total",
    "reroute_refused": "refused_reroute_refused_total",
    "reroute_unsupported": "refused_reroute_refused_total",
    "gate_error": "refused_gate_error_total",
    "unwired": "refused_gate_error_total",
    "recheck": "refused_recheck_total",
})
CUT_SERIES: Mapping[str, str] = MappingProxyType({
    "cut_killed": "cut_killed_total",
    "cut_key_revoked": "cut_key_revoked_total",
    "cut_snapshot_stale": "cut_snapshot_stale_total",
})

_REFUSAL_NAMES = (
    "refused_stale_total", "refused_global_total", "refused_org_total", "refused_org_model_total",
    "refused_model_total", "refused_isolated_no_fallback_total", "refused_reroute_refused_total",
    "refused_gate_error_total", "refused_recheck_total",
)
_CUT_NAMES = ("cut_killed_total", "cut_key_revoked_total", "cut_snapshot_stale_total")
_MISC_NAMES = (
    "reroute_total", "auto_isolate_total", "auto_isolate_skipped_total", "recover_total",
    "isolated_models", "local_isolation_evicted_total", "risk_untracked_total",
    "events_dropped_total", "notifier_failures_total", "revocation_floor_cuts_total",
    "killswitch_age_seconds",
)
_GAUGE_NAMES = (
    "engaged_scopes", "quarantined_scopes", "scope_generation", "ticker_failures_total",
)
_ALL_NAMES = _REFUSAL_NAMES + _CUT_NAMES + _MISC_NAMES + _GAUGE_NAMES
_NEVER_LOADED = -1.0
"""`killswitch_age_seconds` sentinel when the snapshot has never loaded (as `state_metrics`)."""


def risk_family_series_count(top_k: int, other_series: int, overflow_series: int) -> int:
    """Series the top-K family adds: K + `other` + overflow, whatever the model count."""
    return top_k + other_series + overflow_series


@dataclass(frozen=True, slots=True)
class IncidentReading:
    """A frozen copy of every series; cannot drift under later observations."""

    values: Mapping[str, float]


class IncidentMetrics:
    """Producer for the incident series. Recording is O(1) and reads nothing."""

    SERIES_COUNT = len(_REFUSAL_NAMES) + len(_CUT_NAMES) + len(_MISC_NAMES) + len(_GAUGE_NAMES)
    """How many scalar series this producer can ever emit: 9 + 3 + 11 + 4 = 27, by construction."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._values: dict[str, float] = {name: 0.0 for name in _ALL_NAMES}
        self._values["killswitch_age_seconds"] = _NEVER_LOADED

    def _bump(self, name: str, by: float = 1.0) -> None:
        with self._lock:
            self._values[name] += by

    def _set(self, name: str, value: float) -> None:
        with self._lock:
            self._values[name] = value

    def observe_refusal(self, cause: str) -> None:
        self._bump(REFUSAL_SERIES.get(cause, "refused_gate_error_total"))

    def observe_cut(self, cause: str) -> None:
        self._bump(CUT_SERIES.get(cause, "cut_snapshot_stale_total"))

    def observe_reroute(self) -> None:
        self._bump("reroute_total")

    def observe_auto_isolate(self) -> None:
        self._bump("auto_isolate_total")

    def observe_auto_skipped(self) -> None:
        self._bump("auto_isolate_skipped_total")

    def observe_recover(self) -> None:
        self._bump("recover_total")

    def observe_event_dropped(self) -> None:
        self._bump("events_dropped_total")

    def observe_notifier_failure(self) -> None:
        self._bump("notifier_failures_total")

    def observe_ticker_failure(self) -> None:
        self._bump("ticker_failures_total")

    def observe_revocation_floor_cut(self) -> None:
        self._bump("revocation_floor_cuts_total")

    def set_isolated_models(self, count: int) -> None:
        self._set("isolated_models", float(count))

    def set_local_evicted(self, total: int) -> None:
        self._set("local_isolation_evicted_total", float(total))

    def set_risk_untracked(self, total: int) -> None:
        self._set("risk_untracked_total", float(total))

    def set_engaged_scopes(self, count: int) -> None:
        self._set("engaged_scopes", float(count))

    def set_quarantined(self, count: int) -> None:
        self._set("quarantined_scopes", float(count))

    def set_scope_generation(self, generation: int) -> None:
        self._set("scope_generation", float(generation))

    def set_killswitch_age(self, age_s: float | None) -> None:
        self._set("killswitch_age_seconds", _NEVER_LOADED if age_s is None else float(age_s))

    def series(self) -> dict[str, float]:
        """Flat `name -> value`, prefixed. The key set never depends on traffic or tenants."""
        with self._lock:
            return {f"{PREFIX}_{name}": value for name, value in self._values.items()}

    def snapshot(self) -> IncidentReading:
        with self._lock:
            return IncidentReading(MappingProxyType(dict(self._values)))
