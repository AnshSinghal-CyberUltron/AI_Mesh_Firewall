"""Background task that stops idle sandbox containers."""

from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass

from sandbox.docker_manager import DockerManager
from sandbox.registry import SandboxRegistry

LOG = logging.getLogger("mcp_broker.reaper")

_reaper_task: asyncio.Task | None = None


@dataclass(frozen=True)
class ReaperConfig:
    idle_timeout: float = 600.0
    interval_seconds: float = 60.0

    @classmethod
    def from_env(cls) -> ReaperConfig:
        return cls(
            idle_timeout=float(os.environ.get("MCP_SANDBOX_IDLE_TIMEOUT", "600")),
            interval_seconds=float(os.environ.get("MCP_SANDBOX_REAPER_INTERVAL", "60")),
        )


async def reap_idle_sandboxes(
    registry: SandboxRegistry,
    docker_manager: DockerManager,
    *,
    config: ReaperConfig | None = None,
    now: float | None = None,
) -> list[str]:
    """Stop and unregister sandboxes idle longer than idle_timeout."""
    cfg = config or ReaperConfig.from_env()
    stopped: list[str] = []
    for entry in registry.idle_entries(cfg.idle_timeout, now=now):
        LOG.info("Reaping idle sandbox for org=%s", entry.org_slug)
        await asyncio.to_thread(docker_manager.stop, entry.org_slug)
        registry.remove(entry.org_slug)
        stopped.append(entry.org_slug)
    return stopped


async def _reaper_loop(
    registry: SandboxRegistry,
    docker_manager: DockerManager,
    config: ReaperConfig,
) -> None:
    while True:
        await asyncio.sleep(config.interval_seconds)
        await reap_idle_sandboxes(registry, docker_manager, config=config)


def start_reaper(
    registry: SandboxRegistry,
    docker_manager: DockerManager,
    *,
    config: ReaperConfig | None = None,
) -> asyncio.Task:
    """Start the background idle-sandbox reaper task."""
    global _reaper_task
    cfg = config or ReaperConfig.from_env()
    if _reaper_task is None or _reaper_task.done():
        _reaper_task = asyncio.create_task(
            _reaper_loop(registry, docker_manager, cfg),
            name="mcp-sandbox-reaper",
        )
    return _reaper_task


async def stop_reaper() -> None:
    """Cancel the background reaper task if running."""
    global _reaper_task
    if _reaper_task is not None and not _reaper_task.done():
        _reaper_task.cancel()
        try:
            await _reaper_task
        except asyncio.CancelledError:
            pass
    _reaper_task = None
