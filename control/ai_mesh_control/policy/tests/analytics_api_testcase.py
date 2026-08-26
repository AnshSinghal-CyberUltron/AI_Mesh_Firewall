"""Shared base for HTTP tests that hit /api/security/ or /api/dashboard/.

AnalyticsTimeoutMiddleware opens transaction.atomic(using='analytics') and
_enforcement_events_for_request queries that alias. Django TestCase wraps each
listed connection in its own atomic block; TEST MIRROR='default' shares the
physical DB but NOT the transaction, so default-connection fixtures are
invisible to analytics reads.

TransactionTestCase commits fixtures so both connections see them.
"""

from __future__ import annotations

from django.test import TransactionTestCase


class AnalyticsAPITestCase(TransactionTestCase):
    databases = {"default", "analytics"}
