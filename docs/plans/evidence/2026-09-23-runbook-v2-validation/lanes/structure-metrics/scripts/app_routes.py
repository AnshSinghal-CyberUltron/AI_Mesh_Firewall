"""Import the v1 FastAPI app and count registered routes (whole application, incl. included routers)."""
import sys, json, collections
import main  # noqa
from starlette.routing import Route, WebSocketRoute, Mount
from fastapi.routing import APIRoute, APIWebSocketRoute
rs = main.app.routes
kinds = collections.Counter(type(r).__name__ for r in rs)
api = [r for r in rs if isinstance(r, APIRoute)]
by_mod = collections.Counter(getattr(r.endpoint, "__module__", "?") for r in api)
paths = {r.path for r in api}
print(json.dumps({"total_routes": len(rs), "kinds": kinds, "api_routes": len(api), "unique_api_paths": len(paths),
                  "api_routes_by_endpoint_module": by_mod,
                  "method_path_pairs": sum(len(r.methods or ()) for r in api)}, indent=1, default=str))
