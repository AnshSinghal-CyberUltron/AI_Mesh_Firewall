"""Copy gateway-v2 audit streams into EnforcementEvent rows.

Does not import gateway_v2. Does not call the v1 telemetry drain. One row per
request_id, attributed only to an organization whose slug matches the record
or an explicit ``AMF_V2_AUDIT_ORG_MAP`` entry.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any

from core.v2_audit_adapter import RejectedV2, TranslatedEvent, merge_row, parse_org_map, translate

logger = logging.getLogger(__name__)

_LOCK_KEY = "v2audit:ingest_lock"
_CURSOR_PREFIX = "v2audit:cursor:"
_DEFAULT_STREAM_PREFIX = "{rv2}:audit:"
_BATCH = 100


def ingest_pending(client: Any | None = None) -> int:
    """Read new audit entries and upsert rows. A missed lock returns 0."""
    import redis
    from django.conf import settings

    if client is None:
        client = redis.Redis.from_url(settings.REDIS_URL, decode_responses=True)
    if not client.set(_LOCK_KEY, "1", nx=True, ex=30):
        return 0
    try:
        return _ingest(client)
    finally:
        client.delete(_LOCK_KEY)


def _ingest(client: Any) -> int:
    prefix = os.environ.get("AMF_V2_AUDIT_STREAM_PREFIX", _DEFAULT_STREAM_PREFIX)
    mapping = parse_org_map(os.environ.get("AMF_V2_AUDIT_ORG_MAP", ""))
    written = 0
    for stream in _streams(client, prefix):
        written += _ingest_stream(client, stream, mapping)
    return written


def _streams(client: Any, prefix: str) -> list[str]:
    found: list[str] = []
    cursor = 0
    while True:
        cursor, keys = client.scan(cursor, match=f"{prefix}*", count=50)
        found.extend(key for key in keys if isinstance(key, str))
        if cursor == 0:
            break
    return sorted(set(found))


def _ingest_stream(client: Any, stream: str, mapping: dict[str, str]) -> int:
    cursor_key = _CURSOR_PREFIX + stream
    last = client.get(cursor_key)
    start = "-" if not last else f"({last}"
    rows = client.xrange(stream, min=start, max="+", count=_BATCH)
    written = 0
    last_ok: str | None = None
    for entry_id, fields in rows:
        raw = fields.get("r") if isinstance(fields, dict) else None
        try:
            if _apply_entry(raw, mapping):
                written += 1
        except json.JSONDecodeError:
            logger.warning("v2 audit ingest skipped a non-JSON record on %s", stream)
        except Exception:
            logger.exception("v2 audit ingest stopped on %s at %s", stream, entry_id)
            break
        last_ok = entry_id
    if last_ok is not None:
        client.set(cursor_key, last_ok)
    return written


def _apply_entry(raw: object, mapping: dict[str, str]) -> bool:
    if not isinstance(raw, str) or not raw:
        return False
    payload = json.loads(raw)
    translated = translate(payload)
    if not isinstance(translated, TranslatedEvent):
        if isinstance(translated, RejectedV2):
            logger.warning("v2 audit ingest rejected a record: %s", translated.reason)
        return False
    org_key = payload.get("org_id") if isinstance(payload, dict) else None
    if not isinstance(org_key, str) or not org_key:
        return False
    org = _organization(mapping.get(org_key, org_key))
    if org is None:
        logger.warning("v2 audit ingest has no organization for org_id=%s", org_key)
        return False
    _upsert(org, translated)
    return True


def _organization(slug: str) -> Any:
    from auth.models import Organization

    return Organization.objects.filter(slug=slug, is_active=True).first()


def _upsert(org: Any, translated: TranslatedEvent) -> None:
    from policy.models import EnforcementEvent

    request_id = translated.metadata.get("request_id")
    existing = (
        EnforcementEvent.objects.filter(
            organization=org,
            metadata__source="gateway_v2",
            metadata__request_id=request_id,
        )
        .order_by("-id")
        .first()
    )
    if existing is None:
        action, meta = merge_row(None, None, translated)
        EnforcementEvent.objects.create(
            organization=org,
            action=action[:16],
            metadata=meta,
        )
        return
    action, meta = merge_row(existing.action, existing.metadata, translated)
    existing.action = action[:16]
    existing.metadata = meta
    existing.save(update_fields=["action", "metadata"])
