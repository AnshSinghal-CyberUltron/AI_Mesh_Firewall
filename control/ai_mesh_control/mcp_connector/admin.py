from django.contrib import admin

from .models import MCPServerRegistration

# RED-TEAM L5-05: every upstream credential on MCPServerRegistration is an
# EncryptedCharField whose ``from_db_value`` DECRYPTS on read. The REST layer is careful to
# mark them ``write_only`` (serializers.py), but this ModelAdmin declared only
# list_display/list_filter/search_fields — so Django's ``ModelAdmin.get_form()`` built a
# ModelForm over ALL editable model fields and rendered the PLAINTEXT secret straight into
# the change-form widget (``value="SECRET-BEARER-XYZ"``). With no org-scoped queryset either,
# ANY ``is_staff`` user could read EVERY tenant's upstream bearer tokens, basic-auth
# passwords, custom auth headers, OAuth client secrets, refresh tokens and PKCE verifiers —
# a complete bypass of the API's write-only protection and of tenant isolation.
#
# Both halves are closed below: the secrets are EXCLUDED from the admin form entirely (they
# are managed through the API, which never reads them back), and the queryset is scoped to
# the staff user's own organization unless they are a superuser.
_SECRET_FIELDS = (
    "auth_token",
    "auth_username",
    "auth_password",
    "auth_header_value",
    "oauth_client_secret",
    "oauth_refresh_token",
    "oauth_code_verifier",
)


@admin.register(MCPServerRegistration)
class MCPServerRegistrationAdmin(admin.ModelAdmin):
    list_display = ["name", "url", "transport", "is_active", "created_at"]
    list_filter = ["transport", "is_active"]
    search_fields = ["name", "url"]
    # Never build a form field for a decrypting secret column — excluded, not merely
    # read-only: a readonly_field would still render the decrypted value as text.
    exclude = _SECRET_FIELDS

    def get_queryset(self, request):
        """Tenant isolation: a staff user sees only their own organization's servers.

        A superuser keeps the full cross-tenant view (platform operations). A staff user
        with no resolvable organization sees nothing rather than everything — fail closed.
        """
        qs = super().get_queryset(request)
        user = getattr(request, "user", None)
        if user is not None and user.is_superuser:
            return qs
        org_id = getattr(getattr(user, "profile", None), "organization_id", None)
        if org_id is None:
            return qs.none()
        return qs.filter(organization_id=org_id)
