"""API views for FirewallConfig singleton (GET / PUT)."""

import logging

from django.db import transaction
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from core.firewall_config_serializer import FirewallConfigSerializer
from core.models import FirewallConfig

logger = logging.getLogger(__name__)


class FirewallConfigView(APIView):
    """
    Retrieve or update the singleton firewall configuration.

    GET  /api/firewall/config/  -- returns the current config with all fields
    PUT  /api/firewall/config/  -- partial update (any subset of fields)

    On PUT, the ``post_save`` signal syncs the config to Redis and
    publishes a notification to the ``config_updates`` Pub/Sub channel
    so the gateway hot-reloads without restart.
    """

    permission_classes = [IsAuthenticated]

    def _get_org(self, request):
        return getattr(getattr(request.user, "profile", None), "organization", None)

    def get(self, request):
        org = self._get_org(request)
        config = FirewallConfig.load(organization=org)
        serializer = FirewallConfigSerializer(config, context={"organization": org})
        return Response(serializer.data)

    def put(self, request):
        org = self._get_org(request)
        # Ensure the singleton row exists (load() get_or_creates it) so the
        # select_for_update().get() below always finds a row to lock.
        FirewallConfig.load(organization=org)

        # Serialize the whole read-modify-write under a row-level lock so
        # concurrent PUTs to the same org's config serialize on the row instead
        # of clobbering each other (lost update). The serializer's validate()
        # merges request fields with the instance's stored values, so it must
        # run against the freshly-locked row, not a stale snapshot.
        with transaction.atomic():
            if org is not None:
                config = FirewallConfig.objects.select_for_update().get(organization=org)
            else:
                config = FirewallConfig.objects.select_for_update().get(organization__isnull=True)
            serializer = FirewallConfigSerializer(
                config,
                data=request.data,
                partial=True,
                context={"organization": org},
            )
            serializer.is_valid(raise_exception=True)
            serializer.save(updated_by=request.user)
        logger.info(
            "FirewallConfig updated by user=%s (fields=%s)",
            request.user.username,
            list(request.data.keys()),
        )

        from core.compliance import validate_compliance_requirements

        config.refresh_from_db()
        compliance_warnings = validate_compliance_requirements(config)

        response_data = FirewallConfigSerializer(config, context={"organization": org}).data
        if compliance_warnings:
            response_data["compliance_warnings"] = compliance_warnings
            logger.warning(
                "FirewallConfig has %d compliance violations: %s",
                len(compliance_warnings),
                [w["message"] for w in compliance_warnings],
            )

        return Response(response_data, status=status.HTTP_200_OK)
