"""GW30 pre-emission credential leak guard (R9.1-R9.6).

This module owns the single emission boundary every ``Vector_Adapter``
result, log line, and audit field must pass through before it leaves the
connector. It is the mechanism behind the ``credential_leak_withheld`` posture
code: it scans the *candidate* emission for any byte material of the active
:class:`~gateway_v2.dispatch.vector_credentials.OrgCredential` secret and, on a
hit, **withholds the entire emission** and returns an
:class:`~gateway_v2.dispatch.vector_adapter.AdapterResult` carrying
``code == posture.CREDENTIAL_LEAK_WITHHELD`` whose ``detail_ref`` is the
credential's opaque ``ref_id`` -- never the secret itself.

It is deliberately a separate module from ``vector_credentials.py`` (the value
types + ``Credential_Source`` seam + scope guard) and ``vector_adapter.py`` (the
result/request value types + protocol + registry) so the emission boundary can
evolve without colliding with those layers; it imports both and sits at the same
``dispatch`` layer position.

Design (Components §pre-emission leak guard; Error Handling
``credential_leak_withheld``; requirements R9.1-R9.6):

* **What it scans.** The guard renders the candidate emission -- an
  ``AdapterResult``, a log line, an audit record / field, or any value a
  connector is about to emit or persist -- into a flat text view, then searches
  that view for *any consecutive run of one or more characters* of the secret.
  R9.1-R9.3 require excluding the secret "in both plaintext and any encoded or
  partially-masked form that reproduces one or more consecutive characters" --
  the LITERAL rule, with no minimum-run threshold: a single reproduced character
  of a genuine credential is a leak. The guard searches the rendered candidate
  for the secret in plaintext (down to a single shared character) AND for each
  contiguous run in its lossless encodings (base64 / base64url, hex,
  percent-encoding). Encoding the short *secret run* -- never the whole candidate
  -- keeps the encoded match anchored to real secret material (re-encoding an
  arbitrary candidate would false-positive on nearly every emission, since the
  base64/hex alphabet of any text reproduces most single characters). The false
  positives an ordinary single shared character would cause are handled the
  runbook's way (GW09: "scored rather than assumed") by the ENTROPY ANCHOR
  (:func:`_is_meaningful_secret`): only a genuine, sufficiently-diverse
  credential is treated as protectable, so a non-credential string (empty, or a
  single repeated character) never withholds a clean emission, while a real token
  is caught down to one consecutive character. Any match is a leak: the guard
  fails closed, withholding the whole emission rather than risking a secret on
  the wire.

* **What it emits on a hit.** The whole emission is withheld (never a redacted
  partial -- R9.6 says withhold the *entire* value) and the guard returns
  ``AdapterResult(code=CREDENTIAL_LEAK_WITHHELD, detail_ref=ref_id)``. The
  diagnostic detail is only the credential's reference id (R9.4); the secret is
  never serialized, logged, or returned (R9.5) -- mirroring the GW14c
  ``Finding.evidence``-is-never-serialized lesson in AGENTS.md: a reference id
  *locates* the affected credential without *reproducing* it.

* **What it returns when clean.** The candidate emission is returned unchanged
  (the same object, by identity) when the scan finds no credential byte
  material, so a clean path pays only the scan and is otherwise a no-op.

Purity / house conventions:

* Pure and stateless: no module-level mutable table, no clock, no randomness, no
  capacity literal. Every function is a pure function of its arguments.
* The secret is never logged and never placed in the returned value -- only the
  ``ref_id`` travels. The secret string handed in is used solely for the
  in-memory substring scan and is not retained anywhere.

Layering (import-linter ``layers`` contract): ``dispatch`` may import ``domain``
and below and its own ``dispatch`` siblings. This module imports only the
standard library, ``gateway_v2.domain.posture``, and the two ``dispatch``
sibling value modules, so it adds no forbidden layer edge.
"""

from __future__ import annotations

import base64
from collections.abc import Mapping, Sequence
from typing import Any

from gateway_v2.dispatch.vector_adapter import AdapterResult
from gateway_v2.dispatch.vector_credentials import OrgCredential
from gateway_v2.domain import posture

__all__ = (
    "candidate_leaks_secret",
    "guard_emission",
    "guard_emission_for_credential",
)

# Entropy anchoring (R9.1-R9.3, R9.6; runbook GW09 "false positives SCORED rather
# than ASSUMED", LGW17-6 split-secret floor). The requirement is literal: an
# emission reproducing **one or more consecutive characters** of a credential --
# plaintext or lossless-encoded -- is a leak and the whole emission is withheld.
# There is deliberately NO minimum-run threshold: a single reproduced character
# of a genuine credential is a leak (a run-length gate would lower the
# split-secret floor the runbook forbids). The earlier `MIN_CONSECUTIVE_RUN = 4`
# is removed for exactly that reason.
#
# The false-positive concern the old threshold tried to solve -- ordinary record
# text ("hello", a float "0.5") sharing single characters with a secret -- is
# answered the runbook's way: ANCHOR on whether the secret is genuine credential
# material, not on how many of its characters leaked. A string that carries no
# real protectable material (empty, or fewer than `_MIN_DISTINCT_SECRET_CHARS`
# distinct characters -- e.g. "aaaa") is not a credential, so there is nothing to
# withhold; a genuine, sufficiently-diverse token is matched down to a single
# consecutive character. This is a structural detection parameter (it gates WHAT
# is protectable, not a connection/queue/pool/buffer/worker budget), so it is NOT
# a capacity literal and does not belong in `runtime/resources.py`.
_MIN_DISTINCT_SECRET_CHARS = 2


def _is_meaningful_secret(secret: str) -> bool:
    """``True`` iff ``secret`` is genuine credential material worth protecting.

    The entropy anchor (see the module note): a secret with fewer than
    :data:`_MIN_DISTINCT_SECRET_CHARS` distinct characters -- the empty string, or
    a single repeated character such as ``"aaaa"`` -- carries no real protectable
    material, so matching it would only false-positive on ordinary text. A real,
    sufficiently-diverse token clears the floor and is then matched down to a
    single consecutive character (the literal R9 "one or more" rule). The anchor
    is on the SECRET's character diversity, never on how much of it leaked.
    """
    return len(set(secret)) >= _MIN_DISTINCT_SECRET_CHARS


# --------------------------------------------------------------------------- #
# Rendering the candidate emission into searchable text views.
# --------------------------------------------------------------------------- #


def _render_candidate(candidate: Any) -> str:
    """Flatten an arbitrary candidate emission into one searchable text view.

    The candidate may be an :class:`AdapterResult`, a log line (``str``), an
    audit record / field (a mapping or sequence of scalars), or any value a
    connector is about to emit. Every leaf scalar is stringified and the whole
    structure is concatenated into a single string so a secret split across
    fields -- or embedded inside one -- is still visible to the substring scan.

    The traversal never calls any user ``repr``/``to_dict`` that could hide
    bytes: it walks known container shapes (mappings, sequences, dataclass
    ``__slots__``) and falls back to ``str`` only for leaf scalars. An
    :class:`OrgCredential` leaf is rendered via its own redacted ``repr`` (its
    secret is already excluded there), so a credential object carried *by
    reference* in a payload does not itself count as a leak -- only its secret
    *bytes* reproduced in the surrounding emission do.
    """
    parts: list[str] = []
    _collect(candidate, parts)
    return "\x00".join(parts)


def _collect(value: Any, parts: list[str]) -> None:
    """Depth-first collect every leaf scalar's text into ``parts``."""
    if value is None or isinstance(value, bool):
        parts.append(str(value))
        return
    if isinstance(value, OrgCredential):
        # The credential's own repr is redacted; carrying the object by
        # reference is not a leak. Its secret bytes reproduced elsewhere ARE
        # caught by the surrounding traversal.
        parts.append(repr(value))
        return
    if isinstance(value, (str, int, float, bytes)):
        if isinstance(value, bytes):
            parts.append(value.decode("utf-8", errors="replace"))
        else:
            parts.append(str(value))
        return
    if isinstance(value, Mapping):
        for key, item in value.items():
            _collect(key, parts)
            _collect(item, parts)
        return
    if isinstance(value, Sequence):
        for item in value:
            _collect(item, parts)
        return
    slots = getattr(type(value), "__slots__", None)
    if slots is not None:
        for name in slots:
            _collect(getattr(value, name, None), parts)
        return
    # Last-resort leaf: stringify. Known value types above are handled
    # structurally; this only reaches genuinely opaque scalars.
    parts.append(str(value))


def _encoded_forms(run: str) -> tuple[str, ...]:
    """Return lossless encodings OF A SECRET RUN to search for in the candidate.

    R9.1-R9.3 require catching an "encoded or partially-masked form that
    reproduces one or more consecutive characters". The guard searches the
    **plaintext** candidate rendering for each secret run AND for each run's
    base64 / base64url / hex / percent encoding, so a secret that was *itself*
    encoded on its way into the emission is caught.

    This encodes the SECRET RUN (short, bounded by the credential) -- never the
    whole candidate. Re-encoding an arbitrary candidate and searching it for the
    plaintext run would false-positive on nearly every emission, because the
    base64/hex alphabet of any text reproduces most single characters; encoding
    the run instead keeps the match anchored to real secret material. These are
    pure transforms; no external I/O.
    """
    raw = run.encode("utf-8", errors="replace")
    percent = "".join(
        ch if ch.isalnum() else f"%{ord(ch):02x}" for ch in run if ord(ch) < 128
    )
    forms = (
        run,
        base64.b64encode(raw).decode("ascii"),
        base64.urlsafe_b64encode(raw).decode("ascii"),
        raw.hex(),
        percent,
    )
    # A short base64 encoding carries ``=`` padding the surrounding emission need
    # not reproduce; strip it so an unpadded in-stream encoding still matches.
    return tuple(form.rstrip("=") if "=" in form else form for form in forms)


def _secret_needles(secret: str) -> frozenset[str]:
    """Every searchable needle derived from a genuine ``secret`` (R9, literal rule).

    A leak is **one or more consecutive characters** of the credential, in
    plaintext or a lossless encoding, so for a meaningful secret every contiguous
    run of ``secret`` down to length ONE contributes a needle (its plaintext plus
    :func:`_encoded_forms`). There is no minimum-run threshold -- a single
    reproduced character of a genuine token is a leak, preserving the runbook's
    split-secret floor.

    Entropy anchor: a secret that is not genuine credential material
    (:func:`_is_meaningful_secret` is ``False`` -- empty, or a single repeated
    character) yields NO needles, so it never spuriously withholds a clean
    emission. Generating runs down to length one is O(n^2) in needles; the hot
    path in :func:`candidate_leaks_secret` short-circuits on the cheap
    single-character overlap first and only materialises these needles to confirm
    an encoded reproduction, so the quadratic set is built rarely.
    """
    if not _is_meaningful_secret(secret):
        return frozenset()
    n = len(secret)
    needles: set[str] = set()
    for start in range(n):
        for end in range(start + 1, n + 1):
            needles.update(_encoded_forms(secret[start:end]))
    return frozenset(needle for needle in needles if needle)


# --------------------------------------------------------------------------- #
# The scan + the guard.
# --------------------------------------------------------------------------- #


def candidate_leaks_secret(candidate: Any, secret: str) -> bool:
    """``True`` iff any consecutive run of a genuine ``secret`` appears in ``candidate``.

    The literal R9 rule: for a meaningful credential (:func:`_is_meaningful_secret`)
    a reproduction of **one or more consecutive characters** -- in plaintext or a
    lossless encoding (:func:`_encoded_forms`) -- is a leak. A non-meaningful
    secret (empty, or a single repeated character) has nothing to protect and
    never reports a leak.

    Fast path: the rendered candidate is first checked for ANY single character of
    the secret (a length-1 consecutive run in plaintext); if none of the secret's
    characters appears, no longer plaintext run can either, so the result is clean
    without building the O(n^2) needle set. Only when a plaintext character does
    overlap -- or to catch an encoded-only reproduction -- are the full needles
    (every run plus its encodings) materialised and searched.
    """
    if not _is_meaningful_secret(secret):
        return False
    rendered = _render_candidate(candidate)
    # Plaintext fast path: any single shared secret character is already a
    # one-consecutive-character reproduction and a leak (R9 literal floor).
    if set(secret) & set(rendered):
        return True
    # No plaintext overlap: the only remaining way to leak is an ENCODED
    # reproduction (base64/hex/percent of a run), so confirm against the needles.
    needles = _secret_needles(secret)
    return any(needle in rendered for needle in needles)


def guard_emission(candidate: Any, secret: str, ref_id: str) -> AdapterResult | Any:
    """Return ``candidate`` unchanged when clean, else withhold the whole emission (R9.6).

    This is the single emission boundary. Before a connector returns a result,
    writes a log line, or contributes an audit field, it routes the candidate
    through here with the active credential's ``secret`` and ``ref_id``:

    * **Clean** -- no consecutive run of the secret appears in the candidate (nor
      in its lossless encodings): the *same* ``candidate`` object is returned
      unchanged (by identity), so a clean path is a no-op beyond the scan.
    * **Leak** -- a consecutive run of the secret is detected: the ENTIRE
      candidate is withheld (never a redacted partial) and an
      ``AdapterResult(code=CREDENTIAL_LEAK_WITHHELD, detail_ref=ref_id)`` is
      returned. The only detail is the opaque reference id (R9.4); the secret is
      never serialized, logged, or returned (R9.5).

    The function is pure: it holds no state, retains neither the secret nor the
    candidate, and performs no I/O.
    """
    if candidate_leaks_secret(candidate, secret):
        return AdapterResult(
            code=posture.CREDENTIAL_LEAK_WITHHELD,
            detail_ref=ref_id,
        )
    return candidate


def guard_emission_for_credential(
    candidate: Any,
    credential: OrgCredential,
) -> AdapterResult | Any:
    """Convenience over :func:`guard_emission` sourcing the secret + ref id from a credential.

    Reads the secret material and the opaque ``ref_id`` off the resolved
    :class:`OrgCredential` so a caller holding the credential need not unpack it.
    The credential's secret is used only for the in-memory scan and never
    reaches the returned value: on a leak only ``credential.ref_id`` travels
    (R9.4), and the credential object itself is never serialized (R9.5).
    """
    return guard_emission(candidate, credential._secret, credential.ref_id)
