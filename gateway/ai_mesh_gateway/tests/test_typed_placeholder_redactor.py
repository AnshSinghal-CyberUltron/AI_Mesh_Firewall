"""Edge-case tests for the vector-safe typed-placeholder redactor."""
import re

from typed_placeholder_redactor import (
    detect_and_redact_typed,
    redact_for_embedding,
)


def test_single_email_typed_placeholder():
    assert redact_for_embedding("Email me at a@b.com please") == "Email me at [EMAIL] please"


def test_multiple_same_type_each_placeholdered():
    # R1: each match -> its own [EMAIL], structure preserved
    assert redact_for_embedding("Contact a@b.com or c@d.com") == "Contact [EMAIL] or [EMAIL]"


def test_mixed_pii_all_redacted_no_raw_leak():
    text = "Jane, SSN 123-45-6789, card 4111 1111 1111 1111, email j@x.com"
    out = redact_for_embedding(text)
    assert "[SSN]" in out and "[CREDIT_CARD]" in out and "[EMAIL]" in out
    # no raw values survive
    assert "123-45-6789" not in out
    assert "4111" not in out
    assert "j@x.com" not in out


def test_overlap_is_leak_safe():
    # R2: a secret-assignment span contains an api-key span -> merged, no raw leak
    text = "config: secret=sk-abcdefghijklmnopqrstuvwxyz0123456789"
    out = redact_for_embedding(text)
    assert "sk-abcdefghijklmnopqrstuvwxyz" not in out
    # exactly one placeholder for the merged span (no double replacement artifacts)
    assert out.count("[SECRET]") + out.count("[API_KEY]") == 1


def test_deterministic():
    # R4: identical input -> identical output (so identical docs embed identically)
    text = "reach me: a@b.com / 123-45-6789"
    assert redact_for_embedding(text) == redact_for_embedding(text)


def test_benign_prose_not_redacted():
    # R5: "password manager" / "secret garden" have no assignment operator
    text = "I use a password manager and enjoy my secret garden."
    assert redact_for_embedding(text) == text


def test_placeholder_text_is_stable():
    # R6: a doc already containing placeholders must not be mangled
    assert redact_for_embedding("see [EMAIL] and [SSN]") == "see [EMAIL] and [SSN]"


def test_punctuation_only_no_crash():
    # R7: degenerate input -> no index error, returned unchanged
    for s in ["---", "(__)____", "::::", ""]:
        assert isinstance(redact_for_embedding(s), str)


def test_empty_string():
    r = detect_and_redact_typed("")
    assert r.text == "" and not r.redacted and r.total == 0


def test_result_metadata_counts_and_spans():
    r = detect_and_redact_typed("a@b.com and c@d.com")
    assert r.redacted is True
    assert r.counts.get("email") == 2
    assert len(r.spans.get("email", [])) == 2
    # spans are offsets into the ORIGINAL text
    for start, end in r.spans["email"]:
        assert "@" in "a@b.com and c@d.com"[start:end]


def test_no_raw_value_substring_survives_anywhere():
    text = "AWS AKIA1234567890ABCDEF token=ghp_" + "a" * 36
    out = redact_for_embedding(text)
    assert not re.search(r"AKIA[0-9A-Z]{16}", out)
    assert "ghp_aaaa" not in out


def test_credit_card_with_dashes():
    assert redact_for_embedding("card 4111-1111-1111-1111 done") == "card [CREDIT_CARD] done"


def test_no_redos_on_pathological_long_run():
    """Regression guard: a long [A-Za-z0-9.-] run with no '@'/TLD must redact in
    ~linear time. The previous unbounded email regex catastrophically
    backtracked here (a ~500KB field once hung ~18 minutes)."""
    import time

    payload = "-".join("1234" for _ in range(60000))  # ~300KB, the ReDoS shape
    start = time.perf_counter()
    out = redact_for_embedding(payload)
    elapsed = time.perf_counter() - start
    assert isinstance(out, str)
    assert elapsed < 3.0, f"redaction took {elapsed:.1f}s — possible ReDoS regression"


def test_bare_contextual_phone_does_not_swallow_nested_email():
    """B4 span-unification regression: a contextual bare-phone whose cue is split
    from the value by an email ("contact <email>, call back on <phone>") must
    redact to BOTH [EMAIL] and [PHONE]. Before the fix the broadened phone match
    SPAN (cue..value) overlap-swallowed the nested email, dropping [EMAIL] — a
    divergence from patterns.redact_all (which masks only the trailing digits).
    The typed redactor now narrows the phone span to its value, mirroring
    _mask_phone_bare_contextual, so both engines redact structurally identically."""
    text = "Customer record: contact bob@corp.example, call back on 8929554991."
    r = detect_and_redact_typed(text)
    assert "8929554991" not in r.text, f"raw phone survived: {r.text!r}"
    assert "bob@corp.example" not in r.text, f"raw email survived: {r.text!r}"
    assert "[EMAIL]" in r.text, f"email placeholder swallowed by phone span: {r.text!r}"
    assert "[PHONE]" in r.text, f"phone not redacted: {r.text!r}"
    # The cue/gap text between the two placeholders is preserved (not consumed).
    assert "call back on" in r.text
