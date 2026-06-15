"""WIRE-3 smoke tests: guard the SOC/HITL route mounting and Celery task
registration against silent regression.

The review-queue/incident endpoints (1.7d) and the policy/vector compile +
ingest Celery tasks (1.3b) were each a regression that had to be fixed once —
a dropped ``include(...)`` line, a renamed view import, or a moved ``@shared_task``
would pass the rest of the suite and fail only at runtime / in prod. These
assertions turn that into a fast unit failure.
"""

from django.test import SimpleTestCase
from django.urls import NoReverseMatch, reverse


class SecurityWiringSmokeTest(SimpleTestCase):
    """Every SOC/HITL review + incident route must resolve under /api/security/."""

    def test_review_and_incident_routes_are_mounted(self):
        cases = [
            ("security-review-queue", {}),
            ("security-review-approve", {"pk": 1}),
            ("security-review-reject", {"pk": 1}),
            ("security-incident-list", {}),
            ("security-incident-escalate", {"pk": 1}),
            ("security-incident-resolve", {"pk": 1}),
            ("incident-escalate", {"pk": 1}),
            ("incident-resolve", {"pk": 1}),
        ]
        for name, kwargs in cases:
            try:
                url = reverse(name, kwargs=kwargs)
            except NoReverseMatch as exc:  # pragma: no cover - failure path
                self.fail(f"SOC/HITL route {name!r} is not mounted (NoReverseMatch): {exc}")
            self.assertTrue(
                url.startswith("/api/security/"),
                f"route {name!r} resolved to unexpected path {url!r}",
            )


class CeleryTaskRegistrationSmokeTest(SimpleTestCase):
    """The compile/ingest tasks must be discoverable by the worker at boot."""

    def test_compile_and_ingest_tasks_are_registered(self):
        from main_app.celery_app import app

        # Same path the worker runs at boot (autodiscover is lazy until this).
        app.loader.import_default_modules()
        required = {
            "policy.compile_policies",
            "policy.compile_vector_policies",
            "core.tasks.vector_ingest_task",
        }
        missing = required - set(app.tasks)
        self.assertFalse(missing, f"Celery tasks not registered (worker would not run them): {missing}")
