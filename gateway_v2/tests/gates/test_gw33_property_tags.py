# ruff: noqa: E501  (GW33 test file: long assertion/docstring lines kept readable)
"""GW33 task 12.2: every correctness Property 1-16 is tagged in source with an iteration constant."""
from __future__ import annotations

import re
from pathlib import Path

_TESTS = Path(__file__).resolve().parents[1]
_TAG = re.compile(r"#\s*Feature:\s*gw33-output-parity,\s*Property\s+(\d+):")
_ITER = re.compile(r"^_ITERATIONS\s*=\s*([\d_]+)", re.MULTILINE)


def test_every_property_has_a_tag_and_an_iteration_constant_of_at_least_100() -> None:
    found: dict[int, list[Path]] = {}
    for path in sorted(_TESTS.rglob("*.py")):
        if path.name == Path(__file__).name:
            continue
        text = path.read_text(encoding="utf-8")
        for number in {int(m.group(1)) for m in _TAG.finditer(text)}:
            found.setdefault(number, []).append(path)
    assert set(found) == set(range(1, 17)), sorted(set(range(1, 17)) - set(found))
    for number, paths in found.items():
        best = 0
        for path in paths:
            match = _ITER.search(path.read_text(encoding="utf-8"))
            if match:
                best = max(best, int(match.group(1).replace("_", "")))
        assert best >= 100, (number, [p.name for p in paths], best)
