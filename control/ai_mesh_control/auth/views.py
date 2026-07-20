import logging

from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db.models import Q
from django.utils import timezone
from drf_spectacular.utils import OpenApiExample, extend_schema, inline_serializer
from rest_framework import generics, serializers, status
from rest_framework.permissions import AllowAny, IsAdminUser, IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.exceptions import InvalidToken, TokenError
from rest_framework_simplejwt.serializers import TokenRefreshSerializer
from rest_framework_simplejwt.settings import api_settings as jwt_api_settings
from rest_framework_simplejwt.tokens import RefreshToken, UntypedToken
from rest_framework_simplejwt.views import (
    TokenObtainPairView,
    TokenRefreshView,
    TokenVerifyView,
)

from django.db import connection, transaction

from .models import TerminatedSession, UserProfile
from .throttling import LoginEmailThrottle, LoginIPThrottle
from .serializers import (
    OFFERING_ROLES,
    CustomTokenObtainPairSerializer,
    UserCreateSerializer,
    UserManagementSerializer,
    UserMeSerializer,
    UserSelfProfileUpdateSerializer,
    UserUpdateSerializer,
    _get_offering_role,
)
from .utils import get_request_organization

User = get_user_model()
logger = logging.getLogger(__name__)


def _blacklist_user_refresh_tokens(user) -> int:
    """Blacklist every outstanding refresh token for a user.

    Used on password change (and could be reused for admin reset) so a
    phished/stolen refresh token cannot keep minting access tokens after the
    victim remediates. Relies on the rest_framework_simplejwt.token_blacklist
    app (installed); each refresh token is recorded as an OutstandingToken when
    issued/rotated.
    """
    try:
        from rest_framework_simplejwt.token_blacklist.models import (
            BlacklistedToken,
            OutstandingToken,
        )
    except Exception:  # noqa: BLE001 - blacklist app not installed
        return 0
    count = 0
    for ot in OutstandingToken.objects.filter(user_id=getattr(user, "pk", None)):
        _, created = BlacklistedToken.objects.get_or_create(token=ot)
        if created:
            count += 1
    return count


class CustomTokenObtainPairView(TokenObtainPairView):
    """POST /api/auth/token/ — login with email + password."""

    permission_classes = [AllowAny]
    serializer_class = CustomTokenObtainPairSerializer
    # Brute-force / credential-stuffing protection: cap attempts per IP and per
    # target email (HTTP 429 on exceed). See auth/throttling.py.
    throttle_classes = [LoginIPThrottle, LoginEmailThrottle]

    @extend_schema(
        tags=["Auth"],
        summary="Login (obtain JWT tokens)",
        description=(
            "Authenticate with email and password to obtain a JWT access/refresh token pair.\n\n"
            "**To use Try It:** Replace the example email and password below with your "
            "own account credentials, then click Send.\n\n"
            "The access token should be sent in subsequent requests as "
            "`Authorization: Bearer <access_token>`.\n\n"
            "Access tokens expire after the configured lifetime (default: 60 minutes). "
            "Use the refresh token at `/api/auth/token/refresh/` to obtain a new access token. "
            "Refresh tokens rotate on each use and the previous one is blacklisted.\n\n"
            "**No account?** Ask your administrator, or create one via CLI:\n"
            "```\n"
            "docker compose exec backend python manage.py createsuperuser\n"
            "```"
        ),
        request=inline_serializer(
            name="LoginRequest",
            fields={
                "email": serializers.EmailField(help_text="User email address"),
                "password": serializers.CharField(help_text="User password"),
            },
        ),
        responses={
            200: inline_serializer(
                name="LoginResponse",
                fields={
                    "access": serializers.CharField(help_text="JWT access token"),
                    "refresh": serializers.CharField(help_text="JWT refresh token"),
                    "token_type": serializers.CharField(help_text="Always 'bearer'"),
                    "expires_in": serializers.FloatField(help_text="Access token lifetime in seconds"),
                },
            ),
            401: inline_serializer(
                name="LoginErrorResponse",
                fields={
                    "detail": serializers.CharField(help_text="Error message"),
                },
            ),
        },
        examples=[
            OpenApiExample(
                "Login request (replace with your credentials)",
                description=(
                    "Replace the email and password with your own account credentials. "
                    "If you do not have an account, create one via the frontend dashboard "
                    "or run: docker compose exec backend python manage.py createsuperuser"
                ),
                value={"email": "your-email@example.com", "password": "your-password"},
                request_only=True,
            ),
            OpenApiExample(
                "Successful login",
                value={
                    "access": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
                    "refresh": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
                    "token_type": "bearer",
                    "expires_in": 3600.0,
                },
                response_only=True,
                status_codes=["200"],
            ),
        ],
    )
    def post(self, request: Request, *args, **kwargs):
        # request.data may be a non-dict (malformed JSON array/string/number);
        # extract the email defensively so the failed-login log can never raise
        # on the error path (that produced an UNAUTHENTICATED 500).
        email_for_log = (
            request.data.get("email", "") if isinstance(request.data, dict) else ""
        )
        serializer = self.get_serializer(data=request.data)
        try:
            serializer.is_valid(raise_exception=True)
        except Exception:
            logger.warning(
                "Login failed for email=%s from ip=%s",
                email_for_log,
                request.META.get("REMOTE_ADDR", ""),
            )
            return Response({"detail": "Invalid email or password."}, status=status.HTTP_401_UNAUTHORIZED)
        data = serializer.validated_data
        # SimpleJWT returns access, refresh; add token_type and expires_in for API contract
        from rest_framework_simplejwt.settings import api_settings

        access = data["access"]
        refresh = data["refresh"]
        user = data.get("user")
        if user:
            user.last_login = timezone.now()
            user.save(update_fields=["last_login"])
            logger.info(
                "User logged in: user_id=%s email=%s from ip=%s",
                user.pk,
                getattr(user, "email", ""),
                request.META.get("REMOTE_ADDR", ""),
            )
        else:
            logger.info("Login successful from ip=%s", request.META.get("REMOTE_ADDR", ""))
        return Response(
            {
                "access": str(access),
                "refresh": str(refresh),
                "token_type": "bearer",
                "expires_in": api_settings.ACCESS_TOKEN_LIFETIME.total_seconds(),
            }
        )


class AtomicTokenRefreshSerializer(TokenRefreshSerializer):
    """Refresh-token rotation that is safe under concurrency.

    With ROTATE_REFRESH_TOKENS + BLACKLIST_AFTER_ROTATION, stock SimpleJWT does
    a read-then-blacklist with no lock: two concurrent refreshes of the SAME
    token both pass the blacklist check before either blacklists it, so BOTH
    mint new (usable) token chains — one stolen token becomes N live sessions.

    Fix: take a per-jti Postgres transaction advisory lock before validating, so
    concurrent refreshes of the same token serialize. The first rotates and
    blacklists; the rest, once they acquire the lock, see the now-committed
    blacklist entry and are rejected (TokenError -> 401).
    """

    def validate(self, attrs):
        raw = attrs.get("refresh") or ""
        jti = ""
        token_user_id = None
        try:
            payload = RefreshToken(raw, verify=False).payload
            jti = str(payload.get(jwt_api_settings.JTI_CLAIM, "") or "")
            token_user_id = payload.get(jwt_api_settings.USER_ID_CLAIM)
        except Exception:  # noqa: BLE001 - malformed token; let super() reject it
            jti = ""
            token_user_id = None
        # Apply the SAME disabled/terminated gate as login BEFORE minting a new
        # access token. Otherwise a terminated (or deactivated) user keeps issuing
        # fresh access tokens via /token/refresh until the 7-day refresh expires,
        # even though every API call from that access token is rejected. Reject
        # outright so termination is effective on the refresh path too.
        if token_user_id is not None:
            target = User.objects.filter(pk=token_user_id).first()
            if target is not None and (
                not target.is_active
                or TerminatedSession.objects.filter(user=target, cleared_at__isnull=True).exists()
            ):
                raise InvalidToken("User session has been terminated.")
        with transaction.atomic():
            if jti and connection.vendor == "postgresql":
                with connection.cursor() as cur:
                    # xact-scoped advisory lock keyed on the refresh jti; released
                    # at commit so the next concurrent refresh sees the blacklist.
                    cur.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", [jti])
            return super().validate(attrs)


class AtomicTokenRefreshView(TokenRefreshView):
    """TokenRefreshView using the concurrency-safe rotation serializer."""

    serializer_class = AtomicTokenRefreshSerializer


class HardenedTokenVerifyView(TokenVerifyView):
    """POST /api/auth/token/verify/ — verify an ACCESS token only (auth #8).

    The stock TokenVerifyView only checks signature+expiry, so it would return
    200 for a REFRESH token (which must never be presented as a bearer access
    token) and ignores session termination / password rotation. This view:
      * rejects non-access tokens (``token_type != 'access'``),
      * rejects a token whose user has an active (uncleared) session termination,
      * rejects an access token minted before the user's last password change,
    mirroring the live authentication gate so /verify can never bless a token the
    authenticator would reject.
    """

    def post(self, request: Request, *args, **kwargs):
        from .authentication import token_predates_password_change

        raw = request.data.get("token") if isinstance(request.data, dict) else None
        if not raw:
            return Response({"detail": "token is required."}, status=status.HTTP_400_BAD_REQUEST)
        try:
            token = UntypedToken(raw)  # validates signature + expiry
        except TokenError:
            return Response(
                {"detail": "Token is invalid or expired."}, status=status.HTTP_401_UNAUTHORIZED
            )
        payload = token.payload
        if payload.get("token_type") != "access":
            return Response(
                {"detail": "Only access tokens can be verified."},
                status=status.HTTP_401_UNAUTHORIZED,
            )
        uid = payload.get(jwt_api_settings.USER_ID_CLAIM)
        if uid is not None:
            target = User.objects.filter(pk=uid).select_related("profile").first()
            if target is None or not target.is_active:
                return Response({"detail": "User is inactive."}, status=status.HTTP_401_UNAUTHORIZED)
            if TerminatedSession.objects.filter(user=target, cleared_at__isnull=True).exists():
                return Response(
                    {"detail": "User session has been terminated."},
                    status=status.HTTP_401_UNAUTHORIZED,
                )
            if token_predates_password_change(target, token):
                return Response(
                    {"detail": "Token was issued before the last password change."},
                    status=status.HTTP_401_UNAUTHORIZED,
                )
        return Response({}, status=status.HTTP_200_OK)


@extend_schema(
    tags=["Auth"],
    summary="Get current user profile",
    description="Returns the authenticated user's profile information including roles.",
    responses={200: UserMeSerializer},
    examples=[
        OpenApiExample(
            "Current user",
            value={
                "id": 1,
                "email": "you@yourcompany.com",
                "first_name": "Admin",
                "last_name": "User",
                "is_active": True,
                "roles": ["admin", "user"],
            },
            response_only=True,
        ),
    ],
)
class MeView(generics.RetrieveAPIView):
    """GET /api/auth/me/ — current user from JWT."""

    permission_classes = [IsAuthenticated]
    serializer_class = UserMeSerializer

    def get_object(self):
        return self.request.user


class MeProfileUpdateView(generics.GenericAPIView):
    """PATCH /api/auth/me/profile/ — update current user's editable profile fields."""

    permission_classes = [IsAuthenticated]
    serializer_class = UserSelfProfileUpdateSerializer

    @extend_schema(
        tags=["Auth"],
        summary="Update current user profile",
        description=(
            "Update editable profile fields for the authenticated user.\n\n"
            "Supported fields: `first_name`, `last_name`, `email`, and `preferences`.\n"
            "Changing email requires `current_password`."
        ),
        request=UserSelfProfileUpdateSerializer,
        responses={200: UserMeSerializer},
    )
    def patch(self, request: Request):
        serializer = self.get_serializer(data=request.data, context={"request": request})
        serializer.is_valid(raise_exception=True)
        user = serializer.update(request.user, serializer.validated_data)
        logger.info("Profile updated for user_id=%s fields=%s", request.user.pk, list(serializer.validated_data.keys()))
        return Response(UserMeSerializer(user).data, status=status.HTTP_200_OK)


class LogoutView(generics.GenericAPIView):
    """POST /api/auth/logout/ — server-side logout: blacklist the refresh token."""

    permission_classes = [IsAuthenticated]

    @extend_schema(
        tags=["Auth"],
        summary="Logout",
        description=(
            "Server-side logout. Pass the current `refresh` token in the body to revoke it "
            "(it is added to the blacklist and can no longer be used to mint access tokens). "
            "The client should also discard its access token.\n\nReturns 204 No Content on success."
        ),
        request=inline_serializer(
            name="LogoutRequest",
            fields={
                "refresh": serializers.CharField(required=False, help_text="Refresh token to revoke"),
            },
        ),
        responses={204: None},
    )
    def post(self, request: Request):
        if not isinstance(request.data, dict):
            # A non-object JSON body (list/string/number) would crash
            # `request.data.get(...)` with AttributeError -> authenticated 500.
            return Response(
                {"detail": "Request body must be a JSON object."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        # Revoke the refresh token so it (and tokens rotated from it) cannot be reused.
        refresh_token = request.data.get("refresh")
        if refresh_token:
            from rest_framework_simplejwt.exceptions import TokenError
            from rest_framework_simplejwt.tokens import RefreshToken

            try:
                token = RefreshToken(refresh_token)
                # Ownership check: only blacklist a refresh token that belongs to
                # the authenticated caller. Without this, anyone could pass a
                # victim's refresh token here and revoke their session (the token's
                # user is not otherwise tied to request.user). Treat a mismatch as
                # an idempotent no-op so logout never leaks token validity.
                token_user_id = token.payload.get(jwt_api_settings.USER_ID_CLAIM)
                if str(token_user_id) != str(request.user.pk):
                    logger.warning(
                        "Logout: refusing to blacklist refresh token owned by a different user "
                        "(caller user_id=%s)",
                        request.user.pk,
                    )
                else:
                    token.blacklist()
            except TokenError:
                # Already expired/blacklisted/invalid — logout is still idempotently successful.
                pass
            except Exception:  # noqa: BLE001 - never fail logout on a revoke hiccup
                logger.warning("Logout: failed to blacklist refresh token for user_id=%s", request.user.pk)
        return Response(status=status.HTTP_204_NO_CONTENT)


class ChangePasswordView(APIView):
    """POST /api/auth/change-password/ — change the authenticated user's password."""

    permission_classes = [IsAuthenticated]

    @extend_schema(
        tags=["Auth"],
        summary="Change password",
        description="Change the authenticated user's password. Requires the current password for verification.",
        request=inline_serializer(
            name="ChangePasswordRequest",
            fields={
                "old_password": serializers.CharField(help_text="Current password"),
                "new_password": serializers.CharField(help_text="New password (min 8 chars)"),
                "confirm_password": serializers.CharField(help_text="Confirm new password"),
            },
        ),
        responses={
            200: inline_serializer(
                name="ChangePasswordResponse",
                fields={"detail": serializers.CharField()},
            ),
            400: inline_serializer(
                name="ChangePasswordError",
                fields={"detail": serializers.CharField()},
            ),
        },
    )
    def post(self, request: Request):
        if not isinstance(request.data, dict):
            # A non-object JSON body would crash `request.data.get(...)` with
            # AttributeError -> authenticated 500.
            return Response(
                {"detail": "Request body must be a JSON object."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        old_password = request.data.get("old_password")
        new_password = request.data.get("new_password")
        confirm_password = request.data.get("confirm_password")

        if not old_password or not new_password or not confirm_password:
            return Response(
                {"detail": "old_password, new_password, and confirm_password are required."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if not request.user.check_password(old_password):
            return Response({"detail": "Current password is incorrect."}, status=status.HTTP_400_BAD_REQUEST)

        if new_password != confirm_password:
            return Response({"detail": "Passwords do not match."}, status=status.HTTP_400_BAD_REQUEST)

        # Enforce the configured AUTH_PASSWORD_VALIDATORS (common/numeric/similarity/
        # length). A hand-rolled length check alone accepted weak passwords like
        # "password" or "12345678"; run Django's validators before persisting.
        try:
            validate_password(new_password, user=request.user)
        except DjangoValidationError as exc:
            return Response({"detail": " ".join(exc.messages)}, status=status.HTTP_400_BAD_REQUEST)

        request.user.set_password(new_password)
        request.user.save(update_fields=["password"])
        # Stamp password_changed_at so any already-minted ACCESS token (iat earlier
        # than now) is rejected at authentication time — otherwise a stolen access
        # token survives a password change for its full 60-min TTL. Refresh tokens
        # are revoked below; this closes the access-token gap.
        changed_at = timezone.now()
        try:
            profile, _ = UserProfile.objects.get_or_create(user=request.user)
            profile.password_changed_at = changed_at
            profile.save(update_fields=["password_changed_at", "updated_at"])
        except Exception:  # noqa: BLE001 - never fail the password change on stamp hiccup
            logger.warning("Failed to stamp password_changed_at for user_id=%s", request.user.pk)
        # Invalidate all of this user's existing refresh tokens so a phished /
        # stolen token cannot survive the password change (the primary remediation
        # for account compromise). Blacklists every outstanding refresh token.
        revoked = _blacklist_user_refresh_tokens(request.user)
        logger.info("Password changed for user_id=%s (revoked %s outstanding token(s))", request.user.pk, revoked)
        return Response({"detail": "Password changed successfully."}, status=status.HTTP_200_OK)


class SessionTerminateView(APIView):
    """POST /api/auth/sessions/terminate/ — terminate a user session (admin/staff only)."""

    permission_classes = [IsAuthenticated, IsAdminUser]

    @extend_schema(
        tags=["Auth"],
        summary="Terminate user session (admin only)",
        description=(
            "Forcefully terminates a user's session. Requires admin/staff privileges.\n\n"
            "After termination, the user's existing JWT tokens will be rejected on the next request "
            "(via the `JWTAuthenticationWithTermination` backend)."
        ),
        request=inline_serializer(
            name="SessionTerminateRequest",
            fields={
                "user_id": serializers.IntegerField(help_text="ID of the user whose session to terminate"),
                "reason": serializers.CharField(required=False, help_text="Optional reason for termination"),
            },
        ),
        responses={
            200: inline_serializer(
                name="SessionTerminateResponse",
                fields={
                    "detail": serializers.CharField(),
                    "user_id": serializers.IntegerField(),
                },
            ),
            400: inline_serializer(
                name="SessionTerminateBadRequest",
                fields={"user_id": serializers.CharField()},
            ),
            404: inline_serializer(
                name="SessionTerminateNotFound",
                fields={"detail": serializers.CharField()},
            ),
        },
        examples=[
            OpenApiExample(
                "Terminate session",
                value={"user_id": 5, "reason": "Suspicious activity detected"},
                request_only=True,
            ),
            OpenApiExample(
                "Success",
                value={"detail": "Session terminated.", "user_id": 5},
                response_only=True,
                status_codes=["200"],
            ),
        ],
    )
    def post(self, request: Request):
        user_id = request.data.get("user_id")
        if user_id is None:
            return Response({"user_id": "Required."}, status=status.HTTP_400_BAD_REQUEST)

        user = User.objects.filter(pk=user_id).select_related("profile").first()
        if not user:
            return Response({"detail": "User not found."}, status=status.HTTP_404_NOT_FOUND)

        req_org = get_request_organization(request)
        target_org_id = getattr(getattr(user, "profile", None), "organization_id", None)
        if req_org is not None and target_org_id != req_org.id and not request.user.is_superuser:
            return Response({"detail": "User not found."}, status=status.HTTP_404_NOT_FOUND)

        reason = request.data.get("reason", "")
        TerminatedSession.objects.create(
            user=user,
            reason=reason,
            terminated_by=request.user,
        )
        return Response({"detail": "Session terminated.", "user_id": user_id}, status=status.HTTP_200_OK)


class SessionReinstateView(APIView):
    """POST /api/auth/sessions/reinstate/ — lift a user's session termination (admin/staff only)."""

    permission_classes = [IsAuthenticated, IsAdminUser]

    @extend_schema(
        tags=["Auth"],
        summary="Reinstate user session (admin only)",
        description=(
            "Clears any active (uncleared) session termination for a user so they can "
            "authenticate again. Requires admin/staff privileges.\n\n"
            "The termination audit rows are retained (marked cleared) rather than deleted."
        ),
        request=inline_serializer(
            name="SessionReinstateRequest",
            fields={
                "user_id": serializers.IntegerField(help_text="ID of the user whose session to reinstate"),
            },
        ),
        responses={
            200: inline_serializer(
                name="SessionReinstateResponse",
                fields={
                    "detail": serializers.CharField(),
                    "user_id": serializers.IntegerField(),
                    "cleared": serializers.IntegerField(),
                },
            ),
            400: inline_serializer(
                name="SessionReinstateBadRequest",
                fields={"user_id": serializers.CharField()},
            ),
            404: inline_serializer(
                name="SessionReinstateNotFound",
                fields={"detail": serializers.CharField()},
            ),
        },
        examples=[
            OpenApiExample(
                "Reinstate session",
                value={"user_id": 5},
                request_only=True,
            ),
            OpenApiExample(
                "Success",
                value={"detail": "Session reinstated.", "user_id": 5, "cleared": 1},
                response_only=True,
                status_codes=["200"],
            ),
        ],
    )
    def post(self, request: Request):
        user_id = request.data.get("user_id")
        if user_id is None:
            return Response({"user_id": "Required."}, status=status.HTTP_400_BAD_REQUEST)

        user = User.objects.filter(pk=user_id).select_related("profile").first()
        if not user:
            return Response({"detail": "User not found."}, status=status.HTTP_404_NOT_FOUND)

        req_org = get_request_organization(request)
        target_org_id = getattr(getattr(user, "profile", None), "organization_id", None)
        if req_org is not None and target_org_id != req_org.id and not request.user.is_superuser:
            return Response({"detail": "User not found."}, status=status.HTTP_404_NOT_FOUND)

        cleared = TerminatedSession.objects.filter(user=user, cleared_at__isnull=True).update(
            cleared_at=timezone.now(),
            cleared_by=request.user,
        )
        logger.info("Session reinstated for user_id=%s by %s (cleared %s)", user_id, request.user.email, cleared)
        return Response(
            {"detail": "Session reinstated.", "user_id": user_id, "cleared": cleared},
            status=status.HTTP_200_OK,
        )


# ---------------------------------------------------------------------------
# User Management - shared by both offerings (filtered by ?offering=)
# ---------------------------------------------------------------------------


class IsOfferingAdmin(IsAuthenticated):
    """Allow access only if the requesting user is admin for the requested offering."""

    def has_permission(self, request, view):
        if not super().has_permission(request, view):
            return False
        if request.user.is_superuser:
            return True
        offering = request.query_params.get("offering") or request.data.get("offering")
        if not offering:
            return False
        return _get_offering_role(request.user, offering) == "admin"


@extend_schema(tags=["User Management"])
class UserManagementListCreateView(APIView):
    """
    GET  /api/auth/users/?offering=platform  — list users (admin only)
    POST /api/auth/users/                              — create user (admin only)
    """

    permission_classes = [IsAuthenticated]

    def _check_admin(self, request, offering):
        if request.user.is_superuser:
            return True
        return _get_offering_role(request.user, offering) == "admin"

    @extend_schema(
        summary="List users for an offering",
        parameters=[
            inline_serializer(
                name="OfferingParam",
                fields={"offering": serializers.ChoiceField(choices=list(OFFERING_ROLES.keys()))},
            )
        ],
        responses={200: UserManagementSerializer(many=True)},
    )
    def get(self, request: Request):
        offering = request.query_params.get("offering")
        if offering not in OFFERING_ROLES:
            return Response(
                {"detail": "offering query param required (platform)."}, status=status.HTTP_400_BAD_REQUEST
            )
        if not self._check_admin(request, offering):
            return Response({"detail": "Admin role required for this offering."}, status=status.HTTP_403_FORBIDDEN)

        org = get_request_organization(request)
        admin_role, user_role = OFFERING_ROLES[offering]
        users = (
            User.objects.filter(Q(profile__roles__name__in=[admin_role, user_role]) | Q(is_superuser=True))
            .distinct()
            .order_by("email")
        )
        if org is not None:
            users = users.filter(profile__organization_id=org.id)
        elif not request.user.is_superuser:
            users = users.none()

        serializer = UserManagementSerializer(users, many=True)
        return Response(serializer.data)

    @extend_schema(
        summary="Create a new user for an offering",
        request=UserCreateSerializer,
        responses={201: UserManagementSerializer},
    )
    def post(self, request: Request):
        offering = request.data.get("offering")
        if offering not in OFFERING_ROLES:
            return Response(
                {"detail": "offering field required (platform)."}, status=status.HTTP_400_BAD_REQUEST
            )
        if not self._check_admin(request, offering):
            return Response({"detail": "Admin role required for this offering."}, status=status.HTTP_403_FORBIDDEN)

        serializer = UserCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = serializer.save()
        org = get_request_organization(request)
        if org is not None:
            profile = UserProfile.objects.get(user=user)
            profile.organization = org
            profile.save(update_fields=["organization", "updated_at"])
        logger.info("User created by %s: %s", request.user.email, user.email)
        return Response(UserManagementSerializer(user).data, status=status.HTTP_201_CREATED)


@extend_schema(tags=["User Management"])
class UserManagementDetailView(APIView):
    """
    GET    /api/auth/users/{id}/?offering= — user detail
    PATCH  /api/auth/users/{id}/           — update role/status (admin only)
    DELETE /api/auth/users/{id}/           — delete user (admin only)
    """

    permission_classes = [IsAuthenticated]

    def _get_user_or_404(self, pk):
        try:
            return User.objects.get(pk=pk)
        except User.DoesNotExist:
            return None

    def _check_admin(self, request, offering):
        if request.user.is_superuser:
            return True
        return _get_offering_role(request.user, offering) == "admin"

    @extend_schema(summary="Get user detail", responses={200: UserManagementSerializer})
    def get(self, request: Request, pk: int):
        offering = request.query_params.get("offering")
        if offering not in OFFERING_ROLES:
            return Response({"detail": "offering query param required."}, status=status.HTTP_400_BAD_REQUEST)
        if not self._check_admin(request, offering):
            return Response({"detail": "Admin role required."}, status=status.HTTP_403_FORBIDDEN)
        user = self._get_user_or_404(pk)
        if not user:
            return Response({"detail": "User not found."}, status=status.HTTP_404_NOT_FOUND)
        org = get_request_organization(request)
        if org is not None and getattr(user.profile, "organization_id", None) != org.id:
            return Response({"detail": "User not found."}, status=status.HTTP_404_NOT_FOUND)
        return Response(UserManagementSerializer(user).data)

    @extend_schema(
        summary="Update user role or status", request=UserUpdateSerializer, responses={200: UserManagementSerializer}
    )
    def patch(self, request: Request, pk: int):
        offering = request.data.get("offering") or request.query_params.get("offering")
        if not offering or offering not in OFFERING_ROLES:
            return Response(
                {"detail": "offering field required (platform)."}, status=status.HTTP_400_BAD_REQUEST
            )
        if not self._check_admin(request, offering):
            return Response({"detail": "Admin role required."}, status=status.HTTP_403_FORBIDDEN)
        user = self._get_user_or_404(pk)
        if not user:
            return Response({"detail": "User not found."}, status=status.HTTP_404_NOT_FOUND)
        org = get_request_organization(request)
        if org is not None and getattr(user.profile, "organization_id", None) != org.id:
            return Response({"detail": "User not found."}, status=status.HTTP_404_NOT_FOUND)
        serializer = UserUpdateSerializer(user, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        updated = serializer.save()
        logger.info("User %s updated by %s", user.email, request.user.email)
        return Response(UserManagementSerializer(updated).data)

    @extend_schema(summary="Delete a user", responses={204: None})
    def delete(self, request: Request, pk: int):
        offering = request.query_params.get("offering") or request.data.get("offering")
        if not offering or offering not in OFFERING_ROLES:
            return Response({"detail": "offering query param required."}, status=status.HTTP_400_BAD_REQUEST)
        if not self._check_admin(request, offering):
            return Response({"detail": "Admin role required."}, status=status.HTTP_403_FORBIDDEN)
        user = self._get_user_or_404(pk)
        if not user:
            return Response({"detail": "User not found."}, status=status.HTTP_404_NOT_FOUND)
        org = get_request_organization(request)
        if org is not None and getattr(user.profile, "organization_id", None) != org.id:
            return Response({"detail": "User not found."}, status=status.HTTP_404_NOT_FOUND)
        if user == request.user:
            return Response({"detail": "You cannot delete your own account."}, status=status.HTTP_400_BAD_REQUEST)
        logger.info("User %s deleted by %s", user.email, request.user.email)
        user.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)
