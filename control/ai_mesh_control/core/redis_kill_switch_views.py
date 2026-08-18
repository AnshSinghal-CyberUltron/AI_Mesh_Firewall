"""
Validate and repair org-scoped kill_switch:* keys in Redis.

Malformed payloads cause the gateway to fail-closed (503). Operators can
scan and optionally delete bad keys, then resync from the database.
"""

from __future__ import annotations

import json
import logging

from django.core.management import call_command
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from core.admin_views import IsAdminOrSuperuser
from core.models import KillSwitchAuditLog
from core.signals import _get_redis_client

logger = logging.getLogger(__name__)


def _org_slug(request) -> str | None:
    org = getattr(getattr(request.user, "profile", None), "organization", None)
    return org.slug if org else None


def _scan_org_kill_switch_keys(client, org_slug: str) -> list[dict]:
    """Return findings for keys matching kill_switch:{org_slug}:*"""
    prefix = f"kill_switch:{org_slug}:"
    findings: list[dict] = []

    for raw_key in client.scan_iter(f"{prefix}*"):
        key_str = raw_key.decode() if isinstance(raw_key, bytes) else str(raw_key)
        raw_val = client.get(raw_key)
        if raw_val is None:
            findings.append(
                {
                    "key": key_str,
                    "status": "missing_value",
                    "malformed": True,
                    "detail": "Key exists but has no value",
                }
            )
            continue

        text = ""
        try:
            text = raw_val if isinstance(raw_val, str) else raw_val.decode()
            data = json.loads(text)
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            findings.append(
                {
                    "key": key_str,
                    "status": "malformed_json",
                    "malformed": True,
                    "detail": str(exc),
                    "preview": text[:120] if text else "",
                }
            )
            continue

        if not isinstance(data, dict):
            findings.append(
                {
                    "key": key_str,
                    "status": "invalid_shape",
                    "malformed": True,
                    "detail": "Payload is not a JSON object",
                }
            )
            continue

        payload_slug = str(data.get("org_slug") or "").strip()
        key_parts = key_str.split(":")
        key_slug = key_parts[1] if len(key_parts) > 1 else ""
        slug_mismatch = bool(payload_slug and key_slug and payload_slug != key_slug)

        # Orphan credential keys missing `:model:` segment
        # Expected: kill_switch:{org}:credential:{prefix}:model:{model}
        # Bad:       kill_switch:{org}:credential:{prefix}
        orphan_credential = False
        if len(key_parts) >= 3 and key_parts[2] == "credential":
            # parts: [kill_switch, org, credential, prefix, model, model_name...]
            if len(key_parts) < 6 or key_parts[4] != "model":
                orphan_credential = True

        malformed = slug_mismatch or orphan_credential
        detail = "valid"
        if slug_mismatch:
            detail = f"payload.org_slug={payload_slug} key_slug={key_slug}"
        elif orphan_credential:
            detail = "credential key missing :model:{name} segment (gateway cannot match)"

        findings.append(
            {
                "key": key_str,
                "status": (
                    "orphan_credential"
                    if orphan_credential
                    else ("slug_mismatch" if slug_mismatch else "ok")
                ),
                "malformed": malformed,
                "detail": detail,
                "is_active": bool(data.get("is_active")),
                "action": data.get("action"),
            }
        )

    return findings


class RedisKillSwitchValidateView(APIView):
    """POST /api/admin/redis/kill-switches/validate/ — scan keys; optional repair."""

    permission_classes = [IsAuthenticated, IsAdminOrSuperuser]

    def post(self, request):
        org_slug = _org_slug(request)
        if not org_slug:
            return Response(
                {"error": "No organization on user profile"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        repair = bool(request.data.get("repair", False))
        client = _get_redis_client()
        findings = _scan_org_kill_switch_keys(client, org_slug)
        malformed = [f for f in findings if f.get("malformed")]
        repaired_keys: list[str] = []

        if repair and malformed:
            for item in malformed:
                key = item["key"]
                client.delete(key)
                repaired_keys.append(key)
                logger.warning(
                    "Redis kill-switch repair deleted key=%s org=%s by=%s",
                    key,
                    org_slug,
                    request.user.email,
                )

            call_command("resync_kill_switches", validate_slugs=True)

            KillSwitchAuditLog.objects.create(
                organization=request.user.profile.organization,
                event="redis_kill_switch_repair",
                model_name="__redis__",
                risk_score=0,
                threshold=0,
                action="repair",
                reason=f"Deleted {len(repaired_keys)} malformed Redis key(s)",
                triggered_by=request.user.email or request.user.username,
                metadata={"keys": repaired_keys},
            )

        return Response(
            {
                "org_slug": org_slug,
                "scanned": len(findings),
                "malformed_count": len(malformed),
                "repaired": repair,
                "repaired_keys": repaired_keys,
                "findings": findings,
            }
        )
