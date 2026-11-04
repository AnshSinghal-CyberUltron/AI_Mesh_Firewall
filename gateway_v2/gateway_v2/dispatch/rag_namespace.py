"""GW31 RAG namespaces: one injective, case-stable wire encoding for (org, project, collection).

The wire string is encode-each-segment-then-join over LOWERCASE-only segments, so it is injective
over triples, immune to case folding on any connector, and bounded by ``wire_max_length``.
Uppercase is refused, never folded. ``wire_collection`` is the only builder of a wire string.
"""
from __future__ import annotations

from gateway_v2.domain.rag import Namespace

__all__ = (
    "DEFAULT_PROJECT_SEGMENT",
    "NamespaceRefused",
    "build_namespace",
    "encode_segment",
    "parse_wire",
    "validate_segment",
    "wire_collection",
)

DEFAULT_PROJECT_SEGMENT = "default"
_ALLOWED = frozenset("abcdefghijklmnopqrstuvwxyz0123456789_-")
_SEP = "_s"


class NamespaceRefused(ValueError):
    """A segment is outside the charset, empty, too long, or the wire name is over its bound."""


def validate_segment(value: object, max_len: int) -> str:
    """Return ``value`` when it is a non-empty ASCII lowercase ``[a-z0-9_-]`` string."""
    if not isinstance(value, str) or not value or len(value) > max_len:
        raise NamespaceRefused("segment empty, mistyped or too long")
    if any(ch not in _ALLOWED for ch in value):
        raise NamespaceRefused("segment outside [a-z0-9_-]")
    return value


def encode_segment(segment: str) -> str:
    """``_`` becomes ``_u`` and ``-`` becomes ``_d``; the result is in ``[a-z0-9_]``."""
    return segment.replace("_", "_u").replace("-", "_d")


def build_namespace(
    org_id: str,
    project: str,
    collection: str,
    isolation: str,
    *,
    org_max: int,
    project_max: int,
    collection_max: int,
    wire_max: int,
) -> Namespace:
    """The only constructor path. Under ``isolation == "org"`` the project segment is fixed."""
    effective = project if isolation == "project" else DEFAULT_PROJECT_SEGMENT
    ns = Namespace(
        org_id=validate_segment(org_id, org_max),
        project=validate_segment(effective, project_max),
        collection=validate_segment(collection, collection_max),
    )
    if len(wire_collection(ns)) > wire_max:
        raise NamespaceRefused("encoded wire name over its bound")
    return ns


def wire_collection(ns: Namespace) -> str:
    """``o<org>_s<project>_s<collection>``, each segment encoded."""
    return (
        "o" + encode_segment(ns.org_id) + _SEP + encode_segment(ns.project)
        + _SEP + encode_segment(ns.collection)
    )


def _decode(encoded: str) -> str | None:
    out: list[str] = []
    i = 0
    while i < len(encoded):
        ch = encoded[i]
        if ch == "_":
            if i + 1 >= len(encoded) or encoded[i + 1] not in "ud":
                return None
            out.append("_" if encoded[i + 1] == "u" else "-")
            i += 2
        else:
            out.append(ch)
            i += 1
    return "".join(out)


def parse_wire(name: str) -> Namespace | None:
    """Inverse of :func:`wire_collection`; ``None`` for any other shape."""
    if not name.startswith("o") or any(
        ch not in "abcdefghijklmnopqrstuvwxyz0123456789_" for ch in name
    ):
        return None
    parts: list[str] = []
    current: list[str] = []
    i = 1
    while i < len(name):
        if name[i] == "_":
            if i + 1 >= len(name):
                return None
            nxt = name[i + 1]
            if nxt == "s":
                parts.append("".join(current))
                current = []
            elif nxt in "ud":
                current.append(name[i : i + 2])
            else:
                return None
            i += 2
        else:
            current.append(name[i])
            i += 1
    parts.append("".join(current))
    if len(parts) != 3:
        return None
    decoded = [_decode(p) for p in parts]
    if any(d is None or not d for d in decoded):
        return None
    org, project, collection = (str(d) for d in decoded)
    ns = Namespace(org, project, collection)
    return ns if wire_collection(ns) == name else None
