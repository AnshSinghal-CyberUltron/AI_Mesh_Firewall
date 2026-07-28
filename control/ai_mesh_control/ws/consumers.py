import asyncio
import json
import logging
import time
from urllib.parse import parse_qs

from asgiref.sync import sync_to_async
from channels.generic.websocket import AsyncWebsocketConsumer
from django.conf import settings
from django.contrib.auth import get_user_model
from rest_framework_simplejwt.exceptions import ExpiredTokenError, InvalidToken
from rest_framework_simplejwt.tokens import AccessToken

LOG = logging.getLogger("ws.consumers")

NOTIFICATIONS_GROUP_PREFIX = "notifications"
HEARTBEAT_INTERVAL_SECONDS = 30  # DATA-04 FIX: Server-side heartbeat interval


def notification_group_for_org(org_id: int | str) -> str:
    # Channels group names must only contain ASCII alphanumerics, hyphens,
    # underscores, or periods. Avoid ':' which triggers runtime TypeError.
    return f"{NOTIFICATIONS_GROUP_PREFIX}_{org_id}"


def _get_origin_from_scope(scope):
    """Extract Origin header from ASGI scope headers (list of (bytes, bytes))."""
    headers = scope.get("headers") or []
    for name, value in headers:
        if name.lower() == b"origin":
            return value.decode("utf-8").strip() if value else ""
    return ""


def _get_token_from_scope(scope):
    """Extract token from query string (?token=...)."""
    qs = scope.get("query_string") or b""
    parsed = parse_qs(qs.decode("utf-8"))
    tokens = parsed.get("token", [])
    return tokens[0] if tokens else None


def _get_org_id_from_scope(scope):
    """Extract optional organization_id from query string (?organization_id=...)."""
    qs = scope.get("query_string") or b""
    parsed = parse_qs(qs.decode("utf-8"))
    values = parsed.get("organization_id", [])
    if not values:
        return None
    try:
        return int(values[0])
    except (TypeError, ValueError):
        return None


async def _get_user_from_token(token_string):
    """Validate JWT and return User or None."""
    User = get_user_model()

    def _get_user():
        try:
            access = AccessToken(token_string)
            user_id = access.get("user_id") or access.get("user")
            if not user_id:
                return None
            return User.objects.get(id=user_id)
        except (InvalidToken, ExpiredTokenError, User.DoesNotExist):
            return None

    return await sync_to_async(_get_user)()


class NotificationConsumer(AsyncWebsocketConsumer):
    """WebSocket consumer: origin check, optional JWT, notifications group, ping/pong."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._heartbeat_task = None
        self._connected = False

    async def _heartbeat_loop(self):
        """DATA-04 FIX: Server-side heartbeat to detect stale connections."""
        while self._connected:
            try:
                await asyncio.sleep(HEARTBEAT_INTERVAL_SECONDS)
                if self._connected:
                    await self.send(text_data=json.dumps({
                        "type": "heartbeat",
                        "timestamp": time.time()
                    }))
            except Exception as e:
                LOG.warning("Heartbeat failed for ws %s: %s", self.channel_name, e)
                await self.close()
                break

    async def connect(self):
        # Origin validation
        origin = _get_origin_from_scope(self.scope)
        allowed = getattr(settings, "ASGI_ALLOWED_ORIGINS", [])
        if allowed and origin not in allowed:
            LOG.warning("WebSocket origin rejected: %s", origin)
            await self.close(code=4403)
            return

        # JWT from query string is required for tenant-scoped channels.
        token = _get_token_from_scope(self.scope)
        if not token:
            await self.close(code=4401)
            return

        user = await _get_user_from_token(token)
        if not user:
            LOG.warning("WebSocket rejected: invalid or expired token")
            await self.close(code=4401)
            return

        self.scope["user"] = user
        self.scope["user_id"] = user.id

        # Resolve tenant boundary for this websocket session.
        # Use sync_to_async for Django ORM access in async context.
        def _get_org_from_profile(u):
            try:
                return u.profile.organization_id
            except Exception:
                return None

        requested_org_id = _get_org_id_from_scope(self.scope)
        profile_org_id = await sync_to_async(_get_org_from_profile, thread_sensitive=True)(user)

        org_id = None
        if user.is_superuser:
            # M-08 FIX: a superuser must explicitly select a tenant (?organization_id=...)
            # or have a profile org. Do NOT silently default to org 1 — that would
            # implicitly associate the session with a real, unintended tenant and leak
            # its notifications. When neither is present, org_id stays None and the
            # shared `if org_id is None:` guard below rejects the connection (4403).
            org_id = requested_org_id if requested_org_id is not None else profile_org_id
        else:
            org_id = profile_org_id
            # Defense-in-depth: a non-superuser client that claims a different org
            # in the query string must not join that tenant channel.
            if (
                requested_org_id is not None
                and profile_org_id is not None
                and requested_org_id != profile_org_id
            ):
                LOG.warning(
                    "WebSocket rejected: org claim mismatch user_id=%s claimed=%s profile=%s",
                    user.id,
                    requested_org_id,
                    profile_org_id,
                )
                await self.close(code=4403)
                return

        if org_id is None:
            LOG.warning("WebSocket rejected: no org_id for user_id=%s", user.id)
            await self.close(code=4403)
            return

        self.scope["organization_id"] = org_id
        self.group_name = notification_group_for_org(org_id)

        await self.accept()
        self._connected = True

        # Join organization-scoped notifications group for future broadcasts.
        if hasattr(self, "channel_layer") and self.channel_layer:
            await self.channel_layer.group_add(self.group_name, self.channel_name)

        # DATA-04 FIX: Start server-side heartbeat task
        self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())

    async def disconnect(self, close_code):
        # DATA-04 FIX: Stop heartbeat task on disconnect
        self._connected = False
        if self._heartbeat_task and not self._heartbeat_task.done():
            self._heartbeat_task.cancel()
            try:
                await self._heartbeat_task
            except asyncio.CancelledError:
                pass

        if hasattr(self, "channel_layer") and self.channel_layer:
            group = getattr(self, "group_name", None)
            if group:
                await self.channel_layer.group_discard(group, self.channel_name)

    async def notification_message(self, event):
        """Handle broadcast from group_send: forward message payload to WebSocket client."""
        payload = event.get("message") or event
        await self.send(text_data=json.dumps(payload))

    async def receive(self, text_data=None, bytes_data=None):
        if not text_data:
            return
        try:
            data = json.loads(text_data)
            action = data.get("type") or data.get("action")
            if action == "ping":
                await self.send(text_data=json.dumps({"type": "pong"}))
        except json.JSONDecodeError:
            pass
