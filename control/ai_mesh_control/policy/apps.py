import logging
import sys
import time

from django.apps import AppConfig

logger = logging.getLogger(__name__)


def _run_with_db_lock_retry(fn, label: str, attempts: int = 5, base_sleep_s: float = 1.0):
    from django.db.utils import OperationalError

    for attempt in range(1, attempts + 1):
        try:
            return fn()
        except OperationalError as exc:
            message = str(exc).lower()
            if "locked" not in message or attempt >= attempts:
                raise
            wait_s = base_sleep_s * attempt
            logger.warning(
                "%s skipped on transient DB lock (attempt %d/%d); retrying in %.1fs",
                label,
                attempt,
                attempts,
                wait_s,
            )
            time.sleep(wait_s)


class PolicyConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "policy"
    verbose_name = "Policy Engine"

    def ready(self) -> None:
        import policy.compiler_signals
        import policy.vector_provider_signals  # noqa: F401
        import policy.vector_signals  # noqa: F401

        if not self._is_management_or_test():
            self._auto_compile_policies()

    @staticmethod
    def _is_management_or_test() -> bool:
        if "test" in sys.argv or "pytest" in sys.modules:
            return True
        if "manage.py" in sys.argv:
            command = ""
            for arg in sys.argv[1:]:
                if arg and not arg.startswith("-"):
                    command = arg
                    break
            # Only run startup background jobs for long-lived Django server process.
            return command != "runserver"
        return False

    @staticmethod
    def _auto_compile_policies() -> None:
        import threading

        def _run() -> None:
            try:
                from policy.compiler import PolicyCompiler

                compiler = PolicyCompiler()
                _run_with_db_lock_retry(
                    lambda: compiler.compile_and_push(trigger="startup"),
                    "Policy compile",
                )
                logger.info("Startup policy compilation complete.")
            except Exception:
                logger.warning(
                    "Startup policy compilation failed (Redis may be unavailable).",
                    exc_info=True,
                )

            try:
                from policy.vector_compiler import VectorPolicyCompiler

                compiler = VectorPolicyCompiler()
                _run_with_db_lock_retry(
                    lambda: compiler.compile_and_push(trigger="startup"),
                    "Vector policy compile",
                )
                logger.info("Startup vector policy compilation complete.")
            except Exception:
                logger.warning(
                    "Startup vector policy compilation failed (Redis may be unavailable).",
                    exc_info=True,
                )

        timer = threading.Timer(3.0, _run)
        timer.daemon = True
        timer.start()
        logger.info("Scheduled policy + vector compile (3s delay)")
