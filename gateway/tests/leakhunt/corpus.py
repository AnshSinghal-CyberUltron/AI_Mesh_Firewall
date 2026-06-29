"""PII corpus for the leak-hunt egress rig (story A2).

A single source of truth for the raw PII *values* that MUST NEVER appear in the
bytes the gateway egresses to a provider / embedding / vector store. Every B-story
gate ("no raw PII on the wire") is expressed against this corpus, so adding a
leaking format here is how a future story documents — and then closes — a gap.

Each :class:`PiiItem` carries:

* ``raw``    — the exact value that must not egress (substring-checked on the wire).
* ``cue``    — a natural-language lead-in the value is embedded in, so the prompt
               built from it mirrors how the value actually arrives from a user.
* ``covered``— whether the CURRENT deterministic redactor (``patterns.redact_all``
               + ``llm_router`` digit backstop) is expected to neutralize it.
               ``False`` items are the documented leaks the B-stories fix; a few
               (bare-10-digit with NO phone cue) are ``False`` *by design* — they
               are ambiguous order-id-shaped runs the firewall deliberately keeps
               to avoid over-redaction, so they are flagged ``intentional``.

Nothing here imports the code under test, so the corpus stays an independent
specification rather than a mirror of the redactor's own regexes.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List


@dataclass(frozen=True)
class PiiItem:
    label: str           # PII class: phone / email / ssn / card / secret
    fmt: str             # format variant within the class
    raw: str             # the raw value that must never reach the wire
    cue: str             # natural-language lead-in the value is embedded in
    covered: bool        # is the current redactor expected to neutralize it?
    intentional: bool = False  # covered=False BY DESIGN (ambiguous, FP-protected)
    note: str = ""

    def prompt(self) -> str:
        """A user-message string carrying this PII value in a realistic phrasing."""
        return f"{self.cue}{self.raw}"


# ── Phone formats — the headline leak class (G0). The redactor intentionally skips
#    a *bare* 10-digit run (order-id / revenue false positives) unless a phone cue
#    disambiguates it; the digit backstop only catches *contiguous* 7+ digit runs,
#    so any separator-split format slips both. ──
_PHONES: List[PiiItem] = [
    PiiItem("phone", "us_3_3_4", "415-555-0142",
            cue="reach me at ", covered=True),
    PiiItem("phone", "us_dotted", "415.555.0142",
            cue="my number is ", covered=True),
    PiiItem("phone", "us_paren", "(415) 555-0142",
            cue="call ", covered=True),
    PiiItem("phone", "bare10_contextual", "8929554991",
            cue="call me at ", covered=True,
            note="phone_us_bare_contextual: cue + contiguous \\d{10}"),
    PiiItem("phone", "intl_e164", "+919876543210",
            cue="my mobile is ", covered=True),
    PiiItem("phone", "intl_cc_dash", "+91-8088054321",
            cue="ping ", covered=True),
    # ── KNOWN LEAKS the B-stories close ──
    PiiItem("phone", "us_5_5_spaced", "89295 54991",
            cue="please call me at ", covered=False,
            note="5+5 spaced: no pattern matches, space breaks the digit backstop (B2)"),
    PiiItem("phone", "cc_space_10", "+1 8929554991",
            cue="text me on ", covered=True,
            note="phone_intl \\d{1,4}[\\s-]?\\d{6,12} branch"),
    # ── Ambiguous BY DESIGN — kept raw to avoid order-id false positives (NOT a bug) ──
    PiiItem("phone", "bare10_no_cue", "8929554991",
            cue="ref number ", covered=False, intentional=True,
            note="bare 10-digit, no phone cue: indistinguishable from an order id"),
]

_EMAILS: List[PiiItem] = [
    PiiItem("email", "plain", "evance.maps@mail.com",
            cue="email me at ", covered=True),
    PiiItem("email", "plus_tag", "jane.doe+billing@example.co.uk",
            cue="cc ", covered=True),
]

_SSNS: List[PiiItem] = [
    PiiItem("ssn", "dashed", "123-45-6789", cue="my ssn is ", covered=True),
    PiiItem("ssn", "spaced", "123 45 6789", cue="ssn ", covered=True),
    PiiItem("ssn", "nosep_contextual", "123456789",
            cue="social security number ", covered=True),
]

_CARDS: List[PiiItem] = [
    PiiItem("card", "spaced", "4111 1111 1111 1111", cue="my card is ", covered=True),
    PiiItem("card", "dashed", "4111-1111-1111-1111", cue="charge ", covered=True),
]

_SECRETS: List[PiiItem] = [
    PiiItem("secret", "openai_proj", "sk-proj-ABCD1234efgh5678IJKL90mn",
            cue="my key is ", covered=True),
    PiiItem("secret", "openrouter", "sk-or-v1-abcdef0123456789abcdef0123",
            cue="use ", covered=True),
]

CORPUS: List[PiiItem] = _PHONES + _EMAILS + _SSNS + _CARDS + _SECRETS

# Items whose raw value MUST be absent from any wire when redaction fired.
REDACTABLE = [it for it in CORPUS if it.covered]

# Documented leaks (real gaps the B-stories fix), excluding the by-design pass-throughs.
KNOWN_LEAKS = [it for it in CORPUS if not it.covered and not it.intentional]

# The raw values, for quick substring membership scans.
ALL_RAW = [it.raw for it in CORPUS]


def by_label(label: str) -> List[PiiItem]:
    return [it for it in CORPUS if it.label == label]
