import asyncio
import json
import logging
import time
from urllib.parse import parse_qs

from asgiref.sync import sync_to_async
from channels.generic.websocket import AsyncWebsocketConsumer
from django.conf import settings
from django.contrib.auth import get_user_model
from rest_framework_simplejwt.exceptions import ExpiredTokenError, InvalidToken, TokenError
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
    """Validate the access JWT and return its User, or None to refuse the socket.

    #122 review (A S2 / #131, connect part): the same user checks REST applies in
    auth/authentication.py JWTAuthenticationWithTermination, which this path skipped:
    an inactive user, a user with an active (uncleared) TerminatedSession, and a token
    minted before the user's last password change are refused. Sockets that are already
    open are not re-checked (#131).
    """
    User = get_user_model()

    def _get_user():
        from auth.authentication import token_predates_password_change
        from auth.models import TerminatedSession

        try:
            access = AccessToken(token_string)
            user_id = access.get("user_id") or access.get("user")
            if not user_id:
                return None
            user = User.objects.select_related("profile").get(id=user_id)
        except (InvalidToken, ExpiredTokenError, TokenError, User.DoesNotExist, ValueError, TypeError):
            return None
        if not user.is_active:
            LOG.warning("WebSocket rejected: inactive user_id=%s", user.pk)
            return None
        if TerminatedSession.objects.filter(user=user, cleared_at__isnull=True).exists():
            LOG.warning("WebSocket rejected: terminated session user_id=%s", user.pk)
            return None
        if token_predates_password_change(user, access):
            LOG.warning("WebSocket rejected: token predates password change user_id=%s", user.pk)
            return None
        return user

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

        def _resolve_org_id(u, requested_org_id):
            # #120 / #122, owner decision 12 Nov ("superuser = ZeroShield staff"):
            # - no ?organization_id, or the caller's own org: the caller's profile org;
            # - ZeroShield staff, or a platform operator (#139: is_staff + flag), may name
            #   any EXISTING org; the subscription is audited (cross_org_access) before
            #   the socket joins, and refused if the row cannot be written;
            # - anyone else naming another org joins their own org (the parameter is
            #   ignored, as REST's get_request_organization does) and the attempt is
            #   audited (cross_org_denied, the requested id only).
            from auth.audit import audit_cross_org_access, audit_cross_org_denied
            from auth.models import Organization, is_platform_operator, is_zeroshield_staff

            own_org_id = _get_org_from_profile(u)
            if requested_org_id is None or requested_org_id == own_org_id:
                return own_org_id
            if is_zeroshield_staff(u) or is_platform_operator(u):
                if not Organization.objects.filter(pk=requested_org_id).exists():
                    LOG.warning(
                        "WebSocket rejected: user_id=%s named unknown organization_id=%s", u.pk, requested_org_id
                    )
                    return None
                try:
                    audit_cross_org_access(
                        user=u, target_org_id=requested_org_id, action="ws.subscribe",
                        obj=f"ws:{notification_group_for_org(requested_org_id)}", via="websocket",
                    )
                except Exception:
                    LOG.exception("WebSocket rejected: cross-org audit write failed for user_id=%s", u.pk)
                    return None
                return requested_org_id
            audit_cross_org_denied(
                user=u, action="ws.subscribe", requested=f"organization:{requested_org_id}", via="websocket"
            )
            return own_org_id

        # M-08: never default to a tenant. With no org, the guard below rejects (4403).
        org_id = await sync_to_async(_resolve_org_id, thread_sensitive=True)(
            user, _get_org_id_from_scope(self.scope)
        )

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
