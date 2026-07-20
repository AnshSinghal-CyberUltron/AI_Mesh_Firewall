"""Custom JWT authentication that rejects terminated users."""

import logging
import time

from django.db import close_old_connections, connection
from django.db.utils import InterfaceError, OperationalError
from rest_framework_simplejwt.authentication import JWTAuthentication
from rest_framework_simplejwt.exceptions import InvalidToken

from .models import TerminatedSession

LOG = logging.getLogger(__name__)

# Transient Docker/DNS blips ("failed to resolve host 'postgres'") must not
# turn an otherwise-valid JWT into a hard 500 on Module 2 refetch paths.
_AUTH_DB_RETRIES = 2
_AUTH_DB_RETRY_SLEEP_SEC = 0.15


def token_predates_password_change(user, validated_token) -> bool:
    """True if an ACCESS token was minted before the user's last password change.

    ChangePasswordView stamps ``UserProfile.password_changed_at`` and revokes all
    refresh tokens, but an already-minted access token would otherwise survive for
    its full TTL (~60 min). Comparing the token ``iat`` against
    ``password_changed_at`` closes that window so a stolen access token dies the
    moment the victim rotates their password.
    """
    try:
        iat = validated_token.payload.get("iat")
        if iat is None:
            return False
        pca = getattr(getattr(user, "profile", None), "password_changed_at", None)
        if pca is None:
            return False
        # iat is integer epoch seconds; compare at second granularity (int floor of
        # password_changed_at) so a fresh post-change login is never false-rejected.
        return int(iat) < int(pca.timestamp())
    except Exception:  # noqa: BLE001 - never break auth on a stamp/clock hiccup
        return False


class JWTAuthenticationWithTermination(JWTAuthentication):
    """JWT auth that rejects terminated users and password-rotated access tokens."""

    def authenticate(self, request):
        last_exc = None
        for attempt in range(_AUTH_DB_RETRIES + 1):
            try:
                return self._authenticate_once(request)
            except (OperationalError, InterfaceError) as exc:
                last_exc = exc
                LOG.warning(
                    "JWT auth DB connectivity error (attempt %s/%s): %s",
                    attempt + 1,
                    _AUTH_DB_RETRIES + 1,
                    exc,
                )
                try:
                    connection.close()
                except Exception:  # noqa: BLE001 - best-effort reconnect
                    pass
                close_old_connections()
                if attempt >= _AUTH_DB_RETRIES:
                    break
                time.sleep(_AUTH_DB_RETRY_SLEEP_SEC * (attempt + 1))
        raise last_exc

    def _authenticate_once(self, request):
        result = super().authenticate(request)
        if result is None:
            return None
        user, validated_token = result
        # Only an *active* (uncleared) termination blocks the user; a reinstated
        # one keeps its audit row but no longer rejects requests.
        if TerminatedSession.objects.filter(user=user, cleared_at__isnull=True).exists():
            raise InvalidToken("User session has been terminated.")
        if token_predates_password_change(user, validated_token):
            raise InvalidToken("Token was issued before the last password change.")
        # P9b: stamp the org into the structured-log context AT auth time (early in
        # the request) so in-request log lines carry org_id, not just post-view ones.
        # The CorrelationIDMiddleware resets it in finally, so no cross-request leak.
        try:
            from main_app.log_extras import set_org_id
            set_org_id(getattr(getattr(user, "profile", None), "organization_id", None))
        except Exception:  # noqa: BLE001 - logging context must never break auth
            pass
        return user, validated_token
