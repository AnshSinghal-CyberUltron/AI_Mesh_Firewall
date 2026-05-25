"""
ASGI config for main_app project.

Routes HTTP to Django and WebSocket to Channels.
"""

import os

from django.core.asgi import get_asgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "main_app.settings")

django_application = get_asgi_application()

from channels.routing import ProtocolTypeRouter, URLRouter  # noqa: E402

from main_app import routing  # noqa: E402

application = ProtocolTypeRouter(
    {
        "http": django_application,
        "websocket": URLRouter(routing.websocket_urlpatterns),
    }
)
