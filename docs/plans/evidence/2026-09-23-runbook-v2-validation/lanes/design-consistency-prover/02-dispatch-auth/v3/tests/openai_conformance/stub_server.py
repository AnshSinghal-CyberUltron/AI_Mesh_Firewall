# ruff: noqa: E402
"""Boot the stubbed v1 app on loopback TCP for Node + live-socket jobs."""

from __future__ import annotations

import os
import sys
from pathlib import Path

# Script: sys.path must be set before ai_mesh_gateway imports.
# ruff: noqa: E402

_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_ROOT))
sys.path.insert(0, str(_ROOT.parent / "gateway"))
sys.path.insert(0, str(_ROOT.parent / "shared"))

import uvicorn

import ai_mesh_gateway.main as gm
from tests.openai_conformance.test_openai_sdk_compat_live_uvicorn import _apply_stubs, _free_port


def main() -> None:
    _apply_stubs()
    port = int(os.environ.get("AMF_STUB_PORT", "0") or 0) or _free_port()
    url = f"http://127.0.0.1:{port}"
    url_file = os.environ.get("AMF_STUB_URL_FILE", "").strip()
    if url_file:
        Path(url_file).write_text(url + "\n", encoding="utf-8")
    print(url, flush=True)
    uvicorn.run(gm.app, host="127.0.0.1", port=port, log_level="error", lifespan="off")


if __name__ == "__main__":
    main()
