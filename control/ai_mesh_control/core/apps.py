import logging
import os
import sys
import time

from django.apps import AppConfig

logger = logging.getLogger(__name__)


def _run_with_db_lock_retry(fn, label: str, attempts: int = 5, base_sleep_s: float = 1.0):
    """Retry transient SQLite lock errors for startup jobs to avoid noisy failures."""
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


class CoreConfig(AppConfig):
    name = "core"

    def ready(self) -> None:
        import core.signals  # noqa: F401 - register signal handlers

        if not self._is_management_or_test():
            self._start_telemetry_drain()
            self._seed_simulator_default_key()

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
    def _seed_simulator_default_key() -> None:
        import threading

        def _run() -> None:
            time.sleep(3.0)
            try:
                _run_with_db_lock_retry(
                    lambda: __import__(
                        "core.simulator_seed", fromlist=["ensure_simulator_default_gateway_key"]
                    ).ensure_simulator_dev_bootstrap(),
                    "Simulator dev bootstrap",
                    attempts=8,
                    base_sleep_s=1.5,
                )
            except Exception:
                logger.warning("Simulator default gateway key seed failed", exc_info=True)

        threading.Thread(
            target=_run, daemon=True, name="simulator-key-seed"
        ).start()

    @staticmethod
    def _start_telemetry_drain() -> None:
        import threading
        from django.conf import settings

        drain_mode = str(getattr(settings, "TELEMETRY_DRAIN_MODE", "beat")).strip().lower()
        if drain_mode != "thread":
            logger.info("Telemetry drain thread disabled (TELEMETRY_DRAIN_MODE=%s)", drain_mode)
            return

        def _drain_loop() -> None:
            from core.tasks import drain_telemetry_from_redis

            # Let migrations / other startup work settle before periodic writes begin.
            time.sleep(20.0)
            batch_size = int(getattr(settings, "TELEMETRY_DRAIN_BATCH_SIZE", 250))
            interval_seconds = float(getattr(settings, "TELEMETRY_DRAIN_INTERVAL_SEC", 1.0))
            while True:
                try:
                    count = drain_telemetry_from_redis(batch_size=batch_size)
                    if count > 0:
                        logger.debug("Telemetry drain: processed %d events", count)
                except Exception:
                    logger.warning("Telemetry drain iteration failed", exc_info=True)
                time.sleep(interval_seconds)

        thread = threading.Thread(target=_drain_loop, daemon=True, name="telemetry-drain")
        thread.start()
        logger.info("Telemetry drain thread started (interval=%ss)", getattr(settings, "TELEMETRY_DRAIN_INTERVAL_SEC", 1.0))
