"""What the projector reads from control: one consistent view of the rows that decide state.

Control (Django) is the system of record for keys, organisations, kill switches and plan
documents. The projector reads them with plain SQL (`source_pg.py`) and never imports Django;
this module holds the value types both sides agree on, and an in-memory twin with the same
semantics for the offline suite.

Liveness is evaluated by the SOURCE, at the source's own clock (`as_of`): an expiring key is
live or not "as of this read", and the projector's safety heartbeat attests exactly that instant.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Protocol


@dataclass(frozen=True, slots=True)
class SourceOrg:
    slug: str
    active: bool


@dataclass(frozen=True, slots=True)
class SourceKey:
    """One gateway API key row. `org_slug` is None for an org-less row."""

    key_hash: str
    key_id: str
    org_slug: str | None
    active: bool
    """The key's own state at `as_of`: `is_active` and not past `expires_at`."""


@dataclass(frozen=True, slots=True)
class SourcePlan:
    """One organisation's published plan document, as control compiled it."""

    org_slug: str
    body: str
    revision: int


@dataclass(frozen=True, slots=True)
class SourceSwitch:
    """One ENGAGED kill-switch row. Disengaged rows are not read: absent means off."""

    org_slug: str | None
    model_name: str
    api_key_prefix: str
    action: str


@dataclass(frozen=True, slots=True)
class SourceView:
    as_of: float
    """Epoch seconds of the read's snapshot (the database's `transaction_timestamp()`)."""

    orgs: tuple[SourceOrg, ...]
    keys: tuple[SourceKey, ...]
    plans: tuple[SourcePlan, ...]
    switches: tuple[SourceSwitch, ...]


class ControlSource(Protocol):
    def read(self) -> SourceView:
        """One consistent snapshot of every row the projection needs."""
        ...


@dataclass
class _Key:
    key_id: str
    org_slug: str | None
    is_active: bool
    expires_at: float | None


@dataclass
class _Rows:
    orgs: dict[str, bool] = field(default_factory=dict)
    keys: dict[str, _Key] = field(default_factory=dict)
    plans: dict[str, tuple[str, int]] = field(default_factory=dict)
    switches: dict[tuple[str | None, str, str], str] = field(default_factory=dict)


class MemorySource:
    """Mutable twin of control's tables. Reads are snapshots; writes mimic the console."""

    def __init__(self, *, clock: Callable[[], float] = time.time) -> None:
        self._rows = _Rows()
        self._lock = threading.Lock()
        self._clock = clock
        self.fail_next = False

    def read(self) -> SourceView:
        with self._lock:
            if self.fail_next:
                self.fail_next = False
                raise ConnectionError("injected source failure")
            now = self._clock()
            rows = self._rows
            return SourceView(
                as_of=now,
                orgs=tuple(SourceOrg(slug, active) for slug, active in sorted(rows.orgs.items())),
                keys=tuple(
                    SourceKey(
                        key_hash=key_hash,
                        key_id=row.key_id,
                        org_slug=row.org_slug,
                        active=row.is_active and (row.expires_at is None or row.expires_at > now),
                    )
                    for key_hash, row in sorted(rows.keys.items())
                ),
                plans=tuple(
                    SourcePlan(slug, body, revision)
                    for slug, (body, revision) in sorted(rows.plans.items())
                ),
                switches=tuple(
                    SourceSwitch(org, model, prefix, action)
                    for (org, model, prefix), action in sorted(
                        rows.switches.items(), key=lambda item: repr(item[0]),
                    )
                ),
            )

    # --- console-shaped mutations ----------------------------------------------------------

    def org(self, slug: str, *, active: bool = True) -> None:
        with self._lock:
            self._rows.orgs[slug] = active

    def drop_org(self, slug: str) -> None:
        """A deleted org cascades to its keys, plan document and switches, as in Django."""
        with self._lock:
            rows = self._rows
            rows.orgs.pop(slug, None)
            rows.plans.pop(slug, None)
            for key_hash in [h for h, row in rows.keys.items() if row.org_slug == slug]:
                del rows.keys[key_hash]
            for scope in [s for s in rows.switches if s[0] == slug]:
                del rows.switches[scope]

    def key(
        self,
        key_hash: str,
        *,
        key_id: str,
        org_slug: str | None,
        active: bool = True,
        expires_at: float | None = None,
    ) -> None:
        with self._lock:
            self._rows.keys[key_hash] = _Key(key_id, org_slug, active, expires_at)

    def drop_key(self, key_hash: str) -> None:
        with self._lock:
            self._rows.keys.pop(key_hash, None)

    def plan(self, slug: str, body: str) -> None:
        with self._lock:
            _old, revision = self._rows.plans.get(slug, ("", 0))
            self._rows.plans[slug] = (body, revision + 1)

    def drop_plan(self, slug: str) -> None:
        with self._lock:
            self._rows.plans.pop(slug, None)

    def switch(
        self,
        org_slug: str | None,
        *,
        model_name: str = "__global__",
        api_key_prefix: str = "",
        action: str = "disable",
        engaged: bool = True,
    ) -> None:
        with self._lock:
            scope = (org_slug, model_name, api_key_prefix)
            if engaged:
                self._rows.switches[scope] = action
            else:
                self._rows.switches.pop(scope, None)

    def rename_org(self, old: str, new: str) -> None:
        """A slug edit: every row that references the org follows it, as a foreign key does."""
        with self._lock:
            rows = self._rows
            rows.orgs[new] = rows.orgs.pop(old)
            if old in rows.plans:
                rows.plans[new] = rows.plans.pop(old)
            for row in rows.keys.values():
                if row.org_slug == old:
                    row.org_slug = new
            for scope in [s for s in rows.switches if s[0] == old]:
                rows.switches[(new, scope[1], scope[2])] = rows.switches.pop(scope)
