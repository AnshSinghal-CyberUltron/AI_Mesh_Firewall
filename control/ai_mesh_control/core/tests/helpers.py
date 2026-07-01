"""Shared test helpers for core API tests."""

from __future__ import annotations


def grant_platform_admin(user, org=None) -> None:
    """Attach platform_admin role so mutating admin APIs authorize the user."""
    from auth.models import Role, UserProfile

    profile, _ = UserProfile.objects.get_or_create(user=user)
    if org is not None:
        profile.organization = org
    role, _ = Role.objects.get_or_create(
        name="platform_admin",
        defaults={"description": "Administrator for Offering 1 (Dashboard + Modules 1–4)"},
    )
    profile.roles.add(role)
    profile.save()
    user.is_staff = True
    user.save(update_fields=["is_staff"])
