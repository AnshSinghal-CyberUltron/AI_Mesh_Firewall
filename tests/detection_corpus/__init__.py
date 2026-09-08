"""Detection corpus package (G0.1).

Test-only, additive artefact: a labelled detection corpus plus a pure
``corpus_lint.py`` validator and its pytest wrapper. Everything in this package
lives under ``tests/detection_corpus/`` and changes no production code and no
scanner verdict (Requirement 9, no blast radius).

SPLIT_SEED
----------
The deterministic train/eval split (Requirement 8) is a pure function of each
item's committed ``id``: ``assign_split(id)`` maps
``sha256(SPLIT_SEED + id)`` into ``[0, 1)`` and assigns ``eval`` below the
eval-fraction, else ``train``. The canonical ``SPLIT_SEED`` constant is defined
and committed in ``corpus_lint.py`` (created in task 3.1) and documented for
reuse in ``SAMPLING_METHODOLOGY.md``. It is intentionally NOT defined here; this
note only records its authoritative location so any party rebuilding the corpus
knows where to find the single source of truth. Do not duplicate the seed value
in this file.
"""
