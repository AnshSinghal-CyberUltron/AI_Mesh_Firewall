"""M-26: EndpointListSerializer N+1 fix.

The old serializer evaluated obj.agents.all() twice per endpoint (once per
SerializerMethodField) and resolved each primary user with a per-row
User.objects.get — 3N+1 queries on GET /api/endpoints/. The batched list
serializer resolves all primary agents in ONE query and all users in ONE
in_bulk query, so list serialization is constant at 3 queries.
"""

from __future__ import annotations

from django.contrib.auth import get_user_model
from django.test import TestCase

from core.models import Agent, Endpoint
from core.serializers import EndpointListSerializer

User = get_user_model()


class EndpointListSerializerQueryTests(TestCase):
    def setUp(self):
        self.user1 = User.objects.create_user(
            username="ep_user1", password="pw", first_name="Alice", last_name="One"
        )
        self.user2 = User.objects.create_user(username="ep_user2", password="pw")

        self.ep1 = Endpoint.objects.create(name="EP1", identifier="ep-1")
        self.ep2 = Endpoint.objects.create(name="EP2", identifier="ep-2")
        self.ep3 = Endpoint.objects.create(name="EP3", identifier="ep-3")  # no agents
        self.ep4 = Endpoint.objects.create(name="EP4", identifier="ep-4")

        # ep1: older agent -> user2, newer agent -> user1 (primary must be user1)
        Agent.objects.create(
            agent_type="desktop", name="old-agent", endpoint=self.ep1, user_id=self.user2.id
        )
        Agent.objects.create(
            agent_type="desktop", name="new-agent", endpoint=self.ep1, user_id=self.user1.id
        )
        # ep2: single agent -> user2
        Agent.objects.create(
            agent_type="browser", name="b-agent", endpoint=self.ep2, user_id=self.user2.id
        )
        # ep4: agent without a user
        Agent.objects.create(agent_type="api", name="anon-agent", endpoint=self.ep4, user_id=None)

    def _rows_by_identifier(self, data):
        return {row["identifier"]: row for row in data}

    def test_list_serialization_is_constant_three_queries(self):
        qs = Endpoint.objects.all()
        with self.assertNumQueries(3):  # endpoints + agents batch + users in_bulk
            data = EndpointListSerializer(qs, many=True).data
        self.assertEqual(len(data), 4)

    def test_list_serialization_correctness(self):
        rows = self._rows_by_identifier(EndpointListSerializer(Endpoint.objects.all(), many=True).data)

        # Most recently updated agent wins (Agent.Meta.ordering = ["-updated_at"]).
        self.assertEqual(rows["ep-1"]["primary_user_id"], self.user1.id)
        self.assertEqual(rows["ep-1"]["primary_user_display"], "Alice One")

        self.assertEqual(rows["ep-2"]["primary_user_id"], self.user2.id)
        self.assertEqual(rows["ep-2"]["primary_user_display"], "ep_user2")

        # No agents -> both None.
        self.assertIsNone(rows["ep-3"]["primary_user_id"])
        self.assertIsNone(rows["ep-3"]["primary_user_display"])

        # Agent without user_id -> both None.
        self.assertIsNone(rows["ep-4"]["primary_user_id"])
        self.assertIsNone(rows["ep-4"]["primary_user_display"])

    def test_list_respects_existing_prefetch(self):
        qs = Endpoint.objects.prefetch_related("agents")
        # endpoints + prefetch(agents) + users in_bulk — no extra agents batch.
        with self.assertNumQueries(3):
            data = EndpointListSerializer(qs, many=True).data
        rows = self._rows_by_identifier(data)
        self.assertEqual(rows["ep-1"]["primary_user_id"], self.user1.id)

    def test_deleted_user_yields_none_display(self):
        dead_user_id = self.user2.id
        self.user2.delete()
        rows = self._rows_by_identifier(EndpointListSerializer(Endpoint.objects.all(), many=True).data)
        # user_id is still on the agent, but the User row is gone.
        self.assertEqual(rows["ep-2"]["primary_user_id"], dead_user_id)
        self.assertIsNone(rows["ep-2"]["primary_user_display"])

    def test_detail_serialization_single_agent_evaluation(self):
        endpoint = Endpoint.objects.get(pk=self.ep1.pk)
        with self.assertNumQueries(2):  # agents (once, memoized) + user get
            data = EndpointListSerializer(endpoint).data
        self.assertEqual(data["primary_user_id"], self.user1.id)
        self.assertEqual(data["primary_user_display"], "Alice One")
