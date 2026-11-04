"""GW33 task 10.3: pathological inputs and the per-delta canary overlap rescan against the hot path."""
from __future__ import annotations

import time

from gateway_v2.detect.output.canary import CanaryDetector
from gateway_v2.detect.output.exfil import ExfilDetector
from gateway_v2.detect.output.ip_leak import IpLeakDetector
from gateway_v2.domain import FindingStatus
from tests.e2e.gw33_support import TOKEN, contract

_C = contract()


def test_the_canary_overlap_rescan_per_delta_fits_the_hot_path_budget() -> None:
    """L-4: 1-byte deltas amplify to `overlap` characters each; that stays inside the 20 ms p99."""
    detector = CanaryDetector(TOKEN)
    overlap = _C.canary_scan_overlap_chars()
    whole = "a" * (overlap * 3)
    per_call = []
    for end in range(len(whole) - 200, len(whole)):
        start = time.perf_counter()
        detector.detect_from(whole[:end], max(0, end - 1 - (overlap - 1)))
        per_call.append(time.perf_counter() - start)
    worst_ms = max(per_call) * 1000
    assert worst_ms < _C.target_p99_ms, (worst_ms, _C.target_p99_ms)


def test_pathological_text_stays_bounded_for_every_detector() -> None:
    bound = _C.grounding_wait_budget_s()
    cases = {
        "deep percent": "%2525252541" * 20000,
        "unclosed markup": "![a " * 50000 + "<img " * 20000,
        "dotted names": "a.b." * 60000,
        "colons": ":" * 100000,
        "many addresses": " ".join(f"10.0.{i % 255}.{i % 200}" for i in range(20000)),
        "long host": "![a](http://" + "a" * 5000 + ".com/x)",
    }
    detectors = (IpLeakDetector(bounds=_C.output_bounds()), ExfilDetector(bounds=_C.output_bounds()),
                 CanaryDetector(TOKEN))
    for name, text in cases.items():
        for detector in detectors:
            start = time.perf_counter()
            findings = detector.detect(text)
            assert time.perf_counter() - start < bound, (name, type(detector).__name__)
            assert all(f.status in (FindingStatus.EXECUTED, FindingStatus.UNAVAILABLE)
                       for f in findings)
            assert len(findings) <= _C.output_scan_candidate_cap() + 1 or isinstance(
                detector, CanaryDetector)
