"""Publish a saved FirewallConfig as a gateway-v2 plan document.

The document is the JSON shape ``plan.document`` decodes. This module does
not import gateway_v2. A missing org map or a failed Redis write does not
undo the saved row.
"""

from __future__ import annotations

import json
import logging
import os
from collections.abc import Mapping
from typing import Any

from core.firewall_v2_map import drafts_for
from core.v2_audit_adapter import parse_org_map

logger = logging.getLogger(__name__)

PUBLISHED_PLAN_KEY = "{rv2}:dev:firewall-plan"
_PRIORITY = 100


def gateway_org_for(slug: str, raw_map: str) -> str | None:
    """The one gateway org mapped to this console slug. Ambiguous maps return none."""
    matches = [source for source, target in parse_org_map(raw_map).items() if target == slug]
    if len(matches) != 1:
        return None
    return matches[0]


def document_bytes(org_id: str, payload: Mapping[str, Any]) -> bytes:
    """The plan body for this payload. An empty rule list means the firewall is off."""
    rules = [{**row, "priority": _PRIORITY} for row in drafts_for(payload)]
    body = {"org_id": org_id, "rules": rules, "streaming_mode": "incremental"}
    return json.dumps(body, separators=(",", ":"), sort_keys=True).encode("utf-8")


def publish_saved_plan(slug: str | None, payload: Mapping[str, Any]) -> dict[str, object]:
    """Write the document. ``skipped`` and ``unavailable`` leave the gateway plan untouched."""
    org_id = gateway_org_for(slug or "", os.environ.get("AMF_V2_AUDIT_ORG_MAP", ""))
    if org_id is None:
        return {"status": "skipped", "reason": "org_not_mapped"}
    raw = document_bytes(org_id, payload)
    try:
        _write(raw)
    except Exception:
        logger.exception("gateway-v2 plan was not written for org %s", org_id)
        return {"status": "unavailable", "org_id": org_id}
    return {"status": "published", "org_id": org_id, "rules": raw.count(b'"rule_id"')}


def _write(raw: bytes) -> None:
    import redis
    from django.conf import settings

    client = redis.Redis.from_url(settings.REDIS_URL, socket_timeout=2, socket_connect_timeout=2)
    try:
        client.set(PUBLISHED_PLAN_KEY, raw)
    finally:
        client.close()
