"""Regression tests for VectorCollectionPolicy CRUD (RAG / Vector DB firewall).

Two bugs made the RAG & Vector DB firewall unusable from the UI:

1. ``project_id`` REQUIRED-but-blank. The model field lacks ``blank=True`` so the
   create serializer inferred ``required=True, allow_blank=False`` and rejected
   the frontend's default ``project_id=""`` with 400 "This field may not be
   blank." — nobody could create a per-collection policy without hand-entering an
   arbitrary project_id, and a policy-less collection then hard-403s every query
   (``rag_access_denied``). project_id is legacy: the gateway keys policies by
   ``{organization_id}::{collection}``.

2. CRUD did not reach the gateway WITHOUT a Celery worker. create/update/destroy
   only persisted + scheduled a debounced Celery recompile
   (``compile_vector_policies_task.apply_async``) consumed solely by the optional
   ``workers`` compose profile. With no worker running, the compiled Redis bundle
   never updated, so UI policy changes silently had no effect. The views now
   compile+push synchronously (mirroring provider-config sync).
"""

from __future__ import annotations

from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from policy.vector_models import VectorCollectionPolicy

User = get_user_model()


def _valid_payload(**overrides):
    payload = {
        "name": "kb policy",
        "collection_name": "zeroshield-rag-e2e",
        "vector_db_type": "pinecone",
        "default_action": "allow",
        "allowed_operations": ["query", "insert"],
        "max_results_per_query": 25,
        "max_query_length": 2048,
        "require_context_scan": True,
        "block_sensitive_documents": False,
        "enabled": True,
    }
    payload.update(overrides)
    return payload


class VectorPolicyCrudSyncTests(TestCase):
    def setUp(self):
        from auth.models import Organization, UserProfile

        self.org = Organization.objects.create(name="VP Org", slug="vp-org")
        self.user = User.objects.create_user(
            username="vp_admin", password="pw", is_staff=True, is_superuser=True
        )
        profile, _ = UserProfile.objects.get_or_create(user=self.user)
        profile.organization = self.org
        profile.save(update_fields=["organization"])
        self.client = APIClient()
        self.client.force_authenticate(self.user)

    # ── Bug 1: blank project_id must be accepted ──

    @patch("policy.vector_compiler.VectorPolicyCompiler.compile_and_push", return_value=True)
    def test_create_with_blank_project_id_returns_201(self, _compile):
        resp = self.client.post(
            "/api/vector-policies/", _valid_payload(project_id=""), format="json"
        )
        self.assertEqual(resp.status_code, 201, resp.content)
        self.assertEqual(resp.data["project_id"], "")
        self.assertTrue(
            VectorCollectionPolicy.objects.filter(
                organization=self.org, collection_name="zeroshield-rag-e2e"
            ).exists()
        )

    @patch("policy.vector_compiler.VectorPolicyCompiler.compile_and_push", return_value=True)
    def test_create_omitting_project_id_returns_201(self, _compile):
        payload = _valid_payload()
        payload.pop("project_id", None)
        resp = self.client.post("/api/vector-policies/", payload, format="json")
        self.assertEqual(resp.status_code, 201, resp.content)
        self.assertEqual(resp.data["project_id"], "")

    # ── Bug 2: CRUD must compile+push synchronously (no Celery worker) ──

    @patch("policy.vector_compiler.VectorPolicyCompiler.compile_and_push", return_value=True)
    def test_create_compiles_synchronously(self, mock_compile):
        resp = self.client.post(
            "/api/vector-policies/", _valid_payload(project_id=""), format="json"
        )
        self.assertEqual(resp.status_code, 201, resp.content)
        mock_compile.assert_called_once()

    @patch("policy.vector_compiler.VectorPolicyCompiler.compile_and_push", return_value=True)
    def test_update_compiles_synchronously(self, mock_compile):
        pol = VectorCollectionPolicy.objects.create(
            organization=self.org, name="p", project_id="",
            collection_name="c1", vector_db_type="pinecone",
            default_action="allow", allowed_operations=["query"], enabled=True,
        )
        mock_compile.reset_mock()
        resp = self.client.patch(
            f"/api/vector-policies/{pol.id}/", {"default_action": "monitor"}, format="json"
        )
        self.assertEqual(resp.status_code, 200, resp.content)
        mock_compile.assert_called_once()

    @patch("policy.vector_compiler.VectorPolicyCompiler.compile_and_push", return_value=True)
    def test_destroy_compiles_synchronously(self, mock_compile):
        pol = VectorCollectionPolicy.objects.create(
            organization=self.org, name="p", project_id="",
            collection_name="c2", vector_db_type="pinecone",
            default_action="allow", allowed_operations=["query"], enabled=True,
        )
        mock_compile.reset_mock()
        resp = self.client.delete(f"/api/vector-policies/{pol.id}/")
        self.assertEqual(resp.status_code, 204, resp.content)
        mock_compile.assert_called_once()
        self.assertFalse(VectorCollectionPolicy.objects.filter(id=pol.id).exists())

    @patch("policy.vector_compiler.VectorPolicyCompiler.compile_and_push", return_value=True)
    def test_compile_failure_does_not_break_crud(self, mock_compile):
        # A compile/push hiccup must not fail the persisted CRUD response.
        mock_compile.side_effect = RuntimeError("redis down")
        resp = self.client.post(
            "/api/vector-policies/", _valid_payload(project_id=""), format="json"
        )
        self.assertEqual(resp.status_code, 201, resp.content)
