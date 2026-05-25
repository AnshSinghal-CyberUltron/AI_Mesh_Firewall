import logging

from celery import shared_task

logger = logging.getLogger(__name__)


@shared_task
def tier2_post_scan_task(payload: dict) -> dict:
    """Asynchronous Tier-2 follow-up scan payload sink."""
    logger.info(
        "tier2_post_scan_task request_id=%s org_id=%s mode=%s",
        payload.get("request_id", ""),
        payload.get("organization_id"),
        payload.get("execution_mode", ""),
    )
    return {"status": "queued", "request_id": payload.get("request_id", "")}


@shared_task
def chat_postprocess_task(payload: dict) -> dict:
    """Asynchronous post-response workflow payload sink."""
    logger.info(
        "chat_postprocess_task request_id=%s action=%s",
        payload.get("request_id", ""),
        payload.get("action", ""),
    )
    return {"status": "queued", "request_id": payload.get("request_id", "")}
