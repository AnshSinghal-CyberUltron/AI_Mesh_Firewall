from django.contrib.auth import get_user_model
from django.contrib.auth.hashers import check_password, make_password
from django.utils import timezone
from rest_framework import serializers
from rest_framework_simplejwt.serializers import TokenObtainPairSerializer

from .models import Role, TerminatedSession, UserProfile

User = get_user_model()

# Pre-computed dummy hash used to equalise login timing for non-existent emails
# (computed once at import). Verifying against it costs the same as a real
# user's check_password, so an attacker cannot enumerate valid emails by timing.
_DUMMY_PASSWORD_HASH = make_password("zs-constant-time-dummy-password")

OFFERING_ROLES = {
    "platform": ("platform_admin", "platform_user"),
}


class CustomTokenObtainPairSerializer(TokenObtainPairSerializer):
    """Accept email + password and return JWT tokens."""

    email = serializers.EmailField(required=True)
    password = serializers.CharField(required=True, write_only=True)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields.pop("username", None)

    def validate(self, attrs):
        email = attrs.get("email")
        password = attrs.get("password")
        user = User.objects.filter(email__iexact=email).first()
        if user is None:
            # Run a dummy verification so a non-existent email takes the same
            # time as an existing one (prevents timing-based email enumeration).
            check_password(password, _DUMMY_PASSWORD_HASH)
            raise serializers.ValidationError("Invalid email or password.")
        if not user.check_password(password):
            raise serializers.ValidationError("Invalid email or password.")
        if not user.is_active:
            raise serializers.ValidationError("User account is disabled.")
        # Reject login outright for an actively-terminated (uncleared) user so we
        # never mint tokens for an identity that every subsequent request would
        # reject anyway (JWTAuthenticationWithTermination). Cleared/reinstated
        # terminations keep their audit row but no longer block login.
        if TerminatedSession.objects.filter(user=user, cleared_at__isnull=True).exists():
            raise serializers.ValidationError("User account is disabled.")
        # Update last_login so User Management Last Login column is correct
        user.last_login = timezone.now()
        user.save(update_fields=["last_login"])
        # Build token payload using username for SimpleJWT; expose user for logging in view
        attrs["username"] = user.username
        attrs["user"] = user
        return super().validate(attrs)


class UserMeSerializer(serializers.ModelSerializer):
    """Current user for GET /api/auth/me/."""

    roles = serializers.SerializerMethodField()
    is_superuser = serializers.BooleanField(read_only=True)
    organization = serializers.SerializerMethodField()
    preferences = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = (
            "id",
            "email",
            "first_name",
            "last_name",
            "is_active",
            "is_superuser",
            "roles",
            "organization",
            "preferences",
        )

    def get_organization(self, obj):
        try:
            org = obj.profile.organization
            if org is None:
                return None
            return {"id": org.id, "name": org.name, "slug": org.slug}
        except Exception:
            return None

    def get_roles(self, obj):
        if obj.is_superuser:
            return ["admin", "user", "platform_admin", "platform_user"]
        if obj.is_staff:
            return ["staff", "user"]
        try:
            profile_roles = list(obj.profile.roles.values_list("name", flat=True))
            if profile_roles:
                return profile_roles
        except Exception:
            pass
        return ["user"]

    def get_preferences(self, obj):
        default_preferences = {
            "theme": "system",
            "email_notifications": True,
            "security_alerts": True,
        }
        try:
            prefs = getattr(obj.profile, "preferences", {}) or {}
            if not isinstance(prefs, dict):
                return default_preferences
            return {
                "theme": prefs.get("theme", "system"),
                "email_notifications": bool(prefs.get("email_notifications", True)),
                "security_alerts": bool(prefs.get("security_alerts", True)),
            }
        except Exception:
            return default_preferences


class UserSelfProfileUpdateSerializer(serializers.Serializer):
    """Write serializer for authenticated user self-service profile updates."""

    first_name = serializers.CharField(max_length=150, required=False, allow_blank=False)
    last_name = serializers.CharField(max_length=150, required=False, allow_blank=True)
    email = serializers.EmailField(required=False)
    current_password = serializers.CharField(required=False, write_only=True, trim_whitespace=False)
    preferences = serializers.DictField(required=False)

    def validate_email(self, value):
        request = self.context.get("request")
        user = getattr(request, "user", None)
        normalized = value.lower()
        if user and User.objects.filter(email__iexact=normalized).exclude(pk=user.pk).exists():
            raise serializers.ValidationError("A user with this email already exists.")
        return normalized

    def validate_preferences(self, value):
        if not isinstance(value, dict):
            raise serializers.ValidationError("Preferences must be an object.")
        theme = value.get("theme", "system")
        if theme not in {"light", "dark", "system"}:
            raise serializers.ValidationError("theme must be one of: light, dark, system.")
        for bool_key in ("email_notifications", "security_alerts"):
            if bool_key in value and not isinstance(value[bool_key], bool):
                raise serializers.ValidationError(f"{bool_key} must be a boolean.")
        return value

    def validate(self, attrs):
        request = self.context.get("request")
        user = getattr(request, "user", None)
        if "email" in attrs:
            current_password = attrs.get("current_password")
            if not current_password:
                raise serializers.ValidationError({"current_password": "Current password is required to change email."})
            if not user or not user.check_password(current_password):
                raise serializers.ValidationError({"current_password": "Current password is incorrect."})
        if not attrs:
            raise serializers.ValidationError("At least one field must be provided.")
        return attrs

    def update(self, instance, validated_data):
        first_name = validated_data.get("first_name")
        last_name = validated_data.get("last_name")
        email = validated_data.get("email")
        preferences = validated_data.get("preferences")

        updated_user_fields = []
        if first_name is not None:
            instance.first_name = first_name
            updated_user_fields.append("first_name")
        if last_name is not None:
            instance.last_name = last_name
            updated_user_fields.append("last_name")
        if email is not None:
            instance.email = email
            updated_user_fields.append("email")
        if updated_user_fields:
            instance.save(update_fields=updated_user_fields)

        if preferences is not None:
            profile, _ = UserProfile.objects.get_or_create(user=instance)
            existing_preferences = profile.preferences if isinstance(profile.preferences, dict) else {}
            profile.preferences = {
                "theme": preferences.get("theme", existing_preferences.get("theme", "system")),
                "email_notifications": preferences.get(
                    "email_notifications", existing_preferences.get("email_notifications", True)
                ),
                "security_alerts": preferences.get("security_alerts", existing_preferences.get("security_alerts", True)),
            }
            profile.save(update_fields=["preferences", "updated_at"])

        return instance


def _get_offering_role(user, offering):
    """Return 'admin', 'user', or None for the given offering."""
    if user.is_superuser:
        return "admin"
    try:
        role_names = set(user.profile.roles.values_list("name", flat=True))
    except Exception:
        role_names = set()
    admin_role, user_role = OFFERING_ROLES.get(offering, (None, None))
    if admin_role and admin_role in role_names:
        return "admin"
    if user_role and user_role in role_names:
        return "user"
    return None


class UserManagementSerializer(serializers.ModelSerializer):
    """Read serializer for user management lists and detail."""

    platform_role = serializers.SerializerMethodField()
    date_joined = serializers.DateTimeField(read_only=True)
    last_login = serializers.DateTimeField(read_only=True)

    class Meta:
        model = User
        fields = (
            "id",
            "email",
            "first_name",
            "last_name",
            "is_active",
            "date_joined",
            "last_login",
            "platform_role",
        )

    def get_platform_role(self, obj):
        return _get_offering_role(obj, "platform")

class UserCreateSerializer(serializers.Serializer):
    """Write serializer for creating a new user via user management."""

    first_name = serializers.CharField(max_length=150)
    last_name = serializers.CharField(max_length=150, required=False, default="")
    email = serializers.EmailField()
    password = serializers.CharField(write_only=True, min_length=12)
    offering = serializers.ChoiceField(choices=list(OFFERING_ROLES.keys()))
    role = serializers.ChoiceField(choices=["admin", "user"])

    def validate_email(self, value):
        if User.objects.filter(email__iexact=value).exists():
            raise serializers.ValidationError("A user with this email already exists.")
        return value.lower()

    def create(self, validated_data):
        offering = validated_data.pop("offering")
        role_choice = validated_data.pop("role")
        password = validated_data.pop("password")

        email = validated_data["email"]
        username = email.split("@")[0]
        base_username = username
        counter = 1
        while User.objects.filter(username=username).exists():
            username = f"{base_username}{counter}"
            counter += 1

        user = User.objects.create_user(
            username=username,
            email=email,
            password=password,
            first_name=validated_data.get("first_name", ""),
            last_name=validated_data.get("last_name", ""),
        )

        admin_role_name, user_role_name = OFFERING_ROLES[offering]
        role_name = admin_role_name if role_choice == "admin" else user_role_name
        role_obj, _ = Role.objects.get_or_create(name=role_name)
        profile, _ = UserProfile.objects.get_or_create(user=user)
        profile.roles.add(role_obj)

        return user


class UserUpdateSerializer(serializers.Serializer):
    """Write serializer for updating a user's role or active status."""

    offering = serializers.ChoiceField(choices=list(OFFERING_ROLES.keys()), required=False)
    role = serializers.ChoiceField(choices=["admin", "user"], required=False)
    is_active = serializers.BooleanField(required=False)
    first_name = serializers.CharField(max_length=150, required=False)
    last_name = serializers.CharField(max_length=150, required=False)

    def update(self, instance, validated_data):
        offering = validated_data.get("offering")
        role_choice = validated_data.get("role")

        if offering and role_choice:
            admin_role_name, user_role_name = OFFERING_ROLES[offering]
            both = {admin_role_name, user_role_name}
            profile, _ = UserProfile.objects.get_or_create(user=instance)
            profile.roles.remove(*Role.objects.filter(name__in=both))
            role_name = admin_role_name if role_choice == "admin" else user_role_name
            role_obj, _ = Role.objects.get_or_create(name=role_name)
            profile.roles.add(role_obj)

        if "is_active" in validated_data:
            instance.is_active = validated_data["is_active"]
        if "first_name" in validated_data:
            instance.first_name = validated_data["first_name"]
        if "last_name" in validated_data:
            instance.last_name = validated_data["last_name"]
        instance.save()
        return instance
