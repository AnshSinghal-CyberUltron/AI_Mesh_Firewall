"""API views for FirewallConfig (GET / PUT).

A caller with an organization reads and writes that organization's row. The row with
``organization IS NULL`` is the platform default: the v1 gateway serves it to every org that has
no row of its own (``gateway/ai_mesh_gateway/config_sync.py`` ``ConfigSync.get_config``). Only a
platform operator without an organization (``auth.models.is_platform_operator``: ``is_staff`` and
the profile flag) may read or change it, and every change writes an AuditLog row in the same
transaction. Anyone else without an organization gets 403 and nothing is read or written (#130).
"""

import ipaddress
import json
import logging

from django.db import transaction
from rest_framework import status
from rest_framework.exceptions import PermissionDenied
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from auth.models import is_platform_operator
from core.firewall_config_serializer import FirewallConfigSerializer
from core.models import AuditLog, FirewallConfig

logger = logging.getLogger(__name__)

PLATFORM_DEFAULT_AUDIT_ACTION = "platform_default_config_updated"
PLATFORM_DEFAULT_RESOURCE = "firewall_config:platform_default"
_NOT_AUDITED = frozenset({"id", "organization", "updated_at", "updated_by"})


def _stored_values(config: FirewallConfig) -> dict:
    return {
        f.attname: getattr(config, f.attname)
        for f in config._meta.concrete_fields
        if f.name not in _NOT_AUDITED
    }


def _remote_addr(request) -> str | None:
    """REMOTE_ADDR if it is a valid address (AuditLog.ip_address is inet), else None.

    This code never parses X-Forwarded-For; the raw header is kept only as a labelled detail. That
    does not make REMOTE_ADDR trustworthy in production: control runs under gunicorn's
    UvicornWorker with ``--forwarded-allow-ips "${GUNICORN_FORWARDED_ALLOW_IPS:-*}"``
    (``control/server-entrypoint.sh``). With ``*`` uvicorn trusts every peer and sets the client,
    hence REMOTE_ADDR, to the left-most X-Forwarded-For entry, which the client chooses (nginx
    appends the real peer on the right). Until that is pinned to the proxy (#154), this address is
    client-forgeable.
    """
    try:
        return str(ipaddress.ip_address(request.META.get("REMOTE_ADDR") or ""))
    except ValueError:
        return None


def _audit_platform_default_write(request, before: dict, config: FirewallConfig, created: bool) -> None:
    """Record a platform-default change. Runs inside the write's transaction: no row, no change."""
    after = _stored_values(config)
    changed = {k: [before.get(k), v] for k, v in after.items() if before.get(k) != v}
    details = {
        "created": created,
        "changed": changed,
        "requested_fields": sorted(str(k) for k in request.data),
        "x_forwarded_for_client_supplied": (request.META.get("HTTP_X_FORWARDED_FOR") or "")[:200],
    }
    AuditLog.objects.create(
        user=request.user,
        organization=None,
        action=PLATFORM_DEFAULT_AUDIT_ACTION,
        resource=PLATFORM_DEFAULT_RESOURCE,
        details=json.dumps(details, sort_keys=True, default=str),
        ip_address=_remote_addr(request),
    )


class FirewallConfigView(APIView):
    """
    Retrieve or update the caller's firewall configuration.

    GET  /api/firewall/config/  -- returns the current config with all fields
    PUT  /api/firewall/config/  -- partial update (any subset of fields)

    On PUT, the ``post_save`` signal syncs the config to Redis and
    publishes a notification to the ``config_updates`` Pub/Sub channel
    so the gateway hot-reloads without restart.
    """

    permission_classes = [IsAuthenticated]

    def _get_org(self, request):
        """The caller's organization; None means the platform default (operators only).

        Raises PermissionDenied (403) for a caller without an organization who is not a platform
        operator, before anything is read or written.
        """
        org = getattr(getattr(request.user, "profile", None), "organization", None)
        if org is None and not is_platform_operator(request.user):
            logger.warning(
                "FirewallConfig %s refused: user=%s has no organization and is not a platform operator",
                request.method,
                request.user.pk,
            )
            raise PermissionDenied("An organization is required to read or change the firewall configuration.")
        return org

    def get(self, request):
        org = self._get_org(request)
        # The platform default is read without load(): load() would create the row and publish it
        # to Redis, a write that only the audited PUT below may make.
        config = FirewallConfig.load(organization=org) if org is not None else FirewallConfig.platform_default()
        serializer = FirewallConfigSerializer(config, context={"organization": org})
        return Response(serializer.data)

    def put(self, request):
        org = self._get_org(request)
        if org is not None:
            # Ensure the singleton row exists (load() get_or_creates it) so the
            # select_for_update().get() below always finds a row to lock.
            FirewallConfig.load(organization=org)

        # Serialize the whole read-modify-write under a row-level lock so
        # concurrent PUTs to the same org's config serialize on the row instead
        # of clobbering each other (lost update). The serializer's validate()
        # merges request fields with the instance's stored values, so it must
        # run against the freshly-locked row, not a stale snapshot.
        before: dict = {}
        created = False
        with transaction.atomic():
            if org is not None:
                config = FirewallConfig.objects.select_for_update().get(organization=org)
            else:
                # Platform default (operator only). Created inside the transaction, so a request
                # that fails validation leaves no row behind, and the audit row covers creation.
                _, created = FirewallConfig.objects.get_or_create(organization__isnull=True)
                config = FirewallConfig.objects.select_for_update().get(organization__isnull=True)
                before = _stored_values(config)
            serializer = FirewallConfigSerializer(
                config,
                data=request.data,
                partial=True,
                context={"organization": org},
            )
            serializer.is_valid(raise_exception=True)
            serializer.save(updated_by=request.user)
            if org is None:
                _audit_platform_default_write(request, before, config, created)
        logger.info(
            "FirewallConfig updated by user=%s scope=%s (fields=%s)",
            request.user.username,
            org.slug if org is not None else "platform_default",
            list(request.data.keys()),
        )

        from core.compliance import validate_compliance_requirements

        config.refresh_from_db()
        compliance_warnings = validate_compliance_requirements(config)

        response_data = FirewallConfigSerializer(config, context={"organization": org}).data
        from core.firewall_v2_publish import publish_for_org

        response_data["gateway_v2_plan"] = publish_for_org(org)
        if compliance_warnings:
            response_data["compliance_warnings"] = compliance_warnings
            logger.warning(
                "FirewallConfig has %d compliance violations: %s",
                len(compliance_warnings),
                [w["message"] for w in compliance_warnings],
            )

        return Response(response_data, status=status.HTTP_200_OK)
