# CHG-0102 — bound the CHG-0100 cross-block split-check cost (resource-bomb containment)

**Change-id:** CHG-0102
**Date:** 2026-07-03
**Severity:** MEDIUM (DoS-amplification self-correction; no leak — corrects a performance regression I introduced in CHG-0100).
**Area:** HARDEN item 16 "resource bombs contained" — the scan path.
**Files:** `gateway/ai_mesh_gateway/mcp_proxy.py` (`_boundary_concat` + `_result_has_split_secret`); `gateway/ai_mesh_gateway/tests/test_mcp_result_cross_block_split.py` (+1 test).
**Whose work it touches:** my own CHG-0100 (self-correction); the owning-session MCP scan core.

## Root cause

A resource-bomb probe of the scan path (malicious upstream result designed to DoS) found: the scan fails CLOSED on a JSON bomb (deeply-nested → `SCAN_ERROR` block), but a large result is slow — a 5 MB multi-block result took ~9 s, of which **`_result_has_split_secret` (CHG-0100) alone was ~3 s (33 %)**. CHG-0100 concatenated ALL content-block text (`"".join(texts)`) and ran three detector passes (`detect_secrets` + `detect_credential_exposure` + `detect_pii`) over the full O(total_text) concatenation. Under the 5k–10k-concurrent stress scenario, that 3 s amplification per large result is a real DoS vector I introduced.

## The fix (CHG-0102)

A secret/credential token is short, so a cross-block split only spans a block BOUNDARY within `_MCP_SPLIT_SECRET_SPAN` (default 512, env-overridable) chars. `_boundary_concat` trims each block to its boundary regions — a block ≤ 2×span is kept whole; a longer block keeps only its first-span + last-span chars, with a `\n\x00\n` sentinel between them (so its own halves can't form a false cross-boundary span). A long block's INTERIOR is dropped — a contiguous secret there is already covered by the full-text scan. `_result_has_split_secret` now scans `_boundary_concat(texts)`, bounding its cost to O(num_blocks × span) instead of O(total_text).

## Verification (all green)

```
cd gateway
./.venv/bin/python -m pytest ai_mesh_gateway/tests/test_mcp_result_cross_block_split.py -q   # 9 passed
./.venv/bin/python -m pytest ai_mesh_gateway/tests -q                                        # 1605 passed, 0 failed
cd ../services/mcp-broker && ./.venv/bin/python -m pytest tests -q -k "not websocket"        # 108 passed
```

**Perf (measured):** the 5 MB multi-block split-check dropped from **3.03 s → 0.378 s** (~8×). **Correctness (unchanged):** 2-way split still caught; 3-way split (short middle block fully inside the secret) still caught; a boundary split where the LEFT block is ~700 KB (interior trimmed) still caught; a contiguous secret in a long block's interior is NOT falsely flagged (the full-text scan covers it); benign multi-block still passes. All CHG-0100 tests still pass.

## Scope / honesty note

Corrects a DoS-amplification I introduced in CHG-0100 while preserving detection of realistic cross-block splits (secrets/credentials ≤ span chars). RESIDUAL: a very long secret (e.g. a >512-char JWT) split so that a fragment falls in a long block's TRIMMED interior could be missed by the split-check — extreme + narrow; span is env-tunable, and the normal full-text scan still catches any contiguous long secret. The pre-existing full-text detect cost on very large results (~10 s for a 9 MB single block) is a separate, product-level trade-off (capping/blocking large legit results) — not changed here. Does not change the host-blocked live-stress status (items 14–20). Partial coverage is not completion.
