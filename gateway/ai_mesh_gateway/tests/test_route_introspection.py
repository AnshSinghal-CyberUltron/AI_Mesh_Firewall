"""Route-introspection guards for the OpenAI-compat surface.

Starlette routing is FIRST-MATCH-WINS: if a path+method is registered twice,
the FIRST handler serves every request and the later one is dead code that
silently diverges. Story C2-dead-route removed the shadowed second
``@app.post("/v1/responses")``; these tests are the regression gate that keeps
exactly one handler per OpenAI-compat verb so a future edit can't reintroduce a
shadowed duplicate.
"""
from __future__ import annotations

from collections import Counter

from ai_mesh_gateway import main as gateway_main


def _route_method_pairs():
    """(path, method) for every concrete HTTP route, ignoring HEAD/OPTIONS noise."""
    pairs = []
    for route in gateway_main.app.routes:
        path = getattr(route, "path", None)
        methods = getattr(route, "methods", None)
        if path is None or not methods:
            continue
        for method in methods:
            if method in ("HEAD", "OPTIONS"):
                continue
            pairs.append((path, method))
    return pairs


def test_exactly_one_post_v1_responses():
    """The full-featured (streaming + store) handler is the only POST /v1/responses."""
    posts = [
        r
        for r in gateway_main.app.routes
        if getattr(r, "path", None) == "/v1/responses"
        and "POST" in (getattr(r, "methods", None) or set())
    ]
    assert len(posts) == 1, (
        "expected exactly one POST /v1/responses handler, found "
        f"{[getattr(r.endpoint, '__name__', r.endpoint) for r in posts]}"
    )
    # The surviving handler must be the full-featured one (streaming + store).
    assert getattr(posts[0].endpoint, "__name__", "") == "proxy_responses"


def test_no_duplicate_path_method_registrations():
    """No (path, method) is registered more than once across the whole app.

    A duplicate means the second registration is unreachable dead code under
    Starlette's first-match-wins routing.
    """
    counts = Counter(_route_method_pairs())
    duplicates = {pair: n for pair, n in counts.items() if n > 1}
    assert not duplicates, f"shadowed duplicate routes (dead code): {duplicates}"
