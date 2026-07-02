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
| cursor-ralph-iter2 | `docs/mcp/PARALLEL_CLAIMS.md`, `mcp-parallel/**`, `.cursor/ralph/scratchpad.md` | C0 hive join iter2 | 2026-07-02T07:35:00Z | released |
| cursor-ralph-iter2 | `mcp-parallel/findings/p1-2/**`, `scripts/playwright_mcp_p1_http_oauth_repro.mjs` | P1.2 HTTP oauth 0-tools repro | 2026-07-02T08:00:00Z | released |
| cursor-ralph-iter3 | `mcp-parallel/findings/p1-3/**`, `scripts/playwright_mcp_p1_dialog_focus_repro.mjs` | P1.3 Add-Server dialog focus loss (B4) | 2026-07-02T08:15:00Z | released |
| cursor-ralph-iter5 | `docs/mcp/oss-research-remote-transport-proxies.md`, `mcp-parallel/findings/p2-5/**`, `docs/mcp/PARALLEL_CLAIMS.md` | P2.5 OSS remote transport research | 2026-07-02T10:00:00Z | released |
| cursor-ralph-iter6 | `docs/mcp/oss-research-docker-hardening.md`, `mcp-parallel/findings/p2-6/**`, `docs/mcp/PARALLEL_CLAIMS.md`, `.cursor/ralph/scratchpad.md` | P2.6 gVisor + Docker hardening research | 2026-07-02T11:00:00Z | released |
| cursor-ralph-iter7 | `docs/mcp/oss-research-oauth-sandbox-client.md`, `mcp-parallel/findings/p2-7/**`, `docs/mcp/PARALLEL_CLAIMS.md`, `.cursor/ralph/scratchpad.md` | P2.7 MCP OAuth sandbox research | 2026-07-02T12:00:00Z | released |
| cursor-ralph-iter8 | `docs/mcp/SANDBOX_TRANSPORT_CONTRACT.md`, `mcp-parallel/findings/p3-8/**`, `docs/mcp/PARALLEL_CLAIMS.md`, `.cursor/ralph/scratchpad.md` | P3.8 sandbox transport contract | 2026-07-02T13:00:00Z | released |

## Claim protocol

1. Append a row to this table **and** `mcp-parallel/claims/<agent>-<story>.claim`.
2. Only edit files in your owned set.
3. On commit, set claim `status: released` and remove or mark released in the claim file.

## Hive

- Hive ID: `hive-1782976205971-u1gav1`
- Cursor agents: `cursor-ralph-iter1` (iter 1), `cursor-ralph-iter2` (iter 2, joined 2026-07-02)
