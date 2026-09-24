"""LGW01-5 junit compare is deterministic."""

from __future__ import annotations

from pathlib import Path

from lint.compare_junit import main


def test_compare_junit_identical(tmp_path: Path) -> None:
    xml = """<?xml version="1.0"?>
<testsuite>
  <testcase classname="a" name="t1"/>
  <testcase classname="a" name="t2"><skipped/></testcase>
</testsuite>
"""
    left = tmp_path / "a.xml"
    right = tmp_path / "b.xml"
    left.write_text(xml)
    right.write_text(xml)
    assert main(["", str(left), str(right)]) == 0


def test_compare_junit_drift(tmp_path: Path) -> None:
    left = tmp_path / "a.xml"
    right = tmp_path / "b.xml"
    left.write_text(
        '<?xml version="1.0"?><testsuite><testcase classname="a" name="t1"/></testsuite>',
    )
    right.write_text(
        '<?xml version="1.0"?><testsuite>'
        '<testcase classname="a" name="t1"><failure/></testcase></testsuite>',
    )
    assert main(["", str(left), str(right)]) == 1
