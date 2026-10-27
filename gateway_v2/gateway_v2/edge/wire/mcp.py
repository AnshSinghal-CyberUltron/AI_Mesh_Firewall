"""MCP method table, text-leaf fold, rebuild and bounds (GW17, task 4.3).

Pure. The fold turns the JSON fields an MCP message carries into ``(channel, text)``
pairs for the shared detectors: every string value at any depth, and every object key.
``rebuild`` writes the enforced texts back by path into a copy. Bounds are checked while
folding, so an over-deep or over-wide value stops at the first breach.

Scan order: the text of every ``content[i]`` block is emitted first and contiguously,
then every other leaf. A join run over those block leaves lets the detector see a secret
split across two blocks (LGW17-6) without a ``"type": "text"`` string in between.
"""

from __future__ import annotations

import copy
import json
from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType

from gateway_v2.domain.channels import TEXT_CHANNELS
from gateway_v2.runtime.resources import McpBounds

__all__ = (
    "CHANNEL_ARGUMENTS",
    "CHANNEL_COMPLETION",
    "CHANNEL_ERROR",
    "CHANNEL_RESULT",
    "LOCAL_METHODS",
    "METHODS",
    "Fold",
    "Leaf",
    "MethodSpec",
    "check_request_size",
    "fold_reply",
    "fold_request",
    "rebuild",
    "request_targets",
)

CHANNEL_ARGUMENTS = "mcp_arguments"
CHANNEL_COMPLETION = "mcp_completion"
CHANNEL_RESULT = "mcp_result"
CHANNEL_ERROR = "mcp_error"
_MCP_PREFIX = "mcp:"
_FOLDED = frozenset({CHANNEL_ARGUMENTS, CHANNEL_COMPLETION, CHANNEL_RESULT, CHANNEL_ERROR})

_TOO_LARGE_REQUEST = "mcp_request_too_large"
_TOO_LARGE_RESULT = "mcp_result_too_large"
_INVALID_PARAMS = "mcp_invalid_params"

Path = tuple[str | int, ...]


@dataclass(frozen=True, slots=True)
class MethodSpec:
    """How one method is handled. ``forward`` false means answered by the gateway."""

    forward: bool
    scan_reply: bool


METHODS: Mapping[str, MethodSpec] = MappingProxyType(
    {
        "initialize": MethodSpec(forward=False, scan_reply=False),
        "ping": MethodSpec(forward=False, scan_reply=False),
        "notifications/initialized": MethodSpec(forward=False, scan_reply=False),
        "tools/list": MethodSpec(forward=True, scan_reply=True),
        "tools/call": MethodSpec(forward=True, scan_reply=True),
        "resources/read": MethodSpec(forward=True, scan_reply=True),
        "prompts/get": MethodSpec(forward=True, scan_reply=True),
        "completion/complete": MethodSpec(forward=True, scan_reply=True),
    }
)
LOCAL_METHODS = frozenset(name for name, spec in METHODS.items() if not spec.forward)


@dataclass(frozen=True, slots=True)
class Leaf:
    """One text the detector sees. ``is_key`` marks an object key rather than a value."""

    path: Path
    is_key: bool
    text: str
    channel: str


@dataclass(frozen=True, slots=True)
class Fold:
    """Leaves in scan order, and the index runs to scan joined."""

    leaves: tuple[Leaf, ...]
    join_runs: tuple[tuple[int, int], ...] = ()

    def pairs(self) -> tuple[tuple[str, str], ...]:
        return tuple((leaf.channel, leaf.text) for leaf in self.leaves)


class _Budget:
    """Node and depth accounting for one fold. Raises ``_Breach`` at the first breach."""

    __slots__ = ("depth", "nodes")

    def __init__(self, depth: int, nodes: int) -> None:
        self.depth = depth
        self.nodes = nodes

    def visit(self, depth: int) -> None:
        self.nodes -= 1
        if self.nodes < 0 or depth > self.depth:
            raise _Breach


class _Breach(Exception):
    pass


def request_targets(method: str, params: object) -> tuple[tuple[Path, object, str], ...] | str:
    """``(path within params, value, channel)`` for each request field to scan, or a code."""
    if params is not None and not isinstance(params, dict):
        return _INVALID_PARAMS
    p: Mapping[str, object] = params or {}
    if method == "tools/call":
        if not isinstance(p.get("name"), str) or not isinstance(p.get("arguments", {}), dict):
            return _INVALID_PARAMS
        return ((("arguments",), p.get("arguments", {}), CHANNEL_ARGUMENTS),)
    if method == "prompts/get":
        if not isinstance(p.get("name"), str) or not isinstance(p.get("arguments", {}), dict):
            return _INVALID_PARAMS
        return ((("arguments",), p.get("arguments", {}), CHANNEL_ARGUMENTS),)
    if method == "completion/complete":
        return _completion_targets(p)
    if method == "resources/read":
        return () if isinstance(p.get("uri"), str) else _INVALID_PARAMS
    return ()


def _completion_targets(p: Mapping[str, object]) -> tuple[tuple[Path, object, str], ...] | str:
    argument = p.get("argument")
    if not isinstance(argument, dict) or not isinstance(argument.get("value"), str):
        return _INVALID_PARAMS
    targets: list[tuple[Path, object, str]] = [
        (("argument", "value"), argument["value"], CHANNEL_COMPLETION)
    ]
    context = p.get("context")
    if isinstance(context, dict) and "arguments" in context:
        if not isinstance(context["arguments"], dict):
            return _INVALID_PARAMS
        targets.append((("context", "arguments"), context["arguments"], CHANNEL_ARGUMENTS))
    return tuple(targets)


def check_request_size(params: object, bounds: McpBounds) -> str | None:
    """The argument byte bound, checked before any fold."""
    try:
        size = len(json.dumps(params, ensure_ascii=False).encode("utf-8"))
    except (TypeError, ValueError):
        return _INVALID_PARAMS
    return _TOO_LARGE_REQUEST if size > bounds.argument_bytes else None


def fold_request(
    targets: tuple[tuple[Path, object, str], ...], bounds: McpBounds
) -> Fold | str:
    """Fold the request targets. No join runs: request fields are scanned leaf by leaf."""
    budget = _Budget(bounds.json_depth, bounds.argument_nodes)
    leaves: list[Leaf] = []
    try:
        for path, value, channel in targets:
            _walk(value, path, channel, 0, budget, leaves)
    except _Breach:
        return _TOO_LARGE_REQUEST
    return Fold(leaves=tuple(leaves))


def fold_reply(messages: tuple[Mapping[str, object], ...], bounds: McpBounds) -> Fold | str:
    """Fold ``result`` (or ``error``) of every reply message, block texts first.

    A message that is a server-initiated request (it has a ``method``) folds its
    ``params`` as result text, so it is scanned before it is withheld.
    """
    budget = _Budget(bounds.json_depth, bounds.result_nodes)
    blocks = 0
    found: list[Leaf] = []
    try:
        for index, message in enumerate(messages):
            for key, channel in (("result", CHANNEL_RESULT), ("error", CHANNEL_ERROR)):
                if key in message:
                    blocks += _count_blocks(message[key])
                    _walk(message[key], (index, key), channel, 0, budget, found)
            if "method" in message and "params" in message:
                _walk(message["params"], (index, "params"), CHANNEL_RESULT, 0, budget, found)
    except _Breach:
        return _TOO_LARGE_RESULT
    if blocks > bounds.result_blocks:
        return _TOO_LARGE_RESULT
    return _block_first(found)


def _count_blocks(value: object) -> int:
    if isinstance(value, dict) and isinstance(value.get("content"), list):
        return len(value["content"])
    return 0


def _walk(
    value: object, path: Path, channel: str, depth: int, budget: _Budget, out: list[Leaf]
) -> None:
    budget.visit(depth)
    if isinstance(value, str):
        out.append(Leaf(path, False, value, channel))
    elif isinstance(value, dict):
        for key in value:
            out.append(Leaf((*path, key), True, str(key), channel))
            _walk(value[key], (*path, key), channel, depth + 1, budget, out)
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _walk(item, (*path, index), channel, depth + 1, budget, out)


def _is_block_text(leaf: Leaf) -> bool:
    path = leaf.path
    return (
        not leaf.is_key
        and len(path) >= 3
        and path[-1] == "text"
        and isinstance(path[-2], int)
        and path[-3] == "content"
    )


def _block_first(leaves: list[Leaf]) -> Fold:
    """Block texts first, grouped by content list, then every other leaf."""
    blocks = [leaf for leaf in leaves if _is_block_text(leaf)]
    others = [leaf for leaf in leaves if not _is_block_text(leaf)]
    runs: list[tuple[int, int]] = []
    start = 0
    for index in range(1, len(blocks) + 1):
        if index == len(blocks) or blocks[index].path[:-2] != blocks[start].path[:-2]:
            if index - start >= 2:
                runs.append((start, index - 1))
            start = index
    return Fold(leaves=tuple(blocks + others), join_runs=tuple(runs))


def rebuild(value: object, fold: Fold, texts: tuple[str, ...], *, offset: int = 0) -> object:
    """A copy of ``value`` with every leaf replaced by ``texts`` (by path).

    ``offset`` drops that many leading path segments (a message index, for a reply).
    Values are written first with the original keys, then keys are renamed deepest
    first, so every path stays valid. Raises ``ValueError`` on a count mismatch or a
    key rename that would collide; the caller fails closed.
    """
    if len(texts) != len(fold.leaves):
        raise ValueError("text count does not match the fold")
    out = copy.deepcopy(value)
    pairs = list(zip(fold.leaves, texts, strict=True))
    for leaf, text in pairs:
        if not leaf.is_key and text != leaf.text:
            _set(out, leaf.path[offset:], text)
    keys = sorted((p for p in pairs if p[0].is_key), key=lambda p: -len(p[0].path))
    for leaf, text in keys:
        if text != leaf.text:
            _rename(out, leaf.path[offset:], text)
    return out


def _parent(root: object, path: Path) -> object:
    node = root
    for segment in path[:-1]:
        if isinstance(node, dict) and isinstance(segment, str):
            node = node[segment]
        elif isinstance(node, list) and isinstance(segment, int):
            node = node[segment]
        else:
            raise ValueError("path does not match the value")
    return node


def _set(root: object, path: Path, text: str) -> None:
    parent = _parent(root, path)
    last = path[-1]
    if isinstance(parent, dict) and isinstance(last, str):
        parent[last] = text
    elif isinstance(parent, list) and isinstance(last, int):
        parent[last] = text
    else:
        raise ValueError("path does not match the value")


def _rename(root: object, path: Path, text: str) -> None:
    parent = _parent(root, path)
    old = path[-1]
    if not isinstance(parent, dict) or not isinstance(old, str) or text in parent:
        raise ValueError("key rename does not apply cleanly")
    parent[text] = parent.pop(old)


def _assert_fold_total() -> None:
    expected = frozenset(
        channel.name for channel in TEXT_CHANNELS if channel.sdk_path.startswith(_MCP_PREFIX)
    )
    if expected != _FOLDED:
        raise RuntimeError(
            f"MCP fold is not total over TEXT_CHANNELS: expected {sorted(expected)}, "
            f"folded {sorted(_FOLDED)}"
        )


_assert_fold_total()
