"""Attribution resolution for the posture scoring harness.

Resolves the corpus version (git commit pinning ``tests/detection_corpus/``), the
single reproducible command, and the target FPR. A run cannot emit a report with
any of these missing (Requirements 6.5, 6.6, 6.10, 3.1).

Each helper returns either its resolved value or an :class:`AttributionUnavailable`
sentinel result object naming the value that could not be determined. Returning a
sentinel (rather than raising) lets the CLI check every attribution value, name the
missing one in its error indication, and emit no report — the fail-closed honesty
Requirement 6.10 mandates. ``resolve_attribution`` composes the three helpers into a
single :class:`Attribution` or the first :class:`AttributionUnavailable` encountered.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Union

# The Target_FPR defining the headline operating point, fixed at 0.01 / 1%
# (Requirements 3.1, 6.6). This value is always determinable.
_TARGET_FPR = 0.01

# The single documented command that runs the harness (Requirements 6.6, 7.1).
# It runs inside the gateway venv so ``import ai_mesh_gateway.scanner`` resolves.
_REPRODUCIBLE_COMMAND = (
    "cd gateway && ./.venv/bin/python ../scripts/detection/score_postures.py"
)

# The corpus path (relative to the repository root) whose git commit pins the
# exact Detection_Corpus contents a run was computed over (Requirement 6.5).
_CORPUS_RELATIVE_PATH = "tests/detection_corpus"


@dataclass(frozen=True)
class AttributionUnavailable:
    """Non-success signal that a required attribution value cannot be determined.

    ``value`` names the missing attribution field (for example ``"corpus_version"``)
    so the caller can identify it in an error indication; ``reason`` is a non-empty
    human-readable explanation. Its presence in a resolution means the harness must
    emit no report (Requirement 6.10).
    """

    value: str
    reason: str

    def __post_init__(self) -> None:
        if not self.value:
            raise ValueError("AttributionUnavailable.value must be a non-empty string")
        if not self.reason:
            raise ValueError("AttributionUnavailable.reason must be a non-empty string")


@dataclass(frozen=True)
class Attribution:
    """The fully-resolved attribution metadata for a scoring run.

    All three fields are mandatory; this object is only constructed when every
    attribution value was determined (Requirements 6.5, 6.6, 6.10).
    """

    corpus_version: str
    reproducible_command: str
    target_fpr: float


# A helper returns either its resolved value or the non-success sentinel.
CorpusVersionResult = Union[str, AttributionUnavailable]
AttributionResult = Union[Attribution, AttributionUnavailable]


def corpus_version(root: Union[str, Path]) -> CorpusVersionResult:
    """Resolve the git commit id pinning ``tests/detection_corpus/`` under ``root``.

    Runs ``git log -1 --format=%H -- tests/detection_corpus`` from the repository
    ``root`` and returns the pinning commit hash. The corpus version is the commit
    that last touched the corpus path, which is sufficient to attribute and
    reproduce every number (Requirements 6.5, 10.3).

    Returns an :class:`AttributionUnavailable` (naming ``"corpus_version"``) when the
    version cannot be determined — ``root`` is not inside a git work tree, ``git`` is
    not installed or fails, or the corpus path has no commit history (for example it
    is untracked). The caller then emits no report (Requirement 6.10).
    """
    root_path = Path(root)

    if not root_path.is_dir():
        return AttributionUnavailable(
            "corpus_version",
            f"repository root {root_path!s} is not an existing directory",
        )

    corpus_path = root_path / _CORPUS_RELATIVE_PATH
    if not corpus_path.exists():
        return AttributionUnavailable(
            "corpus_version",
            f"detection corpus path {corpus_path!s} does not exist",
        )

    try:
        completed = subprocess.run(
            [
                "git",
                "log",
                "-1",
                "--format=%H",
                "--",
                _CORPUS_RELATIVE_PATH,
            ],
            cwd=str(root_path),
            capture_output=True,
            text=True,
            check=False,
        )
    except FileNotFoundError:
        return AttributionUnavailable(
            "corpus_version",
            "git executable not found; cannot determine the corpus version",
        )
    except OSError as exc:  # pragma: no cover - defensive
        return AttributionUnavailable(
            "corpus_version",
            f"failed to invoke git to determine the corpus version: {exc}",
        )

    if completed.returncode != 0:
        stderr = completed.stderr.strip()
        detail = f": {stderr}" if stderr else ""
        return AttributionUnavailable(
            "corpus_version",
            f"git could not resolve a commit for {_CORPUS_RELATIVE_PATH!r} "
            f"(is {root_path!s} a git work tree?){detail}",
        )

    commit = completed.stdout.strip()
    if not commit:
        return AttributionUnavailable(
            "corpus_version",
            f"no commit pins {_CORPUS_RELATIVE_PATH!r}; the corpus is untracked or "
            "has no history, so its version cannot be determined",
        )

    return commit


def reproducible_command() -> str:
    """Return the single documented command that regenerates the report.

    The command is fixed and always determinable (Requirements 6.6, 7.1).
    """
    return _REPRODUCIBLE_COMMAND


def target_fpr() -> float:
    """Return the Target_FPR (0.01) defining the headline operating point.

    The value is fixed and always determinable (Requirements 3.1, 6.6).
    """
    return _TARGET_FPR


def resolve_attribution(root: Union[str, Path]) -> AttributionResult:
    """Resolve all three attribution values into an :class:`Attribution`.

    Returns the first :class:`AttributionUnavailable` encountered (naming the
    missing value) if any value cannot be determined, so the caller emits no report
    (Requirement 6.10). ``reproducible_command`` and ``target_fpr`` are always
    determinable, so in practice only ``corpus_version`` can fail today; resolving
    all three here keeps the fail-closed contract in one place.
    """
    version = corpus_version(root)
    if isinstance(version, AttributionUnavailable):
        return version

    return Attribution(
        corpus_version=version,
        reproducible_command=reproducible_command(),
        target_fpr=target_fpr(),
    )
