from channels.routing import URLRouter
from django.urls import path

from ws.routing import websocket_urlpatterns as ws_patterns

websocket_urlpatterns = [
    path("ws/", URLRouter(ws_patterns)),
]
