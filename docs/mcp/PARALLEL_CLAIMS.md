# Parallel Session Claims — MCP Gateway Hardening

Two autonomous sessions work this repo in parallel. **Claim before edit; release on commit.**

## Session ownership (stable)

| Session | Owns (edit) | Never edit |
|---------|-------------|------------|
| **Cursor** (this session) | `frontend/**`, `services/mcp-broker/sandbox-image/agent/**`, `services/mcp-broker/src/sandbox/docker_manager.py`, `docs/**` | gateway (`mcp_proxy`, `mcp_ws_adapter`, `mcp_stdio_adapter`, `mcp_sandbox_client`), broker `routes.py`, harness |
| **Claude Code** | gateway backend, broker `routes.py`, harness | Cursor-owned paths above |

## Seam contract

Cross-seam work must follow `docs/mcp/SANDBOX_TRANSPORT_CONTRACT.md` and be ratified via `hive-mind_consensus` before implementation.

## Active claims

| Agent | Files / scope | Story | Since (UTC) | Status |
|-------|---------------|-------|-------------|--------|
| cursor-ralph-iter1 | `docs/mcp/PARALLEL_CLAIMS.md`, `mcp-parallel/**`, `docs/mcp/**` | C0 coordination setup | 2026-07-02T07:15:00Z | released |
| cursor-ralph-iter1 | `mcp-parallel/findings/**`, `scripts/playwright_mcp_p1_stdio_oauth_repro.mjs` | P1.1 stdio OAuth repro | 2026-07-02T07:15:00Z | released |

## Claim protocol

1. Append a row to this table **and** `mcp-parallel/claims/<agent>-<story>.claim`.
2. Only edit files in your owned set.
3. On commit, set claim `status: released` and remove or mark released in the claim file.

## Hive

- Hive ID: `hive-1782976205971-u1gav1`
- Cursor agent: `cursor-ralph-iter1` (specialist, joined iter 1)
