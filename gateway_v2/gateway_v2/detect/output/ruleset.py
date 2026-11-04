"""Frozen, hashed rulesets for the GW33 output detectors (detection semantics, not capacity).

CIDR tables, suffix sets, sink patterns and ratios live here under a `ruleset_version_of` digest so
a ruleset change changes `detector_version`. Nothing here is a capacity number: those come from
`runtime/resources_output.py` through `OutputBounds`.
"""
from __future__ import annotations

import hashlib
from collections.abc import Mapping
from types import MappingProxyType
from typing import Final

__all__ = (
    "ENTROPY_THRESHOLD",
    "EXFIL_RULESET",
    "GROUNDING_RULESET",
    "IP_RULESET",
    "MIN_CONTENT_WORDS",
    "MIN_WORD_CHARS",
    "PRINTABLE_RATIO",
    "SENTENCE_SUPPORT",
    "SHINGLE_SIZE",
    "STOP_WORDS",
    "ruleset_version_of",
)

# -- IP / infrastructure (requirement 1) ---------------------------------------------------------
#: Internal ranges: RFC1918, loopback, link-local (incl. cloud metadata), ULA, CGNAT.
_INTERNAL: Final = (
    "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "127.0.0.0/8", "169.254.0.0/16",
    "100.64.0.0/10", "::1/128", "fe80::/10", "fc00::/7",
)
#: Documentation ranges: explicitly NOT findings.
_DOCUMENTATION: Final = ("192.0.2.0/24", "198.51.100.0/24", "203.0.113.0/24", "2001:db8::/32")
#: Internal hostname suffixes (the leading dot is significant).
_HOST_SUFFIXES: Final = (
    ".internal", ".local", ".lan", ".corp", ".intranet", ".home.arpa", ".svc",
    ".svc.cluster.local",
)

IP_RULESET: Final[Mapping[str, tuple[str, ...]]] = MappingProxyType(
    {
        "internal_networks": _INTERNAL,
        "documentation_networks": _DOCUMENTATION,
        "internal_host_suffixes": _HOST_SUFFIXES,
    }
)

# -- Exfiltration (requirement 2) -----------------------------------------------------------------
#: Tags whose URL attribute is fetched without a click.
_AUTOFETCH_TAGS: Final = (
    "img", "iframe", "video", "audio", "source", "embed", "object", "input", "script", "image",
    "use", "base", "track", "frame",
)
_URL_ATTRS: Final = ("src", "href", "srcset", "poster", "data", "xlink:href")
_LINK_AUTOFETCH_RELS: Final = ("stylesheet", "dns-prefetch", "preconnect", "preload", "prefetch")
_SCHEMES: Final = ("http", "https", "ftp", "ws", "wss")

EXFIL_RULESET: Final[Mapping[str, tuple[str, ...]]] = MappingProxyType(
    {
        "autofetch_tags": _AUTOFETCH_TAGS,
        "url_attributes": _URL_ATTRS,
        "link_autofetch_rels": _LINK_AUTOFETCH_RELS,
        "schemes": _SCHEMES,
        "css_sinks": ("url(", "@import"),
        "markdown_sinks": ("inline_image", "reference_image", "inline_link"),
    }
)

#: Share of decoded bytes that must be printable for an encoded run to count as a payload.
PRINTABLE_RATIO: Final = 0.85
#: Shannon entropy (bits per character) from which a leftmost DNS label looks like a blob.
ENTROPY_THRESHOLD: Final = 3.6

# -- Grounding (requirement 3) --------------------------------------------------------------------
#: Content words shorter than this are ignored.
MIN_WORD_CHARS: Final = 4
#: Word n-gram size used for shingle support.
SHINGLE_SIZE: Final = 3
#: Share of a sentence's content words that must occur in the context for support.
SENTENCE_SUPPORT: Final = 0.6
#: Sentences with fewer content words are too short to judge and count as supported.
MIN_CONTENT_WORDS: Final = 3
STOP_WORDS: Final[frozenset[str]] = frozenset(
    """about above after again against also because been before being below between both but
    could does doing down during each from further have having here hers herself himself into
    itself just more most myself once only other ought ours ourselves over same should some such
    than that their theirs them themselves then there these they this those through under until
    very was were what when where which while who whom will with would your yours yourself
    yourselves""".split()
)

GROUNDING_RULESET: Final[Mapping[str, tuple[str, ...]]] = MappingProxyType(
    {
        "modes": ("lexical", "model"),
        "constants": (
            str(PRINTABLE_RATIO), str(MIN_WORD_CHARS), str(SHINGLE_SIZE), str(SENTENCE_SUPPORT),
            str(MIN_CONTENT_WORDS),
        ),
        "stop_words": tuple(sorted(STOP_WORDS)),
    }
)


def ruleset_version_of(ruleset: Mapping[str, tuple[str, ...]]) -> str:
    """Deterministic sha256 over the sorted records of ``ruleset`` (same idiom as GW09)."""
    hasher = hashlib.sha256()
    for key in sorted(ruleset):
        hasher.update("\x1f".join((key, *ruleset[key])).encode("utf-8"))
        hasher.update(b"\x1e")
    return hasher.hexdigest()
