"""FROZEN copy of gateway v1's request token estimate and its pre-estimate clamp (oracle).

Spec ``v2-enforcement-leaks`` Group F (task 9; design Property 12, Property 16, Glossary "Token
estimate" and "Output budget clamp"). gateway_v2 tests never import the v1 package, so this is a
pure, hand-made copy of the v1 behaviour the fixed v2 budget phase must reproduce. It is FROZEN:
it must not change when v2 changes. Only a deliberate re-read of v1 may edit it.

v1 sources copied (``gateway/ai_mesh_gateway``):

* :func:`estimate_prompt_tokens` -- ``llm_router.py`` L2111-2127,
  ``LLMRouter.estimate_prompt_tokens(messages, max_tokens=0, n=1)``. List content parts are joined
  with ``" "``; input tokens are ``max(1, prompt_chars // 4)`` when there are characters and 0
  otherwise; the requested output budget ``max_tokens * n`` is added; the total is at least 1.
* :func:`v1_clamp`, in v1's request order:

  1. ``main.py`` L7215-7284, the ``max_tokens`` boundary validator. Explicit JSON ``null`` is
     treated identically to absent (the key is dropped). A non-integral float, an un-coercible
     value or a negative value is refused ``invalid_max_tokens``. ``0`` is kept as the scan-only
     sentinel. A value above ``MAX_OUTPUT_TOKENS_CEILING`` (``main.py`` L3399, ``1_000_000``) is
     refused HTTP 400 ``invalid_max_tokens`` with ``param`` ``max_tokens``. Anything else is
     normalised to ``int``.
  2. ``main.py`` L7593-7615, the ``n`` clamp. A present ``n`` is coerced with ``int()`` (an
     un-coercible value becomes 1); any value other than 1 is recorded ``"n=<n>->1"`` (I-20) and
     ``n`` is forced to 1, because v1's output guard is single-choice.
  3. ``main.py`` L8457-8468, the pre-estimate clamp. When ``max_tokens`` is an ``int > 0`` it
     becomes ``min(max_tokens, org max_response_tokens (default 4096), 65536)``, recorded
     ``"max_tokens=<pre>-><post>"`` (I-20) when it changes.

* :func:`v1_estimate` -- :func:`estimate_prompt_tokens` over the clamped body, which is the
  estimate the design names: v1's ``estimate_prompt_tokens`` applied after v1's clamp.

``max_completion_tokens`` is deliberately not modelled: v1 charges only ``max_tokens`` (design
"Only ``max_tokens`` is charged").
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

__all__ = (
    "DEFAULT_MAX_RESPONSE_TOKENS",
    "MAX_OUTPUT_TOKENS_CEILING",
    "MT_HARD_CAP",
    "V1ClampResult",
    "V1InvalidMaxTokens",
    "estimate_prompt_tokens",
    "v1_clamp",
    "v1_estimate",
)

#: ``main.py`` L3399 ``MAX_OUTPUT_TOKENS_CEILING`` -- the boundary refusal for ``max_tokens``.
MAX_OUTPUT_TOKENS_CEILING = 1_000_000
#: ``main.py`` L8459 ``org_config.get("max_response_tokens", 4096) or 4096``.
DEFAULT_MAX_RESPONSE_TOKENS = 4096
#: ``main.py`` L8460 ``_mt_hard_cap = 65536``.
MT_HARD_CAP = 65536


def estimate_prompt_tokens(messages: Sequence[object], max_tokens: int = 0, n: object = 1) -> int:
    """``LLMRouter.estimate_prompt_tokens`` (``llm_router.py`` L2111-2127), copied exactly."""
    prompt_chars = 0
    for message in messages or []:
        content = message.get("content", "") if isinstance(message, dict) else ""
        if isinstance(content, list):
            content = " ".join(
                str(part.get("text") or "") for part in content if isinstance(part, dict)
            )
        prompt_chars += len(str(content or ""))
    prompt_tokens = max(1, prompt_chars // 4) if prompt_chars else 0
    try:
        _n: int = max(1, int(n or 1))  # type: ignore[call-overload]
    except (TypeError, ValueError):
        _n = 1
    return max(prompt_tokens + max(max_tokens or 0, 0) * _n, 1)


class V1InvalidMaxTokens(Exception):
    """v1's boundary refusal: HTTP 400 ``invalid_max_tokens`` with ``param`` ``max_tokens``."""

    status = 400
    code = "invalid_max_tokens"
    param = "max_tokens"


@dataclass(frozen=True, slots=True)
class V1ClampResult:
    """The body after v1's boundary validator, ``n`` clamp and pre-estimate clamp."""

    body: Mapping[str, object]
    request_clamps: tuple[str, ...]


def _validate_max_tokens(body: dict[str, object]) -> None:
    """``main.py`` L7215-7284: null is absent; refuse off-contract and ``> 1_000_000`` values."""
    _max_tokens_val = body.get("max_tokens")
    if _max_tokens_val is None:
        body.pop("max_tokens", None)
        return
    if isinstance(_max_tokens_val, float) and not _max_tokens_val.is_integer():
        raise V1InvalidMaxTokens("'max_tokens' must be an integer.")
    try:
        _max_tokens_int = int(_max_tokens_val)  # type: ignore[call-overload]
    except (TypeError, ValueError, OverflowError) as exc:
        raise V1InvalidMaxTokens("'max_tokens' must be an integer.") from exc
    if _max_tokens_int < 0:
        raise V1InvalidMaxTokens("'max_tokens' must be a positive integer.")
    if _max_tokens_int == 0:
        body["max_tokens"] = 0
    elif _max_tokens_int > MAX_OUTPUT_TOKENS_CEILING:
        raise V1InvalidMaxTokens(f"'max_tokens' must not exceed {MAX_OUTPUT_TOKENS_CEILING}.")
    else:
        body["max_tokens"] = _max_tokens_int


def _clamp_n(body: dict[str, object], request_clamps: list[str]) -> None:
    """``main.py`` L7593-7615: ``n`` is forced to 1 (single-choice output guard)."""
    _n_val = body.get("n")
    if _n_val is not None:
        try:
            _n_int = int(_n_val)  # type: ignore[call-overload]
        except (TypeError, ValueError, OverflowError):
            _n_int = 1
        if _n_int != 1:
            request_clamps.append(f"n={_n_int}->1")
        body["n"] = 1 if _n_int != 1 else _n_int


def _clamp_max_tokens(
    body: dict[str, object], request_clamps: list[str], max_response_tokens: int | None
) -> None:
    """``main.py`` L8457-8468: ``min(mt, org max_response_tokens or 4096, 65536)`` when ``> 0``."""
    _mt_pre = body.get("max_tokens")
    if isinstance(_mt_pre, int) and _mt_pre > 0:
        _mt_ceiling = int(max_response_tokens or DEFAULT_MAX_RESPONSE_TOKENS)
        _mt_hard_cap = MT_HARD_CAP
        _mt_clamped = min(_mt_pre, _mt_ceiling, _mt_hard_cap)
        if _mt_clamped != _mt_pre:
            body["max_tokens"] = _mt_clamped
            request_clamps.append(f"max_tokens={_mt_pre}->{_mt_clamped}")


def v1_clamp(
    body: Mapping[str, object], *, max_response_tokens: int | None = None
) -> V1ClampResult:
    """Apply v1's boundary validator, ``n`` clamp and pre-estimate clamp, in v1's order.

    ``max_response_tokens`` is the org's ``org_config["max_response_tokens"]``; ``None`` (unset)
    gives v1's 4096 default. Raises :class:`V1InvalidMaxTokens` for a refused ``max_tokens``.
    """
    working = dict(body)
    clamps: list[str] = []
    _validate_max_tokens(working)
    _clamp_n(working, clamps)
    _clamp_max_tokens(working, clamps, max_response_tokens)
    return V1ClampResult(body=working, request_clamps=tuple(clamps))


def v1_estimate(body: Mapping[str, object], *, max_response_tokens: int | None = None) -> int:
    """v1's ``estimate_prompt_tokens`` over the clamped body (the Group F charged cost)."""
    clamped = v1_clamp(body, max_response_tokens=max_response_tokens).body
    messages = clamped.get("messages") or []
    if not isinstance(messages, list):
        messages = []
    max_tokens = clamped.get("max_tokens") or 0
    if not isinstance(max_tokens, int):
        max_tokens = 0
    return estimate_prompt_tokens(messages, max_tokens, clamped.get("n", 1))
