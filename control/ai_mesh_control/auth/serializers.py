from django.contrib.auth import get_user_model
from django.utils import timezone
from rest_framework import serializers
from rest_framework_simplejwt.serializers import TokenObtainPairSerializer

from .models import Role, UserProfile

User = get_user_model()

OFFERING_ROLES = {
    "platform": ("platform_admin", "platform_user"),
    "aiguardx": ("aiguardx_admin", "aiguardx_user"),
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
        if not user or not user.check_password(password):
            raise serializers.ValidationError("Invalid email or password.")
        if not user.is_active:
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

    class Meta:
        model = User
        fields = ("id", "email", "first_name", "last_name", "is_active", "is_superuser", "roles", "organization")

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
            return ["admin", "user", "platform_admin", "platform_user", "aiguardx_admin", "aiguardx_user"]
        if obj.is_staff:
            return ["staff", "user"]
        try:
            profile_roles = list(obj.profile.roles.values_list("name", flat=True))
            if profile_roles:
                return profile_roles
        except Exception:
            pass
        return ["user"]


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
    aiguardx_role = serializers.SerializerMethodField()
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
            "aiguardx_role",
        )

    def get_platform_role(self, obj):
        return _get_offering_role(obj, "platform")

    def get_aiguardx_role(self, obj):
        return _get_offering_role(obj, "aiguardx")


class UserCreateSerializer(serializers.Serializer):
    """Write serializer for creating a new user via user management."""

    first_name = serializers.CharField(max_length=150)
    last_name = serializers.CharField(max_length=150, required=False, default="")
    email = serializers.EmailField()
    password = serializers.CharField(write_only=True, min_length=8)
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
