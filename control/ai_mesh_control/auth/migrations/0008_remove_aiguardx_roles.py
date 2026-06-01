from django.db import migrations


def migrate_aiguardx_roles(apps, schema_editor):
    Role = apps.get_model("auth_api", "Role")
    UserProfile = apps.get_model("auth_api", "UserProfile")

    platform_admin, _ = Role.objects.get_or_create(
        name="platform_admin",
        defaults={"description": "Administrator for AI Mesh Firewall"},
    )
    platform_user, _ = Role.objects.get_or_create(
        name="platform_user",
        defaults={"description": "User for AI Mesh Firewall"},
    )

    aiguardx_admin = Role.objects.filter(name="aiguardx_admin").first()
    aiguardx_user = Role.objects.filter(name="aiguardx_user").first()
    if not aiguardx_admin and not aiguardx_user:
        return

    for profile in UserProfile.objects.prefetch_related("roles").all():
        role_names = set(profile.roles.values_list("name", flat=True))
        if "aiguardx_admin" in role_names:
            profile.roles.add(platform_admin)
        elif "aiguardx_user" in role_names:
            profile.roles.add(platform_user)
        profile.roles.remove(*Role.objects.filter(name__in=["aiguardx_admin", "aiguardx_user"]))

    Role.objects.filter(name__in=["aiguardx_admin", "aiguardx_user"]).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("auth_api", "0007_userprofile_preferences"),
    ]

    operations = [
        migrations.RunPython(migrate_aiguardx_roles, reverse_code=migrations.RunPython.noop),
    ]
