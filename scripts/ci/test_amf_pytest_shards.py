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
                "phases": {n: "passed/passed/passed" for n in selected},
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

    def test_hostile_weights_are_bounded(self) -> None:
        nan, inf = float("nan"), float("inf")
        weights = {
            "format": 1,
            "per_test_default": nan,
            "files": {
                "z.py": 0.0,
                "n.py": nan,
                "i.py": inf,
                "s.py": "9",
                "t.py": True,
                "h.py": 1e300,
            },
        }
        got = shards.file_weights(
            {"z.py": 1, "n.py": 2, "i.py": 3, "s.py": 4, "t.py": 5, "h.py": 6}, weights
        )
        # 0 -> floor; NaN, Infinity, strings and booleans -> tests * 1.0 (NaN default -> 1.0)
        self.assertEqual(
            got,
            {
                "z.py": shards.MIN_WEIGHT,
                "n.py": 2.0,
                "i.py": 3.0,
                "s.py": 4.0,
                "t.py": 5.0,
                "h.py": shards.MAX_WEIGHT,
            },
        )

    def test_degenerate_weights_still_spread_files(self) -> None:
        # All-zero (or all-NaN) weights used to leave every load at 0, so every file went to
        # shard 0 and that shard ran the whole suite.
        nodeids = [f"tests/test_{i:02d}.py::t" for i in range(16)]
        for value in (0.0, float("nan"), float("inf")):
            files = {shards.file_of(n): value for n in nodeids}
            weights = {"format": 1, "per_test_default": value, "files": files}
            sizes = [len(shards.select(nodeids, i, 4, weights)[0]) for i in range(4)]
            self.assertEqual(sizes, [4, 4, 4, 4], value)

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

    def test_selected_but_never_set_up(self) -> None:
        # pytest.exit(returncode=0) in an earlier test, or a hookwrapper that dropped the item
        # after selection: exit 0, but no report for the test.
        reports = _reports(NODEIDS, 2)
        del reports[0]["phases"][reports[0]["selected"][0]]
        self._fails(reports, "did not run to the end")
        self._fails(reports, "(never set up)")

    def test_set_up_but_never_called(self) -> None:
        # pytest.exit(returncode=0) inside the test: setup passed, no call, no teardown report.
        reports = _reports(NODEIDS, 2)
        reports[1]["phases"][reports[1]["selected"][-1]] = "passed/-/-"
        self._fails(reports, "(set up, never called)")

    def test_called_but_never_torn_down(self) -> None:
        # pytest.exit(returncode=0) in a fixture finalizer.
        reports = _reports(NODEIDS, 2)
        reports[1]["phases"][reports[1]["selected"][0]] = "passed/passed/-"
        self._fails(reports, "(never torn down)")

    def test_setup_skip_or_error_needs_no_call(self) -> None:
        reports = _reports(NODEIDS, 2)
        first, second = reports[0]["selected"][:2]
        reports[0]["phases"][first] = "skipped/-/passed"
        reports[0]["phases"][second] = "failed/-/passed"
        shards.verify(reports)  # complete runs: the exit status reports the error

    def test_ran_without_being_selected(self) -> None:
        reports = _reports(NODEIDS, 2)
        stray = reports[1]["selected"][0]
        reports[0]["phases"][stray] = "passed/passed/passed"
        self._fails(reports, "1 tests ran without being selected")

    def test_malformed_phases(self) -> None:
        reports = _reports(NODEIDS, 2)
        reports[0]["phases"][reports[0]["selected"][0]] = "passed"
        with self.assertRaises(ValueError):
            shards.verify(reports)
        reports = _reports(NODEIDS, 2)
        reports[0]["phases"] = []
        self._fails(reports, "`phases` is not an object")

    def test_failure_and_unfinished_both_reported(self) -> None:
        reports = _reports(NODEIDS, 2)
        reports[0]["exitstatus"] = 2
        del reports[0]["phases"][reports[0]["selected"][-1]]
        with self.assertRaises(shards.VerifyError) as ctx:
            shards.verify(reports)
        self.assertIn("shard 0: exit 2", str(ctx.exception))
        self.assertIn("did not run to the end", str(ctx.exception))

    def test_format_1_reports_are_refused(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            report = _reports(NODEIDS, 1)[0]
            report["format"] = 1
            del report["phases"]
            (Path(tmp) / "shard-0.json").write_text(json.dumps(report))
            with self.assertRaises(shards.VerifyError):
                shards.read_reports(Path(tmp))


class ExpectedCollectionTest(unittest.TestCase):
    def test_union_equals_unsharded(self) -> None:
        result = shards.verify(_reports(NODEIDS, 3), list(reversed(NODEIDS)))
        self.assertTrue(result["matched_unsharded"])

    def test_shared_drift_is_caught(self) -> None:
        # Every shard collected one more test than the canonical run (e.g. an --ignore lost to
        # another config file): the shards agree with each other, the canonical list does not.
        drifted = NODEIDS + ["tests/ignored/test_extra.py::test_t"]
        reports = _reports(drifted, 3)
        shards.verify(reports)  # internally consistent
        with self.assertRaises(shards.VerifyError) as ctx:
            shards.verify(reports, NODEIDS)
        self.assertIn("1 extra", str(ctx.exception))
        with self.assertRaises(shards.VerifyError) as ctx:
            shards.verify(_reports(NODEIDS[:-1], 2), NODEIDS)
        self.assertIn("1 missing", str(ctx.exception))

    def test_duplicate_in_unsharded_list(self) -> None:
        with self.assertRaises(shards.VerifyError):
            shards.verify(_reports(NODEIDS, 2), NODEIDS + NODEIDS[:1])


class CollectionIdsTest(unittest.TestCase):
    def test_ci_layout(self) -> None:
        text = "\n".join(NODEIDS) + f"\n\n{len(NODEIDS)} tests collected in 1.0s\n"
        self.assertEqual(shards.collection_ids(text), NODEIDS)

    def test_warning_before_and_warnings_summary_after(self) -> None:
        # A stdout warning before the ids, and a warnings summary after them that names tests
        # with `::`: only the id block counts.
        text = (
            "/venv/site-packages/plugin.py:300: PytestDeprecationWarning: option unset.\n"
            "Valid scopes are: function, module\n\n"
            + "\n".join(NODEIDS)
            + "\n\n=== warnings summary ===\n"
            + f"{NODEIDS[0]}\n  /x.py:3: UserWarning: careful\n\n"
            + f"{len(NODEIDS)} tests collected in 1.0s\n"
        )
        self.assertEqual(shards.collection_ids(text), NODEIDS)

    def test_deselected_summary(self) -> None:
        text = "\n".join(NODEIDS) + f"\n\n{len(NODEIDS)}/9 tests collected (2 deselected) in 1s\n"
        self.assertEqual(len(shards.collection_ids(text)), len(NODEIDS))
        self.assertEqual(
            shards.collection_ids(f"{NODEIDS[0]}\n\n1 test collected in 0.1s"), NODEIDS[:1]
        )

    def test_truncated_or_empty_listing_fails(self) -> None:
        for text in (
            "\n".join(NODEIDS[:-1]) + f"\n\n{len(NODEIDS)} tests collected in 1.0s\n",
            "\n".join(NODEIDS) + "\n",  # no summary line
            "no tests collected in 0.1s\n",
            "",
        ):
            with self.assertRaises(ValueError, msg=text):
                shards.collection_ids(text)


class _Config:
    def __init__(self, args: list[str], **options: object) -> None:
        self.invocation_params = type("P", (), {"args": tuple(args)})()
        self.options = options
        self.registered: list[object] = []
        self.pluginmanager = type(
            "PM", (), {"register": lambda _s, obj, _n: self.registered.append(obj)}
        )()

    def getoption(self, name: str) -> object:
        return self.options.get(name)


class ConfigureTest(unittest.TestCase):
    def test_space_separated_values_are_refused(self) -> None:
        cfg = _Config(
            ["--shard-weights", "w.json", "--shard-index=0"],
            **{"--shard-index": 0, "--shard-count": 2},
        )
        with self.assertRaises(shards._UsageError) as ctx:
            shards.pytest_configure(cfg)
        self.assertIn("--option=value", str(ctx.exception))
        self.assertEqual(cfg.registered, [])

    def test_equals_form_registers(self) -> None:
        cfg = _Config(
            ["--shard-index=1", "--shard-count=2"], **{"--shard-index": 1, "--shard-count": 2}
        )
        shards.pytest_configure(cfg)
        self.assertEqual(len(cfg.registered), 1)

    def test_no_options_no_plugin(self) -> None:
        cfg = _Config(["-q"])
        shards.pytest_configure(cfg)
        self.assertEqual(cfg.registered, [])

    def test_bad_combinations(self) -> None:
        for opts in ({"--shard-index": 0}, {"--shard-index": 2, "--shard-count": 2}):
            with self.assertRaises(shards._UsageError):
                shards.pytest_configure(_Config([], **opts))


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

    def test_verify_expect_collection(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            directory = self._write(tmp, _reports(NODEIDS, 3))
            listing = Path(tmp) / "collect.txt"
            listing.write_text("\n".join(NODEIDS) + f"\n\n{len(NODEIDS)} tests collected in 1.0s\n")
            out = io.StringIO()
            with redirect_stdout(out):
                code = shards.main(["verify", str(directory), "--expect-collection", str(listing)])
            self.assertEqual(code, 0, out.getvalue())
            self.assertIn("union equal to the unsharded collection", out.getvalue())
            listing.write_text("\n".join(NODEIDS[:-1]) + "\n")
            with redirect_stdout(io.StringIO()):
                code = shards.main(["verify", str(directory), "--expect-collection", str(listing)])
            self.assertEqual(code, 1)

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
