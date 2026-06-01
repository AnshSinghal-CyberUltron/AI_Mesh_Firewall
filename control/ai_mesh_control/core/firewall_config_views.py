"""API views for FirewallConfig singleton (GET / PUT)."""

import logging

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
        config = FirewallConfig.load(organization=org)
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
