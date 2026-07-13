"""CDL WASA password complexity validators (finding #13)."""

import re

from django.core.exceptions import ValidationError
from django.utils.translation import gettext as _


class PasswordComplexityValidator:
    """Require upper, lower, digit, and special character."""

    _SPECIAL = re.compile(r"[^A-Za-z0-9]")

    def validate(self, password, user=None):
        if not password:
            return
        missing = []
        if not re.search(r"[A-Z]", password):
            missing.append("uppercase letter")
        if not re.search(r"[a-z]", password):
            missing.append("lowercase letter")
        if not re.search(r"\d", password):
            missing.append("digit")
        if not self._SPECIAL.search(password):
            missing.append("special character")
        if missing:
            raise ValidationError(
                _("Password must include: %(items)s."),
                code="password_too_weak",
                params={"items": ", ".join(missing)},
            )

    def get_help_text(self):
        return "Your password must include uppercase, lowercase, a digit, and a special character."
