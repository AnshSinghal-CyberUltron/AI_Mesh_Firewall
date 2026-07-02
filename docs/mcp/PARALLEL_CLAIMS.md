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
| cursor-ralph-iter12 | `services/mcp-broker/src/sandbox/docker_manager.py`, `services/mcp-broker/tests/test_sandbox_lifecycle.py`, `services/mcp-broker/tests/test_sandbox_routes.py`, `services/mcp-broker/tests/test_sandbox_reaper.py`, `docs/mcp/PARALLEL_CLAIMS.md`, `.cursor/ralph/scratchpad.md` | P4.12 gVisor + docker hardening | 2026-07-02T15:00:00Z | released |
| cursor-ralph-iter13 | `mcp-parallel/findings/p4-13/**`, `docs/mcp/PARALLEL_CLAIMS.md`, `.cursor/ralph/scratchpad.md` | P4.13 blocked + P5.14 B1 verify | 2026-07-02T15:00:00Z | released |
| cursor-ralph-iter14 | `frontend/**`, `mcp-parallel/findings/p5-15/**`, `docs/mcp/PARALLEL_CLAIMS.md`, `.cursor/ralph/scratchpad.md` | P5.15 B2 pending-auth verify | 2026-07-02T16:00:00Z | released |
| cursor-ralph-iter15 | `scripts/playwright_mcp_b4_verify.mjs`, `scripts/playwright_mcp_b1_b2_b4_e2e.mjs`, `mcp-parallel/findings/p5-16/**`, `mcp-parallel/findings/p5-17/**`, `docs/mcp/PARALLEL_CLAIMS.md`, `.cursor/ralph/scratchpad.md`, `scripts/ralph/mcp_progress.md` | P5.16 B4 + P5.17 E2E verify | 2026-07-02T17:00:00Z | released |
| cursor-ralph-iter17 | `services/mcp-broker/src/sandbox/docker_manager.py`, `services/mcp-broker/tests/test_sandbox_lifecycle.py`, `mcp-parallel/findings/p6-19/**`, `docs/mcp/PARALLEL_CLAIMS.md`, `.cursor/ralph/scratchpad.md`, `scripts/ralph/mcp_progress.md` | P6.19 prep + P7.23 N1 npm hardening | 2026-07-02T18:30:00Z | released |

## Claim protocol

1. Append a row to this table **and** `mcp-parallel/claims/<agent>-<story>.claim`.
2. Only edit files in your owned set.
3. On commit, set claim `status: released` and remove or mark released in the claim file.

| cursor-ralph-iter18 | `services/mcp-broker/sandbox-image/agent/stdio_manager.py`, `services/mcp-broker/sandbox-image/agent/tests/test_stdio_manager_packages.py`, `docs/mcp/PARALLEL_CLAIMS.md`, `.cursor/ralph/scratchpad.md`, `scripts/ralph/mcp_progress.md` | P7.23 N2+N3 package allowlist + pinned-pkg parity | 2026-07-02T09:16:00Z | released |

| cursor-ralph-iter19 | `scripts/mcp_multi_org_harness.py`, `mcp-parallel/findings/p8-26/**`, `docs/mcp/PARALLEL_CLAIMS.md`, `.cursor/ralph/scratchpad.md`, `scripts/ralph/mcp_progress.md` | P8.26 3-org × 5-server provisioning + multi-org harness | 2026-07-02T09:27:00Z | released |

| cursor-ralph-iter20 | `scripts/mcp_multi_org_harness.py`, `services/mcp-broker/src/sandbox/docker_manager.py`, `services/mcp-broker/tests/test_sandbox_lifecycle.py`, `mcp-parallel/findings/p8-26/**`, `docs/mcp/PARALLEL_CLAIMS.md`, `.cursor/ralph/scratchpad.md`, `scripts/ralph/mcp_progress.md` | P8.26 harness (item 27 parallel 15-MCP driver + isolation) + nproc/UID shared-cap fix | 2026-07-02T09:45:00Z | released |

| cursor-ralph-iter21 | `scripts/mcp_multi_org_harness.py`, `mcp-parallel/findings/p9-28/**`, `docs/mcp/PARALLEL_CLAIMS.md`, `.cursor/ralph/scratchpad.md`, `scripts/ralph/mcp_progress.md` | P9.28 concurrency 2nd-oracle corroboration + #29 saturation boundary | 2026-07-02T10:05:00Z | released |

| cursor-ralph-iter22 | `scripts/mcp_multi_org_harness.py` (SUSTAINED phase), `mcp-parallel/findings/p9-29/CURSOR_*`, `docs/mcp/PARALLEL_CLAIMS.md`, `.cursor/ralph/scratchpad.md`, `scripts/ralph/mcp_progress.md` | P9.29 CURSOR angle — broker sandbox reuse/pooling + pids cgroup limits + no orphan/503 storm under sustained load (complementary to claude-mcp-item29 `mcp_load_live.py`) | 2026-07-02T10:22:00Z | released |

| claude-ralph-stress | `gateway/ai_mesh_gateway/{policy_engine,output_guard,bedrock_scanner,scanner,patterns,typed_placeholder_redactor,context_guard}.py`, `gateway/tests/golden/**`, `frontend/src/components/{ModelConnectionPanel,OutputPipelineTimeline}.jsx`, `scripts/ralph/stress_scratchpad.md`, `scripts/ralph/precommit-secret-scan.sh`, `docs/stress/**` | R0–R7 chat-pipeline adversarial stress + client E2E + trace-card polish | 2026-07-02T09:30:43Z | active |

| cursor-ralph-iter23 | `scripts/mcp_oauth_transport_live.py`, `mcp-parallel/findings/p9-31/**`, `docs/mcp/PARALLEL_CLAIMS.md`, `.cursor/ralph/scratchpad.md`, `scripts/ralph/mcp_progress.md` | P9.31 OAuth/transport correctness under load | 2026-07-02T10:50:00Z | released |

| cursor-ralph-iter24 | `scripts/mcp_p10_recursive_gate.py`, `mcp-parallel/findings/p10-32/**`, `mcp-parallel/findings/p4-13/**`, `docs/mcp/PARALLEL_CLAIMS.md`, `.cursor/ralph/scratchpad.md`, `scripts/ralph/mcp_progress.md` | P10.32 recursive P3-P9 verification + P4.13/P6.18 recheck | 2026-07-02T11:00:00Z | released |

| cursor-ralph-iter25 | `scripts/mcp_sandbox_transport_verify.py`, `mcp-parallel/findings/p4-13/**`, `docs/mcp/gateway-integration-checklist.md`, `docs/mcp/PARALLEL_CLAIMS.md`, `.cursor/ralph/scratchpad.md` | P4.13/P6.18 recheck + transport verify prep | 2026-07-02T11:30:00Z | released |

| cursor-ralph-iter26 | `mcp-parallel/findings/p4-13/RECHECK_ITER26.md`, `docs/mcp/PARALLEL_CLAIMS.md`, `.cursor/ralph/scratchpad.md`, `scripts/ralph/mcp_progress.md`, `mcp-parallel/claims/cursor-ralph-iter26-P7.23.claim` | P7.23 N2+N3 verify + P4.13/P6.18 iter26 recheck | 2026-07-02T11:35:00Z | released |

> **New program — chat-pipeline adversarial stress (`claude-ralph-stress`).** Separate from the MCP-hardening work above. Owns gateway **chat** modules (a Claude-Code-owned area), the golden suite, and a **narrow** frontend carve-out of only `ModelConnectionPanel.jsx` + `OutputPipelineTimeline.jsx` (Cursor's broad `frontend/**` is otherwise respected). Never touches `MCPConnectorPanel.jsx`, `mcp_*` gateway files, or `services/mcp-broker/**`. A pre-commit secret-scan guard (`scripts/ralph/precommit-secret-scan.sh`, installed at `.git/hooks/pre-commit`) blocks any commit containing the runtime OpenRouter key (one-way fingerprint) or a real-shape `sk-or-` token outside vetted redaction fixtures — this protects **all** sessions.

## Hive

- Hive ID: `hive-1782991737290-ylo911` (re-init iter26; prior `hive-1782976205971-u1gav1`)
- Cursor agents: `cursor-ralph-iter26` (iter 26), `cursor-ralph-iter1` (iter 1), `cursor-ralph-iter2` (iter 2)
