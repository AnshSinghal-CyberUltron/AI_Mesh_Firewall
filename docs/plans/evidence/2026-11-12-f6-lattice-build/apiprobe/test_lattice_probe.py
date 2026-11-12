"""F6 Lattice API probe: what each overview / detail box would read today.

Not a product test. It runs inside the control test runner against a throwaway
test database, seeds enforcement events with KNOWN counts, calls every endpoint
the Lattice mockups name (and the ones the plan proposes instead), and writes
one JSON record per call: HTTP status, top-level keys, the X-Analytics-Source
header, wall time, and the numbers a box would show. It never prints or stores a
credential: key plaintext in the create response is dropped before writing.

Run (from <worktree>/control, after `. /home/claude/work/env-ctl.sh`):
  LATTICE_PROBE_OUT=/tmp/probe.json \
  PYTHONPATH=<worktree>/control:<worktree>/shared:<this dir> \
  flock /home/claude/work/.heavy.lock flock /home/claude/work/.ctl-db.lock \
  python manage.py test test_lattice_probe --noinput -v 1
"""

from __future__ import annotations

import json
import os
import time
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.utils import timezone
from rest_framework.test import APIClient

from policy.models import EnforcementEvent
from policy.tests.analytics_api_testcase import AnalyticsAPITestCase

User = get_user_model()

# (label, path) pairs: the Lattice source lines plus the parameter each box needs
# for "Last 24 hours". Paths are exactly what the console would request.
ENDPOINTS = [
    ("soc-kpis 24h", "/api/security/soc-kpis/?period=24h"),
    ("module-kpis 24h", "/api/security/module-kpis/?period=24h"),
    ("module-trends 24h", "/api/security/module-trends/?period=24h"),
    ("attack-vector-trends 24h", "/api/security/attack-vector-trends/?period=24h"),
    ("policies/analytics days=1", "/api/policies/analytics/?days=1"),
    ("policy/top-rules days=1", "/api/policy/top-rules/?days=1&limit=5"),
    ("owasp-stats hours=24", "/api/security/owasp-stats/?hours=24"),
    ("rag-pipeline-kpis", "/api/security/rag-pipeline-kpis/"),
    ("threat-feed hours=24", "/api/security/threat-feed/?hours=24&limit=5"),
    ("health", "/api/health/"),
    ("health/services", "/api/health/services/"),
    ("gateways/stats", "/api/gateways/stats/"),
    ("gateways/keys", "/api/gateways/keys/"),
    ("gateways/public-url", "/api/gateways/public-url/"),
    ("firewall/config", "/api/firewall/config/"),
    ("firewall/models", "/api/firewall/models/"),
    ("kill-switches", "/api/kill-switches/"),
    ("models/status", "/api/models/status/"),
    ("mcp-connector/health", "/api/mcp-connector/health/"),
    ("mcp-connector/servers", "/api/mcp-connector/servers/"),
    ("mcp-connector/events/summary hours=24", "/api/mcp-connector/events/summary/?hours=24"),
    ("policies", "/api/policies/"),
    ("policies/compile/status", "/api/policies/compile/status/"),
]

# Malformed or out-of-range parameters: how each endpoint fails today.
MALFORMED = [
    ("soc-kpis period=abc", "/api/security/soc-kpis/?period=abc"),
    ("owasp-stats hours=abc", "/api/security/owasp-stats/?hours=abc"),
    ("policies/analytics days=abc", "/api/policies/analytics/?days=abc"),
    ("policy/top-rules days=abc", "/api/policy/top-rules/?days=abc"),
    ("threat-feed hours=abc", "/api/security/threat-feed/?hours=abc"),
    ("threat-feed/{pk} unknown", "/api/security/threat-feed/999999999/"),
]

SECRET_FIELDS = {"key", "api_key", "plaintext_key", "raw_key", "secret", "token"}


def _shape(body):
    if isinstance(body, dict):
        return {"type": "object", "keys": sorted(body.keys())}
    if isinstance(body, list):
        first = body[0] if body else None
        return {
            "type": "array",
            "length": len(body),
            "item_keys": sorted(first.keys()) if isinstance(first, dict) else None,
        }
    return {"type": type(body).__name__}


def _scrub(obj):
    if isinstance(obj, dict):
        return {k: ("<dropped>" if k in SECRET_FIELDS else _scrub(v)) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_scrub(v) for v in obj]
    return obj


class LatticeApiProbe(AnalyticsAPITestCase):
    def setUp(self):
        from auth.models import Organization, UserProfile

        self.org = Organization.objects.create(name="Probe Org", slug="probe-org")
        # No password: the client uses force_authenticate, so the user gets an unusable one.
        self.user = User.objects.create_superuser(
            username="probe_admin", password=None, email="probe@example.com"
        )
        profile, _ = UserProfile.objects.get_or_create(user=self.user)
        profile.organization = self.org
        profile.save(update_fields=["organization"])
        self.client = APIClient(raise_request_exception=False)
        self.client.force_authenticate(user=self.user)

        # Known counts, request-level: 6 allow, 2 redact, 1 monitor, 1 block,
        # plus one request with two events (redact then block) that must count as
        # ONE blocked request. 12 events, 11 requests.
        now = timezone.now()
        plan = [
            ("allow", 6), ("redact", 2), ("monitor", 1), ("block", 1),
        ]
        n = 0
        for action, count in plan:
            for _ in range(count):
                n += 1
                EnforcementEvent.objects.create(
                    organization=self.org,
                    action=action,
                    created_at=now - timedelta(minutes=5 * n),
                    metadata={"request_id": f"probe-req-{n:04d}", "latency_ms": 10 * n,
                              "source": "gateway", "threat_type": "pii" if action == "redact" else ""},
                )
        for action in ("redact", "block"):
            EnforcementEvent.objects.create(
                organization=self.org,
                action=action,
                created_at=now - timedelta(minutes=3),
                metadata={"request_id": "probe-req-dual", "latency_ms": 42, "source": "gateway"},
            )
        # One event outside the 24 h window: must not be counted.
        EnforcementEvent.objects.create(
            organization=self.org, action="block", created_at=now - timedelta(hours=30),
            metadata={"request_id": "probe-req-old"},
        )
        self.seeded = {
            "events_in_window": 12, "requests_in_window": 11,
            "requests_allowed_only": 6, "requests_redacted": 2, "requests_monitored": 1,
            "requests_blocked": 2, "events_outside_window": 1,
        }

    def _call(self, label, path, method="get", data=None):
        t0 = time.perf_counter()
        resp = getattr(self.client, method)(path, data=data, format="json") if data is not None \
            else getattr(self.client, method)(path)
        ms = round((time.perf_counter() - t0) * 1000, 1)
        try:
            body = resp.json()
        except Exception:
            body = None
        rec = {
            "label": label, "method": method.upper(), "path": path, "status": resp.status_code,
            "ms": ms, "x_analytics_source": resp.headers.get("X-Analytics-Source"),
            "shape": _shape(body) if body is not None else None,
        }
        return rec, body

    def test_probe(self):
        out = {"seeded": self.seeded, "calls": [], "malformed": [], "agreement": {}, "notes": []}

        # Create one gateway key through the API so keys/stats have a row.
        rec, body = self._call(
            "gateways/keys POST", "/api/gateways/keys/", method="post",
            data={"name": "probe key", "project_id": "probe"},
        )
        rec["body_scrubbed"] = _scrub(body) if isinstance(body, dict) else None
        out["calls"].append(rec)

        bodies = {}
        for label, path in ENDPOINTS:
            rec, body = self._call(label, path)
            bodies[label] = body
            out["calls"].append(rec)

        ev = EnforcementEvent.objects.filter(organization=self.org).order_by("-created_at").first()
        rec, body = self._call("threat-feed/{pk}", f"/api/security/threat-feed/{ev.pk}/")
        out["calls"].append(rec)

        for label, path in MALFORMED:
            rec, body = self._call(label, path)
            rec["body_excerpt"] = (json.dumps(body)[:200] if body is not None else None)
            out["malformed"].append(rec)

        # Analytics concurrency cap: hold both permits, then ask for soc-kpis.
        from policy import analytics_concurrency as ac

        ac.reset_analytics_semaphore_for_tests(max_inflight=2)
        ac._semaphore.acquire()
        ac._semaphore.acquire()
        try:
            rec, body = self._call("soc-kpis while 2 analytics in flight", "/api/security/soc-kpis/?period=24h")
            rec["body_excerpt"] = json.dumps(body)[:200] if body is not None else None
            out["malformed"].append(rec)
        finally:
            ac._semaphore.release()
            ac._semaphore.release()

        soc = bodies.get("soc-kpis 24h") or {}
        pa = bodies.get("policies/analytics days=1") or {}
        keys = bodies.get("gateways/keys") or {}
        key_rows = keys.get("results", keys) if isinstance(keys, dict) else keys
        mt = bodies.get("module-trends 24h") or {}
        series = {}
        if isinstance(mt, dict):
            for k, v in mt.items():
                if isinstance(v, dict):
                    series[k] = {mk: (len(mv) if isinstance(mv, list) else type(mv).__name__)
                                 for mk, mv in list(v.items())[:8]}
                elif isinstance(v, list):
                    series[k] = len(v)
        avt = bodies.get("attack-vector-trends 24h")
        out["series_lengths"] = {
            "module-trends 24h": series,
            "attack-vector-trends 24h": (len(avt) if isinstance(avt, list) else _shape(avt)),
        }
        out["agreement"] = {
            "soc_kpis": {k: soc.get(k) for k in (
                "requests_inspected", "requests_allowed", "requests_blocked", "requests_redacted",
                "total_threats", "blocked", "redacted", "action_breakdown", "avg_latency_ms")},
            "policies_analytics": {k: pa.get(k) for k in (
                "total_violations", "total_blocked", "total_redacted", "total_monitored")},
            "keys_last_used_at": [r.get("last_used_at") for r in (key_rows or []) if isinstance(r, dict)],
            "health_body": bodies.get("health"),
        }
        path = os.environ.get("LATTICE_PROBE_OUT")
        if path:
            with open(path, "w", encoding="utf-8") as fh:
                json.dump(out, fh, indent=2, sort_keys=True, default=str)
        self.assertTrue(out["calls"])
