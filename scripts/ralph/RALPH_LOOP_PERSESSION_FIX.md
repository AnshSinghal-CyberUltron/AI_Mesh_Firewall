# Ralph-loop per-session state fix (why loops "stopped without completion")

## Root cause
The `ralph-loop` plugin kept ALL loop state in ONE project-scoped file
`.claude/ralph-loop.local.md`. Running two `/ralph-loop`s in the SAME working directory
(a chat-stress loop **and** an MCP-gateway loop, here on `main`) made every `/ralph-loop`
truncate-overwrite that single file. The stop hook has a session-isolation guard:

```bash
if [[ -n "$STATE_SESSION" ]] && [[ "$STATE_SESSION" != "$HOOK_SESSION" ]]; then exit 0; fi
```

So whichever session last wrote the file "owned" it; every other session's Stop hook saw a
session-id mismatch and `exit 0`'d **without re-feeding** — i.e. its loop stopped after one turn.
(It was never max-iterations or a false `<promise>`.)

## Fix (applied 2026-07-02)
Patched the plugin cache
`~/.claude/plugins/cache/claude-plugins-official/ralph-loop/1.0.0/`:

- **`scripts/setup-ralph-loop.sh`** writes a PER-SESSION file
  `.claude/ralph-loop.${CLAUDE_CODE_SESSION_ID}.local.md` (via `printf` field-by-field so a prompt
  body containing `$...` or an `EOF`-like line is written verbatim). Falls back to the legacy shared
  name only when no session id is in the env.
- **`hooks/stop-hook.sh`** resolves `.claude/ralph-loop.<hook.session_id>.local.md`; if absent it
  falls back to the legacy `.claude/ralph-loop.local.md` but adopts it only when it is session-less or
  its `session_id` matches this session (so a stale legacy file owned by another session is ignored).

Originals are backed up next to each script as `*.orig`.

## Verified
`bash -n` clean on both; and behaviorally: per-session re-feed bumps its own `iteration` and returns
`{"decision":"block","reason":<prompt>}`; a legacy file with a matching/absent session re-feeds; a
legacy or per-session file owned by another session makes THIS session exit without cross-feeding;
`<promise>` detection removes the (correct) per-session file. Prompt bodies with `$`/`EOF` survive verbatim.

## Migrate an existing loop
Run `/ralph-loop ...` **once more** in each session — setup then creates that session's per-session file
and the loop auto-continues from then on, independently of any other loop in the directory. The old
shared `.claude/ralph-loop.local.md` keeps working via fallback until its owner re-invokes.

## Re-apply after a plugin update
A plugin upgrade may overwrite the cache. To re-apply: restore from `*.orig` if needed, then re-do the
two edits above (session-keyed `STATE_FILE` in setup; session-keyed `RALPH_STATE_FILE` + legacy fallback
in the stop hook). The change is self-contained and additive.
