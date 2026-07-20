# CHG-0100 — secret SPLIT ACROSS content-array items evaded the tool-result scan

**Change-id:** CHG-0100
**Date:** 2026-07-03
**Severity:** MEDIUM–HIGH (fail-open 1.4 leak; client-concat-dependent reconstruction — completes the split-evasion family).
**Area:** HARDEN 1.4 — field-level redaction of tool RESULTS (byte-verified, fail-closed).
**Files:** `gateway/ai_mesh_gateway/mcp_proxy.py` (`_result_content_texts` + `_result_has_split_secret` + `_scan_tool_result_floor`); `gateway/ai_mesh_gateway/tests/test_mcp_result_cross_block_split.py` (new).
**Whose work it touches:** the owning-session MCP scan core; the last member of the split-evasion family (SSE multi-line CHG-0093, markdown-split CHG-0099, content-array-split this).

## Root cause

An MCP tool result's `content` is an ARRAY of blocks. A malicious upstream can split a secret so each half is a benign sub-pattern in adjacent blocks — `{"content":[{"type":"text","text":"…AKIAIOSFOD"},{"type":"text","text":"NN7EXAMPLE…"}]}`. The whole-payload scan sees the two texts separated by JSON structure (`"},{"type":"text","text":"`), so the value `AKIAIOSFODNN7EXAMPLE` is never contiguous → **nothing is detected** (`tags=[]`). But a client that CONCATENATES the text blocks (for display or to feed the model) reconstructs the full secret. This was the residual flagged by CHG-0099.

Empirically confirmed pre-fix: a 2-block split of an AWS key egressed with `tags=[]` (undetected); the block-text concatenation is `the key is AKIAIOSFODNN7EXAMPLE end`.

## The fix (CHG-0100)

`_scan_tool_result_floor` now runs a cross-block check (unless a per-tool `monitor` action wins): `_result_has_split_secret` concatenates the text of all content blocks and scans it for HIGH-CONFIDENCE secrets/credentials (`detect_secrets` + `detect_credential_exposure` + SECRET-tagged `detect_pii`). A kind that appears in the concatenation but NOT wholly inside any single block was reconstructed only by the join → the result **fails CLOSED (block)** — a cross-block split cannot be masked in place. Scoped to secrets/credentials (NOT generic PII), so the `""`-join cannot false-fire on two adjacent benign blocks (a real AWS key / token forming across a boundary from legit text is astronomically unlikely). A secret wholly inside one block is untouched by this check — the normal redaction floor masks it (no over-block).

## Verification (all green)

```
cd gateway
./.venv/bin/python -m pytest ai_mesh_gateway/tests/test_mcp_result_cross_block_split.py -q   # 8 passed
./.venv/bin/python -m pytest ai_mesh_gateway/tests -q                                        # 1602 passed, 0 failed
cd ../services/mcp-broker && ./.venv/bin/python -m pytest tests -q -k "not websocket"        # 108 passed
```

8 tests: secret split across 2 blocks → blocked (`cross_block_split_secret` meta); split across 3 blocks → blocked; contiguous secret in one block → REDACTED, NOT split-blocked (no over-block); benign multi-block → passes; single block → no split check; `monitor` posture → observe-only, not blocked; helper units (`_result_content_texts`, `_result_has_split_secret`).

## Scope / honesty note

Completes the split-evasion family on the MCP result path (SSE multi-line split CHG-0093, markdown/encoded split CHG-0099, content-array split this). This is a BLOCK (fail-closed) decision, not a content mask, so aidefence-as-oracle does not apply (aidefence is also blind to AWS-key-class secrets); the behavior test (split→blocked + contiguous→redacted-not-blocked + benign→pass) is authoritative. The reconstruction is client-concat-dependent (a client joining blocks with a separator the model does not strip would not reconstruct), but blocking on the worst-case `""`-join is the conservative fail-closed choice. Does not change the host-blocked live-stress status (items 14–20). Partial coverage is not completion.
