"""Control-plane routing pool hardening tests."""

from __future__ import annotations

import json
from io import StringIO
from unittest.mock import MagicMock, patch

from django.core.management import call_command
from django.test import TestCase

from auth.models import Organization, UserProfile
from core.llm_model_serializer import LLMModelConfigSerializer
from core.models import LLMModelConfig, is_reserved_inference_model_name
from core.signals import _sync_all_llm_models
from django.contrib.auth import get_user_model

User = get_user_model()


class RoutingPoolControlTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(name="Routing Org", slug="routing-org")
        self.user = User.objects.create_user(username="routinguser", password="Password123!")
        profile, _ = UserProfile.objects.get_or_create(user=self.user)
        profile.organization = self.org
        profile.save(update_fields=["organization"])

    def test_reserved_bedrock_foundation_id_detected(self):
        self.assertTrue(
            is_reserved_inference_model_name(
                "bedrock-llama-3",
                "meta.llama3-70b-instruct-v1:0",
            )
        )
        self.assertFalse(
            is_reserved_inference_model_name(
                "gpt-5.2",
                "openai/gpt-5.2",
            )
        )

    @patch("core.signals._get_redis_client")
    def test_sync_excludes_reserved_models_from_redis_routing(self, mock_redis_factory):
        client = MagicMock()
        mock_redis_factory.return_value = client

        LLMModelConfig.objects.create(
            organization=self.org,
            provider="aws_bedrock",
            model_name="bedrock-llama-3",
            model_id="meta.llama3-70b-instruct-v1:0",
            is_active=True,
        )
        LLMModelConfig.objects.create(
            organization=self.org,
            provider="openai",
            model_name="gpt-5.2",
            model_id="openai/gpt-5.2",
            is_active=True,
        )

        _sync_all_llm_models(None)
        payload = json.loads(client.set.call_args[0][1])
        names = {m["model_name"] for m in payload["routing"]}
        self.assertEqual(names, {"gpt-5.2"})
        self.assertEqual({m["model_name"] for m in payload["models"]}, {"gpt-5.2"})

    def test_serializer_rejects_activating_reserved_bedrock_foundation_id(self):
        serializer = LLMModelConfigSerializer(
            data={
                "provider": "aws_bedrock",
                "model_name": "bedrock-llama-3",
                "model_id": "meta.llama3-70b-instruct-v1:0",
                "is_active": True,
            }
        )
        self.assertFalse(serializer.is_valid())
        self.assertIn("non_field_errors", serializer.errors)

    def test_audit_routing_models_reports_reserved_models(self):
        LLMModelConfig.objects.create(
            organization=self.org,
            provider="aws_bedrock",
            model_name="bedrock-llama-3",
            model_id="meta.llama3-70b-instruct-v1:0",
            is_active=True,
        )
        out = StringIO()
        call_command("audit_routing_models", "--org", "routing-org", stdout=out)
        text = out.getvalue()
        self.assertIn("reserved_bedrock_foundation_id", text)
        self.assertIn("bedrock-llama-3", text)

    def test_audit_routing_models_fix_deactivates_flagged_models(self):
        model = LLMModelConfig.objects.create(
            organization=self.org,
            provider="aws_bedrock",
            model_name="bedrock-llama-3",
            model_id="meta.llama3-70b-instruct-v1:0",
            is_active=True,
        )
        call_command("audit_routing_models", "--org", "routing-org", "--fix")
        model.refresh_from_db()
        self.assertFalse(model.is_active)
