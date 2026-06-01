"""
Microsoft Graph outbound mail for security alerts and operational notifications.

Uses application permissions (client credentials) — same pattern as backend POC email.
Falls back to Django SMTP when Graph credentials are not configured.
"""

from __future__ import annotations

import logging
import os
from typing import Iterable

import msal
import requests

logger = logging.getLogger(__name__)

_GRAPH_SCOPE = ["https://graph.microsoft.com/.default"]
_GRAPH_SEND_URL = "https://graph.microsoft.com/v1.0/users/{sender}/sendMail"


def graph_mail_configured() -> bool:
    """True when MSAL client-credentials env vars are present."""
    return bool(
        os.environ.get("TENANT_ID", "").strip()
        and os.environ.get("CLIENT_ID", "").strip()
        and os.environ.get("CLIENT_SECRET", "").strip()
    )


def _graph_sender_email() -> str:
    return (
        os.environ.get("GRAPH_SENDER_EMAIL", "").strip()
        or os.environ.get("ALERT_FROM_EMAIL", "").strip()
        or "support@zeroshield.ai"
    )


def acquire_graph_access_token() -> str:
    tenant_id = os.environ.get("TENANT_ID", "").strip()
    client_id = os.environ.get("CLIENT_ID", "").strip()
    client_secret = os.environ.get("CLIENT_SECRET", "").strip()
    if not (tenant_id and client_id and client_secret):
        raise RuntimeError("Microsoft Graph mail is not configured (missing TENANT_ID/CLIENT_ID/CLIENT_SECRET)")

    authority = f"https://login.microsoftonline.com/{tenant_id}"
    app = msal.ConfidentialClientApplication(
        client_id,
        authority=authority,
        client_credential=client_secret,
    )
    token = app.acquire_token_for_client(scopes=_GRAPH_SCOPE)
    if "access_token" not in token:
        desc = token.get("error_description") or token.get("error") or "unknown"
        raise RuntimeError(f"MSAL token acquisition failed: {desc}")
    return token["access_token"]


def send_graph_mail(
    *,
    recipients: Iterable[str],
    subject: str,
    body_text: str,
    body_html: str | None = None,
    sender_email: str | None = None,
    timeout_seconds: float = 15.0,
) -> bool:
    """
    Send email via Microsoft Graph ``/users/{sender}/sendMail``.

    Returns True when Graph accepts the message (HTTP 202).
    """
    to_list = [r.strip() for r in recipients if r and "@" in r]
    if not to_list:
        logger.warning("send_graph_mail: no valid recipients")
        return False

    sender = (sender_email or _graph_sender_email()).strip()
    access_token = acquire_graph_access_token()
    content_type = "HTML" if body_html else "Text"
    content = body_html if body_html else body_text

    payload = {
        "message": {
            "subject": subject,
            "body": {"contentType": content_type, "content": content},
            "toRecipients": [{"emailAddress": {"address": addr}} for addr in to_list],
        },
        "saveToSentItems": False,
    }
    url = _GRAPH_SEND_URL.format(sender=sender)
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
    }
    resp = requests.post(url, headers=headers, json=payload, timeout=timeout_seconds)
    if resp.status_code == 202:
        logger.info("Graph mail accepted for %d recipient(s), subject=%s", len(to_list), subject[:80])
        return True

    logger.error(
        "Graph sendMail failed status=%s body=%s",
        resp.status_code,
        (resp.text or "")[:500],
    )
    resp.raise_for_status()
    return False
