# ruff: noqa: E501  (GW33 test file: long assertion/docstring lines kept readable)
"""GW33 task 7.7: the output corpus (L33-1/2/3 shapes plus negatives) against the detectors."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from gateway_v2.detect.output.exfil import ExfilDetector
from gateway_v2.detect.output.grounding import GroundingDetector
from gateway_v2.detect.output.ip_leak import IpLeakDetector
from gateway_v2.domain import FindingStatus
from gateway_v2.runtime.resources_output import DEFAULT_OUTPUT_BOUNDS

_CORPUS = json.loads(
    (Path(__file__).resolve().parents[1] / "detection_corpus" / "output_gw33.json").read_text("utf-8"),
)


def _run(case: dict[str, object]) -> list[object]:
    text = str(case["text"])
    if case["detector"] == "exfil":
        detector = ExfilDetector(allow_hosts=frozenset(_CORPUS["allow_hosts"]))
        return list(detector.detect(text))
    if case["detector"] == "ip_leak":
        return list(IpLeakDetector(bounds=DEFAULT_OUTPUT_BOUNDS).detect(text))
    grounding = GroundingDetector(
        context_chunks=tuple(_CORPUS["context"]), max_input_chars=1_000_000, threshold=0.3,
    )
    return list(grounding.detect(text))


@pytest.mark.parametrize("case", _CORPUS["cases"], ids=lambda c: c["id"])
def test_corpus_case(case: dict[str, object]) -> None:
    findings = _run(case)
    executed = [f for f in findings if f.status is FindingStatus.EXECUTED]  # type: ignore[attr-defined]
    if case["expect"] == "finding":
        assert executed, case["id"]
        if "spans" in case:
            assert len(executed) == case["spans"]
    else:
        assert findings == [], case["id"]
