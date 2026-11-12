"""Self-tests for amf_pytest_shards.py (stdlib only; `CI helper scripts` runs them).

The gateway-v2 workflow proves on every run that its shards partition the real suite (the
aggregate job runs `verify`); these tests pin the assignment rules and every way `verify`
must fail.
"""

from __future__ import annotations

import copy
import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import amf_pytest_shards as shards  # noqa: E402

NODEIDS = [
    "tests/a/test_one.py::test_x",
    "tests/a/test_one.py::test_y[1]",
    "tests/a/test_one.py::test_y[2]",
    "tests/b/test_two.py::C::test_z",
    "tests/b/test_three.py::test_w",
    "tests/test_four.py::test_v",
    "tests/test_five.py::test_u",
]


def _reports(nodeids: list[str], count: int, weights: dict | None = None) -> list[dict]:
    out = []
    for index in range(count):
        selected, shard_of = shards.select(nodeids, index, count, weights)
        out.append(
            {
                "format": shards.REPORT_FORMAT,
                "index": index,
                "count": count,
                "collected": len(nodeids),
                "collected_sha256": shards.digest(nodeids),
                "assignment_sha256": shards.assignment_digest(shard_of),
                "selected": sorted(selected),
                "outcomes": {"passed": len(selected)},
                "files": {shards.file_of(n): 1.0 for n in selected},
                "slowest": [[n, 0.5] for n in selected[:2]],
                "exitstatus": 0,
                "wall_seconds": 1.0,
            }
        )
    return out


class AssignmentTest(unittest.TestCase):
    def test_file_of(self) -> None:
        self.assertEqual(shards.file_of("tests/a/test_one.py::C::t[a::b]"), "tests/a/test_one.py")
        self.assertEqual(shards.file_of("tests/a/test_one.py"), "tests/a/test_one.py")

    def test_partition_for_every_count(self) -> None:
        for count in range(1, 10):
            picked: list[str] = []
            for index in range(count):
                picked += shards.select(NODEIDS, index, count, None)[0]
            self.assertEqual(sorted(picked), sorted(NODEIDS), count)
            self.assertEqual(len(picked), len(set(picked)), count)

    def test_whole_files_stay_together(self) -> None:
        for index in range(3):
            files = {shards.file_of(n) for n in shards.select(NODEIDS, index, 3, None)[0]}
            for other in range(3):
                if other != index:
                    others = {shards.file_of(n) for n in shards.select(NODEIDS, other, 3, None)[0]}
                    self.assertFalse(files & others)

    def test_collection_order_kept(self) -> None:
        selected = shards.select(NODEIDS, 0, 2, None)[0]
        self.assertEqual(selected, [n for n in NODEIDS if n in set(selected)])

    def test_deterministic_and_order_independent(self) -> None:
        weights = {"format": 1, "per_test_default": 2.0, "files": {"tests/test_five.py": 9.0}}
        a = shards.select(NODEIDS, 1, 3, weights)[1]
        b = shards.select(list(reversed(NODEIDS)), 1, 3, weights)[1]
        self.assertEqual(a, b)

    def test_lpt_heaviest_first_to_lightest_shard(self) -> None:
        # Greedy LPT (not optimal: 14/12 here, the optimum is 13/13): a->0, b->1, c->1 (6<10),
        # d->0 (10<11), e->1 (11<14). Ties go to the lower index, then by path.
        shard_of = shards.assign({"a": 10.0, "b": 6.0, "c": 5.0, "d": 4.0, "e": 1.0}, 2)
        self.assertEqual(shard_of, {"a": 0, "b": 1, "c": 1, "d": 0, "e": 1})
        self.assertEqual(shards.assign({"y": 1.0, "x": 1.0}, 2), {"x": 0, "y": 1})

    def test_unknown_files_weigh_tests_times_default(self) -> None:
        weights = {"format": 1, "per_test_default": 0.5, "files": {"x.py": 7.0, "bad.py": -1}}
        self.assertEqual(
            shards.file_weights({"x.py": 3, "y.py": 4, "bad.py": 2}, weights),
            {"x.py": 7.0, "y.py": 2.0, "bad.py": 1.0},
        )

    def test_bad_index_or_count(self) -> None:
        with self.assertRaises(ValueError):
            shards.select(NODEIDS, 3, 3, None)
        with self.assertRaises(ValueError):
            shards.assign({"a": 1.0}, 0)

    def test_more_shards_than_files_leaves_empty_shards(self) -> None:
        count = 9
        sizes = [len(shards.select(NODEIDS, i, count, None)[0]) for i in range(count)]
        self.assertEqual(sum(sizes), len(NODEIDS))
        self.assertIn(0, sizes)

    def test_load_weights_rejects_other_formats(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "w.json"
            path.write_text(json.dumps({"format": 2, "files": {}}))
            with self.assertRaises(ValueError):
                shards.load_weights(str(path))
            path.write_text(json.dumps({"format": 1, "files": {"a.py": 1.0}}))
            self.assertEqual(shards.load_weights(str(path))["files"], {"a.py": 1.0})
        self.assertIsNone(shards.load_weights(None))


class VerifyTest(unittest.TestCase):
    def test_good_reports_pass(self) -> None:
        result = shards.verify(_reports(NODEIDS, 3))
        self.assertEqual(result["collected"], len(NODEIDS))
        self.assertEqual(result["outcomes"], {"passed": len(NODEIDS)})
        self.assertEqual([s["index"] for s in result["shards"]], [0, 1, 2])

    def _fails(self, reports: list[dict], fragment: str) -> None:
        with self.assertRaises(shards.VerifyError) as ctx:
            shards.verify(reports)
        self.assertIn(fragment, str(ctx.exception))

    def test_no_reports(self) -> None:
        self._fails([], "no shard reports")

    def test_missing_shard(self) -> None:
        self._fails(_reports(NODEIDS, 3)[:2], "one report per shard index")

    def test_repeated_shard(self) -> None:
        reports = _reports(NODEIDS, 3)
        self._fails(reports + [copy.deepcopy(reports[0])], "one report per shard index")

    def test_count_disagreement(self) -> None:
        reports = _reports(NODEIDS, 3)
        reports[1]["count"] = 4
        self._fails(reports, "disagree on count")

    def test_collection_disagreement(self) -> None:
        reports = _reports(NODEIDS, 3)
        reports[2]["collected_sha256"] = "0" * 64
        self._fails(reports, "disagree on collected_sha256")

    def test_assignment_disagreement(self) -> None:
        reports = _reports(NODEIDS, 3)
        reports[0]["assignment_sha256"] = "f" * 64
        self._fails(reports, "disagree on assignment_sha256")

    def test_duplicate_selection(self) -> None:
        reports = _reports(NODEIDS, 2)
        reports[1]["selected"].append(reports[0]["selected"][0])
        self._fails(reports, "selected by shards")

    def test_dropped_test(self) -> None:
        reports = _reports(NODEIDS, 2)
        reports[0]["selected"].pop()
        self._fails(reports, "the shards selected 6 tests")

    def test_swapped_test_same_count(self) -> None:
        reports = _reports(NODEIDS, 2)
        reports[0]["selected"][0] = "tests/other.py::test_not_collected"
        self._fails(reports, "different ids")

    def test_failed_shard(self) -> None:
        reports = _reports(NODEIDS, 2)
        reports[1]["exitstatus"] = 1
        self._fails(reports, "shard 1: exit 1")


class CliTest(unittest.TestCase):
    def _write(self, tmp: str, reports: list[dict]) -> Path:
        directory = Path(tmp) / "reports"
        for r in reports:
            sub = directory / f"gw2-shard-{r['index']}"
            sub.mkdir(parents=True)
            (sub / f"shard-{r['index']}.json").write_text(json.dumps(r))
        return directory

    def test_verify_and_weights(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            directory = self._write(tmp, _reports(NODEIDS, 3))
            out = io.StringIO()
            with redirect_stdout(out):
                self.assertEqual(shards.main(["verify", str(directory)]), 0)
            self.assertIn(f"{len(NODEIDS)} tests in 3 shards", out.getvalue())
            out = io.StringIO()
            with redirect_stdout(out):
                self.assertEqual(shards.main(["weights", str(directory)]), 0)
            doc = json.loads(out.getvalue())
            self.assertEqual(doc["format"], shards.WEIGHTS_FORMAT)
            self.assertEqual(set(doc["files"]), {shards.file_of(n) for n in NODEIDS})

    def test_verify_fails_with_annotation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            directory = self._write(tmp, _reports(NODEIDS, 3)[:2])
            out = io.StringIO()
            with redirect_stdout(out):
                self.assertEqual(shards.main(["verify", str(directory)]), 1)
            self.assertIn("::error title=pytest shards::", out.getvalue())

    def test_empty_directory_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out = io.StringIO()
            with redirect_stdout(out):
                self.assertEqual(shards.main(["verify", tmp]), 1)


if __name__ == "__main__":
    unittest.main()
