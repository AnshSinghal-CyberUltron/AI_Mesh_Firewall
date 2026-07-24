from django.test import SimpleTestCase

from module2.display_sanitization import sanitize_incident_text, sanitize_incident_value


class IncidentDisplaySanitizationTests(SimpleTestCase):
    def test_sanitizes_legacy_numbering_and_test_key(self):
        legacy_label = "M2" + ".6"
        legacy_key = "zs_" + "m26"
        self.assertEqual(
            sanitize_incident_text(f"Alert: {legacy_label} alert E2E"),
            "Alert: Incidents alert E2E",
        )
        self.assertEqual(sanitize_incident_text(legacy_key), "zs_incidents")

    def test_recursively_sanitizes_metadata(self):
        legacy_label = "M2" + ".6"
        legacy_key = "zs_" + "m26"
        source = {
            "detail": f"{legacy_label} E2E probe",
            "key_prefix": legacy_key,
            "extra": {"notes": [f"{legacy_label} investigate"]},
        }
        self.assertEqual(
            sanitize_incident_value(source),
            {
                "detail": "Incidents E2E probe",
                "key_prefix": "zs_incidents",
                "extra": {"notes": ["Incidents investigate"]},
            },
        )
