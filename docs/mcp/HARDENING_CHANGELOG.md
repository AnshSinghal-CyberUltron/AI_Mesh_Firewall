# MCP Hardening Changelog — canonical four-memory log

Canonical, human-readable ledger for the **MCP-hardening BACKSTOP** effort (Claude Code + Cursor
sessions running in parallel). This file is memory #4 of four. Every hardening change MUST be logged
to **all four** memories in the same commit:

1. **Ruflo** — `mcp__ruflo__memory_store(namespace="mcp-hardening/changes", key="<change-id>", value=…)`
   followed by `mcp__ruflo__hooks_notify(...)` so sibling sessions see it live.
2. **Claude** — a one-line pointer under *MCP Hardening BACKSTOP changelog* in the repo-root `AGENTS.md`.
3. **Cursor** — an entry in `.cursor/rules/mcp-hardening-changelog.mdc`.
4. **This file** (`docs/mcp/HARDENING_CHANGELOG.md`) — the full, canonical entry (§Entries below).

## §0 — Protocol (READ FIRST)

Rationale: multiple autonomous sessions edit the same worktree/index concurrently
(see `~/.claude/.../memory/gateway-shared-index-hazard.md`). Without a single traceable ledger, a
backstop edit that corrects another session's omission is invisible and can be silently reverted or
duplicated. This protocol makes every change attributable, reversible, and verifiable.

Rules:
- **One change-id per logical change.** Format: `CHG-NNNN` (zero-padded, monotonic). Never reuse an id.
- **Log in the SAME commit as the change.** A code change without its four-memory entry is incomplete.
- **Every entry carries, at minimum:** change-id · date · files · WHAT changed · WHY (which
  gap/mistake/omission) · what it NOW DOES · whose work it touched · how to VERIFY.
- **Commit small; rebase from `main` often.** Stage narrowly (never `git add -A`) — the index is shared.
- **Evidence wins.** A "redact/block" verdict is valid ONLY if the captured egress bytes reflect it;
  fail closed on a no-op scrub. Cross-check leak claims with an independent oracle
  (`mcp__ruflo__aidefence_scan` / `aidefence_has_pii`) over the actual bytes.
- **Gates before any scratchpad `[x]`:** relevant pytest + broker tests + the item's stress harness
  green; frontend → build + Playwright verified.

### Entry template (copy for each new change)

```
### CHG-NNNN — <short title>
- **Date:** YYYY-MM-DD
- **Scratchpad item:** G<n> item <n> (scripts/ralph/mcp_hardening_scratchpad.md)
- **Files:** path:line …
- **WHAT:** <the concrete change>
- **WHY (gap/mistake/omission):** <root cause / what was missing or wrong>
- **NOW DOES:** <post-change behavior / invariant now enforced>
- **Touched whose work:** <backstop-only | corrects <session/commit> | extends <session/commit>>
- **VERIFY:** <exact commands / harness / oracle to reproduce green>
```

## §Entries

### CHG-0001 — Establish the four-memory hardening changelog protocol
- **Date:** 2026-07-02
- **Scratchpad item:** G0 item 0 (four-memory changelog + runsc enforcement audit)
- **Files:** `docs/mcp/HARDENING_CHANGELOG.md` (new) · `.cursor/rules/mcp-hardening-changelog.mdc` (new)
  · `AGENTS.md` (new *MCP Hardening BACKSTOP changelog* section) · `scripts/ralph/mcp_hardening_scratchpad.md` (item 0 marked).
- **WHAT:** Created the canonical changelog + §0 protocol + entry template, the Cursor rule mirror, and
  the Claude/AGENTS.md pointer. Bootstraps the traceability discipline the whole BACKSTOP effort requires.
- **WHY (gap/mistake/omission):** None of the four changelog memories existed
  (`HARDENING_CHANGELOG.md`, `BACKSTOP_FINDINGS.md`, `.cursor/rules/mcp-hardening-changelog.mdc` all
  absent). Parallel sessions had no shared, attributable ledger, so a backstop correction of another
  session's omission would be invisible and could be reverted/duplicated.
- **NOW DOES:** Every subsequent hardening change has one place to record change-id + files + what/why +
  verification, mirrored to Ruflo, Claude, and Cursor memories in the same commit.
- **Touched whose work:** backstop-only (net-new docs + protocol; no code paths altered).
- **VERIFY:**
  - Files exist: `ls docs/mcp/HARDENING_CHANGELOG.md .cursor/rules/mcp-hardening-changelog.mdc`
  - AGENTS.md pointer: `grep -n "MCP Hardening BACKSTOP changelog" AGENTS.md`
  - Ruflo entry: `mcp__ruflo__memory_search(query="CHG-0001", namespace="mcp-hardening/changes")`
  - Runsc enforcement audited fail-closed (see note below): `services/mcp-broker/src/sandbox/docker_manager.py:343-359`.

**Item-0 runsc audit (no code change needed — mechanism already present & correct):**
`DockerSandboxManager._resolve_runtime()` (`docker_manager.py:343`) raises `SandboxRuntimeUnavailableError`
(fail-closed) when `MCP_SANDBOX_RUNTIME_REQUIRED=true` and either `MCP_SANDBOX_RUNTIME` is unset OR the
named runtime (e.g. `runsc`) is not reported by `docker info`.`Runtimes`. Production deployments MUST set
`MCP_SANDBOX_RUNTIME=runsc` **and** `MCP_SANDBOX_RUNTIME_REQUIRED=true`; with both set, a host missing
gVisor refuses to launch sandboxes rather than silently downgrading to the default runc runtime. This is
recorded here as the item-0 enforcement contract; verification of the *runtime-required* env being set in
the prod compose/manifests is tracked under G3 item 12.

### CHG-0002 — Backstop audit → ranked findings (BACKSTOP_FINDINGS.md)
- **Date:** 2026-07-02
- **Scratchpad item:** G1 item 1 (backstop audit of parallel Claude+Cursor sessions)
- **Files:** `docs/mcp/BACKSTOP_FINDINGS.md` (new) · `scripts/ralph/mcp_hardening_scratchpad.md` (item 1 [x] + priority order).
- **WHAT:** Ran a read-only 6-auditor parallel workflow (`wf_ca0d6349-33e`, 7 agents, 904k tokens) over the
  gateway guardrail chain, control-plane compliance/authz, broker sandbox, stress harnesses, and frontend;
  synthesized/deduped to 24 findings (13 high · 8 medium · 1 low), each grounded in file:line + a verify command.
- **WHY (gap/mistake/omission):** No consolidated, evidence-based map of what the parallel sessions left
  incomplete/wrong existed; items 2–20 had no prioritized, verifiable roadmap.
- **NOW DOES:** Gives the backstop a ranked, corroboration-annotated worklist. Key confirmed gaps: SSE
  `ext_mcp_proxy` egress unscanned (raw PII/secret today); per-actor authz absent on stdio/ws adapter path;
  compliance tags are audit-only (no tag-driven enforcement) with two disjoint vocabularies; gVisor/egress
  default fail-open; only stdio reaches the sandbox; a **dead cross-tenant oracle** (`for fs in []` →
  `foreign_org_events` structurally 0) and a **15-sandbox scale ceiling** (hardcoded 3 orgs) that overstate
  prior "isolation/scale validated" claims.
- **Touched whose work:** backstop-only (net-new audit doc). Findings implicate prior sessions' commits
  (e.g. 5d0dd346 "scale matrix validated" was 6/6 403s at 15 MCPs) but no code was changed.
- **VERIFY:** Spot-verified 3 load-bearing claims directly: `sed -n '1021,1035p' gateway/ai_mesh_gateway/mcp_proxy.py`
  (SSE `aiter_bytes` passthrough); `python3 -c "print(any(1 for fs in []))"` → False +
  `sed -n '129p' scripts/mcp_scale_matrix_live.py`; `grep -n 'ORGS = \[' -A5 scripts/mcp_scale_provision.py`
  (literal 3-tuple). Full doc: `docs/mcp/BACKSTOP_FINDINGS.md`. Ruflo: `memory_search CHG-0002 namespace mcp-hardening/changes`.
