"""Tests for playbook kill_switch step execution."""

from django.test import TestCase

from auth.models import Organization
from module2.models import Playbook, PlaybookRun
from module2.tasks import execute_playbook


class PlaybookKillSwitchTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(name="Playbook Org", slug="playbook-org")
        self.playbook = Playbook.objects.create(
            organization=self.org,
            name="Contain key",
            steps=[
                {
                    "action": "kill_switch",
                    "params": {
                        "model_name": "gpt-4o",
                        "api_key_prefix": "abc12345",
                        "reason": "test playbook",
                    },
                }
            ],
        )
        self.run = PlaybookRun.objects.create(playbook=self.playbook, status="running")

    def test_execute_playbook_kill_switch_creates_active_switch(self):
        execute_playbook(self.run.id)
        self.run.refresh_from_db()
        self.assertEqual(self.run.status, "success")
        from core.models import KillSwitch

        ks = KillSwitch.objects.get(
            organization=self.org,
            model_name="gpt-4o",
            api_key_prefix="abc12345",
        )
        self.assertTrue(ks.is_active)
        self.assertEqual(ks.reason, "test playbook")
