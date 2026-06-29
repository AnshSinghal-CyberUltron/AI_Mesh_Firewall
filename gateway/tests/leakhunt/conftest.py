"""Leak-hunt suite bootstrap.

This suite lives OUTSIDE ``ai_mesh_gateway/`` (the CLAUDE.md gate runs
``cd gateway && pytest tests/leakhunt -q``), so the gateway package's own conftest
is not on the collection path. Put the gateway source dir on ``sys.path`` so
``import main`` / ``from llm_router import ...`` resolve exactly as they do in the
in-tree tests, and the leakhunt dir itself so ``import corpus`` /
``import recording_provider`` resolve as flat sibling modules.
"""
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
# gateway/tests/leakhunt -> gateway/ai_mesh_gateway
_GATEWAY_SRC = _HERE.parents[1] / "ai_mesh_gateway"

for p in (str(_GATEWAY_SRC), str(_HERE)):
    if p not in sys.path:
        sys.path.insert(0, p)
