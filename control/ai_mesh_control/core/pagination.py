"""DRF pagination with browser-reachable next/previous links."""

from __future__ import annotations

from urllib.parse import urlparse, urlunparse

from django.conf import settings
from rest_framework.pagination import PageNumberPagination


def _public_api_base() -> str | None:
    """Prefer Vite same-origin URL so pagination works through the dev proxy."""
    base = (
        getattr(settings, "BACKEND_PUBLIC_URL", None)
        or getattr(settings, "FRONTEND_ORIGIN", None)
        or ""
    ).strip().rstrip("/")
    return base or None


def _rewrite_pagination_url(url: str | None) -> str | None:
    if not url:
        return None
    public = _public_api_base()
    if not public:
        return url
    parsed = urlparse(url)
    pub = urlparse(public)
    return urlunparse(
        (
            pub.scheme or parsed.scheme,
            pub.netloc or parsed.netloc,
            parsed.path,
            parsed.params,
            parsed.query,
            parsed.fragment,
        )
    )


class PublicUrlPagination(PageNumberPagination):
    """PageNumberPagination that never emits Docker-internal hostnames in links."""

    def get_next_link(self) -> str | None:
        return _rewrite_pagination_url(super().get_next_link())

    def get_previous_link(self) -> str | None:
        return _rewrite_pagination_url(super().get_previous_link())
