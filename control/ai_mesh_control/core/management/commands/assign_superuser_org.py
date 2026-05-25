"""Assign a superuser to an organization, creating the org and profile if needed."""

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Assign superuser to an organization (creates org + profile if needed)"

    def add_arguments(self, parser):
        parser.add_argument("--email", type=str, default="admin@example.com")
        parser.add_argument("--org-slug", type=str, default="default")
        parser.add_argument("--org-name", type=str, default="Default Organization")

    def handle(self, *args, **options):
        from auth.models import Organization, UserProfile

        User = get_user_model()
        email = options["email"]

        user = User.objects.filter(email=email).first()
        if not user:
            user = User.objects.filter(is_superuser=True).first()
        if not user:
            self.stderr.write(f"No user with email={email} and no superuser found")
            return

        org, created = Organization.objects.get_or_create(
            slug=options["org_slug"],
            defaults={"name": options["org_name"]},
        )
        if created:
            self.stdout.write(f"Created organization '{org.slug}'")

        profile, p_created = UserProfile.objects.get_or_create(user=user)
        if profile.organization_id != org.id:
            profile.organization = org
            profile.save(update_fields=["organization"])
            self.stdout.write(
                self.style.SUCCESS(f"Assigned {user.email} to org '{org.slug}'")
            )
        else:
            self.stdout.write(f"User {user.email} already in org '{org.slug}'")
