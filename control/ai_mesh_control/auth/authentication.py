"""Custom JWT authentication that rejects terminated users."""

from rest_framework_simplejwt.authentication import JWTAuthentication
from rest_framework_simplejwt.exceptions import InvalidToken

from .models import TerminatedSession


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
