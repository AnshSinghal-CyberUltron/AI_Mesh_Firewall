"""Phase 0a C-4b: cap concurrent heavy analytics views per worker."""

from __future__ import annotations

import os
import threading

from django.http import JsonResponse

ANALYTICS_MAX_INFLIGHT = int(os.environ.get("ANALYTICS_MAX_INFLIGHT", "2"))

_wait_s = 0.0
_semaphore = threading.BoundedSemaphore(ANALYTICS_MAX_INFLIGHT)


def reset_analytics_semaphore_for_tests(*, max_inflight: int = 2, wait_s: float = 0.0) -> None:
    global ANALYTICS_MAX_INFLIGHT, _semaphore, _wait_s
    ANALYTICS_MAX_INFLIGHT = max_inflight
    _wait_s = wait_s
    _semaphore = threading.BoundedSemaphore(max_inflight)


def _acquire() -> bool:
    if _wait_s <= 0:
        return _semaphore.acquire(blocking=False)
    return _semaphore.acquire(timeout=_wait_s)


class AnalyticsConcurrencyMixin:
    """Reject a third concurrent heavy analytics request with HTTP 503."""

    def dispatch(self, request, *args, **kwargs):
        if not _acquire():
            return JsonResponse(
                {
                    "error": "analytics_busy",
                    "detail": "Too many concurrent analytics queries",
                },
                status=503,
            )
        try:
            return super().dispatch(request, *args, **kwargs)
        finally:
            _semaphore.release()
