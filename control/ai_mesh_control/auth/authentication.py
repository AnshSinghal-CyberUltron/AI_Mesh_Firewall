"""Custom JWT authentication that rejects terminated users."""

from rest_framework_simplejwt.authentication import JWTAuthentication
from rest_framework_simplejwt.exceptions import InvalidToken

from .models import TerminatedSession


class JWTAuthenticationWithTermination(JWTAuthentication):
    """JWT auth that rejects requests if user has been terminated (session termination)."""

    def authenticate(self, request):
        result = super().authenticate(request)
        if result is None:
            return None
        user, validated_token = result
        if TerminatedSession.objects.filter(user=user).exists():
            raise InvalidToken("User session has been terminated.")
        return user, validated_token
