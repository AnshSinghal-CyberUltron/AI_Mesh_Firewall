"""The one redactor: pure string span replacement shared by dispatch/ and egress/."""

from __future__ import annotations

from collections.abc import Sequence


def merge_spans(spans: Sequence[tuple[int, int, str]]) -> list[tuple[int, int, str]]:
    merged: list[tuple[int, int, str]] = []
    for a, b, rep in sorted(spans):
        if merged and a < merged[-1][1]:
            pa, pb, prep = merged[-1]
            merged[-1] = (pa, max(pb, b), prep)
        else:
            merged.append((a, b, rep))
    return merged


def redact_text(text: str, spans: Sequence[tuple[int, int, str]]) -> str:
    """Replace merged, sorted spans [a, b) of `text` with their replacement strings."""
    out: list[str] = []
    pos = 0
    for a, b, rep in merge_spans(spans):
        out.append(text[pos:a])
        out.append(rep)
        pos = b
    out.append(text[pos:])
    return "".join(out)
