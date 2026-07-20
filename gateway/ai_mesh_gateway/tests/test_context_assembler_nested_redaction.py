"""Regression: field-level sensitivity redaction must cover JSON at ANY nesting depth.

Root cause (fixed): context_assembler._JSON_BLOCK_RE matched objects nested at most
ONE level deep. redact_structured_fields (used on every RAG grounding document in
rag_pipeline/generator_stage.py with max_sensitivity='internal') therefore matched only
an inner fragment of a >=2-level-nested blob, so a sensitivity-classified field sitting
OUTSIDE that fragment (e.g. a top-level ssn/diagnosis/salary) silently egressed to the
LLM grounding context. Plain-text ssn/email/api_key/phone had a backstop
(_redact_retrieved_pii) but field-map-only semantic fields (salary, diagnosis, clearance,
medical_record, employee_id, name, address, dob) did NOT — those leaked.

Fix replaced the single-nesting regex with a brace-balanced, string-aware span scanner
(_find_top_level_json_spans) so the FULL object is parsed and _redact_dict recurses.
"""

from __future__ import annotations

import sys
from pathlib import Path

_GW = Path(__file__).resolve().parents[1]
if str(_GW) not in sys.path:
    sys.path.insert(0, str(_GW))

from context_assembler import redact_structured_fields  # noqa: E402


def test_flat_object_redacts():
    out = redact_structured_fields('{"ssn":"111-22-3333"}', "public")
    assert "111-22-3333" not in out and "[REDACTED:ssn]" in out


def test_two_level_nested_top_field_redacted():
    # The bug: a top-level classified field beside a >=2-level-nested sibling leaked.
    out = redact_structured_fields('note {"ssn":"111-22-3333","a":{"b":{"x":1}}}', "public")
    assert "111-22-3333" not in out


def test_three_level_nested_internal_default():
    # generator_stage uses max_sensitivity='internal'; 'diagnosis' is 'restricted'.
    out = redact_structured_fields('{"diagnosis":"cancer","m":{"k":{"z":{"q":1}}}}', "internal")
    assert "cancer" not in out


def test_classified_field_deep_inside_is_redacted():
    out = redact_structured_fields('{"a":{"b":{"salary":99000}}}', "public")
    assert "99000" not in out and "[REDACTED:salary]" in out


def test_field_map_only_semantic_fields_no_plaintext_backstop():
    # These semantic fields have NO regex backstop in _redact_retrieved_pii, so the
    # nested-JSON field redaction is the ONLY control — verify each is masked at depth.
    # salary/diagnosis/clearance are confidential/restricted (redacted even at the RAG
    # default 'internal'); employee_id is 'internal', so test at 'public' where every
    # classified field (level > 0) must be masked.
    for field, val in (("salary", "250000"), ("diagnosis", "leukemia"),
                       ("clearance", "TOP-SECRET"), ("employee_id", "E-9931")):
        blob = f'{{"wrap":{{"inner":{{"{field}":"{val}"}}}}}}'
        out = redact_structured_fields(blob, "public")
        assert val not in out, f"{field}={val} leaked: {out}"

    # And at the actual RAG default ('internal'): confidential/restricted semantic
    # fields must still be masked even when deeply nested.
    for field, val in (("salary", "250000"), ("diagnosis", "leukemia"),
                       ("clearance", "TOP-SECRET"), ("medical_record", "MR-77")):
        blob = f'{{"wrap":{{"inner":{{"{field}":"{val}"}}}}}}'
        out = redact_structured_fields(blob, "internal")
        assert val not in out, f"{field}={val} leaked at internal: {out}"


def test_brace_inside_string_value_not_miscounted():
    out = redact_structured_fields('{"note":"has } brace","ssn":"111-22-3333"}', "public")
    assert "111-22-3333" not in out


def test_benign_prose_unchanged():
    text = "Onboarding guide: submit your timesheet every Friday by 5pm."
    assert redact_structured_fields(text, "internal") == text


def test_benign_json_no_classified_field_no_churn():
    # No classified field => return the original bytes verbatim (no reformatting churn).
    text = '{"a":{"b":1}}'
    assert redact_structured_fields(text, "internal") == text


def test_multiple_objects_in_one_string():
    out = redact_structured_fields('a {"ssn":"111-22-3333"} b {"salary":10} c', "public")
    assert "111-22-3333" not in out and "[REDACTED:salary]" in out


def test_public_level_keeps_internal_field_but_redacts_restricted():
    # max_sensitivity='internal' keeps 'internal'-level fields (name) but masks 'restricted' (ssn).
    out = redact_structured_fields('{"name":"Jane","w":{"ssn":"111-22-3333"}}', "internal")
    assert "Jane" in out and "111-22-3333" not in out
