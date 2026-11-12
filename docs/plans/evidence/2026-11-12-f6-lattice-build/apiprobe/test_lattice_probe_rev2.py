"""F6 Lattice API probe, revision 2: the claims the rev-1 plan reviews raised.

Not a product test. It runs inside the control test runner against a throwaway
test database (its own name, never the shared `test_aimesh`) and records, as JSON,
what the API does today for each claim the rev-2 plan relies on:

  P1  rule writes vs Policy.version, 409 and plan publishes      (Review A, A-B1)
  P2  /api/policies/test evaluates saved rules only              (rev-2 finding N-16)
  P3  log records for a 200 and a 404 carrying X-Request-ID      (Review B, B-B1)
  P4  analytics_busy: Retry-After, and an anonymous caller       (Review B, B-S2c)
  P5  threat-feed?limit=1 default window                         (Review A, A-N3)
  P6  silent parameter fallbacks                                 (Review A, A-N2)
  P7  module-kpis fields for 1.7                                 (Review A, A-S2)
  P8  sibling event ids open the same request                    (Review A, A-N8)
  P9  gateway key PATCH has no version (last write wins)         (Review A, edge cases)
  P10 keys list ignores an is_active filter                      (Review A, A-N4)
  P11 collapsed threat-feed paging stops at the 4,000-event scan (rev-2 finding N-15)

It never prints or stores a credential: key plaintext in create responses is
dropped before anything is written.

Run (from <worktree>/control, after `. /home/claude/work/env-ctl.sh`):
  DJANGO_SETTINGS_MODULE=f6rev2_settings LATTICE_PROBE2_OUT=/tmp/probe2.json \
  PYTHONPATH=<shim dir>:<worktree>/control/ai_mesh_control:<worktree>/shared:<this dir> \
  flock /home/claude/work/.heavy.lock flock /home/claude/work/.ctl-db.lock \
  nice -n 10 python manage.py test test_lattice_probe_rev2 --noinput -v 1
"""

from __future__ import annotations

import json
import logging
import os
from datetime import timedelta
from unittest import mock

from django.contrib.auth import get_user_model
from django.utils import timezone
from rest_framework.test import APIClient

from policy.models import EnforcementEvent, Policy, Rule
from policy.tests.analytics_api_testcase import AnalyticsAPITestCase

User = get_user_model()
OUT: dict = {}


class _Capture(logging.Handler):
    def __init__(self):
        super().__init__(level=logging.DEBUG)
        self.records: list[dict] = []

    def emit(self, record):
        from main_app.log_extras import get_request_id

        try:
            msg = record.getMessage()
        except Exception:  # noqa: BLE001
            msg = str(record.msg)
        self.records.append(
            {"logger": record.name, "level": record.levelname, "rid": get_request_id(), "msg": msg[:160]}
        )


def _attach(cap: _Capture):
    """Attach to root and to every logger that does not propagate."""
    attached = [logging.getLogger()]
    for name, lg in logging.root.manager.loggerDict.items():
        if isinstance(lg, logging.Logger) and not lg.propagate:
            attached.append(lg)
    old = {}
    for lg in attached:
        old[lg.name] = lg.level
        lg.addHandler(cap)
        lg.setLevel(logging.DEBUG)
    return attached, old


def _detach(cap, attached, old):
    for lg in attached:
        lg.removeHandler(cap)
        lg.setLevel(old.get(lg.name, logging.NOTSET))


def _write():
    path = os.environ.get("LATTICE_PROBE2_OUT")
    if path:
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(OUT, fh, indent=2, sort_keys=True, default=str)


class LatticeProbeRev2(AnalyticsAPITestCase):
    def setUp(self):
        from auth.models import Organization, UserProfile

        self.org = Organization.objects.create(name="Probe2 Org", slug="probe2-org")
        self.user = User.objects.create_superuser(username="probe2_admin", password=None,
                                                  email="probe2@example.com")
        profile, _ = UserProfile.objects.get_or_create(user=self.user)
        profile.organization = self.org
        profile.save(update_fields=["organization"])
        self.client = APIClient(raise_request_exception=False)
        self.client.force_authenticate(user=self.user)

    def _seed_small(self):
        now = timezone.now()
        n = 0
        for action, count in (("allow", 6), ("redact", 2), ("monitor", 1), ("block", 1)):
            for _ in range(count):
                n += 1
                EnforcementEvent.objects.create(
                    organization=self.org, action=action, created_at=now - timedelta(minutes=5 * n),
                    metadata={"request_id": f"probe2-req-{n:04d}", "source": "gateway"})
        dual = []
        for action in ("redact", "block"):
            dual.append(EnforcementEvent.objects.create(
                organization=self.org, action=action, created_at=now - timedelta(minutes=3),
                metadata={"request_id": "probe2-req-dual", "source": "gateway"}))
        return dual

    # ------------------------------------------------------------------ P1, P2
    def test_a_policy_rules(self):
        c = self.client
        pol = Policy.objects.create(organization=self.org, name="Probe2 policy", code="PROBE2",
                                    policy_domain="pipeline")
        rule = Rule.objects.create(policy=pol, name="r1", rule_type="keywords",
                                   condition={"keywords": ["alpha"], "field": "prompt"},
                                   action="block", priority=10)
        v0 = Policy.objects.get(pk=pol.pk).version
        with mock.patch("core.firewall_v2_publish.publish_for_org", return_value={"probe": True}) as pub:
            r_patch = c.patch(f"/api/policies/rules/{rule.pk}/", {"priority": 50, "action": "redact"},
                              format="json")
            v1 = Policy.objects.get(pk=pol.pk).version
            r_create = c.post("/api/policies/rules/", {"policy": pol.pk, "name": "r2", "rule_type": "keywords",
                                                       "condition": {"keywords": ["beta"], "field": "prompt"},
                                                       "action": "block", "priority": 5}, format="json")
            v2 = Policy.objects.get(pk=pol.pk).version
            r2_id = (r_create.json() or {}).get("id")
            r_tmp = c.post("/api/policies/rules/", {"policy": pol.pk, "name": "r3", "rule_type": "keywords",
                                                    "condition": {"keywords": ["gamma"], "field": "prompt"},
                                                    "action": "block", "priority": 1}, format="json")
            r_delete = c.delete(f"/api/policies/rules/{(r_tmp.json() or {}).get('id')}/")
            v3 = Policy.objects.get(pk=pol.pk).version
            publishes_after_rule_writes = pub.call_count
            r_stale_after_rules = c.patch(f"/api/policies/{pol.pk}/", {"description": "tab B", "version": v0},
                                          format="json")
            r_stale_after_policy = c.patch(f"/api/policies/{pol.pk}/", {"description": "tab C", "version": v0},
                                           format="json")
            publishes_total = pub.call_count
        OUT["P1_rule_writes"] = {
            "version_initial": v0,
            "rule_patch_status": r_patch.status_code, "version_after_rule_patch": v1,
            "rule_create_status": r_create.status_code, "version_after_rule_create": v2,
            "rule_delete_status": r_delete.status_code, "version_after_rule_delete": v3,
            "plan_publishes_for_4_rule_writes": publishes_after_rule_writes,
            "rules_in_db_now": sorted(Rule.objects.filter(policy=pol).values_list("name", "action", "priority")),
            "policy_patch_with_initial_version_after_rule_writes": r_stale_after_rules.status_code,
            "policy_patch_with_initial_version_after_policy_write": r_stale_after_policy.status_code,
            "plan_publishes_total": publishes_total,
        }

        # P2: a draft rule in the body is ignored; only saved rules are evaluated.
        draft = [{"name": "draft", "rule_type": "keywords",
                  "condition": {"keywords": ["zeta"], "field": "prompt"}, "action": "block", "priority": 99}]
        r_draft = c.post("/api/policies/test/", {"policy_id": pol.pk, "prompt": "zeta", "rules": draft},
                         format="json")
        r_saved = c.post("/api/policies/test/", {"policy_id": pol.pk, "prompt": "beta"}, format="json")
        Policy.objects.filter(pk=pol.pk).update(enabled=False)
        r_disabled = c.post("/api/policies/test/", {"policy_id": pol.pk, "prompt": "beta"}, format="json")
        OUT["P2_policy_test"] = {
            "draft_rule_in_body_prompt_matches_draft_only": {"status": r_draft.status_code,
                                                             "action": (r_draft.json() or {}).get("action")},
            "saved_rule_prompt": {"status": r_saved.status_code, "action": (r_saved.json() or {}).get("action")},
            "disabled_policy": {"status": r_disabled.status_code, "body": r_disabled.json()},
        }
        _write()
        self.assertTrue(OUT["P1_rule_writes"])

    # ------------------------------------------------------- P3 to P10
    def test_b_reads(self):
        c = self.client
        dual = self._seed_small()

        # P3: log records for a 200 and a 404 carrying X-Request-ID
        cap = _Capture()
        attached, old = _attach(cap)
        try:
            r200 = c.get("/api/security/soc-kpis/?period=24h", HTTP_X_REQUEST_ID="ui-traffic-probe2-0001")
            recs_200 = [x for x in cap.records if not x["logger"].startswith("django.db")]
            cap.records.clear()
            r404 = c.get("/api/security/threat-feed/999999999/", HTTP_X_REQUEST_ID="ui-detail-probe2-0002")
            recs_404 = [x for x in cap.records if not x["logger"].startswith("django.db")]
        finally:
            _detach(cap, attached, old)
        OUT["P3_logs"] = {
            "200": {"status": r200.status_code, "echoed": r200.headers.get("X-Request-ID"),
                    "records": recs_200,
                    "records_with_rid": sum(1 for x in recs_200 if x["rid"] == "ui-traffic-probe2-0001")},
            "404": {"status": r404.status_code, "echoed": r404.headers.get("X-Request-ID"),
                    "records": recs_404,
                    "records_with_rid": sum(1 for x in recs_404 if x["rid"] == "ui-detail-probe2-0002")},
        }

        # P4: analytics_busy headers, and an anonymous caller while busy
        from policy import analytics_concurrency as ac

        ac.reset_analytics_semaphore_for_tests(max_inflight=2)
        ac._semaphore.acquire()
        ac._semaphore.acquire()
        try:
            r = c.get("/api/security/soc-kpis/?period=24h")
            ra = APIClient(raise_request_exception=False).get("/api/security/soc-kpis/?period=24h")
        finally:
            ac._semaphore.release()
            ac._semaphore.release()
        rb = APIClient(raise_request_exception=False).get("/api/security/soc-kpis/?period=24h")
        OUT["P4_busy"] = {"status": r.status_code, "retry_after": r.headers.get("Retry-After"),
                          "body": r.json(), "anonymous_status_while_busy": ra.status_code,
                          "anonymous_status_when_idle": rb.status_code}

        # P7: module-kpis fields for 1.7
        r = c.get("/api/security/module-kpis/?period=24h")
        OUT["P7_module_kpis_1_7_keys"] = sorted(((r.json().get("modules") or {}).get("1.7") or {}).keys())

        # P8: the two events of one request open the same request
        a = c.get(f"/api/security/threat-feed/{dual[0].pk}/").json()
        b = c.get(f"/api/security/threat-feed/{dual[1].pk}/").json()
        OUT["P8_siblings"] = {
            "event_ids": [dual[0].pk, dual[1].pk],
            "request_id": [a.get("request_id"), b.get("request_id")],
            "id_returned": [a.get("id"), b.get("id")],
            "action": [a.get("action"), b.get("action")],
            "related_event_ids": [a.get("related_event_ids"), b.get("related_event_ids")],
        }

        # P6: silent parameter fallbacks
        r1 = c.get("/api/dashboard/model-usage/?days=abc")
        r2 = c.get("/api/security/owasp-stats/?hours=-5")
        r3 = c.get("/api/security/owasp-stats/?hours=24")
        OUT["P6_params"] = {
            "model_usage_days_abc": r1.status_code,
            "owasp_hours_minus5": {"status": r2.status_code,
                                   "unique_events_count": (r2.json() or {}).get("unique_events_count")},
            "owasp_hours_24_for_comparison": {"status": r3.status_code,
                                              "unique_events_count": (r3.json() or {}).get("unique_events_count")},
        }

        # P9: gateway key PATCH has no version: two tabs, last write wins
        created = c.post("/api/gateways/keys/", {"name": "probe2 key", "project_id": "probe2"}, format="json")
        kid = (created.json() or {}).get("id")
        p_a = c.patch(f"/api/gateways/keys/{kid}/", {"rate_limit_tokens_per_minute": 1000}, format="json")
        p_b = c.patch(f"/api/gateways/keys/{kid}/", {"name": "renamed in tab B"}, format="json")
        p_c = c.patch(f"/api/gateways/keys/{kid}/", {"rate_limit_tokens_per_minute": 5000, "version": 1},
                      format="json")
        after = c.get(f"/api/gateways/keys/{kid}/").json()
        OUT["P9_key_patch"] = {
            "create_status": created.status_code,
            "patch_statuses": [p_a.status_code, p_b.status_code, p_c.status_code],
            "has_version_field": "version" in after,
            "final_rate_limit": after.get("rate_limit_tokens_per_minute"),
            "final_name": after.get("name"),
        }

        # P10: keys list ignores is_active
        c.post("/api/gateways/keys/", {"name": "probe2 key 2", "project_id": "probe2"}, format="json")
        c.patch(f"/api/gateways/keys/{kid}/", {"is_active": False}, format="json")
        all_keys = c.get("/api/gateways/keys/").json()
        active_only = c.get("/api/gateways/keys/?is_active=true").json()
        OUT["P10_keys_filter"] = {
            "count_unfiltered": all_keys.get("count"),
            "count_with_is_active_true": active_only.get("count"),
            "page_size_default": len(all_keys.get("results") or []),
            "is_active_values": sorted(str(x.get("is_active")) for x in (all_keys.get("results") or [])),
        }

        # P5: newest event older than threat-feed's default hours=48
        EnforcementEvent.objects.filter(organization=self.org).update(
            created_at=timezone.now() - timedelta(hours=60))
        r_default = c.get("/api/security/threat-feed/?limit=1").json()
        r_year = c.get("/api/security/threat-feed/?limit=1&hours=8760").json()
        OUT["P5_threat_feed_window"] = {
            "default_hours": {"count": r_default.get("count"), "results": len(r_default.get("results") or [])},
            "hours_8760": {"count": r_year.get("count"), "results": len(r_year.get("results") or [])},
        }
        _write()
        self.assertTrue(OUT["P3_logs"])

    # ---------------------------------------------------------------- P11
    def test_c_feed_scan_cap(self):
        c = self.client
        now = timezone.now()
        total = 4050
        EnforcementEvent.objects.bulk_create([
            EnforcementEvent(organization=self.org, action="allow",
                             created_at=now - timedelta(seconds=10 * i),
                             metadata={"request_id": f"probe2-cap-{i:06d}", "source": "gateway"})
            for i in range(total)
        ])
        pages = {}
        for offset in (0, 3900, 3990, 4000):
            b = c.get(f"/api/security/threat-feed/?hours=24&limit=100&offset={offset}").json()
            pages[str(offset)] = {"count": b.get("count"), "results": len(b.get("results") or []),
                                  "scan_truncated": b.get("scan_truncated")}
        raw = c.get("/api/security/threat-feed/?hours=24&limit=100&offset=4000&collapse=false").json()
        OUT["P11_scan_cap"] = {
            "seeded_requests": total, "collapsed_pages": pages,
            "uncollapsed_offset_4000": {"count": raw.get("count"), "results": len(raw.get("results") or [])},
        }
        _write()
        self.assertTrue(OUT["P11_scan_cap"])
