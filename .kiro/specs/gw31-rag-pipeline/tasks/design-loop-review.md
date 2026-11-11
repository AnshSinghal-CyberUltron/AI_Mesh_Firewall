# GW31 Design Loop Review (round 5)

Reviewed `design.md` (including the round 4 responses) against `requirements.md` (Req 1-20, C-1..C-9), the code in `gateway_v2/gateway_v2/` of this worktree, and the runbook card as quoted by requirements.md. I did not re-extract the .docx in this pass.

Verdict: CHANGES_REQUESTED (0 HIGH, 1 MEDIUM, 1 LOW, 1 NIT).

All round 4 findings are resolved in the text. I checked each against the code or the requirements:

- F-1 (function spans): `design.md` L328 now has the per-function rule and a line-budget table (L329-336). Baselines match: `app.py` 800, `routes.py` 799, `resources.py` 800.
- F-2 (stage binding): D-2 fails closed with `rag_internal_error`, with a test.
- F-3 (near-duplicate cap): ranker step 4 defines the cap as a fixed point against the post-cap context. I checked the worked example (10 duplicates + 2 others keep 2) and the single-cluster case (keeps 1).
- F-4 and F-8: C-9 exists in `requirements.md` L352 and covers both Req 12.4 and Req 9.3.
- F-5 and F-6: one ingest order is stated once, `put_many` is idempotent, and `WROTE_NOTHING_CODES` uses real posture codes.
- F-7: `scan_executor` is in `RagDeps` with the call shape specified.

## Findings

### F-1 (MEDIUM) The dev wiring cannot be built where the design puts it: `chat`, `scan_executor` and `exports` do not exist inside `build_dev_dependencies`

The design says `attach_dev_rag(deps, chat)` is called from one line in `edge/dev_profile.py::build_dev_dependencies`, folded into the `build_dev_mcp` block (design L64, L328, L336, L404). It also says `dev_rag.build_dev_rag` asserts `chat.phase_resolvers is not None` and builds `RagDeps` including `scan_executor`, and that chunk scans pass `exports=chat.exports`.

The code does not support that. `build_dev_dependencies` only returns a `deps` dict plus boot logs.
- No chat route is built there. Routes are built later from `deps` in `build_app` and `build_routes`.
- `ScanExecutor` is created inside `build_app` (`edge/app.py:293`: `scan_executor = ScanExecutor(contract=contract)`). `dev_profile.py` never references it.
- `exports` is created at `dev_profile.py:393`, after the `build_dev_mcp` call at line 390, so it is not available at the planned insertion line either.

So `attach_dev_rag(deps, chat)` has no `chat` to receive. `RagDeps.scan_executor` (a required field) cannot be filled by the dev builder. The "one replaced line" cannot be implemented as written, and the 119-line function-span plan rests on it. An implementer will find this when the first task compiles, and then has to choose between building a second `ScanExecutor` in the dev path (a second thread pool) or moving construction into `build_app` (which is 800 lines, 119-line function, with zero budget).

Fix: choose one construction point and write it into D-2, the module table, the wiring section and the size table.
1. `dev_rag.build_dev_rag(env, contract, *, phase_resolvers, exports)` takes only values that exist at `dev_profile.py` time. `phase_resolvers` is `deps["phase_resolvers"]` and `exports` is the object created at L393. Call it after L393 as one line that replaces the existing `deps.update(...)` continuation, or add it as a keyword argument to that `deps.update(...)` call. State which line is replaced, and keep the span at 119 or less.
2. `RagDeps` holds no `scan_executor`. The route receives the executor `build_app` already creates. `build_app` already passes `executor=scan_executor` into the chat route (`app.py:342`), so `rag_entries(chat, rag)` reads `chat.executor` and the design says so. Delete the `scan_executor` field and F-7 text accordingly, or keep the field and have `build_app` pass `executor` into `rag_entries` explicitly (state the zero-net-line way).
3. Replace "asserts `chat.phase_resolvers is not None`" at dev-build time with an assertion on `deps["phase_resolvers"]`. Keep the request-time check in `resolver_at`, which is the real guard.
4. Add a test that boots the dev profile through `build_dev_dependencies` and `build_app`, and checks that `/v1/rag/query` is served (not `rag_unavailable`) and that only one `ScanExecutor` exists.

### F-2 (LOW) The line-budget row for `dev_profile.py` says "1 removed (mcp line, folded)", but the `build_dev_mcp` block is three lines

`dev_profile.py:390-392` is the call, `if mcp is not None:` and `deps["mcp"] = mcp`. The table counts one removed line and one added line. Folding the RAG call into that block, or after L393, is not shown to net zero. The baseline is recorded as "recorded first" and the function span is 119. Fix: state the exact replacement text of the lines touched (for example, move the `if mcp is not None` assignment into `deps.update(...)` at L402 and use the freed lines for the RAG call), and make "span <= 120" a unit assertion rather than only a manual check.

### F-3 (NIT) Round 4 response table is not marked as history

The "Responses to design-loop review (round 4)" section at the end of `design.md` records resolved findings. It should carry the same history banner as `tasks/design-history.md`, or move there, so implementers read only normative text.

## Verified Assumptions

- `routes.py` is 799 lines, `app.py` 800, `resources.py` 800 (`wc -l` on this worktree).
- `ScanExecutor` is created in `build_app` (`app.py:293`) and passed to the chat route as `executor` (`app.py:342`).
- `build_dev_dependencies` returns `(deps, logs)`; `deps` carries `phase_resolvers` (`dev_profile.py:355`), and `exports` is created at `dev_profile.py:393`.
- `build_dev_mcp` is called at `dev_profile.py:390` with the `if mcp is not None` assignment on L391-392.
- C-9 amends Req 12.4 and Req 9.3 in `requirements.md`.
- `DEFAULT_DEV_ORG_ID = "dev-org"` claim carried from round 3, not re-read.

## Unverified / Wrong Assumptions

- Wrong: a chat route and a `ScanExecutor` are available to the dev profile builder (F-1).
- Wrong: the `exports` object exists at the `build_dev_mcp` call line (F-1).
- Not verified: the runbook .docx text beyond what `requirements.md` quotes; the Pinecone namespace length limit (the design already marks it unverified); per-transport id round-trips beyond the design's cited lines.
