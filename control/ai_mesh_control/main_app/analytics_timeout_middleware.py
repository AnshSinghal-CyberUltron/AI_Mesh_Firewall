"""Phase 0a C-7: wrap analytics HTTP paths in a transaction + SET LOCAL."""

from __future__ import annotations

from django.db import transaction
from django.http import JsonResponse

from main_app.analytics_db import (
    ANALYTICS_DB_ALIAS,
    analytics_timeout_payload,
    apply_analytics_timeout,
    is_analytics_http_path,
    is_statement_timeout,
)


class AnalyticsTimeoutMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        path = getattr(request, "path", "") or ""
        if not is_analytics_http_path(path):
            return self.get_response(request)

        from django.db import connections

        if ANALYTICS_DB_ALIAS not in connections.databases:
            return self.get_response(request)

        try:
            with transaction.atomic(using=ANALYTICS_DB_ALIAS):
                apply_analytics_timeout()
                response = self.get_response(request)
                # A cancelled statement aborts the txn. DRF may already have
                # turned that into a 504 Response — COMMIT would then 500.
                if getattr(response, "status_code", 200) >= 400:
                    transaction.set_rollback(True, using=ANALYTICS_DB_ALIAS)
                return response
        except Exception as exc:
            if is_statement_timeout(exc):
                return JsonResponse(analytics_timeout_payload(), status=504)
            raise
