# P2.6 — gVisor + Docker sandbox hardening research

**Date:** 2026-07-02  
**Agent:** cursor-ralph-iter6  
**Story:** P2 scratchpad item #6  

## Summary

Extended `docs/mcp/oss-research-docker-hardening.md` with gVisor runsc host requirements,
fail-closed prod pattern, egress default-deny-proxy architecture, and a verified
`_run_kwargs` field-by-field map against `docker_manager.py:247-285`.

## `_run_kwargs` control matrix (present / absent → P7)

| Control | H# | In `_run_kwargs`? | P7 item |
|---------|-----|-------------------|---------|
| read_only + tmpfs | H4 | **PRESENT** ✅ | — |
| mem/cpu/pids limits | H6/H7 | **PRESENT** ✅ | memswap_limit (#22) |
| per-org network | H12 | **PRESENT** ✅ | — |
| runtime=runsc hook | H10 | **conditional** (env only) | require fail-closed (#22, P4 #12) |
| no-new-privileges | H1 | **ABSENT** | #22 |
| cap_drop ALL | H2 | **ABSENT** | #22 |
| seccomp (custom) | H3 | default only | prefer gVisor |
| user= pinned | H5 | **ABSENT** (image USER only) | #25 |
| ulimits | H8 | **ABSENT** | #22 |
| init/tini | H14 | **ABSENT** | #24 |
| storage_opt / disk cap | H9 | **ABSENT** | #22/#24 |
| egress allowlist | H13 | **ABSENT** (full NAT) | #22/#23 |
| HTTP_PROXY env | H13 | **ABSENT** | #22 |

**Present today:** 8 kwargs (image, limits, read_only, tmpfs, network, volumes, labels, conditional runtime).  
**Missing for P7:** H1, H2, H5, H8, H9, H13, H14, memswap_limit, fail-closed runsc.

## gVisor runsc

- Install `runsc` on Linux host → `runsc install` → restart Docker.
- Repo: `MCP_SANDBOX_RUNTIME=runsc` → `kwargs["runtime"]` at `:283-284`.
- **Prod pattern (proposed):** `MCP_SANDBOX_RUNTIME_REQUIRED=true` → probe runsc at startup/create; **503 if unavailable** — no silent runc fallback.
- Dev (macOS): runc only; required flag stays false.
- Validate MCP init under runsc before prod flip (npx + uvicorn agent overhead).

## Egress default-deny

- Per-org bridge currently has unrestricted NAT egress (needed for npx).
- Target: host allowlist proxy (`host.docker.internal:3128`) + `HTTP(S)_PROXY` in sandbox env + nftables DROP non-proxy egress.
- Allow: npm/PyPI + per-org declared remote MCP hosts only; exact-host match; #30 canary proof.

## Artifacts

- `docs/mcp/oss-research-docker-hardening.md` (§ gVisor, § egress architecture, § `_run_kwargs` map)
- `mcp-parallel/findings/p2-6/sources.json`
- Cross-ref: `docs/mcp/broker-sandbox-lifecycle.md` §2 gaps

## Next

**P2.7** — MCP OAuth 2.1 web research (PKCE, RFC 9728/8414, stdio-wrapped-remote UX, http/ws in sandbox).

## Blockers

None for research. **Implementation blockers for P4 #12:** runsc not installed on dev macOS host; prod host must install + smoke-test before `RUNTIME_REQUIRED` flip.
