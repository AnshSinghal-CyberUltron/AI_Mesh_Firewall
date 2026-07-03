"""
ASGI config for main_app project.

Routes HTTP to Django and WebSocket to Channels.

Sync work offloaded off the event loop (non-thread-sensitive ``sync_to_async`` and
``loop.run_in_executor(None, ...)``) runs on the event loop's *default* thread-pool
executor. Python defaults that pool to ``min(32, os.cpu_count() + 4)`` — and
``os.cpu_count()`` is NOT cgroup-aware, so a ``--cpus=6`` container would still get
a 20-thread pool per worker (thread over-provisioning). We size it instead from the
cgroup-aware detector (``ASGI_THREADS``, set by control/server-entrypoint.sh) so the
offload pool tracks the real CPU budget and total threads stay bounded
(perf_scratchpad P3, items 09/11). Thread-*sensitive* sync (Django views/ORM) still
runs on asgiref's single-thread executor — untouched here; that is the P4 concern.
"""

import asyncio
import logging
import os
from concurrent.futures import ThreadPoolExecutor

from django.core.asgi import get_asgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "main_app.settings")

django_application = get_asgi_application()

from channels.routing import ProtocolTypeRouter, URLRouter  # noqa: E402

from main_app import routing  # noqa: E402

logger = logging.getLogger(__name__)

_protocol_router = ProtocolTypeRouter(
    {
        "http": django_application,
        "websocket": URLRouter(routing.websocket_urlpatterns),
    }
)

_executor_configured = False


def _configure_default_executor() -> None:
    """Size this worker's event-loop default executor from ASGI_THREADS (once).

    Idempotent and fail-safe: on no running loop it leaves ``_executor_configured``
    False so a later request retries; on a bad/absent value it leaves Python's
    default pool in place. An explicit ASGI_THREADS (env) is honored verbatim.
    """
    global _executor_configured
    if _executor_configured:
        return
    raw = os.environ.get("ASGI_THREADS", "").strip()
    if not raw:
        _executor_configured = True
        return
    try:
        n = int(raw)
    except ValueError:
        _executor_configured = True
        return
    if n <= 0:
        _executor_configured = True
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return  # no loop yet — retry on the next scope
    loop.set_default_executor(
        ThreadPoolExecutor(max_workers=n, thread_name_prefix="asgi-offload")
    )
    _executor_configured = True
    logger.info("ASGI default thread-pool executor sized to %d (ASGI_THREADS)", n)


async def application(scope, receive, send):
    if scope.get("type") != "lifespan":
        _configure_default_executor()
    await _protocol_router(scope, receive, send)
