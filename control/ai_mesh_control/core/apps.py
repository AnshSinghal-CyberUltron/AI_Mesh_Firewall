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
            self._start_gateway_key_resync()
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
    def _start_gateway_key_resync() -> None:
        """Reconcile active gateway API keys into Redis on boot and periodically.

        A Redis flush / container recycle drops the ``auth:apikey:{hash}`` cache
        the data plane reads on every ``/v1/*`` request, which then 401s for every
        request until each key is re-saved. Control owns the keys and is always
        running, so it runs the reconcile in a daemon thread — no workers profile
        / Celery beat required (the worker boot hook remains a secondary path for
        the workers-profile deployment).
        """
        import threading

        interval_seconds = float(os.environ.get("GATEWAY_KEY_RESYNC_INTERVAL_SEC", "300"))

        def _resync_loop() -> None:
            from core.signals import resync_all_gateway_keys

            # Boot reconcile shortly after startup so a recycle recovers fast,
            # then reconcile periodically as a safety net against Redis eviction.
            time.sleep(5.0)
            while True:
                try:
                    count = resync_all_gateway_keys()
                    logger.info(
                        "Gateway-key resync reconciled %d active keys into Redis", count
                    )
                except Exception:
                    logger.warning("Gateway-key resync iteration failed", exc_info=True)
                time.sleep(interval_seconds)

        threading.Thread(
            target=_resync_loop, daemon=True, name="gateway-key-resync"
        ).start()
        logger.info(
            "Gateway-key resync thread started (interval=%ss)", interval_seconds
        )

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
            try:
                from module2.telemetry_health import maybe_repair_stale_telemetry

                maybe_repair_stale_telemetry(force=True)
            except Exception:
                logger.warning("Startup Module 2 telemetry repair failed", exc_info=True)
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
