"""Authentication for the Prometheus /metrics scrape endpoint."""
from __future__ import annotations

import os
import secrets


def metrics_scraper_configured() -> bool:
    return bool(os.environ.get("METRICS_SCRAPER_KEY", "").strip())


def verify_metrics_scraper(headers: dict[str, str]) -> bool:
    """
    Validate scraper credentials.

    Accepted:
      - Header ``X-Metrics-Scraper-Key: <secret>``
      - Header ``Authorization: Bearer <secret>``
    """
    expected = os.environ.get("METRICS_SCRAPER_KEY", "").strip()
    if not expected:
        allow_open = os.environ.get("METRICS_ALLOW_OPEN", "").strip().lower() in {
            "1",
            "true",
            "yes",
        }
        return allow_open

    supplied = (headers.get("x-metrics-scraper-key") or "").strip()
    if not supplied:
        auth = (headers.get("authorization") or "").strip()
        if auth.lower().startswith("bearer "):
            supplied = auth[7:].strip()

    if not supplied or not secrets.compare_digest(supplied, expected):
        return False
    return True
