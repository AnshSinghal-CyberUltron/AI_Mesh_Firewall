"""Cached Docker connectivity probe — avoid blocking /health and hot RPC paths."""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sandbox.docker_manager import DockerManager

_docker_ok_cache: bool = False
_docker_ok_checked_at: float = 0.0
_DOCKER_OK_TTL_SECONDS = 15.0
_manager: DockerManager | None = None


def bind_docker_manager(manager: DockerManager) -> None:
    global _manager
    _manager = manager


def cached_docker_ok(*, force: bool = False) -> bool:
    global _docker_ok_cache, _docker_ok_checked_at
    if _manager is None:
        return False
    now = time.time()
    if force or now - _docker_ok_checked_at >= _DOCKER_OK_TTL_SECONDS:
        _docker_ok_cache = _manager.ping()
        _docker_ok_checked_at = now
    return _docker_ok_cache
