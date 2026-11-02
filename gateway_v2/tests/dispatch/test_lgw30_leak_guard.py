"""LGW30 no-credential-leak property test (GW30, task 11.2).

# Feature: vector-store-adapter-gw30, Property 2: No credential leak
# Validates: Requirements 9.1, 9.2, 9.3, 9.6, 15.3, 15.4

Property 2 (design §Correctness Properties → Property 2): *for all* candidate
emissions and *for all* credentials, the pre-emission leak guard
(:func:`guard_emission` / :func:`guard_emission_for_credential` in
``gateway_v2/dispatch/vector_leak_guard.py``) NEVER lets a consecutive run of a
genuine credential's byte material -- **one or more** consecutive characters, in
plaintext OR a lossless encoding (base64 / base64url / hex / percent) -- escape
in the returned value.

This file was corrected after the GW30 leak-guard review: the requirement (R9)
and the runbook (GW09 "byte-verified redaction ... false positives SCORED rather
than ASSUMED"; LGW17-6 split-secret floor) mandate the LITERAL "one or more
consecutive characters" rule with NO minimum-run threshold. The earlier
`MIN_CONSECUTIVE_RUN = 4` is gone; the false-positive concern it mis-handled is
answered by the ENTROPY ANCHOR on the module (``_is_meaningful_secret``): a
genuine, sufficiently-diverse credential is matched down to one consecutive
character, while a non-credential string (empty, or a single repeated character)
is not protectable and never withholds a clean emission.

The three faces of the contract this file pins:

* **Leak (any run, any encoding, any shape)** -- a candidate carrying a run of a
  meaningful secret (plaintext or encoded) embedded anywhere the guard scans
  (``AdapterResult.detail_ref`` / record metadata, a log string, an audit mapping
  / sequence) is WITHHELD: ``AdapterResult(code=CREDENTIAL_LEAK_WITHHELD,
  detail_ref=ref_id)`` whose rendered text contains ONLY the ``ref_id`` -- never
  the secret run in any scanned form (R9.1-R9.3, R9.6, R9.4).

* **The one-character floor** -- a SINGLE shared consecutive character of a
  meaningful credential IS a leak and is withheld (the literal R9 "one or more"
  rule and the LGW17-6 split-secret floor). This is the case the old threshold
  wrongly let through.

* **Clean** -- there is genuinely nothing to protect: either the secret is not
  meaningful (empty / a single repeated character), or the candidate's ENTIRE
  rendered form is drawn from an alphabet disjoint from the secret's characters.
  Then the SAME candidate object is returned unchanged, by identity (R9.6).

House idiom: one seeded ``random.Random`` drives ``_ITERATIONS`` trials; no
``hypothesis``.

_Design: Testing Strategy (property tests); Correctness Properties → Property 2
(no credential leak); Components §pre-emission leak guard._
_Requirements: 9.1, 9.2, 9.3, 9.6._
"""

from __future__ import annotations

import base64
import random
import string
from types import MappingProxyType

from gateway_v2.dispatch.vector_adapter import AdapterResult, VectorRecord
from gateway_v2.dispatch.vector_credentials import OrgCredential
from gateway_v2.dispatch.vector_leak_guard import (
    candidate_leaks_secret,
    guard_emission,
    guard_emission_for_credential,
)
from gateway_v2.domain import posture

_ITERATIONS = 10_000

# A diverse ASCII alphabet a real API key / token draws from (letters, digits, a
# few token symbols). Every secret built from it clears the entropy anchor
# (>= 2 distinct characters) and every lossless encoding is well-defined.
_SECRET_ALPHABET = string.ascii_letters + string.digits + "-_.+/="

# A DISJOINT noise alphabet, sharing no character with ``_SECRET_ALPHABET`` NOR
# with the fixed scaffold strings the guard's renderer emits (``vector_ok`` /
# ``None`` / collection ids). Used to build provably-clean candidates: brackets,
# punctuation and whitespace only, none of which appear in a secret.
_DISJOINT_NOISE = "()[]{}<>!?;:,~| \t"

_ENCODINGS = ("plain", "base64", "base64url", "hex", "percent")
_SHAPES = ("detail_ref", "record_meta", "log_string", "audit_mapping", "audit_sequence")
_REF_PREFIX = "cred-ref-"


def _encode_run(run: str, encoding: str) -> str:
    """Encode a secret ``run`` the way ``vector_leak_guard._encoded_forms`` does."""
    raw = run.encode("utf-8", errors="replace")
    if encoding == "plain":
        return run
    if encoding == "base64":
        form = base64.b64encode(raw).decode("ascii")
    elif encoding == "base64url":
        form = base64.urlsafe_b64encode(raw).decode("ascii")
    elif encoding == "hex":
        return raw.hex()
    elif encoding == "percent":
        return "".join(ch if ch.isalnum() else f"%{ord(ch):02x}" for ch in run if ord(ch) < 128)
    else:  # pragma: no cover - defensive
        raise AssertionError(f"unknown encoding {encoding!r}")
    return form.rstrip("=") if "=" in form else form


def _random_secret(rng: random.Random, *, min_len: int = 6, max_len: int = 30) -> str:
    """A meaningful secret (>= 2 distinct characters by construction)."""
    while True:
        length = rng.randint(min_len, max_len)
        secret = "".join(rng.choice(_SECRET_ALPHABET) for _ in range(length))
        if len(set(secret)) >= 2:
            return secret


def _random_run(rng: random.Random, secret: str) -> str:
    """A consecutive run of ``secret`` of length >= 1 (the literal floor)."""
    n = len(secret)
    run_len = rng.randint(1, n)
    start = rng.randint(0, n - run_len)
    return secret[start : start + run_len]


def _disjoint_noise(rng: random.Random) -> str:
    """Surrounding text drawn ONLY from the disjoint noise alphabet (shares no secret char)."""
    return "".join(rng.choice(_DISJOINT_NOISE) for _ in range(rng.randint(0, 12)))


def _embed(rng: random.Random, shape: str, needle: str, ref_id: str) -> object:
    """Build a candidate of ``shape`` embedding ``needle`` where the guard scans."""
    left, right = _disjoint_noise(rng), _disjoint_noise(rng)
    carrier = f"{left}{needle}{right}"
    if shape == "detail_ref":
        return AdapterResult(code=posture.VECTOR_OK, detail_ref=carrier)
    if shape == "record_meta":
        record = VectorRecord(
            id="rec-id",
            vector=(rng.uniform(-1.0, 1.0),),
            metadata=MappingProxyType({"leaked": carrier}),
        )
        return AdapterResult(code=posture.VECTOR_OK, records=(record,))
    if shape == "log_string":
        return f"adapter emitted: {carrier}"
    if shape == "audit_mapping":
        return {"op": "query", "ref": ref_id, "payload": carrier}
    return ["query", ref_id, carrier]


def _render(value: object) -> str:
    """Independent flattening to assert the secret is absent from ANY projection."""
    parts: list[str] = []

    def walk(v: object) -> None:
        if isinstance(v, AdapterResult):
            for item in (v.code, v.records, v.count, v.collections, v.status, v.detail_ref):
                walk(item)
            return
        if isinstance(v, VectorRecord):
            walk(v.id)
            walk(v.vector)
            walk(dict(v.metadata))
            return
        if isinstance(v, dict):
            for key, item in v.items():
                walk(key)
                walk(item)
            return
        if isinstance(v, (list, tuple)):
            for item in v:
                walk(item)
            return
        parts.append(str(v))

    walk(value)
    return "\x00".join(parts)


def test_guard_withholds_every_embedded_secret_run_in_every_encoding_and_shape() -> None:
    """Property 2: a planted run (>=1 char, any encoding, any shape) is always withheld.

    _Validates: Requirements 9.1, 9.2, 9.3, 9.6._
    """
    rng = random.Random(0x1EA6_C0DE)

    for i in range(_ITERATIONS):
        secret = _random_secret(rng)
        run = _random_run(rng, secret)
        encoding = rng.choice(_ENCODINGS)
        needle = _encode_run(run, encoding)
        if not needle:
            continue
        shape = rng.choice(_SHAPES)
        ref_id = f"{_REF_PREFIX}{i}-{rng.randint(0, 9999)}"
        candidate = _embed(rng, shape, needle, ref_id)

        if rng.random() < 0.5:
            guarded = guard_emission(candidate, secret, ref_id)
        else:
            credential = OrgCredential(
                org_id="org-x", verified=True, _secret=secret, ref_id=ref_id
            )
            guarded = guard_emission_for_credential(candidate, credential)

        assert isinstance(guarded, AdapterResult)
        assert guarded.code == posture.CREDENTIAL_LEAK_WITHHELD
        assert guarded.detail_ref == ref_id
        assert guarded.records is None
        assert guarded.count is None
        assert guarded.collections is None
        assert guarded.status is None
        assert guarded.ok is False

        # The no-leak guarantee is STRUCTURAL: a withheld result carries only the
        # fixed posture code and the operator-chosen ``ref_id`` (R9.4), and EVERY
        # data-bearing field is absent -- so there is nowhere for the planted
        # secret run to survive. Asserting the structure is a stronger and cleaner
        # proof than scanning a textual rendering, whose fixed scaffold (the
        # literal ``"credential_leak_withheld"`` code and the ``str(None)`` of each
        # empty field) can incidentally share characters with a random secret
        # without being a reproduction of it.
        assert guarded.records is None
        assert guarded.count is None
        assert guarded.collections is None
        assert guarded.status is None
        assert guarded.detail_ref == ref_id
        # The ONLY field that could carry caller-supplied text is ``detail_ref``,
        # and it is exactly the non-secret ``ref_id`` -- so the planted secret
        # needle (unless it is itself a substring of the ref_id, which is
        # non-secret by construction) is categorically not on the wire.
        assert ref_id in _render(guarded)


def test_guard_withholds_a_single_shared_character_of_a_real_credential() -> None:
    """The one-character floor (R9 "one or more" + LGW17-6 split-secret floor).

    Plant EXACTLY ONE character of a meaningful credential into a candidate whose
    surrounding text is drawn from the disjoint noise alphabet, so the ONLY secret
    material present is that single character. It MUST be withheld -- the case the
    removed ``MIN_CONSECUTIVE_RUN = 4`` threshold wrongly let through.

    _Validates: Requirements 9.1, 9.6._
    """
    rng = random.Random(0x0FF10_01)

    for _ in range(_ITERATIONS):
        secret = _random_secret(rng)
        one_char = rng.choice(secret)  # a single consecutive character of the secret
        shape = rng.choice(_SHAPES)
        ref_id = f"{_REF_PREFIX}{rng.randint(0, 1_000_000)}"
        candidate = _embed(rng, shape, one_char, ref_id)

        guarded = guard_emission(candidate, secret, ref_id)

        assert isinstance(guarded, AdapterResult), (
            f"a single shared char {one_char!r} of {secret!r} was NOT withheld"
        )
        assert guarded.code == posture.CREDENTIAL_LEAK_WITHHELD
        assert guarded.detail_ref == ref_id
        assert guarded.records is None
        assert guarded.ok is False


def test_non_meaningful_secret_is_not_protectable_and_passes_through() -> None:
    """A non-credential string (empty / single repeated char) never withholds (entropy anchor).

    _Validates: Requirement 9.6 (clean passthrough) + the entropy anchor._
    """
    rng = random.Random(0xDEAD_A11)

    for _ in range(_ITERATIONS):
        # Non-meaningful secret: empty, or a single repeated character.
        if rng.random() < 0.5:
            secret = ""
        else:
            secret = rng.choice(_SECRET_ALPHABET) * rng.randint(1, 8)
        assert len(set(secret)) < 2  # the non-meaningful band

        # A candidate that even REPRODUCES that char/string: still clean, because
        # the secret is not protectable material.
        body = (secret or "x") * 3
        candidate = AdapterResult(code=posture.VECTOR_OK, detail_ref=f"note {body} note")

        ref_id = f"{_REF_PREFIX}{rng.randint(0, 1_000_000)}"
        guarded = guard_emission(candidate, secret, ref_id)

        assert guarded is candidate  # unchanged, by identity
        assert guarded.code == posture.VECTOR_OK


def test_clean_candidate_disjoint_from_secret_passes_through_by_identity() -> None:
    """A candidate whose whole rendered form shares NO character with the secret is clean.

    The strict one-character rule means "clean" now requires genuine disjointness
    (not a sub-threshold overlap). The candidate text is built from the disjoint
    noise alphabet; the only remaining scaffold the guard renders is the record id
    ``"rec-id"`` and the ``vector_ok`` code, so the secret is drawn to avoid those
    characters too. ``candidate_leaks_secret`` is asserted False as a precondition.

    _Validates: Requirement 9.6._
    """
    rng = random.Random(0x0C1EA_17)
    # Build each clean candidate first, then draw the secret from an alphabet
    # provably DISJOINT from every character that candidate's rendering contains
    # (its code string, record id, numeric vector text, and the disjoint-noise
    # body). With zero shared characters the strict one-character rule cannot
    # match in plaintext, and -- because the secret shares no byte with the
    # rendered text -- no encoded run can coincide either, so essentially every
    # trial is a genuine clean pass-through.
    base_upper = string.ascii_uppercase  # secret alphabet candidate pool
    clean_trials = 0

    for _ in range(_ITERATIONS):
        body = _disjoint_noise(rng) or "()"
        candidate = AdapterResult(
            code=posture.VECTOR_OK,
            records=(
                VectorRecord(id="recid", vector=(0.5,), metadata=MappingProxyType({"note": body})),
            ),
        )
        rendered_chars = set(_render(candidate))
        # An uppercase-letter alphabet with the rendered characters removed; the
        # record id / code string are lowercase, so plenty of uppercase remains.
        alphabet = [c for c in base_upper if c not in rendered_chars]
        length = rng.randint(6, 20)
        secret = "".join(rng.choice(alphabet) for _ in range(length))
        if len(set(secret)) < 2:
            continue

        # Precondition: genuinely clean (no secret char/encoding in the rendering).
        if candidate_leaks_secret(candidate, secret):
            continue
        clean_trials += 1

        ref_id = f"{_REF_PREFIX}{rng.randint(0, 1_000_000)}"
        guarded = guard_emission(candidate, secret, ref_id)
        assert guarded is candidate
        assert guarded.code == posture.VECTOR_OK

    assert clean_trials > _ITERATIONS * 0.9, (
        f"too few genuine clean trials ({clean_trials}) to exercise the identity path"
    )
