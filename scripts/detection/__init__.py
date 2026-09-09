"""Posture scoring harness (G0.2).

Measurement-only, strictly additive package that scores three named detection
postures of the shipped scanner over the labelled detection corpus and emits a
committed, human-readable report to ``docs/perf/``.

This package changes no line of ``gateway/ai_mesh_gateway/scanner.py`` or any
other production path; every file it introduces lives under ``scripts/detection/**``
or ``docs/perf/**`` (Requirement 9).
"""
