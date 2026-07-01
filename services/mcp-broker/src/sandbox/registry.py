"""In-memory registry of active per-org sandbox containers."""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass


@dataclass
class SandboxEntry:
    org_slug: str
    container_id: str
    agent_url: str
    last_activity: float


class SandboxRegistry:
    """Thread-safe map of org_slug → running sandbox metadata."""

    def __init__(self, clock: Callable[[], float] | None = None) -> None:
        self._entries: dict[str, SandboxEntry] = {}
        self._lock = threading.Lock()
        self._clock = clock or time.time

    def register(
        self,
        org_slug: str,
        container_id: str,
        agent_url: str,
        *,
        last_activity: float | None = None,
    ) -> SandboxEntry:
        now = self._clock() if last_activity is None else last_activity
        entry = SandboxEntry(
            org_slug=org_slug,
            container_id=container_id,
            agent_url=agent_url,
            last_activity=now,
        )
        with self._lock:
            self._entries[org_slug] = entry
        return entry

    def touch(self, org_slug: str, *, last_activity: float | None = None) -> SandboxEntry | None:
        now = self._clock() if last_activity is None else last_activity
        with self._lock:
            entry = self._entries.get(org_slug)
            if entry is None:
                return None
            entry.last_activity = now
            return entry

    def get(self, org_slug: str) -> SandboxEntry | None:
        with self._lock:
            entry = self._entries.get(org_slug)
            return entry

    def remove(self, org_slug: str) -> SandboxEntry | None:
        with self._lock:
            return self._entries.pop(org_slug, None)

    def list(self) -> list[SandboxEntry]:
        with self._lock:
            return list(self._entries.values())

    def idle_entries(self, idle_timeout: float, *, now: float | None = None) -> list[SandboxEntry]:
        current = self._clock() if now is None else now
        with self._lock:
            return [
                entry
                for entry in self._entries.values()
                if current - entry.last_activity > idle_timeout
            ]
