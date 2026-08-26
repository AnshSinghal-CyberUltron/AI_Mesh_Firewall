"""Staff-only T-C6 canary: SELECT pg_sleep(6) on the analytics alias.

If AnalyticsTimeoutMiddleware applied SET LOCAL statement_timeout=5s, this
returns HTTP 504 query_timeout. If the deadline is missing, it returns 200
after six seconds. IsAdminUser only — not a tenant-facing surface.
"""

from __future__ import annotations

from rest_framework.permissions import IsAdminUser
from rest_framework.response import Response
from rest_framework.views import APIView

from main_app.analytics_db import ANALYTICS_DB_ALIAS


class AnalyticsTimeoutProbeView(APIView):
    permission_classes = [IsAdminUser]

    def get(self, request):
        from django.db import connections

        conn = connections[ANALYTICS_DB_ALIAS]
        if conn.vendor != "postgresql":
            return Response({"detail": "probe requires postgresql"}, status=501)
        with conn.cursor() as cursor:
            cursor.execute("SELECT pg_sleep(6)")
        return Response({"slept": 6})
