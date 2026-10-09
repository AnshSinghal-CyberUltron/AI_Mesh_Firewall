"""Edge-seam 503 rendering boundary test (R2-07 / R2-08 / GW19), task 15.2.

Asserts the codes-vs-render boundary at the ``admit`` -> ``edge`` seam: admission emits a frozen
:class:`~gateway_v2.admit.grant.ShedVerdict` *value* and ``edge`` renders the HTTP 503 overload
response from it (``status 503`` + ``Retry-After: ceil(retry_after_s)`` + ``x-should-retry: false``
+ echoed ``request_id``). This mirrors the ``posture.py`` code-vs-render split the design calls out
for ``SHARED_STATE_UNAVAILABLE`` etc.

There is no real ``edge`` module in this rewrite tree (``gateway_v2/gateway_v2/edge`` does not
exist), so this is the LOCAL SEAM-CONTRACT equivalent: a small pure helper local to this test
(``render_overload`` — no FastAPI/Starlette/HTTPException/JSONResponse) renders a ``ShedVerdict``
exactly the way ``edge`` will, and the test asserts the four rendered properties. If a real ``edge``
module is added that renders overload responses, prefer asserting directly against it instead.

Crucially the test also proves the other half of the boundary: ``admit`` itself constructs NO HTTP
object. A structural (AST) check asserts that neither ``grant.py`` nor ``admission.py`` imports
``fastapi`` / ``starlette`` / ``HTTPException`` / ``JSONResponse`` — admission returns only the
``ShedVerdict`` value; the 503 is an ``edge`` concern.

House idiom: seeded ``random.Random`` loop (>= 10,000 iterations, seed logged in the assertion
message), no ``hypothesis``. Test files are not under the import-linter layer contract.
"""

from __future__ import annotations

import ast
import math
import random
from dataclasses import dataclass
from pathlib import Path

import gateway_v2.admit.admission as admission_mod
import gateway_v2.admit.grant as grant_mod
from gateway_v2.admit.grant import ShedVerdict, shed_verdict
from gateway_v2.domain.posture import MIN_RETRY_AFTER_S, OVERLOAD_SHED

_ITERATIONS = 10_000

# HTTP-framework names forbidden inside the admit layer: admission returns a value, edge renders.
_FORBIDDEN_HTTP_NAMES = ("fastapi", "starlette", "HTTPException", "JSONResponse")


@dataclass(frozen=True, slots=True)
class RenderedResponse:
    """The HTTP shape ``edge`` renders from a ``ShedVerdict`` — a plain value, not a framework type.

    This stands in for whatever ``edge`` eventually returns (status + headers + body). It uses only
    the standard library so the seam contract is asserted without importing an HTTP framework into
    the test (and never into ``admit``).
    """

    status: int
    headers: dict[str, str]
    body: dict[str, str]


def render_overload(verdict: ShedVerdict) -> RenderedResponse:
    """Render a ``ShedVerdict`` the way ``edge`` will: 503 + Retry-After + x-should-retry + body.

    Pure local mirror of the ``edge`` rendering rule from the design's overload-response boundary:
    ``status 503``, ``Retry-After: ceil(retry_after_s)``, ``x-should-retry`` the lowercased boolean
    (``"false"`` on a shed, because ``verdict.should_retry`` is always ``False``), and the
    ``request_id`` echoed into the body. No HTTP framework object is constructed anywhere.
    """
    return RenderedResponse(
        status=503,
        headers={
            "Retry-After": str(math.ceil(verdict.retry_after_s)),
            "x-should-retry": "true" if verdict.should_retry else "false",
        },
        body={"code": verdict.code, "request_id": verdict.request_id},
    )


# --------------------------------------------------------------------------- #
# Seam contract: edge renders 503 from the ShedVerdict value (Req 5.1, 6.3)
# --------------------------------------------------------------------------- #


def test_edge_renders_503_from_shed_verdict() -> None:
    """Every shed verdict renders a 503 with the mandated headers + echoed request_id (seam)."""
    seed = 0x19_15
    rng = random.Random(seed)
    for i in range(_ITERATIONS):
        request_id = f"zs-{rng.getrandbits(48):012x}"
        verdict = shed_verdict(rng, request_id)
        rendered = render_overload(verdict)

        assert rendered.status == 503, (
            f"seed={seed:#x} iter={i} overload must render HTTP 503, got {rendered.status}"
        )

        retry_after = rendered.headers["Retry-After"]
        assert retry_after == str(math.ceil(verdict.retry_after_s)), (
            f"seed={seed:#x} iter={i} Retry-After must be ceil(retry_after_s)="
            f"{math.ceil(verdict.retry_after_s)}, got {retry_after!r}"
        )
        assert int(retry_after) >= 1, (
            f"seed={seed:#x} iter={i} Retry-After must be >= 1 s, got {retry_after!r}"
        )

        assert rendered.headers["x-should-retry"] == "false", (
            f"seed={seed:#x} iter={i} x-should-retry must be 'false' because "
            f"verdict.should_retry={verdict.should_retry}"
        )
        assert verdict.should_retry is False, (
            f"seed={seed:#x} iter={i} a shed verdict must carry should_retry=False"
        )

        assert rendered.body["request_id"] == request_id, (
            f"seed={seed:#x} iter={i} request_id must be echoed into the body: "
            f"{rendered.body['request_id']!r} != {request_id!r}"
        )
        assert rendered.body["code"] == OVERLOAD_SHED, (
            f"seed={seed:#x} iter={i} rendered code must be OVERLOAD_SHED, "
            f"got {rendered.body['code']!r}"
        )


def test_retry_after_ceiling_is_at_least_the_floor() -> None:
    """``ceil(retry_after_s)`` is >= ``ceil(MIN_RETRY_AFTER_S)`` for a representative verdict."""
    verdict = shed_verdict(random.Random(0x1915C), "zs-ceil")
    assert math.ceil(verdict.retry_after_s) >= math.ceil(MIN_RETRY_AFTER_S)


# --------------------------------------------------------------------------- #
# Structural proof: admit constructs NO HTTP object (grant.py / admission.py)
# --------------------------------------------------------------------------- #


def _imported_names(module_path: Path) -> set[str]:
    """Collect every imported module/name in ``module_path`` via the AST (no execution).

    Returns the union of ``import X``/``from X import Y`` module paths and the bound names, so a
    forbidden HTTP framework reaching the module is visible regardless of the import spelling.
    """
    tree = ast.parse(module_path.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                names.add(alias.name)
                names.add(alias.name.split(".", 1)[0])
                if alias.asname:
                    names.add(alias.asname)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                names.add(node.module)
                names.add(node.module.split(".", 1)[0])
            for alias in node.names:
                names.add(alias.name)
                if alias.asname:
                    names.add(alias.asname)
    return names


def test_admit_grant_imports_no_http_framework() -> None:
    """``admit/grant.py`` imports no FastAPI/Starlette/HTTPException/JSONResponse (value only)."""
    names = _imported_names(Path(grant_mod.__file__))
    offenders = sorted(names & set(_FORBIDDEN_HTTP_NAMES))
    assert not offenders, f"grant.py imports forbidden HTTP names: {offenders}"


def test_admit_admission_imports_no_http_framework() -> None:
    """``admit/admission.py`` imports no FastAPI/Starlette/HTTPException/JSONResponse."""
    names = _imported_names(Path(admission_mod.__file__))
    offenders = sorted(names & set(_FORBIDDEN_HTTP_NAMES))
    assert not offenders, f"admission.py imports forbidden HTTP names: {offenders}"


def test_shed_verdict_is_the_only_overload_surface() -> None:
    """A shed produces a plain ``ShedVerdict`` value (no framework base in its MRO)."""
    verdict = shed_verdict(random.Random(0x1915E), "zs-surface")
    mro_names = {cls.__module__.split(".", 1)[0] for cls in type(verdict).__mro__}
    assert "fastapi" not in mro_names and "starlette" not in mro_names, (
        f"ShedVerdict must be a plain value, not an HTTP object; MRO modules: {mro_names}"
    )
    assert isinstance(verdict, ShedVerdict)
