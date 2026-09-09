"""CLI-operability and exit-code tests for ``score_postures.py`` (Task 9.3).

Additive, measurement-only (Requirement 9): this file lives under
``scripts/detection/`` and touches nothing outside it — every filesystem write a
test triggers is redirected under pytest's ``tmp_path`` by monkeypatching the
module's ``_report_path`` (and ``_repo_root``) helpers, so the real repository
``docs/perf/`` and ``tests/detection_corpus/`` are never read or written.

These tests exercise ``score_postures.main(argv)`` for real — its orchestration
and its exit-code / fail-closed contract (Requirements 1.4, 6.7, 6.10, 7.3, 8.5)
— by monkeypatching only the *boundaries* the CLI calls out to:

* ``score_postures._attribution.resolve_attribution`` — return a resolved
  :class:`attribution.Attribution` (or an :class:`AttributionUnavailable`).
* ``score_postures._corpus.load_corpus`` — return a small fake
  :class:`corpus.Corpus` (or raise, to model an unreadable corpus).
* ``score_postures._postures.build_postures`` — return fake posture adapters.
* ``score_postures._report.emit`` — captured, so a test can assert whether a
  report was emitted and, when it was, inspect the :class:`report.PostureReport`
  that ``main`` assembled (including its :class:`report.ScoredSetMeta`).

``_corpus.scored_set``, ``_corpus.partition``, ``_corpus.split_of``,
``metrics``/``thresholds`` and ``report.build_report`` all run for real, so the
orchestration + exit codes are genuinely tested rather than stubbed away.

Hermetic: no real scanner, no real git, no network; all fakes are in-process.
"""

from __future__ import annotations

import os
import sys

import pytest

# The detection modules import as top-level names with ``scripts/detection`` on
# the path (they are a self-contained additive package). Make that import work
# whether pytest is invoked from the repo root, the gateway dir, or elsewhere.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import attribution  # noqa: E402
import corpus  # noqa: E402
import postures  # noqa: E402
import report  # noqa: E402
import score_postures  # noqa: E402


# ---------------------------------------------------------------------------
# Fakes (uniquely ``_sp_`` prefixed so this file can co-exist with siblings)
# ---------------------------------------------------------------------------


def _sp_item(
    item_id: str,
    text: str,
    label: str,
    family: str,
    split: str,
) -> corpus.Corpus_Item:
    """Build a real :class:`corpus.Corpus_Item` (so downstream code runs for real)."""
    return corpus.Corpus_Item(
        id=item_id,
        text=text,
        label=label,
        family=family,
        split=split,
        provenance="fake",
    )


def _sp_corpus(items: list[corpus.Corpus_Item]) -> corpus.Corpus:
    """Build a real :class:`corpus.Corpus` backed by ``items``.

    ``attack_families`` / ``benign_families`` are supplied but not consulted by
    ``main`` (the per-family cells come from the scored items themselves), so any
    non-empty tuple is fine.
    """
    return corpus.Corpus(
        items=tuple(items),
        attack_families=("injection", "paraphrase"),
        benign_families=("developer_traffic",),
        root=corpus.Path("/fake/detection_corpus"),
    )


def _sp_mixed_corpus() -> corpus.Corpus:
    """A small corpus with BOTH eval and non-eval (train) items in each label.

    Two malicious + two benign items live in ``eval``; a further malicious + benign
    pair live in ``train``. So the ``eval`` scored set is a strict subset of
    ``full``, letting a test tell the two scored sets apart by their item counts and
    by the ``ScoredSetMeta`` the report records (Requirement 6.7). The train split is
    non-empty so threshold tuning has data on either scored set.
    """
    return _sp_corpus(
        [
            _sp_item("m-eval-1", "attack one", "malicious", "injection", "eval"),
            _sp_item("m-eval-2", "attack two", "malicious", "paraphrase", "eval"),
            _sp_item("b-eval-1", "hello there", "benign", "developer_traffic", "eval"),
            _sp_item("b-eval-2", "good morning", "benign", "developer_traffic", "eval"),
            _sp_item("m-train-1", "attack three", "malicious", "injection", "train"),
            _sp_item("b-train-1", "how are you", "benign", "developer_traffic", "train"),
        ]
    )


class _SpFakeAdapter:
    """A minimal in-process :class:`postures.PostureAdapter` (name + probe + score).

    ``probe`` returns the configured :class:`postures.AvailabilityResult`; ``score``
    returns a deterministic score derived from the text (``"attack"`` in the text →
    high, else low) so scored postures produce plausible metrics without a scanner.
    """

    def __init__(
        self,
        name: str,
        availability: postures.AvailabilityResult | None = None,
    ) -> None:
        self.name = name
        self._availability = availability or postures.AvailabilityResult.ok()

    def probe(self) -> postures.AvailabilityResult:
        return self._availability

    def score(self, text: str) -> float:
        return 0.9 if "attack" in text else 0.1


def _sp_three_available() -> list[_SpFakeAdapter]:
    """Three available fake adapters (the happy path — postures can be scored)."""
    return [
        _SpFakeAdapter(postures.TIER1_ONLY_NAME),
        _SpFakeAdapter(postures.TIER1_PLUS_POLICY_NAME),
        _SpFakeAdapter(postures.TIER1_PLUS_SEMANTIC_NAME),
    ]


def _sp_three_unavailable() -> list[_SpFakeAdapter]:
    """Three UNAVAILABLE fake adapters (each probe reports a non-empty reason)."""
    return [
        _SpFakeAdapter(
            postures.TIER1_ONLY_NAME,
            postures.AvailabilityResult.unavailable("scanner not importable"),
        ),
        _SpFakeAdapter(
            postures.TIER1_PLUS_POLICY_NAME,
            postures.AvailabilityResult.unavailable("policy engine not importable"),
        ),
        _SpFakeAdapter(
            postures.TIER1_PLUS_SEMANTIC_NAME,
            postures.AvailabilityResult.unavailable("Tier-2 disabled"),
        ),
    ]


class _SpEmitCapture:
    """A stand-in for ``report.emit`` that records what ``main`` tried to emit.

    Instances are callable with ``emit``'s signature ``(report, out_path)`` and
    record the call so a test can assert emit was (or was not) called and inspect
    the :class:`report.PostureReport` that was assembled. Writes NOTHING to disk.
    """

    def __init__(self) -> None:
        self.calls: list[tuple[report.PostureReport, object]] = []

    def __call__(self, report_obj: report.PostureReport, out_path: object) -> None:
        self.calls.append((report_obj, out_path))

    @property
    def called(self) -> bool:
        return bool(self.calls)

    @property
    def call_count(self) -> int:
        return len(self.calls)

    @property
    def last_report(self) -> report.PostureReport:
        assert self.calls, "emit was never called; no report to inspect"
        return self.calls[-1][0]


def _sp_install_boundaries(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
    *,
    corpus_obj: corpus.Corpus | None = None,
    load_corpus_side_effect: Exception | None = None,
    adapters: list | None = None,
    attribution_obj: object | None = None,
) -> _SpEmitCapture:
    """Monkeypatch every ``main`` boundary and redirect the report path to tmp_path.

    Returns the :class:`_SpEmitCapture` bound in place of ``report.emit`` so the
    caller can assert on emission. Any boundary not overridden by a keyword defaults
    to a succeeding happy-path stub. Nothing here touches the real repo: the report
    path is forced under ``tmp_path`` and ``load_corpus`` is a fake.
    """
    # Force the report output path under tmp_path (never docs/perf/**), and pin the
    # repo root to tmp_path so no real filesystem location is consulted.
    monkeypatch.setattr(score_postures, "_repo_root", lambda: tmp_path)
    monkeypatch.setattr(
        score_postures, "_report_path", lambda root: tmp_path / "posture_scores.md"
    )
    monkeypatch.setattr(
        score_postures, "_corpus_root", lambda root: tmp_path / "detection_corpus"
    )

    # Attribution boundary: resolved Attribution unless a test supplies otherwise.
    resolved = (
        attribution_obj
        if attribution_obj is not None
        else attribution.Attribution(
            corpus_version="deadbeef" * 5,
            reproducible_command="fake command",
            target_fpr=0.01,
        )
    )
    monkeypatch.setattr(
        score_postures._attribution,
        "resolve_attribution",
        lambda root: resolved,
    )

    # Corpus load boundary: raise (unreadable) or return the fake corpus.
    if load_corpus_side_effect is not None:
        def _raise(root, _exc=load_corpus_side_effect):
            raise _exc

        monkeypatch.setattr(score_postures._corpus, "load_corpus", _raise)
    else:
        the_corpus = corpus_obj if corpus_obj is not None else _sp_mixed_corpus()
        monkeypatch.setattr(
            score_postures._corpus, "load_corpus", lambda root: the_corpus
        )

    # Posture factory boundary.
    the_adapters = adapters if adapters is not None else _sp_three_available()
    monkeypatch.setattr(
        score_postures._postures, "build_postures", lambda: list(the_adapters)
    )

    # Emit boundary: capture instead of writing.
    capture = _SpEmitCapture()
    monkeypatch.setattr(score_postures._report, "emit", capture)
    return capture


# ---------------------------------------------------------------------------
# 1. --scored-set: default is 'eval', 'full' is accepted, and it is recorded
#    in the emitted report's ScoredSetMeta (Requirement 6.7)
# ---------------------------------------------------------------------------


def test_scored_set_default_is_eval() -> None:
    """``--scored-set`` defaults to ``eval`` (the headline split) (R6.7)."""
    assert score_postures._parse_args([]).scored_set == "eval"


def test_scored_set_full_is_accepted() -> None:
    """``--scored-set full`` is accepted and parsed as ``full`` (R6.7)."""
    assert score_postures._parse_args(["--scored-set", "full"]).scored_set == "full"


def test_scored_set_rejects_unknown_value() -> None:
    """An unknown ``--scored-set`` value is rejected by argparse (R7.1: only the
    documented choices ``eval``/``full`` are accepted)."""
    with pytest.raises(SystemExit):
        score_postures._parse_args(["--scored-set", "nonsense"])


def test_default_run_records_eval_scored_set(monkeypatch, tmp_path) -> None:
    """Default run measures the ``eval`` scored set and records ``which='eval'``.

    Drives ``main([])`` over a corpus with both eval and non-eval items; the
    emitted report's ``ScoredSetMeta.which`` is ``eval`` and its malicious/benign
    counts reflect ONLY the eval items (2 malicious + 2 benign), not the train
    ones — proving the default headline set is the eval split (R6.7).
    """
    capture = _sp_install_boundaries(monkeypatch, tmp_path)

    exit_code = score_postures.main([])

    assert exit_code == 0
    assert capture.called
    meta = capture.last_report.scored_set
    assert meta.which == "eval"
    assert meta.malicious_count == 2
    assert meta.benign_count == 2


def test_full_run_records_full_scored_set(monkeypatch, tmp_path) -> None:
    """``--scored-set full`` measures the whole corpus and records ``which='full'``.

    Same corpus as the eval case, but ``main(['--scored-set','full'])`` scores every
    item: the report's ``ScoredSetMeta.which`` is ``full`` and its counts include the
    train items too (3 malicious + 3 benign) — so the recorded scored set matches the
    requested choice (R6.7).
    """
    capture = _sp_install_boundaries(monkeypatch, tmp_path)

    exit_code = score_postures.main(["--scored-set", "full"])

    assert exit_code == 0
    assert capture.called
    meta = capture.last_report.scored_set
    assert meta.which == "full"
    assert meta.malicious_count == 3
    assert meta.benign_count == 3


# ---------------------------------------------------------------------------
# 2. Success -> exit 0, emit called exactly once
# ---------------------------------------------------------------------------


def test_success_returns_zero_and_emits_once(monkeypatch, tmp_path) -> None:
    """Happy path: resolved attribution + non-empty corpus + >=1 scorable posture.

    ``main`` returns 0 and ``emit`` is called exactly once with a fully-assembled
    three-posture report (Requirement 7.3 success half)."""
    capture = _sp_install_boundaries(monkeypatch, tmp_path)

    exit_code = score_postures.main([])

    assert exit_code == 0
    assert capture.call_count == 1
    assert len(capture.last_report.postures) == report.EXPECTED_POSTURE_COUNT


# ---------------------------------------------------------------------------
# 3. Empty / unreadable corpus -> non-zero + no report (Requirement 7.3)
# ---------------------------------------------------------------------------


def test_unreadable_corpus_fails_closed(monkeypatch, tmp_path, capsys) -> None:
    """(a) ``load_corpus`` raising ``FileNotFoundError`` → non-zero, no emit (R7.3)."""
    capture = _sp_install_boundaries(
        monkeypatch,
        tmp_path,
        load_corpus_side_effect=FileNotFoundError("malicious.jsonl missing"),
    )

    exit_code = score_postures.main([])

    assert exit_code != 0
    assert not capture.called
    err = capsys.readouterr().err
    assert "could not read the detection corpus" in err
    assert "no report emitted" in err


def test_empty_corpus_fails_closed(monkeypatch, tmp_path, capsys) -> None:
    """(b) corpus with 0 items → non-zero, no emit (Requirements 1.6, 7.3)."""
    capture = _sp_install_boundaries(
        monkeypatch, tmp_path, corpus_obj=_sp_corpus([])
    )

    exit_code = score_postures.main([])

    assert exit_code != 0
    assert not capture.called
    err = capsys.readouterr().err
    assert "contains 0 items" in err
    assert "no report emitted" in err


def test_empty_scored_set_fails_closed(monkeypatch, tmp_path, capsys) -> None:
    """(c) non-empty corpus but the selected scored set is empty → non-zero, no emit.

    The corpus has only ``train`` items, so the ``eval`` scored set is empty. ``main``
    fails closed before scoring (Requirements 1.6, 7.3).
    """
    train_only = _sp_corpus(
        [
            _sp_item("m-train-1", "attack one", "malicious", "injection", "train"),
            _sp_item("b-train-1", "hello", "benign", "developer_traffic", "train"),
        ]
    )
    capture = _sp_install_boundaries(monkeypatch, tmp_path, corpus_obj=train_only)

    exit_code = score_postures.main([])  # default 'eval' scored set → empty

    assert exit_code != 0
    assert not capture.called
    err = capsys.readouterr().err
    assert "scored set 'eval' is empty" in err
    assert "no report emitted" in err


# ---------------------------------------------------------------------------
# 4. Missing attribution -> non-zero + no report (Requirement 6.10)
# ---------------------------------------------------------------------------


def test_missing_attribution_fails_closed(monkeypatch, tmp_path, capsys) -> None:
    """``resolve_attribution`` returns ``AttributionUnavailable`` → non-zero, no emit.

    A missing attribution value aborts BEFORE any posture is probed or scored, and no
    report is emitted (Requirement 6.10). The error names the missing value.
    """
    unavailable = attribution.AttributionUnavailable(
        "corpus_version", "not a git work tree"
    )
    capture = _sp_install_boundaries(
        monkeypatch, tmp_path, attribution_obj=unavailable
    )

    exit_code = score_postures.main([])

    assert exit_code != 0
    assert not capture.called
    err = capsys.readouterr().err
    assert "corpus_version" in err
    assert "no report emitted" in err


# ---------------------------------------------------------------------------
# 5. All three postures unavailable -> non-zero, "no posture could be scored",
#    no report (Requirement 8.5)
# ---------------------------------------------------------------------------


def test_all_postures_unavailable_fails_closed(monkeypatch, tmp_path, capsys) -> None:
    """All three postures probe unavailable → non-zero, no emit, cause-naming message.

    ``build_postures`` returns three adapters whose ``probe()`` is unavailable; ``main``
    fails closed with the message "no posture could be scored" and emits no report
    (Requirement 8.5).
    """
    capture = _sp_install_boundaries(
        monkeypatch, tmp_path, adapters=_sp_three_unavailable()
    )

    exit_code = score_postures.main([])

    assert exit_code != 0
    assert not capture.called
    err = capsys.readouterr().err
    assert "no posture could be scored" in err
    assert "no report emitted" in err


# ---------------------------------------------------------------------------
# 6. Extensibility (Requirement 1.4): a synthetic FOURTH PostureAdapter flows
#    through the metric/threshold/report-result pipeline unchanged.
# ---------------------------------------------------------------------------


class _SpFourthAdapter:
    """A synthetic 4th :class:`postures.PostureAdapter` — proves extensibility (R1.4).

    It is a brand-new named detection configuration (``name`` + ``probe`` +
    ``score``) that the harness has never seen. Requirement 1.4 says such a posture
    must flow through the metric-computation, threshold-selection, and
    report-result logic WITHOUT any change to those modules. ``score`` flags
    ``"attack"`` texts high and everything else low so it produces real metrics.
    """

    name = "Tier1_Plus_Synthetic_Fourth"

    def probe(self) -> postures.AvailabilityResult:
        return postures.AvailabilityResult.ok()

    def score(self, text: str) -> float:
        return 0.95 if "attack" in text else 0.05


def test_fourth_adapter_flows_through_score_posture_unchanged() -> None:
    """A 4th adapter produces a scored :class:`report.PostureResult` with NO change to
    the metric/threshold/report-result logic (Requirement 1.4).

    ``report.build_report`` enforces the exactly-3-posture invariant, so the
    extensibility of the *pipeline* is verified at ``_score_posture`` — the single
    per-posture scoring path that runs the threshold-selection, metric-computation,
    and report-result modules. Feeding the brand-new adapter through it yields a
    scored ``PostureResult`` carrying the adapter's own name and computed metric
    values, demonstrating a new posture needs no modification to those layers.
    """
    fourth = _SpFourthAdapter()
    # A scored set the synthetic adapter separates cleanly: malicious "attack" items
    # scored 0.95, benign items scored 0.05. A train split feeds threshold tuning.
    scored_items = [
        _sp_item("m-eval-1", "attack alpha", "malicious", "injection", "eval"),
        _sp_item("m-eval-2", "attack beta", "malicious", "paraphrase", "eval"),
        _sp_item("b-eval-1", "hello world", "benign", "developer_traffic", "eval"),
        _sp_item("b-eval-2", "good day", "benign", "developer_traffic", "eval"),
        _sp_item("m-train-1", "attack gamma", "malicious", "injection", "train"),
        _sp_item("b-train-1", "how are you", "benign", "developer_traffic", "train"),
    ]

    result = score_postures._score_posture(fourth, scored_items)

    # It flows through unchanged: a real scored PostureResult, named for the 4th
    # adapter, that WOULD appear in the report (R1.4).
    assert isinstance(result, report.PostureResult)
    assert result.posture_name == "Tier1_Plus_Synthetic_Fourth"
    assert result.is_scored
    assert result.status == report.STATUS_SCORED
    # The metric/threshold pipeline actually ran and produced real values.
    assert result.selected_threshold is not None
    # The adapter separates malicious (0.95) from benign (0.05) perfectly, so at a
    # threshold in-between recall is 1.0 and FPR is 0.0 — a computed metric value,
    # not a fabricated one.
    assert result.recall_at_target_fpr == 1.0
    assert result.achieved_fpr == 0.0
    # Per-family cells were computed for the families present in the scored set.
    assert "injection" in result.per_family_recall
    assert "developer_traffic" in result.per_family_fpr


def test_fourth_adapter_pipeline_uses_unmodified_report_result_type() -> None:
    """The 4th adapter's scored result is the SAME ``report.PostureResult`` type the
    three built-in postures use — the report-result logic is unchanged (R1.4).

    Confirms the extensibility claim end-to-end at the type level: a new adapter
    reuses the exact report-result model, so a ``build_report`` over a set that
    included this posture (in a 3-posture-valid arrangement) would render it with no
    special-casing.
    """
    fourth = _SpFourthAdapter()
    scored_items = [
        _sp_item("m-eval-1", "attack one", "malicious", "injection", "eval"),
        _sp_item("b-eval-1", "benign one", "benign", "developer_traffic", "eval"),
        _sp_item("m-train-1", "attack two", "malicious", "injection", "train"),
        _sp_item("b-train-1", "benign two", "benign", "developer_traffic", "train"),
    ]

    result = score_postures._score_posture(fourth, scored_items)

    assert type(result) is report.PostureResult
    assert result.posture_name == fourth.name
    assert result.is_scored
