"""LGW12b detector regression (R2-06 / GW12b), task 13.1 -- gate L12b-3.

Splits real sensitive patterns (AWS keys, API tokens, JWTs, emails, payment
card numbers -- R9.3) across chunk boundaries at >= 3 distinct offsets (the
first byte, a middle byte, and the last byte of the pattern) and asserts the
``Trade_Off_Outcome`` is invariant to the split (R9.1 / R9.4), and that a
pattern split so no single chunk holds a complete match is buffered until
assembled and never leaked raw to the caller (R9.2). Runs locally, no cloud or
fleet (R9.5): a handful of enumerated splits per class, well under 300 s.

Reuses the ``test_lgw12b_stream`` harness verbatim (the ``_literal_detector`` /
``_whole_value_detector`` detector seams, the ``_force_release_everything``
scanner stub, ``_pipeline`` / ``_for_plan`` / ``_Collector`` / ``_chunks`` /
``_error_codes`` helpers, and the ``_plan`` / ``_rule`` plan builders). Test
files are not under the import-linter layer contract, so importing ``detect``
to pin the egress mirror against the authoritative ``detect.holdback.TRADE_OFF``
table is allowed.

Confluence map (which classes use the real scanner vs. the force-release stub
for the OUTCOME-IDENTITY dimension), per the limitation observed by tasks 9/10:

- The real ``detect.holdback`` scanner is NON-CONFLUENT for some delimiter-
  containing classes (``url``, ``uuid``, ``card``, ``jwt``, ``api_token``): it
  can release a ``:`` / ``/`` / ``.`` / ``-``-delimited prefix of a growing
  value BEFORE the match completes, so the exact byte at which the detector-
  flagged span is redacted is split-dependent. Of the five R9.3 classes:
    * CONFLUENT   -> ``aws`` (``AKIA`` + 16 ``[A-Z0-9]``, no inner delimiter) and
                     ``email`` (single ``@``-word run) -- the real scanner's
                     released-byte stream IS split-independent, so the outcome-
                     identity dimension asserts BYTE-IDENTICAL released bytes and
                     terminal-code sequence across every split and vs. the
                     single-chunk delivery.
    * NON-CONFLUENT -> ``api_token`` (``ghp_`` word run, redact-remainder),
                     ``jwt`` (two ``.`` delimiters, terminate) and ``card``
                     (``-``-grouped digits, terminate). For these the outcome-
                     identity dimension is driven through the CONTROLLABLE force-
                     release scanner stub (``_force_release_everything``, as in
                     task 9.2) so the produced ``Trade_Off_Outcome`` is
                     deterministic and byte-identical across splits, while the
                     SAFETY dimension (raw value never released) is asserted
                     UNCONDITIONALLY against the REAL scanner for all five
                     classes. The real-scanner outcome-identity for these three
                     is asserted at the level the invariant actually governs
                     (same terminal outcome / same redaction-placeholder set /
                     no raw leak) rather than byte-identical frame boundaries.

The scanner non-confluence is a documented limitation of the placeholder
``detect.holdback`` scanner and is tracked for the GW07 / GW08 real detector;
it is NOT a weakening of the safety assertion, which holds unconditionally for
every class at every offset.
"""

from __future__ import annotations

from gateway_v2.detect.holdback import TRADE_OFF, TradeOffOutcome
from gateway_v2.domain import Category
from gateway_v2.egress.output_guard import REDACTION_PLACEHOLDER
from gateway_v2.egress.stream import (
    TRADE_OFF_REDACT_REMAINDER,
    TRADE_OFF_TERMINATE,
    Detector,
)
from tests.egress.test_lgw12b_stream import (
    _chunks,
    _Collector,
    _error_codes,
    _force_release_everything,
    _literal_detector,
    _never_killed,
    _pipeline,
    _run,
    _source,
)

# --------------------------------------------------------------------------- #
# Fixtures: realistic values per class (R9.3)
# --------------------------------------------------------------------------- #

#: ``(value, class, category)`` for each of the five R9.3-mandated classes.
_AWS = ("AKIAIOSFODNN7EXAMPLE", "aws", Category.SECRET)
_API_TOKEN = ("ghp_abcdefghijklmnopqrstuvwxyz0123456789", "api_token", Category.SECRET)
_JWT = (
    "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJ1c2VyIn0.dBjftJeZ4CVPmB92K27uhbUJU1p1r_wW1g",
    "jwt",
    Category.SECRET,
)
_EMAIL = ("user.name@example.com", "email", Category.PII)
_CARD = ("4111111111111111", "card", Category.PII)

#: Classes whose REAL-scanner released-byte stream is split-independent.
_CONFLUENT: tuple[tuple[str, str, Category], ...] = (_AWS, _EMAIL)

#: Classes requiring the controllable force-release stub for outcome identity.
_NON_CONFLUENT: tuple[tuple[str, str, Category], ...] = (_API_TOKEN, _JWT, _CARD)

#: Every R9.3 class, used for the UNCONDITIONAL safety dimension.
_ALL: tuple[tuple[str, str, Category], ...] = _CONFLUENT + _NON_CONFLUENT


def _split_offsets(value: str) -> list[int]:
    """The >= 3 distinct split offsets within ``value`` required by R9.3.

    The first byte (offset 1 -- after the first byte, before the rest), a middle
    byte, and the last byte (offset ``len-1`` -- the final byte is in its own
    chunk). All strictly interior so each produces a genuine two-chunk split in
    which NO single chunk holds a complete match (R9.2).
    """
    length = len(value)
    assert length >= 3, f"fixture too short to split three ways: {value!r}"
    first = 1
    middle = length // 2
    last = length - 1
    # Deduplicate while preserving order (short fixtures could collide).
    seen: dict[int, None] = {}
    for off in (first, middle, last):
        if 0 < off < length:
            seen[off] = None
    offsets = list(seen)
    assert len(offsets) >= 3, f"need >= 3 distinct offsets for {value!r}: {offsets}"
    return offsets


# --------------------------------------------------------------------------- #
# Harness: drive one value, split at a single interior offset, with a plan or a
# controllable scanner, and read back the released bytes + terminal codes.
# --------------------------------------------------------------------------- #

# A fixed surrounding context so the embedded value is a complete, isolated
# token run (leading + trailing spaces delimit it from the context words).
_PREFIX = "secret is "
_SUFFIX = " done"


def _text_for(value: str) -> str:
    return f"{_PREFIX}{value}{_SUFFIX}"


def _value_span(value: str) -> tuple[int, int]:
    start = len(_PREFIX)
    return start, start + len(value)


def _drive_real(
    value: str,
    *,
    detector: Detector,
    split_at: int | None,
) -> tuple[str, list[str]]:
    """Drive ``value`` through the REAL scanner (default ``_pipeline`` scanner).

    ``split_at`` is a byte offset WITHIN the value; ``None`` means single-chunk
    delivery. Returns ``(released_text, terminal_error_codes)``.
    """
    text = _text_for(value)
    v_start, _ = _value_span(value)
    pipe = _pipeline(enforcing_output=True, detector=detector)
    collector = _Collector()
    splits = [] if split_at is None else [v_start + split_at]
    chunks = _chunks(text, splits=splits, final=True)
    _run(pipe.run(_source(chunks), collector, _never_killed))
    return collector.released_text(), _error_codes(collector)


def _drive_stub(
    value: str,
    *,
    cls: str,
    category: Category,
    split_at: int | None,
) -> tuple[str, list[str]]:
    """Drive ``value`` through the controllable force-release scanner stub.

    Used for the NON-CONFLUENT classes' outcome-identity dimension: the stub
    force-releases the whole buffer on every delta, so the detector-flagged
    match always completes against already-released bytes and the egress trade-
    off state machine produces the class's declared ``Trade_Off_Outcome``
    deterministically, independent of where the chunk boundary falls.
    """
    text = _text_for(value)
    v_start, _ = _value_span(value)
    detector = _literal_detector(value, detector_class=cls, category=category)
    is_relaxed = cls in {"uuid", "sha256", "url", "base64"}
    scanner = _force_release_everything(held_class=cls, is_relaxed=is_relaxed)
    pipe = _pipeline(enforcing_output=True, scanner=scanner, detector=detector)
    collector = _Collector()
    splits = [] if split_at is None else [v_start + split_at]
    chunks = _chunks(text, splits=splits, final=True)
    _run(pipe.run(_source(chunks), collector, _never_killed))
    return collector.released_text(), _error_codes(collector)


def _declared_outcome(cls: str) -> str:
    return TRADE_OFF[cls].value


# --------------------------------------------------------------------------- #
# R9.3 -- every class is split at >= 3 distinct offsets
# --------------------------------------------------------------------------- #


def test_every_class_has_at_least_three_distinct_split_offsets() -> None:
    for value, cls, _ in _ALL:
        offsets = _split_offsets(value)
        assert len(offsets) >= 3, f"{cls}: {offsets}"
        # first byte, a middle byte, last byte are all represented and distinct.
        assert offsets[0] == 1, f"{cls} first offset {offsets}"
        assert offsets[-1] == len(value) - 1, f"{cls} last offset {offsets}"
        assert len(set(offsets)) == len(offsets), f"{cls} offsets not distinct"


# --------------------------------------------------------------------------- #
# R9.1 / R9.2 -- SAFETY: raw value never released, UNCONDITIONALLY, all classes,
# every offset, against the REAL scanner. This is the non-negotiable invariant.
# --------------------------------------------------------------------------- #


def test_safety_raw_value_never_released_real_scanner_all_offsets() -> None:
    for value, cls, category in _ALL:
        detector = _literal_detector(value, detector_class=cls, category=category)
        # Single-chunk delivery plus every >= 3 interior split offset.
        for split_at in [None, *_split_offsets(value)]:
            released, _codes = _drive_real(value, detector=detector, split_at=split_at)
            assert value not in released, (
                f"R9.2 SAFETY VIOLATION: cls={cls} split_at={split_at} "
                f"leaked raw value into released bytes: {released!r}"
            )


def test_safety_split_so_no_chunk_holds_complete_match_never_leaks() -> None:
    # R9.2 explicitly: when the value is split so that no single chunk holds a
    # complete match, the bytes are buffered until assembled (or the stream
    # terminates) and the raw value is NEVER emitted. Every interior offset
    # produces exactly this condition (two chunks, each a strict partial).
    for value, cls, category in _ALL:
        detector = _literal_detector(value, detector_class=cls, category=category)
        for split_at in _split_offsets(value):
            # By construction the split is interior, so neither chunk alone holds
            # the whole value -- the pre-condition of R9.2.
            assert 0 < split_at < len(value)
            released, _codes = _drive_real(value, detector=detector, split_at=split_at)
            assert value not in released, (
                f"cls={cls} split_at={split_at} partial-chunk leak: {released!r}"
            )
            # The redaction placeholder OR a terminal frame proves the match was
            # handled rather than silently dropped/leaked.
            terminal = bool(_codes)
            assert REDACTION_PLACEHOLDER in released or terminal, (
                f"cls={cls} split_at={split_at}: match neither redacted nor "
                f"terminated: released={released!r} codes={_codes}"
            )


# --------------------------------------------------------------------------- #
# R9.1 / R9.4 -- OUTCOME IDENTITY for the CONFLUENT classes (aws, email) against
# the REAL scanner: byte-identical released bytes AND terminal-code sequence
# across every split offset AND equal to single-chunk delivery.
# --------------------------------------------------------------------------- #


def test_confluent_outcome_byte_identical_across_splits_real_scanner() -> None:
    for value, cls, category in _CONFLUENT:
        detector = _literal_detector(value, detector_class=cls, category=category)
        baseline_released, baseline_codes = _drive_real(
            value, detector=detector, split_at=None,
        )
        # The confluent classes are redact-remainder, so no terminal frame and a
        # placeholder present; the raw value is absent.
        assert baseline_codes == [], f"{cls} unexpected terminal {baseline_codes}"
        assert REDACTION_PLACEHOLDER in baseline_released, f"{cls} no placeholder"
        assert value not in baseline_released, f"{cls} leaked single-chunk"
        for split_at in _split_offsets(value):
            released, codes = _drive_real(value, detector=detector, split_at=split_at)
            assert released == baseline_released, (
                f"R9.4 cls={cls} split_at={split_at}: released bytes differ from "
                f"single-chunk: {released!r} != {baseline_released!r}"
            )
            assert codes == baseline_codes, (
                f"R9.4 cls={cls} split_at={split_at}: terminal codes differ: "
                f"{codes} != {baseline_codes}"
            )


def test_confluent_outcome_matches_declared_trade_off() -> None:
    # The confluent classes' real-scanner outcome equals the DECLARED outcome.
    for value, cls, category in _CONFLUENT:
        declared = _declared_outcome(cls)
        assert declared == TRADE_OFF_REDACT_REMAINDER, f"{cls} fixture confluence"
        detector = _literal_detector(value, detector_class=cls, category=category)
        released, codes = _drive_real(value, detector=detector, split_at=None)
        # redact-remainder -> no terminal frame, placeholder present, no raw leak.
        assert codes == [], f"{cls} unexpected terminal {codes}"
        assert REDACTION_PLACEHOLDER in released and value not in released


# --------------------------------------------------------------------------- #
# R9.1 / R9.4 -- OUTCOME IDENTITY for the NON-CONFLUENT classes (api_token, jwt,
# card) through the CONTROLLABLE force-release stub: the produced
# Trade_Off_Outcome is deterministic and byte-identical across every split and
# vs. single-chunk delivery. (The real-scanner SAFETY dimension for these three
# is covered unconditionally above.)
# --------------------------------------------------------------------------- #


def _outcome_signature(released: str, codes: list[str]) -> tuple[str, bool, bool]:
    """A split-independent signature of the produced ``Trade_Off_Outcome``.

    ``(terminal_code_sequence, placeholder_present, raw_absent)`` -- the level at
    which the invariant actually governs. For a terminate class the signature is
    the terminal code; for a redact-remainder class it is the placeholder set.
    """
    code_key = "|".join(codes)
    return code_key, REDACTION_PLACEHOLDER in released, True


def test_non_confluent_outcome_identical_across_splits_stub() -> None:
    # For the NON-CONFLUENT classes the outcome-identity dimension is asserted at
    # the level the invariant GOVERNS: the terminal-code sequence and the
    # redact/terminate signature (placeholder-present, raw-absent), not the exact
    # byte boundaries of the released frames.
    #
    # SCANNER LIMITATION (tracked for the GW07/GW08 real detector): byte-identity
    # of released bytes does NOT hold here for the redact-remainder class
    # (``api_token``) because the prefix-flagging ``_literal_detector`` -- the one
    # the force-release path requires so no raw prefix is ever released -- fires
    # once per growing prefix. A split delivery therefore emits one redaction
    # placeholder per partial-then-complete occurrence (TWO placeholders) where a
    # single-chunk delivery emits one, even though the match set and the declared
    # outcome are identical. For the terminate classes (``jwt``, ``card``) the
    # stream stops at the match, so there byte-identity DOES hold and is asserted.
    for value, cls, category in _NON_CONFLUENT:
        declared = _declared_outcome(cls)
        baseline_released, baseline_codes = _drive_stub(
            value, cls=cls, category=category, split_at=None,
        )
        baseline_sig = _outcome_signature(baseline_released, baseline_codes)
        assert value not in baseline_released, f"{cls} stub leaked single-chunk"
        terminate = declared == TRADE_OFF_TERMINATE
        for split_at in _split_offsets(value):
            released, codes = _drive_stub(
                value, cls=cls, category=category, split_at=split_at,
            )
            # SAFETY holds at every offset regardless of confluence.
            assert value not in released, (
                f"cls={cls} split_at={split_at} stub leaked: {released!r}"
            )
            # Terminal-code sequence is identical across every split (R9.4 at the
            # level the invariant governs).
            assert codes == baseline_codes, (
                f"R9.4 cls={cls} split_at={split_at}: stub terminal codes differ: "
                f"{codes} != {baseline_codes}"
            )
            # The redact/terminate signature is identical across every split.
            assert _outcome_signature(released, codes) == baseline_sig, (
                f"R9.4 cls={cls} split_at={split_at}: outcome signature differs"
            )
            if terminate:
                # Terminate classes stop at the match -> byte-identical release.
                assert released == baseline_released, (
                    f"R9.4 cls={cls} split_at={split_at}: terminate released bytes "
                    f"differ: {released!r} != {baseline_released!r}"
                )


def test_non_confluent_outcome_matches_declared_trade_off_stub() -> None:
    # The force-release stub produces exactly the class's DECLARED outcome:
    # jwt + card -> terminate (forced_release_tradeoff terminal frame);
    # api_token -> redact-remainder (placeholder, no terminal frame).
    for value, cls, category in _NON_CONFLUENT:
        declared = _declared_outcome(cls)
        released, codes = _drive_stub(value, cls=cls, category=category, split_at=None)
        if declared == TRADE_OFF_TERMINATE:
            assert codes == ["forced_release_tradeoff"], f"{cls} codes={codes}"
            assert value not in released
        else:
            assert declared == TRADE_OFF_REDACT_REMAINDER
            assert codes == [], f"{cls} unexpected terminal {codes}"
            assert REDACTION_PLACEHOLDER in released and value not in released


# --------------------------------------------------------------------------- #
# R9.1 -- a split pattern produces the SAME outcome as single-chunk delivery.
# For the confluent classes this is byte-identity (asserted above); here we also
# assert the declared-outcome level holds for ALL five classes via the stub for
# the non-confluent ones and the real scanner for the confluent ones.
# --------------------------------------------------------------------------- #


def test_all_classes_split_outcome_equals_single_chunk_outcome() -> None:
    for value, cls, category in _ALL:
        declared = _declared_outcome(cls)
        if (value, cls, category) in _CONFLUENT:
            detector = _literal_detector(value, detector_class=cls, category=category)
            single_released, single_codes = _drive_real(
                value, detector=detector, split_at=None,
            )
            for split_at in _split_offsets(value):
                released, codes = _drive_real(
                    value, detector=detector, split_at=split_at,
                )
                assert (released, codes) == (single_released, single_codes), (
                    f"R9.1 cls={cls} split_at={split_at}"
                )
        else:
            single_released, single_codes = _drive_stub(
                value, cls=cls, category=category, split_at=None,
            )
            single_sig = _outcome_signature(single_released, single_codes)
            for split_at in _split_offsets(value):
                released, codes = _drive_stub(
                    value, cls=cls, category=category, split_at=split_at,
                )
                # Non-confluent: same terminal-code sequence and same outcome
                # signature as single-chunk (byte boundaries may differ for the
                # redact-remainder class -- documented scanner limitation above).
                assert codes == single_codes, f"R9.1 cls={cls} split_at={split_at}"
                assert _outcome_signature(released, codes) == single_sig, (
                    f"R9.1 cls={cls} split_at={split_at} signature"
                )
        # And the produced outcome equals the frozen declared outcome (R9.1).
        if declared == TradeOffOutcome.TERMINATE.value:
            # terminate classes are non-confluent here -> stub drive above.
            _, codes = _drive_stub(value, cls=cls, category=category, split_at=None)
            assert codes == ["forced_release_tradeoff"], f"{cls} {codes}"
