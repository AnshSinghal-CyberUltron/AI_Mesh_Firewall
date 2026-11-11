"""Static edge-config regression gate surfaced to CI (GW16b / B5, task 3.2, L16b-7).

Shells the shipped static gate `gateway_v2/deploy/edge/check_edge_keepalive.sh` so it is
collected by the standard `pytest` run (and therefore by CI) as part of the `gateway_v2` edge
test suite. The gate is a pure source grep over the v2 edge conf template -- it needs NO Docker
and NO container, so these tests always run (R7.3).

Two tests mirror the design's §4 pytest-wrapper + the R7.2 negative control:

- `test_edge_keepalive_gate_passes` runs the shipped script against the real v2 edge dir and
  asserts exit 0 (the shipped conf passes the static gate).
- `test_edge_keepalive_gate_fails_when_keepalive_removed` copies the edge dir into a tmp dir,
  strips ONLY the live `keepalive 64;` directive from the temp conf (leaving the explanatory
  comment, matching how the script hardened against comment false-positives), runs the COPIED
  script against that mutated copy, and asserts a non-zero exit -- proving removing the keepalive
  directive reintroduces the 0.9986-conns/request / 502-above-420-RPS defect and fails CI.

The real conf is never mutated: the negative control operates entirely inside pytest's `tmp_path`.
Subprocess is invoked with a list argument (no `shell=True`, no string interpolation of untrusted
input).
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

# Resolve the repo's v2 edge dir from THIS test file's location, robustly:
# .../gateway_v2/tests/edge/test_edge_config_gate.py  ->  .../gateway_v2/deploy/edge
_EDGE_DIR = Path(__file__).resolve().parents[2] / "deploy" / "edge"
_GATE_SCRIPT = _EDGE_DIR / "check_edge_keepalive.sh"
_CONF = _EDGE_DIR / "gateway-edge.conf"
_INC = _EDGE_DIR / "security-headers.inc"

# The live keepalive directive the gate asserts (leading whitespace only, trailing semicolon),
# matching the script's own `^[[:space:]]*keepalive[[:space:]]+64;` contract. The explanatory
# COMMENT lines (e.g. `# keepalive 64 is THE fix ...`) must survive the strip so the negative
# control proves the gate matches the directive, not the comment.
_KEEPALIVE_DIRECTIVE = re.compile(r"^[ \t]*keepalive\s+64;.*$", re.MULTILINE)

_BASH = shutil.which("bash")
_requires_bash = pytest.mark.skipif(
    _BASH is None,
    reason="bash is unavailable; the static edge-config gate is a bash script (unlikely on CI)",
)


def _run_gate(script: Path) -> subprocess.CompletedProcess[str]:
    """Run a copy of the gate script, capturing stdout/stderr as text.

    The script resolves CONF/INC from its OWN SCRIPT_DIR via BASH_SOURCE, so running a copy that
    sits next to a (possibly mutated) conf makes it grep that sibling conf.
    """
    assert _BASH is not None  # guarded by _requires_bash
    return subprocess.run(
        [_BASH, str(script)],
        capture_output=True,
        text=True,
        check=False,
    )


@_requires_bash
def test_edge_keepalive_gate_passes() -> None:
    """The shipped v2 edge conf passes the static keepalive/header gate (exit 0)."""
    assert _GATE_SCRIPT.is_file(), f"missing gate script {_GATE_SCRIPT}"
    result = _run_gate(_GATE_SCRIPT)
    # Surface the script's own PASS/FAIL narration so a reviewer sees which directive is missing.
    assert result.returncode == 0, (
        "check_edge_keepalive.sh FAILED against the shipped v2 edge conf "
        f"(exit {result.returncode})\n"
        f"--- stdout ---\n{result.stdout}\n--- stderr ---\n{result.stderr}"
    )


@_requires_bash
def test_edge_keepalive_gate_fails_when_keepalive_removed(tmp_path: Path) -> None:
    """Negative control (R7.2): stripping the keepalive directive fails the gate.

    Copies the whole edge dir into tmp_path, deletes ONLY the live `keepalive 64;` directive line
    from the temp conf (leaving the comment), and runs the COPIED script against the mutated copy.
    The real conf is never touched.
    """
    assert _CONF.is_file(), f"missing conf {_CONF}"

    # Copy the edge dir (conf + include + script) into an isolated temp dir.
    temp_edge = tmp_path / "edge"
    shutil.copytree(_EDGE_DIR, temp_edge)

    temp_conf = temp_edge / "gateway-edge.conf"
    temp_script = temp_edge / "check_edge_keepalive.sh"

    # Strip ONLY the live `keepalive 64;` directive (regex line delete); keep everything else,
    # including any explanatory comment the script was hardened to ignore.
    original = temp_conf.read_text(encoding="utf-8")
    mutated, n_removed = _KEEPALIVE_DIRECTIVE.subn("", original)
    assert n_removed >= 1, (
        "expected to find a live `keepalive 64;` directive to strip in the copied conf; "
        "the gate's regex and this test's regex may have drifted"
    )
    temp_conf.write_text(mutated, encoding="utf-8")

    # Running the COPY makes it resolve CONF from the temp dir (its own SCRIPT_DIR) -> mutated conf.
    result = _run_gate(temp_script)
    assert result.returncode != 0, (
        "check_edge_keepalive.sh UNEXPECTEDLY PASSED after the keepalive directive was removed "
        "-- the L16b-7 regression gate is not actually catching keepalive removal\n"
        f"--- stdout ---\n{result.stdout}\n--- stderr ---\n{result.stderr}"
    )

    # The real conf must be untouched by this negative control.
    assert _KEEPALIVE_DIRECTIVE.search(_CONF.read_text(encoding="utf-8")) is not None, (
        "the real gateway-edge.conf lost its keepalive directive -- the negative control must "
        "operate only inside tmp_path"
    )
