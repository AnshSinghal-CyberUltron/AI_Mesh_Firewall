"""Serve the genuine ``build_app`` over a scripted provider on a loopback port (for Node cells).

Prints ``PORT=<n>`` once listening, then runs until stdin closes. Test tooling only::

    python -m tests.openai_conformance.sdk_matrix.serve_scripted
"""

from __future__ import annotations

import sys

from tests.openai_conformance.sdk_matrix.harness import (
    LoopbackServer,
    build_matrix_app,
    matrix_contract,
)
from tests.openai_conformance.sdk_matrix.scripts import scripted_provider


def main() -> int:
    app = build_matrix_app(contract=matrix_contract(), provider=scripted_provider())
    with LoopbackServer(app) as server:
        print(f"PORT={server.port}", flush=True)
        sys.stdin.read()  # block until the parent closes our stdin
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
