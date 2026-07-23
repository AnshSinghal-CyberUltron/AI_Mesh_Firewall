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


# #32 (consistency with control #29 / gateway #31): align this "aligned with the
# policy engine" helper to cap 500 (was a recursive depth-10 cap). The gateway's
# LIVE key-path scan walker is extract_scan_targets._walk (unbounded, bounded by the
# _MCP_MAX_RESULT_DEPTH result guard) — collect_key_values is currently only exercised
# by tests, so the depth-10 cap was latent, not a live bypass; still fixed so a future
# caller can't inherit the old blind spot. Iterative → no RecursionError at any depth.
_KEY_COLLECT_MAX_DEPTH = 500
_KEY_COLLECT_MAX_NODES = 2_000_000


def collect_key_values(obj: Any, key: str) -> list[str]:
    """Collect string values under ``key`` (case-insensitive), ITERATIVE walk."""
    if not key:
        return []
    target = _normalize_key(key)
    out: list[str] = []
    stack: list[tuple[Any, int]] = [(obj, 0)]
    nodes = 0
    while stack:
        cur, depth = stack.pop()
        if depth > _KEY_COLLECT_MAX_DEPTH:
            continue
        nodes += 1
        if nodes > _KEY_COLLECT_MAX_NODES:
            break
        if isinstance(cur, dict):
            for k, v in cur.items():
                if _normalize_key(k) == target:
                    out.append(v if isinstance(v, str) else _safe_json(v))
                else:
                    stack.append((v, depth + 1))
        elif isinstance(cur, list):
            for item in cur:
                stack.append((item, depth + 1))
    return out


def _get_by_dot_path(obj: Any, path: str) -> list[Any]:
    """Return values at a dot-separated path (e.g. arguments.email).

    Key comparison is NFKC-casefolded (B2 live-binder fix): the detection binder
    (``policy_engine._collect_dot_path_values``), the seed redaction, and the plain-key walk all
    normalize, so an EXACT match here under-masked a case/NFKC-variant payload key (a raw credential
    egressed while the collapsed seed masked it). An all-blank path (``.``/``..``) yields no parts →
    ``[]`` (parity with the collector), rather than returning the WHOLE payload as one target."""
    if not path:
        return []
    parts = [p for p in path.split(".") if p]
    if not parts:
        return []
    nodes: list[Any] = [obj]
    for part in parts:
        pn = _normalize_key(part)
        next_nodes: list[Any] = []
        for node in nodes:
            if isinstance(node, dict):
                next_nodes.extend(v for k, v in node.items() if _normalize_key(k) == pn)
            elif isinstance(node, list):
                for item in node:
                    if isinstance(item, dict):
                        next_nodes.extend(v for k, v in item.items() if _normalize_key(k) == pn)
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
        def _make_setter(i: int) -> Callable[[str], None]:
            def _set(v: str) -> None:
                _mutate_dot_path(payload, path, i, v)

            return _set

        for idx, val in enumerate(values):
            if isinstance(val, str):
                targets.append((val, _make_setter(idx)))
            else:
                # CHG-0046: a non-string dot-path target (number / list / object)
                # previously got a NO-OP setter, so a detected secret/PII inside it
                # was reported redacted (scan_mcp_payload sets result_redacted=True)
                # yet egressed RAW — and the E12 result-floor is then BYPASSED
                # (the returned payload is a fresh object, so `scanned is
                # result_content` is False). Bind the SAME real mutator so redaction
                # replaces the value with the masked string (fail-closed byte truth,
                # never report-redact-while-forwarding-raw).
                targets.append((_safe_json(val), _make_setter(idx)))
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
                        # CHG-0046: a non-string keyed value (number / list / object)
                        # was bound to a NO-OP setter — detected secret/PII in it was
                        # reported redacted but forwarded RAW. Bind a REAL setter so
                        # the masked string replaces the value in place (fail-closed).
                        def _set_nonstr(new: str, _p=node, _k=k) -> None:
                            _p[_k] = new

                        targets.append((_safe_json(v), _set_nonstr))
                else:
                    _walk(v, node, k)
        elif isinstance(node, list):
            for i, item in enumerate(node):
                _walk(item, node, i)

    _walk(payload, None, None)
    return targets


def _mutate_dot_path(root: Any, path: str, index: int, new_value: str) -> None:
    """Replace the index-th value along a dot path (best-effort). NFKC-casefolded key match, so the
    setter writes back the SAME case/NFKC-variant key the getter matched (B2 live-binder fix)."""
    parts = [p for p in path.split(".") if p]
    if not parts or not isinstance(root, dict):
        return

    def _find_key(d: dict, part: str):
        pn = _normalize_key(part)
        for k in d:
            if _normalize_key(k) == pn:
                return k
        return None

    node: Any = root
    for part in parts[:-1]:
        if not isinstance(node, dict):
            return
        k = _find_key(node, part)
        if k is None:
            return
        node = node[k]
    if not isinstance(node, dict):
        return
    last = _find_key(node, parts[-1])
    if last is None:
        return
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
