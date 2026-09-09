"""Read-only detection-corpus loader for the posture scoring harness.

Loads the labelled corpus produced by G0.1 from ``tests/detection_corpus/``,
partitions items by split and label, and never writes to the corpus or derives a
label from the scanner (Requirements 2.3, 2.6, 2.7, 7.4, 7.7).

This module is a pure, side-effect-free reader apart from ``load_corpus`` which
performs read-only filesystem reads. It NEVER writes to ``tests/detection_corpus/``
and NEVER inspects any scanner output — labels come solely from each item's
``label`` field.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Literal

# Recognized ground-truth label values (Requirement 2.3). Any other value —
# including an absent label — is treated as neither malicious nor benign.
MALICIOUS_LABEL = "malicious"
BENIGN_LABEL = "benign"

# Recognized split values (Requirement 7.7). Any other value is "other" and is
# excluded from both threshold tuning and the headline measurement.
TRAIN_SPLIT = "train"
EVAL_SPLIT = "eval"

ScoredSetName = Literal["eval", "full"]
SplitClass = Literal["train", "eval", "other"]


@dataclass(frozen=True)
class Corpus_Item:
    """A single labelled record of the Detection_Corpus (one JSONL object).

    Field names mirror the corpus schema produced by G0.1: ``id``, ``text``,
    ``label``, ``family``, ``split``, ``provenance``, ``fake_fixtures``. ``label``
    is the ONLY source of the ground-truth classification (Requirements 2.3, 7.4).
    """

    id: str
    text: str
    label: str
    family: str
    split: str
    provenance: str
    fake_fixtures: tuple = field(default_factory=tuple)

    @staticmethod
    def from_obj(obj: dict) -> "Corpus_Item":
        """Build a Corpus_Item from a decoded JSON object.

        Missing fields degrade gracefully: an absent ``label`` becomes an empty
        string (so it is treated as unrecognized), an absent ``split`` becomes an
        empty string (so it is treated as "other"). No value is ever derived from
        anything other than the record itself.
        """
        raw_fixtures = obj.get("fake_fixtures", [])
        try:
            fixtures = tuple(raw_fixtures)
        except TypeError:
            fixtures = ()
        return Corpus_Item(
            id=str(obj.get("id", "")),
            text=str(obj.get("text", "")),
            label=obj["label"] if isinstance(obj.get("label"), str) else "",
            family=obj["family"] if isinstance(obj.get("family"), str) else "",
            split=obj["split"] if isinstance(obj.get("split"), str) else "",
            provenance=str(obj.get("provenance", "")),
            fake_fixtures=fixtures,
        )


@dataclass(frozen=True)
class Corpus:
    """The loaded Detection_Corpus: all items plus the declared family names."""

    items: tuple[Corpus_Item, ...]
    attack_families: tuple[str, ...]
    benign_families: tuple[str, ...]
    root: Path


@dataclass(frozen=True)
class ScoredSet:
    """The set of Corpus_Items over which one scoring run computes its metrics."""

    which: ScoredSetName
    items: tuple[Corpus_Item, ...]


@dataclass(frozen=True)
class LabelPartition:
    """Items split by ground-truth label, with an excluded (unrecognized) bucket.

    Invariant: ``len(malicious) + len(benign) + len(excluded) == total`` where
    ``total`` is the number of items partitioned (Requirements 2.6, 2.7).
    """

    malicious: tuple[Corpus_Item, ...]
    benign: tuple[Corpus_Item, ...]
    excluded: tuple[Corpus_Item, ...]

    @property
    def malicious_count(self) -> int:
        return len(self.malicious)

    @property
    def benign_count(self) -> int:
        return len(self.benign)

    @property
    def excluded_count(self) -> int:
        return len(self.excluded)

    @property
    def total(self) -> int:
        return self.malicious_count + self.benign_count + self.excluded_count


def _read_jsonl(path: Path) -> list[Corpus_Item]:
    """Read a JSONL file read-only into Corpus_Item records.

    Blank lines are skipped. The file is opened for reading only; this function
    never writes to ``path``.
    """
    items: list[Corpus_Item] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            stripped = line.strip()
            if not stripped:
                continue
            obj = json.loads(stripped)
            items.append(Corpus_Item.from_obj(obj))
    return items


def load_corpus(root: Path | str) -> Corpus:
    """Read ``malicious.jsonl``, ``benign.jsonl`` and ``families.json`` read-only.

    ``root`` is the ``tests/detection_corpus/`` directory. This performs only
    filesystem reads and never writes to the corpus, and never derives a label
    from the scanner (Requirements 2.3, 7.4).

    Raises ``FileNotFoundError`` when a required corpus file is absent so the
    caller can abort with a failure exit and no report (Requirement 7.3).
    """
    root_path = Path(root)

    malicious_items = _read_jsonl(root_path / "malicious.jsonl")
    benign_items = _read_jsonl(root_path / "benign.jsonl")

    families_path = root_path / "families.json"
    with families_path.open("r", encoding="utf-8") as handle:
        families_obj = json.load(handle)
    attack_families = tuple(str(name) for name in families_obj.get("attack", []))
    benign_families = tuple(str(name) for name in families_obj.get("benign", []))

    items = tuple(malicious_items) + tuple(benign_items)
    return Corpus(
        items=items,
        attack_families=attack_families,
        benign_families=benign_families,
        root=root_path,
    )


def split_of(item: Corpus_Item) -> SplitClass:
    """Classify an item's split as ``train`` / ``eval`` / ``other``.

    A split value other than ``train`` or ``eval`` maps to ``other``; the caller
    excludes those items from tuning and the headline measurement and counts them
    (Requirement 7.7).
    """
    if item.split == TRAIN_SPLIT:
        return TRAIN_SPLIT
    if item.split == EVAL_SPLIT:
        return EVAL_SPLIT
    return "other"


def partition(items: Iterable[Corpus_Item]) -> LabelPartition:
    """Partition items by their ``label`` field alone (Requirements 2.3, 2.6, 2.7).

    Classification is solely from ``item.label``: exactly ``malicious`` or exactly
    ``benign``. An absent or unrecognized label puts the item in ``excluded`` and
    it is counted there. No posture score or scanner output is ever consulted, so
    the partition is invariant to any scoring. The three buckets sum to the total
    number of items partitioned.
    """
    malicious: list[Corpus_Item] = []
    benign: list[Corpus_Item] = []
    excluded: list[Corpus_Item] = []

    for item in items:
        if item.label == MALICIOUS_LABEL:
            malicious.append(item)
        elif item.label == BENIGN_LABEL:
            benign.append(item)
        else:
            excluded.append(item)

    return LabelPartition(
        malicious=tuple(malicious),
        benign=tuple(benign),
        excluded=tuple(excluded),
    )


def scored_set(corpus: Corpus, which: ScoredSetName) -> ScoredSet:
    """Select the Scored_Set from the corpus.

    ``which="eval"`` returns only the items whose split is ``eval`` (the headline
    measurement set); ``which="full"`` returns every item in the corpus
    (Requirement 6.7). Any other value is rejected.
    """
    if which == "eval":
        items = tuple(item for item in corpus.items if split_of(item) == EVAL_SPLIT)
    elif which == "full":
        items = tuple(corpus.items)
    else:
        raise ValueError(f"unknown scored set: {which!r}; expected 'eval' or 'full'")
    return ScoredSet(which=which, items=items)
