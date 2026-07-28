from django.contrib import admin

from policy.encrypted_fields import EncryptedCharField

from .models import MCPServerRegistration


# L5-05 (lifecycle red-team wf_c7ea99b8): the EncryptedCharField secrets DECRYPT on read,
# so a Django admin change form with no ``exclude`` rendered auth_token, auth_password,
# auth_header_value, oauth_client_secret/refresh_token/code_verifier in CLEARTEXT for EVERY
# tenant to any is_staff user. Derive the exclude list from the model itself so a NEW
# encrypted field added later is hidden automatically (a regression test enforces this).
_ENCRYPTED_SECRET_FIELDS = tuple(
    f.name for f in MCPServerRegistration._meta.get_fields()
    if isinstance(f, EncryptedCharField)
)


@admin.register(MCPServerRegistration)
class MCPServerRegistrationAdmin(admin.ModelAdmin):
    list_display = ["name", "url", "transport", "is_active", "created_at"]
    list_filter = ["transport", "is_active"]
    search_fields = ["name", "url"]
    # Never render decrypted upstream credentials in the admin (managed via the API).
    exclude = _ENCRYPTED_SECRET_FIELDS
