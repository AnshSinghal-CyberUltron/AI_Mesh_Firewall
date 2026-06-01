from django.urls import path
from drf_spectacular.utils import extend_schema, extend_schema_view
from rest_framework_simplejwt.views import TokenRefreshView, TokenVerifyView

from . import views
from .views import UserManagementDetailView, UserManagementListCreateView, ChangePasswordView

DecoratedTokenRefreshView = extend_schema_view(
    post=extend_schema(
        tags=["Auth"],
        summary="Refresh JWT access token",
        description=(
            "Submit a valid refresh token to receive a new access token.\n\n"
            "The refresh token itself is not rotated unless `ROTATE_REFRESH_TOKENS` "
            "is enabled in the backend settings."
        ),
    )
)(TokenRefreshView)

DecoratedTokenVerifyView = extend_schema_view(
    post=extend_schema(
        tags=["Auth"],
        summary="Verify JWT token validity",
        description=(
            "Submit a token to verify that it is valid and has not expired.\n\n"
            "Returns 200 if valid, 401 if the token is invalid or expired."
        ),
    )
)(TokenVerifyView)

urlpatterns = [
    path("token/", views.CustomTokenObtainPairView.as_view(), name="token_obtain_pair"),
    path("token/refresh/", DecoratedTokenRefreshView.as_view(), name="token_refresh"),
    path("token/verify/", DecoratedTokenVerifyView.as_view(), name="token_verify"),
    path("me/", views.MeView.as_view(), name="me"),
    path("me/profile/", views.MeProfileUpdateView.as_view(), name="me_profile_update"),
    path("logout/", views.LogoutView.as_view(), name="logout"),
    path("change-password/", ChangePasswordView.as_view(), name="change_password"),
    path("sessions/terminate/", views.SessionTerminateView.as_view(), name="session_terminate"),
    path("users/", UserManagementListCreateView.as_view(), name="user_management_list"),
    path("users/<int:pk>/", UserManagementDetailView.as_view(), name="user_management_detail"),
]
