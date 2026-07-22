# OSS research — official MCP stdio server patterns (P1 item #6)

> Deliverable for `scripts/ralph/mcp_progress.md` **P1 item #6**: how official stdio MCP servers
> start + handshake, from `modelcontextprotocol/servers` (fetched via GitHub MCP, `main`, 2026-07-02).
> Informs B3 (readiness/cold-start), the P8/P9 harness (Everything/Filesystem), and the isolation model.

## 1. How official stdio servers start
- Launch is `npx -y @modelcontextprotocol/server-<name> [stdio]` (stdio is the default transport;
  `sse`/`streamableHttp` are opt-in CLI args). Evidence: `src/everything/README.md`,
  `src/filesystem/README.md`.
- Startup flow (`src/everything/docs/startup.md`): launcher (`node dist/index.js [transport]`) → a
  **transport manager** → `createServer()` builds an `McpServer` (SDK) with declared **capabilities**
  (`tools`, `logging`, `prompts`, `resources:{subscribe}`) + server instructions, registers
  tools/resources/prompts, then connects the server to the SDK's stdio transport.
- **stdio = one process-bound connection**: `clientConnect()` on connect, `cleanup()` on `SIGINT`.
  No multiplexing (that's SSE/streamableHttp with sessionIds).
- The command is always an **allow-listed interpreter** (`npx`/`node`/`python`/`uvx`) — matches this
  repo's command allowlist (`stdio_manager.py:37`, `mcp_stdio_adapter.py:350`). ✓ No custom binaries.

## 2. The handshake (MCP stdio)
Standard MCP lifecycle over newline-delimited JSON-RPC on stdin/stdout:
1. Client → `initialize` `{protocolVersion, capabilities, clientInfo}`.
2. Server → result `{protocolVersion, capabilities, serverInfo, instructions?}`.
3. Client → `notifications/initialized` (no response).
4. Then `tools/list`, `tools/call`, etc.

**Key invariants (MCP spec + confirmed by the SDK transport):**
- **stdout carries ONLY JSON-RPC**; all logging/diagnostics go to **stderr**. A server that prints a
  banner to stdout corrupts the stream. → This repo already honors the split: the stdio adapter reads
  stdout lines as JSON-RPC (`mcp_stdio_adapter._start_reader`) and drains stderr separately
  (`_log_stderr`); the in-container agent does the same (`stdio_manager.py:120` reader / `:284` stderr).
- **There is no "ready" banner.** Readiness == the process can answer `initialize`. So a broker/agent
  cannot detect readiness by watching stdout for a marker — it must attempt the `initialize` exchange
  (with a timeout) or poll a wrapper's health. This validates the repo's model:
  `_ensure_initialized` (gateway `mcp_stdio_adapter.py:548`, agent `stdio_manager.py:369`) sends
  `initialize` with protocolVersion `2024-11-05` under an init semaphore, then `notifications/initialized`.
- **Cold-start latency is the `npx` package fetch**, not the handshake. First `npx -y <pkg>` downloads
  the package (network + disk) before the server process even starts → this is the dominant first-call
  delay behind **B3**. Mitigations that fit: warm the npm cache (broker already mounts an exec tmpfs
  `/var/npm-cache`, `docker_manager.py:275`), eager-provision + a readiness `initialize` probe with
  bounded backoff (B3 items #19-21), and treat "still fetching/starting" as a distinct *provisioning*
  state rather than a hard 502.

## 3. Everything server — the P8/P9 deterministic test server
- Launch: `npx -y @modelcontextprotocol/server-everything` (stdio default).
- Deterministic tools (fetched from `src/everything/tools/`):
  - **`echo`** — input `{ message: string }` → text `"Echo: <message>"` (`echo.ts`). readOnly, idempotent.
  - **sum tool** — ⚠️ **RENAMED**: on current `main` it is **`get-sum`**, input `{ a: number, b: number }`
    → text `"The sum of <a> and <b> is <sum>."` (`get-sum.ts`). Older published versions expose it as
    **`add`** with the same `{a,b}` schema. readOnly, idempotent.
- ⚠️ **HARNESS GOTCHA (items #26-31):** the task says "Everything.echo/add", but the tool name is
  **version-dependent** (`add` vs `get-sum`). The 15-MCP harness MUST NOT hardcode `add` — it should
  either (a) `tools/list` first and pick the sum tool by name-in {`add`,`get-sum`} / by its `{a,b}`
  schema, or (b) pin `@modelcontextprotocol/server-everything@<version>` in the registered args.
  `echo` is stable across versions. Determinism check: `echo{message:"canary-<org>"}` and
  `add|get-sum{a,b}` give exact-match assertions with no external dependency.
- Also useful: `trigger-long-running-operation` (for timeout/interrupt tests), `get-env` (⚠️ could
  reveal injected env — good for a cred-isolation leak probe in P9).

## 4. Filesystem server — the P9 cross-tenant canary
- Launch: `npx -y @modelcontextprotocol/server-filesystem <allowed-dir> [<dir2> ...]`. Requires **≥1
  allowed directory** (via args OR MCP Roots) or it errors at init.
- Tools: `write_file{path,content}`, `read_text_file{path}`, `list_directory{path}`,
  `list_allowed_directories{}`, `search_files`, `move_file`, `edit_file`, `get_file_info`, … with MCP
  ToolAnnotations (readOnlyHint/idempotentHint/destructiveHint) — a good source for the repo's
  `_classify_tool_write_risk` mapping (`control views.py:192`).
- **Canary plan (item #30):** register a filesystem server in **Org B** scoped to Org B's per-org
  volume dir and `write_file` a secret canary token there; register a filesystem server in **Org A**
  scoped to Org A's dir. Prove Org A's `read_text_file`/`list_directory`/`search_files` can NEVER see
  Org B's canary — enforced at THREE layers: (a) each org's server has different allowed dirs, (b) each
  runs in a different per-org sandbox container + volume (`docker_manager.py:225/267`), (c) gateway
  org-scope authz blocks Org A from even addressing Org B's server (`mcp_proxy.py:1562`). The captured
  egress bytes + `aidefence_scan` over them are the leak oracle.

## 5. Reusable takeaways for this repo
- Official stdio servers launch via `npx`/`uvx` (allowlisted) → the repo's command allowlist is correct;
  keep it. The supply-chain risk is the **package name in args**, not the command — reinforces the P7
  gap "no npm/PyPI package allowlist on the broker path".
- Readiness cannot be sniffed from stdout; must be an `initialize` probe → B3 fix should poll the agent
  by driving a real `initialize` (or the agent should expose per-server initialized state, which it
  already tracks via `proc.initialized`, `stdio_manager.py:408`).
- The P8/P9 harness must discover tool names dynamically (echo stable, sum tool = add|get-sum) — do not
  hardcode `add`.
- `mcp-remote` (studied further in item #8) is itself an npm stdio wrapper that turns a remote HTTP MCP
  into a local stdio server + does OAuth — this is why a stdio row can legitimately carry an HTTP URL in
  args (the B1 nuance).
