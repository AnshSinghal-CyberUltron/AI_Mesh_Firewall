import logging

from django.contrib.auth import get_user_model
from django.db.models import Q
from django.utils import timezone
from drf_spectacular.utils import OpenApiExample, extend_schema, inline_serializer
from rest_framework import generics, serializers, status
from rest_framework.permissions import AllowAny, IsAdminUser, IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.views import TokenObtainPairView

from .models import TerminatedSession, UserProfile
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


class CustomTokenObtainPairView(TokenObtainPairView):
    """POST /api/auth/token/ — login with email + password."""

    permission_classes = [AllowAny]
    serializer_class = CustomTokenObtainPairSerializer

    @extend_schema(
        tags=["Auth"],
        summary="Login (obtain JWT tokens)",
        description=(
            "Authenticate with email and password to obtain a JWT access/refresh token pair.\n\n"
            "**To use Try It:** Replace the example email and password below with your "
            "own account credentials, then click Send.\n\n"
            "The access token should be sent in subsequent requests as "
            "`Authorization: Bearer <access_token>`.\n\n"
            "Access tokens expire after the configured lifetime (default: 5 minutes). "
            "Use the refresh token at `/api/auth/token/refresh/` to obtain a new access token.\n\n"
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
                    "expires_in": 300.0,
                },
                response_only=True,
                status_codes=["200"],
            ),
        ],
    )
    def post(self, request: Request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        try:
            serializer.is_valid(raise_exception=True)
        except Exception:
            logger.warning(
                "Login failed for email=%s from ip=%s",
                request.data.get("email", ""),
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
    """POST /api/auth/logout/ — optional server-side logout (client clears tokens)."""

    permission_classes = [IsAuthenticated]

    @extend_schema(
        tags=["Auth"],
        summary="Logout",
        description=(
            "Server-side logout. The client should also discard its tokens.\n\nReturns 204 No Content on success."
        ),
        request=None,
        responses={204: None},
    )
    def post(self, request: Request):
        # Optional: pass refresh to blacklist if rest_framework_simplejwt.token_blacklist is used
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

        if len(new_password) < 8:
            return Response({"detail": "New password must be at least 8 characters."}, status=status.HTTP_400_BAD_REQUEST)

        if new_password != confirm_password:
            return Response({"detail": "Passwords do not match."}, status=status.HTTP_400_BAD_REQUEST)

        request.user.set_password(new_password)
        request.user.save(update_fields=["password"])
        logger.info("Password changed for user_id=%s", request.user.pk)
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
