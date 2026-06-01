from django.contrib.auth import get_user_model
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from auth.models import UserProfile


User = get_user_model()


class MeProfileUpdateApiTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="profileuser",
            email="profile@example.com",
            password="Password123!",
            first_name="Profile",
            last_name="User",
        )
        UserProfile.objects.get_or_create(user=self.user)
        self.url = reverse("me_profile_update")

    def test_patch_updates_name_and_preferences(self):
        self.client.force_authenticate(user=self.user)
        response = self.client.patch(
            self.url,
            {
                "first_name": "Updated",
                "last_name": "Name",
                "preferences": {
                    "theme": "dark",
                    "email_notifications": False,
                    "security_alerts": True,
                },
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.user.refresh_from_db()
        profile = self.user.profile
        self.assertEqual(self.user.first_name, "Updated")
        self.assertEqual(self.user.last_name, "Name")
        self.assertEqual(profile.preferences["theme"], "dark")
        self.assertFalse(profile.preferences["email_notifications"])

    def test_patch_email_requires_current_password(self):
        self.client.force_authenticate(user=self.user)
        response = self.client.patch(
            self.url,
            {"email": "newemail@example.com"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("current_password", response.data)

    def test_patch_email_rejects_incorrect_current_password(self):
        self.client.force_authenticate(user=self.user)
        response = self.client.patch(
            self.url,
            {"email": "newemail@example.com", "current_password": "WrongPassword123!"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("current_password", response.data)

    def test_patch_email_succeeds_with_current_password(self):
        self.client.force_authenticate(user=self.user)
        response = self.client.patch(
            self.url,
            {"email": "newemail@example.com", "current_password": "Password123!"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.user.refresh_from_db()
        self.assertEqual(self.user.email, "newemail@example.com")
