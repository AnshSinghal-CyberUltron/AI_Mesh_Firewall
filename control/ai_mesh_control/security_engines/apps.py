"""AppConfig for security_engines: warm up ML models at Django startup."""

import logging
import threading

from django.apps import AppConfig

logger = logging.getLogger(__name__)


class SecurityEnginesConfig(AppConfig):
    name = "security_engines"
    default_auto_field = "django.db.models.BigAutoField"

    def ready(self):
        def _warmup():
            try:
                from policy.evaluation_views import _get_security_scanner

                logger.info("SecurityEnginesConfig: warming up IntegratedSecurityScanner in background thread...")
                scanner = _get_security_scanner()
                # Trigger actual ML model load
                scanner.scan_prompt("warmup_prompt_for_ml_models")
                logger.info("SecurityEnginesConfig: scanner warmup complete.")
            except Exception as exc:
                logger.warning("SecurityEnginesConfig: scanner warmup failed: %s", exc)

        threading.Thread(target=_warmup, daemon=True).start()
