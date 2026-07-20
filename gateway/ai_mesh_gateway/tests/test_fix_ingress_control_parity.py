"""I-06 / I-07 — INGRESS-CONTROL PARITY between /v1/chat/completions and /v1/embeddings.

These were filed as two MEDIUM bugs but they are one architectural defect:
``proxy_embeddings`` was built by copying a SUBSET of the chat ingress chain, so every
control added to chat afterwards has to be remembered here by hand. Two were not:

  I-06  per-KEY TPM ceiling  — ``RATE_LIMITER.check_rate_limit`` was never called
        (and ``record_usage`` never charged the bucket), so a key exhausted on chat
        kept embedding without limit and ``/v1/usage`` showed a flat ``tpm_used``.
  I-07  ``blocked_keywords`` — enforced on chat, absent here, so a term the org
        forbids in conversation was still embeddable and indexable.

Fixing only those two would leave the PATTERN intact. This module is the structural
guard: it asserts the two handlers' ingress-control lists stay in sync, so the next
control added to chat cannot silently skip embeddings.

Run: cd gateway && .venv/bin/python -m pytest \
    ai_mesh_gateway/tests/test_fix_ingress_control_parity.py -q -p no:cacheprovider
"""
from __future__ import annotations

import ast
import pathlib

MAIN = pathlib.Path(__file__).resolve().parents[1] / "main.py"

# Ingress controls that MUST exist on both surfaces, expressed as the source token
# that proves the control is wired. Deliberately source-level: this is an
# architectural assertion about the handlers, not a behavioural one.
REQUIRED_ON_BOTH = {
    "per-key TPM ceiling": "check_rate_limit",
    "per-key TPM reconcile": "record_usage",
    "org blocked_keywords": "blocked_keywords",
    "kill-switch": "check_kill_switch",
    "model-state isolation": "check_model_state",
}


def _handler_source(name: str) -> str:
    tree = ast.parse(MAIN.read_text())
    for node in ast.walk(tree):
        if isinstance(node, (ast.AsyncFunctionDef, ast.FunctionDef)) and node.name == name:
            return ast.get_source_segment(MAIN.read_text(), node) or ""
    raise AssertionError(f"handler {name!r} not found in main.py")


def test_embeddings_ingress_control_parity_with_chat():
    """Every control in REQUIRED_ON_BOTH must be wired on BOTH handlers.

    If this fails after you added a control to proxy_chat, the fix is to add it to
    proxy_embeddings too — not to delete the entry. Embeddings reach the SAME
    third-party providers with the SAME tenant data; a control that only guards chat
    leaves an equivalent egress path wide open.
    """
    chat = _handler_source("proxy_chat")
    emb = _handler_source("proxy_embeddings")
    missing_chat = [n for n, tok in REQUIRED_ON_BOTH.items() if tok not in chat]
    missing_emb = [n for n, tok in REQUIRED_ON_BOTH.items() if tok not in emb]
    assert not missing_chat, f"controls missing from proxy_chat: {missing_chat}"
    assert not missing_emb, (
        f"controls missing from proxy_embeddings: {missing_emb}. "
        "Embeddings is not a lesser surface — it egresses tenant data to the same "
        "providers. Add the control here rather than removing it from the list.")


def test_embeddings_handler_did_not_shrink_below_its_known_controls():
    """Guards against a refactor silently dropping the I-06/I-07 additions."""
    emb = _handler_source("proxy_embeddings")
    for token in ("_emb_est_tokens", "_emb_firewall_disabled", "rate_limit_tpm"):
        assert token in emb, f"embeddings ingress lost {token!r}"
