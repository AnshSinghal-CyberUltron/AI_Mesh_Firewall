"""Long-prompt corpus stratum for the load harness (B6 / R2-21).

Mixes **long** 4,000–7,000-token prompts into the offered traffic at a configured share (one of
1% / 5% / 25%, Req 4.1) against **short** prompts of at most 1,024 tokens, drawing one prompt per
request from an injected ``random.Random`` so the realized long-share converges to the configured
share within tolerance over a run (Req 4.2). Each pick carries a long-vs-short **tag** plus the
prompt's token count and body, so the C4 collector (``c4.py``, task 6.1) can split long-prompt vs
≤ 1,024-token C4 statistics (Req 4.3).

Self-contained (determinism spine). This module takes only primitives + an injected ``rng``; it does
**not** import the in-flight ``arrivals.py`` (nor any other harness module). A single stratum
pick is a pure function of ``(corpus, share, rng)``, so it is offline-deterministic and
property-testable in isolation, and the ``request_plan.py`` composer (task 4.6) can layer it onto
an ``Arrival`` without a circular edge.

Token-count proxy (documented approximation, Req 4.1/4.3). The harness does **not** run a real
tokenizer: the token figures here are an explicit **~4-characters-per-token** proxy
(``_CHARS_PER_TOKEN``), the common rule-of-thumb for English/BPE-style tokenizers. A prompt of ``n``
proxy-tokens is generated as a body of ``n * _CHARS_PER_TOKEN`` characters, and a body's proxy token
count is ``len(body) / _CHARS_PER_TOKEN``. This is a *size* proxy chosen so the body is the right
order of magnitude for the serving path to exercise long- vs short-prompt behaviour; it is NOT a
claim of exact token parity with any specific model's tokenizer, and the C4 split keys only on the
long/short **tag**, never on an exact token count.

Corpus-policy constants, NOT capacity positions (Req 14.2). The 4,000 / 7,000 / 1,024 token figures
and the 1 / 5 / 25 % shares are **stratum-corpus policy** — the traffic shape GW20b asks the
instrument to offer — the same category as ``config.py``'s strata tolerances and
``latency_slo._LOST_SEGMENT_TAIL_MS``. They are named module constants with a documented rationale,
NOT host-capacity positions (CPU / RAM / FD / RPS), so they are not what ``check_capacity_literals``
keys on. The run's SLO/capacity ceiling is discovered by the ladder against the server and is read
from ``ResourceContract.target_p99_ms`` elsewhere; this module holds no such literal.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from enum import Enum

__all__ = (
    "DEFAULT_CORPUS",
    "PromptCorpus",
    "PromptPick",
    "PromptTag",
    "default_corpus",
    "pick",
)


# --------------------------------------------------------------------------------------------------
# Stratum-corpus policy constants (NOT capacity-position literals).
#
# These describe the TRAFFIC SHAPE the instrument offers (long/short prompt sizes + the sanctioned
# long shares), not the host's CPU / RAM / FD / RPS budget. They are the same category as the strata
# tolerances in `config.py` and `latency_slo._LOST_SEGMENT_TAIL_MS`: named operational policy of the
# measurement procedure, documented here, and deliberately NOT a capacity position (so
# `check_capacity_literals` — which keys on capacity-position names/calls — does not flag them). The
# run's SLO/capacity ceiling is read from `ResourceContract.target_p99_ms` elsewhere, never here.
# --------------------------------------------------------------------------------------------------

_CHARS_PER_TOKEN = 4
"""Characters per proxy-token (~4 chars/token English/BPE rule-of-thumb).

PROXY constant (Req 4.1/4.3). The harness runs no real tokenizer; a prompt of ``n`` proxy-tokens
is a body of ``n * _CHARS_PER_TOKEN`` characters. A *size* proxy so bodies are the right order of
magnitude, not a claim of exact token parity with any model's tokenizer. Not a capacity position.
"""

_LONG_PROMPT_MIN_TOKENS = 4_000
"""Lower bound (inclusive) of the long-prompt token band (Req 4.1: 4,000–7,000 tokens).

CORPUS-POLICY constant — the long-prompt traffic shape GW20b asks the instrument to offer, not a
host capacity position.
"""

_LONG_PROMPT_MAX_TOKENS = 7_000
"""Upper bound (inclusive) of the long-prompt token band (Req 4.1: 4,000–7,000 tokens).

CORPUS-POLICY constant — paired with ``_LONG_PROMPT_MIN_TOKENS`` above; not a capacity position.
"""

_SHORT_PROMPT_MAX_TOKENS = 1_024
"""Upper bound (inclusive) of the short-prompt band — "≤ 1,024 tokens" (Req 4.1/4.3).

CORPUS-POLICY constant. Short prompts are drawn in ``[1, _SHORT_PROMPT_MAX_TOKENS]`` and the C4
collector splits long vs ≤ 1,024-token stats on this boundary. Not a capacity position.
"""

_SHORT_PROMPT_MIN_TOKENS = 1
"""Lower bound (inclusive) of the short-prompt band.

CORPUS-POLICY constant — a short prompt carries at least one proxy-token so its body is non-empty.
Not a capacity position.
"""

# The three GW20b-sanctioned long-prompt shares (Req 4.1). Mirrors `config._LONG_PROMPT_SHARES`;
# kept here (not imported) so this stratum module stays self-contained. Policy, not capacity.
_SANCTIONED_LONG_SHARES = (0.01, 0.05, 0.25)

_BODY_FILL_CHAR = "x"
"""Filler character used to synthesize a prompt body of a target length.

A single repeated ASCII byte keeps body generation O(length), deterministic, and free of any
secret-shaped content (secret-shaped output is a SEPARATE stratum, ``strata/secret_output.py``).
"""


class PromptTag(Enum):
    """Long-vs-short classification carried on every :class:`PromptPick` (Req 4.3).

    ``LONG`` marks a 4,000–7,000-token prompt; ``SHORT`` marks a ≤ 1,024-token prompt. The C4
    collector (``c4.py``) splits long-prompt vs ≤ 1,024-token C4 statistics on this tag alone —
    never on an exact token count — so the proxy token count below is reporting detail, not a key.
    """

    LONG = "long"
    SHORT = "short"


@dataclass(frozen=True, slots=True)
class PromptCorpus:
    """The long/short prompt-size policy a run draws from (frozen + slots, Req 14.3).

    ``long_min_tokens`` / ``long_max_tokens`` bound the long band (default 4,000–7,000);
    ``short_min_tokens`` / ``short_max_tokens`` bound the short band (default 1–1,024);
    ``chars_per_token`` is the documented size proxy (default ~4). All token figures are
    corpus-policy, not capacity positions. Use :func:`default_corpus` for the GW20b defaults.
    """

    long_min_tokens: int
    long_max_tokens: int
    short_min_tokens: int
    short_max_tokens: int
    chars_per_token: int


@dataclass(frozen=True, slots=True)
class PromptPick:
    """One per-request long-or-short prompt draw (frozen + slots, Req 4.2/4.3).

    ``tag`` is the long/short classification the C4 collector splits on; ``token_count`` is the
    prompt's proxy token count; ``body`` is a synthesized prompt body of ``token_count *
    chars_per_token`` characters (the request input the driver sends). ``is_long`` is a convenience
    mirror of ``tag is PromptTag.LONG``.
    """

    tag: PromptTag
    token_count: int
    body: str

    @property
    def is_long(self) -> bool:
        """True iff this pick is a long (4,000–7,000-token) prompt."""
        return self.tag is PromptTag.LONG


def default_corpus() -> PromptCorpus:
    """The GW20b default corpus: 4,000–7,000-token long band, 1–1,024-token short band, ~4 c/tok."""
    return PromptCorpus(
        long_min_tokens=_LONG_PROMPT_MIN_TOKENS,
        long_max_tokens=_LONG_PROMPT_MAX_TOKENS,
        short_min_tokens=_SHORT_PROMPT_MIN_TOKENS,
        short_max_tokens=_SHORT_PROMPT_MAX_TOKENS,
        chars_per_token=_CHARS_PER_TOKEN,
    )


# A module-level NAME bound to a frozen (immutable) value is not module-level MUTABLE state; it is a
# constant default, the same shape as a frozen dataclass default elsewhere in the tree.
DEFAULT_CORPUS = default_corpus()


def _make_body(token_count: int, *, chars_per_token: int) -> str:
    """Synthesize a prompt body of ``token_count * chars_per_token`` filler characters.

    The body length realizes the proxy (``len(body) / chars_per_token == token_count``); content is
    a single repeated ASCII char so generation is O(length) and carries no secret-shaped bytes.
    """
    char_count = max(token_count, 0) * max(chars_per_token, 1)
    return _BODY_FILL_CHAR * char_count


def pick(corpus: PromptCorpus, share: float, rng: random.Random) -> PromptPick:
    """Draw one prompt — long with probability ``share``, else short — from ``corpus`` via ``rng``.

    Pure per-request draw (Req 4.2). A single Bernoulli trial on ``rng.random() < share`` picks
    the long band; the token count is then drawn uniformly within the selected band and the body is
    synthesized at ``token_count * corpus.chars_per_token`` characters. Over many independent draws
    the realized long-share converges to ``share`` (the Bernoulli mean) — the convergence
    *tolerance* itself lives in ``config.Tolerances.share_convergence``; this function only draws
    correctly.

    ``share`` is clamped to ``[0.0, 1.0]`` defensively; a share of ``0.0`` never draws long and
    ``1.0`` always draws long. ``share`` is expected to be one of the sanctioned 1/5/25 % values
    when the stratum is enabled (``config`` snaps it), but any probability in range draws correctly.
    """
    clamped = min(max(share, 0.0), 1.0)
    if rng.random() < clamped:
        token_count = rng.randint(corpus.long_min_tokens, corpus.long_max_tokens)
        tag = PromptTag.LONG
    else:
        token_count = rng.randint(corpus.short_min_tokens, corpus.short_max_tokens)
        tag = PromptTag.SHORT
    body = _make_body(token_count, chars_per_token=corpus.chars_per_token)
    return PromptPick(tag=tag, token_count=token_count, body=body)
