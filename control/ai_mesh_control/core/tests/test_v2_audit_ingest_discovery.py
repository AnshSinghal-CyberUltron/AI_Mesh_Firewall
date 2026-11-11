"""v2 audit stream discovery is O(organizations) and never scans the shared Redis keyspace.

PR #100 review finding 5: the control drain loop ran ``SCAN MATCH {rv2}:audit:*`` every second
over the Redis that v1 shares, i.e. work proportional to every key in the store.
"""
from __future__ import annotations

import json
from unittest import mock

import fakeredis
from django.test import TestCase

from core import v2_audit_ingest
from policy.models import EnforcementEvent


class _NoScan(fakeredis.FakeRedis):
    def scan(self, *args, **kwargs):  # pragma: no cover - the assertion is that it never runs
        raise AssertionError("v2 audit discovery must not SCAN the keyspace")

    def scan_iter(self, *args, **kwargs):  # pragma: no cover
        raise AssertionError("v2 audit discovery must not SCAN the keyspace")


def _record(org_id: str, request_id: str) -> str:
    return json.dumps(
        {
            "v": 1,
            "request_id": request_id,
            "org_id": org_id,
            "phase": "input",
            "outcome": "block",
            "stages": [],
        }
    )


class DiscoveryTests(TestCase):
    def setUp(self):
        from auth.models import Organization

        self.org = Organization.objects.create(name="Acme", slug="acme")
        Organization.objects.create(name="Gone", slug="gone", is_active=False)
        self.client_ = _NoScan(decode_responses=True)
        for i in range(2_000):  # unrelated v1 keys sharing the store
            self.client_.set(f"llm:model_configs:org{i}", "x")

    def test_finds_slug_alias_and_unauthenticated_streams_only(self):
        prefix = "{rv2}:audit:"
        self.client_.xadd(prefix + "acme", {"r": "{}"})
        self.client_.xadd(prefix + "dev-org", {"r": "{}"})  # mapped alias
        self.client_.xadd(prefix + "_unauthenticated", {"r": "{}"})
        self.client_.xadd(prefix + "gone", {"r": "{}"})  # inactive org: not attributable
        self.client_.xadd(prefix + "stranger", {"r": "{}"})  # unknown id: not attributable

        found = v2_audit_ingest._streams(self.client_, prefix, {"dev-org": "acme"})

        self.assertEqual(
            found,
            [prefix + "_unauthenticated", prefix + "acme", prefix + "dev-org"],
        )

    def test_ingest_end_to_end_without_scan(self):
        prefix = "{rv2}:audit:"
        self.client_.xadd(prefix + "acme", {"r": _record("acme", "req-1")})
        with mock.patch.dict("os.environ", {"AMF_V2_AUDIT_ORG_MAP": ""}):
            written = v2_audit_ingest.ingest_pending(self.client_)
        self.assertEqual(written, 1)
        self.assertTrue(
            EnforcementEvent.objects.filter(
                organization=self.org, metadata__request_id="req-1"
            ).exists()
        )
