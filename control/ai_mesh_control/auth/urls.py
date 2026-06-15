from django.urls import path
from drf_spectacular.utils import extend_schema, extend_schema_view

from . import views
from .views import UserManagementDetailView, UserManagementListCreateView, ChangePasswordView

DecoratedTokenRefreshView = extend_schema_view(
    post=extend_schema(
        tags=["Auth"],
        summary="Refresh JWT access token",
        description=(
            "Submit a valid refresh token to receive a new access token.\n\n"
            "Refresh tokens rotate on each use and the previous one is blacklisted; "
            "rotation is concurrency-safe (only the first of N simultaneous refreshes "
            "of the same token succeeds, the rest return 401)."
        ),
    )
)(views.AtomicTokenRefreshView)

DecoratedTokenVerifyView = extend_schema_view(
    post=extend_schema(
        tags=["Auth"],
        summary="Verify JWT token validity",
        description=(
            "Submit an ACCESS token to verify it is valid, unexpired, not from a "
            "terminated session, and not issued before the user's last password "
            "change.\n\nReturns 200 if valid, 401 otherwise. Refresh tokens are "
            "rejected (use them only at /token/refresh/)."
        ),
    )
)(views.HardenedTokenVerifyView)

urlpatterns = [
    path("token/", views.CustomTokenObtainPairView.as_view(), name="token_obtain_pair"),
    path("token/refresh/", DecoratedTokenRefreshView.as_view(), name="token_refresh"),
    path("token/verify/", DecoratedTokenVerifyView.as_view(), name="token_verify"),
    path("me/", views.MeView.as_view(), name="me"),
    path("me/profile/", views.MeProfileUpdateView.as_view(), name="me_profile_update"),
    path("logout/", views.LogoutView.as_view(), name="logout"),
    path("change-password/", ChangePasswordView.as_view(), name="change_password"),
    path("sessions/terminate/", views.SessionTerminateView.as_view(), name="session_terminate"),
    path("sessions/reinstate/", views.SessionReinstateView.as_view(), name="session_reinstate"),
    path("users/", UserManagementListCreateView.as_view(), name="user_management_list"),
    path("users/<int:pk>/", UserManagementDetailView.as_view(), name="user_management_detail"),
]
