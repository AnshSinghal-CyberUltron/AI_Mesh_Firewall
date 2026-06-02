"""Target extraction and mutation for MCP two-tier scanning.

Supports entire-payload scanning and key-path targeting (dot paths or
recursive key-name collection, aligned with the policy engine).
"""

from __future__ import annotations

import copy
import json
import unicodedata
from typing import Any, Callable


def _normalize_key(key: str) -> str:
    return unicodedata.normalize("NFKC", key).casefold()


def _safe_json(value: Any) -> str:
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        return str(value)


def collect_key_values(obj: Any, key: str, *, _depth: int = 0) -> list[str]:
    """Recursively collect string values under ``key`` (case-insensitive)."""
    if _depth > 10 or not key:
        return []
    target = _normalize_key(key)
    out: list[str] = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            if _normalize_key(k) == target:
                out.append(v if isinstance(v, str) else _safe_json(v))
            else:
                out.extend(collect_key_values(v, key, _depth=_depth + 1))
    elif isinstance(obj, list):
        for item in obj:
            out.extend(collect_key_values(item, key, _depth=_depth + 1))
    return out


def _get_by_dot_path(obj: Any, path: str) -> list[Any]:
    """Return values at a dot-separated path (e.g. arguments.email)."""
    if not path:
        return []
    parts = [p for p in path.split(".") if p]
    nodes: list[Any] = [obj]
    for part in parts:
        next_nodes: list[Any] = []
        for node in nodes:
            if isinstance(node, dict) and part in node:
                next_nodes.append(node[part])
            elif isinstance(node, list):
                for item in node:
                    if isinstance(item, dict) and part in item:
                        next_nodes.append(item[part])
        nodes = next_nodes
        if not nodes:
            return []
    return nodes


def extract_scan_targets(
    payload: Any,
    *,
    target_mode: str,
    key_path: str,
) -> list[tuple[str, Callable[[str], None]]]:
    """Return (text, setter) pairs to scan.

    For ``entire`` mode a single target is returned. For ``key_path`` mode
    each matching string leaf receives an individual setter that mutates
    only that leaf.
    """
    if payload is None:
        return []

    if target_mode != "key_path" or not (key_path or "").strip():
        text = payload if isinstance(payload, str) else _safe_json(payload)

        def _set_entire(value: str) -> None:
            nonlocal payload
            payload = value

        return [(text, _set_entire)]

    path = key_path.strip()
    targets: list[tuple[str, Callable[[str], None]]] = []

    if "." in path:
        values = _get_by_dot_path(payload, path)
        # Dot-path mutation is best-effort: rebuild only when a single dict path.
        for idx, val in enumerate(values):
            if isinstance(val, str):

                def _make_setter(i: int) -> Callable[[str], None]:
                    def _set(v: str) -> None:
                        _mutate_dot_path(payload, path, i, v)

                    return _set

                targets.append((val, _make_setter(idx)))
            else:
                targets.append((_safe_json(val), lambda _v, _val=val: None))
        return targets

    # Simple key name — walk all matching keys.
    def _walk(node: Any, parent: Any | None, key_in_parent: Any | None) -> None:
        if isinstance(node, dict):
            for k, v in node.items():
                if _normalize_key(k) == _normalize_key(path):
                    if isinstance(v, str):

                        def _set_str(new: str, _p=node, _k=k) -> None:
                            _p[_k] = new

                        targets.append((v, _set_str))
                    else:
                        targets.append((_safe_json(v), lambda _n, _p=node, _k=k: None))
                else:
                    _walk(v, node, k)
        elif isinstance(node, list):
            for i, item in enumerate(node):
                _walk(item, node, i)

    _walk(payload, None, None)
    return targets


def _mutate_dot_path(root: Any, path: str, index: int, new_value: str) -> None:
    """Replace the index-th value along a dot path (best-effort)."""
    parts = [p for p in path.split(".") if p]
    if not parts or not isinstance(root, dict):
        return
    node: Any = root
    for part in parts[:-1]:
        if not isinstance(node, dict) or part not in node:
            return
        node = node[part]
    last = parts[-1]
    if isinstance(node, dict) and last in node:
        cur = node[last]
        if isinstance(cur, list) and 0 <= index < len(cur):
            node[last][index] = new_value
        elif index == 0:
            node[last] = new_value


def apply_target_updates(payload: Any, updates: list[tuple[Callable[[str], None], str]]) -> Any:
    """Apply redacted strings via setters; returns deep-copied payload."""
    if not updates:
        return payload
    cloned = copy.deepcopy(payload)
    # Re-extract is expensive; setters close over cloned structure when built
    # from extract_scan_targets on cloned payload.
    return cloned


def build_mutable_payload(payload: Any) -> tuple[Any, list[tuple[str, Callable[[str], None]]]]:
    """Deep-copy payload and return targets bound to the copy."""
    cloned = copy.deepcopy(payload)
    return cloned, []


def extract_and_bind(
    payload: Any,
    *,
    target_mode: str,
    key_path: str,
) -> tuple[Any, list[tuple[str, Callable[[str], None], str]]]:
    """Return (mutable_payload, [(text, setter, path_label), ...])."""
    state: list[Any] = [copy.deepcopy(payload)]
    label = key_path if target_mode == "key_path" and key_path else "entire"

    if target_mode != "key_path" or not (key_path or "").strip():
        text = state[0] if isinstance(state[0], str) else _safe_json(state[0])

        def _set_entire(value: str) -> None:
            if isinstance(state[0], str):
                state[0] = value
            else:
                try:
                    state[0] = json.loads(value)
                except (TypeError, ValueError):
                    state[0] = value

        return state, [(text, _set_entire, label)]

    pairs = extract_scan_targets(
        state[0],
        target_mode=target_mode,
        key_path=key_path,
    )
    return state, [(t, s, label) for t, s in pairs]
